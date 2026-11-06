#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知网 PDF 纯 HTTP 直拉（无浏览器、无点击、无验证码）——机构权限 + 已登录 cookie。

原理（实测 2026-11 有效）：
    ① GET 文献详情页（DOI 短链 https://link.cnki.net/doi/... 或 kns.cnki.net 详情页）
    ② HTML 里 <a id="pdfDown" href="https://bar.cnki.net/bar/download/order?id=...">
       这个 href 是**服务端渲染**出来的，不需要执行 JS
    ③ GET 该 href → 302 到 bar.cnki.net/docgateway/api/distribute/route?q=... → 返回 application/pdf

所以只要 cookie（~/.cnki_cookies.json）还有效、机构 IP 有下载权限，就能纯脚本批量拉，
不用开浏览器、不用盯验证码。

用法（在文献工程目录里运行）：
    python <skill>\\scripts\\cnki_pdf_direct.py --metadata 题录_CNKI500.jsonl
    python <skill>\\scripts\\cnki_pdf_direct.py --metadata 题录.jsonl --workers 4 --sleep 0.6
    python <skill>\\scripts\\cnki_pdf_direct.py --metadata 题录.jsonl --limit 20 --json > report.log

产出：PDF\\*.pdf + 映射.json（文件名 → 题录题名，供 pdf_to_txt.py 精确对题）
断点续传：已下过的（映射里有 / 磁盘上有同名 PDF）自动跳过。
熔断：连续 --max-fail 篇失败（多半是 cookie 过期或权限掉线）自动停下并提示。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urljoin

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

COOKIE_FILE = Path.home() / ".cnki_cookies.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_print_lock = threading.Lock()


def log(msg: str):
    """过程日志一律走 stderr，保证 --json 时 stdout 只有纯 JSON。"""
    with _print_lock:
        print(msg, file=sys.stderr, flush=True)


def norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(s or "")).lower()


def safe_name(s: str, limit: int = 60) -> str:
    n = BAD_CHARS.sub("_", str(s or "").strip())[:limit].strip("_")
    return n or "未命名"


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    retry = Retry(total=2, backoff_factor=1.2,
                  status_forcelist=(500, 502, 503, 504),
                  allowed_methods=("GET", "HEAD"))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    if COOKIE_FILE.exists():
        for c in json.loads(COOKIE_FILE.read_text(encoding="utf-8")):
            try:
                s.cookies.set(c["name"], c.get("value", ""),
                              domain=c.get("domain", ""), path=c.get("path", "/"))
            except Exception:
                pass
    return s


def detail_url(rec: dict) -> str:
    """题录 → 详情页 URL。优先原始 URL，其次 DOI 短链。"""
    u = str(rec.get("URL") or rec.get("url") or "").strip()
    if u.startswith("http"):
        return u
    doi = str(rec.get("DOI") or rec.get("doi") or "").strip()
    if doi:
        return f"https://link.cnki.net/doi/{doi}"
    return ""


def find_pdf_href(html: str) -> str:
    """从详情页 HTML 取 #pdfDown 的 href（服务端渲染，无需 JS）。"""
    for pat in (r'id="pdfDown"[^>]*?href="([^"]+)"',
                r'href="([^"]+)"[^>]*?id="pdfDown"',
                r'id=["\']pdfDown["\'][^>]*?href=["\']([^"\']+)["\']'):
        m = re.search(pat, html, re.S | re.I)
        if m:
            href = m.group(1).replace("&amp;", "&").strip()
            if href.startswith("javascript") or not href:
                continue
            return href
    return ""


def detect_block(html: str) -> str:
    """识别被拦的原因，便于给用户可操作的提示。"""
    low = html.lower()
    if "tcaptcha" in low or "验证码" in html:
        return "遇到滑块验证码（cookie 失效或被判定异常）"
    if "登录" in html and ("账号登录" in html or "login" in low):
        return "需要登录（cookie 已过期）"
    if "未订阅" in html or "无权限" in html or "购买" in html:
        return "机构未订阅该刊（无下载权限）"
    if "网络首发" in html and "pdfdown" not in low:
        return "网络首发文献可能暂无 PDF"
    return ""


def fetch_one(sess: requests.Session, rec: dict, out_dir: Path,
              sleep_s: float) -> dict:
    title = str(rec.get("题名") or "").strip()
    r = {"题名": title, "ok": False, "why": ""}
    url = detail_url(rec)
    if not url:
        r["why"] = "题录既无 URL 也无 DOI，无法定位"
        return r
    try:
        resp = sess.get(url, timeout=40, verify=False, allow_redirects=True)
    except Exception as e:
        r["why"] = f"详情页请求失败：{str(e)[:60]}"
        return r
    if resp.status_code != 200:
        r["why"] = f"详情页 HTTP {resp.status_code}"
        return r
    html = resp.text

    href = find_pdf_href(html)
    if not href:
        r["why"] = "详情页没有 pdfDown 链接｜" + (detect_block(html) or "可能未收录 PDF")
        return r

    pdf_url = urljoin(resp.url, href)
    try:
        head = sess.get(pdf_url, timeout=60, verify=False,
                        allow_redirects=False, stream=True,
                        headers={"Referer": resp.url})
    except Exception as e:
        r["why"] = f"下载下单失败：{str(e)[:60]}"
        return r

    if head.status_code in (301, 302, 303, 307, 308):
        loc = head.headers.get("Location") or ""
        if not loc:
            r["why"] = f"下单返回 {head.status_code} 但无 Location"
            return r
        pdf_url = urljoin(pdf_url, loc)
        head.close()
        try:
            head = sess.get(pdf_url, timeout=120, verify=False, stream=True,
                            headers={"Referer": "https://kns.cnki.net/"})
        except Exception as e:
            r["why"] = f"取流失败：{str(e)[:60]}"
            return r

    ctype = (head.headers.get("Content-Type") or "").lower()
    if head.status_code != 200:
        r["why"] = f"取流 HTTP {head.status_code}"
        head.close()
        return r

    # 先读首块判真伪，避免把 HTML 错误页存成 .pdf
    first = next(head.iter_content(8), b"")
    if not first.startswith(b"%PDF"):
        body = first + head.content[:400]
        head.close()
        r["why"] = "返回的不是 PDF（" + (detect_block(body.decode("utf-8", "ignore"))
                                        or f"CT={ctype or '空'}") + "）"
        return r

    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{safe_name(title)}_{safe_name(str(rec.get('作者') or '').split(';')[0], 12)}.pdf"
    dest = out_dir / fname
    n = 1
    while dest.exists() and dest.stat().st_size > 0:      # 同名不覆盖，加序号
        dest = out_dir / f"{Path(fname).stem}_{n}.pdf"
        n += 1

    total = len(first)
    try:
        with open(dest, "wb") as f:
            f.write(first)
            for chunk in head.iter_content(65536):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
    except Exception as e:
        r["why"] = f"写盘失败：{str(e)[:50]}"
        return r
    finally:
        head.close()

    if total < 8000:                                    # 小于 8KB 基本是错误页
        dest.unlink(missing_ok=True)
        r["why"] = f"文件过小（{total} 字节），疑似错误页，已删除"
        return r

    r.update({"ok": True, "文件": dest.name, "字节": total,
              "知网题名": (re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip()[:60]
                          if re.search(r"<title>(.*?)</title>", html, re.S) else "")})
    if sleep_s:
        time.sleep(sleep_s)
    return r


def main():
    ap = argparse.ArgumentParser(description="知网 PDF 纯 HTTP 直拉")
    ap.add_argument("--metadata", required=True, help="题录 JSONL")
    ap.add_argument("--out", default="", help="PDF 目录（默认 工程目录/PDF）")
    ap.add_argument("--workers", type=int, default=2, help="并发数（默认 2；超过 3 易被知网限流）")
    ap.add_argument("--sleep", type=float, default=1.5, help="每篇成功后间隔秒（默认 1.5）")
    ap.add_argument("--limit", type=int, default=0, help="最多下几篇")
    ap.add_argument("--max-fail", type=int, default=4, help="一批里连续失败多少篇就冷却（默认 4）")
    ap.add_argument("--cooldown", type=float, default=45.0, help="冷却秒数（默认 45）")
    ap.add_argument("--max-cooldown", type=int, default=6, help="最多冷却几次就放弃（默认 6）")
    ap.add_argument("--retry", type=int, default=1, help="单篇失败重试次数（默认 1）")
    ap.add_argument("--force", action="store_true", help="忽略已有文件，强制重下")
    ap.add_argument("--json", action="store_true", help="末尾打印机器可读报告")
    a = ap.parse_args()

    mp = Path(a.metadata)
    if not mp.exists():
        raise SystemExit(f"题录不存在：{mp}")
    recs = [json.loads(l) for l in mp.read_text(encoding="utf-8").splitlines() if l.strip()]
    out_dir = Path(a.out) if a.out else (Path.cwd() / "PDF")
    map_path = Path.cwd() / "映射.json"
    mapping = json.loads(map_path.read_text(encoding="utf-8")) if map_path.exists() else {}

    # 已在映射里/磁盘上有同名文件 → 跳过
    have = {norm(k) for k in mapping}
    if not a.force:
        todo = []
        for r in recs:
            t = str(r.get("题名") or "")
            fname = f"{safe_name(t)}_{safe_name(str(r.get('作者') or '').split(';')[0], 12)}.pdf"
            if norm(t) in have or (out_dir / fname).exists():
                continue
            todo.append(r)
    else:
        todo = recs
    if a.limit:
        todo = todo[:a.limit]

    log(f"题录 {len(recs)} 条｜已下载跳过 {len(recs) - len(todo)}｜本次待办 {len(todo)}")
    if not todo:
        log("没有待下载的文献 ✓")
        return 0
    log(f"并发 {a.workers}｜间隔 {a.sleep}s｜冷却 {a.cooldown:.0f}s×{a.max_cooldown}｜输出 {out_dir}\n")

    ok_list, bad_list = [], []
    consec_fail = 0
    lock = threading.Lock()
    stop = threading.Event()
    sessions = [make_session() for _ in range(max(1, a.workers))]
    t0 = time.time()

    def work(idx_rec):
        i, rec = idx_rec
        if stop.is_set():
            return None
        sess = sessions[i % len(sessions)]
        res = fetch_one(sess, rec, out_dir, a.sleep)
        tries = 0
        while not res.get("ok") and tries < a.retry and not stop.is_set():
            tries += 1
            time.sleep(3.0 + tries * 2)      # 失败退避后再试，避免同一篇立刻重打
            res = fetch_one(sess, rec, out_dir, a.sleep)
        return res

    done = 0
    cooldowns = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        pos = 0
        while pos < len(todo) and not stop.is_set():
            # 分批提交：一批跑完才评估连续失败，冷却才真正生效
            batch = [(pos + j, r) for j, r in enumerate(todo[pos:pos + a.workers])]
            pos += len(batch)
            fmap = {ex.submit(work, x): x[1] for x in batch}
            for fut in as_completed(fmap):
                rec = fmap[fut]
                done += 1
                try:
                    res = fut.result()
                except Exception as e:
                    res = {"题名": rec.get("题名", ""), "ok": False,
                           "why": f"线程异常 {str(e)[:50]}"}
                if res is None:
                    continue
                t = str(res.get("题名") or "")
                if res.get("ok"):
                    ok_list.append(res)
                    mapping[res["文件"]] = t
                    consec_fail = 0
                    log(f"[{done}/{len(todo)}] ✓ {t[:34]}  {res['字节'] // 1024}KB")
                else:
                    bad_list.append(res)
                    consec_fail += 1
                    log(f"[{done}/{len(todo)}] ✗ {t[:34]}  {res['why'][:60]}")

            # 连续失败 → 冷却后继续（知网按 IP 限流，通常歇一会儿就恢复）
            if consec_fail >= a.max_fail and not stop.is_set():
                cooldowns += 1
                if cooldowns > a.max_cooldown:
                    stop.set()
                    log(f"\n⚠ 已冷却 {a.max_cooldown} 次仍连续失败，停止。"
                        f"多半是 cookie 过期或机构权限掉线——请在浏览器正常登录一次知网，"
                        f"导出新 cookie 覆盖 {COOKIE_FILE} 后重跑（已下的不会重下）。")
                    break
                log(f"\n… 连续失败 {consec_fail} 篇，冷却 {a.cooldown:.0f}s "
                    f"（第 {cooldowns}/{a.max_cooldown} 次）。多半是限流，歇一下再继续。")
                time.sleep(a.cooldown)
                consec_fail = 0

    skipped = len(todo) - len(ok_list) - len(bad_list)

    if mapping:
        map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")

    el = time.time() - t0
    rep = {"ok": len(ok_list), "failed": len(bad_list), "unprocessed": skipped,
           "seconds": round(el, 1), "out": str(out_dir),
           "downloaded": [{"题名": r["题名"], "文件": r["文件"]} for r in ok_list],
           "failed_detail": [{"题名": r["题名"], "why": r["why"]} for r in bad_list]}
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("\n" + "=" * 60)
        print(f"  成功 {len(ok_list)}｜失败 {len(bad_list)}｜未处理 {skipped}｜耗时 {el / 60:.1f} 分钟")
        print("=" * 60)
        reasons = {}
        for r in bad_list:
            k = r["why"].split("｜")[0][:40]
            reasons[k] = reasons.get(k, 0) + 1
        for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"  {v:3d} 篇  {k}")
        if skipped:
            print(f"\n  还有 {skipped} 篇没轮到（被冷却上限或中断）——直接重跑同一条命令即可续传。")
        if ok_list:
            print(f"\n  下一步：python <skill>\\\\scripts\\\\pdf_to_txt.py && python <skill>\\\\scripts\\\\plan_batches.py")
    return 0 if (not bad_list and not skipped) else 1


if __name__ == "__main__":
    # 知网站点证书链在本机代理环境下可能校验失败，统一关掉验证仅影响程序内部 HTTPS
    requests.packages.urllib3.disable_warnings()
    sys.exit(main())
