# 智联招聘岗位采集工具

这个项目现在的主流程是：`python main.py` 读取 `.env`，启动本机 GoLogin 下载的 Orbita 浏览器，通过 CDP 接管浏览器页面，人工处理登录和验证码，然后抓取智联招聘列表页与详情页，最后增量写入 Excel。

重点先说清楚：当前跑通的主流程不是 Playwright 自带 Chromium 无头浏览器。代码里仍然需要 `playwright` 这个 Python 包来连接 CDP，但浏览器本体走的是 GoLogin/Orbita，也就是 `.env` 里的：

```env
BROWSER_BACKEND=orbita_cdp
HEADLESS=false
MANUAL_AUTH=true
```

不要把 `HEADLESS` 改成 `true`。智联招聘详情页抓取过程中经常会弹验证码，第一次进入详情页时尤其容易出现，后面也可能隔一阵弹一次。浏览器必须显示出来，看到验证码就手动点掉，程序会继续往后跑。

## 当前主流程

1. 安装 Python 依赖。
2. 安装 GoLogin 桌面端。
3. 登录 GoLogin，并等待 GoLogin 把 Orbita 浏览器下载完成。
4. 配置 `.env` 为 `orbita_cdp`、可见浏览器、开启详情页补全。
5. 先运行一次智联招聘登录态初始化，把登录状态保存到 `auth/zhaopin_profile`。
6. 正式运行 `python main.py`。
7. 运行过程中如果弹验证码，手动处理。
8. 结果写入 `output/关键词.xlsx`。

## 安装依赖

进入项目目录：

```powershell
cd D:\-647-main
```

安装 Python 依赖：

```powershell
pip install -r requirements.txt
```

这里不需要再按旧 README 去执行 `playwright install chromium` 作为主流程。当前主流程使用本机 Orbita 浏览器；`playwright` 只是连接 Orbita 的 CDP 控制层。

## 安装 GoLogin 和 Orbita

官方入口：

- GoLogin 下载页：https://gologin.com/download/
- GoLogin 安装文档：https://gologin.com/docs/getting-started/setup/supported-platforms-installation

Windows 上也可以用：

```powershell
winget install --id Gologin.Gologin --exact
```

安装完成后：

1. 打开 GoLogin。
2. 登录你的 GoLogin 账号。
3. 在 GoLogin 里创建或打开一个浏览器 Profile。
4. 等它自动下载 Orbita 浏览器。
5. 确认 Orbita 能正常打开。

程序会自动查找常见路径，例如：

```text
C:\Users\<你的用户名>\.gologin\browser\orbita-browser-xxx\chrome.exe
```

如果 Orbita 还没下载完，程序会找不到浏览器。这种情况先回到 GoLogin 里等待下载完成，或者手动运行一次 GoLogin Profile。

## 配置 .env

第一次使用时复制配置文件：

```powershell
copy .env.example .env
```

当前主流程推荐配置如下：

```env
KEYWORDS=教育培训

# 留空表示不限地区，也就是不做城市过滤
REGIONS=
DEFAULT_REGIONS=

MAX_PAGES_PER_REGION=5

HEADLESS=false
MANUAL_AUTH=true
BROWSER_BACKEND=orbita_cdp

LOGIN_ZHAOPIN=false
ZHAOPIN_USER_DATA_DIR=auth/zhaopin_profile
AUTH_WAIT_SECONDS=300

SKIP_DETAIL_FETCH=false
REFETCH_CRAWLED_DETAILS=false

OUTPUT_DIR=output
CRAWLED_LINKS_DIR=output/crawled_links
```

关键配置说明：

| 配置 | 建议值 | 说明 |
| --- | --- | --- |
| `KEYWORDS` | `教育培训` | 搜索关键词，多个关键词用英文逗号分隔 |
| `REGIONS` | 留空 | 留空就是不限地区，抓到哪个城市都保留 |
| `DEFAULT_REGIONS` | 留空 | 保持留空，避免空地区又被默认城市覆盖 |
| `MAX_PAGES_PER_REGION` | 按需要 | 每个关键词最多翻多少页 |
| `HEADLESS` | `false` | 必须显示浏览器，方便处理验证码 |
| `MANUAL_AUTH` | `true` | 遇到登录/验证时等待人工处理 |
| `BROWSER_BACKEND` | `orbita_cdp` | 当前主流程，启动 Orbita 并通过 CDP 连接 |
| `SKIP_DETAIL_FETCH` | `false` | false 才会点进详情页补全工作内容/任职要求 |
| `REFETCH_CRAWLED_DETAILS` | `false` | 正常跑保持 false；如果以前只抓了列表、详情为空，临时改 true 回补 |
| `ZHAOPIN_USER_DATA_DIR` | `auth/zhaopin_profile` | 智联登录态保存目录 |
| `GOLOGIN_TOKEN` | 留空 | `orbita_cdp` 主流程不需要填 |
| `GOLOGIN_PROFILE_ID` | 留空 | `orbita_cdp` 主流程不需要填 |

## 保存智联登录态

正式抓取前，先用 Orbita 打开一次智联并完成登录：

```powershell
python main.py --login-zhaopin --auth-wait-seconds 300
```

程序会打开 Orbita 窗口。你需要在这个窗口里：

1. 登录智联招聘。
2. 如果出现验证码，手动完成。
3. 等命令行倒计时结束。

登录态会保存到：

```text
D:\-647-main\auth\zhaopin_profile
```

后续正常运行会复用这个目录。不要随便删除 `auth/zhaopin_profile`，否则要重新登录。

## 正式运行

确认 `.env` 配好后运行：

```powershell
python main.py
```

正常日志会类似：

```text
Orbita CDP：独立启动 + Playwright 远程连接（无自动化标识条）
稳妥模式（跳过详情页）：False
开始抓取智联招聘：关键词=教育培训，地区=不限地区
正在分析详情链接 (1/20)：...
第 1 页：解析 20 条，未进行地区过滤，详情补全 20 条，新增 ...
```

如果看到：

```text
已启用列表页导出模式：跳过详情页补全
```

说明 `SKIP_DETAIL_FETCH` 仍然是 `true`，或者当前命令没有读到你改过的 `.env`。

## 验证码和人工处理

这个版本不是全自动无人值守验证码版本。正确姿势是：

- 浏览器窗口必须显示出来。
- 第一次进入详情页时，可能会弹验证码。
- 后面跑多页时，也可能隔一段时间再弹。
- 弹出来就手动点，点完程序会继续。
- 不要开无头模式，不要把 `HEADLESS` 改成 `true`。

如果长时间没有继续输出，可以看 Orbita 窗口是不是停在验证页、登录页或异常页。

## 输出和保险机制

主结果文件在：

```text
output\关键词.xlsx
```

例如：

```text
output\教育培训.xlsx
```

写入逻辑是增量合并：

- 优先按 `岗位链接` 去重。
- 如果没有链接，再按 `公司名称 + 岗位名称 + 城市` 去重。
- 已存在岗位如果补到了新的 `工作内容/任职要求`，会更新旧记录。

当前版本已经加了 Excel 保险机制：

1. 写 Excel 时会先写临时文件，再替换主文件。
2. 如果 `output\教育培训.xlsx` 被 Excel/WPS 占用，程序不会直接崩掉，会另存为：

```text
output\教育培训_recovered_YYYYMMDD_HHMMSS.xlsx
```

3. CLI 抓取过程中还会持续写断点备份：

```text
output\checkpoints\关键词_YYYYMMDD_HHMMSS.jsonl
```

建议：运行程序时尽量关掉正在打开的结果 Excel。保险机制能兜底，但最稳的方式还是不要让 WPS/Excel 占用目标文件。

## 长时间挂机脚本

主流程仍然是：

```powershell
python main.py
```

如果你想长时间跑，可以用临时挂机脚本：

```powershell
python long_run.py
```

它会循环启动 `main.py`，每轮完成后随机冷却一段时间，异常退出也会等待后重试。手动结束用 `Ctrl+C`。

常用测试：

```powershell
python long_run.py --once
python long_run.py --dry-run
```

这个脚本只是为了长时间挂机更稳，不是必须流程。

## 旧流程和可选能力

仓库里还保留了一些旧后端和可选入口，例如：

- `BROWSER_BACKEND=playwright`
- `BROWSER_BACKEND=scrapling`
- `BROWSER_BACKEND=gologin`
- `BROWSER_BACKEND=adspower`
- `python web_app.py`
- 51job 相关流程

这些不是当前已经跑通并推荐使用的智联主流程。当前主流程请优先按本文档配置：

```env
BROWSER_BACKEND=orbita_cdp
HEADLESS=false
MANUAL_AUTH=true
SKIP_DETAIL_FETCH=false
```

## 常见问题

### 1. 地区留空为什么是不限地区？

现在 CLI 已经按这个逻辑处理：`REGIONS=` 且 `DEFAULT_REGIONS=` 时，会把地区作为一个空值任务执行，显示为 `不限地区`，不做城市过滤。

### 2. 为什么跑了很多条，最后新增很少？

页面日志里的“新增”是本轮抓取时的去重累计；最终写 Excel 时还会和旧 Excel 合并去重。如果旧文件里已经有相同岗位链接，就不会算新增，只会在字段变化时算更新。

### 3. 为什么没有点详情页？

检查：

```env
SKIP_DETAIL_FETCH=false
HEADLESS=false
MANUAL_AUTH=true
```

看到“正在分析详情链接”才说明详情页补全正在执行。

### 4. 以前只抓了列表，详情字段都是空，怎么回补？

临时改：

```env
REFETCH_CRAWLED_DETAILS=true
```

跑完回补后建议改回：

```env
REFETCH_CRAWLED_DETAILS=false
```

### 5. 报 `PermissionError: output\教育培训.xlsx`

说明 Excel/WPS 正在占用文件。关掉表格后重跑。当前版本也会自动另存 recovered 文件，避免整轮白跑。

### 6. 找不到 Orbita 浏览器？

先打开 GoLogin，登录账号，并启动一个 Profile，让 GoLogin 把 Orbita 下载完整。下载完成后再运行 `python main.py`。

## 主要文件

| 文件 | 说明 |
| --- | --- |
| `main.py` | CLI 主入口 |
| `long_run.py` | 长时间挂机包装脚本 |
| `job_crawler/zhaopin.py` | 智联招聘列表页和详情页抓取 |
| `job_crawler/orbita_cdp_backend.py` | 启动 Orbita，并通过 CDP 连接 |
| `job_crawler/output.py` | Excel 增量合并、格式化、recovered 保险写入 |
| `job_crawler/crawled_links.py` | 已抓详情链接记录 |
| `.env` | 本机运行配置，不提交 |
| `.env.example` | 配置模板 |

## 输出字段

Excel 表头为：

```text
序号 | 招聘平台 | 岗位类型一级 | 岗位类型二级 | 岗位名称 | 岗位类型企业/公务员/事业单位/军队文职 | 公司名称 | 公司规模 | 所在省份 | 城市 | 详细地址 | 学历要求 | 经验要求 | 薪资范围 | 福利标签 | 工作内容 | 任职要求 | 岗位链接 | 发布时间 | 投递起始时间 | 投递截止时间 | 证书要求 | 备注
```

网页中拿不到的字段会填 `/`。
