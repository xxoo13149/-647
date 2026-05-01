import asyncio
import json
import urllib.parse
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .constants import FIFTYONE_CITY_CODE_MAP, FIFTYONE_SEARCH_URL
from .crawled_links import CrawledLinkStore
from .utils import *  # noqa: F403

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except ImportError:
    PlaywrightTimeoutError = TimeoutError
    async_playwright = None


def extract_51job_detail_summary_from_html(html: str) -> str:
    """从 51job 详情页提取职位描述正文。"""
    soup = BeautifulSoup(html, "html.parser")
    selector_candidates = [
        "div.bmsg.job_msg.inbox",
        "div.job_msg",
        "div.job-detail",
        "div.jobDetail",
        "div.tCompany_main",
        "div[class*='job_msg']",
        "div[class*='job-detail']",
    ]
    for selector in selector_candidates:
        candidates = []
        for node in soup.select(selector):
            text = clean_multiline_text(node.get_text("\n", strip=True))
            if looks_like_job_summary_text(text):
                candidates.append(text)
        if candidates:
            return max(candidates, key=len)

    body_text = clean_multiline_text(soup.get_text("\n", strip=True))
    for marker in ["职位信息", "职位描述", "岗位职责", "工作职责", "任职要求"]:
        idx = body_text.find(marker)
        if idx < 0:
            continue
        snippet = clean_multiline_text(body_text[idx : idx + 6000])
        if looks_like_job_summary_text(snippet):
            return snippet
    return ""


def build_51job_detail_url(job_id: str) -> str:
    """根据 51job 列表页 jobId 构造详情页链接。"""
    text = clean_text(job_id)
    if not text:
        return ""
    return f"https://jobs.51job.com/all/{urllib.parse.quote(text)}.html"


def build_51job_search_url(keyword: str, city_name: str) -> str:
    """构造 51job 搜索 URL。常用城市使用 jobArea 编码。"""
    normalized_city = normalize_city_name(city_name)
    params = {
        "keyword": keyword,
        "searchType": "2",
        "sortType": "0",
    }
    city_code = FIFTYONE_CITY_CODE_MAP.get(normalized_city)
    if city_code:
        params["jobArea"] = city_code
    return f"{FIFTYONE_SEARCH_URL}?{urllib.parse.urlencode(params)}"


def parse_51job_jobs_from_dom(html: str) -> list[dict[str, Any]]:
    """从 51job 搜索结果页 DOM 解析岗位列表。"""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.joblist-item")
    jobs: list[dict[str, Any]] = []

    for card in cards:
        job_node = card.select_one("div.joblist-item-job")
        sensors_data = {}
        if job_node and job_node.get("sensorsdata"):
            try:
                sensors_data = json.loads(job_node.get("sensorsdata", "{}"))
            except json.JSONDecodeError:
                sensors_data = {}

        job_name = clean_text(
            sensors_data.get("jobTitle")
            or (card.select_one(".jname").get_text() if card.select_one(".jname") else "")
        )
        salary = clean_text(
            sensors_data.get("jobSalary")
            or (card.select_one(".sal").get_text() if card.select_one(".sal") else "")
        )
        area = clean_text(sensors_data.get("jobArea", ""))
        if not area:
            area_node = card.select_one(".area")
            area = clean_text(area_node.get_text(" ", strip=True)) if area_node else ""

        company_node = card.select_one("a.comp .cname")
        company_name = clean_text(company_node.get_text()) if company_node else ""
        company_link_node = card.select_one("a.comp")

        company_meta = [
            clean_text(node.get_text())
            for node in card.select("a.comp .bc .dc")
            if clean_text(node.get_text())
        ]
        company_size = ""
        for value in reversed(company_meta):
            if any(token in value for token in ["人", "少于", "以上"]):
                company_size = value
                break

        tags = [
            clean_text(node.get_text())
            for node in card.select(".joblist-item-tags .tag")
            if clean_text(node.get_text())
        ]

        job_id = clean_text(str(sensors_data.get("jobId", "")))
        detail_url = build_51job_detail_url(job_id)
        if not detail_url and company_link_node:
            detail_url = normalize_absolute_url(company_link_node.get("href", ""), "https://jobs.51job.com")

        city = normalize_city_name(area)
        job_time = normalize_publish_time_text(sensors_data.get("jobTime", ""))

        if not job_name and not company_name:
            continue

        jobs.append(
            {
                "招聘平台": "51job",
                "岗位类别/大类": "",
                "岗位名称": job_name or "未知岗位",
                "公司名称": company_name or "未知单位",
                "公司规模": company_size,
                "所在省份": infer_province(city),
                "城市": city,
                "详细地址": area,
                "学历要求": clean_text(sensors_data.get("jobDegree", "")),
                "经验要求": clean_text(sensors_data.get("jobYear", "")),
                "薪资范围": salary,
                "福利标签": format_tags(tags),
                "工作内容": "",
                "任职要求": "",
                "岗位链接": detail_url,
                "投递起始时间": job_time,
                "投递截止时间": "",
                "备注": "；".join([x for x in ["公司信息：" + " / ".join(company_meta) if company_meta else ""] if x]),
                "__工作城市": area,
                "__详情链接": detail_url,
                "__岗位摘要": "",
            }
        )

    return jobs


def looks_like_51job_verification_page(html: str) -> bool:
    """判断 51job 是否进入滑块验证页。"""
    text = clean_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    return any(token in text for token in ["访问验证", "滑动滑块", "拖动到最右边", "请按住滑块"])


async def search_51job_keyword(
    page,
    keyword: str,
    city_name: str,
    settings: dict[str, Any],
) -> None:
    """打开 51job 搜索页并触发列表加载。"""
    direct_url = build_51job_search_url(keyword, city_name)
    try:
        await page.goto(direct_url, wait_until="domcontentloaded", timeout=90000)
    except Exception as exc:
        print(f"打开 51job 搜索页异常：{exc}，正在重试...")
        await human_sleep(*settings["delays"]["retry_reload"])
        await page.goto(direct_url, wait_until="domcontentloaded", timeout=90000)

    await human_sleep(*settings["delays"]["after_open_search"])
    if await page.locator(".joblist-item").count() > 0:
        return

    html = await page.content()
    visible_input_count = await page.locator("input:visible").count()
    if settings["manual_auth"] and (
        looks_like_51job_verification_page(html) or visible_input_count == 0
    ):
        await wait_for_manual_51job_auth(
            context=page.context,
            page=page,
            settings=settings,
            reason="搜索页需要登录/验证后才显示搜索框",
        )
        try:
            await page.goto(direct_url, wait_until="domcontentloaded", timeout=90000)
            await human_sleep(*settings["delays"]["after_open_search"])
            if await page.locator(".joblist-item").count() > 0:
                return
        except Exception:
            pass

    try:
        search_input = page.locator("input:visible").first
        await search_input.fill(keyword)
        await search_input.press("Enter")
    except Exception as exc:
        print(f"51job 输入关键词失败：{exc}")
        return

    await human_sleep(*settings["delays"]["after_open_search"])

    # 51job 的城市筛选偶尔会保留默认城市；这里尽量点击目标城市，再由后续解析做严格过滤。
    if city_name:
        try:
            await page.get_by_text(city_name, exact=True).first.click(timeout=5000)
            await human_sleep(*settings["delays"]["after_open_search"])
        except Exception:
            pass


async def wait_for_manual_51job_auth(context, page, settings: dict[str, Any], reason: str) -> None:
    """等待用户人工完成 51job 登录。持久化 Profile 会自动保存会话。"""
    wait_seconds = int(settings["auth_wait_seconds"])
    print(
        f"51job 需要人工处理：{reason}。请在打开的浏览器中使用手机号/短信验证码登录，"
        f"程序将在 {wait_seconds} 秒后继续。"
    )
    await asyncio.sleep(wait_seconds)
    print(f"51job 真实浏览器会话已保存在：{settings['user_data_dir']}")

    try:
        await page.reload(wait_until="domcontentloaded", timeout=90000)
        await human_sleep(*settings["delays"]["after_open_search"])
    except Exception:
        pass


async def login_51job_profile(settings: dict[str, Any]) -> None:
    """打开持久化浏览器 Profile，让用户真实登录 51job。"""
    if async_playwright is None:
        raise RuntimeError(
            "缺少 Playwright 依赖，请先运行：pip install -r requirements.txt && playwright install chromium"
        )

    user_data_dir = Path(settings["user_data_dir"])
    user_data_dir.mkdir(parents=True, exist_ok=True)
    wait_seconds = int(settings["auth_wait_seconds"])

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_https_errors=True,
            user_agent=settings["user_agent"],
            viewport=settings["viewport"],
            locale="zh-CN",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://we.51job.com/pc/login", wait_until="domcontentloaded", timeout=90000)
        print(
            f"已打开 51job 登录页。请用手机号和短信验证码完成真实登录，"
            f"程序将在 {wait_seconds} 秒后保存 Profile 并退出。"
        )
        await asyncio.sleep(wait_seconds)
        print(f"51job 登录 Profile 已保存：{user_data_dir}")
        await context.close()


async def fetch_51job_summary_from_detail_page(
    detail_page,
    context,
    detail_url: str,
    settings: dict[str, Any],
) -> str:
    """访问 51job 详情页，尽量提取工作内容/任职要求正文。"""
    if not detail_url:
        return ""

    max_retries = settings["max_detail_retries"]
    for attempt in range(1, max_retries + 2):
        try:
            await human_sleep(*settings["delays"]["before_open_detail"])
            await detail_page.goto(
                detail_url,
                wait_until="domcontentloaded",
                timeout=settings["detail_page_timeout_ms"],
            )
            await human_sleep(*settings["delays"]["after_open_detail"])
            html = await detail_page.content()

            if looks_like_51job_verification_page(html):
                if settings["manual_auth"]:
                    await wait_for_manual_51job_auth(
                        context=context,
                        page=detail_page,
                        settings=settings,
                        reason="详情页触发验证",
                    )
                    html = await detail_page.content()
                else:
                    print(f"51job 详情页触发验证，已跳过：{detail_url}")
                    return ""

            summary = extract_51job_detail_summary_from_html(html)
            if summary:
                return summary

            if attempt > max_retries:
                return ""
            await human_sleep(*settings["delays"]["detail_retry"])
        except Exception as exc:
            if attempt > max_retries:
                print(f"51job 详情页抓取失败，已放弃：{detail_url}，原因：{exc}")
                return ""
            await human_sleep(*settings["delays"]["detail_retry"])

    return ""


async def enrich_51job_jobs_with_detail_summaries(
    context,
    jobs: list[dict[str, Any]],
    settings: dict[str, Any],
    crawled_link_store: CrawledLinkStore | None = None,
) -> int:
    """补全 51job 岗位详情。详情页可能需要人工验证。"""
    if not jobs:
        return 0

    updated_count = 0
    detail_page = await context.new_page()
    try:
        for item in jobs:
            detail_url = clean_text(str(item.get("__详情链接", "")))
            if not detail_url:
                continue
            if crawled_link_store is not None and crawled_link_store.contains(detail_url):
                continue
            summary = await fetch_51job_summary_from_detail_page(
                detail_page=detail_page,
                context=context,
                detail_url=detail_url,
                settings=settings,
            )
            if crawled_link_store is not None:
                crawled_link_store.add(detail_url)
                crawled_link_store.save()
            if not summary:
                continue
            work_content, requirement = split_job_summary(summary)
            changed = False
            if work_content and item.get("工作内容") != work_content:
                item["工作内容"] = work_content
                changed = True
            if requirement and item.get("任职要求") != requirement:
                item["任职要求"] = requirement
                changed = True
            if changed:
                updated_count += 1
            await human_sleep(*settings["delays"]["between_details"])
    finally:
        await detail_page.close()
    return updated_count


async def crawl_51job(
    keyword: str,
    city: str,
    settings: dict[str, Any],
    crawled_link_store: CrawledLinkStore | None = None,
) -> list[dict]:
    """爬取 51job 搜索列表数据并返回岗位记录。"""
    if async_playwright is None:
        raise RuntimeError(
            "缺少 Playwright 依赖，请先运行：pip install -r requirements.txt && playwright install chromium"
        )

    jobs: list[dict[str, Any]] = []
    seen = set()

    async with async_playwright() as p:
        user_data_dir = Path(settings["user_data_dir"])
        profile_ready = user_data_dir.exists() and any(user_data_dir.rglob("Cookies"))
        user_data_dir.mkdir(parents=True, exist_ok=True)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=settings["headless"],
            args=["--disable-blink-features=AutomationControlled"],
            ignore_https_errors=True,
            user_agent=settings["user_agent"],
            viewport=settings["viewport"],
            locale="zh-CN",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            print(f"开始抓取 51job：关键词={keyword}，地区={city}")
            await search_51job_keyword(
                page=page,
                keyword=keyword,
                city_name=city,
                settings=settings,
            )
            html = await page.content()
            if looks_like_51job_verification_page(html) and settings["manual_auth"]:
                await wait_for_manual_51job_auth(
                    context=context,
                    page=page,
                    settings=settings,
                    reason="搜索页触发验证",
                )
                await search_51job_keyword(
                    page=page,
                    keyword=keyword,
                    city_name=city,
                    settings=settings,
                )

            current_page = 1
            while current_page <= settings["max_pages_per_region"]:
                await human_sleep(*settings["delays"]["between_pages"])
                html = await page.content()
                raw_page_jobs = parse_51job_jobs_from_dom(html)

                if not raw_page_jobs:
                    print(f"51job 第 {current_page} 页未解析到岗位，停止当前搜索。")
                    break

                page_jobs = [
                    item
                    for item in raw_page_jobs
                    if is_job_in_target_city(str(item.get("__工作城市", "")), city)
                ]
                filtered_count = len(raw_page_jobs) - len(page_jobs)

                new_count = 0
                for item in page_jobs:
                    if not clean_text(str(item.get("岗位类别/大类", ""))):
                        item["岗位类别/大类"] = keyword
                    key = (
                        item["公司名称"],
                        item["岗位名称"],
                        item.get("城市", ""),
                        item.get("岗位链接", ""),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    jobs.append(item)
                    new_count += 1

                if profile_ready:
                    detail_updated = await enrich_51job_jobs_with_detail_summaries(
                        context=context,
                        jobs=page_jobs,
                        settings=settings,
                        crawled_link_store=crawled_link_store,
                    )
                else:
                    detail_updated = 0

                print(
                    f"51job 第 {current_page} 页：解析 {len(raw_page_jobs)} 条，"
                    f"过滤非目标地区 {filtered_count} 条，详情补全 {detail_updated} 条，"
                    f"新增 {new_count} 条（累计 {len(jobs)} 条）"
                )

                if current_page >= settings["max_pages_per_region"]:
                    break

                next_buttons = page.get_by_text("下一页", exact=True)
                if await next_buttons.count() == 0:
                    break
                try:
                    await next_buttons.first.click(timeout=5000)
                except Exception:
                    break
                await human_sleep(*settings["delays"]["after_next_page"])
                current_page += 1

        finally:
            await context.close()

    return jobs
