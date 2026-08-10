# 反爬对抗指纹实验室

> 以学习为目的，实地演示网站反爬机制——从"网站如何检测自动化"到"如何合规地取回页面内容"。

项目包含两层：**层1（lab1–4）** 系统性实验"过墙"——TLS/浏览器指纹、本地判官、Turnstile 解码；**层2（lab5）** 主用工具——从学术文章页提取通讯作者邮箱，支持批量断点续跑。

---

## 环境要求

- Python venv：`C:\Users\hao.guo\.workbuddy\binaries\python\envs\default`
- 依赖：`curl_cffi`、`playwright`、`playwright-stealth`、`patchright`（反检测 playwright fork）、`camoufox`（反检测 Firefox）、`lxml`（lab5 HTML 解析）

---

## 快速开始

```
lab5 https://你的站/article/123        # 单篇：抽通讯作者邮箱
lab5 --list urls.txt --headed          # 批量：一个会话验一次，边跑边存，断点续跑
```

层1 实验在 `lab1-4/` 子目录，见下方 [lab1-4 学习实验](#lab1-4-学习实验)。

---

## lab5：通讯作者邮箱提取（主工具）

lab5 从文章页 HTML 中提取**所有通讯作者**的邮箱，三个难点及其解法：

| 难点 | 解法 |
|---|---|
| 通讯作者数量不定 | list + union 去重 + `n_authors`（citation_author meta）计数 |
| 判断谁是通讯作者 | 强信号（mailto / citation_author_email / 通讯段 / CF 邮箱在通讯上下文）才标 corr；弱信号不标 |
| 不漏 | 多策略并采（不短路）+ 渲染兜底 + ✉ 点击流（多作者累计） |

**提取策略**（全跑，结果并集）：`citation_*` meta / JSON-LD（in_author 传播）/ JS charcode / mailto / CF 邮箱混淆 / 通讯段 / at-dot / 反转 attr / 脚本嵌入。静态抓不到时启用动态抓取（XHR 拦截 + 点 reveal），再不行自动诊断 0 邮箱原因。

### 单篇提取

```
lab5 https://你的站/article/123        # 单篇
lab5 --headed https://...              # 渲染兜底时弹真窗
```

真站通讯作者邮箱常藏在 ✉ 图标点开后的侧栏，lab5 会逐个点击信封并累计邮箱；若遇人机验证（Turnstile），先自动点复选框（暖 IP 通常直接过），过不了再等手动。

### 批量提取

```
lab5 --list urls.txt --headed                        # 一个会话验证一次，cf_clearance 复用
lab5 --list urls.txt --headed --delay 5-10 --rest-every 10:30   # 篇间延迟 5-10s 养 IP，每 10 篇歇 30s
```

- **cf_clearance 复用**：第一个文章页验证一次，后续同会话共享放行 cookie，不重复验证；过期则重新触发验证。
- **篇间延迟养 IP**：`--delay MIN-MAX` 每篇随机延迟，`--rest-every N:S` 每 N 篇额外歇 S 秒，降低触发挑战的频率。
- **断点续跑**：每篇结果即时写入 CSV，中断后重跑自动跳过已完成文章。

### 结果文件

`lab5_results.csv`（UTF-8 BOM，8 列）：

```
article_url, title, authors, email, source, email_author, claimed_author, name_check
```

| 列 | 含义 |
|---|---|
| `article_url` / `title` / `authors` | 文章溯源 |
| `email` / `source` | 邮箱及其来源策略 |
| `email_author` | 邮箱归属的作者姓名（best-effort，页面结构支持时才有） |
| `claimed_author` / `name_check` | 批量任务带 claimed 作者时，校验其是否为真通讯作者（match / mismatch / unverified） |

默认只输出通讯作者邮箱（减噪）；设 `LAB5_VERBOSE=1` 可看全部抓到（含非通讯，用于调试通讯判定）。

### 已知边界

- **邮箱归属姓名是 best-effort**：✉ 点击上下文、JSON-LD、mailto 链接文本可归到姓名；纯 `<meta>` 或脚本嵌入且无紧邻姓名的邮箱归不到。
- **抽取只能拿"已交付"的数据**：若页面用域名锁库拉取邮箱（库在非授权域名上 abort），邮箱从未渲染，lab5 抽不到——需站主解锁或改发 `citation_author_email` meta。

---

## lab1-4 学习实验

层1 用于实地弄清"网站怎么检测我"。全部在 `lab1-4/` 子目录，入口 `lab`（统一跑全套）：

```
cd lab1-4 && lab                       # 全套：TLS / 浏览器指纹 / 本地判官 / Turnstile demo
cd lab1-4 && lab https://你的站/       # 全套打你的站（lab1 仍打 tls.peet.ws）
cd lab1-4 && lab --headed https://你的站/  # 弹真窗，区分 headless 检测 vs 指纹层
```

| Lab | 主题 | 核心结论 |
|---|---|---|
| lab1 | TLS / HTTP2 指纹 | 服务器对 ClientHello 做 MD5 → JA3 哈希。urllib/requests 的哈希是众所周知的脚本指纹，一眼被识破；`curl_cffi` 套用 Chrome 的 BoringSSL 参数集，哈希与真 Chrome 一致。 |
| lab2 | 浏览器指纹 | vanilla headless Chromium 漏 `navigator.webdriver`、plugins、window.chrome、WebGL、UA 等明显痕迹；`playwright-stealth` 逐个补齐。 |
| lab3 | 反爬判官 + 实战 | 端到端验证：stealth 只补 navigator 不够，header 层（Accept-Language/UA）也得配齐。真实 Cloudflare 站实测见附录。 |
| lab4 | Turnstile 解码 | Turnstile 不是图形验证码，是 bot-score 挑战——环境过关才吐 token。两条流别混：widget token vs edge clearance（cf_clearance）。 |

---

## 附录：实验明细

### A. 层1 指纹实测

| 客户端 | JA3 hash | HTTP2 指纹 | UA |
|---|---|---|---|
| urllib | a1ebe7f9... | 无 | python-urllib |
| requests | 7291ea5e... | 无 | python-requests |
| curl_cffi (chrome120) | f5b6df53... | 有 | 真实 Chrome |

### B. Cloudflare 六客户端矩阵（真实 Bot Management 站）

覆盖"无状态 HTTP"到"反检测浏览器"的完整梯度：

| 客户端 | headless | headed |
|---|---|---|
| urllib 裸 | 403 硬挡 | 403 硬挡 |
| curl_cffi (chrome120) | 200 ✅（概率性） | 200 ✅（概率性） |
| curl_cffi (chrome146) + 全 header | 200 ✅ / 403 challenge（抖） | 403 challenge（抖） |
| playwright + stealth | 403 硬挡 | 403 Turnstile |
| patchright | 403 硬挡 | 403 Turnstile |
| camoufox | 403 challenge（Turnstile） | 403 Turnstile |

三条确认结论：

1. **CDP 不是卡点**：patchright（补 CDP leak）与 playwright+stealth 结果逐字相同——stealth 路线从根上是错方向。
2. **TLS 栈有边际作用但过不了墙**：headless 下 Chromium 硬挡、Firefox 降到 challenge，但最终都倒在 Turnstile。
3. **墙 = CF Bot Management 的 JS challenge / Turnstile**：能跑 JS 的真浏览器全被识破。唯一缝隙是暖 IP 下的无状态 curl_cffi。

**CF 判定信号**（读响应头）：`server: cloudflare` + `cf-ray` → CF 是边缘；`cf-mitigated: challenge` → Bot Management 专属头（软挡）；`__cf_bm` cookie → bot beacon；`set-cookie: EUID=...` → 放行。

### C. Turnstile 实测

- 官方测试 sitekey 下，裸 headless 也能拿到 token——只证明采集管线通，**不证明能过真站**；siteverify 的 `success` 完全由 secret 决定。
- 真站两条流：**widget token**（表单内嵌 sitekey，后端 siteverify 验）和 **edge clearance / cf_clearance**（整站挑战页，放行 cookie，绑 IP+UA）。
- **cf_clearance 可无状态复用**：同 IP + 同 UA + Chrome 指纹一致时，搬到 curl_cffi 即可在 TTL 内批量抓；换 IP/换机器即失效。
- **暖 IP 是最省的过墙法**：同一 IP 多次"人样"访问后 CF 降 bot-score，连挑战都不发，全自动直连 200。代价是只在那台机器那个 IP 有效、暖度会衰减。
- 解 Turnstile 的梯度：L0 无 JS（没 token）→ L1 裸 headless（被标 bot）→ L1.5 反检测 → L2 住宅代理 → L3 打码服务 → L4 人点（高危会话兜底）。

### D. lab5 实测与踩坑

- **✉ 点击流**：真站 2 通讯作者页跑通，多作者累计不漏。
- **域名锁库终墙**：域名锁 JS 库在非授权域 abort，邮箱从未渲染，0 个可抽——不是 lab5 的锅，是"数据未交付"。
- **通讯作者过滤**：mailto 策略只对"单个 mailto"默认通讯，多 mailto 需在 correspondence-section 才算；批量输出只留 corr=True。
- **踩坑记录**：SVG `inner_text` 会抛（改 `get_attribute`）；点信封后 `networkidle` 会挂（改固定等待）；弹窗关闭后邮箱从 DOM 消失（累计 `all_found`）；cookie 横幅晚弹（`_accept_cookies` 调两次）。

---

## 方法学：如何系统性学习一层反爬

1. **抓包**——用 Wireshark/mitmproxy 抓真实 Chrome 的 ClientHello，记录参数集。
2. **对比**——用 `tls.peet.ws` 这类端点看自己客户端漏什么。
3. **定位差异**——哪个字段和 Chrome 不一致。
4. **补齐**——找对应工具（curl_cffi / stealth）复刻该字段。
5. **回测**——再打检测端点，直到指纹一致。

---

## 合规声明

本项目仅用于**学习与自己网站的反爬对抗**。验证码自动点击、打码服务等仅限自有站点测试，不得用于他人网站。
