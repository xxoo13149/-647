# 智联招聘数据爬虫 (Zhaopin Crawler)

这是一个用于自动化抓取招聘平台职位数据的本地工具。当前支持**智联招聘** (zhaopin.com) 和 **51job**，可以根据自定义关键词和城市/地区进行定向抓取，也可以在 Web 控制台中创建任务、查看进度、预览结果并下载 Excel。

## 技术实现细节

本项目主要使用 Python 基于 `playwright` 异步接口进行开发，结合了多种技术来提高数据抓取的稳定性和准确性：

- **自动化驱动底层**：使用 `playwright.async_api` 异步驱动无头浏览器 (Chromium) 模拟真实用户行为，支持对动态渲染页面的完美拉取。
- **混合解析策略**：
  - **状态拦截提取**：为了避免脆弱的 DOM 结构变动，程序优先通过正则提取页面源码中硬编码的 React `__INITIAL_STATE__` JSON 状态树，从中精准反序列化出结构化的职位数据。
  - **DOM 兜底解析**：当状态树未能提供足够包含度的信息（如岗位详情页摘要部分）时，回退到使用 `BeautifulSoup` (bs4) 进行网页 DOM 的解析提取。
- **反爬/防封策略优化**：
  - **仿人类行为延时**：实现了动态随机的等待机制（如翻页延时、打字延时、超时重试冷却、以及长时仿真休息 `long_break` 等），避免触发机器行为风控规则。
  - **设备环境伪装**：支持配置自定义 `USER_AGENT` 以及屏幕级 `VIEWPORT` 宽高，模拟不同的真实设备环境。
- **数据结构化输出**：使用 `pandas` 数据框架统一清洗、归一化薪资、城市信息和发布时间，并最终规范化导出。

## 核心功能

- **多地点多关键词**：支持配置多个搜索关键词和目标城市，自动跨维度进行数据采集。
- **不限地区模式**：在 Web 任务中城市/地区留空时，不进行城市过滤，所有城市的岗位都可以进入结果。
- **动态延时防封**：支持自定义各类点击、翻页、加载操作的随机等待延时，模拟真人操作。
- **Headless 模式**：支持无头浏览器后台静默运行。
- **自动错误重试**：遇到页面加载失败或数据为空时，支持自动刷新重试机制。
- **全局链接去重**：所有任务共用一个已爬详情链接文件，跨任务、跨重启避免重复访问同一岗位链接。
- **Web 历史任务持久化**：Web 任务信息会保存到本地，重新打开 Web 程序后仍可查看历史任务。

## 代码结构

- `main.py`：命令行入口和任务编排。
- `config.py`：`.env`、命令行参数和运行配置解析。
- `constants.py`：平台 URL、默认配置、城市映射和输出字段定义。
- `utils.py`：文本清洗、城市判断、延时、参数解析等通用工具。
- `zhaopin.py`：智联招聘搜索页、详情页解析和抓取流程。
- `fiftyone.py`：51job 搜索页、登录 Profile、详情页解析和抓取流程。
- `output.py`：岗位记录归一化、增量合并和 Excel 导出格式化。
- `web.py` / `web_app.py`：本地 Web 控制台、任务 API、历史任务持久化和页面路由。
- `static/frontend.js`：Web 前端状态、API 调用和浏览器路由。
- `static/views.js`：Web 前端页面渲染，包括首页、进行中、历史任务和任务详情。

## 环境要求

- Python 3.10+
- [python-dotenv](https://pypi.org/project/python-dotenv/) (用于读取本地 `.env` 配置)
- `playwright`、`pandas`、`beautifulsoup4`、`openpyxl`

## 安装步骤

1. **克隆/下载代码到本地存储库**
   ```bash
   cd zhaopin_crawler
   ```

2. **安装依赖**
   建议使用虚拟环境进行安装：
   ```bash
   pip install -r requirements.txt
   ```

3. **安装 Playwright 浏览器内核**
   本项目依赖 Playwright 来驱动浏览器抓取数据。在第一次运行前，您必须下载相关的浏览器二进制文件（由于本项目默认使用 Chrome/Chromium，建议仅安装 Chromium 以节省时间）：
   ```bash
   playwright install chromium
   ```

## 配置说明

项目根目录下需要存在 `.env` 配置文件（如果不存在，请复制一份预设的格式）。

```bash
copy .env.example .env
```

您可以通过修改 `.env` 的以下这些变量来定制抓取行为：

| 配置变量名 | 默认值示例 | 说明 |
| :--- | :--- | :--- |
| `KEYWORDS` | "大模型算法工程师,数据分析师,软件开发" | 想要搜索的职位关键词，用英文逗号分隔 |
| `REGIONS` | "北京,上海,深圳" | 指定抓取的城市/地区列表，用英文逗号分隔；命令行模式为空时会使用 `DEFAULT_REGIONS` |
| `DEFAULT_REGIONS` | "北京,上海,广州,深圳,杭州" | 命令行模式下 `REGIONS` 读取失败或为空时的备用城市列表 |
| `MAX_PAGES_PER_REGION`| 5 | 每个地区-关键词组合下最多抓取的页数上限 |
| `MAX_EMPTY_PAGE_RETRIES`| 2 | 若单次列表加载空白或失败，最大刷新重试次数 |
| `HEADLESS` | true | 浏览器是否启用无头/静默模式（true 隐藏界面，false 显示界面便于调试） |
| `USER_AGENT` | "Mozilla/5.0 (...)" | 自定义请求头 User-Agent，用于反检测 |
| `VIEWPORT_WIDTH` | 1400 | 操作浏览器时的视口宽度 |
| `VIEWPORT_HEIGHT`| 900 | 操作浏览器时的视口高度 |
| `DELAY_AFTER_OPEN_SEARCH`| "2.5,4.0" | 打开搜索页后的随机等待时间(秒)，下限与上限使用英文逗号分隔 |
| `DELAY_BETWEEN_PAGES`| "1.8,3.0" | 点击下一页/翻页过程的随机等待时间(秒)，Web 任务设置中可按任务覆盖 |
| `DELAY_RETRY_RELOAD`| "4.0,6.0" | 触发异常重试或页面重载时的随机等待冷却时间(秒) |
| `OUTPUT_DIR` | "output" | 数据抓取结果的输出和保存目录 |
| `CRAWLED_LINKS_DIR` | "output/crawled_links" | 已访问过的岗位详情链接文本存储目录；所有任务会合并写入同一个 `all_links.txt` |

## 运行项目

完成环境搭建和 `.env` 配置修改后，使用以下命令启动爬虫：

```bash
python main.py
```

也可以用命令行参数临时覆盖 `.env` 配置：

```bash
python main.py --keywords Java开发 --regions 北京,上海 --max-pages 3 --headless
```

抓取 51job：

```bash
python main.py --platform 51job --keywords Java开发 --regions 上海 --max-pages 1 --headless
```

51job 详情页需要真实登录时，先初始化一个专用浏览器 Profile：

```bash
python main.py --platform 51job --login-51job --auth-wait-seconds 180
```

在弹出的浏览器中用手机号和短信验证码完成真实登录。程序会把登录状态保存在 `auth/51job_profile`。之后正常抓取 51job 时会复用这个 Profile，并尝试补全详情页中的 `工作内容` 和 `任职要求`：

```bash
python main.py --platform 51job --keywords Java开发 --regions 上海 --max-pages 1 --headless
```

常用参数：

- `--platform`：招聘平台，支持 `zhaopin` 和 `51job`。
- `--login-51job`：打开真实浏览器，等待人工用手机号/短信验证码登录 51job，并保存 Profile。
- `--auth-wait-seconds`：人工登录等待秒数。
- `--user-data-dir`：51job 登录 Profile 保存/读取目录。
- `--keywords`：本次运行的岗位关键词，多个值用英文逗号分隔。
- `--regions`：本次运行的城市/地区，多个值用英文逗号分隔。
- `--max-pages`：每个“关键词 + 城市”最多抓取页数。
- `--headless` / `--headed`：分别表示隐藏或显示浏览器窗口。
- `--output-dir`：本次运行的输出目录。
- `--max-empty-retries`：列表页解析为空时的最大重试次数。
- `--max-detail-retries`：详情页抓取失败时的最大重试次数。

## Web 控制台

启动本地 Web 程序：

```bash
python web_app.py
```

默认访问地址：

- 首页/任务设置：`http://127.0.0.1:5000/`
- 进行中任务：`http://127.0.0.1:5000/tasks`
- 历史任务：`http://127.0.0.1:5000/history`
- 任务详情：`http://127.0.0.1:5000/tasks/<任务ID>` 或 `http://127.0.0.1:5000/history/<任务ID>`

Web 任务设置说明：

- 城市/地区可以留空；留空时不按城市过滤，抓到哪个城市都可以进入结果。
- `翻页等待秒` 对应 `.env` 中的 `DELAY_BETWEEN_PAGES`，格式为 `下限,上限`，例如 `1.8,3.0`。
- Web 任务信息会保存到 `web_tasks/<任务ID>/task.json`，重新启动 Web 程序后自动读取历史任务。
- Web 任务结果仍保存在各自任务目录下，例如 `web_tasks/<任务ID>/output/`。

## 数据输出

爬行结束或进行中时，抓取到的数据将会默认自动保存到项目的 `OUTPUT_DIR` (默认为 `output/` 文件夹) 目录下。
您可以在此文件夹内获取结构化后的职位招聘列表数据。

Web 任务的输出文件保存在 `web_tasks/<任务ID>/output/`，可在任务详情页预览和下载。

所有已访问过的岗位详情链接统一保存在 `CRAWLED_LINKS_DIR/all_links.txt`。后续无论创建多少任务、重启多少次程序，已记录的链接都会被跳过；列表内去重也会优先按岗位链接判断，只有缺少链接时才回退到公司、岗位和城市组合。

当前输出表头与“岗位信息表.xlsx”的 Sheet1 保持一致：

`序号 | 招聘平台 | 岗位类别/大类 | 岗位名称 | 公司名称 | 公司规模 | 所在省份 | 城市 | 详细地址 | 学历要求 | 经验要求 | 薪资范围 | 福利标签 | 工作内容 | 任职要求 | 岗位链接 | 投递起始时间 | 投递截止时间 | 备注`

网页中无法提取到的字段会统一填充为 `/`。
