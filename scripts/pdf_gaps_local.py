# -*- coding: utf-8 -*-
"""题录维度的 PDF 缺口检查（纯本地精简版，无腾讯文档依赖）。

从 cnki-tencent-pipeline 的 pdf_gaps.py 抽出 check_metadata 一支：
cnki_pdf_grab.py --metadata 用它判断"题录里哪些还没有本地 PDF"。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def suggest_name(rec: dict) -> str:
    """建议的本地文件名：题名_作者_年.pdf。"""
    t = str(rec.get("题名") or rec.get("title") or "").strip()
    t = re.split(r"\[[JMDCNR]\]|【[JMDCNR]】|\bDOI\b", t)[0].strip(" .;；")
    a = str(rec.get("作者") or rec.get("author") or "").strip().split(";")[0]
    y = str(rec.get("年") or rec.get("year") or "").strip()
    parts = [p for p in (t, a, y) if p]
    name = BAD_CHARS.sub("_", "_".join(parts))[:110].strip("_")
    return (name or "未命名文献") + ".pdf"


def key_of(rec: dict) -> str:
    return str(rec.get("题名") or rec.get("title") or "").strip()


def norm(s: str) -> str:
    return re.sub(r"[\s\-—_·,，.。:：;；()（）\[\]【】]+", "", str(s or "")).lower()


def check_metadata(mpath: Path, pdf_dir: Path | None) -> list[dict]:
    """题录维度：没有本地 PDF 文件的记录。"""
    out = []
    for line in mpath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        p = str(rec.get("pdf") or "").strip()
        has = bool(p) and Path(p).exists()
        if not has and pdf_dir and pdf_dir.exists():
            want = norm(key_of(rec))
            for f in list(pdf_dir.glob("*.pdf")) + list(pdf_dir.glob("*.caj")):
                if want and (want in norm(f.stem) or norm(f.stem) in want):
                    has = True
                    break
        if not has:
            out.append({"题名": key_of(rec), "作者": rec.get("作者", ""),
                        "文献来源": rec.get("文献来源", ""), "年": rec.get("年", ""),
                        "原因": "题录里没有对应 PDF 文件",
                        "建议文件名": suggest_name(rec),
                        "来源文件": str(mpath.name)})
    return out
