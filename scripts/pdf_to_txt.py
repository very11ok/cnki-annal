# -*- coding: utf-8 -*-
"""PDF -> 全文 txt（PyMuPDF 优先，pypdf 逐页容错兜底）。在文献工程目录里运行。

用法: python <skill>\\scripts\\pdf_to_txt.py [--dir 工程目录]
目录约定: <工程>/PDF  ->  <工程>/提取输入/*.txt + <工程>/提取输入/清单.json
可选 <工程>/映射.json：{"文件名.pdf": "题录题名"}，优先用映射题名（cnki_pdf_grab
下载文件名≠题录题名，映射由 fix_mapping.py / intake_manual.py 维护）。
增量运行：已抽过的文件跳过（清单里有且 txt>200 字节）。
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def norm_title(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(s or "")).lower()


def safe_name(s: str) -> str:
    n = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(s or "").strip())[:100].strip("_")
    return n or "未命名"


def extract_text(f: Path) -> str:
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # 旧包名
    try:
        with fitz.open(str(f)) as doc:
            return "\n".join(page.get_text() for page in doc)
    except Exception:
        pass
    import pypdf
    out = []
    r = pypdf.PdfReader(str(f))
    for p in r.pages:
        try:
            out.append(p.extract_text() or "")
        except Exception:
            out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="文献工程目录（默认当前目录）")
    a = ap.parse_args()
    base = Path(a.dir).resolve()
    pdf_dir = base / "PDF"
    txt_dir = base / "提取输入"
    manifest_path = txt_dir / "清单.json"
    mapping_path = base / "映射.json"
    txt_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    mapping = json.loads(mapping_path.read_text(encoding="utf-8")) if mapping_path.exists() else {}

    files = sorted(pdf_dir.glob("*.pdf")) if pdf_dir.exists() else []
    print(f"PDF 共 {len(files)} 个")
    new = 0
    for i, f in enumerate(files, 1):
        title = mapping.get(f.name) or re.sub(r"_\d{1,4}$", "", f.stem)
        txt_path = txt_dir / (safe_name(f.stem) + ".txt")
        if txt_path.exists() and txt_path.stat().st_size > 200:
            m = manifest.get(f.name)
            if m and m.get("状态") in ("ok", "扫描版"):
                continue
        try:
            txt = extract_text(f)
        except Exception as e:
            txt = ""
            print(f"[{i}] {title[:40]}  抽取异常: {e}")
        txt = txt.strip()
        txt_path.write_text(txt, encoding="utf-8")
        state = "ok" if len(txt) >= 200 else "扫描版"
        manifest[f.name] = {"题名": title, "txt": str(txt_path), "字数": len(txt), "状态": state}
        new += 1
        if i % 20 == 0 or state == "扫描版":
            print(f"[{i}] {title[:40]}  {len(txt)} 字  {state}")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for m in manifest.values() if m["状态"] == "ok")
    scan = sum(1 for m in manifest.values() if m["状态"] == "扫描版")
    print(f"本轮新处理 {new}；清单累计 {len(manifest)}（可分析 {ok}，扫描版 {scan}）")
    if scan:
        print("⚠ 扫描版文献抽不出正文，不要拿去硬分析——转人工下载文字版 PDF 后重跑。")


if __name__ == "__main__":
    main()
