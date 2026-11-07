#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""变量准入体检：抓出「不可能从二手数据库取到」的变量，以及仿真/纯理论论文被硬塞变量的情况。

背景：表2 的用途是给后续实证研究提供可获取的变量清单。所以：
    ① 仿真类 / 纯理论类论文应当 `变量: []`，只出现在表1；
    ② 其余论文的每条变量，数据来源必须是具名、外部可获取、能直接构造的二手数据源。

本脚本按此规则扫描 `提取输出/*.json`，把违规项列出来供人工处置（**不自动修改任何文件**）。

用法（在文献工程目录里运行）：
    python <skill>\\scripts\\audit_vars.py                # 全量体检
    python <skill>\\scripts\\audit_vars.py --strict       # 可疑项也算失败（退出码 1）
    python <skill>\\scripts\\audit_vars.py --json         # 机器可读
    python <skill>\\scripts\\audit_vars.py --file 行07.json

退出码：有「违规」→ 1；仅有「可疑」→ 0（--strict 时也返回 1）。
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

VAR_TYPES = ["因变量", "自变量", "中介/机制变量", "调节变量", "控制变量",
             "内生检验工具变量", "异质性分析维度变量", "其他变量"]

# 论文类型判定（看「研究方法」字段）
SIM_KEYS = ["仿真", "数值模拟", "数值实验", "系统动力学", "ABM", "多主体", "agent-based",
            "agent based", "博弈", "群决策", "模糊评价", "运筹", "优化模型", "数学建模",
            "网络建模", "情景模拟", "蒙特卡洛", "遗传算法", "元胞自动机"]
THEORY_KEYS = ["纯理论", "理论推演", "理论演绎", "逻辑演绎", "概念辨析", "文献综述",
               "书评", "政策阐释", "规范研究", "思辨"]

# 数据来源黑名单：命中即判「不可获取」，必须删除该变量
BAD_SOURCE = [
    "问卷", "量表", "访谈", "实地调研", "调研", "田野", "专家打分", "打分法",
    # 注意别写裸的「实验」：会把「哈佛大学增长实验室」「国家重点实验室」等机构名误判成实验法
    "实验法", "实验数据", "实验组", "实验操纵", "实验研究", "数值实验", "室内实验",
    "情景模拟", "内部资料", "企业内部", "课题组", "作者整理", "手工整理",
    "自行整理", "自建", "自采", "未公开", "涉密", "未获取",
]
# 「原文未提及」这类等于无法定位数据源
NO_SOURCE = ["原文未提及", "未提及", "未说明", "未明确", "不详", "无", ""]

# 数据来源白名单特征（只用于判断「来源是否像样」，不强制）
GOOD_HINTS = [
    "CSMAR", "国泰安", "Wind", "万得", "同花顺", "iFinD", "CNRDS", "CCER", "EPS",
    "CEIC", "中经网", "巨潮", "年报", "公告", "招股说明书", "统计年鉴", "年鉴",
    "海关", "专利", "CNIPA", "知识产权局", "裁判文书", "信用信息公示",
    "CGSS", "CFPS", "CHARLS", "CLDS", "CHIP", "世界银行", "WDI", "OECD",
    "Comtrade", "CEPII", "BACI", "Penn", "Compustat", "CRSP", "Datastream",
    "Osiris", "BvD", "Bloomberg", "交易所", "统计局", "证监会",
    "财政部", "人民银行", "银保监", "社会责任报告", "ESG", "天眼查", "企查查",
    # 实测出现过的具名商业库 / 公开库 / 官方平台（白名单是示例非穷举）
    "FactSet", "Revere", "IncoPat", "fDi Markets", "FRED", "UIBE", "GVC",
    "CEADs", "cnefn", "GTA", "WITS", "TRAINS", "UNCTAD", "ICIO", "TiVA",
    "WIOD", "EUKLEMS", "ICRG", "EIU", "经济学人", "Growth Lab", "Harvard",
    "清科", "Zero2IPO", "私募通", "华证", "RESSET", "锐思", "迪博", "数林",
    "北大法宝", "法律信息平台", "海洋科学数据", "应急管理", "工信部", "发改委",
    "教育部", "科技部", "国家税务总局", "国家知识产权", "中国裁判文书",
    "高新技术企业认定", "V-Lab", "创新力报告", "市场化指数报告", "复旦大学",
    "王小鲁", "开放数林", "指数网",
    # 第二批 27 篇实测出现的具名来源（2026-11-07）
    "58同城", "气候政策不确定性", "政商关系评价报告", "中国人民大学",
    "营商环境研究报告", "世界不确定性指数", "WUI", "气象科学数据", "CNMDC",
    "中国工业企业数据库", "税收调查", "数字普惠金融", "人民政府网站",
    "人力资源和社会保障", "人社部门", "WinGo", "文构财经", "联合国贸易和发展",
    "国家金融监督管理总局", "金融机构网点",
]
# 含糊表述：写了等于没写，判「可疑」等人工确认（注意别把 "CSMAR数据库" 误判，
# 所以只匹配这些整体含糊词，不单独把「数据库」当白名单）
VAGUE_HINTS = ["相关数据库", "某数据库", "各类数据库", "多种数据库", "相关数据",
               "公开数据", "二手数据", "数据库整理", "公开资料"]


def has_any(s: str, keys) -> str | None:
    for k in keys:
        if k and k in s:
            return k
    return None


def audit_one(jf: Path) -> dict:
    """返回 {file, title, vars, violations[], suspects[], kind}"""
    out = {"file": jf.name, "title": "", "vars": 0, "violations": [], "suspects": [], "kind": ""}
    try:
        d = json.loads(jf.read_text(encoding="utf-8"))
    except Exception as e:
        out["violations"].append(f"JSON 解析失败：{e}")
        return out

    title = str(d.get("题名") or "")
    out["title"] = title[:40]
    method = f'{d.get("研究方法") or ""} {d.get("研究模型与假设") or ""}'
    vs = d.get("变量") or []
    out["vars"] = len(vs)

    sim_hit = has_any(method, SIM_KEYS)
    theory_hit = has_any(method, THEORY_KEYS)
    if sim_hit or theory_hit:
        out["kind"] = "仿真" if sim_hit else "纯理论"
        if vs:
            out["violations"].append(
                f'判定为{out["kind"]}类（研究方法含「{sim_hit or theory_hit}」），'
                f'按准则应为 变量:[]，实际有 {len(vs)} 条')

    for i, v in enumerate(vs, 1):
        src = str(v.get("数据来源") or "").strip()
        name = str(v.get("变量名称") or v.get("概念名称") or "")[:20]
        vt = str(v.get("变量类型") or "")
        tag = f"第{i}条「{name}」"

        if vt not in VAR_TYPES:
            out["violations"].append(f"{tag} 变量类型非法：「{vt}」")

        if src in NO_SOURCE or not src:
            out["violations"].append(f"{tag} 数据来源为空/「{src}」→ 无法定位，应删")
            continue

        bad = has_any(src, BAD_SOURCE)
        if bad:
            out["violations"].append(f"{tag} 数据来源含「{bad}」（{src[:30]}）→ 不可获取，应删")
            continue

        # 含糊词只在「整条来源都很短」时才算问题；像
        # 「cnefn.com公开数据库（https://www.cnefn.com/…）」虽含"公开数据"却是具名库，不该误报
        if not has_any(src, GOOD_HINTS) or (len(src) <= 14 and has_any(src, VAGUE_HINTS)):
            out["suspects"].append(f"{tag} 数据来源「{src[:30]}」不够具体 → 人工确认能否定位到具名库")
        if "原文未提及" in src or "未提及" in src:
            out["violations"].append(f"{tag} 数据来源写「未提及」→ 应删")
        for need in ("概念名称", "变量测度"):
            if not str(v.get(need) or "").strip():
                out["suspects"].append(f"{tag} 缺「{need}」")
    return out


def main():
    ap = argparse.ArgumentParser(description="变量准入体检（只报不改）")
    ap.add_argument("--dir", default=".", help="文献工程目录（默认当前目录）")
    ap.add_argument("--file", default="", help="只体检某一个 JSON")
    ap.add_argument("--strict", action="store_true", help="可疑项也算失败")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    a = ap.parse_args()
    base = Path(a.dir).resolve()

    if a.file:
        files = [base / "提取输出" / a.file]
        if not files[0].exists():
            files = [Path(a.file)]
    else:
        files = sorted((base / "提取输出").glob("*.json"))
    if not files:
        raise SystemExit(f"{base / '提取输出'} 下没有 .json（先跑分析）")

    results = [audit_one(f) for f in files if f.exists()]
    n_var = sum(r["vars"] for r in results)
    n_bad = sum(len(r["violations"]) for r in results)
    n_sus = sum(len(r["suspects"]) for r in results)
    empty = sum(1 for r in results if r["vars"] == 0)
    simtheory = [r for r in results if r["kind"]]

    if a.json:
        print(json.dumps({"files": len(results), "vars": n_var,
                          "violations": n_bad, "suspects": n_sus,
                          "detail": results}, ensure_ascii=False, indent=1))
    else:
        print(f"体检 {len(results)} 篇｜变量 {n_var} 条｜空变量篇 {empty}")
        if simtheory:
            print(f"仿真/纯理论判定 {len(simtheory)} 篇（这些篇按准则不应有变量行）")
        print("-" * 64)
        for r in results:
            if not (r["violations"] or r["suspects"]):
                continue
            head = f'{r["file"]}  {r["title"]}'
            if r["kind"]:
                head += f'  [{r["kind"]}]'
            print(head)
            for v in r["violations"]:
                print(f'   ✗ {v}')
            for s in r["suspects"]:
                print(f'   ? {s}')
        print("-" * 64)
        print(f"违规 {n_bad} 处｜可疑 {n_sus} 处")
        if n_bad == 0 and n_sus == 0:
            print("全部通过准入 ✓")
        elif n_bad:
            print("→ 修改对应 JSON 的变量数组（删掉违规项）后重跑；再 build_excel.py")

    if n_bad:
        return 1
    return 1 if (a.strict and n_sus) else 0


if __name__ == "__main__":
    sys.exit(main())
