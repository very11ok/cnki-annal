#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""直连 cnki-deepsearch 的 stdio MCP 客户端（比 mcporter 可控）。

为什么不用 mcporter：
  - `--env` 对已注册服务不生效（想把 CNKI_HEADED=1 传给服务进程做不到）
  - `--stdio` 现场起服务这边实测直接 Connection closed
  - 参数要 JSON payload，长文本会被 cmd.exe 8191 字符上限截断
自己起进程就没这些事：环境变量、cwd（决定 PDF 存到哪）、超时全在手。

MCP stdio 传输 = 一行一个 JSON-RPC 消息。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

DEFAULT_EXE = Path.home() / ".local" / "bin" / "cnki-deepsearch.EXE"
PROTOCOL = "2024-11-05"


class CnkiStdio:
    """cnki-deepsearch 的 stdio 客户端（串行调用；服务端本身也是串行的）。"""

    def __init__(self, exe: Path | str = "", headed: bool = False,
                 cwd: Path | str = "", log: bool = False):
        self.exe = Path(exe) if exe else DEFAULT_EXE
        self.headed = headed
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.log = log
        self.p = None
        self._id = 0
        self._buf: list[str] = []
        self._err: list[str] = []
        self._lock = threading.Lock()

    # ── 生命周期 ──────────────────────────────────────────────
    def start(self, timeout: float = 60) -> None:
        if not self.exe.exists():
            raise RuntimeError(
                f"找不到 cnki-deepsearch：{self.exe}\n"
                "装：uv tool install cnki-deepsearch（见 preflight.py）")
        self.cwd.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        if self.headed:
            env["CNKI_HEADED"] = "1"        # 服务端读这个开关 → 开可见浏览器
        self.p = subprocess.Popen(
            [str(self.exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, cwd=str(self.cwd),
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._drain_err, daemon=True).start()
        r = self._rpc("initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "cnki-tencent-pipeline", "version": "1.0"}},
            timeout=timeout)
        self._notify("notifications/initialized")
        return r

    def close(self) -> None:
        try:
            if self.p and self.p.poll() is None:
                self.p.terminate()
                try:
                    self.p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.p.kill()
        except Exception:  # noqa: BLE001
            pass
        self.p = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *a):
        self.close()

    # ── 调用 ──────────────────────────────────────────────────
    def tools(self, timeout: float = 60) -> list[dict]:
        r = self._rpc("tools/list", {}, timeout=timeout)
        return (r or {}).get("tools", [])

    def call(self, tool: str, arguments: dict, timeout: float = 180) -> dict:
        """调工具；返回解析后的 dict（服务端 content[0].text 是 JSON）。"""
        r = self._rpc("tools/call", {"name": tool, "arguments": arguments},
                      timeout=timeout)
        if r is None:
            return {"error": f"{tool} 无响应（{timeout:.0f}s 超时）"}
        if isinstance(r, dict) and r.get("isError"):
            return {"error": _text_of(r) or "工具报错"}
        txt = _text_of(r)
        if not txt:
            return {"error": "返回为空", "raw": r}
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            return {"text": txt}

    # ── 协议细节 ──────────────────────────────────────────────
    def _drain_err(self) -> None:
        try:
            for line in self.p.stderr:      # type: ignore[union-attr]
                self._err.append(line.rstrip())
                if self.log:
                    print("  [server]", line.rstrip()[:160])
        except Exception:  # noqa: BLE001
            pass

    def _send(self, obj: dict) -> None:
        self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")  # type: ignore
        self.p.stdin.flush()                                          # type: ignore

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _rpc(self, method: str, params: dict, timeout: float = 60) -> dict | None:
        with self._lock:
            self._id += 1
            mid = self._id
            self._send({"jsonrpc": "2.0", "id": mid, "method": method,
                        "params": params})
            deadline = time.time() + timeout
            while time.time() < deadline:
                line = self._readline(deadline)
                if line is None:
                    return None
                line = line.strip()
                if not line or not line.startswith("{"):
                    if line and self.log:
                        print("  [skip]", line[:120])
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") != mid:
                    continue                      # 别人的响应/通知，跳过
                if "error" in msg:
                    return {"error": str(msg["error"])[:300]}
                return msg.get("result")
            return None

    def _readline(self, deadline: float) -> str | None:
        """带超时地读一行（避免 readline 永久阻塞）。"""
        out: list[str] = []

        def rd():
            ln = self.p.stdout.readline()        # type: ignore[union-attr]
            out.append(ln)

        t = threading.Thread(target=rd, daemon=True)
        t.start()
        while time.time() < deadline:
            if out:
                return out[0]
            if self.p.poll() is not None and not out:
                return None
            time.sleep(0.05)
        return None


def _text_of(result: dict) -> str:
    if not isinstance(result, dict):
        return ""
    parts = []
    for c in (result.get("content") or []):
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(c.get("text") or "")
    return "\n".join(parts).strip()


def main():
    """自检：起服务 → 列工具 → 退出。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser(description="cnki-deepsearch stdio 客户端自检")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--list", action="store_true", help="列出工具")
    ap.add_argument("--call", help="工具名")
    ap.add_argument("--args", default="{}", help="JSON 参数")
    ap.add_argument("--timeout", type=float, default=180)
    a = ap.parse_args()
    c = CnkiStdio(headed=a.headed, log=True)
    t0 = time.time()
    c.start()
    print(f"✓ 服务已起（{time.time() - t0:.1f}s，headed={a.headed}）")
    try:
        if a.list or not a.call:
            for t in c.tools():
                print(f"  - {t['name']}" + (f"  {(t.get('description') or '')[:60]}"
                                            if a.list else ""))
        if a.call:
            r = c.call(a.call, json.loads(a.args), timeout=a.timeout)
            print(json.dumps(r, ensure_ascii=False)[:1200])
    finally:
        c.close()
        print("已退出")


if __name__ == "__main__":
    main()
