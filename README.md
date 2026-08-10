# 反爬对抗指纹实验室

以学习为目的，实地演示网站反爬机制——"怎么检测你"和"怎么被还原"。

## 当前进展

两层都已建好并真机实测（2026-08）：

- **层1 过墙**（lab1→4）：TLS 指纹 / 浏览器指纹 / 判官 / Turnstile 解码。你站实测：暖 IP 直连 200（L2）、手点 → cf_clearance → curl_cffi 批量复用（L4）、cf_clearance 可无状态复用（同 IP + UA + Chrome 指纹）。
- **层2 取内容**（lab5）：多策略抽通讯作者邮箱 + ✉ 点击流（多通讯作者累计）+ 0 时自动诊断。你站实测：✉ 点击流跑通 2 通讯作者页（`fuchunhua@mail.hust.edu.cn` + `yulongjiang@hust.edu.cn`）。
- **批量实战**（lab5 --list）：一个会话验一次（cf_clearance 复用）+ 篇间延迟养 IP（--delay/--rest-every）+ 边跑边存 `lab5_results.csv`（断点续跑，email ↔ 文章溯源）。
- **核心结论**：抽取只能拿"已交付"的数据；交付断了（域名锁库 abort）就得修站（站主加授权/删检测，或自己发 `citation_author_email` meta）。lab5 对已交付数据（✉ 可点的）能完整抽到。
- **自测**：lab5 合成 HTML 6/6 found / 0 误报 / 3 corr；lab4 demo 5 浏览器全过测试 key。

## 环境

- Python venv：`C:\Users\hao.guo\.workbuddy\binaries\python\envs\default`
- 依赖：`curl_cffi`、`playwright`、`playwright-stealth`、`patchright`（反检测 playwright fork）、`camoufox`（反检测 Firefox）、`lxml`（lab5 HTML 解析）

## 运行

lab1-4 已移到 `lab1-4/` 子目录；`lab5` 在根目录（主用工具）。先 `cd lab1-4` 再跑层1：

```
cd lab1-4 && lab                    # 全套层1 (lab1 tls.peet.ws, lab2 example.com, lab3 本地判官, lab4 demo 测试 key)
cd lab1-4 && lab https://你的站/     # 全套: lab2/3/4 打你的站, lab1 仍打 tls.peet.ws
lab5 https://你的站/article          # 层2 取内容 (抽通讯作者邮箱), 根目录独立入口
```

- **lab1 不吃目标参数**——它必须打 `tls.peet.ws` 这类 TLS 回显服务，普通站不返回 JA3；所以给目标 URL 时 lab1 仍打 tls.peet.ws（它看的是客户端指纹，与目标无关）。
- **判定挡在哪层**：看每个客户端 HTTP 状态码，全 200 = 没设防；403/挑战页 = 那层有检测。
- **WAF 厂商识别**：lab3 会读响应头里的厂商签名（`server`/`cf-ray`/`x-cdn`/`x-iinfo` 等），一眼看出是 Cloudflare/Imperva/Akamai/Sucuri 哪家在挡。
- **区分 headless 检测 vs 指纹/JS 层**：用 `--headed` 弹真窗跑 lab3——headed 能过而 headless 挂 = headless 检测；headed 也挂 = TLS/HTTP2 指纹或 JS challenge 层。
  ```
  lab --headed https://你的站/
  ```
- 想只跑某一个 lab：`venv/Scripts/python.exe lab4_turnstile.py`。
- **lab4**（Turnstile）在 `lab` 全套里；**lab5**（取内容）独立用根目录 `lab5.cmd`，见下方章节。

---

## Lab1 —— TLS / HTTP2 指纹（传输层）

**实测结果**

| 客户端 | JA3 hash | HTTP2 指纹 | UA |
|---|---|---|---|
| urllib | a1ebe7f9... | 无 | python-urllib |
| requests | 7291ea5e... | 无 | python-requests |
| curl_cffi (chrome120) | f5b6df53... | 有 | 真实 Chrome |

**结论**
- 服务器不解密 TLS，只对 ClientHello 的 5 个明文参数做 MD5 → JA3 哈希。
- urllib/requests 的哈希是固定的、广为人知的脚本指纹 → Cloudflare 一眼识破。
- `curl_cffi` 直接套用 Chrome 的 BoringSSL 参数集，哈希与真 Chrome 一致 = "指纹还原"。
- 这是最接近"协议逆向"的一层，但本质是**抓包重放**，没碰对方代码。

---

## Lab2 —— 浏览器指纹（运行时层）

**实测结果**

| 信号 | vanilla headless | + stealth |
|---|---|---|
| navigator.webdriver | **True** | False |
| plugins.length | **0** | 3 |
| window.chrome | **False** | True |
| WebGL vendor | **None** | Intel Inc. |
| UA | HeadlessChrome | Chrome |

**结论**
- vanilla headless Chromium 漏 5 个明显自动化痕迹 → 服务器侧 JS 一查就知是脚本。
- `playwright-stealth` 逐个把这些值补成正常浏览器值。
- 这是"环境拟真"，不读对方代码。

---

## Lab3 —— 本地反爬判官靶子（端到端，完全合规）

**实测结果**（自建 127.0.0.1 判官服务，模拟 header/UA 指纹检测）

| 客户端 | HTTP | 判定 | 关键信号 |
|---|---|---|---|
| urllib 裸 | 403 | bot | 无 sec-ch-ua / 无 Accept-Language / UA=python |
| curl_cffi (chrome120) | 200 | human | 全信号通过 |
| playwright + stealth | 403 | bot | 缺 Accept-Language + UA 残留 bot 词 |

**结论**
- 端到端看到"指纹还原"效果：curl_cffi 全信号通过 → 放行。
- **stealth 不是银弹**：反爬是多层，只补 navigator 不够，header 层（Accept-Language/UA）也得配齐。
- 所以实战要 `curl_cffi + stealth + 合理 header` 组合，而非单一工具。

### Lab3 实战：打真实 Cloudflare 站的六客户端矩阵

打一个真实 Cloudflare Bot Management 站（自有站），用 `--headed` 跑两档，六个客户端结果汇总。覆盖"无状态 HTTP"到"反检测浏览器"的完整梯度：

| # | 客户端 | TLS 栈 | 跑 JS | 定位 |
|---|---|---|---|---|
| 1 | urllib 裸 | Python ssl | ✗ | 基线，裸脚本 |
| 2 | curl_cffi chrome120 | Chrome 拟真 | ✗ | L1：老 profile 裸跑 |
| 3 | playwright+stealth | Chromium | ✓ | vanilla 浏览器 + navigator 补丁 |
| 4 | curl_cffi chrome146 + 真 Chrome header 全套 | Chrome 拟真 | ✗ | L1+：新 profile + 补 sec-ch-ua/sec-fetch/Accept-Language |
| 5 | patchright（无 stealth） | Chromium（CDP 补） | ✓ | L2：补 CDP leak，不叠 stealth |
| 6 | camoufox | Firefox | ✓ | L2+：换 TLS 栈（Chromium→Firefox） |

**实测结果**（同一 Cloudflare 站，headless / headed 两档）

| 客户端 | headless | headed |
|---|---|---|
| urllib 裸 | 403 硬挡 | 403 硬挡 |
| curl_cffi (chrome120) | 200 ✅（概率性） | 200 ✅（概率性） |
| curl_cffi (chrome146) + hdr | 200 ✅ / 403 challenge（抖） | 403 challenge（抖） |
| playwright + stealth | 403 硬挡 | 403 Turnstile（`cf-mitigated: challenge`） |
| patchright | 403 硬挡 | 403 Turnstile |
| camoufox | 403 **challenge**（Turnstile） | 403 Turnstile |

**三条确认结论**

1. **CDP 不是卡点**——patchright（补了 CDP leak）和 playwright+stealth 结果逐字相同（两档都一样）。补 CDP 零改善，stealth 那条路从根上是错方向。
2. **TLS 栈有边际作用，但过不了墙**——headless 下：Chromium（patchright）→ 硬挡；Firefox（camoufox）→ challenge。Firefox 的 bot 评分比 Chromium 低一档，印证"Chromium build 的 TLS 被 CF 标得更狠"，但离自动放行还差一截，两档都最终倒在 Turnstile。
3. **墙 = CF Bot Management 的 JS challenge / Turnstile**——每个能跑 JS 的真浏览器（Chromium/Firefox、补 CDP/叠 stealth、headless/headed）都被挑战或硬挡。CF 在比 TLS/CDP/navigator 更深的那层（Turnstile 的 JS 探针）识破自动化环境。

**唯一漏网：无状态 HTTP 客户端**
只有 curl_cffi（无状态、单请求、不跑 JS）能过，且只在 IP 信誉暖 + 概率时过，chrome146 还会抖。它能拿正文，但：不能跑 JS（JS 渲染页 / CF 下发 JS challenge 即废）；过是蹭线过的，不可靠。结论：**该站对"能跑 JS 的自动化"密不透风，对"无状态 HTTP 客户端"在暖 IP 下有缝隙。**

**CF 判定信号（从响应头读）**
- `server: cloudflare` + `cf-ray` → CF 是边缘；block 页上的 `Reference number` = cf-ray 前缀，所以那张带 IP 的 block 页是 CF 自己出的。
- `cf-mitigated: challenge` → **Cloudflare Bot Management 专属头**（企业版），CF 下发了 JS/managed challenge。有这个头 = 软挡（给挑战机会）；没有 + 直接 block 页 = 硬挡。
- `__cf_bm` cookie → CF bot management beacon。硬挡时不发 = bot 评分最高；发了 = 边缘级判定。
- `set-cookie: EUID=...` → 拿到正文（放行）的标志。

**站主调优建议（如何堵缝隙）**
- CF → Security → Settings / Bot Management，把"无 `__cf_bm` 历史 cookie 的首次请求"强制走 managed challenge（状态化），让无状态客户端蹭不过去。
- 调高 challenge→pass 的 bot-score 阈值，让蹭线的 200 变回 challenge。
- 拿 `cf-ray` 进 CF Dashboard → Security → Events 看每条具体 bot score + 触发信号（ML/JA3/HTTP2/行为），这是调参唯一权威依据。

**L3 天花板**
再往下是**解 Turnstile**——CF 专门设计来挡自动化的，要过它要么人点，要么接 `2captcha`/`capsolver` 这类付费打码服务。lab 目前测出的是"挡在哪层、是哪家"：Cloudflare Bot Management，JS-challenge/Turnstile 层，对 JS 自动化密不透风，对无状态客户端有缝隙。

**下一步**
根据自己的网站，学习如何解 Turnstile，通过这个过程学习"打码"相关技术。但**不能用于别的网站，只限于用自己的网站测试**！

---

## Lab4 —— Turnstile 解码（打码层）

L3 测出"挡在哪层、是哪家"（Cloudflare Bot Management 的 Turnstile）。Lab4 往下走：学"解 Turnstile"到底在解什么。

**核心认知（先破一个误区）：Turnstile 不是图形验证码。** 没有歪字要 OCR、没有红绿灯要点。它是一个看不见的 bot-score 挑战——CF 的 challenge JS 在你浏览器里跑，收集环境信号（TLS/指纹/行为/IP 信誉），环境过关就自动吐一个 token（`cf-turnstile-response`）。所以"解 Turnstile" = "拿到一个有效 token"，不是"认图"。打码服务（2captcha/capsolver）卖的就是这个 token——它们在自己的反检测浏览器 + 住宅代理农场里跑完 JS，把 token 给你。token 只能靠"环境过关"产，没法 OCR。

**两条路，千万别混（Lab4 最关键的概念）：**

| | Widget token（`cf-turnstile-response`） | Edge clearance（`cf_clearance`） |
|---|---|---|
| 哪来的 | 表单里嵌的 site key | CF 边缘整站挑战页（`cf-mitigated`） |
| 拿到什么 | 一个 token 字符串 | 一个放行 cookie |
| 谁验 | 你的后端，用 secret 去 siteverify | CF 边缘自己 |
| Lab3 信号 | — | `cf-mitigated: challenge` |
| 打码服务卖啥 | 就是这个 token | 单独的 "Cloudflare Challenge" 产品 |
| 难度 | 较易（页面里一个 JS） | 较难（跑全套 interstitial，绑 IP+UA） |

**解的梯度（从裸到工业）：**

| Lvl | 做法 | 结果 |
|---|---|---|
| L0 | curl_cffi 无 JS | 跑不了 challenge JS，没 token（Lab3 已证） |
| L1 | 真浏览器 headless 裸 | 跑了 JS 但被标 bot → 挑战升级或不吐 token |
| L1.5 | headed + 反检测（patchright/camoufox） | bot-score 低，隐形挑战可能自动吐 |
| L2 | + 住宅代理（干净 IP） | bot-score 够低，隐形挑战常自动过 → token 吐出 |
| L3 | 打码服务（capsolver 等） | 他们在 L1.5-L2 规模化跑，把 token 给你 |
| L4 | 人点 | Turnstile 给高危会话留的兜底 |

### Lab4 demo 实测（官方测试 key，只打 localhost）

用 Cloudflare 官方测试 sitekey `1x00000000000000000000AA`（always-pass，任何域名/localhost 都行，文档 `/turnstile/troubleshooting/testing/`）起一个本地 widget 页，三个浏览器去采 token，再用 siteverify 验。

| 客户端 | 采到 token | 说明 |
|---|---|---|
| vanilla playwright (headless) | `XXXX.DUMMY.TOKEN.XXXX` ✅ | 测试 key 跳过真挑战 → 连裸 headless 都拿到 |
| patchright (headless) | `XXXX.DUMMY.TOKEN.XXXX` ✅ | 同上 |
| camoufox (headless) | `XXXX.DUMMY.TOKEN.XXXX` ✅ | 同上 |

siteverify 验证矩阵（同一个 token × 两个 secret）：

| secret | success | error-codes |
|---|---|---|
| `1x0000000000000000000000000000000AA`（该过） | **True** | — |
| `2x0000000000000000000000000000000AA`（该不过） | False | `['invalid-input-response']` |

调用：`POST https://challenges.cloudflare.com/turnstile/v0/siteverify`，form 字段 `secret` + `response`（+可选 `remoteip`/`idempotency_key`）；JSON key 是连字符（`error-codes`），不是合法 Python 属性，要 `[...]` 取。

**结论**
1. 三个浏览器（含裸 headless）全拿到 token——因为测试 key 跳过了真挑战。**这只证明"采集 token + siteverify"管线通了，不证明你能过真站。**
2. siteverify 的 `success` 完全由 secret 决定：同一 token，PASS secret→true，FAIL secret→false。验证是站主服务端的事，不是浏览器侧。
3. 真站要换真 sitekey：此时裸 playwright 会被 CF 标 bot（官方文档明说），得上 patchright/camoufox + 干净 IP（L1.5-L2），仍不行就上打码服务（L3）。
4. 若你站是 edge 挑战（`cf-mitigated`）而非表单 widget——那是 `cf_clearance` 流，打 widget token 帮不了你；得跑 interstitial 拿放行 cookie。

### Lab4 真站模式（只打你自己的站）

```
python lab4_turnstile.py   # 设 TARGET_URL=https://你的站/
# 可选: TURNSTILE_SECRET=.. (siteverify), CAPSOLVER_API_KEY=.. + CAPSOLVER_PROXY=.. (edge 墙打码)
# 批量抓 (第2.7): URL_LIST_FILE=urls.txt | URL_LIST=u1,u2  MAX_REQ=25  JITTER_MIN=1.5  JITTER_MAX=4.0
```

- **第0步 探测**：patchright 加载你的站，报 status/WAF 头/有无 widget + sitekey。直接 200（无挑战）= 暖 IP（L2），不需 cf_clearance。
- **第1步 widget 流**：有 widget 就采 token（真挑战此时见分晓：裸挂、反检测可能吐）；给了 `TURNSTILE_SECRET` 就 siteverify。
- **第2步 edge 流**：看 cookie 里有没有 `cf_clearance`（绑 IP+UA，要真浏览器跑完 interstitial）。
- **第2.5步 curl_cffi 复用**：浏览器拿到 200 后（过墙 or 暖 IP 直连），把 cookie+UA 搬到 curl_cffi（全 Chrome header + chrome146 TLS）无状态复用，看能否拿正文。
- **第2.6步 批量抓**：复用成功就连发 5 次，验"点一次批量抓"摊得开。
- **第2.7步 批量抓多链接**：curl_cffi Session + 抖动（默认 1.5-4s）+ 403 重试 + **L4 回暖兜底**（撞天花板→弹窗人点→注回 cf_clearance→续抓）。URL 来自 `URL_LIST_FILE`/`URL_LIST` 或自动抽同源链接，循环到 `MAX_REQ`（默认 25）。看暖 IP 在第几十个请求被刷冷、回暖续命的全过程。
- **第3步 打码服务**：widget 站用 `AntiTurnstileTaskProxyLess`（无 proxy，返回 widget token）；**edge 站用 `AntiCloudflareTask`**（必填 proxy——cf_clearance 绑 IP，得从同一 IP 解+复用，返回 `solution.cookies.cf_clearance` + `userAgent`，再用 curl_cffi 走同 proxy 复用）。给 `CAPSOLVER_API_KEY` + `CAPSOLVER_PROXY` 即真跑，只打你自己的站。

### Lab4 真站实测（自有站，IP 112.64.53.45，VPN 关）

| 步 | 结果 |
|---|---|
| 第0步 探测 | 403，`cf-mitigated: challenge`，"Are you a robot?" 交互挑战页 → **edge 流，无 widget** |
| 第2步 edge（headed + 手点） | 7s 后 `cf_clearance` 落下，**二次请求 200** ✅ 真过墙 |
| 第2.5步 curl_cffi 复用 | 全 Chrome header + chrome146 TLS + 同 UA → **HTTP 200** ✅ 可无状态复用，拿到正文 |
| (再跑，IP 已暖) 第0步 | **直接 200**，`cf-mitigated: None`，无挑战 → **暖 IP（L2）达成，不需 cf_clearance** |

headless 对比（同 IP）：`cf-mitigated: None` + "There was a problem..." 硬挡错误页（连挑战都不给）——headless 比 headed 待遇更差一档。

**确认结论（你这站）**
1. **L4 手点能过**——headed patchright + 人点 checkbox → `cf_clearance` → 二次请求 200 = 真过墙。
2. **cf_clearance 可搬到 curl_cffi 无状态复用**——同 IP + 同 UA + 全 Chrome header（sec-ch-ua/sec-fetch/Accept-Language）+ chrome146 TLS（最近的拟真档）→ 200。点一次，之后 curl_cffi 在 ≈30min TTL 内批量抓，不必每请求开浏览器。**注：之前以为"搬不走"是错的——只要同 IP + UA + Chrome 指纹一致就搬得动；搬不动的是换 IP/换机器。**
3. **暖 IP 是最省的过墙法（L2）**——多点几次后 CF 把这 IP 的 bot-score 记低，连挑战都不发 → 第0步直接 200，curl_cffi 也直连 200，全自动，无需手点/cf_clearance/打码。这就是 Lab3 里 curl_cffi "概率性 200" 的暖 IP 机制。代价：只在那台机器那个 IP 有效，暖度会衰减。
4. **唯一非自动步** = 那一下手点（L4）。想全自动：L3 打码的 Cloudflare Challenge 产品（`AntiCloudflareTask`，必填 proxy，返回 `cf_clearance`）。

### Lab4 天花板

- demo（测试 key）证明的是"管线通"，不是"真 bypass"。真站 token 采不采得到，取决于浏览器环境 + IP 信誉能不能压下 bot-score。
- `cf_clearance` 绑 IP+UA（+Chrome 指纹）：同机器同 UA 下能搬到 curl_cffi 无状态复用（已验证 200）；但换 IP/换机器即失效。
- 打码服务两种产品：widget token（`AntiTurnstileTaskProxyLess`，无 proxy，常见便宜）；edge 墙的 `cf_clearance`（`AntiCloudflareTask`，**必填 proxy** 因绑 IP，lab4 已内置）。
- 最省力路径是暖 IP（L2）：同一 IP 多次"人样"访问后 CF 降 bot-score，连挑战都不发 → 全自动直连 200，不需手点/cookie/打码。代价：只在那台机那个 IP 有效，暖度会衰减，不是稳定工程方案。
- 再往下没有"纯技术解"——Turnstile 给高危会话留的兜底就是人点（L4），这是设计意图。

---

## Lab5 —— 取内容（层2）：抽通讯作者邮箱

层1（lab1-4）拿 200+HTML；lab5 从 HTML 抠出"所有通讯作者的邮箱"。三个难点 → 解法：

- **数量不定** → list + union + `n_authors`（citation_author meta）计数。
- **判断谁是通讯** → 强信号（mailto / citation_author_email / 通讯段 / CF 邮箱在通讯上下文）才算 corr；弱信号（at-dot/反转/charcode 不在通讯块）corr=False。
- **不漏** → 多策略并采（不短路）+ union 去重 + patchright 渲染兜底（没强通讯信号才渲染）。

**策略（全跑）**：`citation_*` meta / JSON-LD（in_author 传播，mailto:/%40 规范化）/ JS charcode（`String.fromCharCode`）/ mailto / CF 邮箱混淆（`data-cfemail` + `/cdn-cgi/...#hex` 两种，靠祖先 class 判通讯上下文）/ 通讯段（锚"Corresponding author"短语，不误匹配 correspondence analysis）/ at-dot（confirm-only，不加新）/ 反转 attr / 脚本嵌入（扫所有 `<script>` 文本里的邮箱，catch JS 数据 blob）。

没强通讯信号 → **动态抓** hunt_dynamic（networkidle + XHR 拦截 + `[class*=email]` 扫 + 点 reveal）+ 渲染兜底；还 0 → **自动诊断**（dump script src/内联片段/loose 扫描，定位邮箱在哪或为啥 0）。

```
lab5 https://你的站/article/123        # 单篇
lab5 --list urls.txt --headed          # 批量 (一个会话验一次, cf_clearance 复用, 边跑边存 lab5_results.csv, 断点续跑)
lab5 --list urls.txt --headed --delay 5-10 --rest-every 10:30   # 篇间延迟 5-10s 养 IP, 每 10 篇歇 30s
lab5 --headed https://...              # 渲染兜底弹真窗
```

**自测**（合成 HTML，每策略放一个邮箱）：6/6 found，0 误报，3 corr。覆盖：JSON-LD `mailto:` 规范化 / `%40` mailto 解码 / CF 全站混淆靠 class 上下文判（避免把 co-author 邮箱标通讯）/ JS charcode / service-desk 邮箱（authorhelp@）靠 `maybe_generic` 过滤 / "correspondence analysis" 不误匹配 / at-dot confirm-only 防散文误报 / `text_content()` 合并相邻文本的坑（改 `//text()` 空格拼接）。

**对抗审查**（4 视角：漏/误判通讯/未覆盖混淆/误报，folded in）：JSON-LD mailto: 丢 / 渲染兜底门槛（0→无强通讯）/ CF 全站过标 / correspondence-analysis 误匹配 / JSON-LD 结构性 corr=False / JS charcode 漏 / service-desk 误报 / CF href 变体漏 / at-dot 散文误报。**未覆盖**（等真站页面确认再加）：ROT13 / base64 data-attr / split data-attr / CSS pseudo-content。

### ✉ 点击流 + 批量实战（真站，已跑通）

**✉ 点击流（单篇）**——真站通讯作者邮箱藏在 ✉ 图标点开后的侧栏：

```
人机验证(Turnstile) → 你点 → cf_clearance → cookie 同意(OneTrust) → 收 mailto →
点 ✉ 信封(多通讯作者全点, score>0 按 envelope/mail/email/correspond 关键字) →
子验证(若有) → 收邮箱(累计 all_found, 弹窗关了邮箱从 DOM 消失也不丢) → 返回全部
```

实测：2 通讯作者页 → `fuchunhua@mail.hust.edu.cn` + `yulongjiang@hust.edu.cn` ✅（多作者累计，不漏）。

- **自动点 Turnstile**：检测到人机验证 → `_wait_human_verify` 先**自动点 Turnstile 复选框**（切到 challenges.cloudflare.com iframe 点复选框，暖 IP 可能自动过，≤30s）；过不了再等你手动点。暖 IP 全自动，冷 IP 才需要你。（只在你自己站学习用；别人站别用）
- **影刀触发**：配 `YINGDAO_TRIGGER` env → 检测到验证先触发影刀点 → 过不了回退内置自动点 → 再没过等你点。三种触发：`file:C:\path\t.txt`（创建文件，影刀监听"创建"）/ `hotkey:f9`（发热键）/ `cmd:C:\path\t.bat`（跑命令）。
- **触发文件清理 + 3 篇兜底**：验证过了自动删触发文件（避免残留重复触发）；**连续 3 篇没抓到邮箱 → 触发影刀兜底清挑战**（应对没识别到的挑战）。
- **检测可靠性**：`has_challenge()` 轮询 ~4s 等晚渲染的挑战 + 拓宽选择器/文本（Turnstile iframe/cf-challenge/challenge-stage/"verify you are human"/"安全验证"等）；页面有真内容（author-group/article）就跳过等待。解决"挑战晚渲染识别不到"。

**批量（`--list`）**——一个浏览器会话遍历，验一次：

- **cf_clearance 复用**：第一篇验一次，后续篇同会话 goto 时 cookie jar 复用 → 不重验；中途过期 → `_wait_human_verify` 检测到挑战 → 你重点（需 --headed）→ 拿新 cf_clearance。
- **篇间延迟养 IP**：`--delay MIN-MAX`（默认 3-8）每篇 goto 前随机延迟；`--rest-every N:S` 每 N 篇额外歇 S 秒。降挑战频率。

**结果文件（溯源）**——边跑边存 `lab5_results.csv`（每篇 flush，中断不丢）：`article_url, title, authors, email, source`。email ↔ 文章链接 + 标题 + 作者列表 → 跑一批也有完整溯源。作者名 best-effort（`citation_author` meta 或 `author-group` div）。

**断点续跑**——重跑读 `lab5_results.csv` 已有 `article_url` → 跳过；中断再跑接着续；想全重跑删该文件。

**输出默认只通讯作者**——控制台默认只打印通讯作者邮箱（非通讯的作者邮箱不显示，减噪）；想看全部抓到的（含非通讯，调试/核对通讯判定用）设 `LAB5_VERBOSE=1`。CSV 里存的也是通讯作者。

**踩过的坑（fix 记录）**
- `score()` 调 `inner_text` → SVG 抛 → ✉ 信封 SVG 被过滤从没点过 → 改只用 `get_attribute`。
- 点后 `networkidle` 挂（信封点击触发跳转/弹窗）→ 改固定 1.5s + 点后 `_wait_human_verify`。
- `resp.text()` 挂在不完整响应 → 找到邮箱就跳过 XHR 读取。
- 弹窗关了邮箱从 DOM 消失 → `all_found` 累计，不看最后一次。
- stray `found`（改 `all_found` 漏一处）崩 → 修；cookie 横幅晚弹 → `_accept_cookies` 调两次；删 20s 轮询（✉-门控页没 mailto 注入）。
- email ↔ 具体作者精确对应：best-effort（列页上所有作者）；精确对应需解析作者-信封关联（后续）。

### 真站实测（终墙：域名锁库文章）

自有站文章页（暖 IP，HTTP 200）跑 lab5：**0 个邮箱**（静态/渲染/XHR/点 reveal 全 0）。诊断 dump 显示：

- 页面 JS = Cloudflare Turnstile widget + Adobe 分析（`smetrics.elsevier.com`）+ 一个**域名锁 JS 库**抛 `"This library is not authorized for this domain. Please contact your CSM"` 然后 abort。
- `.gh-user-email` 元素在但空（citation_author/JSON-LD/mailto/cfemail 全 0）。
- **根因**：该拉邮箱的库（借的 Elsevier 组件）在你域名上拒绝运行 → 邮箱从没被拉 → 抽不到。
- **正解（Path A，站主）**：① 解锁那个库（配置加授权/删域名检测）→ 库跑 → 邮箱渲染 → lab5 抓；或 ② 更省：自己把邮箱发成 `citation_author_email` meta / mailto / JSON-LD（你 own 数据，别借锁死组件发）。
- **不是 lab5 的锅**：抽取只能拿已交付数据；交付断了（域名锁 abort）就得修站，不是加策略。这是层2 终墙。

### 真站实测（精度：通讯作者过滤）

- **问题**：一篇论文只抓出全部作者邮箱（11 个），但通讯作者只有 1-2 个 → 过度抓取。
- **根因**：mailto 策略把所有作者邮箱都标通讯；reveal_auto_batch 不过滤 corresponding。
- **修复**：
  - mailto **只有"单个 mailto"才默认通讯**（单作者页）；多 mailto 时只有 correspondence-section（靠 "Corresponding author" 文本）才算通讯。
  - reveal_auto_batch **只输出 corresponding=True** 的邮箱；✉ 弹窗邮箱（点信封抓的）算通讯。
- **实测**：一篇 2 通讯作者的论文 → `通讯作者邮箱 (2)`：`hongli@cqu.edu.cn`（✉ 弹窗）+ `lilanzhang@cqu.edu.cn`（correspondence-section）；其余 11 个 script-embedded 作者邮箱都标非通讯，不进通讯表。✅ 精度对了。

### 影刀触发（文件触发稳定性）

- **问题**：影刀触发失败一次后就再也触发不了。
- **根因**：同一文件名反复删/建，影刀 watcher 可能当"已存在/覆盖"不重新触发（或 watcher 一次性）。
- **修复**：每次用**唯一文件名（毫秒时间戳）** `yingdao_trigger_<时间戳>.txt` + 清理旧文件；影刀监听**目录的"新文件创建"**事件 → 每次都是真·新文件 → 必触发。
- **配置**：触发器配"监听目录 `...\anti-bot-lab\` 的新文件创建（匹配 `yingdao_trigger_*.txt`）"；或改用热键 `YINGDAO_TRIGGER=hotkey:f9`（更稳，无文件状态问题）。
- **实测**：唯一文件名 → 影刀触发 → 点过 Turnstile → ✅。

---

## 方法学：如何系统性学习一层反爬

1. **抓包**——用 Wireshark/mitmproxy 抓真实 Chrome 的 ClientHello，记录参数集。
2. **对比**——用 `tls.peet.ws` 这类端点看自己客户端漏什么。
3. **定位差异**——哪个字段和 Chrome 不一致。
4. **补齐**——找对应工具（curl_cffi / stealth）复刻该字段。
5. **回测**——再打检测端点，直到指纹一致。