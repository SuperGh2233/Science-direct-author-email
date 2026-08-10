# -*- coding: utf-8 -*-
"""
指纹实验室 Lab1 —— TLS / HTTP2 指纹对比
目的: 直观看到 requests 和 curl_cffi(伪装 Chrome) 在 TLS 指纹上的差异
目标端点: tls.peet.ws (一个专门用于指纹检测的中立测试服务, 非任何受保护站点)
学习点: 为什么 requests 会被 Cloudflare 一眼识破, 而 curl_cffi 不会
"""
import json
import urllib.request

# 三个客户端: urllib / requests(若装了) / curl_cffi(伪装 chrome)
TARGET = "https://tls.peet.ws/api/all"

def fetch_urllib():
    """原生 urllib —— Go 标准库指纹"""
    req = urllib.request.Request(TARGET, headers={"User-Agent": "python-urllib/3.13"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def fetch_requests():
    try:
        import requests
        s = requests.Session()
        s.headers["User-Agent"] = "python-requests/2.31"
        return s.get(TARGET, timeout=20).json()
    except Exception as e:
        return {"_error": str(e)}

def fetch_curlcffi(impersonate):
    try:
        from curl_cffi import requests as creq
        r = creq.get(TARGET, impersonate=impersonate, timeout=20)
        return r.json()
    except Exception as e:
        return {"_error": str(e)}

def summarize(label, j):
    if "_error" in j:
        print(f"[{label}] 调用失败: {j['_error']}")
        return
    tls = j.get("tls", {})
    ja3 = tls.get("ja3", "?")
    ja3_hash = tls.get("ja3_hash", "?")
    ja4 = tls.get("ja4", "?")
    http2 = j.get("http2", {})
    akamai = http2.get("akamai_fingerprint_hash", "?")
    ua = j.get("user_agent", "?")
    print(f"[{label}]")
    print(f"  JA3       = {ja3[:60]}...")
    print(f"  JA3 hash  = {ja3_hash}")
    print(f"  JA4       = {ja4}")
    print(f"  HTTP2指纹 = {akamai}")
    print(f"  User-Agent= {ua}")

if __name__ == "__main__":
    print("=" * 70)
    print("Lab1: TLS / HTTP2 指纹对比  (目标 tls.peet.ws, 中立端点)")
    print("=" * 70)
    print()
    print(">>> 客户端1: urllib (Python 原生)")
    try:
        summarize("urllib", fetch_urllib())
    except Exception as e:
        print("  失败:", e)
    print()
    print(">>> 客户端2: requests (默认)")
    summarize("requests", fetch_requests())
    print()
    print(">>> 客户端3: curl_cffi impersonate=chrome120")
    summarize("curl_cffi(chrome120)", fetch_curlcffi("chrome120"))
    print()
    print("=" * 70)
    print("结论: urllib/requests 的 JA3 hash 是固定的、广为人知的脚本指纹;")
    print("      curl_cffi(chrome120) 的 JA3 hash 与真实 Chrome 一致 —— 这就是'指纹还原'。")
    print("=" * 70)
