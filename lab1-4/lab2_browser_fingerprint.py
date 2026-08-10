# -*- coding: utf-8 -*-
"""
指纹实验室 Lab2 —— 浏览器指纹泄露对比 (vanilla headless vs stealth)
目的: 直观看到 headless Chromium 漏出的"自动化痕迹", 以及 stealth 如何补
目标端点: example.com (中立, 仅用于注入 JS 读 navigator 属性)
学习点: 服务器侧检测的是这些浏览器环境信号, 不是你的 IP
"""
import os
from playwright.sync_api import sync_playwright

PROBES = """
() => {
    const out = {};
    out.webdriver = navigator.webdriver;
    out.plugins_len = navigator.plugins.length;
    out.languages = navigator.languages.join(',');
    out.platform = navigator.platform;
    out.vendor = navigator.vendor;
    out.chrome_runtime = (typeof window.chrome !== 'undefined');
    out.permissions = (typeof navigator.permissions !== 'undefined');
    out.webgl_vendor = (() => {
        try {
            const c = document.createElement('canvas');
            const gl = c.getContext('webgl');
            return gl ? gl.getParameter(37445) : 'no-webgl';
        } catch(e) { return 'err:' + e; }
    })();
    out.canvas_hash = (() => {
        try {
            const c = document.createElement('canvas');
            const ctx = c.getContext('2d');
            ctx.textBaseline = 'top';
            ctx.font = "14px 'Arial'";
            ctx.fillStyle = '#f60';
            ctx.fillRect(0, 0, 100, 30);
            ctx.fillStyle = '#069';
            ctx.fillText('fingerprint-test', 2, 15);
            return c.toDataURL().slice(-32);
        } catch(e) { return 'err'; }
    })();
    out.ua = navigator.userAgent;
    return out;
}
"""

def probe(label, use_stealth):
    print(f"\n>>> {label}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        if use_stealth:
            try:
                from playwright_stealth import Stealth
                Stealth().apply_stealth_sync(ctx)
                print("  [stealth 已注入]")
            except Exception as e:
                print(f"  [stealth 不可用, 回退 vanilla: {e}]")
        page = ctx.new_page()
        target = os.getenv("TARGET_URL", "https://example.com")
        page.goto(target, timeout=20000)
        res = page.evaluate(PROBES)
        browser.close()
        for k, v in res.items():
            print(f"  {k:16} = {v}")
        return res

if __name__ == "__main__":
    print("=" * 70)
    print("Lab2: 浏览器指纹泄露 (vanilla headless vs stealth)  目标 example.com")
    print("=" * 70)
    vanilla = probe("客户端A: vanilla headless Chromium", use_stealth=False)
    stealth = probe("客户端B: headless + playwright-stealth", use_stealth=True)
    print("\n" + "=" * 70)
    print("关键对比:")
    print(f"  navigator.webdriver  vanilla={vanilla.get('webdriver')}  vs stealth={stealth.get('webdriver')}")
    print(f"  plugins.length       vanilla={vanilla.get('plugins_len')}  vs stealth={stealth.get('plugins_len')}")
    print(f"  chrome runtime       vanilla={vanilla.get('chrome_runtime')}  vs stealth={stealth.get('chrome_runtime')}")
    print("结论: vanilla 漏 webdriver=true / 无插件 => 一眼脚本; stealth 把这些补成正常浏览器值")
    print("=" * 70)
