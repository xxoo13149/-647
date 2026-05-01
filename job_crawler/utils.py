import asyncio
import datetime as dt
import random
import re
import urllib.parse
from typing import Any

import pandas as pd

from .constants import CITY_PROVINCE_MAP, EMPTY_CELL_VALUE


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


def fill_empty(value: Any) -> str:
    """将空值统一填充为模板要求的 /。"""
    if value is None:
        return EMPTY_CELL_VALUE
    if isinstance(value, float) and pd.isna(value):
        return EMPTY_CELL_VALUE

    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%Y-%m-%d")

    text = clean_multiline_text(str(value))
    return text if text else EMPTY_CELL_VALUE


def first_text_from_item(item: dict[str, Any], keys: list[str]) -> str:
    """按候选 key 从接口字段中提取第一个非空文本。"""
    for key in keys:
        value = item.get(key)
        text = extract_text_from_any(value)
        if text:
            return text
    return ""


def extract_text_from_any(value: Any) -> str:
    """兼容字符串、数字、字典、列表等常见字段形态。"""
    if value is None:
        return ""

    if isinstance(value, (str, int, float)):
        return clean_text(str(value))

    if isinstance(value, dict):
        for key in ["name", "text", "desc", "value", "label", "title"]:
            text = extract_text_from_any(value.get(key))
            if text:
                return text
        values = [extract_text_from_any(v) for v in value.values()]
        return " / ".join([x for x in values if x])

    if isinstance(value, list):
        values = [extract_text_from_any(v) for v in value]
        return " / ".join([x for x in values if x])

    return clean_text(str(value))


def extract_tag_names(value: Any) -> list[str]:
    """从标签列表中提取去重后的可展示文本。"""
    raw_text = extract_text_from_any(value)
    if not raw_text:
        return []

    tags = []
    seen = set()
    for part in re.split(r"[/,，、;；\n]+", raw_text):
        text = clean_text(part)
        if not text or text in seen:
            continue
        seen.add(text)
        tags.append(text)
    return tags


def infer_province(city: str) -> str:
    """根据城市名尽量推断省份，无法判断时留空。"""
    normalized = normalize_city_name(city)
    if not normalized:
        return ""
    return CITY_PROVINCE_MAP.get(normalized, "")


def split_job_summary(summary: str) -> tuple[str, str]:
    """将详情正文尽量拆分成工作内容与任职要求。"""
    text = clean_multiline_text(summary)
    if not text:
        return "", ""

    requirement_markers = [
        "任职要求",
        "岗位要求",
        "职位要求",
        "人员要求",
        "应聘要求",
        "任职资格",
        "资格要求",
        "岗位职责要求",
        "要求：",
        "要求:",
    ]
    for marker in requirement_markers:
        idx = text.find(marker)
        if idx <= 0:
            continue
        work = clean_multiline_text(text[:idx])
        requirement = clean_multiline_text(text[idx:])
        return work, requirement

    return text, ""


def format_tags(tags: list[str]) -> str:
    """格式化标签列表。"""
    cleaned = []
    seen = set()
    for tag in tags:
        text = clean_text(tag)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return " / ".join(cleaned)


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


def normalize_absolute_url(url: str, base_url: str = "https://www.zhaopin.com") -> str:
    """将相对链接、协议相对链接归一化为绝对链接。"""
    text = clean_text(url)
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    return urllib.parse.urljoin(base_url, text)


def looks_like_job_summary_text(text: str) -> bool:
    """判断文本是否像岗位详情摘要。"""
    cleaned = clean_text(text)
    if len(cleaned) < 60:
        return False
    if any(token in cleaned for token in ["职位描述", "岗位职责", "任职要求", "工作内容", "岗位要求"]):
        return True
    return len(cleaned) >= 180


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


def parse_csv_arg(value: str) -> list[str]:
    """解析命令行逗号分隔参数，去重并保留顺序。"""
    items = []
    seen = set()
    for part in value.split(","):
        text = clean_text(part)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


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


def extract_labeled_value(text: str, label: str) -> str:
    """从旧版“能力描述”中提取“标签：值”片段。"""
    pattern = rf"{re.escape(label)}[:：](.*?)(?=；(?:薪资|地点|经验|学历|技能标签|岗位摘要)[:：]|$)"
    match = re.search(pattern, text, re.S)
    if not match:
        return ""
    return clean_multiline_text(match.group(1))


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
