# -*- coding: utf-8 -*-
"""WHUT WebVPN 通道：host 加密 + 登录 + 通过 webvpn 取一篇 PDF 验证。

host 加密（实测复现）：
    prev = key = b"wrdvpnisthebest!"
    ct_i = pt_i XOR AES_encrypt(prev, key);  prev = ct_i
    token = hex(IV) + hex(ct)
URL = https://webvpn.whut.edu.cn/<scheme>/<token>/<path>
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

KEY = b"wrdvpnisthebest!"
VPN = "https://webvpn.whut.edu.cn"


def _aes(b: bytes) -> bytes:
    e = Cipher(algorithms.AES(KEY), modes.ECB()).encryptor()
    return e.update(b) + e.finalize()


def enc_host(host: str) -> str:
    pt = host.encode()
    prev, out = KEY, b""
    for i in range(0, len(pt), 16):
        blk = pt[i:i + 16]
        ks = _aes(prev)
        ct = bytes(a ^ b for a, b in zip(blk, ks))
        out += ct
        prev = ct
    return KEY.hex() + out.hex()


def vpn_url(url: str) -> str:
    """把 https://host/path 改写成 webvpn 地址。"""
    scheme, rest = ("https", url[8:]) if url.startswith("https://") else ("http", url[7:])
    host, _, path = rest.partition("/")
    return f"{VPN}/{scheme}/{enc_host(host)}/{path}"


if __name__ == "__main__":
    sample = "77726476706e69737468656265737421e7fe5298283d2653760986b4915b247bfa5069"
    got = enc_host("wisdom.chaoxing.com")
    print("复现样本:", got == sample, "|", got)
    print("例：", vpn_url("https://www.sciencedirect.com/science/article/pii/S0048733324001234")[:120])
