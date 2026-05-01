import asyncio
import datetime as dt
import json
import random
import re
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup

from .constants import DEFAULT_CONFIG, ZHAOPIN_SEARCH_URL
from .crawled_links import CrawledLinkStore
from .utils import *  # noqa: F403

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except ImportError:
    PlaywrightTimeoutError = TimeoutError
    async_playwright = None


def build_detail_address_from_state_item(item: dict[str, Any]) -> str:
    """从列表页状态中提取或组合尽量完整的地址信息。"""
    explicit_address = first_text_from_item(
        item,
        [
            "address",
            "workAddress",
            "geoAddress",
            "securityAddressLabel",
            "addressLabel",
        ],
    )
    if explicit_address:
        return explicit_address

    parts = [
        first_text_from_item(item, ["workCity"]),
        first_text_from_item(item, ["cityDistrict"]),
        first_text_from_item(item, ["tradingArea", "businessArea"]),
        first_text_from_item(item, ["streetName"]),
    ]
    cleaned = []
    seen = set()
    for part in parts:
        text = clean_text(part)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return "·".join(cleaned)


def extract_initial_state(html: str) -> dict:
    """从页面 HTML 中提取 __INITIAL_STATE__ JSON。"""
    match = re.search(r"__INITIAL_STATE__=(\{.*?\})</script>", html, re.S)
    if not match:
        return {}

    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def build_search_url(keyword: str, city_name: str, page: int = 1) -> str:
    """构造智联搜索 URL，使用城市名称直接搜索（无需 code）。"""
    params = {"jl": city_name, "kw": keyword, "p": str(page)}
    return f"{ZHAOPIN_SEARCH_URL}?{urllib.parse.urlencode(params)}"


def extract_detail_url_from_state_item(item: dict[str, Any]) -> str:
    """从列表 state 项中提取职位详情页链接。"""
    key_candidates = [
        "positionURL",
        "positionUrl",
        "positionurl",
        "jobUrl",
        "jobURL",
        "joburl",
        "positionDetailUrl",
        "detailUrl",
        "positionHref",
    ]

    for key in key_candidates:
        value = item.get(key)
        if not isinstance(value, str):
            continue
        normalized = normalize_absolute_url(value)
        if normalized.startswith("http"):
            return normalized

    return ""


def build_ability_desc(
    salary: str,
    location: str,
    experience: str,
    education: str,
    tags: list[str],
    summary: str,
) -> str:
    """拼接输出字段中的“能力描述”。"""
    parts = []
    if salary:
        parts.append(f"薪资：{salary}")
    if location:
        parts.append(f"地点：{location}")
    if experience:
        parts.append(f"经验：{experience}")
    if education:
        parts.append(f"学历：{education}")
    if tags:
        parts.append("技能标签：" + " / ".join(tags[:10]))
    if summary:
        summary = clean_multiline_text(summary)
        parts.append("岗位摘要：" + summary)

    if not parts:
        return "（暂无能力描述）"
    return "；".join(parts)


def extract_job_summary_from_detail_state(state: dict[str, Any]) -> str:
    """从详情页 state 中提取较完整的岗位描述。"""
    best = ""
    key_hints = (
        "summary",
        "description",
        "detail",
        "content",
        "responsibility",
        "requirement",
        "duty",
        "jobdesc",
        "positiondesc",
    )

    def walk(node: Any, key_hint: str = "") -> None:
        nonlocal best

        if isinstance(node, str):
            candidate = clean_multiline_text(node)
            if not candidate:
                return
            key_lower = key_hint.lower()
            if any(hint in key_lower for hint in key_hints) or looks_like_job_summary_text(candidate):
                if len(candidate) > len(best):
                    best = candidate
            return

        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, str(key))
            return

        if isinstance(node, list):
            for value in node:
                walk(value, key_hint)

    walk(state)
    return best


def extract_job_summary_from_detail_dom(html: str) -> str:
    """从详情页 DOM 中提取岗位描述文本。"""
    soup = BeautifulSoup(html, "html.parser")
    selector_candidates = [
        "div.describtion-card__detail-content",
        "div.jobdetail-box__content",
        "div.describtion__detail-content",
        "div.jobdetail__content",
        "div.job-detail",
        "div.job-description",
        "section.jobdetail__content",
        "div.jobrequirement",
    ]

    candidates = []
    for selector in selector_candidates:
        for node in soup.select(selector):
            text = clean_multiline_text(node.get_text("\n", strip=True))
            if looks_like_job_summary_text(text):
                candidates.append(text)

    if candidates:
        return max(candidates, key=len)

    body_text = clean_multiline_text(soup.get_text("\n", strip=True))
    for marker in ["职位描述", "岗位职责", "任职要求"]:
        idx = body_text.find(marker)
        if idx < 0:
            continue
        snippet = clean_multiline_text(body_text[idx : idx + 5000])
        if looks_like_job_summary_text(snippet):
            return snippet

    return ""


def extract_job_summary_from_detail_html(html: str) -> str:
    """从详情页 HTML 提取尽可能完整的岗位描述。"""
    state = extract_initial_state(html)
    from_state = extract_job_summary_from_detail_state(state) if state else ""
    from_dom = extract_job_summary_from_detail_dom(html)

    # 优先返回页面可见的详情正文，其次再回退到 state 抽取结果。
    if from_dom:
        return from_dom
    return from_state


def merge_summary_into_ability_desc(ability_desc: str, summary: str) -> str:
    """将完整岗位摘要合并回能力描述字段。"""
    summary_text = clean_multiline_text(summary)
    if not summary_text:
        return clean_multiline_text(ability_desc) or "（暂无能力描述）"

    marker = "岗位摘要："
    base_desc = clean_multiline_text(ability_desc)
    if not base_desc or base_desc == "（暂无能力描述）":
        return marker + summary_text

    if marker in base_desc:
        prefix = base_desc.split(marker, 1)[0].rstrip("；")
        if prefix:
            return prefix + "；" + marker + summary_text
        return marker + summary_text

    return base_desc + "；" + marker + summary_text








def extract_latest_publish_time_from_state_item(item: dict[str, Any]) -> str:
    """从 state 岗位项中提取发布时间/更新时间。"""
    key_candidates = [
        "refreshTime",
        "refreshDate",
        "updateTime",
        "updateDate",
        "lastUpdateTime",
        "lastModifyTime",
        "publishTime",
        "publishDate",
        "releaseTime",
        "releaseDate",
        "createTime",
        "createDate",
        "timeDesc",
    ]

    for key in key_candidates:
        extracted = extract_publish_time_from_any(item.get(key))
        if extracted:
            return extracted

    for key, value in item.items():
        if not isinstance(key, str):
            continue
        key_lower = key.lower()
        if "time" not in key_lower and "date" not in key_lower:
            continue
        extracted = extract_publish_time_from_any(value)
        if extracted:
            return extracted

    return ""


def extract_latest_publish_time_from_dom_card(card) -> str:
    """从 DOM 岗位卡片中兜底提取发布时间/更新时间。"""
    selector_candidates = [
        "span.jobinfo__time",
        "span.jobinfo__meta-time",
        "div.jobinfo__meta span",
        "div.joblist-box__item-footer span",
        "div.jobinfo__other-info-item",
    ]

    for selector in selector_candidates:
        for node in card.select(selector):
            text = normalize_publish_time_text(node.get_text())
            if text and looks_like_publish_time(text):
                return text

    card_text = clean_text(card.get_text(" ", strip=True))
    match = re.search(
        r"(\d{4}[./-]\d{1,2}[./-]\d{1,2}(?:\s+\d{1,2}:\d{2})?|"
        r"\d{1,2}[./-]\d{1,2}(?:\s+\d{1,2}:\d{2})?|"
        r"\d+\s*(?:分钟前|小时前|天前)|今天|昨天|刚刚)",
        card_text,
    )
    if match:
        return normalize_publish_time_text(match.group(1))

    return ""






def parse_jobs_from_state(state: dict) -> list[dict]:
    """优先从 __INITIAL_STATE__.positionList 解析岗位。"""
    jobs = []
    for item in state.get("positionList", []):
        company_name = clean_text(item.get("companyName", "")) or "未知单位"
        job_name = clean_text(item.get("name", "")) or "未知岗位"

        salary = clean_text(item.get("salary60", ""))
        work_city = clean_text(item.get("workCity", ""))
        district = clean_text(item.get("cityDistrict", ""))
        location = "·".join([x for x in [work_city, district] if x])

        experience = clean_text(item.get("workingExp", ""))
        education = clean_text(item.get("education", ""))
        company_size = first_text_from_item(
            item,
            ["companySize", "companyScale", "companySizeName", "scale", "size"],
        )
        address = build_detail_address_from_state_item(item)

        tags = [
            clean_text(tag.get("name", ""))
            for tag in item.get("jobSkillTags", [])
            if clean_text(tag.get("name", ""))
        ]
        welfare_tags = []
        for key in ["welfareLabel", "welfareLabels", "welfareTags", "jobBenefits", "benefits"]:
            welfare_tags.extend(extract_tag_names(item.get(key)))

        summary = item.get("jobSummary", "")
        work_content, requirement = split_job_summary(summary)
        detail_url = extract_detail_url_from_state_item(item)
        latest_publish_time = extract_latest_publish_time_from_state_item(item)
        remark_parts = []
        if tags:
            remark_parts.append("技能标签：" + format_tags(tags[:10]))

        jobs.append(
            {
                "招聘平台": "智联招聘",
                "岗位类别/大类": "",
                "岗位名称": job_name,
                "公司名称": company_name,
                "公司规模": company_size,
                "所在省份": infer_province(work_city),
                "城市": normalize_city_name(work_city),
                "详细地址": address or location,
                "学历要求": education,
                "经验要求": experience,
                "薪资范围": salary,
                "福利标签": format_tags(welfare_tags),
                "工作内容": work_content,
                "任职要求": requirement,
                "岗位链接": detail_url,
                "投递起始时间": latest_publish_time,
                "投递截止时间": "",
                "备注": "；".join(remark_parts),
                "__工作城市": work_city,
                "__详情链接": detail_url,
                "__岗位摘要": clean_multiline_text(summary),
            }
        )

    return jobs


def parse_jobs_from_dom(html: str) -> list[dict]:
    """当 __INITIAL_STATE__ 不可用时，使用 DOM 结构兜底解析。"""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.joblist-box__item")
    if not cards:
        return []

    jobs = []
    for card in cards:
        title_tag = card.select_one("a.jobinfo__name")
        company_tag = card.select_one("a.companyinfo__name")
        salary_tag = card.select_one("p.jobinfo__salary")
        detail_url = normalize_absolute_url(title_tag.get("href", "")) if title_tag else ""

        job_name = clean_text(title_tag.get_text()) if title_tag else "未知岗位"
        company_name = clean_text(company_tag.get_text()) if company_tag else "未知单位"
        salary = clean_text(salary_tag.get_text()) if salary_tag else ""
        company_size_tag = card.select_one(
            "div.companyinfo__tag span, div.companyinfo__tag div, span.companyinfo__tag"
        )
        company_size = clean_text(company_size_tag.get_text()) if company_size_tag else ""

        info_items = card.select("div.jobinfo__other-info-item")
        location = ""
        experience = ""
        education = ""
        if info_items:
            first_loc = info_items[0].select_one("span")
            if first_loc:
                location = clean_text(first_loc.get_text())
            if len(info_items) > 1:
                experience = clean_text(info_items[1].get_text())
            if len(info_items) > 2:
                education = clean_text(info_items[2].get_text())

        tags = [
            clean_text(t.get_text())
            for t in card.select("div.jobinfo__tag div.joblist-box__item-tag")
            if clean_text(t.get_text())
        ]

        latest_publish_time = extract_latest_publish_time_from_dom_card(card)
        city = normalize_city_name(location)
        remark_parts = []
        if tags:
            remark_parts.append("标签：" + format_tags(tags[:10]))

        jobs.append(
            {
                "招聘平台": "智联招聘",
                "岗位类别/大类": "",
                "岗位名称": job_name,
                "公司名称": company_name,
                "公司规模": company_size,
                "所在省份": infer_province(city),
                "城市": city,
                "详细地址": location,
                "学历要求": education,
                "经验要求": experience,
                "薪资范围": salary,
                "福利标签": "",
                "工作内容": "",
                "任职要求": "",
                "岗位链接": detail_url,
                "投递起始时间": latest_publish_time,
                "投递截止时间": "",
                "备注": "；".join(remark_parts),
                "__工作城市": location,
                "__详情链接": detail_url,
                "__岗位摘要": "",
            }
        )

    return jobs


def extract_next_page_url(html: str, base_url: str = "https://www.zhaopin.com") -> str:
    """提取“下一页”链接。"""
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a.soupager__btn"):
        text = clean_text(anchor.get_text())
        if text != "下一页":
            continue

        href = anchor.get("href", "")
        if not href:
            return ""
        return urllib.parse.urljoin(base_url, href)

    return ""


def looks_like_verification_page(html: str) -> bool:
    """判断是否进入了验证页（使用强特征，避免误判普通职位页）。"""
    soup = BeautifulSoup(html, "html.parser")

    title_text = clean_text(soup.title.get_text()) if soup.title else ""
    if any(sig in title_text for sig in ["安全验证", "行为验证", "验证码"]):
        return True

    # 腾讯验证码通常会以内嵌 iframe 或容器出现。
    captcha_nodes = soup.select(
        "iframe[src*='captcha.eo.qq.com'], "
        "iframe[src*='geetest'], "
        "div[id*='captcha'], "
        "div[class*='captcha'], "
        "div[id*='geetest'], "
        "div[class*='geetest']"
    )
    if captcha_nodes:
        return True

    # 普通职位页至少会有 root 容器或岗位卡片；两者都缺失时更可能是拦截页。
    has_root = bool(soup.select_one("#root"))
    has_cards = bool(soup.select("div.joblist-box__item"))
    if not has_root and not has_cards:
        body_text = soup.get_text(" ", strip=True)
        if any(sig in body_text for sig in ["请完成验证", "验证后继续", "点击完成验证"]):
            return True

    return False


async def search_keyword(page, keyword: str) -> None:
    """兼容旧调用（未指定城市时默认广州）。"""
    await search_keyword_in_city(
        page,
        keyword=keyword,
        city_name="广州",
        delay=(2.5, 4.0),
        typing_delay_ms=tuple(DEFAULT_CONFIG["typing_delay_ms"]),
    )


async def fetch_job_summary_from_detail_page(
    detail_page,
    detail_url: str,
    settings: dict[str, Any],
) -> str:
    """抓取职位详情页并提取完整岗位摘要。"""
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
            if looks_like_verification_page(html):
                raise RuntimeError("详情页疑似触发验证")

            summary = extract_job_summary_from_detail_html(html)
            if summary:
                return summary

            if attempt > max_retries:
                print(f"详情页未提取到岗位摘要，已放弃：{detail_url}")
                return ""

            raise RuntimeError("详情页未提取到岗位摘要")
        except Exception as exc:
            if attempt > max_retries:
                print(f"详情页抓取失败，已放弃：{detail_url}，原因：{exc}")
                return ""

            retry_delay = scale_retry_delay(
                settings["delays"]["detail_retry"],
                attempt=attempt,
                backoff_factor=settings["retry_backoff_factor"],
                max_seconds=settings["max_retry_delay_seconds"],
            )
            print(
                f"详情页抓取异常，准备重试（{attempt}/{max_retries}）：{detail_url}，原因：{exc}"
            )
            await human_sleep(*retry_delay)

    return ""


async def enrich_jobs_with_detail_summaries(
    context,
    jobs: list[dict[str, Any]],
    settings: dict[str, Any],
    summary_cache: dict[str, str],
    crawled_link_store: CrawledLinkStore | None = None,
) -> int:
    """逐条访问详情页，补全能力描述中的岗位摘要。"""
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

            fetched_from_network = False
            if detail_url in summary_cache:
                full_summary = summary_cache[detail_url]
            else:
                fetched_from_network = True
                full_summary = await fetch_job_summary_from_detail_page(
                    detail_page=detail_page,
                    detail_url=detail_url,
                    settings=settings,
                )
                summary_cache[detail_url] = full_summary
                if crawled_link_store is not None:
                    crawled_link_store.add(detail_url)
                    crawled_link_store.save()

            if full_summary:
                work_content, requirement = split_job_summary(full_summary)
                changed = False
                if work_content and work_content != item.get("工作内容", ""):
                    item["工作内容"] = work_content
                    changed = True
                if requirement and requirement != item.get("任职要求", ""):
                    item["任职要求"] = requirement
                    changed = True
                item["__岗位摘要"] = full_summary
                if changed:
                    updated_count += 1

            if fetched_from_network:
                await human_sleep(*settings["delays"]["between_details"])
    finally:
        await detail_page.close()

    return updated_count


async def search_keyword_in_city(
    page,
    keyword: str,
    city_name: str,
    delay: tuple[float, float],
    typing_delay_ms: tuple[int, int],
) -> None:
    """按关键词 + 城市名称直达搜索页，确保输入岗位和地区同时生效。"""
    target_url = build_search_url(keyword=keyword, city_name=city_name, page=1)
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=90000)
    except Exception as e:
        print(f"首次请求出现异常（可能网络波动）：{e}，正在重试...")
        await asyncio.sleep(3.0)
        await page.goto(target_url, wait_until="domcontentloaded", timeout=90000)

    await human_sleep(*delay)

    # 校验页面状态中的关键词是否与输入一致；若不一致，尝试输入框兜底。
    html = await page.content()
    state = extract_initial_state(html)
    page_keyword = clean_text(
        state.get("queryParams", {}).get("keyWords", "")
        or state.get("displayParams", {}).get("keyWords", "")
    )
    if page_keyword == clean_text(keyword):
        return

    try:
        await page.wait_for_selector("input.query-search__content-input", timeout=20000)
        search_input = page.locator("input.query-search__content-input:visible").first
        await search_input.click()
        await search_input.fill("")
        await search_input.type(keyword, delay=random.randint(typing_delay_ms[0], typing_delay_ms[1]))

        try:
            await page.click("button.query-search__content-button", timeout=10000)
        except PlaywrightTimeoutError:
            await search_input.press("Enter")

        await human_sleep(*delay)
    except PlaywrightTimeoutError:
        # 页面结构异常时交由后续解析逻辑处理。
        return


async def crawl_zhaopin(
    keyword: str,
    city: str,
    settings: dict[str, Any],
    crawled_link_store: CrawledLinkStore | None = None,
) -> list[dict]:
    """爬取智联招聘岗位数据并返回列表。"""
    if async_playwright is None:
        raise RuntimeError(
            "缺少 Playwright 依赖，请先运行：pip install -r requirements.txt && playwright install chromium"
        )

    jobs = []
    seen = set()
    detail_summary_cache: dict[str, str] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings["headless"])
        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent=settings["user_agent"],
            viewport=settings["viewport"],
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = await context.new_page()

        try:
            print(f"开始抓取：关键词={keyword}，地区={city}")
            await search_keyword_in_city(
                page,
                keyword=keyword,
                city_name=city,
                delay=settings["delays"]["after_open_search"],
                typing_delay_ms=settings["typing_delay_ms"],
            )

            current_page = 1
            empty_retry_count = 0
            while current_page <= settings["max_pages_per_region"]:
                await human_sleep(*settings["delays"]["between_pages"])
                html = await page.content()

                state = extract_initial_state(html)
                raw_page_jobs = parse_jobs_from_state(state)
                if not raw_page_jobs:
                    raw_page_jobs = parse_jobs_from_dom(html)

                if not raw_page_jobs:
                    empty_retry_count += 1
                    if empty_retry_count <= settings["max_empty_page_retries"]:
                        if looks_like_verification_page(html):
                            print(
                                f"第 {current_page} 页疑似触发验证，"
                                f"自动重试（{empty_retry_count}/{settings['max_empty_page_retries']}）..."
                            )
                        else:
                            print(
                                f"第 {current_page} 页暂未解析到岗位，"
                                f"自动重试（{empty_retry_count}/{settings['max_empty_page_retries']}）..."
                            )

                        retry_delay = scale_retry_delay(
                            settings["delays"]["retry_reload"],
                            attempt=empty_retry_count,
                            backoff_factor=settings["retry_backoff_factor"],
                            max_seconds=settings["max_retry_delay_seconds"],
                        )
                        await human_sleep(*retry_delay)
                        await page.reload(wait_until="domcontentloaded", timeout=90000)
                        continue

                    print(
                        f"第 {current_page} 页连续 {settings['max_empty_page_retries']} 次未解析到岗位，"
                        "已停止继续翻页，保留已抓取数据。"
                    )
                    break

                empty_retry_count = 0

                page_jobs = [
                    item
                    for item in raw_page_jobs
                    if is_job_in_target_city(str(item.get("__工作城市", "")), city)
                ]
                filtered_count = len(raw_page_jobs) - len(page_jobs)

                if not page_jobs:
                    print(
                        f"第 {current_page} 页：解析 {len(raw_page_jobs)} 条，"
                        f"但均不属于目标地区《{city}》，已跳过。"
                    )
                    if current_page >= settings["max_pages_per_region"]:
                        break

                    next_page = current_page + 1
                    next_url = build_search_url(keyword=keyword, city_name=city, page=next_page)
                    if not extract_next_page_url(html):
                        print("已到最后一页。")
                        break

                    await human_sleep(*settings["delays"]["before_next_page"])
                    try:
                        await page.goto(next_url, wait_until="domcontentloaded", timeout=90000)
                    except Exception as e:
                        print(f"跳转下一页时遇到网络异常：{e}，结束当前搜索。")
                        break
                    await human_sleep(*settings["delays"]["after_next_page"])
                    current_page = next_page
                    continue

                detail_updated = await enrich_jobs_with_detail_summaries(
                    context=context,
                    jobs=page_jobs,
                    settings=settings,
                    summary_cache=detail_summary_cache,
                    crawled_link_store=crawled_link_store,
                )

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

                print(
                    f"第 {current_page} 页：解析 {len(raw_page_jobs)} 条，"
                    f"过滤非目标地区 {filtered_count} 条，详情补全 {detail_updated} 条，"
                    f"新增 {new_count} 条（累计 {len(jobs)} 条）"
                )

                if current_page >= settings["max_pages_per_region"]:
                    break

                # 优先使用 query 参数构造下一页 URL，减少对页面内加密路径的依赖。
                next_page = current_page + 1
                next_url = build_search_url(keyword=keyword, city_name=city, page=next_page)

                # 当页面确认没有下一页时停止。
                if not extract_next_page_url(html):
                    print("已到最后一页。")
                    break

                if (
                    settings["long_break_every_pages"] > 0
                    and current_page % settings["long_break_every_pages"] == 0
                    and random.random() < settings["long_break_probability"]
                ):
                    print("执行随机冷却暂停，降低高频行为特征。")
                    await human_sleep(*settings["delays"]["long_break"])

                await human_sleep(*settings["delays"]["before_next_page"])
                try:
                    await page.goto(next_url, wait_until="domcontentloaded", timeout=90000)
                except Exception as e:
                    print(f"跳转下一页时遇到网络异常：{e}，正在重试...")
                    await human_sleep(2.0, 4.0)
                    try:
                        await page.goto(next_url, wait_until="domcontentloaded", timeout=90000)
                    except Exception as e2:
                        print(f"重试跳页依然失败：{e2}，结束当前搜索。")
                        break

                await human_sleep(*settings["delays"]["after_next_page"])
                current_page = next_page

        finally:
            await context.close()
            await browser.close()

    return jobs
