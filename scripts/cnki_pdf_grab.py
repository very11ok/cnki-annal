#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自动下载知网 PDF（纯本地版）：按题名搜 → 点「PDF下载」→ 存到本地目录。不上传任何云端。

为什么能行：知网全文要机构权限，而 cnki-deepsearch 的浏览器带着**已登录的
机构 cookie**（`~/.cnki_cookies.json`）去点下载，等于替你点了一下。实测约 5-25 秒/篇。
⚠ 必须**有头模式**（屏幕上有浏览器窗口）：实测无头模式点下载会 60 秒超时。
   首次若提示要登录/验证码，请在弹出窗口里完成一次，cookie 会记住。

用法（在文献工程目录里运行，工程目录约定见 SKILL.md）：
    python <skill>\\scripts\\cnki_pdf_grab.py --metadata 题录.jsonl        # 取题录里还没有本地 PDF 的
    python <skill>\\scripts\\cnki_pdf_grab.py --metadata 题录.jsonl --out PDF --sleep 8 --json > grab_report.log
    python <skill>\\scripts\\cnki_pdf_grab.py --titles "某篇题名" "另一篇"   # 单独补几篇
    --out PDF（默认 工程目录/PDF）  --sleep 8（篇间隔，防风控）  --json（末尾打印机器可读报告）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pdf_gaps_local as pdf_gaps  # noqa: E402
from cnki_stdio import CnkiStdio  # noqa: E402

BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
AUTHOR_PREFIX = re.compile(r"^[\u4e00-\u9fa5A-Za-z,，、;；\s]{2,40}[.．。]\s*")


def title_for_search(raw: str) -> str:
    """从"识别列原文"里取出干净的题名：砍作者前缀、砍 [J].期刊…DOI 尾巴。"""
    t = str(raw or "").strip()
    t = re.split(r"\[[JMDCNR]\]|【[JMDCNR]】|\bDOI\b", t)[0].strip(" .;；")
    t = AUTHOR_PREFIX.sub("", t)
    return t.strip(" .;；")[:80]


QUOTES = "\"'“”‘’《》「」『』"


def search_candidates(raw: str) -> list[str]:
    """篇名检索的候选串，由严到宽（去引号 → 只留主标题 → 去标点）。"""
    full = title_for_search(raw)
    cands = []
    if full:
        cands.append(full)
    no_q = re.sub(f"[{re.escape(QUOTES)}]", "", full).strip()
    if no_q and no_q not in cands:
        cands.append(no_q)
    main = re.split(r"[:：]|——|—|–", no_q)[0].strip(" .;；,，")
    if len(main) >= 8 and main not in cands:
        cands.append(main)
    tight = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", main)
    if len(tight) >= 8 and tight not in cands:
        cands.append(tight)
    return cands


def norm(s: str) -> str:
    return re.sub(r"[\s\u3000\-—_·,，.。:：;；()（）\[\]【】《》<>\"'/\\|?!！?]+",
                  "", str(s or "")).lower()


def pick_best(results: list[dict], want: str, min_ratio: float = 0.62):
    """从搜索结果里挑最匹配的一篇。宁可返回 None（转人工）也不抓错论文。"""
    w = norm(title_for_search(want))
    if not w:
        return None, 0.0
    best, best_r = None, 0.0
    for r in results or []:
        t = norm(r.get("title") or "")
        if not t:
            continue
        m = SequenceMatcher(None, w, t, autojunk=False)
        run = m.find_longest_match(0, len(w), 0, len(t)).size
        ratio = max(m.ratio(), run / max(1, min(len(w), len(t))))
        if t in w or w in t:
            ratio = max(ratio, 0.99)
        if ratio > best_r:
            best, best_r = r, ratio
    return (best if best_r >= min_ratio else None), best_r


def safe_name(name: str) -> str:
    n = BAD_CHARS.sub("_", str(name or "").strip())[:150].strip("_")
    return n or "未命名文献.pdf"


def grab_one(cli: CnkiStdio, raw_title: str, out_dir: Path,
             dl_root: Path, sleep_after: float = 0.0) -> dict:
    """搜 + 下 + 落到 out_dir。返回报告 dict。"""
    q = title_for_search(raw_title)
    rec: dict = {"题名": raw_title, "查询": q}
    results, tried = [], []
    for cand in search_candidates(raw_title):
        tried.append(cand[:40])
        # search_simple（一框式）会被风控拦；带摘要检索（字段=篇名）这条路实测稳定
        sr = cli.call("cnki_search_with_abstracts",
                      {"query": cand, "field": "篇名", "max_results": 8}, timeout=180)
        if sr.get("error") or not (sr.get("results") or []):
            sr = cli.call("cnki_search",
                          {"query": f"篇名:{cand}", "max_results": 8}, timeout=180)
        if sr.get("error"):
            err = str(sr["error"])
            if "暂无数据" in err or "没有找到" in err:
                continue
            rec.update({"ok": False, "why": f"检索失败：{err[:80]}"})
            return rec
        results = sr.get("results") or []
        if results:
            q = cand
            break
    rec["试过"] = tried
    if not results:
        rec.update({"ok": False,
                    "why": "篇名搜不到（已降级试过 " + str(len(tried)) + " 种写法，"
                           "多半题名与库内不一致，转文献求助）"})
        return rec
    best, ratio = pick_best(results, raw_title)
    if not best:
        rec.update({"ok": False, "why": f"搜到的都不够像（最高相似度 {ratio:.2f}），不抓错",
                    "候选": [r.get("title", "")[:40] for r in results[:3]]})
        return rec
    rec["相似度"] = round(ratio, 3)
    rec["知网题名"] = best.get("title", "")
    rec["来源"] = best.get("source", "")
    rec["年"] = (best.get("date") or "")[:4]

    dr = cli.call("cnki_download", {"url": best.get("url", "")}, timeout=240)
    if dr.get("error") or not dr.get("path"):
        why = str(dr.get("error") or "下载失败")[:100]
        rec.update({"ok": False, "why": f"{why}（窗口里若有验证码/登录页，请人工过一次再重跑）"})
        return rec
    src = Path(dr["path"])
    if not src.exists():
        rec.update({"ok": False, "why": f"下载接口说成功但文件不在：{src}"})
        return rec
    head = src.read_bytes()[:5]
    if not head.startswith(b"%PDF"):
        rec.update({"ok": False, "why": f"下到的不是 PDF（开头 {head!r}），可能要人工处理"})
        return rec
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / safe_name(src.name)
    if dest.exists():                    # 同名就加序号，别覆盖
        dest = out_dir / f"{dest.stem}_{int(time.time()) % 10000}{dest.suffix}"
    shutil.copy2(src, dest)
    rec.update({"ok": True, "文件": str(dest), "字节": dest.stat().st_size})
    if sleep_after:
        time.sleep(sleep_after)
    return rec


def collect_items(a) -> list[str]:
    if a.titles:
        return list(a.titles)
    if a.metadata:
        mp = Path(a.metadata)
        return [it["题名"] for it in pdf_gaps.check_metadata(
            mp, Path(a.out) if Path(a.out).exists() else None)]
    return []


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--titles", nargs="*", help="直接给题名（识别列原文也行）")
    ap.add_argument("--metadata", help="题录 JSONL：取还没有本地 PDF 的")
    ap.add_argument("--out", default="", help="PDF 存哪（默认 工程目录/PDF）")
    ap.add_argument("--limit", type=int, default=0, help="最多下几篇")
    ap.add_argument("--sleep", type=float, default=8.0, help="篇间隔秒数（防风控，默认 8）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not (a.titles or a.metadata):
        print(__doc__)
        return 1

    out_dir = Path(a.out) if a.out else (Path.cwd() / "PDF")
    items = collect_items(a)
    if a.limit:
        items = items[: a.limit]
    if not items:
        print("没有待下载的文献 ✓（题录都有本地 PDF，或 --titles 为空）")
        return 0
    print(f"待办 {len(items)} 篇 → 存到 {out_dir}")
    print("⚠ 屏幕上会出现浏览器窗口（有头模式才行；无头模式点下载会超时）")

    dl_root = Path(tempfile.gettempdir()) / "cnki_dl_raw"   # 别放 out_dir：避免递归重复扫到
    ok_list, bad_list = [], []
    cli = CnkiStdio(headed=True, cwd=dl_root, log=False)
    t0 = time.time()
    try:
        cli.start()
        print(f"✓ cnki-deepsearch 已就绪（{time.time() - t0:.1f}s）\n")
        for i, t in enumerate(items, 1):
            print(f"[{i}/{len(items)}] {title_for_search(t)[:46]}")
            rec = grab_one(cli, t, out_dir, dl_root,
                           sleep_after=(a.sleep if i < len(items) else 0))
            if rec.get("ok"):
                print(f"    ✓ {Path(rec['文件']).name}（{rec['字节'] // 1024} KB，"
                      f"相似度 {rec.get('相似度')}）")
                ok_list.append(rec)
            else:
                print(f"    ✗ {rec['why']}")
                bad_list.append(rec)
    finally:
        cli.close()

    rep = {"ok": len(ok_list), "failed": len(bad_list),
           "downloaded": ok_list, "manual": bad_list, "out": str(out_dir)}
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("\n" + "=" * 60)
        print(f"  下载成功 {len(ok_list)}，仍需人工 {len(bad_list)}")
        print("=" * 60)
        for rec in bad_list:
            print(f"  ⚠ 需你自己下载：{rec['题名'][:44]}")
            print(f"     原因：{rec['why']}")
            if rec.get("候选"):
                print(f"     知网里像的：{rec['候选']}")
        if bad_list:
            print("\n  人工下载后放入 PDF 目录，再跑 "
                  "python <skill>\\scripts\\intake_manual.py --pdf <文件路径>")
        if ok_list and not a.json:
            print("  下一步：python <skill>\\scripts\\pdf_to_txt.py  （PDF→全文 txt）")
    return 0 if not bad_list else 1


if __name__ == "__main__":
    sys.exit(main())
