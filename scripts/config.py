#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置加载与校验（论文入库分析流水线）。

所有用户数据（token、表格 ID、题录、提取结果）存放在用户目录
~/.cnki-tencent-pipeline，代码目录不含任何密钥与表格 ID——脱敏要求。
可用环境变量 PTP_HOME 覆盖配置目录位置（测试/多套配置用）。

首次使用：python scripts/setup.py 交互式生成 config.json。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

HOME = Path(os.environ.get("PTP_HOME") or (Path.home() / ".cnki-tencent-pipeline"))

CONFIG = HOME / "config.json"
STATE = HOME / "state.json"          # 下载/上传清单等运行数据
IN_DIR = HOME / "提取输入"            # --next 导出的论文全文 txt
OUT_DIR = HOME / "提取输出"           # AI 产出的提取 JSON
LOG = HOME / "pipeline.log"

REQUIRED = ("token", "file_id", "sheet_id")
DEFAULTS = {
    # PDF 目的地：团队知识库空间（与同学确认后填写；setup.py 可交互选择）
    "space_id": "",
    "folder_node_id": "",
    # 文献表列名（不同用户的表叫法可能不同，全部可配）
    "key_column": "引文",
    "url_column": "原文链接",
    "status_column": "分析状态",
    "abstract_column": "摘要",
    "c1": "研究贡献",
    "c2": "核心理论",
    "c3": "研究方法",
    "c4": "研究模型与假设",
    "link_ind_column": "关联指标",
    # 指标表
    "ind_sheet_id": "",
    "ind_columns": ["概念名称", "变量类型", "变量名称", "变量分类",
                    "变量测度", "数据来源"],
    "ind_link_doc_column": "关联文献行",
    # 题录文件（JSONL：一行一条，字段 题名/作者/文献来源/年/卷/期/页码/摘要/关键词/pdf）
    "metadata_file": "",
    "add_batch": 40,
    "timeout": 300,
    "mcp_url": "https://docs.qq.com/openapi/mcp",
}

# 提取结果里五个表格列对应的字段名（prompts/分析提示词.md 的 JSON 输出键）
EXTRACT_KEYS = ("摘要", "研究贡献", "核心理论", "研究方法", "研究模型与假设")

# 变量类型枚举与分类规则（与提示词一致；写入前校验用）
VAR_TYPES = ["因变量", "自变量", "中介/机制变量", "调节变量", "控制变量",
             "内生检验工具变量", "异质性分析维度变量", "其他变量"]
VAR_CATS = {
    "00": "创新要素特征", "01": "创新结果绩效", "02": "创新过程效能",
    "1": "高管相关", "2": "组织相关", "3": "财务相关",
    "4": "物", "5": "事", "6": "政府", "7": "社会资本", "8": "其他",
}


class Config(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


def parse_sheet_url(url: str) -> tuple[str, str]:
    """从智能表格 URL 提取 file_id 与子表 sheet_id。

    https://docs.qq.com/smartsheet/DcCCCCCCAAAAA?tab=tXXXXXX
        -> ("DcCCCCCCAAAAA", "tXXXXXX")
    """
    m = re.search(r"/smartsheet/([A-Za-z0-9]+)", url or "")
    t = re.search(r"[?&]tab=([A-Za-z0-9]+)", url or "")
    if not m:
        raise ValueError("URL 里没有 /smartsheet/<file_id> 段，请粘贴智能表格的完整网址")
    return m.group(1), (t.group(1) if t else "")


def parse_space_url(url: str) -> tuple[str, str]:
    """从知识库空间 URL 提取 space_id 与（可选）文件夹节点 id。

    https://docs.qq.com/space/DYYYY?resourceId=VZZZZ&type=folder&mode=wiki_mode
        -> ("DYYYY", "VZZZZ")
    """
    m = re.search(r"/space/([A-Za-z0-9]+)", url or "")
    r = re.search(r"[?&]resourceId=([A-Za-z0-9]+)", url or "")
    if not m:
        raise ValueError("URL 里没有 /space/<space_id> 段，请粘贴知识库空间的完整网址")
    return m.group(1), (r.group(1) if r else "")


def clean_token(raw: str) -> str:
    """容忍用户粘贴 'Token：xxx'、带引号、带空白等形式，提取出 token 本体。"""
    s = (raw or "").strip().strip("\"'，。 ")
    m = re.search(r"(?:token|令牌|密钥)\s*[:：=]\s*([A-Za-z0-9._-]+)", s, re.I)
    if m:
        s = m.group(1)
    return s.strip().strip("\"'")


def load(strict: bool = True) -> Config:
    if not CONFIG.exists():
        raise SystemExit(
            f"未找到配置 {CONFIG}\n"
            "首次使用请运行: python scripts/setup.py  （交互式生成配置）")
    d = json.loads(CONFIG.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if not d.get(k)]
    if strict and missing:
        raise SystemExit(f"配置缺少必填项: {missing}；重新运行 python scripts/setup.py")
    merged = {**DEFAULTS, **d}
    if strict and not merged.get("sheet_id"):
        raise SystemExit("配置缺少 sheet_id（子表 id）；请粘贴带 ?tab= 的表格网址重新运行 setup.py")
    c = Config(merged)
    for p in (HOME, IN_DIR, OUT_DIR):
        p.mkdir(parents=True, exist_ok=True)
    return c
