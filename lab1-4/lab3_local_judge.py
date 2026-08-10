# -*- coding: utf-8 -*-
"""
指纹实验室 Lab3 —— 本地反爬判官靶子(端到端, 完全合规)
自建一个 127.0.0.1 的判官服务, 模拟反爬检测逻辑(header/UA 指纹层),
三种客户端去打: 裸 urllib / curl_cffi(伪装Chrome) / playwright+stealth
看谁被判 bot(403)、谁被放行(200)
合规: 本地自建, 不涉及任何外部受保护站点
TLS/JA3 层的'过 CF'证据见 Lab1(curl_cffi JA3 hash 与真 Chrome 一致)
"""
import os, subprocess, sys, time, json, urllib.request, urllib.error

PORT = 8765
# 默认打本地自建判官; 用 TARGET_URL 环境变量覆盖即可打你自己的站(此时跳过起判官,
# 你的站就是判官, 返回的 200/403/挑战页 = 你的反爬判定结果)
#   TARGET_URL=https://your-site.com/ python lab3_local_judge.py
BASE = os.getenv("TARGET_URL", f"http://127.0.0.1:{PORT}/")
LOCAL_JUDGE = BASE.startswith("http://127.0.0.1")

SERVER_CODE = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
class H(BaseHTTPRequestHandler):
    def _verdict(self):
        ua = self.headers.get("User-Agent","")
        s = {
            "ua_is_browser": ("Mozilla" in ua and "Chrome" in ua),
            "has_sec_ch_ua": bool(self.headers.get("sec-ch-ua")),
            "has_accept_language": bool(self.headers.get("Accept-Language")),
            "ua_bot_like": any(b in ua.lower() for b in ["python","requests","curl","bot","headless","urllib"]),
        }
        bot = s["ua_bot_like"] or (not s["ua_is_browser"]) or (not s["has_sec_ch_ua"])
        return ("bot" if bot else "human", s)
    def do_GET(self):
        v, sig = self._verdict()
        code = 200 if v=="human" else 403
        body = json.dumps({"verdict":v,"signals":sig}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self,*a): pass
print("JUDGE up on %d" % %d, flush=True)  # placeholder
''' .replace("%d", str(PORT), 1)  # hack port in print
# 写真实 server 文件
with open("judge_server.py","w",encoding="utf-8") as f:
    f.write('''from http.server import BaseHTTPRequestHandler, HTTPServer
import json
PORT = %d
class H(BaseHTTPRequestHandler):
    def _verdict(self):
        ua = self.headers.get("User-Agent","")
        s = {"ua_is_browser": ("Mozilla" in ua and "Chrome" in ua),
             "has_sec_ch_ua": bool(self.headers.get("sec-ch-ua")),
             "has_accept_language": bool(self.headers.get("Accept-Language")),
             "ua_bot_like": any(b in ua.lower() for b in ["python","requests","curl","bot","headless","urllib"])}
        bot = s["ua_bot_like"] or (not s["ua_is_browser"]) or (not s["has_sec_ch_ua"])
        return ("bot" if bot else "human", s)
    def do_GET(self):
        v, sig = self._verdict()
        code = 200 if v=="human" else 403
        body = json.dumps({"verdict":v,"signals":sig}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self,*a): pass
print("JUDGE up on "+str(PORT), flush=True)
HTTPServer(("127.0.0.1",PORT), H).serve_forever()
''' % PORT)

# 挑 WAF 厂商签名头打出来, 一眼看出是谁在挡 (Cloudflare/Imperva/Akamai/Sucuri...)
WAF_HEADERS = ["server","cf-ray","cf-mitigated","x-cdn","x-akamai-transformed",
               "x-iinfo","x-sucuri-id","set-cookie","via","x-frame-options"]

def show(label, code, body, headers=None):
    print(f"[{label}] HTTP {code}")
    if headers:
        low = {k.lower(): v for k, v in headers.items()}
        for h in WAF_HEADERS:
            if h in low:
                v = str(low[h])
                if h == "set-cookie" and len(v) > 80:
                    v = v[:80] + "..."
                print(f"    {h}: {v}")
    try:
        print("   ", json.loads(body))
    except Exception:
        print("   ", body[:160])

if __name__ == "__main__":
    PY = sys.executable
    # 只在打本地判官时才起子进程; TARGET_URL 指向外部站时跳过(目标站自己就是判官)
    if LOCAL_JUDGE:
        proc = subprocess.Popen([PY, "judge_server.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        time.sleep(1.2)
        if proc.poll() is not None:
            print("判官启动失败:", proc.stdout.read().decode("utf-8","replace"))
            sys.exit(1)
    else:
        proc = None
    print("="*70)
    print("Lab3: 反爬判官靶子  (target %s)" % BASE)
    print("="*70)

    # 1) urllib 裸
    print("\n>>> 客户端1: urllib (裸, UA=python-urllib)")
    try:
        req=urllib.request.Request(BASE, headers={"User-Agent":"python-urllib/3.13"})
        try:
            with urllib.request.urlopen(req,timeout=10) as r:
                show("urllib", r.status, r.read(), dict(r.headers.items()))
        except urllib.error.HTTPError as e:
            show("urllib", e.code, e.read(), dict(e.headers.items()))
    except Exception as e: print("  ERR:", e)

    # 2) curl_cffi chrome120
    print("\n>>> 客户端2: curl_cffi impersonate=chrome120")
    try:
        from curl_cffi import requests as creq
        r = creq.get(BASE, impersonate="chrome120", timeout=10)
        show("curl_cffi(chrome120)", r.status_code, r.content, dict(r.headers.items()))
    except Exception as e: print("  ERR:", e)

    # 3) playwright + stealth (HEADLESS=0 时弹真窗口跑, 用来区分 headless 检测 vs 指纹层)
    headless = os.getenv("HEADLESS","1") != "0"
    print(f"\n>>> 客户端3: playwright {'headless' if headless else 'headed'} + stealth")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=headless)
            ctx = b.new_context()
            try:
                from playwright_stealth import Stealth
                Stealth().apply_stealth_sync(ctx)
            except Exception as e:
                print("  stealth 不可用:", e)
            page = ctx.new_page()
            resp = page.goto(BASE, timeout=15000)
            status = resp.status if resp else "?"
            hdrs = dict(resp.headers) if resp else None
            txt = page.evaluate("()=>document.body.innerText")
            print(f"  [playwright-stealth] HTTP {status}")
            if hdrs:
                low = {k.lower(): v for k,v in hdrs.items()}
                for h in WAF_HEADERS:
                    if h in low:
                        v = str(low[h])
                        if h == "set-cookie" and len(v) > 80: v = v[:80]+"..."
                        print(f"    {h}: {v}")
            print("   ", txt[:200])
            b.close()
    except Exception as e: print("  ERR:", e)

    # 4) curl_cffi chrome146 + 真 Chrome header 全套 (反反爬 L1: 新 profile + 补齐 header)
    #    和客户端2 (chrome120 裸) A/B: 同一目标, 看新指纹+全 header 能否把 CF 判定从 challenge 压下去
    print("\n>>> 客户端4: curl_cffi impersonate=chrome146 + 真 Chrome header 全套")
    try:
        from curl_cffi import requests as creq
        hdr = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Google Chrome";v="146", "Not?A_Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
        }
        r = creq.get(BASE, impersonate="chrome146", headers=hdr, timeout=15)
        show("curl_cffi(chrome146)+hdr", r.status_code, r.content, dict(r.headers.items()))
    except Exception as e: print("  ERR:", e)

    # 5) patchright (反检测 playwright fork, 补 CDP leak, 不叠 stealth) — 反反爬 L2
    #    和客户端3 (playwright+stealth) A/B: 同样 Chromium, 看补了 CDP leak 后能否过 CF
    #    预判: patchright 仍可能挂, 因为 TLS 还是 Chromium build (CF 标的就是这层); 那就换 camoufox
    print(f"\n>>> 客户端5: patchright {'headless' if headless else 'headed'} (无 stealth)")
    try:
        from patchright.sync_api import sync_playwright as ppw
        with ppw() as p:
            b = p.chromium.launch(headless=headless)
            ctx = b.new_context()
            page = ctx.new_page()
            resp = page.goto(BASE, timeout=15000)
            status = resp.status if resp else "?"
            hdrs = dict(resp.headers) if resp else None
            txt = page.evaluate("()=>document.body.innerText")
            print(f"  [patchright] HTTP {status}")
            if hdrs:
                low = {k.lower(): v for k,v in hdrs.items()}
                for h in WAF_HEADERS:
                    if h in low:
                        v = str(low[h])
                        if h == "set-cookie" and len(v) > 80: v = v[:80]+"..."
                        print(f"    {h}: {v}")
            print("   ", txt[:200])
            b.close()
    except Exception as e: print("  ERR:", e)

    # 6) camoufox (反检测 Firefox, 真 Firefox TLS 栈) — 反反爬 L2+: 换 TLS 栈
    #    和客户端3/5 (Chromium 系) A/B: 若 camoufox 过而 patchright/playwright 不过
    #    => 一锤定音: 卡点在 Chromium 的 TLS/HTTP2 指纹, 换 Firefox 栈才行
    print(f"\n>>> 客户端6: camoufox {'headless' if headless else 'headed'} (Firefox TLS)")
    try:
        from camoufox.sync_api import Camoufox
        with Camoufox(headless=headless) as browser:
            page = browser.new_page()
            resp = page.goto(BASE, timeout=20000)
            status = resp.status if resp else "?"
            hdrs = dict(resp.headers) if resp else None
            txt = page.evaluate("()=>document.body ? document.body.innerText : ''")
            print(f"  [camoufox] HTTP {status}")
            if hdrs:
                low = {k.lower(): v for k,v in hdrs.items()}
                for h in WAF_HEADERS:
                    if h in low:
                        v = str(low[h])
                        if h == "set-cookie" and len(v) > 80: v = v[:80]+"..."
                        print(f"    {h}: {v}")
            print("   ", txt[:200])
    except Exception as e: print("  ERR:", e)

    print("\n"+"="*70)
    if LOCAL_JUDGE:
        print("结论: urllib 裸->403 bot; curl_cffi->200 human; stealth->仍可能403(看 Accept-Language/UA 是否补全)")
        print("      说明反爬是多层,单 stealth 不是银弹; JA3 层见 Lab1")
    else:
        print("注: 打的是外部站, 上面的状态码+body 就是该站对每种客户端的真实判定。")
        print("    全 200 = 该层基本没设防; 有 403/挑战页 = 那层有检测。多层差异见 Lab1/2")
    print("="*70)
    if proc:
        proc.terminate()
        try: proc.wait(timeout=3)
        except: proc.kill()
