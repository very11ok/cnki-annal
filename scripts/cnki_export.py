#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知网检索导出（A刊范围可全自动；C刊/北核走页面流程，见 prompts/知网检索导出.md）。

三个期刊范围选项（篇数在选择时展示）：
    A  所有 A 刊（references/期刊清单_A刊.txt，62 本）
    C  C 刊（CSSCI）——检索篇关摘之后在结果页"来源类别"里筛选，篇数在页面上看
    H  北核（北大核心）——同上

时间范围三选项：近三年 / 近几年（默认 5 年，--years N 可改）/ 全部。

用法：
    python scripts/cnki_export.py --keywords "创新联合体,创新组织" --scope A --years 3
    python scripts/cnki_export.py --keywords "…" --scope A --count-only    # 只看篇数
    python scripts/cnki_export.py --keywords "…" --scope C                 # 给出页面流程指引

依赖（可选）：cnki-deepsearch MCP（本机经 mcporter 调用；检索式都不长，
不会触发 cmd.exe 8191 字符上限）。没装就走页面流程（智能体+眼睛）。

风控/空结果须知（2026-10 实测，很重要）：
    - 调用必须串行，一次一个请求；出现验证页需要用户人工完成
    - max_results ≤ 60（知网每页 20 条），接口的 total_results 只是"返回条数"，
      **不是知网总篇数**——要精确篇数请在页面上读「共 X 条」
    - "暂无数据，请稍后重试" **不等于**风控：检索结果为空时也报这句。
      脚本用"控制探针"区分：先直接问，空了再用最宽条件探一次，通道活着就
      认定本批 0 篇（否则会白等好几分钟）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).parent
DEFAULT_JOURNALS = HERE.parent / "references" / "期刊清单_A刊.txt"
MCPORTER = r"C:\Users\liuqin\AppData\Roaming\npm\mcporter.cmd"

BATCH = 12          # 每批期刊数（批太多 LY 知网会超时）
COOLDOWN = 60       # 批间冷却秒数（实测 35s 仍常被风控，拉到 60s）
RETRY_WAITS = (45, 90, 150)   # 被风控后的退避序列
MAX_PER_SEARCH = 60


def call_cnki(expression: str, max_results: int, timeout_ms: int = 240000):
    """调 cnki_professional_search（串行），返回 dict 或 None。"""
    cmd = [MCPORTER, "call", "--server", "cnki-deepsearch",
           "--tool", "cnki_professional_search",
           "--timeout", str(timeout_ms), "--", expression, str(max_results)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout_ms / 1000 + 60)
    except subprocess.TimeoutExpired:
        return None
    out = p.stdout or ""
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def probe_alive(keywords: list[str]) -> bool:
    """控制探针：用最宽的条件问一次，判断通道是否活着。

    作用：区分「本批真的 0 篇」和「被风控」——两者都表现为"暂无数据"。
    """
    expr = f"TKA=('{keywords[0]}')"
    r = call_cnki(expr, 1)
    return bool(r and r.get("results"))


def search_with_retry(expression: str, label: str, keywords: list[str]):
    """检索一批，能区分空结果与风控。

    ⚠ 实测坑（2026-10 验证）：知网/该 MCP 对**空结果**也报
      "暂无数据，请稍后重试——该查询被风控拦截"。
      例：管理世界 2021-2022 有 3 篇「创新联合体」，加"近三年"后确实 0 篇，
      也会报"被风控"。所以不能一看到这句话就长时间退避重试。
    做法：先直接问 → 若空，用控制探针判断通道死活 → 通道活着就认定本批 0 篇。
    """
    r = call_cnki(expression, MAX_PER_SEARCH)
    if r and r.get("results"):
        rows = r["results"]
        flag = (f"  ⚠ 已达 {MAX_PER_SEARCH} 条上限，本批可能还有未取回的文献"
                if len(rows) >= MAX_PER_SEARCH else "")
        print(f"  [{label}] 取回 {len(rows)} 条{flag}")
        return r

    print(f"  [{label}] 本次空/被拦，跑控制探针判断通道死活…")
    if probe_alive(keywords):
        print(f"  [{label}] → 通道正常，本批 0 篇（不是风控）")
        return {"results": []}

    for i, wait in enumerate(RETRY_WAITS, 1):
        print(f"  [{label}] 控制探针也失败 → 真的被风控，等 {wait}s 重试（第 {i} 次）")
        time.sleep(wait)
        r = call_cnki(expression, MAX_PER_SEARCH)
        if r and r.get("results"):
            print(f"  [{label}] → 恢复，取回 {len(r['results'])} 条")
            return r
        if probe_alive(keywords):
            print(f"  [{label}] → 通道正常，本批 0 篇")
            return {"results": []}
    print(f"  [{label}] → 多次重试仍被风控，本批按 0 篇处理（可加大 --cooldown 重跑）")
    return {"results": []}


def year_range(years) -> tuple[str, str] | None:
    """--years 3 → (当年-2, 当年)；None → None（全部）。"""
    if not years or str(years).lower() == "all":
        return None
    n = int(years)
    this_year = datetime.now().year
    return str(this_year - n + 1), str(this_year)


def build_expr(keywords: list[str], journals: list[str] | None,
               yr: tuple[str, str] | None) -> str:
    tka = "+".join(f"'{k.strip()}'" for k in keywords if k.strip())
    parts = [f"TKA=({tka})"]
    if journals:
        ly = " OR ".join(f"LY='{j}'" for j in journals)
        parts.append(f"({ly})")
    if yr:
        parts.append(f"YE >= '{yr[0]}' AND YE <= '{yr[1]}'")
    return " AND ".join(parts)


def norm_title(s: str) -> str:
    """题名归一化（批间查重用）：去空白/标点，转小写。"""
    return re.sub(r"[\s\W_]+", "", str(s or "")).lower()


def to_record(r: dict) -> dict:
    """deepsearch 检索结果 → 题录 dict（键对齐 import_rows 的 META_KEYS）。

    注意：检索接口只给到 题名/作者/来源/日期（没有摘要、关键词、期、页码），
    需要完整题录（含摘要/关键词/页码）时走页面导出 + refs_to_jsonl.py。
    """
    date = str(r.get("date") or "")
    authors = r.get("authors") or []
    if isinstance(authors, str):
        authors = [a for a in re.split(r"[;,，]", authors) if a.strip()]
    src = str(r.get("source") or "")
    title = str(r.get("title") or "").strip()
    year = date[:4]
    rec = {
        "题名": title,
        "作者": ";".join(a.strip() for a in authors if a.strip()),
        "文献来源": src,
        "年": year,
        "出版时间": date,
    }
    rec["引文"] = f"{rec['作者']}.{title}[J].{src},{year}."
    return rec


def write_jsonl(records: list[dict], out: Path):
    import csv
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records)
                   + ("\n" if records else ""), encoding="utf-8")
    print(f"已写出 {len(records)} 条题录 → {out}")
    if records:                      # 顺带给一份 CSV 方便人看
        csvp = out.with_suffix(".csv")
        keys = ["题名", "作者", "文献来源", "年", "出版时间", "引文"]
        with csvp.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(records)
        print(f"已写出 CSV 预览 → {csvp}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keywords", required=True,
                    help="篇关摘检索词，多个用逗号/分号分隔（同字段 OR）")
    ap.add_argument("--scope", choices=["A", "C", "H"], default="A",
                    help="A=A刊清单(62本) C=C刊(CSSCI,页面流程) H=北核(页面流程)")
    ap.add_argument("--years", default="3",
                    help="近 N 年（默认 3）；all=全部")
    ap.add_argument("--journals", default=str(DEFAULT_JOURNALS),
                    help="A 刊清单文件（默认 references/期刊清单_A刊.txt）")
    ap.add_argument("--journals-limit", type=int, default=0,
                    help="只用清单前 N 本（小规模试跑用）")
    ap.add_argument("--batch", type=int, default=BATCH,
                    help=f"每批期刊数（默认 {BATCH}；批次取回条数顶到 60 时调小）")
    ap.add_argument("--cooldown", type=int, default=COOLDOWN,
                    help=f"批间冷却秒数（默认 {COOLDOWN}，被风控时可加大）")
    ap.add_argument("--count-only", action="store_true",
                    help="只统计篇数，不导出题录")
    ap.add_argument("--out", default="题录.jsonl")
    a = ap.parse_args()

    keywords = [k for k in re.split(r"[,，;；]", a.keywords) if k.strip()]
    if not keywords:
        raise SystemExit("检索词为空：--keywords \"词1,词2\"")
    yr = year_range(a.years)
    yrs_label = f"近 {a.years} 年（{yr[0]}-{yr[1]}）" if yr else "全部年份"

    print("=" * 56)
    print(f"  知网检索：篇关摘 {' + '.join(keywords)}；范围 {a.scope}；{yrs_label}")
    print("=" * 56)

    if a.scope in ("C", "H"):
        # C 列（CSSCI）/北核：知网专业检索没有"来源类别"字段，筛选在页面上做
        print("""
  ⚠ C 刊（CSSCI）/ 北核（北大核心）的筛选不在专业检索语法里，
    要在知网结果页左侧「来源类别」里勾选（篇数也在那里显示）。

  页面流程（智能体需能看见页面；看不见就先给智能体装眼睛）：
    1. 知网高级检索 → 检索字段选「篇关摘」→ 输入检索词
    2. 结果页左侧「来源类别」读篇数：CSSCI(N) / 北大核心(N)
    3. 勾选对应类别 → 套用「发表年度」时间筛选
    4. 勾选全部结果 → 导出与分析 → 自定义（题名/作者/文献来源/年/卷/期/
       页码/摘要/关键词/出版时间）→ EndNote 格式下载
    5. python scripts/refs_to_jsonl.py <下载的.txt> -o 题录.jsonl
    6. python scripts/import_rows.py --metadata 题录.jsonl --upload
""")
        return 0

    # A 刊：批量 LY 检索（全自动）
    journals = [l.strip() for l in
                Path(a.journals).read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.journals_limit:
        journals = journals[:a.journals_limit]
    print(f"A 刊清单 {len(journals)} 本，每批 {a.batch} 本，{yrs_label}\n")

    batches = [journals[i:i + a.batch] for i in range(0, len(journals), a.batch)]
    all_rows, seen, seen_titles, capped = [], set(), set(), 0
    for bi, batch in enumerate(batches, 1):
        expr = build_expr(keywords, batch, yr)
        r = search_with_retry(expr, f"批{bi}/{len(batches)}", keywords)
        rows = (r or {}).get("results") or []
        if len(rows) >= MAX_PER_SEARCH:
            capped += 1
        new = 0
        for row in rows:
            rec = to_record(row)
            u = row.get("url", "")
            tk = norm_title(rec["题名"])
            # 同一篇在知网常有 crossdb 两条记录（URL 不同）——题名归一化后
            # 也要查重，否则批间会出重复行（实测 104 篇里混进 7 条）
            if (u and u in seen) or (tk and tk in seen_titles):
                continue
            if u:
                seen.add(u)
            if tk:
                seen_titles.add(tk)
            all_rows.append(rec)
            new += 1
        print(f"  批{bi} 新增 {new}，累计 {len(all_rows)}\n")
        if bi < len(batches):
            time.sleep(a.cooldown)

    print("=" * 56)
    print(f"A 刊范围取回 {len(all_rows)} 篇（去重后；接口每批最多 {MAX_PER_SEARCH} 条）")
    print("⚠ 接口的 total_results 只是「返回条数」，不是知网总篇数；"
          "要报给同学的**精确篇数请在结果页读「共 X 条」**（需要眼睛）")
    if capped:
        print(f"⚠ 有 {capped} 个批次的取回条数达到上限 {MAX_PER_SEARCH}，"
              f"可能漏文献：建议 --batch 调小（如 6）或改用页面导出保证完整")
    if a.count_only:
        return 0
    if not all_rows:
        print("""本批全为 0 篇。若通道正常（脚本会打印控制探针结果），说明检索词在
所选范围/年份内确实没有文献——放宽年份或换关键词；只有控制探针也失败时才是
知网风控，隔几分钟重跑即可。""")
        return 1
    write_jsonl(all_rows, Path(a.out))
    print(f"""
下一步：
  python scripts/import_rows.py --metadata {a.out}
  （题录建行；已有 PDF 时加 --upload 一起传团队空间+回填链接）
  python scripts/fetch_abstracts.py --missing
  （接口题录不带摘要——用检索通道逐篇取知网摘要写进「摘要」列，
    相似度匹配防抓错，幂等可重跑）

  提示：这样导出的题录只有 题名/作者/来源/年/出版时间（接口不给摘要、关键词、
  期、页码）。要完整题录就在知网页面上导出 EndNote，再跑
  python scripts/refs_to_jsonl.py <导出文件> -o 题录.jsonl
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
