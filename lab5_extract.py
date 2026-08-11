# -*- coding: utf-8 -*-
"""
指纹实验室 Lab5 —— 取内容 (层2): 抽通讯作者邮箱
层1 (lab1-4) 让你能拿到 200 + HTML; lab5 解"从 HTML 抠出指定内容"。
焦点: 文章页里"所有通讯作者的邮箱"。三个难点:
  - 数量不定 → list + union + n_authors 计数
  - 判断谁是通讯 → 强信号 (mailto/citation_author_email/通讯段/CF邮箱在通讯上下文) 才算 corr
  - 不能漏 → 多策略并采 + union 去重 + patchright 渲染兜底

策略 (每条都跑, 不短路):
  1. citation_* meta (citation_author_email = 通讯作者邮箱)
  2. JSON-LD (schema.org author.email; author/contributor/creator 节点下 = 通讯上下文; mailto:/%40 规范化)
  2.5 JS char-code (String.fromCharCode / [num,num] 数组) —— drop script 前扫
  3. mailto: 链接 (corr=True; service-desk 邮箱靠 maybe_generic 过滤)
  4. CF 邮箱混淆: data-cfemail 属性 + /cdn-cgi/l/email-protection#hex href 两种 (CF 全站混淆 → 靠 _in_corr_ctx 判)
  5. "Corresponding author/Correspondence to/通讯作者" 段 (锚短语, 紧窗口, 首个邮箱; 不误匹配 correspondence analysis)
  6. at/dot 文本 (confirm-only: 只标已发现的, 避散文误报)
  7. 反转 data-attr (弱信号)
  没强通讯信号 → patchright 渲染兜底 (CF 邮箱混淆/JS 注入 解码后才明文)

合规: 只打你自己的站 (TARGET_URL / --list)。
"""
import os, re, json, argparse
from urllib.parse import unquote

WAF_HEADERS = ["server", "cf-ray", "cf-mitigated", "set-cookie", "via"]
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
# 站点/CDN/客服 邮箱 (非作者) 的常见特征 —— 标"可能非作者", 不删 (不漏)
GENERIC_LOCAL = re.compile(r'^(info|contact|support|admin|webmaster|sales|help|noreply|no-reply|office|editorial|editor|privacy|ads|marketing|press|ir|investor|copyright|permissions|authorhelp|authorservices|production|submission|submit|service|services|subscriptions|customerservice|customercare)', re.I)
CDN_DOMAIN = re.compile(r'(gstatic|googleapis|cloudflare|jsdelivr|unpkg|cdnjs|github|twitter|facebook|linkedin|youtube|w3|schema)\.', re.I)

CHROME_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Google Chrome";v="149", "Not?A_Brand";v="24"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "sec-fetch-dest": "document", "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none", "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


def decode_cfemail(h):
    """Cloudflare 邮箱混淆 (data-cfemail hex 或 href #hex) 解码: 第一字节 key, 后续 XOR key。"""
    r = int(h[:2], 16)
    return "".join(chr(int(h[i:i+2], 16) ^ r) for i in range(2, len(h), 2))


def fetch_html(target):
    """层1: curl_cffi + 全 Chrome header + chrome146 (暖 IP)。返回 (html, status, headers)。"""
    from curl_cffi import requests as creq
    r = creq.get(target, headers=CHROME_HDR, impersonate="chrome146", timeout=25, allow_redirects=True)
    return r.text, r.status_code, dict(r.headers)


def fetch_rendered(target, headless):
    """patchright 渲染兜底: 跑完 JS (含 CF 邮箱解码) 再取 DOM HTML。"""
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless)
        ctx = b.new_context(); page = ctx.new_page()
        page.goto(target, timeout=25000)
        page.wait_for_timeout(3000)
        html = page.content()
        ctx.close(); b.close()
        return html


def _collect_emails(page):
    """收当前所有 frame(主框架 + iframe, 含 sidebar)里的 mailto + 'Correspondence to:' 后邮箱。
    用 content()+lxml (不调 evaluate —— 信封点击触发跳转/弹窗 mid-load 时 evaluate 会死等)。返回 [(email, source)] 去重。"""
    from lxml import html as LH
    out = []
    for fr in [page] + list(page.frames):
        try:
            html_text = fr.content()
        except Exception:
            continue
        if not html_text:
            continue
        try:
            tree = LH.fromstring(html_text)
            for a in tree.xpath('//a[starts-with(@href,"mailto:")]'):
                em = (a.get("href", "").split("mailto:", 1)[-1].split("?")[0]).strip()
                if EMAIL_RE.fullmatch(em):
                    out.append((em, "mailto"))
            body = " ".join(tree.xpath("//text()"))
            m = re.search(r"(Correspondence to|通讯作者|通信作者)", body, re.I)
            if m:
                for em in EMAIL_RE.findall(body[m.end(): m.end() + 200]):
                    out.append((em, "correspondence-to"))
        except Exception:
            pass
    seen, dedup = set(), []
    for em, s in out:
        k = em.lower()
        if k not in seen:
            seen.add(k); dedup.append((em, s))
    return dedup


def _try_auto_click_turnstile(page):
    """自动点 Turnstile 复选框 (在 challenges.cloudflare.com iframe 里)。暖 IP + 可信浏览器可能自动过。
    返回是否点到了。(只在你自己的站学习用; 别人站别用)"""
    try:
        for fr in page.frames:
            if "challenges.cloudflare.com" not in (fr.url or ""):
                continue
            for sel in ('input[type="checkbox"]', '#challenge-stage input', '.ctp-checkbox-label',
                        '#challenge-stage', 'label.cb-lb'):
                try:
                    el = fr.query_selector(sel)
                    if el is not None and el.is_visible():
                        el.click(timeout=3000)
                        print(f"    🖱 自动点了 Turnstile 复选框 ({sel})")
                        return True
                except Exception:
                    continue
            # 兜底: 点 iframe 左侧 (复选框常在左)
            try:
                body = fr.query_selector('body')
                if body is not None:
                    box = body.bounding_box()
                    if box:
                        fr.click('body', position={"x": 30, "y": int(box["height"] // 2)}, timeout=3000)
                        print("    🖱 自动点了 Turnstile 复选框区域 (iframe 左侧)")
                        return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _send_hotkey_ctypes(hotkey_str):
    """用 ctypes keybd_event 发热键 (Windows)。hotkey_str 如 'f9' / 'ctrl+shift+y'。"""
    import ctypes
    VK = {
        'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74, 'f6': 0x75,
        'f7': 0x76, 'f8': 0x77, 'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
        'ctrl': 0x11, 'control': 0x11, 'shift': 0x10, 'alt': 0x12, 'menu': 0x12,
        'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'esc': 0x1B, 'space': 0x20, 'win': 0x5B,
    }
    KEYEVENTF_KEYUP = 0x0002
    vks = []
    for p in hotkey_str.lower().split("+"):
        if p in VK:
            vks.append(VK[p])
        elif len(p) == 1 and p.isalnum():
            vks.append(ord(p.upper()))
        else:
            raise ValueError(f"未知键: {p}")
    for vk in vks:
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _cleanup_yingdao_trigger():
    """验证过了之后删除触发文件 (如果是文件触发), 避免文件残留 / 重复触发。幂等, 可多调。"""
    trigger = os.getenv("YINGDAO_TRIGGER", "").strip()
    if not trigger:
        return
    method, _, arg = trigger.partition(":")
    if method.strip().lower() == "file" and arg.strip():
        try:
            if os.path.exists(arg):
                os.remove(arg)
        except Exception:
            pass


def _cleanup_trigger_files():
    """清理触发文件 (yingdao_trigger*.txt), 批量结束时调, 避免残留。"""
    trigger = os.getenv("YINGDAO_TRIGGER", "").strip()
    if not trigger:
        return
    method, _, arg = trigger.partition(":")
    if method.strip().lower() == "file" and arg.strip():
        arg_dir = os.path.dirname(arg.strip()) or "."
        base = os.path.splitext(os.path.basename(arg.strip()))[0]
        try:
            for fn in os.listdir(arg_dir):
                if fn.startswith(base) and fn.endswith(".txt"):
                    try:
                        os.remove(os.path.join(arg_dir, fn))
                    except Exception:
                        pass
        except Exception:
            pass


def _trigger_yingdao():
    """触发影刀应用 (处理人机验证的模拟点击)。方法由 env YINGDAO_TRIGGER 决定:
       file:C:\\path\\trigger.txt  → 创建触发文件, 影刀监听该文件  [最稳, 无需库]
       hotkey:f9                 → 发热键, 影刀监听该热键 (ctypes keybd_event)
       cmd:C:\\path\\trigger.bat  → 运行命令 (影刀 CLI / 你的 bat)
       不设 → 不触发, 回退内置自动点。"""
    trigger = os.getenv("YINGDAO_TRIGGER", "").strip()
    if not trigger:
        return False
    method, _, arg = trigger.partition(":")
    method, arg = method.strip().lower(), arg.strip()
    try:
        if method == "file":
            import time as _t
            arg_dir = os.path.dirname(arg) or "."
            os.makedirs(arg_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(arg))[0]
            # 清理旧触发文件 (yingdao_trigger*.txt), 避免堆积 + 避免影刀 watcher 混淆
            try:
                for fn in os.listdir(arg_dir):
                    if fn.startswith(base) and fn.endswith(".txt"):
                        try:
                            os.remove(os.path.join(arg_dir, fn))
                        except Exception:
                            pass
            except Exception:
                pass
            _t.sleep(0.5)
            # 每次用唯一文件名(毫秒时间戳) → 影刀监听目录"新文件创建"事件, 每次都是真·新文件 → 必触发
            trig_fn = os.path.join(arg_dir, f"{base}_{int(_t.time()*1000)}.txt")
            with open(trig_fn, "w", encoding="utf-8") as f:
                f.write("lab5-turnstile-trigger")
            print(f"    🤖 已创建触发文件 → {os.path.basename(trig_fn)} (影刀监听目录新文件 → 触发后请点 Turnstile)")
            return True
        elif method == "hotkey":
            _send_hotkey_ctypes(arg)
            print(f"    🤖 已发热键 [{arg}] 触发影刀")
            return True
        elif method == "cmd":
            import subprocess
            subprocess.Popen(arg, shell=True)
            print(f"    🤖 已运行 [{arg}] 触发影刀")
            return True
    except Exception as e:
        print(f"    ⚠ 触发影刀失败: {e}")
        return False
    return False


def _wait_human_verify(page, ctx, timeout=120):
    """若页面是人机验证/挑战页(Turnstile / CF edge managed challenge / 'Just a moment'), 等它通过再返回。
    检测: Turnstile iframe / cf-turnstile / #challenge-form / cf-turnstile-response / cf-spinner + 挑战文本。
    先自动点 Turnstile 复选框(暖 IP + 可信浏览器可能自动过); 过不了再等用户点。
    通过 = 挑战消失 或 cf_clearance cookie 出现。"""
    import time

    def has_challenge():
        # 已过 → False (cf_clearance 或 cf-turnstile token 在 → 不算活跃挑战, 避免解了还死等)
        try:
            if any(c.get("name") == "cf_clearance" for c in ctx.cookies()):
                return False
        except Exception:
            pass
        try:
            el = page.query_selector('[name="cf-turnstile-response"]')
            tok = el.get_attribute("value") if el else ""
            if tok:
                return False
        except Exception:
            pass
        try:
            ts = page.query_selector_all(
                'iframe[src*="challenges.cloudflare"], [class*="cf-turnstile" i], [class*="cf-challenge" i], '
                '#challenge-form, #cf-challenge-form, [name="cf-turnstile-response"], [class*="cf-spinner" i], '
                '#challenge-stage, [class*="turnstile" i], [id*="challenge" i], iframe[src*="turnstile"]')
            if ts:
                return True
        except Exception:
            pass
        try:
            from lxml import html as LH
            txt = " ".join(LH.fromstring(page.content()).xpath("//text()")).lower()
            if any(k in txt for k in ("checking your browser", "verify you are human", "are you a robot",
                                      "please confirm you are a human", "just a moment", "正在检查",
                                      "请确认您是真人", "verifying you are", "verify you are",
                                      "security check", "attention required", "请稍候", "安全验证",
                                      "人机验证", "确认您是真人", "we need to verify", "confirm you are human")):
                return True
        except Exception:
            pass
        return False

    def has_article_content():
        """页面是否已有真文章内容 (author-group/article/h1) → 有就说明没挑战, 不用再等。"""
        try:
            if page.query_selector('article, .author-group, [class*="author-group" i]'):
                return True
        except Exception:
            pass
        return False

    # 挑战可能晚渲染 (networkidle 后才出现) → 先轮询 ~4s: 挑战出现就处理, 真内容出现就过
    if not has_challenge() and not has_article_content():
        _t0 = time.time()
        while time.time() - _t0 < 4:
            page.wait_for_timeout(500)
            if has_challenge() or has_article_content():
                break
    if not has_challenge():
        return True  # 没挑战, 已是正文
    print(f"    ⏳ 检测到人机验证/挑战页 (≤{timeout}s)")
    # 1. 先试触发影刀 (配了 YINGDAO_TRIGGER 时) 处理点击
    if _trigger_yingdao():
        print("    ⏳ 已触发影刀, 等它点验证 (≤60s)...")
        _t0 = time.time()
        while time.time() - _t0 < 60:
            if not has_challenge():
                print("    ✅ 影刀点过了, 继续抽邮箱")
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                _cleanup_yingdao_trigger()  # 验证过了, 删触发文件 (避免残留/重复触发)
                return True
            page.wait_for_timeout(1000)
        print("    ⚠ 影刀触发了但 60s 没过, 回退内置自动点")
    # 2. 内置自动点 Turnstile 复选框 (暖 IP 可能自动过); 过不了再等你点
    if _try_auto_click_turnstile(page):
        print("    ⏳ 已自动点复选框, 等它自动过 (≤30s)...")
        _t0 = time.time()
        while time.time() - _t0 < 30:
            if not has_challenge():
                print("    ✅ 自动点复选框过了, 继续抽邮箱")
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                _cleanup_yingdao_trigger()  # 验证过了, 删触发文件 (避免残留/重复触发)
                return True
            page.wait_for_timeout(1000)
        print("    ⚠ 自动点没自动过 (IP 可能冷), 等你手动点 (≤剩余时间)")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not has_challenge():
            print("    ✅ 人机验证通过, 继续抽邮箱")
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            return True
        try:
            if any(c.get("name") == "cf_clearance" for c in ctx.cookies()):
                print("    ✅ cf_clearance 拿到 (挑战过), 继续抽邮箱")
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                _cleanup_yingdao_trigger()  # 验证过了, 删触发文件 (避免残留/重复触发)
                return True
        except Exception:
            pass
        page.wait_for_timeout(1000)
    print(f"    ⚠ 人机验证 {timeout}s 没过 (headless 遇交互式验证过不了; 用 --headed 你点)")
    return False


def _accept_cookies(page, timeout=6):
    """人机验证过后, 点 cookie 同意横幅的 'Accept all' (OneTrust 等), 免得它挡住正文/✉。返回是否点了。
    无横幅时快速退出 (不空等满 timeout) —— 第一篇点完横幅后, 后续篇多数没有横幅, 空等是纯浪费。"""
    import time
    t0 = time.time()
    sels = ['#onetrust-accept-btn-handler', '[id*="accept" i]', 'button[class*="accept" i]',
            'a[class*="accept" i]', '[class*="accept-all" i]', '[class*="cookie-accept" i]',
            '[data-accept]', '[class*="ot-accept" i]', '#accept-all']
    scans = 0
    while time.time() - t0 < timeout and scans < 3:  # 最多 3 次扫描 (初次 + 2 次晚渲染重试)
        scans += 1
        for sel in sels:
            for el in page.query_selector_all(sel):
                try:
                    if el.is_visible():
                        el.click(timeout=2000)
                        print(f"    🍪 点了 cookie 同意 ({sel})")
                        page.wait_for_timeout(800)
                        return True
                except Exception:
                    pass
        # 通用: 可见按钮文本含 accept all / agree / 同意全部 / 接受所有
        for el in page.query_selector_all("button, a, [role=button]"):
            try:
                if not el.is_visible():
                    continue
                t = (el.inner_text() or "").lower().strip()
                if t and any(k in t for k in ("accept all", "agree to all", "i agree", "accept cookies",
                                              "同意全部", "全部接受", "接受所有", "接受全部", "同意并继续", "agree")):
                    el.click(timeout=2000)
                    print(f"    🍪 点了 cookie 同意: {t[:30]}")
                    page.wait_for_timeout(800)
                    return True
            except Exception:
                pass
        if scans < 3:
            page.wait_for_timeout(800)  # 给晚渲染的横幅时间, 但不长等
    return False


def reveal_auto(target, headless, max_tries=15, settle=4):
    """全自动: 渲染 → DOM 里已有邮箱(mailto/corr 文本)就收 → 没有就试点一批可点元素(按钮/icon/svg/role, 跳过 nav 链接),
    每点一次查邮箱出没出, 出即停, 不出按 Esc 关弹窗继续。不靠关键字猜 ✉, 点+验。
    返回 (emails[(em,src)], final_html, note, xhr_emails[(em,url)])。"""
    from patchright.sync_api import sync_playwright
    xhr_responses = []

    def on_resp(resp):
        try:
            if resp.request.resource_type in ("xhr", "fetch"):
                xhr_responses.append(resp)
        except Exception:
            pass

    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless)
        ctx = b.new_context(); page = ctx.new_page()
        page.on("response", on_resp)
        page.on("dialog", lambda d: d.dismiss())  # 自动关 JS alert/confirm, 免得卡住流程
        try:
            page.goto(target, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        # ⚠ 先等人机验证(Turnstile / CF edge 挑战 / 'Just a moment')通过 + 真页加载, 再收/点 ✉
        hv_ok = _wait_human_verify(page, ctx, timeout=120 if not headless else 20)
        if not hv_ok:
            html = page.content(); ctx.close(); b.close()
            return [], html, "人机验证没过 (headless 遇交互式 Turnstile 过不了; 用 --headed 你点验证)", []
        # ⚠ 验证过后点 cookie 同意横幅 (OneTrust 等), 免得挡住正文/✉ (timeout 12s, 横幅可能晚弹)
        _accept_cookies(page, timeout=12)
        # all_found: 累计所有抓到的邮箱 (页可能有多个通讯作者/多个✉; 点开弹窗关了邮箱会从 DOM 消失, 不能只看最后一次)
        all_found = {}
        def _acc(items):
            for em, src in items:
                all_found.setdefault(em.lower(), (em, src))
        # 渲染后一次性收 (mailto 在 DOM 里就直接拿; 没有就靠下面点✉, 不再 20s 轮询 —— 对✉-门控页纯浪费)
        _acc(_collect_emails(page))
        note = "渲染后已有邮箱, 不用点" if all_found else "渲染后无邮箱, 进点✉"
        _accept_cookies(page, timeout=8)  # 再点一次 cookie (横幅可能晚弹)
        if not all_found:
            # 2. 试点候选(点+验): 按 ✉-似度排序先点 envelope/mail/email/correspond 的; 签名含 bbox(唯一, 避免无 class 的 SVG 碰撞);
            #    每轮重查(避 stale)+ 导航了 go_back + 没出 Esc 关弹窗; 打印点了啥好诊断
            url0 = page.url
            clicked_sigs = set()
            tried = 0
            cand_sel = ('button, [role="button"], svg, [class*="icon" i], [onclick], '
                        'a[href^="#"], a[href^="javascript"], '
                        '[class*="envelope" i], [class*="mail" i], [class*="email" i], [class*="correspond" i], '
                        '[class*="user" i]')
            KW = ("envelope", "mail", "email", "correspond", "通讯", "邮箱", "联系", "✉", "📧")

            def score(c):
                try:
                    # 只用 get_attribute (不用 inner_text —— SVG 没文本会抛 → score 返回 -1 → ✉ 信封 SVG 被过滤掉从没点过)
                    blob = " ".join(filter(None, [c.get_attribute(a) for a in ("class", "id", "aria-label", "title", "data-type")])).lower()
                except Exception:
                    return -1
                return sum(1 for k in KW if k in blob)

            while tried < max_tries:
                cands = page.query_selector_all(cand_sel)  # 每轮重查 → 句柄新鲜
                scored = []
                for c in cands:
                    try:
                        if not c.is_visible():
                            continue
                        attrs = " ".join(filter(None, [c.get_attribute(a) for a in ("class", "id", "aria-label", "title")])).lower()
                        if any(w in attrs for w in ("ot-sdk", "onetrust", "cookie", "consent", "gdpr", "anchor-primary", "gh-help", "button-link-second", "gh-move-to")):
                            continue  # 排除 cookie/consent/nav/help/secondary/spine 按钮 (都不是 ✉)
                        sig = (c.get_attribute("class") or "") + "|" + str(c.bounding_box())  # 不用 inner_text (SVG 会抛 → 候选被全跳过)
                        if sig in clicked_sigs:
                            continue
                        sc = score(c)
                        if sc > 0:  # 只点 ✉-似度>0 的 (有 email/mail/envelope/correspond 关键字); 装饰性 SVG(score 0)不点
                            scored.append((sc, c, sig))
                    except Exception:
                        continue
                if not scored:
                    break
                scored.sort(key=lambda x: -x[0])  # ✉-似度高分在前
                _, cand, sig = scored[0]
                clicked_sigs.add(sig)
                try:
                    cand.click(timeout=2000); tried += 1
                    tag = cand.evaluate("e=>e.tagName.toLowerCase()") or "?"
                    cls = (cand.get_attribute("class") or "")[:30]
                    print(f"    [试 {tried}] 点 <{tag} class={cls!r}>")
                    try:
                        page.wait_for_timeout(1500)
                    except Exception:
                        pass
                    print(f"    [试 {tried}] 点完等1.5s → 查子验证")
                    _wait_human_verify(page, ctx, timeout=60)  # 点 ✉ 又冒子 Turnstile? 等用户点
                    print(f"    [试 {tried}] 子验证查完 → 收邮箱")
                    _acc(_collect_emails(page))
                    print(f"    [试 {tried}] 收完, 累计 {len(all_found)} 个")
                    if all_found:
                        note = f"点第 {tried} 个候选(<{tag} {cls}>)后出邮箱"  # 不 break —— 继续点下一个✉ (多通讯作者)
                    if page.url != url0:
                        try:
                            page.go_back(timeout=8000); page.wait_for_load_state("networkidle", timeout=8000)
                        except Exception:
                            try:
                                page.goto(target, timeout=20000); page.wait_for_load_state("networkidle", timeout=8000)
                            except Exception:
                                pass
                    else:
                        try:
                            page.keyboard.press("Escape")
                        except Exception:
                            pass
                except Exception:
                    pass
            if not all_found:
                note = f"点了 {tried} 个候选都没出邮箱 (✉ 不在候选里 / 是 hover 不是 click / 要登录 / 邮箱没发布)"
                # dump 候选菜单, 方便看出 ✉ 是哪个 (贴给我我精确定位)
                try:
                    menu = page.query_selector_all(cand_sel)
                    print(f"    [候选菜单] 可见候选 (前 15, 已排除 cookie/nav/help/secondary):")
                    shown = 0
                    for c in menu:
                        if shown >= 15:
                            break
                        try:
                            if not c.is_visible():
                                continue
                            a = " ".join(filter(None, [c.get_attribute(x) for x in ("class", "id", "aria-label", "title")])).lower()
                            if any(w in a for w in ("ot-sdk", "onetrust", "cookie", "consent", "gdpr", "anchor-primary", "gh-help", "button-link-second", "gh-move-to")):
                                continue
                            tag = c.evaluate("e=>e.tagName.toLowerCase()") or "?"
                            print(f"      <{tag} class={(c.get_attribute('class') or '')[:40]!r}>")
                            shown += 1
                        except Exception:
                            pass
                except Exception:
                    pass
        # 3. 读 XHR body (点击触发的 email API 兜底) —— 找到邮箱就跳过 (resp.text() 会挂在不完整/streaming 响应上)
        xhr_emails = []
        if not all_found:
            for resp in xhr_responses[:20]:
                try:
                    body = resp.text()
                    for em in EMAIL_RE.findall(body):
                        xhr_emails.append((em, resp.url))
                except Exception:
                    pass
        try:
            html = page.content()
        except Exception:
            html = ""
        ctx.close(); b.close()
    return list(all_found.values()), html, note, xhr_emails


def _harvest(page, ctx, target, headless, max_tries=15, settle=4, force_click=False):
    """在已加载+已验证+已 cookie 的 page 上: 收 mailto/corr + 点✉(多作者累计)。返回 ([(email, src)], {email小写: 归属姓名})。
    reveal_auto(单篇) 和 reveal_auto_batch(多篇) 都调它, 不重复写点击逻辑。
    归属: 点✉前读候选所在作者容器的名字 → 点后新增的邮箱就归这个名字 (ScienceDirect ✉ 挂在作者按钮里)。
    force_click=True: 静态收到邮箱也不短路, 继续点✉拿姓名归属 (补全场景专用)。"""
    all_found = {}
    email_authors = {}
    def _acc(items):
        for em, src in items:
            all_found.setdefault(em.lower(), (em, src))
    _acc(_collect_emails(page))
    if all_found and not force_click:
        return list(all_found.values()), email_authors
    cand_sel = ('button, [role="button"], svg, [class*="icon" i], [onclick], '
                'a[href^="#"], a[href^="javascript"], '
                '[class*="envelope" i], [class*="mail" i], [class*="email" i], [class*="correspond" i], [class*="user" i]')
    KW = ("envelope", "mail", "email", "correspond", "通讯", "邮箱", "联系", "✉", "📧")

    def score(c):
        try:
            blob = " ".join(filter(None, [c.get_attribute(a) for a in ("class", "id", "aria-label", "title", "data-type")])).lower()
        except Exception:
            return -1
        return sum(1 for k in KW if k in blob)

    url0 = page.url
    clicked_sigs = set(); tried = 0
    while tried < max_tries:
        cands = page.query_selector_all(cand_sel)
        scored = []
        for c in cands:
            try:
                if not c.is_visible():
                    continue
                attrs = " ".join(filter(None, [c.get_attribute(a) for a in ("class", "id", "aria-label", "title")])).lower()
                if any(w in attrs for w in ("ot-sdk", "onetrust", "cookie", "consent", "gdpr", "anchor-primary", "gh-help", "button-link-second", "gh-move-to")):
                    continue
                sig = (c.get_attribute("class") or "") + "|" + str(c.bounding_box())
                if sig in clicked_sigs:
                    continue
                sc = score(c)
                if sc > 0:
                    # 已有邮箱时, 跳过非信封类候选 (person/user图标) 省时间
                    if all_found:
                        if not any(kw in attrs for kw in ("envelope", "mail", "email", "✉", "📧")):
                            continue
                    scored.append((sc, c, sig))
            except Exception:
                continue
        if not scored:
            break
        scored.sort(key=lambda x: -x[0])
        _, cand, sig = scored[0]
        clicked_sigs.add(sig)
        try:
            try:
                ctx_name = _clean_click_name(cand.evaluate(_CLICK_NAME_JS))
            except Exception:
                ctx_name = None
            prev_keys = set(all_found)
            cand.click(timeout=2000); tried += 1
            tag = cand.evaluate("e=>e.tagName.toLowerCase()") or "?"
            cls = (cand.get_attribute("class") or "")[:30]
            print(f"    [试 {tried}] 点 <{tag} class={cls!r}>" + (f" 作者={ctx_name}" if ctx_name else ""))
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
            print(f"    [试 {tried}] 点完等1.5s → 查子验证")
            _wait_human_verify(page, ctx, timeout=10)
            print(f"    [试 {tried}] 子验证查完 → 收邮箱")
            _acc(_collect_emails(page))   # 弹窗里的 mailto/correspondence-to (全策略汇总在最后)
            if ctx_name:
                for k in set(all_found) - prev_keys:
                    email_authors.setdefault(k, ctx_name)  # 点这个✉才冒出来的邮箱 → 归这个名字
            # 归属兜底: 弹窗还开着时抓邮箱锚点父容器的作者名 (SD "Author <名> View in Scopus")。
            # 必须在 Esc 关弹窗前跑, 否则锚点从 DOM 消失。
            if force_click or not email_authors:
                try:
                    email_authors.update(email_names_from_dom(page))
                except Exception:
                    pass
            print(f"    [试 {tried}] 收完 (弹窗邮箱 {len(all_found)}, 全策略最后统一汇总)")
            if page.url != url0:
                try:
                    page.go_back(timeout=8000); page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    try:
                        page.goto(target, timeout=20000); page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
            else:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
        except Exception:
            pass
    # 归属兜底: 点✉后从 DOM 邮箱锚点父容器解析 (SD "Author <名> View in Scopus"),
    # 补点击归属覆盖不到的 (icon-only ✉ 按钮没有名字, 名字在邮箱锚点容器)
    if force_click or not email_authors:
        try:
            email_authors.update(email_names_from_dom(page))
        except Exception:
            pass
    return list(all_found.values()), email_authors


def _get_title_authors(html):
    """从 HTML 抽文章标题 + 作者名 (best-effort, 用于结果溯源)。
    title: citation_title meta → <title>; authors: citation_author meta → author-group div 文本。"""
    title = ""
    authors = []
    try:
        from lxml import html as LH
        tree = LH.fromstring(html)
        ct = tree.xpath('//meta[@name="citation_title"]/@content')
        if ct:
            title = ct[0]
        else:
            t = tree.xpath('//title/text()')
            if t:
                title = t[0].strip()
        ca = tree.xpath('//meta[@name="citation_author"]/@content')
        if ca:
            authors = [a.strip() for a in ca if a.strip()]
        else:
            seen = set()
            for g in tree.xpath('//*[contains(@class,"author-group")]//text()'):
                t = g.strip()
                if not t or len(t) > 50 or t.startswith(("Author links", "author-ref")):
                    continue
                if re.match(r'^[A-Za-z一-鿿][A-Za-z一-鿿\s\.\-]+$', t) and t not in seen:
                    seen.add(t); authors.append(t)
    except Exception:
        pass
    return title, authors


def reveal_auto_batch(tasks, headless, csv_path=None, delay=(3, 8), rest_every=None):
    """一个浏览器会话遍历多篇: 第一篇人机验证(cf_clearance), 同会话后续篇复用 cf_clearance 不重验。
    每篇: goto + 验证(复用) + cookie + _harvest + extract(content) 兜底。返回 {url: {"emails","title","authors","email_author","claimed"}}。
    tasks: [(url, claimed_author)] —— claimed 可为空; 有 claimed 时逐邮箱校验"claimed 是否真通讯"。
    csv_path: 给了就边跑边存 CSV (断点续跑: 每篇存完即 flush, 中断不丢; 重跑读已存 article_url 跳过)。
    CSV 8 列: 原 5 列 + email_author(邮箱归属姓名) + claimed_author(任务给的) + name_check(match/mismatch/unverified)。
    delay: 篇间随机延迟秒 (养 IP, 降挑战频率), 默认 (3,8) 随机。rest_every: (每N篇, 歇S秒) 可选。"""
    from patchright.sync_api import sync_playwright
    import csv as _csv
    import random as _random
    results = {}
    fcsv = None; w = None
    if csv_path:
        import os as _os
        need_header = not _os.path.exists(csv_path) or _os.path.getsize(csv_path) == 0
        if not need_header:
            with open(csv_path, "r", encoding="utf-8-sig") as _fh:
                if "email_author" not in _fh.readline():
                    raise SystemExit(f"{csv_path} 是旧的 5 列格式, 新版要写 8 列 (含作者矫正)。先把旧文件改名备份再重跑。")
        fcsv = open(csv_path, "a", encoding="utf-8-sig", newline="")
        w = _csv.writer(fcsv)
        if need_header:
            w.writerow(["article_url", "title", "authors", "email", "source",
                        "email_author", "claimed_author", "name_check"])
    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless)
        ctx = b.new_context(); page = ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())
        consecutive_verify_fails = 0  # 连续验证失败计数 → ≥3 提前停 (IP 冷/没人点, 别浪费)
        consecutive_no_email = 0      # 连续没抓到邮箱计数 → ≥3 触发影刀兜底 (可能有没识别到的挑战)
        for i, (u, claimed) in enumerate(tasks):
            print(f"\n▶ [{i+1}/{len(tasks)}] {u}" + (f"  (claimed: {claimed})" if claimed else ""))
            # 篇间策略性延迟 (养 IP, 降挑战频率); 第一篇不等
            if i > 0 and delay:
                d = _random.uniform(delay[0], delay[1])
                print(f"    ⏸ 篇间延迟 {d:.1f}s (养 IP, 降挑战频率)")
                page.wait_for_timeout(int(d * 1000))
                if rest_every and i % rest_every[0] == 0:
                    print(f"    ⏸ 每 {rest_every[0]} 篇, 歇 {rest_every[1]}s")
                    page.wait_for_timeout(int(rest_every[1] * 1000))
            try:
                page.goto(u, timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
            except Exception as e:
                print("  goto 失败:", e); results[u] = {"emails": [], "title": "", "authors": []}; continue
            hv_ok = _wait_human_verify(page, ctx, timeout=120 if not headless else 20)
            if not hv_ok:
                consecutive_verify_fails += 1
                print(f"  验证没过, 跳过这篇 (连续 {consecutive_verify_fails} 篇)")
                results[u] = {"emails": [], "title": "", "authors": []}
                if consecutive_verify_fails >= 3:
                    print(f"  ⚠ 连续 {consecutive_verify_fails} 篇过不了验证 (IP 冷/没人点), 提前停止, 保住已存的, 剩下下次续跑")
                    break
                continue
            consecutive_verify_fails = 0  # 过了一篇, 重置
            _accept_cookies(page, timeout=6)
            _accept_cookies(page, timeout=3)  # 兜底晚弹的横幅, 短超时即可
            emails, click_authors = _harvest(page, ctx, u, headless)
            email_author = dict(click_authors)  # ✉ 点击上下文归属, 优先级最高
            # 也跑 extract(content) 兜底 script-embedded 等 + 抽标题/作者 (溯源)
            title, authors = "", []
            try:
                rhtml = page.content()
                title, authors = _get_title_authors(rhtml)
                for f in extract(rhtml)[0]:
                    if not f["corresponding"]:
                        continue  # 只要通讯作者 (避免把全部作者邮箱都收进来)
                    em = f["email"]; src = ",".join(f["sources"]) or "rendered"
                    if em.lower() not in {e.lower() for e, _ in emails}:
                        emails.append((em, src))
                    if f.get("author") and em.lower() not in email_author:
                        email_author[em.lower()] = f["author"]  # JSON-LD/mailto文本/通讯段窗口 补归属
            except Exception:
                pass
            results[u] = {"emails": emails, "title": title, "authors": authors,
                          "email_author": email_author, "claimed": claimed}
            # 边跑边存 CSV (断点续跑: 每篇存完即 flush, 中断不丢)
            if w is not None:
                astr = "; ".join(authors) if authors else ""
                for e, src in emails:
                    ea = email_author.get(e.lower(), "")
                    check = ""
                    if claimed:
                        check = ("match" if ea and name_match(claimed, ea)
                                 else ("unverified" if not ea else "mismatch"))
                    w.writerow([u, title, astr, e, src, ea, claimed, check])
                if not emails:
                    # 0 邮箱也留一行 (否则续跑读 CSV 找不到 → 当没跑过 → 死循环重跑)
                    w.writerow([u, title, astr, "", "", "", claimed or "", "unverified" if claimed else ""])
                fcsv.flush()
            if emails:
                consecutive_no_email = 0
                print(f"  ✅ 抓到 {len(emails)} 个: {[e for e, _ in emails]}")
                for e, _s in emails:
                    ea = email_author.get(e.lower(), "")
                    if claimed:
                        verdict = ("✓ 同一人" if ea and name_match(claimed, ea)
                                   else ("? 邮箱没定到人, 校验不了" if not ea else "✗ 不是此人 → 真通讯: " + ea))
                        print(f"     {e} ← {ea or '?'}  (claimed: {claimed} {verdict})")
                    elif ea:
                        print(f"     {e} ← {ea}")
            else:
                consecutive_no_email += 1
                print("  0 个")
                if os.getenv("YINGDAO_TRIGGER", "").strip():
                    print("  ⚠ 这篇没抓到 (可能有没识别到的挑战), 触发影刀兜底清挑战")
                    if _trigger_yingdao():
                        print("    ⏳ 已触发影刀, 等 60s 让它清挑战...")
                        for _ in range(60):
                            page.wait_for_timeout(1000)
                        print("    影刀处理完, 继续")
                    consecutive_no_email = 0
        ctx.close(); b.close()
    if fcsv is not None:
        fcsv.close()
    _cleanup_trigger_files()  # 批量结束清理触发文件, 避免残留
    return results


def _in_corr_ctx(node):
    """节点是否在"通讯/Corresponding"上下文 (祖先 class/id/role 含 correspond/通讯/通信)。
    用于 CF 邮箱混淆 —— CF 全站混淆所有邮箱, 不只通讯, 不能无脑标 corr。
    只看 class/id/role (不看 text_content —— body 全文多半含 correspond, 会全标 True)。"""
    try:
        for anc in node.iterancestors():
            for attr in ("class", "id", "role", "data-type"):
                if re.search(r"correspond|通讯|通信", anc.get(attr) or "", re.I):
                    return True
    except Exception:
        pass
    return False


def _norm_email(e):
    """规范化: 去 mailto: 前缀 + URL 解码 (%40→@) + 去 ?subject。
    防 JSON-LD 的 "email":"mailto:x@y" 和 percent-encoded mailto 被 EMAIL_RE 丢弃。"""
    e = (e or "").strip()
    if e.lower().startswith("mailto:"):
        e = e[7:]
    return unquote(e).split("?", 1)[0].strip()


def _add(found, email, source, corresponding=False, author=None, ctx=None):
    """把一个邮箱加进 found dict (按 email 去重, 累加 sources/author/corr/ctx)。规范化+校验在里头。"""
    e = _norm_email(email)
    if not e or not EMAIL_RE.fullmatch(e):
        return
    k = e.lower()
    if k not in found:
        found[k] = {"email": e, "author": author, "sources": [],
                    "corresponding": False,
                    "maybe_generic": bool(GENERIC_LOCAL.match(e)) or bool(CDN_DOMAIN.search(e)),
                    "ctx": None}
    f = found[k]
    if source not in f["sources"]:
        f["sources"].append(source)
    if author and not f["author"]:
        f["author"] = author
    if corresponding:
        f["corresponding"] = True
    if ctx and not f.get("ctx"):
        f["ctx"] = ctx


# ---- 作者姓名归属/矫正: 任务给的作者可能是第一作者不是真通讯 → 抓邮箱时顺带定"这邮箱是谁的" ----
_NAME_STOP = {"dr", "prof", "professor", "mr", "mrs", "ms", "miss", "md", "phd"}

def _canon_name(s):
    """姓名 → 小写 token 列表 (去 Dr/Prof 等头衔); 中文字符按整块保留。"""
    toks = re.findall(r"[a-z]+|[一-鿿]+", str(s or "").casefold())
    return [t for t in toks if t not in _NAME_STOP]

def _name_pair_match(a, b):
    """两姓名是否同一人: token 数相同, 顺序或反序逐位相等; 单位置允许首字母兼容 (L. Li ~ Lin Li)。"""
    if not a or not b or len(a) != len(b):
        return False
    def ok(x, y):
        return x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))
    if all(ok(x, y) for x, y in zip(a, b)):
        return True
    return len(a) >= 2 and all(ok(x, y) for x, y in zip(a, b[::-1]))

def name_match(claimed, actual):
    """claimed 作者与抓到的通讯作者是否同一人 (大小写/姓序名序/首字母兼容)。"""
    return _name_pair_match(_canon_name(claimed), _canon_name(actual))

def _match_author_in(text, authors):
    """在 text 里找 authors 列表中的某个人 (大小写不敏感, 姓序名序都试); 没找到 None。"""
    low = " ".join(str(text or "").casefold().split())
    for a in authors:
        toks = _canon_name(a)
        if not toks:
            continue
        for order in (" ".join(toks), " ".join(toks[::-1])):
            if order in low:
                return a
    return None

# UI 文案, 不是作者名 (ScienceDirect 等把链接 aria/占位文本渲染成可见文本) —— 过滤掉
_NAME_UI_NOISE = ("author links open overlay panel", "open overlay panel", "author links",
                  "author links open", "view full text", "show more", "show less", "share",
                  "download", "corresponding author", "correspondence to")

def _clean_click_name(s):
    """✉ 点击上下文文本 → 作者名; 空/含邮箱/超长/无字母/UI文案 → None。"""
    t = " ".join(str(s or "").split())
    if not t or "@" in t or len(t) > 60:
        return None
    if not re.search(r"[A-Za-z一-鿿]", t):
        return None
    if any(k in t.casefold() for k in _NAME_UI_NOISE):
        return None
    return t


def email_names_from_dom(page):
    """点✉后, 从 DOM 里 mailto 锚点的父容器文本解析 email→作者名。
    ScienceDirect 邮箱区块结构: "Author Shenbin Cao View in Scopus <单位> <mailto 锚点>"。
    比 _CLICK_NAME_JS (点✉前找作者按钮) 可靠 —— 邮箱和名字在弹窗注入后同容器。"""
    try:
        res = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('a[href^="mailto:"]').forEach(a => {
            const email = a.getAttribute('href').slice(7).split('?')[0].trim();
            if (!email) return;
            let pc = a;
            for (let i = 0; i < 4; i++) { if (pc.parentElement) pc = pc.parentElement; }
            const t = (pc.innerText || '').replace(/\\s+/g, ' ').trim();
            out.push({email: email, text: t});
          });
          return out;
        }""")
    except Exception:
        return {}
    m = {}
    for r in res:
        em = r["email"].lower()
        if not em or "@" not in em:
            continue
        mt = re.search(r"Author\s+([A-Za-z][A-Za-z\s\.\-']*?)(?:\s+View in Scopus|\s*$)", r["text"])
        if mt:
            name = " ".join(mt.group(1).split()).strip(" .")
            if name and not any(k in name.casefold() for k in ("scopus", "author")):
                m[em] = name
    return m

# ✉ 候选元素 → 所在作者容器 (class 含 author 且非大容器) 的首行文本 = 作者名
# author-group 是"所有作者"大容器 (innerText 是整列作者), 必须排除;
# 单作者容器才是目标 (但 SD 的 ✉ 在 icon-only 按钮里, 名字在同级兄弟按钮, 此 JS 读不到 →
#   归属靠 email_names_from_dom 解析邮箱锚点父容器, 见下)。
_CLICK_NAME_JS = """e => {
  let n = e;
  for (let i = 0; i < 5 && n; i++) {
    n = n.parentElement;
    if (!n) break;
    const cn = n.className;
    const cls = String(cn && cn.baseVal !== undefined ? cn.baseVal : (cn || ''));
    if (/author/i.test(cls) && !/author-group|content-authors|author-highlights|author-links|authors-list/i.test(cls)) {
      const t = (n.innerText || '').trim();
      if (t && t.length <= 60) return t.split('\\n')[0].trim();
    }
  }
  return '';
}"""


def extract(html_text):
    """层2: 多策略抽通讯作者邮箱。返回 (findings_list, n_authors, authors_list)。"""
    from lxml import html as LH
    found = {}

    def add(email, author, source, corresponding=False, ctx=None):
        _add(found, email, source, corresponding, author, ctx)

    try:
        tree = LH.fromstring(html_text)
    except Exception:
        return [], 0, []

    def metas(name):
        xp = f"//meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{name}']/@content"
        return [str(x) for x in tree.xpath(xp)]

    # 策略1: citation_* meta
    authors = metas("citation_author")
    for em in metas("citation_author_email") + metas("citation_email") + metas("citation_corresponding_author"):
        add(em, None, "citation_author_email", corresponding=True)

    # 策略2: JSON-LD (in_author 传播: author/contributor/creator 节点下的 email 算通讯)
    for sc in tree.xpath("//script[@type='application/ld+json']"):
        try:
            data = json.loads(sc.text_content() or "")
        except Exception:
            continue

        def walk(o, in_author=False):
            if isinstance(o, dict):
                if isinstance(o.get("email"), str):
                    add(o["email"], o.get("name"), "json-ld", corresponding=in_author)
                for k, v in o.items():
                    walk(v, in_author=(k in ("author", "contributor", "creator", "authors")) or in_author)
            elif isinstance(o, list):
                for v in o:
                    walk(v, in_author)
        walk(data)

    # 策略2.6: 脚本里嵌入的邮箱 (JS 数据 blob) —— 精准: 先 JSON 结构化解析(只取 correspond 标记的:
    #   correspondingAuthorEmail:"x" 或 author 对象里 corresponding:true 的 email); 解析不了回退 proximity(前60字符)
    for sc in tree.xpath("//script"):
        s = sc.text_content() or ""
        if not s.strip() or not EMAIL_RE.search(s):
            continue
        data = None
        try:
            data = json.loads(s)
        except Exception:
            i, j = s.find("{"), s.rfind("}")
            if i >= 0 and j > i:
                try:
                    data = json.loads(s[i:j + 1])
                except Exception:
                    data = None
        corr_emails = set()
        corr_refs = set()  # correspond 键下的非邮箱值 (多半是 id 引用, 如 correspondingAuthorId:"em005")
        id2email = {}      # {"em005":"x@y"} 从 {"id":"em005","href":"mailto:x@y"} 对象建

        def walk_corr(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    kl = k.lower()
                    if "correspond" in kl:
                        if isinstance(v, str):
                            if EMAIL_RE.fullmatch(v):
                                corr_emails.add(v.lower())
                            else:
                                corr_refs.add(v)  # id 引用
                        elif isinstance(v, list):
                            for vv in v:
                                if isinstance(vv, str):
                                    if EMAIL_RE.fullmatch(vv):
                                        corr_emails.add(vv.lower())
                                    else:
                                        corr_refs.add(vv)
                    if kl in ("corresponding", "iscorresponding", "iscorrespondingauthor",
                              "correspondingauthor", "is_corr") and v is True:
                        em = o.get("email") or o.get("mail") or o.get("emailAddress")
                        if isinstance(em, str) and EMAIL_RE.fullmatch(em):
                            corr_emails.add(em.lower())
                        el = o.get("emailLink") or o.get("email_link")
                        if isinstance(el, dict) and isinstance(el.get("id"), str):
                            corr_refs.add(el["id"])  # 对应作者引的 emailLink id
                    walk_corr(v)
            elif isinstance(o, list):
                for v in o:
                    walk_corr(v)

        def walk_idmap(o):
            if isinstance(o, dict):
                if isinstance(o.get("id"), str) and isinstance(o.get("href"), str):
                    h = o["href"].split("mailto:", 1)[-1].split("?")[0].strip()
                    if EMAIL_RE.fullmatch(h):
                        id2email[o["id"]] = h.lower()
                for v in o.values():
                    walk_idmap(v)
            elif isinstance(o, list):
                for v in o:
                    walk_idmap(v)
        if isinstance(data, (dict, list)):
            walk_corr(data)
            walk_idmap(data)
        corr_set = set(corr_emails)
        for ref in corr_refs:
            if ref in id2email:
                corr_set.add(id2email[ref])
        for m in EMAIL_RE.finditer(s):
            em = m.group(0)
            before = s[max(0, m.start() - 60): m.start()]
            # 结构化 corr_set 非空就只用它(精准, 不被邻近字段干扰); 空了回退 proximity(before, 找文本"通讯")
            ctx_corr = (em.lower() in corr_set) if corr_set else bool(re.search(r"correspond|通讯|通信", before, re.I))
            ctx = re.sub(r"\s+", " ", s[max(0, m.start() - 40): m.end() + 20]).strip()[:90]
            add(em, None, "script-embedded", corresponding=ctx_corr, ctx=ctx)

    # 策略2.5: JS char-code 邮箱 (String.fromCharCode(...) / [num,num,...]) —— drop script 前扫
    for sc in tree.xpath("//script[not(@type='application/ld+json')]"):
        s = sc.text_content() or ""
        for nums in re.findall(r"String\.fromCharCode\(([0-9,\s]+)\)", s) + re.findall(r"\[\s*([0-9]+(?:\s*,\s*[0-9]+)+)\s*\]", s):
            try:
                dec = "".join(chr(int(x)) for x in re.split(r"[,\s]+", nums.strip()) if x)
                for em in EMAIL_RE.findall(dec):
                    add(em, None, "js-charcode", corresponding=_in_corr_ctx(sc))
            except Exception:
                pass

    # 拿 body 文本前去 script/style (别把 JS 里的邮箱当正文)
    for s in tree.xpath("//script | //style"):
        par = s.getparent()
        if par is not None:
            try:
                par.remove(s)
            except Exception:
                pass
    # text_content() 会把相邻元素文本无空格拼起来 (john@smith.edu + "Jane" → john@smith.eduJane),
    # EMAIL_RE 还会认 (eduJane 是字母) → 垃圾邮箱。用 "//text()" 空格拼接隔开。
    body_text = " ".join(tree.xpath("//text()"))

    # 策略3: mailto —— 只有"单个 mailto"才默认通讯 (单作者页); 多 mailto 时只有 correspondence-section
    #   才算通讯 (避免把全部作者的邮箱都标通讯)。service-desk 邮箱靠 maybe_generic 过滤。
    mailto_links = tree.xpath("//a[starts-with(@href,'mailto:')]")
    single_mailto = len(mailto_links) == 1
    for a in mailto_links:
        em = a.get("href", "").split("mailto:", 1)[-1]
        name = _clean_click_name(a.text_content())  # 过滤 UI 文案 (如 "Author links open overlay panel")
        add(em, name, "mailto", corresponding=single_mailto)

    # 策略4: CF 邮箱混淆 —— data-cfemail 属性 + /cdn-cgi/l/email-protection#hex href 两种
    #   (CF 全站混淆, 不只通讯 → 靠 _in_corr_ctx 判是否通讯上下文)
    for node in tree.xpath("//*[@data-cfemail]"):
        try:
            add(decode_cfemail(node.get("data-cfemail", "")), None, "cf-email-obfuscation", corresponding=_in_corr_ctx(node))
        except Exception:
            pass
    for a in tree.xpath("//a[contains(@href, '/cdn-cgi/l/email-protection#')]"):
        try:
            hexpart = a.get("href", "").split("#", 1)[-1]
            add(decode_cfemail(hexpart), None, "cf-email-href", corresponding=_in_corr_ctx(a))
        except Exception:
            pass

    # 策略5: "Corresponding author/Correspondence to/通讯作者" 段 —— 锚短语, 紧窗口, 首个邮箱
    #   (不误匹配 "correspondence analysis"/"corresponding to baseline" 散文)
    for m in re.finditer(r"corresponding authors?(?:'s)?|correspondence to|\*corresponding author|通讯作者|通信作者", body_text, re.I):
        window = body_text[m.end(): m.end() + 120]
        em = next(iter(EMAIL_RE.findall(window)), None)
        if em:
            # 顺带定姓名: 先窗口内 ("Corresponding author: NAME, Email: ..."), 再锚前 ("NAME (corresponding author)...")
            author = (_match_author_in(window, authors)
                      or _match_author_in(body_text[max(0, m.start() - 80):m.start()], authors))
            add(em, author, "corresponding-section", corresponding=True)

    # 策略7: 反转 email (data-* attr reversed) —— 弱信号
    for node in tree.xpath("//*[@data-email or @data-mail or @data-href]"):
        for attr in ("data-email", "data-mail", "data-href"):
            v = node.get(attr) or ""
            rev = v[::-1]
            if EMAIL_RE.fullmatch(rev):
                add(rev, None, "reversed-data-attr", corresponding=False)

    # 策略6: at/dot 文本混淆 —— confirm-only (只标已发现的, 避免 "outliers at the dot plot" 散文误报)
    #   要求 "dot" 这个词 (不是句号), domain 段不许含点 —— 否则会贪吃 "data@repo.org. jane" 变垃圾
    for m in re.finditer(r'([A-Za-z0-9._%+-]+)\s*(?:\[?\(?(?:at|@)\]?\)?)\s*([A-Za-z0-9-]+)\s*(?:\[?\(?dot\]?\)?)\s*([A-Za-z]{2,})', body_text):
        em = f"{m.group(1)}@{m.group(2)}.{m.group(3)}".lower()
        if em in found and "at-dot-text" not in found[em]["sources"]:
            found[em]["sources"].append("at-dot-text")

    return list(found.values()), len(authors), authors


def report(findings, n_authors, authors):
    corr = [f for f in findings if f["corresponding"] and not f["maybe_generic"]]
    maybe = [f for f in findings if f["maybe_generic"]]
    print(f"  作者数(citation_author meta): {n_authors}  {' / '.join(authors[:6])}{' ...' if len(authors) > 6 else ''}")
    verbose = os.getenv("LAB5_VERBOSE", "").strip().lower() in ("1", "true", "yes")
    # 详细模式 (LAB5_VERBOSE=1) 才显示所有抓到的邮箱(含非通讯) + 脚本上下文; 默认只输出通讯作者
    if verbose and findings:
        print(f"  [详细 LAB5_VERBOSE] 共抓到 {len(findings)} 个 (通讯 {len(corr)}, 非通讯 {len(findings)-len(corr)}, 可能非作者 {len(maybe)}):")
        for f in findings:
            tag = "✉" if f["corresponding"] else " "
            mg = " [可能非作者]" if f["maybe_generic"] else ""
            print(f"  {','.join(f['sources'])[:24]:24} {f['email'][:42]:42} {tag}   {(f['author'] or '?')}{mg}")
        ctx_findings = [f for f in findings if f.get("ctx")]
        if ctx_findings:
            print(f"  脚本嵌入上下文 (核对 corr 判得准不准; 有 correspond/通讯 才算通讯):")
            for f in ctx_findings[:8]:
                print(f"     {f['email'][:30]:30} corr={f['corresponding']!s:5} ...{f['ctx']!r}")
    print(f"  → 通讯作者邮箱 ({len(corr)}):")
    for f in corr:
        print(f"     {f['email']}  ({f['author'] or '作者名待定'})  [{','.join(f['sources'])}]")
    if not corr and findings:
        print("     (没有强通讯信号 — 邮箱都有, 但只 mailto/citation_author_email/通讯段/CF(在通讯上下文) 的才算通讯)")
    if not findings:
        print("     0 个 — 站点可能不发布通讯邮箱, 或用了未覆盖的混淆; 贴 HTML 给我加解码")
    return corr


def diagnose(html_text):
    """0 个邮箱时, 诊断页面到底有没有邮箱/作者数据、在哪种形式里 —— 定位"为啥 0"。"""
    from lxml import html as LH
    print("  === 诊断: 这页到底有啥 ===")
    print(f"  HTML 大小: {len(html_text)} 字节" + (" (小 → 可能是 SPA 空壳, 内容靠 JS 后填, 渲染也只等了 3s)" if len(html_text) < 8000 else ""))
    try:
        tree = LH.fromstring(html_text)
    except Exception as e:
        print("  HTML 解析失败:", e)
        return
    def metas(n):
        xp = f"//meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{n}']/@content"
        return [str(x) for x in tree.xpath(xp)]
    print(f"  citation_author meta: {len(metas('citation_author'))}; citation_author_email: {len(metas('citation_author_email'))}; citation_doi: {len(metas('citation_doi'))}; citation_title: {len(metas('citation_title'))}")
    jld = tree.xpath("//script[@type='application/ld+json']")
    print(f"  JSON-LD 脚本: {len(jld)} 个")
    for s in jld[:1]:
        print(f"    片段: {(s.text_content() or '')[:200]!r}")
    print(f"  mailto 链接: {len(tree.xpath('//a[starts-with(@href,\"mailto:\")]'))}")
    print(f"  data-cfemail: {len(tree.xpath('//*[@data-cfemail]'))}; /cdn-cgi/email-protection#href: {len(tree.xpath('//a[contains(@href,\"/cdn-cgi/l/email-protection#\")]'))}")
    print(f"  data-email/data-mail/data-href 节点: {len(tree.xpath('//*[@data-email or @data-mail or @data-href]'))}")
    # loose 邮箱扫描: 任何邮箱形状的串 (不限作者, 不限上下文) —— 看页面到底有没有邮箱
    body_text = " ".join(tree.xpath("//text()"))
    loose = EMAIL_RE.findall(body_text)
    print(f"  正文邮箱形状串 (loose, 不限作者): {len(loose)} 个 → {loose[:8]}")
    # 邮箱图片
    email_imgs = [im for im in tree.xpath("//img")
                   if re.search(r"email|mail|@", (im.get("src", "") + " " + im.get("alt", "")), re.I)]
    print(f"  可能是邮箱的 <img>: {len(email_imgs)} 个")
    for im in email_imgs[:3]:
        print(f"    img src={im.get('src','')[:60]} alt={im.get('alt','')[:40]}")
    # 邮箱元素结构: class 含 email/mail/user/correspond/author 的元素 outerHTML (✉ 触发器多半在 .gh-user-email 旁)
    em_els = tree.xpath("//*[contains(@class,'email') or contains(@class,'mail') or contains(@class,'user') or contains(@class,'correspond') or contains(@class,'author')]")
    print(f"  邮箱/作者相关元素 (class 含 email/mail/user/correspond/author): {len(em_els)} 个")
    for el in em_els[:6]:
        try:
            print(f"    <{el.tag} class={el.get('class','')[:50]!r}> outerHTML: {LH.tostring(el, encoding='unicode')[:280]!r}")
        except Exception:
            pass
    # 脚本来源: 找邮箱数据源 (域名锁库 abort, 但源码/数据里可能有端点或嵌入邮箱)
    scripts = tree.xpath("//script")
    srcs = [s.get("src") for s in scripts if s.get("src")]
    print(f"  <script>: {len(scripts)} 个 (外链 {len(srcs)}, 内联 {len(scripts) - len(srcs)})")
    if srcs:
        for u in srcs[:6]:
            print(f"    src: {u[:90]}")
    inline_emails = EMAIL_RE.findall(" ".join((s.text_content() or "") for s in scripts))
    print(f"  内联脚本文本里邮箱形状串: {len(inline_emails)} 个 → {inline_emails[:8]}")
    hits = 0
    for sc in scripts:
        s = sc.text_content() or ""
        if len(s) < 5:
            continue
        for k in ["not authorized", "authorized", "fetch(", "ajax", "/api/", "getEmail", "email", "CSM", "smetrics"]:
            if k in s and hits < 4:
                i = s.find(k)
                print(f"    内联脚本(含 '{k}'): ...{s[max(0,i-50):i+100]!r}...")
                hits += 1
                break
    # correspond/通讯 在脚本里出现处 (看通讯作者是怎么标记的 —— id 引用? 字段? 标志?)
    corr_hits = []
    for sc in scripts:
        s = sc.text_content() or ""
        for m in re.finditer(r"correspond[a-z]*|通讯|通信", s, re.I):
            corr_hits.append(re.sub(r"\s+", " ", s[max(0, m.start() - 40): m.end() + 60])[:110])
    print(f"  脚本里 correspond/通讯 出现 {len(corr_hits)} 处 (看通讯作者怎么标记的):")
    for h in corr_hits[:6]:
        print(f"    ...{h!r}...")
    # 关键词出现处 (作者/通讯/邮箱 附近文本)
    shown = 0
    for kw in ["correspond", "correspondence", "author", "email", "e-mail", "通讯", "通信", "邮箱", "联系", "mailto"]:
        i = body_text.lower().find(kw)
        if i >= 0 and shown < 4:
            print(f"  '{kw}' 处: ...{body_text[max(0,i-25):i+70]!r}...")
            shown += 1
    print("  → 判读: loose 有邮箱串但没抓到 = 在未覆盖形式 (贴那段片段我加解码); loose=0 = 这页没发布邮箱 (换页 / 此页类型不发 / 数据靠更晚的 XHR 或点击才出)")


def selftest():
    """不开浏览器, 验证姓名归属/矫正逻辑: JSON-LD / mailto文本 / 通讯段窗口 三条归属路径 + name_match 匹配规则。"""
    html = """
    <html><head>
      <meta name="citation_author" content="Lin Li">
      <meta name="citation_author" content="Jianguo Liu">
      <script type="application/ld+json">{"@context":"https://schema.org","@type":"ScholarlyArticle",
        "author":[{"@type":"Person","name":"Lin Li","email":"linli@qu.edu.cn"},
                  {"@type":"Person","name":"Jianguo Liu"}]}</script>
    </head><body>
      <a href="mailto:jgliu@imut.edu.cn">Jianguo Liu</a>
      <p>Corresponding author: Lin Li, Email: linli_corr@qu.edu.cn</p>
    </body></html>
    """
    findings, _, _ = extract(html)
    by = {f["email"]: f for f in findings}
    assert by.get("linli@qu.edu.cn", {}).get("author") == "Lin Li", "JSON-LD 归属失败"
    assert by.get("linli@qu.edu.cn", {}).get("corresponding"), "JSON-LD author 节点下应为通讯"
    assert by.get("jgliu@imut.edu.cn", {}).get("author") == "Jianguo Liu", "mailto 链接文本归属失败"
    assert by.get("linli_corr@qu.edu.cn", {}).get("author") == "Lin Li", "通讯段窗口姓名匹配失败"
    assert name_match("Li Lin", "Lin Li"), "反序应匹配"
    assert name_match("L. Li", "Lin Li"), "首字母应匹配"
    assert name_match("lin li", "LIN LI"), "大小写应不敏感"
    assert not name_match("Jianguo Liu", "Lin Li"), "不同人不应匹配"
    assert not name_match("Lin Li", ""), "空名不应匹配"
    assert _clean_click_name("Lin Li") == "Lin Li"
    assert _clean_click_name("Email: x@y.edu.cn") is None
    assert _clean_click_name("  ") is None
    print("selftest OK: JSON-LD/mailto/通讯段 归属 + name_match (反序/首字母/大小写/拒配) 全过")


def main():
    ap = argparse.ArgumentParser(
        description="Lab5 取内容 (层2): 抽通讯作者邮箱",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  lab5 https://你的站/article/123     单篇\n"
               "  lab5 --list urls.txt --headed       批量 (一个会话验一次, cf_clearance 复用, 边跑边存 lab5_results.csv, 断点续跑)\n"
               "  lab5 --list tasks.txt               任务行: URL<TAB>claimed作者姓名 (校验 claimed 是否真通讯, 结果在 email_author/name_check 列)\n"
               "  lab5 --list urls.txt --delay 5-10 --rest-every 10:30   篇间延迟 5-10s 养 IP, 每 10 篇歇 30s\n"
               "  lab5 --self-test                    不开浏览器, 跑姓名归属逻辑自测\n"
               "  只打你自己的站。",
    )
    ap.add_argument("target", nargs="?", help="文章页 URL")
    ap.add_argument("--list", help="URL 列表文件 (一行一个; 行内用 TAB 分隔可带 claimed 作者: URL<TAB>姓名), 批量抽")
    ap.add_argument("--headed", action="store_true", help="渲染兜底弹真窗 (默认 headless)")
    ap.add_argument("--self-test", action="store_true", help="不开浏览器, 跑姓名归属/矫正逻辑自测")
    ap.add_argument("--delay", default="3-8", help="篇间随机延迟秒 (养 IP, 降挑战频率), 格式 MIN-MAX, 默认 3-8")
    ap.add_argument("--rest-every", default="", help="每 N 篇歇 S 秒, 格式 'N:S' (如 10:30), 可选")
    args = ap.parse_args()
    if args.self_test:
        return selftest()
    headless = (not args.headed) and (os.getenv("HEADLESS", "1") == "1")
    try:
        dmin, dmax = [float(x) for x in args.delay.split("-")]
        delay = (dmin, dmax)
    except Exception:
        delay = (3.0, 8.0)
    rest_every = None
    if args.rest_every and ":" in args.rest_every:
        try:
            n, s = args.rest_every.split(":")
            rest_every = (int(n), float(s))
        except Exception:
            rest_every = None

    tasks = []  # [(url, claimed_author)] —— claimed 可空; --list 行内 TAB 分隔 "URL<TAB>姓名"
    if args.list and os.path.exists(args.list):
        for ln in open(args.list, encoding="utf-8"):
            ln = ln.rstrip("\n")
            if not ln.strip() or ln.strip().startswith("#"):
                continue
            if "\t" in ln:
                u, claimed = ln.split("\t", 1)
                tasks.append((u.strip(), claimed.strip()))
            else:
                tasks.append((ln.strip(), ""))
    elif args.target:
        tasks = [(args.target, "")]
    elif os.getenv("TARGET_URL"):
        tasks = [(os.getenv("TARGET_URL"), "")]
    else:
        ap.print_help()
        print("\n给个文章页 URL 或 --list 文件。")
        return
    n_claimed = sum(1 for _, c in tasks if c)

    print("=" * 70)
    print("Lab5: 取内容 (层2) —— 抽通讯作者邮箱")
    print("=" * 70)
    print("""
层1 (lab1-4) 拿 200+HTML; 层2 从 HTML 抠字段。
通讯作者邮箱难点: 数量不定 / 判断谁是通讯 / 不漏 → 多策略并采 + union + 渲染兜底。
策略: citation_* meta / JSON-LD(in_author) / JS charcode / mailto / CF邮箱混淆(属性+href, 上下文判) / 通讯段(锚短语) / at-dot(confirm) / 反转attr
""")

    # 批量 (--list): 一个浏览器会话遍历 (验证一次) + 断点续跑 (边跑边存 lab5_results.csv, 重跑跳过已跑)
    if len(tasks) > 1:
        import csv as _csv
        csv_fn = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lab5_results.csv")
        # 断点续跑: 读已存的 article_url, 跳过
        done = set()
        if os.path.exists(csv_fn):
            try:
                with open(csv_fn, "r", encoding="utf-8-sig") as fcsv:
                    for row in _csv.DictReader(fcsv):
                        if row.get("article_url"):
                            done.add(row["article_url"])
            except Exception:
                pass
        todo = [t for t in tasks if t[0] not in done]
        skipped = len(tasks) - len(todo)
        if skipped:
            print(f"断点续跑: lab5_results.csv 已有 {skipped} 篇结果, 跳过")
        if not todo:
            print("全部 URL 都已跑过 (lab5_results.csv), 无需重跑。想重跑请删 lab5_results.csv")
            return
        dnote = f"篇间延迟 {delay[0]:g}-{delay[1]:g}s 养 IP" + (f", 每 {rest_every[0]} 篇歇 {rest_every[1]:g}s" if rest_every else "")
        print(f"批量模式: {len(todo)} 篇待跑 (其中 {sum(1 for _, c in todo if c)} 篇带 claimed 作者待校验), "
              f"一个浏览器会话 (验证一次, cf_clearance 复用), {dnote}, 边跑边存 lab5_results.csv")
        batch = reveal_auto_batch(todo, headless, csv_path=csv_fn, delay=delay, rest_every=rest_every)
        print("\n" + "=" * 70)
        total_n = sum(len(d["emails"]) for d in batch.values())
        print(f"批量小结: {len(todo)} 篇, 共 {total_n} 个通讯作者邮箱")
        for u2, d in batch.items():
            print(f"  {len(d['emails'])} 个  {u2}  {d['title'][:40]}")
            for e, src in d["emails"]:
                ea = d.get("email_author", {}).get(e.lower(), "")
                print(f"     {e}{' ← ' + ea if ea else ''}  [{src}]")
            if d.get("claimed"):
                eas = sorted({v for v in d.get("email_author", {}).values() if v})
                if any(name_match(d["claimed"], v) for v in eas):
                    print(f"     校验: claimed {d['claimed']} ✓ 是真通讯")
                elif eas:
                    print(f"     校验: claimed {d['claimed']} ✗ 不是通讯 → 真通讯: {' / '.join(eas)}")
                else:
                    print(f"     校验: claimed {d['claimed']} ? 邮箱都没定到人, 无法校验")
        print(f"  📄 结果已逐篇存到 {csv_fn} (断点续跑: 重跑跳过已跑的; 中断再跑会续)")
        print("=" * 70)
        return

    total_corr = []
    for u, _claimed in tasks:
        print(f"\n▶ {u}")
        print(">>> 层1: 暖 IP 拿 HTML (curl_cffi + 全 Chrome header + chrome146)")
        try:
            html_text, status, hdrs = fetch_html(u)
        except Exception as e:
            print("  fetch 失败:", e)
            continue
        print(f"  HTTP {status}")
        low = {k.lower(): v for k, v in hdrs.items()}
        for h in WAF_HEADERS:
            if h in low:
                print(f"    {h}: {str(low[h])[:80]}")
        rendered = False
        if status != 200:
            print("  层1 没过 (非200) — 先 lab4 --headed 暖IP/拿 cf_clearance; 这里试渲染兜底绕过")
            try:
                html_text = fetch_rendered(u, headless)
                rendered = True
                print("  渲染兜底拿到 DOM")
            except Exception as e:
                print("  渲染失败:", e)
                continue
        print(">>> 层2: 多策略抽" + (" (渲染后 DOM)" if rendered else " (原始 HTML)"))
        findings, n_authors, authors = extract(html_text)
        last_html = html_text
        corr_found = [f for f in findings if f["corresponding"] and not f["maybe_generic"]]
        if not corr_found:
            print("  没强通讯信号 → 全自动: 渲染, 已有就收, 没有就试点候选直到出邮箱")
            try:
                dyn_emails, rhtml, note, xhr_em = reveal_auto(u, headless)
                fdict = {f["email"].lower(): f for f in findings}
                for em, src in dyn_emails:
                    _add(fdict, em, src, corresponding=True)
                for em2, url in xhr_em:
                    _add(fdict, em2, "xhr:" + url[:40], corresponding=True)
                for f in extract(rhtml)[0]:
                    for src in f["sources"]:
                        _add(fdict, f["email"], src, f["corresponding"], f.get("author"))
                findings = list(fdict.values())
                last_html = rhtml
                if dyn_emails or xhr_em:
                    print(f"  ✅ 自动抓到: {[e for e, _ in dyn_emails] + [e for e, _ in xhr_em]}  ({note})")
                else:
                    print(f"  {note}")
            except Exception as e:
                print("  自动抓失败:", e)
        corr = report(findings, n_authors, authors)
        if not findings:
            diagnose(last_html)
        total_corr.extend((u, c) for c in corr)

    if len(tasks) > 1:
        print("\n" + "=" * 70)
        print(f"批量小结: {len(tasks)} 篇, 共 {len(total_corr)} 个通讯作者邮箱")
        per = {}
        for url, c in total_corr:
            per.setdefault(url, []).append(c["email"])
        for url, ems in per.items():
            print(f"  {len(ems)} 个  {url}")
            for e in ems:
                print(f"     {e}")
        print("  (数量不定: 每篇通讯作者数不同; 不漏: 多策略 + 渲染兜底 union 去重)")
        print("=" * 70)

    print("\n学到什么:")
    print("  - 层1 过墙 ≠ 层2 取内容; 取内容看页面把数据放哪 (meta / JSON-LD / CF混淆 / JS渲染)。")
    print("  - 通讯判定: mailto/citation_author_email/通讯段/CF邮箱(在通讯上下文) = 强信号; CF全站混淆靠上下文判。")
    print("  - 不漏 = 多策略并采 + union 去重 + 渲染兜底; 散文误报靠 confirm-only + maybe_generic 压。")
    print("  - 邮箱规范化 (mailto:/%40) 防 JSON-LD/percent-encoded 丢; JS charcode/CF-href 是常被漏的形式。")
    print("=" * 70)


if __name__ == "__main__":
    main()
