# cnki-annal

知网文献本地流水线：检索导出题录 → 有头浏览器批量下载 PDF → 抽取全文 → 逐篇精读出结构化 JSON → 汇总本地 Excel 两张表。

## 它做什么

```
refs_to_jsonl.py      EndNote(.enw)/RIS(.xml) 题录 → 流水线 JSONL（带摘要/关键词/卷期页/DOI）
cnki_export.py        知网检索导出题录（A刊清单62本/篇关摘/年份，批间冷却防风控，控制探针区分"真0篇"与风控）
cnki_pdf_grab.py      有头浏览器点「PDF下载」批量存本地（cnki-deepsearch 机构 cookie；宁缺勿错 0.62 相似度）
wf_fetch.py           万方兜底通道（patchright + 学校 WebVPN）
intake_manual.py      人工下载的 PDF 一键入库（复制+题名映射）
pdf_to_txt.py         PDF→全文 txt（PyMuPDF 优先/pypdf 兜底；<200字判扫描版不硬分析）
plan_batches.py       切待分析批次 batches.json（每批5篇，幂等重切）
audit.py              题名一对一体检（每波子代理跑完必跑）
audit_vars.py         变量准入体检（问卷/访谈/仿真参数/无来源 → 应删；仿真纯理论篇不该有变量）
build_excel.py        汇总两张表 → 提取结果.xlsx
cnki_stdio.py         cnki-deepsearch 的 stdio MCP 客户端（可控环境/cwd/超时）
```

## 输出版式（定稿）

**表1 文献表**：`文献信息 | 摘要 | 研究贡献 | 核心理论 | 研究方法 | 研究模型与假设`
——第一列整条引文，全表按发表年份升序；无题名/作者/来源/年/状态列。

**表2 变量表**：`文献信息 | 概念名称 | 变量类型 | 变量名称 | 变量测度 | 数据来源`

变量准入准则（定稿）：**只收研究者能从二手数据库取到数值的变量**。
- 仿真类（数值仿真/ABM/系统动力学/博弈/群决策/模糊评价/OR优化/网络建模）与纯理论类
  （书评/综述/政策阐释/概念辨析）→ `变量: []`，**只出现在表1，表2 里没有它的行**；
- 实证类逐变量过三问：①原文点名具名数据源 ②该源外部可获取 ③测度能由该源既有字段直接构造。
  任一不成立 → 该变量不提取；
- 一手问卷/访谈/实地调研/专家打分/实验操纵/仿真参数/模型决策变量/「原文未提及」→ 一律不收。

详见 `prompts/分析提示词.md` 的「变量准入复核」一节；跑完分析用 `scripts/audit_vars.py` 体检。

## 快速开始

```powershell
# 0) 检索导出题录（A刊+篇关摘+全部年份）
python scripts\cnki_export.py --keywords 零工 --scope A --years all --out 题录_零工_A刊.jsonl
# 1) 批量下载 PDF（弹窗属正常，验证码人工过一次）
python scripts\cnki_pdf_grab.py --metadata 题录_零工_A刊.jsonl --out PDF --sleep 8 --json > grab_report.log
# 2) 抽全文 + 切批次
python scripts\pdf_to_txt.py & python scripts\plan_batches.py
# 3) 子代理并行分析（模板见 SKILL.md）→ audit.py 体检
# 4) 汇总
python scripts\build_excel.py
```

依赖：`pip install pymupdf openpyxl`；mcporter + cnki-deepsearch（stdio 服务）；万方兜底另需 `pip install patchright cryptography` 与学校 WebVPN 凭据。完整流程、子代理指令模板与实战坑清单见 `SKILL.md`。

## 来源

由 2026 年 11 月"零工"主题 A 刊 59 篇全流程实战沉淀而来。
