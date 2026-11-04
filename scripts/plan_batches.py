# -*- coding: utf-8 -*-
"""对照题录/全文/已产出 JSON，切出待分析批次 batches.json。在文献工程目录里运行。

用法: python <skill>\\scripts\\plan_batches.py [--dir 工程目录] [--batch 5]
逻辑: 清单.json 里状态 ok 的全文 txt，若 提取输出/ 下没有同名 .json 则算待分析，
按每批 N 篇（默认 5）写入 batches.json（数组，每项 {txt, 题名, 字数}）。
子代理按批次下标取数分析（见 SKILL.md 的子代理指令模板）。
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="文献工程目录（默认当前目录）")
    ap.add_argument("--batch", type=int, default=5, help="每个子代理处理的篇数（默认5）")
    a = ap.parse_args()
    base = Path(a.dir).resolve()
    mf = base / "提取输入" / "清单.json"
    if not mf.exists():
        raise SystemExit(f"没有 {mf}——先跑 pdf_to_txt.py")
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    done = {p.stem for p in (base / "提取输出").glob("*.json")}

    missing = []
    for fname, m in manifest.items():
        if m["状态"] != "ok":
            continue
        stem = Path(m["txt"]).stem
        if stem not in done:
            missing.append({"txt": m["txt"], "题名": m["题名"], "字数": m["字数"]})

    n_pdf = len(list((base / "PDF").glob("*.pdf"))) if (base / "PDF").exists() else 0
    meta_norms = set()                    # 跨多个题录文件取唯一题名数
    norm = lambda s: re.sub(r"[\s\W_]+", "", str(s or "")).lower()
    for p in base.glob("题录*.jsonl"):
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                meta_norms.add(norm(json.loads(l).get("题名", "")))
    print(f"题录 {len(meta_norms)} ｜ PDF {n_pdf} ｜ 全文ok "
          f"{sum(1 for m in manifest.values() if m['状态']=='ok')} ｜ 已分析 {len(done)} ｜ 待分析 {len(missing)}")
    batches = [missing[i:i + a.batch] for i in range(0, len(missing), a.batch)]
    (base / "batches.json").write_text(
        json.dumps(batches, ensure_ascii=False, indent=1), encoding="utf-8")
    for bi, b in enumerate(batches, 1):
        print(f" 批{bi}: " + "; ".join(x["题名"][:18] for x in b))
    if not batches:
        print("全部已分析 ✓（batches.json 已清空）")


if __name__ == "__main__":
    main()
