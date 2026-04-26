import asyncio
import copy
import datetime as dt
import json
import os
import random
import re
import urllib.parse
from pathlib import Path
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


# 智联搜索入口。关键词通过 query 参数传入，由站点自动跳转到对应 kw 加密路径。
ZHAOPIN_SEARCH_URL = "https://www.zhaopin.com/sou/"

ENV_FILE_NAME = ".env"

DEFAULT_CONFIG: dict[str, Any] = {
    "keywords": ["数据分析"],
    "regions": [],
    "default_regions": ["北京", "上海", "广州", "深圳", "杭州"],
    "max_pages_per_region": 5,
    "max_empty_page_retries": 2,
    "headless": False,
    "user_agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1400, "height": 900},
    "delay_seconds": {
        "after_open_search": [1.0, 2.0],
        "between_pages": [0.8, 1.5],
        "retry_reload": [2.5, 4.0],
        "between_tasks": [2.5, 4.5],
        "before_next_page": [0.5, 1.0],
        "after_next_page": [1.0, 1.8],
        "long_break": [6.0, 10.0],
        "before_open_detail": [0.8, 1.6],
        "after_open_detail": [1.8, 3.0],
        "between_details": [0.9, 1.8],
        "detail_retry": [2.5, 4.5],
    },
    "typing_delay_ms": [40, 100],
    "retry_backoff_factor": 1.7,
    "max_retry_delay_seconds": 36.0,
    "max_detail_retries": 1,
    "detail_page_timeout_ms": 90000,
    "long_break_every_pages": 5,
    "long_break_probability": 0.2,
    "output_dir": "output",
}

OUTPUT_COLUMNS = ["招聘单位名称", "岗位名称", "最新发布时间", "能力描述"]


async def human_sleep(min_s: float = 1.5, max_s: float = 3.5) -> None:
    """随机等待，降低自动化行为特征。"""
    await asyncio.sleep(random.uniform(min_s, max_s))


def clean_text(text: str) -> str:
    """压缩空白并去除首尾空格。"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def clean_multiline_text(text: str) -> str:
    """压缩每行空白并保留换行，适合岗位职责等长文本。"""
    if not text:
        return ""

    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw_line in normalized.split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def normalize_city_name(raw_city: str) -> str:
    """归一化城市名称，兼容“北京市”“北京·朝阳”等格式。"""
    city = clean_text(raw_city)
    if not city:
        return ""

    for sep in ["·", "-", "/", "|", " "]:
        if sep in city:
            city = city.split(sep, 1)[0]

    city = city.replace("市", "")
    return clean_text(city)


def is_job_in_target_city(job_city: str, target_city: str) -> bool:
    """判断岗位城市是否与目标城市一致。"""
    normalized_target = normalize_city_name(target_city)
    normalized_job_city = normalize_city_name(job_city)

    if not normalized_target:
        return True
    if not normalized_job_city:
        return False

    return (
        normalized_job_city == normalized_target
        or normalized_job_city.startswith(normalized_target)
        or normalized_target.startswith(normalized_job_city)
    )


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


def normalize_absolute_url(url: str, base_url: str = "https://www.zhaopin.com") -> str:
    """将相对链接、协议相对链接归一化为绝对链接。"""
    text = clean_text(url)
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    return urllib.parse.urljoin(base_url, text)


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


def looks_like_job_summary_text(text: str) -> bool:
    """判断文本是否像岗位详情摘要。"""
    cleaned = clean_text(text)
    if len(cleaned) < 60:
        return False
    if any(token in cleaned for token in ["职位描述", "岗位职责", "任职要求", "工作内容", "岗位要求"]):
        return True
    return len(cleaned) >= 180


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


def normalize_publish_time_text(raw: Any) -> str:
    """归一化岗位发布时间/更新时间文本。"""
    text = clean_text(str(raw))
    if not text:
        return ""

    text = re.sub(r"^(最新)?(发布|发布时间|更新时间|更新于|发布于)[:：]?\s*", "", text)
    return clean_text(text)


def extract_publish_time_from_any(value: Any) -> str:
    """从任意值中提取可展示的发布时间字符串。"""
    if value is None:
        return ""

    if isinstance(value, (int, float)):
        ts = int(value)
        try:
            # 兼容秒级/毫秒级时间戳。
            if ts > 10**12:
                parsed = dt.datetime.fromtimestamp(ts / 1000)
                return parsed.strftime("%Y-%m-%d %H:%M")
            if ts > 10**9:
                parsed = dt.datetime.fromtimestamp(ts)
                return parsed.strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            pass
        return normalize_publish_time_text(value)

    if isinstance(value, dict):
        for key in ["desc", "text", "name", "value", "time", "date", "updateTime", "publishTime"]:
            extracted = extract_publish_time_from_any(value.get(key))
            if extracted:
                return extracted
        for nested in value.values():
            extracted = extract_publish_time_from_any(nested)
            if extracted:
                return extracted
        return ""

    if isinstance(value, list):
        for nested in value:
            extracted = extract_publish_time_from_any(nested)
            if extracted:
                return extracted
        return ""

    return normalize_publish_time_text(value)


def looks_like_publish_time(text: str) -> bool:
    """判断文本是否像发布时间。"""
    if not text:
        return False

    if re.search(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}(?:\s+\d{1,2}:\d{2})?", text):
        return True
    if re.search(r"\d{1,2}[./-]\d{1,2}(?:\s+\d{1,2}:\d{2})?", text):
        return True
    if re.search(r"\d+\s*(分钟前|小时前|天前)", text):
        return True
    if any(token in text for token in ["今天", "昨天", "刚刚", "发布", "更新"]):
        return True
    return False


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


def parse_publish_time_to_datetime(value: str) -> dt.datetime | None:
    """尽量将发布时间文本解析为 datetime，用于判断新旧。"""
    text = normalize_publish_time_text(value)
    if not text:
        return None

    now = dt.datetime.now()
    if text == "刚刚":
        return now
    if text == "今天":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if text == "昨天":
        return (now - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    minute_match = re.search(r"(\d+)\s*分钟前", text)
    if minute_match:
        return now - dt.timedelta(minutes=int(minute_match.group(1)))

    hour_match = re.search(r"(\d+)\s*小时前", text)
    if hour_match:
        return now - dt.timedelta(hours=int(hour_match.group(1)))

    day_match = re.search(r"(\d+)\s*天前", text)
    if day_match:
        return now - dt.timedelta(days=int(day_match.group(1)))

    patterns = [
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m-%d %H:%M",
        "%m/%d %H:%M",
        "%m-%d",
        "%m/%d",
    ]
    for pattern in patterns:
        try:
            parsed = dt.datetime.strptime(text, pattern)
            if pattern.startswith("%m"):
                parsed = parsed.replace(year=now.year)
            return parsed
        except ValueError:
            continue
    return None


def choose_latest_publish_time(current_value: str, new_value: str) -> str:
    """在现有时间与新时间之间尽量选择更新的时间。"""
    current_text = normalize_publish_time_text(current_value)
    new_text = normalize_publish_time_text(new_value)

    if not new_text:
        return current_text
    if not current_text:
        return new_text

    current_dt = parse_publish_time_to_datetime(current_text)
    new_dt = parse_publish_time_to_datetime(new_text)

    if current_dt and new_dt:
        return new_text if new_dt >= current_dt else current_text

    # 无法可靠比较时，默认保留本次新抓取值。
    return new_text if new_text != current_text else current_text


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

        tags = [
            clean_text(tag.get("name", ""))
            for tag in item.get("jobSkillTags", [])
            if clean_text(tag.get("name", ""))
        ]
        summary = item.get("jobSummary", "")
        detail_url = extract_detail_url_from_state_item(item)

        ability_desc = build_ability_desc(
            salary=salary,
            location=location,
            experience=experience,
            education=education,
            tags=tags,
            summary=summary,
        )
        latest_publish_time = extract_latest_publish_time_from_state_item(item)

        jobs.append(
            {
                "招聘单位名称": company_name,
                "岗位名称": job_name,
                "最新发布时间": latest_publish_time,
                "能力描述": ability_desc,
                "__工作城市": work_city,
                "__详情链接": detail_url,
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

        ability_desc = build_ability_desc(
            salary=salary,
            location=location,
            experience=experience,
            education=education,
            tags=tags,
            summary="",
        )
        latest_publish_time = extract_latest_publish_time_from_dom_card(card)

        jobs.append(
            {
                "招聘单位名称": company_name,
                "岗位名称": job_name,
                "最新发布时间": latest_publish_time,
                "能力描述": ability_desc,
                "__工作城市": location,
                "__详情链接": detail_url,
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


def parse_bool(value: Any, default: bool = False) -> bool:
    """解析布尔配置，支持字符串和数字。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def parse_positive_int(value: Any, default: int) -> int:
    """解析正整数配置。"""
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def parse_positive_float(value: Any, default: float) -> float:
    """解析正浮点数配置。"""
    try:
        parsed = float(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def parse_probability(value: Any, default: float) -> float:
    """解析概率值，范围限制在 [0, 1]。"""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default

    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed


def scale_retry_delay(
    delay_range: tuple[float, float],
    attempt: int,
    backoff_factor: float,
    max_seconds: float,
) -> tuple[float, float]:
    """按重试次数放大延时，降低连续重试触发风控的概率。"""
    if attempt <= 1:
        return delay_range

    base_low, base_high = delay_range
    multiplier = backoff_factor ** (attempt - 1)
    scaled_low = min(base_low * multiplier, max_seconds)
    scaled_high = min(base_high * multiplier, max_seconds)

    if scaled_low > scaled_high:
        scaled_low, scaled_high = scaled_high, scaled_low

    return scaled_low, scaled_high


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

            if full_summary:
                merged_desc = merge_summary_into_ability_desc(
                    ability_desc=str(item.get("能力描述", "")),
                    summary=full_summary,
                )
                if merged_desc != item.get("能力描述", ""):
                    item["能力描述"] = merged_desc
                    updated_count += 1

            if fetched_from_network:
                await human_sleep(*settings["delays"]["between_details"])
    finally:
        await detail_page.close()

    return updated_count


def load_env_config(env_path: Path) -> dict[str, Any]:
    """加载并解析 .env 文件。"""
    if env_path.exists():
        load_dotenv(env_path)
    else:
        print(f"警告：未找到 {env_path.name} 文件，将尝试使用环境变量。")

    raw_keywords = os.getenv("KEYWORDS", "").split(",")
    keywords = []
    seen = set()
    for k in raw_keywords:
        text = clean_text(k)
        if text and text not in seen:
            seen.add(text)
            keywords.append(text)
    
    if not keywords:
        raise ValueError("请在 .env 中配置 KEYWORDS（至少 1 个关键词）")

    raw_regions = os.getenv("REGIONS", "").split(",")
    regions = []
    seen = set()
    for r in raw_regions:
        text = clean_text(r)
        if text:
            normalized = text[:-1] if text.endswith("市") else text
            if normalized not in seen:
                seen.add(normalized)
                regions.append(normalized)

    if not regions:
        raw_default = os.getenv("DEFAULT_REGIONS", "北京,上海,广州,深圳,杭州").split(",")
        for r in raw_default:
            text = clean_text(r)
            if text:
                normalized = text[:-1] if text.endswith("市") else text
                if normalized not in seen:
                    seen.add(normalized)
                    regions.append(normalized)

    if not regions:
        raise ValueError("地区解析为空，请检查 .env 中的 REGIONS/DEFAULT_REGIONS")

    base_dir = env_path.parent
    output_dir_raw = clean_text(os.getenv("OUTPUT_DIR", "output")) or "output"
    output_dir_path = Path(output_dir_raw)
    if not output_dir_path.is_absolute():
        output_dir_path = (base_dir / output_dir_path).resolve()
    output_dir_path.mkdir(parents=True, exist_ok=True)
    
    def parse_delay_env(env_val: str, default: tuple[float, float]) -> tuple[float, float]:
        if not env_val:
            return default
        parts = env_val.split(",")
        if len(parts) != 2:
            return default
        try:
            low, high = float(parts[0]), float(parts[1])
            if low > high:
                low, high = high, low
            return low, high
        except ValueError:
            return default

    def parse_int_range_env(env_val: str, default: tuple[int, int]) -> tuple[int, int]:
        if not env_val:
            return default
        parts = env_val.split(",")
        if len(parts) != 2:
            return default
        try:
            low, high = int(parts[0]), int(parts[1])
            if low <= 0 or high <= 0:
                return default
            if low > high:
                low, high = high, low
            return low, high
        except ValueError:
            return default

    settings = {
        "keywords": keywords,
        "regions": regions,
        "max_pages_per_region": parse_positive_int(os.getenv("MAX_PAGES_PER_REGION", "5"), 5),
        "max_empty_page_retries": parse_positive_int(os.getenv("MAX_EMPTY_PAGE_RETRIES", "2"), 2),
        "headless": parse_bool(os.getenv("HEADLESS", "false"), False),
        "user_agent": clean_text(os.getenv("USER_AGENT", DEFAULT_CONFIG["user_agent"])) or DEFAULT_CONFIG["user_agent"],
        "viewport": {
            "width": parse_positive_int(os.getenv("VIEWPORT_WIDTH", "1400"), 1400),
            "height": parse_positive_int(os.getenv("VIEWPORT_HEIGHT", "900"), 900),
        },
        "delays": {
            "after_open_search": parse_delay_env(
                os.getenv("DELAY_AFTER_OPEN_SEARCH", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["after_open_search"]),
            ),
            "between_pages": parse_delay_env(
                os.getenv("DELAY_BETWEEN_PAGES", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["between_pages"]),
            ),
            "retry_reload": parse_delay_env(
                os.getenv("DELAY_RETRY_RELOAD", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["retry_reload"]),
            ),
            "between_tasks": parse_delay_env(
                os.getenv("DELAY_BETWEEN_TASKS", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["between_tasks"]),
            ),
            "before_next_page": parse_delay_env(
                os.getenv("DELAY_BEFORE_NEXT_PAGE", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["before_next_page"]),
            ),
            "after_next_page": parse_delay_env(
                os.getenv("DELAY_AFTER_NEXT_PAGE", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["after_next_page"]),
            ),
            "long_break": parse_delay_env(
                os.getenv("DELAY_LONG_BREAK", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["long_break"]),
            ),
            "before_open_detail": parse_delay_env(
                os.getenv("DELAY_BEFORE_OPEN_DETAIL", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["before_open_detail"]),
            ),
            "after_open_detail": parse_delay_env(
                os.getenv("DELAY_AFTER_OPEN_DETAIL", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["after_open_detail"]),
            ),
            "between_details": parse_delay_env(
                os.getenv("DELAY_BETWEEN_DETAILS", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["between_details"]),
            ),
            "detail_retry": parse_delay_env(
                os.getenv("DELAY_DETAIL_RETRY", ""),
                tuple(DEFAULT_CONFIG["delay_seconds"]["detail_retry"]),
            ),
        },
        "typing_delay_ms": parse_int_range_env(
            os.getenv("TYPE_DELAY_MS", ""),
            tuple(DEFAULT_CONFIG["typing_delay_ms"]),
        ),
        "retry_backoff_factor": parse_positive_float(
            os.getenv("RETRY_BACKOFF_FACTOR", str(DEFAULT_CONFIG["retry_backoff_factor"])),
            float(DEFAULT_CONFIG["retry_backoff_factor"]),
        ),
        "max_retry_delay_seconds": parse_positive_float(
            os.getenv("MAX_RETRY_DELAY_SECONDS", str(DEFAULT_CONFIG["max_retry_delay_seconds"])),
            float(DEFAULT_CONFIG["max_retry_delay_seconds"]),
        ),
        "max_detail_retries": parse_positive_int(
            os.getenv("MAX_DETAIL_RETRIES", str(DEFAULT_CONFIG["max_detail_retries"])),
            int(DEFAULT_CONFIG["max_detail_retries"]),
        ),
        "detail_page_timeout_ms": parse_positive_int(
            os.getenv("DETAIL_PAGE_TIMEOUT_MS", str(DEFAULT_CONFIG["detail_page_timeout_ms"])),
            int(DEFAULT_CONFIG["detail_page_timeout_ms"]),
        ),
        "long_break_every_pages": parse_positive_int(
            os.getenv("LONG_BREAK_EVERY_PAGES", str(DEFAULT_CONFIG["long_break_every_pages"])),
            int(DEFAULT_CONFIG["long_break_every_pages"]),
        ),
        "long_break_probability": parse_probability(
            os.getenv("LONG_BREAK_PROBABILITY", str(DEFAULT_CONFIG["long_break_probability"])),
            float(DEFAULT_CONFIG["long_break_probability"]),
        ),
        "output_dir": output_dir_path,
    }
    return settings


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
) -> list[dict]:
    """爬取智联招聘岗位数据并返回列表。"""
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

                page_jobs = raw_page_jobs
                detail_updated = await enrich_jobs_with_detail_summaries(
                    context=context,
                    jobs=page_jobs,
                    settings=settings,
                    summary_cache=detail_summary_cache,
                )

                new_count = 0
                for item in page_jobs:
                    key = (
                        item["招聘单位名称"],
                        item["岗位名称"],
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    jobs.append(item)
                    new_count += 1

                print(
                    f"第 {current_page} 页：解析 {len(raw_page_jobs)} 条，详情补全 {detail_updated} 条，"
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


def sanitize_filename(name: str, fallback: str = "未知岗位") -> str:
    """清理非法文件名字符，确保可写入本地文件系统。"""
    sanitized = re.sub(r"[\\/:*?\"<>|]", "_", clean_text(name))
    sanitized = sanitized.strip().rstrip(".")
    return sanitized or fallback


def merge_distinct_text(current_value: str, new_value: str) -> str:
    """合并“、”分隔的文本，去重并保留顺序。"""
    merged = []
    seen = set()

    for raw in (current_value, new_value):
        text = clean_text(raw)
        if not text:
            continue

        for part in text.split("、"):
            item = clean_text(part)
            if not item or item in seen:
                continue
            seen.add(item)
            merged.append(item)

    return "、".join(merged)


def normalize_job_record(item: dict[str, Any]) -> dict[str, str]:
    """标准化岗位记录字段。"""
    record = {}
    for column in OUTPUT_COLUMNS:
        raw_value = str(item.get(column, ""))
        if column == "能力描述":
            record[column] = clean_multiline_text(raw_value)
        else:
            record[column] = clean_text(raw_value)

    if not record["招聘单位名称"]:
        record["招聘单位名称"] = "未知单位"
    if not record["岗位名称"]:
        record["岗位名称"] = "未知岗位"
    record["最新发布时间"] = normalize_publish_time_text(record["最新发布时间"]) or "未知"

    return record


def load_existing_job_records(file_path: Path) -> list[dict[str, str]]:
    """读取已存在的岗位 Excel 记录。"""
    if not file_path.exists():
        return []

    try:
        df = pd.read_excel(file_path, dtype=str, engine="openpyxl").fillna("")
    except Exception as exc:
        print(f"警告：读取现有文件失败，将以新数据重建：{file_path}，原因：{exc}")
        return []

    if df.empty:
        return []

    if "序号" in df.columns:
        df = df.drop(columns=["序号"])

    records = []
    for _, row in df.iterrows():
        records.append(normalize_job_record(row.to_dict()))
    return records


def merge_job_records(
    existing_records: list[dict[str, str]],
    new_records: list[dict[str, str]],
) -> tuple[list[dict[str, str]], int, int]:
    """按“公司 + 岗位”去重合并。描述变化则更新，不存在则追加。"""
    merged_map: dict[tuple[str, str], dict[str, str]] = {}
    ordered_keys: list[tuple[str, str]] = []

    for record in existing_records:
        normalized = normalize_job_record(record)
        key = (normalized["招聘单位名称"], normalized["岗位名称"])
        if key in merged_map:
            continue
        merged_map[key] = normalized
        ordered_keys.append(key)

    appended_count = 0
    updated_count = 0

    for record in new_records:
        normalized = normalize_job_record(record)
        key = (normalized["招聘单位名称"], normalized["岗位名称"])

        if key not in merged_map:
            merged_map[key] = normalized
            ordered_keys.append(key)
            appended_count += 1
            continue

        current = merged_map[key]
        changed = False

        merged_publish_time = choose_latest_publish_time(
            current["最新发布时间"],
            normalized["最新发布时间"],
        )
        if merged_publish_time != current["最新发布时间"]:
            current["最新发布时间"] = merged_publish_time
            changed = True

        if normalized["能力描述"] and normalized["能力描述"] != current["能力描述"]:
            current["能力描述"] = normalized["能力描述"]
            changed = True

        if changed:
            updated_count += 1

    merged_records = [merged_map[key] for key in ordered_keys]
    return merged_records, appended_count, updated_count


def write_job_records_to_excel(file_path: Path, records: list[dict[str, str]]) -> None:
    """将岗位记录写入 Excel，序号每次按当前文件重排。"""
    df = pd.DataFrame(records)

    for column in OUTPUT_COLUMNS:
        if column not in df.columns:
            df[column] = ""

    df = df[OUTPUT_COLUMNS]
    df.insert(0, "序号", range(1, len(df) + 1))
    df.to_excel(file_path, index=False, engine="openpyxl")


def save_jobs_by_keyword(jobs: list[dict], output_dir: Path, keyword: str) -> dict[str, Any]:
    """按用户输入关键词分文件保存，并执行增量去重更新。"""
    if not jobs:
        return {
            "file_count": 0,
            "raw_count": 0,
            "appended_count": 0,
            "updated_count": 0,
            "saved_files": [],
        }

    base_name = sanitize_filename(keyword, fallback="未知关键词")
    file_name = f"{base_name}.xlsx"
    file_path = output_dir / file_name

    normalized_jobs = [normalize_job_record(item) for item in jobs]
    existing_records = load_existing_job_records(file_path)
    merged_records, appended_count, updated_count = merge_job_records(
        existing_records=existing_records,
        new_records=normalized_jobs,
    )

    write_job_records_to_excel(file_path, merged_records)
    print(
        f"关键词《{keyword}》写入完成：新增 {appended_count} 条，"
        f"更新 {updated_count} 条，当前共 {len(merged_records)} 条 -> {file_path}"
    )

    return {
        "file_count": 1,
        "raw_count": len(jobs),
        "appended_count": appended_count,
        "updated_count": updated_count,
        "saved_files": [str(file_path)],
    }


def print_config_summary(settings: dict[str, Any], env_path: Path) -> None:
    """打印本次任务配置摘要。"""
    keywords = "、".join(settings["keywords"])
    regions = "、".join(settings["regions"])

    print(f"已加载配置：{env_path}")
    print(f"关键词：{keywords}")
    print(f"地区：{regions}")
    print(f"每个地区最大页数：{settings['max_pages_per_region']}")
    print(f"浏览器无头模式：{settings['headless']}")
    print(f"翻页延时（秒）：{settings['delays']['between_pages'][0]} - {settings['delays']['between_pages'][1]}")
    print(
        f"详情页延时（秒）：{settings['delays']['before_open_detail'][0]} - "
        f"{settings['delays']['after_open_detail'][1]}"
    )
    print(f"任务冷却（秒）：{settings['delays']['between_tasks'][0]} - {settings['delays']['between_tasks'][1]}")
    print(f"随机长暂停概率：{settings['long_break_probability']}")
    print(f"详情页最大重试次数：{settings['max_detail_retries']}")
    print(f"输出目录：{settings['output_dir']}")
    print(f"批量任务数：{len(settings['keywords']) * len(settings['regions'])}")

async def main() -> None:
    script_dir = Path(__file__).resolve().parent
    env_path = script_dir / ENV_FILE_NAME

    try:
        settings = load_env_config(env_path)
    except Exception as exc:
        print(f"配置加载失败：{exc}")
        print(f"请检查 {env_path} 后重试。")
        return

    print("提示：程序将自动打开浏览器抓取智联招聘数据。")
    print_config_summary(settings, env_path)

    total_tasks = len(settings["keywords"]) * len(settings["regions"])
    current_task = 0
    saved_file_count = 0
    raw_total = 0
    appended_total = 0
    updated_total = 0
    saved_files: list[str] = []

    for keyword in settings["keywords"]:
        keyword_jobs = []
        for city in settings["regions"]:
            current_task += 1
            print(f"\n任务进度：{current_task}/{total_tasks}（关键词={keyword}，地区={city}）")
            region_jobs = await crawl_zhaopin(
                keyword=keyword,
                city=city,
                settings=settings,
            )
            keyword_jobs.extend(region_jobs)

            if current_task < total_tasks:
                await human_sleep(*settings["delays"]["between_tasks"])

        if not keyword_jobs:
            print(f"\n关键词《{keyword}》未抓取到数据，已跳过写入。")
            continue

        keyword_summary = save_jobs_by_keyword(
            keyword_jobs,
            output_dir=settings["output_dir"],
            keyword=keyword,
        )
        saved_file_count += keyword_summary["file_count"]
        raw_total += keyword_summary["raw_count"]
        appended_total += keyword_summary["appended_count"]
        updated_total += keyword_summary["updated_count"]
        saved_files.extend(keyword_summary["saved_files"])

    if not saved_file_count:
        print("未抓取到数据，请稍后重试或检查配置。")
        return

    print(
        f"\n爬取完成！原始抓取 {raw_total} 条，"
        f"共写入 {saved_file_count} 个关键词文件。"
    )
    print(
        f"本次增量结果：新增 {appended_total} 条，"
        f"更新 {updated_total} 条。"
    )
    print(f"输出目录：{settings['output_dir']}")
    print("表格列：序号 | 招聘单位名称 | 岗位名称 | 最新发布时间 | 能力描述")
    if saved_files:
        print("文件列表：")
        for path in saved_files:
            print(f"- {path}")


if __name__ == "__main__":
    asyncio.run(main())
