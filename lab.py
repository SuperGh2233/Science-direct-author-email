# -*- coding: utf-8 -*-
"""
反爬指纹实验室 — 统一入口

用法:
  lab                              # 跑全套 (lab1 tls.peet.ws, lab2 example.com, lab3 本地判官, lab4 demo 测试 key)
  lab https://你的站/              # 跑全套: lab2/3/4 打你的站, lab1 仍打 tls.peet.ws
  lab --headed https://你的站/     # lab3/lab4 弹出真窗口跑 (区分 headless 检测 vs 指纹层)
  python lab.py https://你的站/    # 同上 (不用 .cmd 时)

lab1 不吃目标参数 —— 它必须打 tls.peet.ws 这种 TLS 回显服务, 普通站不返回 JA3。
所以给目标 URL 时, lab1 仍然打 tls.peet.ws (它看的是客户端指纹, 与目标无关),
lab2/lab3/lab4 才用你的 URL。lab4 没给目标时跑 demo (官方测试 sitekey, 只打 localhost)。
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LABS = {
    "1": ("lab1_tls_fingerprint.py", "TLS / HTTP2 指纹 (传输层)"),
    "2": ("lab2_browser_fingerprint.py", "浏览器 navigator 指纹 (运行时层)"),
    "3": ("lab3_local_judge.py", "反爬判官 / 打目标站 (端到端)"),
    "4": ("lab4_turnstile.py", "Turnstile 解码 / 打码层 (demo 用官方测试 key)"),
}

def run_one(key, target=None, headed=False):
    script = os.path.join(HERE, LABS[key][0])
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if target:
        env["TARGET_URL"] = target
    if headed:
        env["HEADLESS"] = "0"  # lab3 的 playwright 客户端会弹真窗口
    label = LABS[key][1]
    tag = ""
    if target: tag += f"  target={target}"
    if headed: tag += "  [headed]"
    print(f"\n▶ Lab{key} — {label}{tag}", flush=True)
    print("=" * 70, flush=True)
    return subprocess.call([sys.executable, script], env=env)

def main():
    ap = argparse.ArgumentParser(
        description="反爬指纹实验室 — 给个 URL 就跑全套打它, 不给就跑默认 demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  lab                       跑全套默认\n  lab https://mysite.com/   全套打该站\n  lab --headed https://mysite.com/   lab3 弹真窗口 (区分 headless 检测)",
    )
    ap.add_argument("target", nargs="?", help="要测的目标站 URL (可选). 不给则 lab2=example.com, lab3=本地判官")
    ap.add_argument("--headed", action="store_true", help="lab3 的 playwright 用真窗口跑 (默认 headless). 用来区分 headless 检测 vs 指纹层")
    args = ap.parse_args()

    target = args.target
    rc = 0
    for k in ["1", "2", "3", "4"]:
        # lab1 不吃目标 (打 tls.peet.ws 回显服务); lab2/3/4 用目标
        # lab4 没给目标时跑 demo (官方测试 key, 只打 localhost), 给了才打你的站
        t = None if k == "1" else target
        rc = run_one(k, t, args.headed) or rc
    sys.exit(rc)

if __name__ == "__main__":
    main()
