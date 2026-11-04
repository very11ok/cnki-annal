---
name: cnki-annal
description: 知网文献纯本地流水线——检索导出题录（A刊清单/篇关摘/年份三选项，篇数当场展示）、有头浏览器批量下载 PDF 到本地、PyMuPDF 抽全文、子代理按分析提示词逐篇精读出 JSON、汇总成本地 Excel 两张表（表1 文献信息+摘要+四段按年排序 / 表2 逐变量）。用户说"把知网文献爬下来存本地 / 知网检索下载PDF / A刊文献批量下载并读出Excel表 / 文献本地分析两张表"时使用。
---

# 知网文献 → 本地 PDF + 本地 Excel 两张表 · 纯本地流水线

检索、下载、分析、汇总全部在本地完成：PDF 存工程目录 `PDF\`，分析结果写本地 Excel。

## 工程目录约定

所有脚本都"在文献工程目录里运行"（cwd 或 `--dir` 指定）。工程目录结构：

```
<工程>/
  题录_<主题>_<范围>.jsonl/.csv   # 第0步检索产出（题名/作者/文献来源/年/出版时间/引文）
  PDF/                            # 下载的全文 PDF
  映射.json                       # {"PDF文件名": "题录题名"}（fix_mapping/intake_manual 维护）
  提取输入/                        # pdf_to_txt 产出：同名 .txt + 清单.json（文件名→题名/字数/状态）
  提取输出/                        # 每篇一个分析 JSON（与 txt 同名）
  batches.json                    # plan_batches 切出的待分析批次
  提取结果.xlsx                    # build_excel 产出两张表（最终交付物）
```

## 流水线总览

```
第0步 cnki_export.py 检索导出题录 ──► cnki_pdf_grab.py 批量下载 PDF（有头浏览器，纯本地）
        │                                      │（下不动的 → wf_fetch.py 万方兜底 → 人工+intake_manual.py）
        ▼                                      ▼
   plan_batches.py 切批次 ◄── pdf_to_txt.py 抽全文 txt
        │
        ▼
   子代理并行逐篇分析（prompts/分析提示词.md）──► audit.py 体检
        │
        ▼
   build_excel.py 汇总两张表（按年排序）──► 交付 提取结果.xlsx
```

## 第 0 步：知网检索导出（题录）

```powershell
# A 刊（62 本清单）+ 篇关摘 + 近3年（--years 5 / all 可改）；--count-only 只看篇数
python <skill>\scripts\cnki_export.py --keywords 零工 --scope A --years all --out 题录_零工_A刊.jsonl
```

- 专业检索 `TKA=('词') AND (LY='刊1' OR …)`；62 本 A 列表每批 12 本、批间冷却 60s；
  **串行调用**，一次一个请求。C 刊（CSSCI）/北核不在专业检索语法里，只能页面筛（需眼睛）。
- 接口题录只有 题名/作者/来源/年/出版时间（+脚本拼的引文），**无摘要/关键词/卷期页**——
  摘要在下载后从全文里取（分析提示词的摘要字段），不阻塞流程。
- **接口 total_results 只是返回条数**；每批顶 60 条上限会打印 ⚠，此时 `--batch 6` 或按单刊重查。
- "暂无数据"≠风控：脚本内置控制探针（最宽条件再问一次），探针活着=本批真 0 篇。

## 第 1 步：批量下载 PDF（纯本地）

```powershell
python <skill>\scripts\cnki_pdf_grab.py --metadata 题录_零工_A刊.jsonl --out PDF --sleep 8 --json > grab_report.log
```

- **必须有人盯着屏幕**：有头浏览器会弹窗，验证码/登录页人工过一次（cookie 记住，今后再用就免登）。
- 挑结果用归一化题名相似度 ≥0.62（宁缺勿错）；篇间隔 8s 防风控；约 5-25 秒/篇。
- `--json` 报告含 downloaded（题名↔文件）与 manual（失败原因）两个清单。
- 失败重试一轮 `--titles "题名"…`；仍失败走兜底：
  - 万方：`python <skill>\scripts\wf_fetch.py --meta 题录.jsonl --pdf-dir PDF --pause 5`
    （依赖 `pip install patchright cryptography`；经学校 WebVPN，掉线时**弹窗等人工登录**——
    无人点窗口必然 0 匹配，别反复空跑，直接转人工）。
  - 人工下载后：`python <skill>\scripts\intake_manual.py --pdf "下载路径.pdf"`（复制入 PDF\ + 登记映射）。
- 知网个别文献 `cnki_download` 稳定 240s 超时（检索能命中、点击流卡），三轮重试无效就果断转人工。

## 第 2 步：抽全文 → 切批次

```powershell
python <skill>\scripts\pdf_to_txt.py          # PDF→txt（PyMuPDF 优先；<200 字判"扫描版"不硬分析）
python <skill>\scripts\plan_batches.py        # 切 batches.json（每批 5 篇；已分析自动跳过）
```

## 第 3 步：子代理并行逐篇分析（AI 的职责）

每个子代理负责 batches.json 里一个批次（下标 i）。**指令模板（原样发给子代理）**：

> 你是经管实证论文阅读专家，负责逐篇精读论文全文并按固定规则提取结构化信息。
> 第一步：先完整读取规则文件 `<skill>\prompts\分析提示词.md`，之后每一篇都严格按该文件全部规则执行。
> 任务：直接读 `<工程>\batches.json`，取第 N 个批次数组（下标 i）里的条目，每个条目有 txt 和 题名
> 字段，逐篇处理：读完 txt 产出一个严格 JSON 文件，写到 `<工程>\提取输出\` 目录，文件名 = txt 同名
> 但扩展名 .json。
> 硬性要求：
> - JSON 键固定为：题名/摘要/研究贡献/核心理论/研究方法/研究模型与假设/变量。
> - "题名"必须用批次条目里给的规范题名原文照抄（用于后续与题录匹配），不要改成 PDF 文件名或加副标题变体。
> - 摘要 200-400 字照抄论文自述摘要；原文无摘要则概括并注明「原文无摘要，根据正文概括」。
> - 研究贡献/核心理论/研究方法/研究模型与假设各为一个自然段，禁止列表、编号、加粗、换行符；
>   每一句都必须能在原文找到出处，禁止臆造拓展；原文未明确的照实写「原文未明确提及」。
> - "变量"是数组：实证论文逐变量一行（概念名称/变量类型/变量名称/变量测度/数据来源），
>   变量类型枚举严格按规则文件；概念/定性/综述类论文若无变量给空数组 []。
> - 只输出 JSON 文件，禁止输出任何评论文字进文件；UTF-8 编码。
> - 全部处理完后，回报：每篇 JSON 路径 + 变量条数 + 有无拿不准的地方（简短列出）。
>   不要修改任何论文 txt 或其他目录文件。

并发与配额：一波 3-6 个子代理为宜；遇到 "Allocated quota exceeded" 说明模型网关限流，
**减小并发重发**即可（子代理可能已写完部分文件，plan_batches 会按实际缺口重切）。

## 第 4 步：体检 + 汇总

```powershell
python <skill>\scripts\audit.py               # 题名一对一 + 枚举体检
python <skill>\scripts\build_excel.py         # 两张表 → 提取结果.xlsx（按年升序）
```

- **铁律：每波子代理跑完必须跑 audit.py / plan_batches.py 核对唯一题名数，
  绝不信子代理自报的"5/5 完成"**——实测有子代理错读批次下标，把别批产物当成
  本批已存在、只写 1 篇还报成功。
- build_excel 的枚举/缺字段校验会打印问题清单并以退出码 1 结束，按提示补齐后重跑。

## 输出版式（用户定稿，勿擅改）

**表1 文献表**：`文献信息 | 摘要 | 研究贡献 | 核心理论 | 研究方法 | 研究模型与假设`
- 第一列「文献信息」= 整条引文（`作者.题名[J].来源,年.`）；**没有**题名/作者/文献来源/年列，
  也**没有**全文txt/分析状态列；
- 全表**按发表年份升序**（同年按引文字符串）；尚无分析 JSON 的题录也占一行（其余列空）。

**表2 变量表**：`文献信息 | 概念名称 | 变量类型 | 变量名称 | 变量测度 | 数据来源`
- 第一列同为引文；行序随表1。

## 已踩过的坑（改代码前必读）

| 现象 | 真因 | 正解 |
|---|---|---|
| 子代理报"5/5 完成"但磁盘上只有 1 篇 | 错读 batches.json 下标，把别的批次产物当成本批已存在 | 每波跑完 `audit.py`+`plan_batches.py` 核对唯一题名数；补跑前必须重切 batches.json |
| `Allocated quota exceeded` | 模型网关限流（并发大上下文子代理挤爆 TPM） | 减小并发（一次 2-3 个）重发；已写完的文件不会重复写 |
| 知网下载稳定 240s 超时 | 个别文献下载端点击流卡死/风控，检索正常 | 重试 ≤3 次；不行就转万方/人工，别循环空跑 |
| 万方"检索无匹配" | WebVPN 掉线且弹窗没人登录 | wf_fetch 弹窗需人工登录；无人值守时直接转人工清单 |
| PDF 文件名≠题录题名（带作者尾巴/全角标点） | cnki_download 落盘名与题录不一致 | 用 grab `--json` 报告维护 映射.json（fix_mapping/intake_manual），pdf_to_txt 优先用映射题名 |
| 某批"暂无数据" | 检索结果为 0 也报这句（不一定是风控） | 脚本内置控制探针自动区分，探针活着=真 0 篇 |
| 批次顶 60 条上限 | 每次检索最多返回 60 条 | `--batch 6` 或按单刊重查；出现 ⚠ 标记的批次必须拆 |
| 知网 PDF 抽不出正文 | 扫描版或 PyMuPDF 缺失 | `<200 字` 判扫描版，不进分析；装 pymupdf 后重跑 pdf_to_txt |
| cmd.exe 里 python -c 多行脚本断掉 | 多行 -c 在 Windows shell 解析失败 | 一律写临时 .py 文件执行，不用内嵌多行 -c |
| 子代理上下文过载 | 一个代理塞太多篇 | 每批 5 篇以内；单篇全文 2-3 万字 |

## 依赖

- mcporter + cnki-deepsearch（stdio 服务 `~/.local/bin/cnki-deepsearch.EXE`，机构 cookie `~/.cnki_cookies.json`）
- `pip install pymupdf openpyxl`（抽文本/Excel）
- 可选（万方兜底）：`pip install patchright cryptography` + 学校 WebVPN 凭据（DPAPI `LIB_USER/LIB_PASS`）
- 凭据一律走环境变量或既有配置文件，不入库、不回显
