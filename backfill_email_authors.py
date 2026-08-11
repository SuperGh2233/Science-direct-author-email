# -*- coding: utf-8 -*-
"""
邮箱补全 —— 给 lab5 无姓名清单里的邮箱补上作者姓名。

背景: lab5 抓到的邮箱多数来自 citation_author_email meta / ✉ 弹窗, 不带姓名;
姓名藏在 ✉ 点击流 (点信封时读作者容器名) / JSON-LD / mailto 文本里。
本脚本对清单里每篇文章重抓, 复用 lab5 的 extract() + _harvest() 拿 email→姓名映射。

用法:
  python backfill_email_authors.py                # 全量 (L1 静态 + L2 浏览器 ✉ 点击流)
  python backfill_email_authors.py --limit 10     # 只跑前 10 篇 (验证用)
  python backfill_email_authors.py --headed       # 浏览器弹真窗 (需手动过验证时)
  python backfill_email_authors.py --static-only  # 只跑 L1 静态 (无浏览器, 快)

输出: <result>/lab5无姓名_补全结果.csv (UTF-8 BOM), 原 xlsx 全部列 + 补全姓名/来源/是否在候选列表
断点续跑: <result>/.backfill_progress.json 记录每篇已完成的 {email: name}, 重跑跳过。
"""
import argparse, csv, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lab5_extract import (extract, fetch_html, _harvest, _wait_human_verify,
                          _accept_cookies, name_match, EMAIL_RE)

RESULT_DIR = r"C:\Users\hao.guo\Desktop\MJ-author\result"
XLSX = os.path.join(RESULT_DIR, "lab5无姓名待补清单.xlsx")
OUT_CSV = os.path.join(RESULT_DIR, "lab5无姓名_补全结果.csv")
PROG_JSON = os.path.join(RESULT_DIR, ".backfill_progress.json")

# xlsx 列 (第0行是表头): #, 所在sheet, 邮箱, DOI, 文章标题, 期刊, 年份, 单位中文, 销售, 审计状态, 论文作者列表(供判断)
COL_EMAIL, COL_DOI, COL_CANDS = 2, 3, 10


def load_rows():
    import openpyxl
    wb = openpyxl.load_workbook(XLSX, read_only=True)
    ws = wb.worksheets[0]
    rows = []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        email = str(r[COL_EMAIL] or "").strip()
        doi = str(r[COL_DOI] or "").strip()
        if not email:
            continue
        rows.append({"row": i, "email": email, "doi": doi,
                     "cands": [c.strip() for c in str(r[COL_CANDS] or "").split(";") if c.strip()],
                     "raw": r})
    return rows


def url_for(doi):
    return f"https://doi.org/{doi}" if doi else None


def in_candidates(name, cands):
    """补到的姓名是否命中论文作者候选列表 (任一候选 name_match)。"""
    if not name or not cands:
        return False
    return any(name_match(name, c) for c in cands)


def static_pass(doi):
    """L1: 无浏览器抓取 + extract, 返回 {email_lower: name}。失败/无归属返回 {}。"""
    url = url_for(doi)
    if not url:
        return {}
    try:
        html, status, _ = fetch_html(url)
        if status != 200:
            return {}
        findings, _, _ = extract(html)
    except Exception:
        return {}
    return {f["email"].lower(): f["author"] for f in findings
            if f["author"] and not f["maybe_generic"]}


def browser_pass(dois, headed):
    """L2: 一个浏览器会话遍历未归的篇, _harvest ✉ 点击流。返回 {doi: {email_lower: name}}。"""
    from patchright.sync_api import sync_playwright
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=not headed)
        ctx = b.new_context()
        page = ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())
        for i, doi in enumerate(dois):
            url = url_for(doi)
            print(f"\n▶ [{i+1}/{len(dois)}] {doi}")
            try:
                page.goto(url, timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
            except Exception as e:
                print("  goto 失败:", e)
                continue
            if not _wait_human_verify(page, ctx, timeout=120 if not headed else 20):
                print("  验证没过, 跳过 (换 IP 或 --headed 重跑)")
                continue
            _accept_cookies(page, timeout=6)
            _accept_cookies(page, timeout=3)
            try:
                emails, click_authors = _harvest(page, ctx, url, headed, force_click=True)
            except Exception as e:
                print("  _harvest 失败:", e)
                continue
            m = dict(click_authors)
            # 静态兜底: 页面最终 DOM 再 extract 一次补归属
            try:
                for f in extract(page.content())[0]:
                    if f.get("author") and f["email"].lower() not in m:
                        m[f["email"].lower()] = f["author"]
            except Exception:
                pass
            out[doi] = m
            print(f"  归属 {len(m)} 个: {[f'{k}←{v}' for k, v in m.items()]}")
        ctx.close(); b.close()
    return out


def main():
    ap = argparse.ArgumentParser(description="lab5 无姓名清单邮箱补全")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 篇 (验证用)")
    ap.add_argument("--headed", action="store_true", help="浏览器弹真窗 (需手动过验证)")
    ap.add_argument("--static-only", action="store_true", help="只跑 L1 静态, 不跑浏览器")
    args = ap.parse_args()

    rows = load_rows()
    print(f"清单 {len(rows)} 条邮箱")
    # 按 DOI 分组 (无 DOI 的单独放, 只能靠浏览器反查标题——本期跳过)
    by_doi = {}
    for r in rows:
        by_doi.setdefault(r["doi"], []).append(r)
    no_doi = [r for r in rows if not r["doi"]]
    if no_doi:
        print(f"  ⚠ {len(no_doi)} 条无 DOI, 本期跳过 (留待人工)")
    dois = [d for d in by_doi if d]
    print(f"去重后 {len(dois)} 篇待抓")
    if args.limit:
        dois = dois[:args.limit]
        print(f"  --limit 只跑前 {len(dois)} 篇")

    # 断点续跑
    prog = {}
    if os.path.exists(PROG_JSON):
        try:
            with open(PROG_JSON, encoding="utf-8") as f:
                prog = json.load(f)
        except Exception:
            prog = {}
    todo = [d for d in dois if d not in prog]
    if todo == dois:
        print(f"断点续跑: 已做 {len(dois) - len(todo)} 篇, 待做 {len(todo)} 篇")
    else:
        print(f"断点续跑: 已做 {len(dois) - len(todo)} 篇, 待做 {len(todo)} 篇")

    # L1 静态
    em2name = {}
    for doi in todo:
        m = static_pass(doi)
        if m:
            em2name.update(m)
            prog[doi] = {"src": "static", "emails": m}
            print(f"  [静态] {doi} → {len(m)} 个归属")
    left = [d for d in todo if d not in prog]
    print(f"静态归到 {len(em2name)} 条, 剩余 {len(left)} 篇进浏览器")

    # L2 浏览器
    if left and not args.static_only:
        bm = browser_pass(left, args.headed)
        for doi, m in bm.items():
            prog[doi] = {"src": "browser", "emails": m}
            em2name.update(m)
    elif left:
        print("  --static-only, 剩余篇留待下次")

    # 保存进度
    with open(PROG_JSON, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=1)

    # 回填输出
    rows_out = []
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["#", "所在sheet", "邮箱", "DOI", "文章标题", "期刊", "年份",
                    "单位中文", "销售", "审计状态", "论文作者列表(供判断)",
                    "补全姓名", "补全来源", "是否在候选列表"])
        done = nog = 0
        for r in rows:
            name = ""; src = ""
            if r["doi"] and r["doi"] in prog:
                pd = prog[r["doi"]]
                if r["email"].lower() in pd.get("emails", {}):
                    name = pd["emails"][r["email"].lower()] or ""
                    src = pd["src"]
            hit = in_candidates(name, r["cands"])
            raw = r["raw"]
            w.writerow([raw[0], raw[1], raw[2], raw[3], raw[4], raw[5], raw[6],
                        raw[7], raw[8], raw[9], raw[10],
                        name, src, "是" if hit else "否"])
            if name:
                done += 1
            else:
                nog += 1
        print(f"\n输出 {OUT_CSV}")
        print(f"补到姓名 {done} 条, 未补到 {nog} 条")


if __name__ == "__main__":
    main()