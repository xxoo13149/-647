import asyncio
import datetime as dt
import json
import random
import re
import urllib.parse
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .browser_backend import (
    fetch_html_with_scrapling,
    launch_persistent_context_with_fallback,
    using_adspower,
    using_gologin,
    using_orbita_cdp,
    using_scrapling,
)
from .constants import DEFAULT_CONFIG, EMPTY_CELL_VALUE, ZHAOPIN_SEARCH_URL
from .crawled_links import CrawledLinkStore
from .gologin_backend import (
    launch_gologin_browser,
    stop_gologin_api,
    using_gologin_api,
    get_gologin_executable_path,
)
from .output import build_job_record_key
from .stealth_js import STEALTH_INIT_SCRIPT
from .adspower_backend import launch_adspower_browser
from .orbita_cdp_backend import launch_orbita_browser, stop_orbita_browser, connect_playwright_to_orbita
from .utils import *  # noqa: F403

try:
    from .yescaptcha import (
        describe_captcha_context,
        detect_captcha_context,
        is_yescaptcha_configured,
        solve_zhaopin_captcha,
    )
except ImportError:
    solve_zhaopin_captcha = None
    is_yescaptcha_configured = None
    detect_captcha_context = None
    describe_captcha_context = None

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except ImportError:
    PlaywrightTimeoutError = TimeoutError
    async_playwright = None


ZHAOPIN_SEARCH_WAIT_SELECTOR = "div.joblist-box__item, input.query-search__content-input"
ZHAOPIN_DETAIL_WAIT_SELECTOR = (
    "div.describtion-card__detail-content, "
    "div.jobdetail-box__content, "
    "div.describtion__detail-content, "
    "div.jobdetail__content, "
    "div.job-detail, "
    "div.job-description, "
    "section.jobdetail__content, "
    "div.jobrequirement"
)


class ZhaopinRateLimitError(RuntimeError):
    """Raised when Zhaopin returns a frequency-control page that cannot be manually cleared."""


def has_detail_summary(item: dict[str, Any]) -> bool:
    """Return whether a list item already carries usable detail text."""
    for key in ("工作内容", "任职要求", "__岗位摘要"):
        value = clean_text(str(item.get(key, "")))
        if value and value != EMPTY_CELL_VALUE:
            return True
    return False


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
    """构造智联搜索 URL，使用城市名称直接搜索（无需 code）。
    
    city_name 为 "全国" 或空字符串时不限制地区。
    """
    params = {"kw": keyword, "p": str(page)}
    normalized_city = city_name.strip()
    if normalized_city and normalized_city != "全国":
        params["jl"] = normalized_city
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
                "岗位类型一级": "",
                "岗位类型二级": "",
                "岗位名称": job_name,
                "岗位类型企业/公务员/事业单位/军队文职": "企业",
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
                "发布时间": latest_publish_time,
                "投递起始时间": latest_publish_time,
                "投递截止时间": "",
                "证书要求": "",
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
                "岗位类型一级": "",
                "岗位类型二级": "",
                "岗位名称": job_name,
                "岗位类型企业/公务员/事业单位/军队文职": "企业",
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
                "发布时间": latest_publish_time,
                "投递起始时间": latest_publish_time,
                "投递截止时间": "",
                "证书要求": "",
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


def classify_zhaopin_access_page(html: str) -> str:
    """识别智联中间拦截页类型：none / verification / rate_limited。"""
    soup = BeautifulSoup(html, "html.parser")
    title_text = clean_text(soup.title.get_text()) if soup.title else ""
    body_text = clean_text(soup.get_text(" ", strip=True))
    lowered_title = title_text.lower()

    if any(sig in body_text for sig in ["操作过于频繁", "请稍后再试", "稍后再试"]):
        return "rate_limited"

    if "security verification" in lowered_title:
        return "verification"

    if any(sig in title_text for sig in ["安全验证", "行为验证", "验证码"]):
        return "verification"

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
        return "verification"

    # 普通职位页至少会有 root 容器或岗位卡片；两者都缺失时更可能是拦截页。
    has_root = bool(soup.select_one("#root"))
    has_cards = bool(soup.select("div.joblist-box__item"))
    if not has_root and not has_cards:
        if any(sig in body_text for sig in ["请完成验证", "验证后继续", "点击完成验证"]):
            return "verification"

    return "none"


def looks_like_rate_limit_page(html: str) -> bool:
    return classify_zhaopin_access_page(html) == "rate_limited"


def looks_like_verification_page(html: str) -> bool:
    """判断是否进入了验证/频控拦截页。"""
    return classify_zhaopin_access_page(html) != "none"


def can_wait_for_zhaopin_auth(settings: dict[str, Any]) -> bool:
    return bool(settings.get("manual_auth") or not settings.get("headless", True))


async def looks_like_zhaopin_logged_in(page) -> bool:
    """Best-effort check for an existing Zhaopin login session."""
    page_url = clean_text(getattr(page, "url", ""))
    if "passport.zhaopin.com" in page_url or "/login" in page_url:
        return False

    html = ""
    try:
        html = await page.content()
    except Exception:
        pass

    if html:
        soup = BeautifulSoup(html, "html.parser")
        body_text = clean_text(soup.get_text(" ", strip=True))
        if any(sig in body_text for sig in ["退出", "我的智联", "个人中心", "消息中心", "我的简历"]):
            return True
        if any(sig in body_text for sig in ["登录", "注册"]) and not any(
            sig in body_text for sig in ["退出", "我的智联", "个人中心"]
        ):
            return False

    try:
        cookies = await page.context.cookies()
    except Exception:
        cookies = []

    for cookie in cookies:
        domain = clean_text(str(cookie.get("domain", ""))).lower()
        name = clean_text(str(cookie.get("name", ""))).lower()
        if "zhaopin.com" in domain and any(token in name for token in ("ticket", "token", "session", "sid", "auth")):
            return True

    return False


async def wait_for_manual_zhaopin_auth(
    page,
    settings: dict[str, Any],
    reason: str,
    target_url: str = "",
) -> bool:
    """Pause for a human to finish Zhaopin's verification in the visible browser.
    
    优先尝试使用 YesCaptcha 自动解决验证码，失败后回退到人工验证。
    """
    if settings.get("headless", True):
        raise RuntimeError(
            "Zhaopin verification appeared in headless mode. "
            "Run once with --login-zhaopin, or crawl with --headed and MANUAL_AUTH=true."
        )

    try:
        initial_html = await page.content()
    except Exception:
        initial_html = ""
    if initial_html and looks_like_rate_limit_page(initial_html):
        raise ZhaopinRateLimitError("当前会话触发智联频控，页面提示“操作过于频繁，请稍后再试”。")
    if detect_captcha_context and describe_captcha_context:
        try:
            context = detect_captcha_context(initial_html, page.url)
            emit_task_log(settings, f"Zhaopin verification diagnostics: {describe_captcha_context(context)}")
        except Exception as exc:
            emit_task_log(settings, f"Zhaopin verification diagnostics failed: {exc}")


    # 优先尝试自动验证码解决
    if solve_zhaopin_captcha and is_yescaptcha_configured(settings):
        emit_task_log(
            settings,
            f"Zhaopin needs verification: {reason}. "
            "Attempting automatic captcha solving with YesCaptcha..."
        )
        try:
            success = await solve_zhaopin_captcha(page, settings)
            if success:
                emit_task_log(settings, "YesCaptcha automatic verification successful; resuming.")
                await asyncio.sleep(2)
                # 验证页面是否已通过
                html = await page.content()
                if classify_zhaopin_access_page(html) == "none":
                    return True
                emit_task_log(settings, "YesCaptcha solved but page still shows verification, falling back to manual.")
            else:
                emit_task_log(settings, "YesCaptcha automatic verification failed, falling back to manual.")
        except Exception as e:
            emit_task_log(settings, f"YesCaptcha error: {e}, falling back to manual verification.")
    else:
        configured = bool(is_yescaptcha_configured(settings)) if is_yescaptcha_configured else False
        emit_task_log(
            settings,
            f"YesCaptcha branch skipped: solver_loaded={bool(solve_zhaopin_captcha)}, configured={configured}.",
        )


    # 回退到人工验证
    emit_task_log(
        settings,
        f"Zhaopin needs manual verification: {reason}. "
        "Please finish it in the opened browser; the task will resume automatically after verification.",
    )

    waiting_logged = False
    while True:
        await asyncio.sleep(3)
        if is_cancel_requested(settings):
            emit_task_log(settings, "Manual verification wait stopped because the task was cancelled.")
            return False
        try:
            html = await page.content()
            gate_kind = classify_zhaopin_access_page(html)
            if gate_kind == "rate_limited":
                raise ZhaopinRateLimitError("当前会话触发智联频控，页面提示“操作过于频繁，请稍后再试”。")
            if gate_kind == "none":
                if target_url and page.url != target_url:
                    try:
                        await page.goto(target_url, wait_until="domcontentloaded", timeout=90000)
                        await human_sleep(*settings["delays"]["after_open_detail"])
                    except Exception as exc:
                        emit_task_log(settings, f"Verification finished, but reopening the original page failed: {exc}")
                emit_task_log(settings, "Zhaopin manual verification appears complete; resuming.")
                return True
            if not waiting_logged:
                emit_task_log(settings, "Verification page is still active. Waiting for manual completion.")
                waiting_logged = True
        except ZhaopinRateLimitError:
            raise
        except Exception:
            pass


async def login_zhaopin_profile(settings: dict[str, Any]) -> None:
    """Open a persistent Zhaopin browser profile for manual verification/login."""
    if async_playwright is None:
        raise RuntimeError(
            "缺少 Playwright Python 依赖，请先运行：pip install -r requirements.txt。"
            "orbita_cdp 主流程不需要安装 Playwright 自带 Chromium。"
        )

    user_data_dir = Path(settings["zhaopin_user_data_dir"])
    user_data_dir.mkdir(parents=True, exist_ok=True)
    wait_seconds = int(settings["auth_wait_seconds"])

    async with async_playwright() as p:
        gl = None
        proc = None
        if using_orbita_cdp(settings):
            orbita_exe = get_gologin_executable_path()
            proc, ws_url = await launch_orbita_browser(orbita_exe, settings["zhaopin_user_data_dir"], headless=False)
            browser, context = await connect_playwright_to_orbita(p, ws_url)
        elif using_adspower(settings):
            # API 模式
            browser, context, gl = await launch_gologin_browser(p, settings)
        elif using_gologin(settings):
            # 本地模式：Orbita 引擎（不传额外 args，Orbita 内置反检测）
            user_data_dir = Path(settings["zhaopin_user_data_dir"])
            user_data_dir.mkdir(parents=True, exist_ok=True)
            context = await launch_persistent_context_with_fallback(
                p.chromium,
                user_data_dir=user_data_dir,
                headless=False,
                executable_path=get_gologin_executable_path(),
                ignore_https_errors=True,
                user_agent=settings["user_agent"],
                viewport=settings["viewport"],
                locale="zh-CN",
            )
        else:
            user_data_dir = Path(settings["zhaopin_user_data_dir"])
            user_data_dir.mkdir(parents=True, exist_ok=True)
            context = await launch_persistent_context_with_fallback(
                p.chromium,
                user_data_dir=user_data_dir,
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_https_errors=True,
                user_agent=settings["user_agent"],
                viewport=settings["viewport"],
                locale="zh-CN",
            )
        await context.add_init_script(
            STEALTH_INIT_SCRIPT,
        )

        # Orbita 浏览器兼容：直接用已有页面，不关闭重建
        page = context.pages[0] if context.pages else await context.new_page()
        home_url = "https://www.zhaopin.com"
        login_url = "https://passport.zhaopin.com/login?BkUrl=https%3A%2F%2Fwww.zhaopin.com"

        await page.goto(home_url, wait_until="domcontentloaded", timeout=90000)
        await page.bring_to_front()
        await asyncio.sleep(2)

        if await looks_like_zhaopin_logged_in(page):
            print(
                "Detected an existing Zhaopin login session. "
                "Staying on https://www.zhaopin.com and saving the profile."
            )
        else:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=90000)
            await page.bring_to_front()
            print(
                "Opened Zhaopin login page. Finish login/verification in the browser; "
                f"the profile will be saved after {wait_seconds} seconds."
            )

        await asyncio.sleep(wait_seconds)
        if using_gologin(settings):
            print(f"Zhaopin Gologin session will be saved on stop.")
        else:
            print(f"Zhaopin browser profile saved at: {user_data_dir}")
        await context.close()
        if proc is not None:
            stop_orbita_browser(proc)
        elif gl is not None:
            stop_gologin_api(gl)


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
    if using_scrapling(settings):
        try:
            scrapling_html = await fetch_html_with_scrapling(
                detail_url,
                settings=settings,
                wait_selector=ZHAOPIN_DETAIL_WAIT_SELECTOR,
                profile_dir=settings["zhaopin_user_data_dir"],
                wait_ms=int(float(settings["delays"]["after_open_detail"][1]) * 1000),
            )
            if scrapling_html and not looks_like_verification_page(scrapling_html):
                summary = extract_job_summary_from_detail_html(scrapling_html)
                if summary:
                    emit_task_log(settings, f"Scrapling loaded Zhaopin detail page: {detail_url}")
                    return summary
                emit_task_log(settings, f"Scrapling opened detail page but did not extract summary: {detail_url}")
            elif scrapling_html:
                emit_task_log(settings, f"Scrapling hit verification on detail page; switching to Playwright: {detail_url}")
        except Exception as exc:
            emit_task_log(settings, f"Scrapling detail fetch failed, falling back to Playwright: {exc}")
    for attempt in range(1, max_retries + 2):
        try:
            if settings.get("_zhaopin_skip_detail_fetch"):
                return ""
            await human_sleep(*settings["delays"]["before_open_detail"])
            await detail_page.goto(
                detail_url,
                wait_until="domcontentloaded",
                timeout=settings["detail_page_timeout_ms"],
            )
            await human_sleep(*settings["delays"]["after_open_detail"])

            html = await detail_page.content()
            if looks_like_verification_page(html):
                if can_wait_for_zhaopin_auth(settings):
                    try:
                        verified = await wait_for_manual_zhaopin_auth(
                            detail_page,
                            settings,
                            reason=f"detail page verification: {detail_url}",
                            target_url=detail_url,
                        )
                    except ZhaopinRateLimitError as exc:
                        settings["_zhaopin_skip_detail_fetch"] = True
                        settings["_zhaopin_skip_detail_reason"] = str(exc)
                        emit_task_log(
                            settings,
                            "详情页触发智联频控，后续详情补全将先跳过，继续导出列表页结果。"
                        )
                        emit_task_log(settings, f"频控详情：{exc}")
                        return ""
                    if not verified and is_cancel_requested(settings):
                        return ""
                    if verified:
                        html = await detail_page.content()
                if looks_like_verification_page(html):
                    raise RuntimeError("Zhaopin detail page still needs verification")

            summary = extract_job_summary_from_detail_html(html)
            if summary:
                return summary

            if attempt > max_retries:
                emit_task_log(settings, f"详情页未提取到岗位摘要，已放弃：{detail_url}")
                return ""

            raise RuntimeError("详情页未提取到岗位摘要")
        except Exception as exc:
            if attempt > max_retries:
                emit_task_log(settings, f"详情页抓取失败，已放弃：{detail_url}，原因：{exc}")
                return ""

            retry_delay = scale_retry_delay(
                settings["delays"]["detail_retry"],
                attempt=attempt,
                backoff_factor=settings["retry_backoff_factor"],
                max_seconds=settings["max_retry_delay_seconds"],
            )
            emit_task_log(
                settings,
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
    item_callback=None,
) -> int:
    """逐条访问详情页，补全能力描述中的岗位摘要。"""
    if not jobs:
        return 0

    updated_count = 0
    detail_page = await context.new_page()

    try:
        total_jobs = len(jobs)
        for index, item in enumerate(jobs, start=1):
            if settings.get("_zhaopin_skip_detail_fetch"):
                if not settings.get("_zhaopin_skip_detail_logged"):
                    reason = clean_text(str(settings.get("_zhaopin_skip_detail_reason", ""))) or "详情页访问已被智联限制"
                    emit_task_log(
                        settings,
                        f"详情页补全已降级：{reason}。后续岗位先保留列表页字段并继续写入 Excel。"
                    )
                    settings["_zhaopin_skip_detail_logged"] = True
                if callable(item_callback):
                    item_callback(item, index)
                if is_cancel_requested(settings):
                    emit_cancel_log_once(settings, "收到中止请求，当前详情已处理完成，停止继续分析剩余详情。")
                    break
                continue
            detail_url = clean_text(str(item.get("__详情链接", "")))
            if not detail_url:
                if callable(item_callback):
                    item_callback(item, index)
                if is_cancel_requested(settings):
                    emit_cancel_log_once(settings, "收到中止请求，当前详情已处理完成，停止继续分析剩余详情。")
                    break
                continue
            detail_already_recorded = crawled_link_store is not None and crawled_link_store.contains(detail_url)
            detail_text_recorded = crawled_link_store is not None and crawled_link_store.has_detail_text(detail_url)
            force_detail_refetch = bool(settings.get("refetch_crawled_details"))
            if detail_already_recorded and not force_detail_refetch and (
                detail_text_recorded or has_detail_summary(item)
            ):
                emit_task_log(settings, f"详情链接已抓取过，跳过 ({index}/{total_jobs})：{detail_url}")
                if callable(item_callback):
                    item_callback(item, index)
                if is_cancel_requested(settings):
                    emit_cancel_log_once(settings, "收到中止请求，当前详情已处理完成，停止继续分析剩余详情。")
                    break
                continue
            if detail_already_recorded and force_detail_refetch:
                emit_task_log(settings, f"详情链接有历史记录，已按配置重新补全 ({index}/{total_jobs})：{detail_url}")
            elif detail_already_recorded:
                emit_task_log(settings, f"详情链接有历史记录但当前缺少详情字段，重新补全 ({index}/{total_jobs})：{detail_url}")

            fetched_from_network = False
            if detail_url in summary_cache:
                full_summary = summary_cache[detail_url]
                emit_task_log(settings, f"使用详情缓存 ({index}/{total_jobs})：{detail_url}")
                if crawled_link_store is not None:
                    crawled_link_store.add(detail_url)
                    crawled_link_store.save()
            else:
                fetched_from_network = True
                update_task_progress(settings, current_detail_url=detail_url, detail_index=index, detail_total=total_jobs)
                emit_task_log(settings, f"正在分析详情链接 ({index}/{total_jobs})：{detail_url}")
                if crawled_link_store is not None and not detail_already_recorded:
                    if not crawled_link_store.add(detail_url):
                        emit_task_log(settings, f"详情链接被其他任务记录，跳过 ({index}/{total_jobs})：{detail_url}")
                        if callable(item_callback):
                            item_callback(item, index)
                        if is_cancel_requested(settings):
                            emit_cancel_log_once(settings, "收到中止请求，当前详情已处理完成，停止继续分析剩余详情。")
                            break
                        continue
                    crawled_link_store.save()
                full_summary = await fetch_job_summary_from_detail_page(
                    detail_page=detail_page,
                    detail_url=detail_url,
                    settings=settings,
                )
                summary_cache[detail_url] = full_summary

            if full_summary:
                if crawled_link_store is not None:
                    crawled_link_store.mark_detail_text(detail_url)
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
            if callable(item_callback):
                item_callback(item, index)
            if is_cancel_requested(settings):
                emit_cancel_log_once(settings, "收到中止请求，当前详情已处理完成，停止继续分析剩余详情。")
                break
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
        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"导航到搜索页超时/失败（可能触发验证）：{e}")

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


async def load_zhaopin_search_html(
    page,
    keyword: str,
    city_name: str,
    settings: dict[str, Any],
    page_number: int = 1,
) -> str:
    target_url = build_search_url(keyword=keyword, city_name=city_name, page=page_number)
    if using_scrapling(settings):
        try:
            html = await fetch_html_with_scrapling(
                target_url,
                settings=settings,
                wait_selector=ZHAOPIN_SEARCH_WAIT_SELECTOR,
                profile_dir=settings["zhaopin_user_data_dir"],
            )
            if html and not looks_like_verification_page(html):
                emit_task_log(settings, f"Search page {page_number} loaded with Scrapling backend.")
                return html
            if html:
                emit_task_log(settings, f"Scrapling hit verification on search page {page_number}; falling back to Playwright.")
        except Exception as exc:
            emit_task_log(settings, f"Scrapling backend failed on search page {page_number}, falling back to Playwright: {exc}")

    if page_number == 1:
        await search_keyword_in_city(
            page,
            keyword=keyword,
            city_name=city_name,
            delay=settings["delays"]["after_open_search"],
            typing_delay_ms=settings["typing_delay_ms"],
        )
    else:
        target_url = build_search_url(keyword=keyword, city_name=city_name, page=page_number)
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            emit_task_log(settings, f"Playwright navigation failed on search page {page_number}: {exc}")
            return ""
    return await page.content()


async def crawl_zhaopin(
    keyword: str,
    city: str,
    settings: dict[str, Any],
    crawled_link_store: CrawledLinkStore | None = None,
) -> list[dict]:
    """爬取智联招聘岗位数据并返回列表。"""
    if async_playwright is None:
        raise RuntimeError(
            "缺少 Playwright Python 依赖，请先运行：pip install -r requirements.txt。"
            "orbita_cdp 主流程不需要安装 Playwright 自带 Chromium。"
        )

    jobs = []
    seen = set()
    detail_summary_cache: dict[str, str] = {}

    async with async_playwright() as p:
        gl = None
        proc = None
        if using_orbita_cdp(settings):
            orbita_exe = get_gologin_executable_path()
            proc, ws_url = await launch_orbita_browser(orbita_exe, settings["zhaopin_user_data_dir"], headless=settings["headless"])
            browser, context = await connect_playwright_to_orbita(p, ws_url)
        elif using_adspower(settings):
            # API 模式：完整指纹伪装
            browser, context, gl = await launch_gologin_browser(p, settings)
        elif using_gologin(settings):
            # 本地模式：Orbita 引擎（不传额外 args，Orbita 内置反检测）
            zhaopin_user_data_dir = Path(settings["zhaopin_user_data_dir"])
            zhaopin_user_data_dir.mkdir(parents=True, exist_ok=True)
            context = await launch_persistent_context_with_fallback(
                p.chromium,
                user_data_dir=zhaopin_user_data_dir,
                headless=settings["headless"],
                executable_path=get_gologin_executable_path(),
                ignore_https_errors=True,
                user_agent=settings["user_agent"],
                viewport=settings["viewport"],
                locale="zh-CN",
            )
            await context.add_init_script(
                STEALTH_INIT_SCRIPT,
            )
        else:
            zhaopin_user_data_dir = Path(settings["zhaopin_user_data_dir"])
            zhaopin_user_data_dir.mkdir(parents=True, exist_ok=True)
            context = await launch_persistent_context_with_fallback(
                p.chromium,
                user_data_dir=zhaopin_user_data_dir,
                headless=settings["headless"],
                args=["--disable-blink-features=AutomationControlled"],
                ignore_https_errors=True,
                user_agent=settings["user_agent"],
                viewport=settings["viewport"],
                locale="zh-CN",
            )
            await context.add_init_script(
                STEALTH_INIT_SCRIPT,
            )

        page = context.pages[0] if context.pages else await context.new_page()

        try:
            region_label = city or "不限地区"
            update_task_progress(
                settings,
                platform="zhaopin",
                keyword=keyword,
                region=region_label,
                page=1,
                total_pages=settings["max_pages_per_region"],
                cumulative_count=0,
                current_detail_url="",
            )
            emit_task_log(settings, f"开始抓取智联招聘：关键词={keyword}，地区={region_label}")
            current_page = 1
            empty_retry_count = 0
            while current_page <= settings["max_pages_per_region"]:
                if is_cancel_requested(settings):
                    emit_cancel_log_once(settings, "收到中止请求，停止读取新的页面。")
                    break
                update_task_progress(
                    settings,
                    platform="zhaopin",
                    keyword=keyword,
                    region=region_label,
                    page=current_page,
                    total_pages=settings["max_pages_per_region"],
                    current_detail_url="",
                )
                emit_task_log(
                    settings,
                    f"正在读取第 {current_page}/{settings['max_pages_per_region']} 页："
                    f"关键词={keyword}，地区={region_label}",
                )
                html = await load_zhaopin_search_html(
                    page=page,
                    keyword=keyword,
                    city_name=city,
                    settings=settings,
                    page_number=current_page,
                )
                await human_sleep(*settings["delays"]["between_pages"])
                if looks_like_verification_page(html) and can_wait_for_zhaopin_auth(settings):
                    current_url = build_search_url(keyword=keyword, city_name=city, page=current_page)
                    try:
                        verified = await wait_for_manual_zhaopin_auth(
                            page,
                            settings,
                            reason=f"search result page {current_page}",
                            target_url=current_url,
                        )
                    except ZhaopinRateLimitError as exc:
                        emit_task_log(settings, f"搜索结果页触发智联频控：{exc}")
                        emit_task_log(settings, "当前关键词已停止继续抓取，请稍后再试或更换会话环境。")
                        break
                    if not verified and is_cancel_requested(settings):
                        break
                    if verified:
                        html = await page.content()

                state = extract_initial_state(html)
                raw_page_jobs = parse_jobs_from_state(state)
                if not raw_page_jobs:
                    raw_page_jobs = parse_jobs_from_dom(html)

                if not raw_page_jobs:
                    empty_retry_count += 1
                    if empty_retry_count <= settings["max_empty_page_retries"]:
                        if looks_like_verification_page(html):
                            emit_task_log(
                                settings,
                                f"第 {current_page} 页疑似触发验证，"
                                f"自动重试（{empty_retry_count}/{settings['max_empty_page_retries']}）..."
                            )
                        else:
                            emit_task_log(
                                settings,
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
                        continue

                    emit_task_log(
                        settings,
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
                existing_filtered_count = 0
                existing_output_record_keys = settings.get("_current_output_record_keys")
                if settings.get("filter_existing_output_early") and isinstance(existing_output_record_keys, set):
                    filtered_page_jobs = []
                    for item in page_jobs:
                        if build_job_record_key(item) in existing_output_record_keys:
                            existing_filtered_count += 1
                            continue
                        filtered_page_jobs.append(item)
                    page_jobs = filtered_page_jobs
                update_task_progress(
                    settings,
                    parsed_count=len(raw_page_jobs),
                    kept_count=len(page_jobs),
                    filtered_count=filtered_count,
                    existing_filtered_count=existing_filtered_count,
                    cumulative_count=len(jobs),
                )
                filter_text = "未进行地区过滤" if not city else f"过滤非目标地区 {filtered_count} 条"
                if existing_filtered_count:
                    filter_text += f"，过滤 Excel 已有岗位 {existing_filtered_count} 条"
                emit_task_log(
                    settings,
                    f"第 {current_page} 页解析到 {len(raw_page_jobs)} 条，"
                    f"{filter_text}，准备处理 {len(page_jobs)} 条。",
                )

                if not page_jobs:
                    if existing_filtered_count:
                        skip_reason = (
                            f"第 {current_page} 页：解析 {len(raw_page_jobs)} 条，"
                            f"{filter_text}，但剩余岗位均已存在于当前 Excel，已跳过。"
                        )
                    else:
                        skip_reason = (
                            f"第 {current_page} 页：解析 {len(raw_page_jobs)} 条，"
                            f"但均不属于目标地区《{region_label}》，已跳过。"
                        )
                    emit_task_log(
                        settings,
                        skip_reason,
                    )
                    if is_cancel_requested(settings):
                        emit_cancel_log_once(settings, "收到中止请求，当前页已处理完成，停止继续翻页。")
                        break
                    if current_page >= settings["max_pages_per_region"]:
                        break

                    if not extract_next_page_url(html):
                        emit_task_log(settings, "已到最后一页。")
                        break

                    await human_sleep(*settings["delays"]["before_next_page"])
                    current_page += 1
                    continue

                new_count = 0
                page_result_callback = settings.get("page_result_callback")

                def handle_processed_item(item, detail_index=None):
                    nonlocal new_count
                    if not clean_text(str(item.get("岗位类别/大类", ""))):
                        item["岗位类别/大类"] = keyword
                    export_keyword = clean_text(str(settings.get("export_keyword", "")))
                    if export_keyword and not clean_text(str(item.get("岗位类型一级", ""))):
                        item["岗位类型一级"] = export_keyword
                    key = build_job_record_key(item)
                    if key in seen:
                        return
                    seen.add(key)
                    if isinstance(existing_output_record_keys, set):
                        existing_output_record_keys.add(key)
                    jobs.append(item)
                    new_count += 1
                    if callable(page_result_callback):
                        page_result_callback(
                            keyword,
                            [item],
                            {
                                "platform": "zhaopin",
                                "region": region_label,
                                "page": current_page,
                                "total_pages": settings["max_pages_per_region"],
                                "detail_index": detail_index,
                                "export_keyword": export_keyword or keyword,
                            },
                        )

                if settings.get("skip_detail_fetch"):
                    detail_updated = 0
                    emit_task_log(settings, "已启用列表页导出模式：跳过详情页补全，直接写入当前页岗位。")
                    for index, item in enumerate(page_jobs, start=1):
                        handle_processed_item(item, index)
                        if is_cancel_requested(settings):
                            emit_cancel_log_once(settings, "收到中止请求，当前页已处理完成，停止继续翻页。")
                            break
                else:
                    detail_updated = await enrich_jobs_with_detail_summaries(
                        context=context,
                        jobs=page_jobs,
                        settings=settings,
                        summary_cache=detail_summary_cache,
                        crawled_link_store=crawled_link_store,
                        item_callback=handle_processed_item,
                    )

                emit_task_log(
                    settings,
                    f"第 {current_page} 页：解析 {len(raw_page_jobs)} 条，"
                    f"{filter_text}，详情补全 {detail_updated} 条，"
                    f"新增 {new_count} 条（累计 {len(jobs)} 条）"
                )
                update_task_progress(settings, cumulative_count=len(jobs), current_detail_url="")

                if is_cancel_requested(settings):
                    emit_cancel_log_once(settings, "收到中止请求，当前页已处理完成，停止继续翻页。")
                    break

                if current_page >= settings["max_pages_per_region"]:
                    break

                # 当页面确认没有下一页时停止。
                if not extract_next_page_url(html):
                    emit_task_log(settings, "已到最后一页。")
                    break

                if (
                    settings["long_break_every_pages"] > 0
                    and current_page % settings["long_break_every_pages"] == 0
                    and random.random() < settings["long_break_probability"]
                ):
                    emit_task_log(settings, "执行随机冷却暂停，降低高频行为特征。")
                    await human_sleep(*settings["delays"]["long_break"])

                await human_sleep(*settings["delays"]["before_next_page"])
                current_page += 1
                continue

        finally:
            await context.close()
            if proc is not None:
                stop_orbita_browser(proc)
            elif gl is not None:
                stop_gologin_api(gl)

    return jobs
