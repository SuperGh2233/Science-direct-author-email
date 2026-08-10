# -*- coding: utf-8 -*-
"""
指纹实验室 Lab4 —— Turnstile 解码 (打码层)
目的: 学会"解 Turnstile"到底在解什么。它不是 OCR 图形验证码 —— 它是一个
      看不见的 bot-score 挑战: Cloudflare 的 challenge JS 在浏览器里跑, 收集环境信号,
      环境过关就自动产出一个 token (cf-turnstile-response)。所谓"打码"= 让 JS 在一个
      "过关"的环境里跑完, 拿到那个 token, 再拿去 siteverify 验。

两条路, 别混 (这是本 lab 最关键的概念):
  - Widget token (cf-turnstile-response): 表单里嵌的 site key。解 = 拿到 token,
    站主用 secret 去 siteverify 验。打码服务卖的就是这个 token。
  - Edge clearance (cf_clearance): CF 边缘整站挑战页。解 = 用真浏览器跑完整套
    interstitial JS (PoW + 指纹), 拿到放行 cookie, 绑 IP+UA。这跟 widget token 是
    两个东西, 打码服务对它有单独的"Cloudflare Challenge"产品。

合规: demo 模式只用 Cloudflare 官方测试 sitekey (always-pass, 只打 localhost);
      真站模式只打你自己的站 (TARGET_URL)。绝不打第三方站。
环境变量:
  TARGET_URL=https://你的站/   真站模式 (只打你自己的站)
  HEADLESS=0                   弹真窗口跑 (区分 headless 检测)
  TURNSTILE_SECRET=0x...       你自己站的 secret, 用来 siteverify 真站 token (你才有)
  CAPSOLVER_API_KEY=...       给了就真调 capsolver 打你的站 (打码服务实跑)
"""
import argparse, os, sys, json, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- Cloudflare 官方测试 key (文档: /turnstile/troubleshooting/testing/) ---
TEST_SITEKEY_PASS    = "1x00000000000000000000AA"   # 可见, 永远过 (任何域名/localhost 都行)
TEST_SITEKEY_FAIL     = "2x00000000000000000000AB"   # 可见, 永远不过
TEST_SECRET_PASS      = "1x0000000000000000000000000000000AA"  # siteverify 永远 success:true
TEST_SECRET_FAIL      = "2x0000000000000000000000000000000AA"  # 永远 success:false
DUMMY_TOKEN           = "XXXX.DUMMY.TOKEN.XXXX"     # 所有测试 sitekey 都吐这个假 token

SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
API_JS     = "https://challenges.cloudflare.com/turnstile/v0/api.js"
DEMO_PORT  = 8766
DEMO_URL   = f"http://127.0.0.1:{DEMO_PORT}/"

WAF_HEADERS = ["server", "cf-ray", "cf-mitigated", "set-cookie", "via"]


# ───────────────────── 概念铺垫 (先看懂再动手) ─────────────────────

def print_concept():
    print("=" * 70)
    print("Lab4: Turnstile 解码 (打码层)  —  先搞清楚“解”在解什么")
    print("=" * 70)
    print("""
Turnstile 不是图形验证码 —— 没有歪字要 OCR, 没有红绿灯要点。它是一个看不见的
bot-score 挑战: Cloudflare 的 challenge JS 在你浏览器里跑, 收集环境信号(TLS/指纹/
行为/IP 信誉), 环境过关就自动吐一个 token (cf-turnstile-response)。
  => "解 Turnstile" = "拿到一个有效 token", 不是"认图"。
  => 打码服务(2captcha/capsolver)卖的就是这个 token —— 它们在自己的反检测浏览器+
     住宅代理农场里跑完 JS, 把 token 给你。token 只能靠"环境过关"产, 没法 OCR。

两条路 (千万别混):
┌──────────────┬ Widget token (cf-turnstile-response) ─┬ Edge clearance (cf_clearance) ────────┐
│ 哪来的       │ 表单里嵌的 site key                    │ CF 边缘整站挑战页 (cf-mitigated)      │
│ 拿到什么     │ 一个 token 字符串                      │ 一个放行 cookie                       │
│ 谁验         │ 你的后端, 用 secret 去 siteverify      │ CF 边缘自己                           │
│ Lab3 信号    │ —                                      │ cf-mitigated: challenge               │
│ 打码服务卖啥 │ 就是这个 token                         │ 单独的 "Cloudflare Challenge" 产品     │
│ 难度         │ 较易 (页面里一个 JS)                   │ 较难 (跑全套 interstitial, 绑 IP+UA) │
└──────────────┴────────────────────────────────────────┴───────────────────────────────────────┘

解的梯度 (从裸到工业):
  L0  curl_cffi 无 JS        → 跑不了 challenge JS, 没 token (Lab3 已证)
  L1  真浏览器 headless 裸   → 跑了 JS 但被标 bot → 挑战升级或不吐 token
  L1.5 headed + 反检测(patchright/camoufox) → bot-score 低, 隐形挑战可能自动吐
  L2  + 住宅代理(干净 IP)    → bot-score 够低, 隐形挑战常自动过 → token 吐出
  L3  打码服务(capsolver等)  → 他们在 L1.5-L2 规模化跑, 把 token 给你
  L4  人点                   → Turnstile 给高危会话留的兜底

本 lab 用 Cloudflare 官方测试 key 跑 demo (always-pass):
  - 测试 key 跳过了真挑战 → 任何浏览器(连裸 headless)都能拿到 token。
  - 所以 demo 全过 ≠ 你能过真站。demo 只证明"采集 token + siteverify"这条管线
    通了; 真站要换真 sitekey, 此时裸 playwright 会被 CF 标 bot (官方文档明说)。
""")


# ───────────────────── Widget HTML + 本地小服务 (demo 用) ─────────────────────

def widget_html(sitekey):
    # 套 <form> 让 Turnstile 自动建 <input name="cf-turnstile-response">
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<script src="{API_JS}" async defer></script></head>'
        f'<body><form><div class="cf-turnstile" data-sitekey="{sitekey}"></div></form></body></html>'
    )

def start_demo_server(sitekey):
    """起一个本地 http 服务, 把 widget HTML 挂在 127.0.0.1 (测试 key 在 localhost 通用)."""
    html = widget_html(sitekey).encode()
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers(); self.wfile.write(html)
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", DEMO_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ───────────────────── 采集 token (客户端无关的公共轮询) ─────────────────────

def harvest(page, url, label, tries=40):
    """page 已建好: goto + 轮询 [name=cf-turnstile-response] 直到 token 出现。
    token 只在 challenge JS 跑完后才填上, 所以必须轮询, 不能读一次。"""
    try:
        page.goto(url, timeout=20000)
    except Exception as e:
        print(f"  [{label}] goto 失败: {e}")
        return ""
    for _ in range(tries):
        v = ""
        try:  # 法1: 隐式渲染在父 form 里建 hidden input
            v = page.input_value('[name=cf-turnstile-response]') or ""
        except Exception:
            v = ""
        if not v:  # 法2: 显式渲染/无 form 时用 JS API
            try:
                v = page.evaluate(
                    "()=>{try{return window.turnstile?turnstile.getResponse():''}catch(e){return ''}}"
                ) or ""
            except Exception:
                v = ""
        if v:
            return v
        try:  # 没出来就点一下 widget (Managed 高危时会出复选框)
            page.click("//div[@class='cf-turnstile']", timeout=1000)
        except Exception:
            pass
        page.wait_for_timeout(400)
    return ""


# ───────────────────── siteverify (服务端验 token) ─────────────────────

def siteverify(token, secret, remoteip=None):
    """POST 到 CF siteverify。先用 curl_cffi 套真 Chrome TLS (CF 可能 TLS 指纹调用方),
    不可用就回退普通 requests (siteverify 是公开 API, 通常不挑 TLS)。"""
    form = {"secret": secret, "response": token}
    if remoteip: form["remoteip"] = remoteip
    try:
        from curl_cffi import requests as creq
        r = creq.post(SITEVERIFY, data=form, impersonate="chrome120", timeout=15)
        return r.json(), "curl_cffi(chrome120)"
    except Exception as e1:
        try:
            import requests
            r = requests.post(SITEVERIFY, data=form, timeout=15)
            return r.json(), f"requests(回退, curl_cffi 不可用: {e1.splitlines()[0] if str(e1) else e1})"
        except Exception as e2:
            return {"_error": f"curl_cffi:{e1} | requests:{e2}"}, "none"

def show_verify(j, client_tag):
    print(f"  [siteverify via {client_tag}]")
    if "_error" in j:
        print(f"    调用失败: {j['_error']}")
        return
    # 注意: JSON key 是连字符, 不是合法 Python 属性 → 用 [] 取
    print(f"    success      = {j.get('success')}")
    print(f"    challenge_ts = {j.get('challenge_ts')}")
    print(f"    hostname     = {j.get('hostname')}")
    print(f"    action       = {j.get('action')}")
    ec = j.get("error-codes", [])
    if ec:
        print(f"    error-codes  = {ec}")


# ───────────────────── 三个浏览器客户端 (demo 全过; 真站见分晓) ─────────────────────

def run_client_playwright(url, headless):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless)
        ctx = b.new_context(); page = ctx.new_page()
        tok = harvest(page, url, "vanilla")
        ctx.close(); b.close()
    return tok

def run_client_patchright(url, headless):
    from patchright.sync_api import sync_playwright as ppw
    with ppw() as p:
        b = p.chromium.launch(headless=headless)  # patchright 自带反检测补丁; 跟 lab3 一致用 bundled chromium
        ctx = b.new_context(); page = ctx.new_page()
        tok = harvest(page, url, "patchright")
        ctx.close(); b.close()
    return tok

def run_client_camoufox(url, headless):
    from camoufox.sync_api import Camoufox
    with Camoufox(headless=headless) as browser:
        page = browser.new_page()
        tok = harvest(page, url, "camoufox")
    return tok


# ───────────────────── 打码服务 (capsolver, 可选 env) ─────────────────────

def capsolver_solve(api_key, page_url, sitekey, action=None, cdata=None, timeout=120):
    """真调 capsolver 解 Turnstile widget token。返回 (token, useragent, 备注)。"""
    import requests
    task = {"type": "AntiTurnstileTaskProxyLess",   # Turnstile 只有 ProxyLess, 无 proxy 参数
            "websiteURL": page_url, "websiteKey": sitekey}
    if action or cdata:
        task["metadata"] = {}
        if action: task["metadata"]["action"] = action
        if cdata: task["metadata"]["cdata"] = cdata
    r = requests.post("https://api.capsolver.com/createTask",
                      json={"clientKey": api_key, "task": task}, timeout=30).json()
    if r.get("errorId", 1):
        return None, None, f"createTask 失败: {r}"
    tid = r["taskId"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        g = requests.post("https://api.capsolver.com/getTaskResult",
                          json={"clientKey": api_key, "taskId": tid}, timeout=30).json()
        if g.get("status") == "ready":
            return g["solution"]["token"], g["solution"].get("userAgent"), "ready"
        if g.get("errorId", 0) or g.get("status") == "failed":
            return None, None, f"getTaskResult 失败: {g}"
        time.sleep(2)
    return None, None, "超时"


def capsolver_challenge(api_key, proxy_url, page_url, timeout=180):
    """真调 capsolver 解 CF 边缘挑战 (AntiCloudflareTask → cf_clearance)。
    proxy 必填 (cf_clearance 绑 IP: 解和复用都得从同一 IP 出)。
    proxy_url 格式: http://user:pass@ip:port 或 socks5://user:pass@ip:port
    返回 (cf_clearance, useragent, 备注)。"""
    import requests
    from urllib.parse import urlparse
    # 转成 capsolver 的单串格式 ip:port:user:pass
    p = urlparse(proxy_url)
    if p.hostname:
        u, pw = p.username or "", p.password or ""
        port = p.port or (1080 if (p.scheme or "").startswith("socks") else 80)
        cap_proxy = f"{p.hostname}:{port}:{u}:{pw}" if u else f"{p.hostname}:{port}"
    else:
        cap_proxy = proxy_url  # 已是非 URL 格式, 原样给
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    task = {"type": "AntiCloudflareTask", "websiteURL": page_url,
            "userAgent": ua, "proxy": cap_proxy}
    r = requests.post("https://api.capsolver.com/createTask",
                      json={"clientKey": api_key, "task": task}, timeout=30).json()
    if r.get("errorId", 1):
        return None, None, f"createTask 失败: {r}"
    tid = r["taskId"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        g = requests.post("https://api.capsolver.com/getTaskResult",
                          json={"clientKey": api_key, "taskId": tid}, timeout=30).json()
        if g.get("status") == "ready":
            sol = g.get("solution", {})
            cf = (sol.get("cookies") or {}).get("cf_clearance") or sol.get("token")
            return cf, sol.get("userAgent") or ua, "ready"
        if g.get("errorId", 0) or g.get("status") == "failed":
            return None, None, f"getTaskResult 失败: {g}"
        time.sleep(3)
    return None, None, "超时"


def rewarm_cf_clearance(target, wait=90):
    """L4 回暖: 弹 headed patchright, 人点 checkbox, 拿 cf_clearance 返回 (拿不到返 None)。"""
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        ctx = b.new_context(); page = ctx.new_page()
        try:
            page.goto(target, timeout=25000)
            t0 = time.time()
            while time.time() - t0 < wait:
                for c in ctx.cookies():
                    if c["name"] == "cf_clearance":
                        return c["value"]
                page.wait_for_timeout(1000)
        finally:
            ctx.close(); b.close()
    return None


# ───────────────────── 真站模式: 探测 widget 还是 edge ─────────────────────

def discover(target, headless):
    """用 patchright 加载 target, 报 status / WAF 头 / 有无 widget + sitekey。"""
    out = {"status": None, "cf_mitigated": None, "server": None,
           "widget": False, "sitekey": None, "body_snip": "", "error": None}
    try:
        from patchright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=headless)
            ctx = b.new_context(); page = ctx.new_page()
            try:
                resp = page.goto(target, timeout=25000)
                out["status"] = resp.status if resp else None
                low = {k.lower(): v for k, v in (resp.headers.items() if resp else [])}
                out["cf_mitigated"] = low.get("cf-mitigated")
                out["server"] = low.get("server")
                for h in WAF_HEADERS:
                    if h in low and h not in ("server", "cf-mitigated"):
                        v = str(low[h])
                        if h == "set-cookie" and len(v) > 80: v = v[:80] + "..."
                        out[h] = v
                el = page.query_selector("[data-sitekey]")
                if el:
                    out["widget"] = True
                    out["sitekey"] = el.get_attribute("data-sitekey")
                try:
                    out["body_snip"] = (page.evaluate("()=>document.body?document.body.innerText:''") or "")[:160]
                except Exception:
                    pass
            except Exception as e:
                out["error"] = str(e)
            ctx.close(); b.close()
    except Exception as e:
        out["error"] = f"patchright 不可用: {e}"
    return out


# ───────────────────── 主流程 ─────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Turnstile 解码 lab (打码层). 给 URL 打你自己的站; 不给跑 demo (官方测试 key, 只打 localhost).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
                "  lab4                              demo (官方测试 key, 只打 localhost)\n"
                "  lab4 https://你的站/              打你的站 (只打你自己的站!)\n"
                "  lab4 --headed https://你的站/     弹真窗口 (区分 headless 检测 vs 指纹层)\n"
                "  可选 env (你站才有, 别人站别打):  TURNSTILE_SECRET=你的secret   CAPSOLVER_API_KEY=..",
    )
    ap.add_argument("target", nargs="?", help="要测的站 URL (只打你自己的站). 不给则跑 demo")
    ap.add_argument("--headed", action="store_true", help="弹真窗口跑 (默认 headless). 区分 headless 检测 vs 指纹层")
    args = ap.parse_args()
    # argv 优先, 没给就回退 TARGET_URL env (lab.py 走子进程时设的就是这个 env)
    target = args.target or os.getenv("TARGET_URL")
    # --headed 优先; 否则看 HEADLESS env (lab.py --headed 时设 HEADLESS=0)
    headless = (not args.headed) and (os.getenv("HEADLESS", "1") == "1")
    secret = os.getenv("TURNSTILE_SECRET")
    capsolver_key = os.getenv("CAPSOLVER_API_KEY")

    print_concept()

    if not target:
        # ====== demo 模式: 官方测试 key, 只打 localhost, 全合规 ======
        print("=" * 70)
        print(f"DEMO 模式: 官方测试 sitekey {TEST_SITEKEY_PASS} (always-pass), 只打 {DEMO_URL}")
        print(f"  浏览器: {'headless' if headless else 'headed'}")
        print("=" * 70)
        srv = start_demo_server(TEST_SITEKEY_PASS)
        time.sleep(0.4)

        # 三个客户端都采一遍 — 测试 key 跳过真挑战, 所以连裸 headless 都该拿到 token
        clients = [
            ("vanilla playwright", run_client_playwright),
            ("patchright",         run_client_patchright),
            ("camoufox",          run_client_camoufox),
        ]
        got = {}
        for name, fn in clients:
            print(f"\n>>> 客户端: {name}  ({'headless' if headless else 'headed'})")
            try:
                tok = fn(DEMO_URL, headless)
                tag = "测试 token ✅" if tok else "没拿到 token ✗"
                print(f"  [{name}] {tag}")
                if tok:
                    print(f"    token = {tok}")
                    got[name] = tok
            except Exception as e:
                print(f"  [{name}] ERR: {e}")

        # 验证矩阵: 用 PASS secret 和 FAIL secret 各验一次, 看 siteverify 的 success 翻转
        print("\n>>> siteverify 验证矩阵 (同一个 token × 两个 secret)")
        tok = got.get("vanilla playwright") or got.get("patchright") or got.get("camoufox") or DUMMY_TOKEN
        for label, sec in [("TEST_SECRET_PASS (该过)", TEST_SECRET_PASS),
                           ("TEST_SECRET_FAIL (该不过)", TEST_SECRET_FAIL)]:
            print(f"\n  --- {label} ---")
            j, tag = siteverify(tok, sec)
            show_verify(j, tag)

        srv.shutdown()
        print("\n" + "=" * 70)
        print("学到什么:")
        print("  1. 三个浏览器(含裸 headless)全拿到 token —— 因为测试 key 跳过了真挑战。")
        print("     这只证明“采集+siteverify”管线通了, 不证明你能过真站。")
        print("  2. siteverify 的 success 完全由 secret 决定: 同一 token, PASS secret→true,")
        print("     FAIL secret→false。验证是站主服务端的事, 不是浏览器侧。")
        print("  3. 真站要换真 sitekey: 此时裸 playwright 会被 CF 标 bot (官方文档明说),")
        print("     得上 patchright/camoufox + 干净 IP (L1.5-L2), 仍不行就上打码服务 (L3)。")
        print("  4. 若你站是 edge 挑战(cf-mitigated) 而非表单 widget —— 那是 cf_clearance 流,")
        print("     打 widget token 帮不了你; 得跑 interstitial 拿放行 cookie (见下方真站模式)。")
        print("=" * 70)
        print("\n真站模式:  python lab4_turnstile.py  设 TARGET_URL=https://你的站/")
        print("           (你站有 widget 就采 token; 是 edge 挑战就试拿 cf_clearance;")
        print("            给 CAPSOLVER_API_KEY 就真调打码服务打你的站)")
        return

    # ====== 真站模式: 只打你自己的站 ======
    print("=" * 70)
    print(f"真站模式: 打 {target}  (只打你自己的站, {'headless' if headless else 'headed'})")
    print("=" * 70)

    print("\n>>> 第0步: 探测 (patchright 加载, 看 status/WAF 头/有无 widget)")
    d = discover(target, headless)
    print(f"  status        = {d['status']}")
    print(f"  server        = {d['server']}")
    print(f"  cf-mitigated  = {d['cf_mitigated']}")
    for h in WAF_HEADERS:
        if h in d and h not in ("server", "cf-mitigated"):
            print(f"  {h:13} = {d[h]}")
    if d.get("error"):
        print(f"  探测错误: {d['error']}")
    if d["widget"]:
        print(f"  ✅ 页面上有 Turnstile widget, sitekey = {d['sitekey']}")
    else:
        print(f"  ✗ 页面上没找到 Turnstile widget (data-sitekey)")
        print(f"     body 预览: {d['body_snip']!r}")
    if d["status"] in (403, 503) or d["cf_mitigated"]:
        print("  ⚠ 该请求被 CF 边缘挑战/挡 (cf-mitigated 或 403/503)。")
        print("    这是 EDGE 流 (cf_clearance), 不是 widget 流。下面先试 widget (若有), 再试 edge。")

    # 1) widget 流: 有 widget 就采 token
    if d["widget"]:
        print(f"\n>>> 第1步: widget 流 — 采 token (sitekey={d['sitekey']})")
        for name, fn in [("patchright", run_client_patchright), ("camoufox", run_client_camoufox)]:
            print(f"\n  --- {name} ---")
            try:
                tok = fn(target, headless)
                if tok:
                    print(f"  [{name}] 拿到 token: {tok[:60]}{'...' if len(tok)>60 else ''}")
                    if secret:
                        j, tag = siteverify(tok, secret)
                        show_verify(j, tag)
                    else:
                        print("  (没设 TURNSTILE_SECRET, 跳过 siteverify —— 你自己站的 secret 你才有)")
                else:
                    print(f"  [{name}] 没拿到 token —— 真挑战把自动化环境拦了 (这正是真站 vs 测试 key 的差别)")
            except Exception as e:
                print(f"  [{name}] ERR: {e}")

    # 2) edge 流: 看有没有拿到 cf_clearance (绑 IP+UA, 要真浏览器跑完 interstitial)
    print("\n>>> 第2步: edge 流 — 看能不能拿到 cf_clearance (绑 IP+UA)")
    cks, ua, has_clear, r2status, reuse_ok = {}, None, False, None, False
    try:
        from patchright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=headless)
            ctx = b.new_context(); page = ctx.new_page()
            resp = page.goto(target, timeout=25000)
            r2status = resp.status if resp else None
            challenged = resp and (resp.status in (403, 503))
            if challenged:
                print(f"  边缘挑战页 (HTTP {resp.status})")
                if headless:
                    # headless 下交互式挑战要人点, 自动过不了 —— 这正是 L4 兜底
                    print("  headless: 若是交互式挑战(要人点), 自动过不了。")
                    print("           想看自动能不能过: 换干净住宅 IP (L2) 把 bot-score 压到隐形挑战自动过。")
                    print("           想真拿到 cf_clearance: 用 --headed 弹真窗口, 你手点 checkbox。")
                    wait = 8   # 短轮询, 看隐形挑战有没有自动过
                else:
                    print("  headed: 弹窗里若出 checkbox/挑战, 你手动点一下 → 我等 cf_clearance 落下来 (≤90s)")
                    wait = 90  # 给人点的时间
                # 轮询 cf_clearance: headed 给人点的时间; headless 看隐形自动过没
                t0 = time.time(); got = False
                while time.time() - t0 < wait:
                    if any(c["name"] == "cf_clearance" for c in ctx.cookies()):
                        got = True; break
                    page.wait_for_timeout(1000)
                if got:
                    print(f"  cf_clearance 落下来了 (等了 {int(time.time()-t0)}s)")
                resp2 = page.goto(target, timeout=25000)  # 再请求一次看是否放行
                r2status = resp2.status if resp2 else None
                print(f"  二次请求 status = {r2status}")
            cks = {c["name"]: c["value"] for c in ctx.cookies() if c["name"] in ("cf_clearance", "__cf_bm")}
            try: ua = page.evaluate("()=>navigator.userAgent")
            except Exception: ua = None
            has_clear = "cf_clearance" in cks
            print(f"  cookie 里的 CF 签名: {list(cks.keys()) or '(无)'}  (__cf_bm=到达挑战页的灯塔; cf_clearance=过挑战才发)")
            if (not challenged) and r2status == 200:
                print("  ✅ 没被挑战, 直接 200 放行 —— IP 已暖 (L2 达成): CF 连挑战都没发, 不需要 cf_clearance")
                print("     (之前手点过几次, CF 把这 IP 的 bot-score 记低了; 暖 IP 是最省的过墙法)")
            elif has_clear and r2status == 200:
                print("  ✅ cf_clearance + 二次请求 200 —— 真过墙了 (IP+UA 组合被认可, 手点成功)")
                print("     注意: cf_clearance 绑 IP+UA, 换 IP/改 UA 即失效, 搬不走")
            elif has_clear:
                print(f"  ⚠ cf_clearance 出现了但二次请求仍 {r2status} —— cookie 在但没被认可, 没真正过墙")
                print("     可能: clearance 没真过挑战 / 站点有额外 WAF 规则 / IP+UA 不匹配")
                print("     往下: --headed 手点(L4); 或换更干净住宅 IP(L2); 或打码 Cloudflare Challenge(L3)")
            else:
                print(f"  ✗ 没过墙 (status={r2status}, 无 cf_clearance) —— bot-score 还不够低, 或交互挑战没点")
                print("     往下: ① --headed 你手点 (L4); ② 换更干净住宅 IP (L2); ③ 打码服务的 Cloudflare Challenge 产品 (L3)")
            ctx.close(); b.close()
    except Exception as e:
        print(f"  patchright 不可用或失败: {e}")

    # 2.5) 浏览器拿到 200 的话, 用 curl_cffi 无状态复用 (同 IP+UA + 全 Chrome header + chrome146 TLS) —— 看能不能真拿正文
    #   有 cf_clearance 就带上(过墙后复用); 没 cf_clearance 就是暖 IP 直连测试(无状态客户端是否也被放行)
    if r2status == 200:
        label = "把 cf_clearance 搬到 curl_cffi 复用" if has_clear else "curl_cffi 直接打 (无 cf_clearance, 测暖 IP 是否连无状态客户端都放行)"
        print(f"\n>>> 第2.5步: {label} (同 IP+UA + 全 Chrome header + chrome146 TLS)")
        try:
            from curl_cffi import requests as creq
            hdr = {
                "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                "sec-ch-ua": '"Google Chrome";v="149", "Not?A_Brand";v="24"',
                "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "sec-fetch-dest": "document", "sec-fetch-mode": "navigate",
                "sec-fetch-site": "none", "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
            }
            print(f"  复用 UA    : {ua}")
            print(f"  复用 cookie: {list(cks.keys()) or '(无)'}")
            print(f"  TLS 指纹   : curl_cffi impersonate=chrome146 (浏览器真 Chrome/149, 无 149 档, 用最近的 146)")
            r = creq.get(target, headers=hdr, cookies=cks, impersonate="chrome146", timeout=20, allow_redirects=True)
            is_chal = ("are you a robot" in r.text.lower()) or (r.status_code in (403, 503))
            snip = r.text[:160].replace("\n", " ")
            reuse_ok = (r.status_code == 200 and not is_chal)
            if reuse_ok:
                print(f"  ✅ curl_cffi HTTP 200 —— 可无状态复用! ({'cf_clearance 被认可' if has_clear else '暖 IP 连无状态客户端都放行'})")
                print(f"     正文预览: {snip!r}")
            else:
                print(f"  ⚠ curl_cffi HTTP {r.status_code} —— 浏览器能过但 curl_cffi 被重新挑战")
                print(f"     => CF 对 curl_cffi 的 TLS/JA3+HTTP2 指纹跟浏览器 (真 Chrome/149) 不一致另眼相看")
                print(f"     => 这就是打码服务要么'整套会话替你跑'、要么 cf_clearance 绑指定 IP+UA(+TLS 档)给你的原因")
                print(f"     正文预览: {snip!r}")

            # 第2.6 批量抓复测: 连发 N 个, 验批量抓摊得开
            if reuse_ok:
                N = 5
                print(f"\n>>> 第2.6步: 批量抓复测 (连发 {N} 次, 验批量抓摊得开)")
                ok = 0
                for i in range(N):
                    t0 = time.time()
                    rr = creq.get(target, headers=hdr, cookies=cks, impersonate="chrome146", timeout=20, allow_redirects=True)
                    ms = int((time.time() - t0) * 1000)
                    chal = ("are you a robot" in rr.text.lower()) or (rr.status_code in (403, 503))
                    good = rr.status_code == 200 and not chal
                    if good: ok += 1
                    print(f"  [{i+1}/{N}] {'✅' if good else '⚠'+str(rr.status_code)}  {len(rr.content)}B  {ms}ms")
                print(f"  小结: {ok}/{N} 个 200 —— {'稳定可复用, 真能批量抓' if ok==N else '有抖(非全 200), CF 概率性, 批量要带重试'}")
        except Exception as e:
            print(f"  curl_cffi 复用失败: {e}")

    # 2.7) 批量抓多链接 (curl_cffi Session + 抖动 + 403重试 + L4 回暖兜底)
    #   看暖 IP 在第几十个请求被刷冷, 然后 L4(headed 点) 回暖续命 —— 量产爬取的工程化
    if reuse_ok:
        import random
        from urllib.parse import urlparse, urljoin
        # 1) 取 URL 列表: 文件 > URL_LIST env > 自动抽同源链接 > 目标URL循环
        f = os.getenv("URL_LIST_FILE")
        if f and os.path.exists(f):
            urls = [ln.strip() for ln in open(f, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
            src = f"文件 {f}"
        elif os.getenv("URL_LIST"):
            urls = [u.strip() for u in os.getenv("URL_LIST").split(",") if u.strip()]
            src = "URL_LIST env"
        else:
            from curl_cffi import requests as creq
            try:
                html = creq.get(target, headers=hdr, cookies=cks, impersonate="chrome146", timeout=20).text
                import re
                host = urlparse(target).hostname
                same = set()
                for m in re.findall(r'href="([^"]+)"', html):
                    u = urljoin(target, m).split("#")[0]
                    if urlparse(u).hostname == host:
                        same.add(u)
                urls = sorted(same)
            except Exception:
                urls = []
            src = f"自动抽同源链接 ({len(urls)} 条)" if len(urls) > 1 else "目标URL循环"
        if not urls:
            urls = [target]; src = "目标URL循环 (没抽到链接)"
        # 2) 排到 MAX_REQ 个 (不够循环)
        max_req = int(os.getenv("MAX_REQ", "25"))
        queue = (urls * ((max_req // len(urls)) + 1))[:max_req]
        jmin = float(os.getenv("JITTER_MIN", "1.5")); jmax = float(os.getenv("JITTER_MAX", "4.0"))
        print(f"\n>>> 第2.7步: 批量抓多链接 ({len(queue)} 请求, 抖动 {jmin}-{jmax}s, L4 回暖兜底)")
        print(f"  URL 源: {src}")
        # 3) curl_cffi Session, 种入浏览器 cookie 起步就暖
        from curl_cffi import requests as creq
        sess = creq.Session()
        sess.headers.update(hdr)
        host = urlparse(target).hostname
        for k, v in cks.items():
            try: sess.cookies.set(k, v, domain=host)
            except Exception: pass
        ok = 0; rewarms = 0; ceiling_at = None
        for i, u in enumerate(queue):
            time.sleep(random.uniform(jmin, jmax))
            try:
                r = sess.get(u, impersonate="chrome146", timeout=25, allow_redirects=True)
            except Exception as e:
                print(f"  [{i+1}/{len(queue)}] ERR {e}"); continue
            chal = ("are you a robot" in r.text.lower()) or (r.status_code in (403, 503))
            if chal:
                if ceiling_at is None: ceiling_at = i + 1
                print(f"  [{i+1}/{len(queue)}] ⚠ {r.status_code} 撞天花板! 暖 IP 在第 {i+1} 个请求被刷冷")
                if not headless:
                    print(f"  → L4 回暖: 弹窗请你点 checkbox (≤90s)")
                    try:
                        cf = rewarm_cf_clearance(target, wait=90)
                    except Exception as e:
                        cf = None; print(f"  → 回暖异常: {e}")
                    if cf:
                        try: sess.cookies.set("cf_clearance", cf, domain=host)
                        except Exception: pass
                        rewarms += 1
                        print(f"  → 回暖成功, cf_clearance 注回 session, 续抓")
                    else:
                        print(f"  → 回暖失败 — L4 也救不回来, 得上 L3 或换 IP; 止步")
                        break
                else:
                    print(f"  headless 没法自动回暖(要人点) — L4 要人 in-the-loop; 止步。想看回暖用 --headed")
                    break
            else:
                ok += 1
                print(f"  [{i+1}/{len(queue)}] ✅ {r.status_code} {len(r.content)}B  {u[:60]}")
        tail = f", 第 {ceiling_at} 个撞天花板, L4 回暖 {rewarms} 次续命" if ceiling_at else f", 没撞天花板 (暖度撑住 {len(queue)} 个; 想压测: MAX_REQ 调大 + JITTER 调小)"
        print(f"  小结: {ok}/{len(queue)} 个 200{tail}")

    # 3) 打码服务 (给了 key 就真调, 只打你的站)
    cap_proxy = os.getenv("CAPSOLVER_PROXY")  # http://user:pass@ip:port ; Challenge task 必填(绑 IP)
    if capsolver_key:
        print("\n>>> 第3步: 打码服务 capsolver (真调, 打你的站)")
        if d["widget"] and d["sitekey"]:
            # widget 流 (表单 token): AntiTurnstileTaskProxyLess, 无 proxy
            tok, ua2, note = capsolver_solve(capsolver_key, target, d["sitekey"])
            if tok:
                print(f"  capsolver 返回 token: {tok[:60]}{'...' if len(tok)>60 else ''}")
                print(f"  capsolver 用的 UA   : {ua2}")
                print("  注意: 这个 token 是 widget token (cf-turnstile-response), 不是 cf_clearance。")
                print("        往表单 cf-turnstile-response 字段提交即过 widget; 但过不了 edge 整站墙。")
                if secret:
                    j, tag = siteverify(tok, secret)
                    show_verify(j, tag)
            else:
                print(f"  capsolver 失败: {note}")
        else:
            # edge 流: AntiCloudflareTask → cf_clearance (要 proxy, 绑 IP)
            print("  你站是 edge 墙 → 用 capsolver 的 Cloudflare Challenge 任务 (AntiCloudflareTask, 返回 cf_clearance)")
            if not cap_proxy:
                print("  ✗ 这个 task 必须给 proxy —— cf_clearance 绑 IP, 必须从同一 IP 解 + 复用")
                print("    设 CAPSOLVER_PROXY=http://user:pass@ip:port (静态/粘性, 会话 ≥3min) 再跑")
                print("    请求长这样: POST createTask {type:AntiCloudflareTask, websiteURL, proxy, userAgent}")
            else:
                print(f"  用的 proxy: {cap_proxy}")
                cf, ua2, note = capsolver_challenge(capsolver_key, cap_proxy, target)
                if cf:
                    print(f"  capsolver 返回 cf_clearance: {cf[:50]}{'...' if len(cf)>50 else ''}")
                    print(f"  capsolver 返回 UA        : {ua2}")
                    # 复用: curl_cffi 走同一 proxy, 带 cf_clearance + capsolver 给的 UA (TLS 库, 普通 requests 会被拒)
                    try:
                        from curl_cffi import requests as creq
                        proxies = {"http": cap_proxy, "https": cap_proxy}
                        r = creq.get(target, headers={"User-Agent": ua2},
                                     cookies={"cf_clearance": cf},
                                     impersonate="chrome146", proxies=proxies,
                                     timeout=25, allow_redirects=True)
                        chal = ("are you a robot" in r.text.lower()) or (r.status_code in (403, 503))
                        snip = r.text[:160].replace("\n", " ")
                        if r.status_code == 200 and not chal:
                            print(f"  ✅ curl_cffi(走同 proxy) HTTP 200 —— 打码出的 cf_clearance 在同 IP 复用成功!")
                            print(f"     正文预览: {snip!r}")
                        else:
                            print(f"  ⚠ curl_cffi HTTP {r.status_code} —— cf_clearance 没在同 IP 复用成功")
                            print(f"     (proxy IP 不够干净 / UA 不匹配 / 站点额外规则)")
                            print(f"     正文预览: {snip!r}")
                    except Exception as e:
                        print(f"  curl_cffi 复用失败: {e}")
                else:
                    print(f"  capsolver 失败: {note}")
    else:
        print("\n>>> 第3步: 打码服务 (没设 CAPSOLVER_API_KEY, 只讲怎么用)")
        print("  你站是 edge 墙, 过它要用 capsolver 的 Cloudflare Challenge 任务 (不是 widget 那个):")
        print('    POST https://api.capsolver.com/createTask')
        print('    {"clientKey": "<key>",')
        print('     "task": {"type": "AntiCloudflareTask",   # 不是 AntiTurnstileTaskProxyLess')
        print('             "websiteURL": "<你的站URL>",')
        print('             "proxy": "<ip:port:user:pass>",   # 必填! cf_clearance 绑 IP')
        print('             "userAgent": "<Chrome UA>"}}')      # 可选, 复用时用同一个')
        print("  → 轮询 getTaskResult 到 status=ready, 取 solution.cookies.cf_clearance + solution.userAgent")
        print("  → 复用: curl_cffi 走同一 proxy, 带 cf_clearance + 那个 UA (TLS 库, 普通 requests 会被拒)")
        print("  给 CAPSOLVER_API_KEY + CAPSOLVER_PROXY 环境变量即真跑 (只打你自己的站)。")

    print("\n" + "=" * 70)
    print("学到什么:")
    print("  - widget 流 (表单 token) 和 edge 流 (cf_clearance) 是两件事, 别混。")
    print("  - edge 流: L4 手点 checkbox → cf_clearance → 二次请求 200 = 真过墙 (你这站已验证)。")
    print("  - cf_clearance 能搬到 curl_cffi 无状态复用 (同 IP+UA + 全 Chrome header + chrome146 TLS) → 200;")
    print("    即 点一次, 之后 curl_cffi 批量抓 (≈30min TTL 内), 不必每请求开浏览器 (你这站已验证)。")
    print("  - headless 比 headed 待遇更差: headless→cf-mitigated:None+硬挡; headed→软挑战(给 checkbox)。")
    print("  - 唯一非自动步: 那一下手点 (L4)。想全自动: L3 打码的 Cloudflare Challenge 产品 (替你跑出 cookie)。")
    print("=" * 70)


if __name__ == "__main__":
    main()
