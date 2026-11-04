# -*- coding: utf-8 -*-
"""万方批量取中文全文（经 WHUT WebVPN）。

流程（单篇）：检索 q=题名 → 点标题开详情页 → 校验标题相似度与刊名/年 →
点「下载」拿中间页 → 解析 iframe#downloadIframe → GET NewFulltext → 校验 %PDF。

用法：
  python wf_fetch.py --meta 题录.jsonl --pdf-dir PDF目录 [--limit N] [--start K]
                      [--pause 6] [--report 报告.json] [--rows 12,34,56]
说明：题录 JSONL 的第 i 行 = 表格第 i 行（1 基）；报告记每篇状态，便于续跑。
"""
import argparse
import html as htmlmod
import json
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from patchright.sync_api import sync_playwright  # noqa: E402

from webvpn_url import vpn_url  # noqa: E402

PROFILE = str(Path(__file__).resolve().parent.parent / "browser-profile")
BASE = "https://webvpn.whut.edu.cn"
BAD = re.compile(r'[\\/:*?"<>|\n\r\t]')


def norm(s):
    return re.sub(r"[^\u4e00-\u9fa5a-z0-9]+", "", (s or "").lower())


def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def fname(m):
    au = (m.get("作者") or "").split(";")[0].split(",")[0].strip() or "佚名"
    return BAD.sub("_", f"{m['题名'][:110]}_{au}").strip("_ ")


def detail_of(page, ctx, title, journal, year):
    """在检索页点开最像的一条，返回 (detail_page, score, 实际题名)。"""
    page.goto(vpn_url("https://s.wanfangdata.com.cn/paper?q=" + quote(title[:45])),
              wait_until="domcontentloaded", timeout=60000)
    time.sleep(9)
    cards = page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('div.title-area').forEach(e => {
            const t = (e.innerText || '').replace(/\\s+/g, ' ').trim();
            if (t.length > 6) out.push(t);
        });
        return out.slice(0, 10);
    }""")
    best = (0.0, None)
    for t in cards:
        s = sim(title, t)
        if s > best[0]:
            best = (s, t)
    if best[0] < 0.62:
        return None, best[0], best[1] or ""
    # 点最像那条（按题名前 12 字定位）
    key = (best[1] or title)[:12]
    try:
        with ctx.expect_page(timeout=25000) as pinfo:
            page.get_by_text(key, exact=False).first.click()
        dp = pinfo.value
    except Exception:
        return None, best[0], best[1] or ""
    try:
        dp.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass
    time.sleep(6)
    return dp, best[0], best[1] or ""


def fetch_one(ctx, page, m, out_dir, pause):
    title = m.get("题名", "")
    dp, score, real = detail_of(page, ctx, title, m.get("来源", ""), m.get("年", ""))
    rec = {"题名": title, "相似度": round(score, 3), "万方题名": real[:60], "ok": False, "how": ""}
    if dp is None:
        rec["how"] = "检索无匹配"
        return rec
    try:
        head = dp.inner_text("body")[:400]
        # 刊名/年粗校验（详情页含《刊名》与年份）
        if m.get("来源") and m["来源"][:6] not in head and sim(m["来源"], head) < 0.3:
            rec["how"] = "刊名不符"
            dp.close()
            return rec
        href = dp.evaluate("() => { const a = document.querySelector('a.download.buttonItem');"
                           " return a ? a.getAttribute('href') : ''; }")
        if not href:
            rec["how"] = "无下载按钮"
            dp.close()
            return rec
        r = ctx.request.get(href, headers={"Referer": dp.url}, timeout=90000)
        mid = r.body().decode("utf-8", "replace")
        tag = re.search(r"<iframe[^>]*downloadIframe[^>]*>", mid)
        if not tag:
            rec["how"] = "中间页无 iframe（可能无权限/需登录）"
            dp.close()
            return rec
        src = htmlmod.unescape(re.search(r'src="([^"]+)"', tag.group(0)).group(1))
        u = src if src.startswith("http") else BASE + src
        r2 = ctx.request.get(u, headers={"Referer": href}, timeout=180000)
        body = r2.body()
        if body[:4] != b"%PDF":
            rec["how"] = f"NewFulltext 非 PDF（{r2.status}）"
            dp.close()
            return rec
        p = out_dir / (fname(m) + ".pdf")
        p.write_bytes(body)
        rec.update(ok=True, how=f"万方 {len(body) // 1024}KB", file=p.name)
    except Exception as e:
        rec["how"] = "异常：" + str(e)[:60]
    finally:
        try:
            dp.close()
        except Exception:
            pass
    time.sleep(pause)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", required=True)
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--report", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--rows", default="")
    ap.add_argument("--pause", type=float, default=6.0)
    a = ap.parse_args()

    meta = [json.loads(l) for l in Path(a.meta).read_text(encoding="utf-8").splitlines() if l.strip()]
    out_dir = Path(a.pdf_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rep_path = Path(a.report) if a.report else out_dir.parent / "wf_report.json"
    report = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.exists() else {}

    want = {int(x) for x in a.rows.split(",") if x.strip()} if a.rows else set()
    idx = [i for i in range(a.start, len(meta) + 1) if (i in want if want else True)]
    # 已下载的跳过
    todo = []
    for i in idx:
        m = meta[i - 1]
        if (out_dir / (fname(m) + ".pdf")).exists() or report.get(str(i), {}).get("ok"):
            continue
        todo.append(i)
    if a.limit:
        todo = todo[:a.limit]
    print(f"待取 {len(todo)} 篇（题录 {len(meta)}）", flush=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=False, channel="chrome",
            args=["--window-position=-2000,-2000", "--window-size=1500,1000"],
            accept_downloads=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        if "/login" in page.url:
            print("⚠ WebVPN 掉线，需重新登录（窗口已开）", flush=True)
        for n, i in enumerate(todo, 1):
            m = meta[i - 1]
            print(f"[{n}/{len(todo)}] 行{i} {m['题名'][:38]}", end=" ", flush=True)
            rec = fetch_one(ctx, page, m, out_dir, a.pause)
            report[str(i)] = rec
            print(("✅ " if rec["ok"] else "✗ ") + rec["how"], flush=True)
            if n % 5 == 0 or not rec["ok"]:
                rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        ctx.close()
    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for r in report.values() if r.get("ok"))
    print(f"完成：成功 {ok}｜累计记录 {len(report)}", flush=True)


if __name__ == "__main__":
    main()
