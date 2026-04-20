# 招聘数据爬虫 (ZhaoPin Crawler)

这是一个用于自动化抓取招聘平台职位数据的爬虫工具。支持根据自定义关键词和城市/地区进行定向抓取，并可以灵活配置浏览器行为及抓取频率。

## 核心功能

- **多地点多关键词**：支持配置多个搜索关键词和目标城市，自动跨维度进行数据采集。
- **动态延时防封**：支持自定义各类点击、翻页、加载操作的随机等待延时，模拟真人操作。
- **Headless 模式**：支持无头浏览器后台静默运行。
- **自动错误重试**：遇到页面加载失败或数据为空时，支持自动刷新重试机制。

## 环境要求

- Python 3.8+
- [python-dotenv](https://pypi.org/project/python-dotenv/) (用于读取本地 `.env` 配置)
- 其他相关的爬虫依赖（可能包括 `playwright`, `selenium` 或 `requests` 等具体视项目代码而定）

## 安装步骤

1. **克隆/下载代码到本地存储库**
   ```bash
   cd zhaopin_crawler
   ```

2. **安装依赖**
   建议使用虚拟环境进行安装：
   ```bash
   pip install python-dotenv playwright
   # 安装其他必要依赖，如 requirements.txt 存在：
   # pip install -r requirements.txt
   ```

3. **安装 Playwright 浏览器内核**
   本项目依赖 Playwright 来驱动浏览器抓取数据。在第一次运行前，您必须下载相关的浏览器二进制文件（由于本项目默认使用 Chrome/Chromium，建议仅安装 Chromium 以节省时间）：
   ```bash
   playwright install chromium
   ```

## 配置说明

项目根目录下需要存在 `.env` 配置文件（如果不存在，请复制一份预设的格式）。

您可以通过修改 `.env` 的以下这些变量来定制抓取行为：

| 配置变量名 | 默认值示例 | 说明 |
| :--- | :--- | :--- |
| `KEYWORDS` | "大模型算法工程师,数据分析师,软件开发" | 想要搜索的职位关键词，用英文逗号分隔 |
| `REGIONS` | "北京,上海,深圳" | 指定抓取的城市/地区列表，用英文逗号分隔 |
| `DEFAULT_REGIONS` | "北京,上海,广州,深圳,杭州" | 当 `REGIONS` 读取失败或为空时的备用城市列表 |
| `MAX_PAGES_PER_REGION`| 5 | 每个地区-关键词组合下最多抓取的页数上限 |
| `MAX_EMPTY_PAGE_RETRIES`| 2 | 若单次列表加载空白或失败，最大刷新重试次数 |
| `HEADLESS` | true | 浏览器是否启用无头/静默模式（true 隐藏界面，false 显示界面便于调试） |
| `USER_AGENT` | "Mozilla/5.0 (...)" | 自定义请求头 User-Agent，用于反检测 |
| `VIEWPORT_WIDTH` | 1400 | 操作浏览器时的视口宽度 |
| `VIEWPORT_HEIGHT`| 900 | 操作浏览器时的视口高度 |
| `DELAY_AFTER_OPEN_SEARCH`| "2.5,4.0" | 打开搜索页后的随机等待时间(秒)，下限与上限使用英文逗号分隔 |
| `DELAY_BETWEEN_PAGES`| "1.8,3.0" | 点击下一页/翻页过程的随机等待时间(秒) |
| `DELAY_RETRY_RELOAD`| "4.0,6.0" | 触发异常重试或页面重载时的随机等待冷却时间(秒) |
| `OUTPUT_DIR` | "output" | 数据抓取结果的输出和保存目录 |

## 运行项目

完成环境搭建和 `.env` 配置修改后，使用以下命令启动爬虫：

```bash
python main.py
```

## 数据输出

爬行结束或进行中时，抓取到的数据将会默认自动保存到项目的 `OUTPUT_DIR` (默认为 `output/` 文件夹) 目录下。
您可以在此文件夹内获取结构化后的职位招聘列表数据。
