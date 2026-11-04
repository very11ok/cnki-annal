# -*- coding: utf-8 -*-
"""体检：提取输出/*.json 与题录/清单/映射的一致性。在文献工程目录里运行。

用法: python <skill>\\scripts\\audit.py [--dir 工程目录]
检查：①JSON 数量与题名唯一性；②JSON 题名能否 norm 匹配到题录；
③清单映射（txt stem→题录题名）齐全；④JSON 题名与清单题名逐字一致。
**每波子代理跑完必须执行**——实测有子代理错读批次下标却自报"5/5 完成"。
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
norm = lambda s: re.sub(r"[\s\W_]+", "", str(s or "")).lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="文献工程目录（默认当前目录）")
    a = ap.parse_args()
    base = Path(a.dir).resolve()

    meta = {}
    meta_files = sorted(base.glob("题录*.jsonl"))
    if not meta_files:
        raise SystemExit(f"{base} 下没有 题录*.jsonl")
    for p in meta_files:
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                meta[norm(r.get("题名", ""))] = r.get("题名", "")

    mref = {}
    mf = base / "提取输入" / "清单.json"
    if mf.exists():
        for m in json.loads(mf.read_text(encoding="utf-8")).values():
            mref[Path(m["txt"]).stem] = m["题名"]

    jfs = sorted((base / "提取输出").glob("*.json"))
    seen, bad = {}, []
    for jf in jfs:
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            bad.append((jf.name, "JSON 解析失败", str(e)[:60]))
            continue
        t = norm(d.get("题名", ""))
        if not t:
            bad.append((jf.name, "题名为空", ""))
        elif t not in meta:
            bad.append((jf.name, "题名对不上题录", d.get("题名", "")[:40]))
        if not mref.get(jf.stem):
            bad.append((jf.name, "无清单映射（txt 未抽？）", ""))
        elif norm(mref[jf.stem]) != t:
            bad.append((jf.name, "JSON题名≠清单题名",
                        f'{d.get("题名","")[:24]} vs {mref[jf.stem][:24]}'))
        if t and t in seen:
            bad.append((jf.name, "题名与另一JSON重复", seen[t]))
        seen[t] = jf.name

    print(f"JSON 总数 {len(jfs)}，唯一题名 {len(seen)}，题录 {len(meta)}")
    for b in bad:
        print(" -", *b)
    if not bad:
        print("全部一对一 ✓")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
