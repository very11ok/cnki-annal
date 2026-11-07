# -*- coding: utf-8 -*-
"""知网机构 IP 登录态续期（纯 HTTP，不需要浏览器）。

背景：机构会话 cookie 有硬过期时间（cookie `c_m_expire`，实测约 24h）。过期后
bar 下单接口 302 到 login.cnki.net，直拉脚本只会看到「返回的不是 PDF」，
表现为"限流/验证"，实际是登录态掉了。

解决：复刻登录页「IP 登录」按钮调用的接口
    POST https://login.cnki.net/TopLoginCore/api/LoginApi/IpLoginPo
    Content-Type: application/json
响应是 JSONP 风格 (`({...})`)，IsSuccess=true 即续期成功，服务端会下发新的
LID / Ecp_LoginStuts / c_m_LinID / c_m_expire，本模块把它们写回 cookie 文件。

用法：
    python cnki_session.py                 # 续期并打印结果
    python cnki_session.py --check        # 只检查当前登录态是否还有效

也可被其他脚本 import：from cnki_session import renew; renew(cookie_file)
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
requests.packages.urllib3.disable_warnings()

DEFAULT_COOKIE = Path.home() / ".cnki_cookies.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
LOGIN_PAGE = "https://login.cnki.net/login/?platform=nxgp"
API = "https://login.cnki.net/TopLoginCore/api/LoginApi/IpLoginPo"
RETURN_URL = "https://kns.cnki.net/kns8s/defaultresult/index"


def _load(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"cookie 文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _session(cookies: list[dict]) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    for c in cookies:
        try:
            s.cookies.set(c["name"], c.get("value", ""),
                          domain=c.get("domain", ""), path=c.get("path", "/"))
        except Exception:
            pass
    return s


def check(cookie_file: Path = DEFAULT_COOKIE) -> dict:
    """检查登录态：取一篇详情页，看 pdfDown 下单是否跳登录。"""
    cookies = _load(cookie_file)
    s = _session(cookies)
    try:
        r = s.get(RETURN_URL, timeout=25, verify=False, allow_redirects=False)
        if r.status_code in (301, 302, 303) and "login" in (r.headers.get("Location") or ""):
            return {"ok": False, "why": "首页即跳登录"}
        return {"ok": True, "why": "首页可达"}
    except Exception as e:
        return {"ok": False, "why": f"请求异常 {e}"}


def renew(cookie_file: Path = DEFAULT_COOKIE, verbose: bool = True) -> bool:
    """刷新机构 IP 登录态并写回 cookie 文件；成功返回 True。"""
    cookie_file = Path(cookie_file)
    cookies = _load(cookie_file)
    s = _session(cookies)

    try:
        from urllib.parse import quote
        s.get(LOGIN_PAGE + "&returnUrl=" + quote(RETURN_URL, safe=""),
              timeout=30, verify=False)
        param = {"isAutoLogin": True, "isForceLogin": False, "p": "0",
                 "verifyType": 3, "platform": "nxgp", "fingerprint": ""}
        qs = {"isAutoLogin": "true", "isForceLogin": "false", "p": "0",
              "verifyType": "3", "platform": "nxgp", "fingerprint": ""}
        r = s.post(API, params=qs, json=param, timeout=45, verify=False,
                   headers={"Referer": LOGIN_PAGE, "Origin": "https://login.cnki.net"})
        r.encoding = "utf-8"
        text = r.text.strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]
        data = json.loads(text)
    except Exception as e:
        if verbose:
            print(f"[cnki_session] 续期请求失败：{e}")
        return False

    if not data.get("IsSuccess"):
        if verbose:
            print(f"[cnki_session] 续期被拒：{data.get('ErrorMsg') or data.get('Msg')}")
        return False

    # 把 session jar 里的新值写回 Playwright 格式的 cookie 文件
    jar = list(s.cookies)
    idx = {}
    for i, c in enumerate(cookies):
        idx[(c["name"], str(c.get("domain", "")).lstrip("."))] = i
    updated = 0
    for c in jar:
        key = (c.name, str(c.domain or "").lstrip("."))
        if key in idx:
            old = cookies[idx[key]]
            old["value"] = c.value
            if c.expires:
                old["expires"] = int(c.expires)
            updated += 1
        elif c.name in {"LID", "c_m_LinID", "c_m_expire", "Ecp_LoginStuts",
                        "Ecp_UserInfo", "Ecp_notFirstLogin"}:
            cookies.append({"name": c.name, "value": c.value,
                            "domain": c.domain or ".cnki.net", "path": c.path or "/",
                            "expires": int(c.expires) if c.expires else -1,
                            "httpOnly": False, "secure": False, "sameSite": "Lax"})
            updated += 1
    cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    if verbose:
        print(f"[cnki_session] 续期成功：{data.get('ShowName') or data.get('UserName')} "
              f"| 账号 {data.get('UserName')} | 有效至 {data.get('Expire')} "
              f"| 写回 {updated} 个 cookie → {cookie_file}")
    return True


def main():
    ap = argparse.ArgumentParser(description="知网机构 IP 登录态续期")
    ap.add_argument("--cookie", default=str(DEFAULT_COOKIE))
    ap.add_argument("--check", action="store_true", help="只检查不续期")
    a = ap.parse_args()
    p = Path(a.cookie)
    if a.check:
        print(json.dumps(check(p), ensure_ascii=False))
        return 0
    return 0 if renew(p) else 1


if __name__ == "__main__":
    raise SystemExit(main())
