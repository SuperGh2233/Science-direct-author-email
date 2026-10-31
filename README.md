# ScienceDirect 通讯作者邮箱提取工具

一个用于学术文章页面信息整理的 Python 实验项目，重点是从**用户有权访问且页面已公开呈现的内容**中提取通讯作者邮箱，并输出可追溯的 CSV 结果。

仓库包含两部分：

- **lab5：** 学术文章通讯作者邮箱提取与批量结果整理。
- **lab1–4：** TLS、浏览器指纹和网站自动化检测机制的学习实验，仅用于自有站点、测试环境或明确授权的安全研究。

## 使用边界

请优先使用出版社提供的官方 API、机构订阅接口、导出功能或经授权的数据源。

本项目不应被用于绕过登录、付费墙、访问控制、验证码、人机验证、速率限制或网站明确设置的自动化访问限制。遇到拒绝访问、挑战页面或权限不足时，应停止自动化请求，联系网站管理员或改用官方接口。

通讯作者邮箱属于个人联系信息。批量处理前应确认合法用途、访问授权和适用的数据保护要求；仅收集完成学术联系所需的信息，并妥善限制 CSV 文件的访问与保存期限。

## 环境要求

- Python 3.10+
- 项目所需依赖：`curl_cffi`、`playwright`、`playwright-stealth`、`patchright`、`camoufox`、`lxml`
- 使用浏览器渲染时，按所选 Playwright 浏览器安装对应运行时

建议创建独立虚拟环境：

```bash
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
# .venv\Scripts\Activate.ps1
```

依赖版本和浏览器安装方式应以当前环境及各依赖的官方文档为准。不要把个人虚拟环境路径写入项目配置或提交到 Git。

## lab5：通讯作者邮箱提取

### 提取信息

工具会综合检查页面中可访问的公开信息，例如：

- `citation_*` 元数据
- JSON-LD 结构化数据
- `mailto:` 链接
- 页面中明确标注的通讯作者区域
- 用户有权访问的、正常渲染后的页面内容

邮箱来源和作者归属会尽量保留证据。页面未提供明确作者关联信息时，不应把推测结果当成确定归属。

### 单篇文章

```bash
python lab5_extract.py "https://example.org/article/123"
```

请将示例地址替换为你有权访问的文章页面。若页面要求登录、订阅或触发自动化访问限制，请停止自动化抓取并使用授权方式获取数据。

### 批量文章

创建 `urls.txt`，每行放置一个已授权访问的文章页面：

```text
https://example.org/article/123
https://example.org/article/456
```

运行：

```bash
python lab5_extract.py --list urls.txt
```

工具支持将批量结果逐篇写入 CSV，便于中断后核对已处理记录。请根据数据源的使用政策设置合理请求间隔，不要通过更换 IP、伪装浏览器或复用挑战凭证来规避访问限制。

### 输出文件

默认结果文件为 `lab5_results.csv`（UTF-8 BOM），包含：

| 字段 | 含义 |
| --- | --- |
| `article_url` | 文章页面地址 |
| `title` | 文章标题 |
| `authors` | 作者列表 |
| `email` | 提取到的邮箱 |
| `source` | 邮箱来源位置或提取策略 |
| `email_author` | 页面证据能够支持时对应的作者姓名 |
| `claimed_author` | 用户提供的待核验作者姓名 |
| `name_check` | 作者核验状态：match / mismatch / unverified |

作者归属是 best-effort：只有页面结构提供足够证据时才关联姓名；无法确认时应保留为未验证，而不是猜测。

## lab1–4：自动化检测机制学习实验

`lab1-4/` 包含 TLS / HTTP 客户端指纹、浏览器可观察属性、自动化检测结果和测试挑战页面的实验代码。

这些实验适合在本地测试站点或明确授权的环境中观察客户端差异、理解检测信号和验证防护策略。请勿将其用于绕过第三方网站的安全控制或获取未授权数据。

## 自测

```bash
python lab5_extract.py --self-test
```

项目没有将真实第三方网站的可访问性作为自动化测试保证。网站结构、页面权限、地区策略和访问政策变化都可能影响提取结果。

## 项目结构

```text
.
├── lab5_extract.py       # 通讯作者邮箱提取入口
├── lab5.cmd              # Windows 命令包装
├── backfill_email_authors.py
└── lab1-4/
    ├── lab.py
    ├── lab1_tls_fingerprint.py
    ├── lab2_browser_fingerprint.py
    ├── lab3_local_judge.py
    └── lab4_turnstile.py
```

## 结果质量与限制

- 页面没有公开呈现邮箱时，工具无法可靠地恢复该信息。
- 动态页面、页面结构变化或浏览器运行环境可能导致提取不完整。
- 邮箱与作者姓名的对应关系必须以页面证据为准；`unverified` 不代表匹配。
- CSV 结果应抽样人工复核，不应未经核验直接导入通讯录或用于群发。

## 许可证

当前仓库根目录未提供独立的 LICENSE 文件。使用或再分发代码前，请先确认代码来源与授权范围。
