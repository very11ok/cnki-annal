#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""EndNote / RIS / EndNote-XML 题录 → 流水线题录 JSONL（纯本地）。

为什么需要它：知网「导出与分析 → EndNote 格式下载」拿到的 .enw 比接口导出信息全得多
（含摘要/关键词/卷期页/DOI），但 cnki_pdf_grab.py 只吃本流水线的 题录*.jsonl。
本脚本把 .enw / .ris / .xml 统一转成那份格式，字段与 cnki_export.py 的输出对齐：
    题名 / 作者 / 文献来源 / 年 / 出版时间 / 引文（+ 摘要/关键词/卷/期/页码/DOI/URL/ISSN）

用法（在文献工程目录里运行）：
    python <skill>\\scripts\\refs_to_jsonl.py <文件.enw>                 # 输出 题录_<文件名>.jsonl
    python <skill>\\scripts\\refs_to_jsonl.py <文件.enw> -o 题录.jsonl
    python <skill>\\scripts\\refs_to_jsonl.py <文件.enw> --stats         # 顺带打印期刊/年份分布
    python <skill>\\scripts\\refs_to_jsonl.py <文件.enw> --keep-dups    # 默认按题名去重

支持格式（按扩展名/内容自动识别）：
    .enw   EndNote tagged（知网自定义导出的就是这种）
    .ris   RIS
    .xml   EndNote XML
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ---------------------------------------------------------------- 通用清洗
def norm_title(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(s or "")).lower()


def clean(s: str) -> str:
    """去首尾空白、合并内部换行/多空格；保留原文标点。"""
    return re.sub(r"\s+", " ", str(s or "")).strip()


def split_authors(s: str) -> list[str]:
    """作者串归一：支持 ; ； , ， 分隔，去掉尾部句点。"""
    parts = re.split(r"[;；]", str(s or ""))
    if len(parts) <= 1:
        parts = re.split(r"[,，]", str(s or ""))
    return [p.strip().strip(".") for p in parts if p.strip().strip(".")]


# ---------------------------------------------------------------- ENW
def parse_enw(text: str) -> list[dict]:
    """EndNote tagged 格式。%T 之类开头是新字段，不以 % 开头的行是上一字段的续行。"""
    records: list[dict] = []
    cur: dict[str, list[str]] = {}
    last_tag = None
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if line.startswith("%") and len(line) > 1:
            tag = line[1]
            val = line[3:] if len(line) > 2 else ""
            if tag == "0":                      # %0 = 新记录起点
                if cur:
                    records.append(cur)
                cur = {"0": [val]}
            else:
                cur.setdefault(tag, []).append(val)
            last_tag = tag
        elif line.strip():
            if last_tag and cur.get(last_tag) is not None:
                cur[last_tag][-1] += " " + line.strip()
        # 空行忽略
    if cur:
        records.append(cur)
    return records


# ---------------------------------------------------------------- RIS
RIS_TAG = {
    "TI": "T", "T1": "T", "AU": "A", "A1": "A", "JO": "J", "JF": "J", "T2": "J",
    "PY": "D", "Y1": "D", "DA": "D", "VL": "V", "IS": "N", "SP": "P",
    "AB": "X", "N1": "X", "KW": "K", "DO": "R", "UR": "U", "SN": "@",
    "PB": "I", "LA": "G",
}


def parse_ris(text: str) -> list[dict]:
    records, cur = [], {}
    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^([A-Z][A-Z0-9])  - (.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            if cur:
                records.append(cur)
            cur = {"0": ["Journal Article"]}
            continue
        if tag == "ER":
            if cur:
                records.append(cur)
                cur = {}
            continue
        t = RIS_TAG.get(tag)
        if t:
            cur.setdefault(t, []).append(val)
    if cur:
        records.append(cur)
    return records


# ---------------------------------------------------------------- EndNote XML
def parse_xml(path: Path) -> list[dict]:
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    out = []
    for rec in root.iter("record"):
        d: dict[str, list[str]] = {"0": ["Journal Article"]}

        def add(tag, val):
            val = clean(val or "")
            if val:
                d.setdefault(tag, []).append(val)

        t = rec.findtext(".//titles/title")
        add("T", t)
        for a in rec.findall(".//contributors/authors/author"):
            add("A", a.text)
        add("J", rec.findtext(".//periodical/full-title")
            or rec.findtext(".//titles/secondary-title"))
        add("D", rec.findtext(".//dates/year"))
        add("V", rec.findtext(".//volume"))
        add("N", rec.findtext(".//number"))
        add("P", rec.findtext(".//pages"))
        add("X", rec.findtext(".//abstract"))
        add("@", rec.findtext(".//isbn"))
        add("R", rec.findtext(".//electronic-resource-num"))
        add("U", rec.findtext(".//urls/related-urls/url"))
        for k in rec.findall(".//keywords/keyword"):
            if k.text:
                d.setdefault("K", []).append(clean(k.text))
        # 关键词合并成一条分号串
        if "K" in d:
            d["K"] = [";".join(d["K"])]
        out.append(d)
    return out


# ---------------------------------------------------------------- 转标准记录
def to_record(d: dict) -> dict:
    g = lambda t: clean((d.get(t) or [""])[0])          # noqa: E731
    authors = split_authors(";".join(d.get("A") or []))
    title = g("T")
    src = g("J")
    year = re.sub(r"[^0-9]", "", g("D"))[:4]
    vol, iss, pages = g("V"), g("N"), g("P")
    # 引文：与 cnki_export.py 保持一致（作者.题名[J].来源,年.）
    au = ";".join(authors)
    citation = f"{au}.{title}[J].{src},{year}." if (au or src or year) else title
    rec = {
        "题名": title,
        "作者": au,
        "文献来源": src,
        "年": year,
        "出版时间": year,
        "引文": citation,
        "卷": vol,
        "期": iss,
        "页码": pages,
        "摘要": g("X"),
        "关键词": g("K"),
        "DOI": g("R"),
        "URL": g("U"),
        "ISSN": g("@"),
        "类型": g("0") or "Journal Article",
    }
    return {k: v for k, v in rec.items() if v}


def detect(text: str, path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".xml" or text.lstrip().startswith("<?xml") or "<records" in text[:2000]:
        return "xml"
    if re.search(r"^TY  - ", text, re.M):
        return "ris"
    if re.search(r"^%0 ", text, re.M):
        return "enw"
    return "unknown"


def main():
    ap = argparse.ArgumentParser(description="EndNote/RIS/XML → 题录 JSONL")
    ap.add_argument("input", help="EndNote(.enw)/RIS(.ris)/XML(.xml) 文件")
    ap.add_argument("-o", "--out", default="", help="输出 JSONL（默认 题录_<输入文件名>.jsonl）")
    ap.add_argument("--keep-dups", action="store_true", help="保留重复题名（默认去重）")
    ap.add_argument("--stats", action="store_true", help="打印期刊/年份分布")
    a = ap.parse_args()

    p = Path(a.input)
    if not p.exists():
        raise SystemExit(f"文件不存在：{p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    kind = detect(text, p)
    if kind == "enw":
        raw = parse_enw(text)
    elif kind == "ris":
        raw = parse_ris(text)
    elif kind == "xml":
        raw = parse_xml(p)
    else:
        raise SystemExit("无法识别格式：需要 .enw / .ris / .xml（EndNote tagged / RIS / EndNote XML）")

    recs, seen, dup = [], set(), 0
    for d in raw:
        r = to_record(d)
        if not r.get("题名"):
            continue
        k = norm_title(r["题名"])
        if not a.keep_dups:
            if k in seen:
                dup += 1
                continue
            seen.add(k)
        recs.append(r)

    out = Path(a.out) if a.out else Path.cwd() / f"题录_{p.stem}.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                   encoding="utf-8")

    with_sum = sum(1 for r in recs if r.get("摘要"))
    years = [r.get("年", "") for r in recs if r.get("年")]
    print(f"格式 {kind}｜解析 {len(raw)} 条，去重 {dup} 条，写出 {len(recs)} 条")
    print(f"有摘要 {with_sum} 条；年份 {min(years, default='?')}-{max(years, default='?')}")
    print(f"→ {out}")

    if a.stats:
        from collections import Counter
        print("\n期刊 TOP15：")
        for name, n in Counter(r.get("文献来源", "?") for r in recs).most_common(15):
            print(f"  {n:4d}  {name}")
        print("\n年份分布：")
        for y, n in sorted(Counter(years).items()):
            print(f"  {y}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
