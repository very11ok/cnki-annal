# -*- coding: utf-8 -*-
"""人工下载的 PDF 一键入库：复制到工程 PDF\\ 并登记 映射.json。在文献工程目录里运行。

用法:
    python <skill>\\scripts\\intake_manual.py --pdf "C:\\Users\\xx\\Downloads\\某文献_作者.pdf" [--title "题录题名"]
    （多个 --pdf 可连给；--title 只对单个文件生效）

题名推断顺序：--title 显式给 > 题录里 norm 包含匹配（文件名 stem 与题录题名互含）> 文件名 stem。
登记后跑 pdf_to_txt.py（增量抽全文）与 plan_batches.py（切待分析批次）。
"""
from __future__ import annotations
import argparse, json, re, shutil, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
norm = lambda s: re.sub(r"[\s\W_]+", "", str(s or "")).lower()


def guess_title(stem: str, meta_norms: dict) -> str:
    sn = norm(stem)
    for tn, title in meta_norms.items():
        if tn and (tn in sn or sn in tn):
            return title
    return re.sub(r"_\d{1,4}$", "", stem)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", nargs="+", required=True, help="人工下载的 PDF 路径（可多个）")
    ap.add_argument("--title", default="", help="题录题名（单文件时可显式指定）")
    ap.add_argument("--dir", default=".", help="文献工程目录（默认当前目录）")
    a = ap.parse_args()
    base = Path(a.dir).resolve()
    pdf_dir = base / "PDF"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    mp = base / "映射.json"
    mapping = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}

    # 题录 norm 表（用于题名推断）
    meta_norms = {}
    for p in sorted(base.glob("题录*.jsonl")):
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                meta_norms[norm(r.get("题名", ""))] = r.get("题名", "")

    ok = 0
    for raw in a.pdf:
        src = Path(raw)
        if not src.exists():
            print(f"✗ 源文件不在：{src}")
            continue
        dst = pdf_dir / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
        title = a.title if (a.title and len(a.pdf) == 1) else guess_title(src.stem, meta_norms)
        mapping[src.name] = title
        ok += 1
        print(f"✓ {src.name} → {dst}（题名：{title}）")
    mp.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"映射.json 共 {len(mapping)} 条")
    if ok:
        print("下一步：python <skill>\\scripts\\pdf_to_txt.py && python <skill>\\scripts\\plan_batches.py")


if __name__ == "__main__":
    main()
