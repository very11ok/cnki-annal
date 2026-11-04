# -*- coding: utf-8 -*-
"""汇总 提取输出/*.json + 题录 → 本地 Excel 两张表（定稿版式）。在文献工程目录里运行。

用法: python scripts\\build_excel.py [--dir 工程目录] [--meta 题录.jsonl] [--out 提取结果.xlsx]

版式（定稿）：
  表1 文献表：文献信息 | 摘要 | 研究贡献 | 核心理论 | 研究方法 | 研究模型与假设
      ——第一列存整条引文（作者.题名[J].来源,年.）；按发表年份升序排列（同年按引文字符串）；
      尚无分析 JSON 的题录也占一行（其余列空）。
  表2 变量表：文献信息 | 概念名称 | 变量类型 | 变量名称 | 变量测度 | 数据来源
      ——第一列同为引文，行序随表1。

写入前校验：变量类型枚举（不合法会打印问题清单并以退出码 1 结束，文件仍生成）。
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

VAR_TYPES = ["因变量", "自变量", "中介/机制变量", "调节变量", "控制变量",
             "内生检验工具变量", "异质性分析维度变量", "其他变量"]


def norm_title(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(s or "")).lower()


def load_meta(p: Path):
    recs = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            r = json.loads(line)
            recs[norm_title(r.get("题名", ""))] = r
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="文献工程目录（默认当前目录）")
    ap.add_argument("--meta", default="", help="题录 JSONL（默认 工程目录/题录*.jsonl 中条数最多者）")
    ap.add_argument("--out", default="", help="输出 xlsx（默认 工程目录/提取结果.xlsx）")
    a = ap.parse_args()
    base = Path(a.dir).resolve()
    if a.meta:
        meta_path = Path(a.meta)
    else:
        # 工程里可能有多个 题录*.jsonl（如"缺PDF清单"派生文件）——选条数最多的当主题录
        cands = sorted(base.glob("题录*.jsonl"))
        meta_path = max(cands, key=lambda p: sum(1 for l in p.read_text(encoding="utf-8").splitlines() if l.strip())) if cands else None
        if meta_path:
            print(f"使用题录：{meta_path.name}")
    if not meta_path or not meta_path.exists():
        raise SystemExit(f"找不到题录 JSONL：{meta_path}")
    meta = load_meta(meta_path)
    xlsx_path = Path(a.out) if a.out else (base / "提取结果.xlsx")

    manifest = {}
    mf = base / "提取输入" / "清单.json"
    if mf.exists():
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    file_by_txt = {Path(m["txt"]).stem: m for m in manifest.values()}

    rows1, rows2, problems, done_norms = [], [], [], set()
    for jf in sorted((base / "提取输出").glob("*.json")):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"{jf.name}: JSON 解析失败 {e}")
            continue
        title = str(d.get("题名") or "").strip()
        tn = norm_title(title)
        mrec = meta.get(tn, {})
        for k in ("摘要", "研究贡献", "核心理论", "研究方法", "研究模型与假设"):
            if not str(d.get(k) or "").strip():
                problems.append(f"{jf.name}: 缺字段 {k}")
        for v in d.get("变量") or []:
            vt = str(v.get("变量类型", ""))
            if vt not in VAR_TYPES:
                problems.append(f"{jf.name}: 非法变量类型「{vt}」")
        done_norms.add(tn)
        intro = mrec.get("引文", title)
        rows1.append([intro, str(d.get("摘要") or ""), str(d.get("研究贡献") or ""),
                      str(d.get("核心理论") or ""), str(d.get("研究方法") or ""),
                      str(d.get("研究模型与假设") or ""), mrec.get("年", "")])
        for v in d.get("变量") or []:
            rows2.append([intro,
                          str(v.get("概念名称") or ""), str(v.get("变量类型") or ""),
                          str(v.get("变量名称") or ""),
                          str(v.get("变量测度") or ""), str(v.get("数据来源") or ""),
                          mrec.get("年", "")])

    # 尚未分析的题录也进表1（其余列空），保证表覆盖全部题录
    for tn, r in meta.items():
        if tn not in done_norms:
            rows1.append([r.get("引文", r.get("题名", "")), "", "", "", "", "",
                          r.get("年", "")])

    # 按年升序（解析不出年份的排最后），同年按引文字符串
    def year_key(row):
        y = str(row[-1] or "")
        return (0, int(y)) if y.isdigit() else (1, 0)
    rows1.sort(key=lambda r: (year_key(r), r[0]))
    rows2.sort(key=lambda r: (year_key(r), r[0]))

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "文献表"
    ws1.append(["文献信息", "摘要", "研究贡献", "核心理论", "研究方法", "研究模型与假设"])
    for row in rows1:
        ws1.append(row[:-1])          # 年列只用于排序，不落表
    ws2 = wb.create_sheet("变量表")
    ws2.append(["文献信息", "概念名称", "变量类型", "变量名称", "变量测度", "数据来源"])
    for row in rows2:
        ws2.append(row[:-1])
    for ws, widths in ((ws1, [44, 60, 50, 44, 44, 50]),
                       (ws2, [44, 20, 14, 24, 50, 24])):
        for c in ws[1]:
            c.font = Font(bold=True)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
    wb.save(xlsx_path)
    print(f"表1 文献表 {len(rows1)} 行；表2 变量表 {len(rows2)} 行 → {xlsx_path}")
    print(f"已完成 {len(done_norms)} 篇（题录 {len(meta)} 篇；未分析的在表1 只占引文行）")
    if problems:
        print("问题：")
        for p in problems:
            print("  -", p)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
