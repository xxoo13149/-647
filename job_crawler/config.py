import argparse
import copy
import os
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from .constants import DEFAULT_CONFIG
from .utils import (
    clean_text,
    parse_bool,
    parse_csv_arg,
    parse_positive_float,
    parse_positive_int,
    parse_probability,
)


def parse_cli_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析本次运行的命令行覆盖项。"""
    parser = argparse.ArgumentParser(
        description="按关键词和城市抓取智联招聘岗位，并导出为岗位信息表格式。"
    )
    parser.add_argument(
        "--keywords",
        help="本次运行的岗位关键词，多个值用英文逗号分隔，例如：Java开发,软件测试",
    )
    parser.add_argument(
        "--regions",
        help="本次运行的城市/地区，多个值用英文逗号分隔，例如：北京,上海",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="每个“关键词 + 城市”最多抓取页数，例如：3",
    )
    parser.add_argument(
        "--output-dir",
        help="输出目录；相对路径会基于项目根目录解析。",
    )
    parser.add_argument(
        "--crawled-links-dir",
        help="已爬取详情链接的文本存储目录；相对路径会基于项目根目录解析。",
    )
    parser.add_argument(
        "--platform",
        choices=["zhaopin", "51job"],
        help="招聘平台：zhaopin 或 51job。默认读取 .env 的 PLATFORM，未配置则为 zhaopin。",
    )
    parser.add_argument(
        "--browser-backend",
        choices=["playwright", "scrapling", "gologin", "adspower", "orbita_cdp"],
        help="浏览器后端：playwright、scrapling、gologin、adspower 或 orbita_cdp。",
    )
    parser.add_argument(
        "--manual-auth",
        action="store_true",
        help="Show a real browser and pause for manual verification when the selected platform needs it.",
    )
    parser.add_argument(
        "--login-51job",
        action="store_true",
        help="只打开 51job 登录页面，等待人工用手机号/短信验证码登录，并保存浏览器用户目录。",
    )
    parser.add_argument(
        "--login-zhaopin",
        action="store_true",
        help="Open Zhaopin in a real browser, wait for manual verification/login, and save the browser profile.",
    )
    parser.add_argument(
        "--auth-wait-seconds",
        type=int,
        help="人工登录等待秒数，默认 120 秒。",
    )
    parser.add_argument(
        "--auth-state",
        help="兼容旧参数：保留但不推荐；51job 现在使用 --user-data-dir 保存真实浏览器 Profile。",
    )
    parser.add_argument(
        "--user-data-dir",
        help="51job 真实登录浏览器用户目录，默认 auth/51job_profile。",
    )

    parser.add_argument(
        "--zhaopin-user-data-dir",
        help="Zhaopin browser profile directory. Defaults to auth/zhaopin_profile.",
    )

    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument(
        "--headless",
        action="store_true",
        help="本次运行启用无头浏览器模式。",
    )
    headless_group.add_argument(
        "--headed",
        action="store_true",
        help="本次运行显示浏览器窗口，便于调试。",
    )

    parser.add_argument(
        "--max-empty-retries",
        type=int,
        help="列表页解析为空时的最大重试次数。",
    )
    parser.add_argument(
        "--max-detail-retries",
        type=int,
        help="详情页抓取失败时的最大重试次数。",
    )
    parser.add_argument(
        "--skip-detail-fetch",
        action="store_true",
        help="稳妥模式：只使用列表页字段导出，跳过详情页抓取，降低触发验证风险。",
    )
    return parser.parse_args(argv)


def apply_cli_overrides(
    settings: dict[str, Any],
    args: argparse.Namespace,
    base_dir: Path,
) -> dict[str, Any]:
    """用命令行参数覆盖 .env 配置。"""
    merged = copy.deepcopy(settings)

    if args.keywords:
        keywords = parse_csv_arg(args.keywords)
        if not keywords:
            raise ValueError("--keywords 至少需要包含 1 个非空关键词")
        merged["keywords"] = keywords

    if args.regions:
        regions = [
            text[:-1] if text.endswith("市") else text
            for text in parse_csv_arg(args.regions)
        ]
        if not regions:
            raise ValueError("--regions 至少需要包含 1 个非空城市/地区")
        merged["regions"] = regions

    if args.max_pages is not None:
        if args.max_pages <= 0:
            raise ValueError("--max-pages 必须是正整数")
        merged["max_pages_per_region"] = args.max_pages

    if args.max_empty_retries is not None:
        if args.max_empty_retries <= 0:
            raise ValueError("--max-empty-retries 必须是正整数")
        merged["max_empty_page_retries"] = args.max_empty_retries

    if args.max_detail_retries is not None:
        if args.max_detail_retries <= 0:
            raise ValueError("--max-detail-retries 必须是正整数")
        merged["max_detail_retries"] = args.max_detail_retries

    if args.skip_detail_fetch:
        merged["skip_detail_fetch"] = True

    if args.headless:
        merged["headless"] = True
    if args.headed:
        merged["headless"] = False

    if args.output_dir:
        output_dir = Path(clean_text(args.output_dir))
        if not output_dir.is_absolute():
            output_dir = (base_dir / output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        merged["output_dir"] = output_dir

    if args.crawled_links_dir:
        crawled_links_dir = Path(clean_text(args.crawled_links_dir))
        if not crawled_links_dir.is_absolute():
            crawled_links_dir = (base_dir / crawled_links_dir).resolve()
        crawled_links_dir.mkdir(parents=True, exist_ok=True)
        merged["crawled_links_dir"] = crawled_links_dir

    if args.platform:
        merged["platform"] = args.platform

    if args.browser_backend:
        merged["browser_backend"] = args.browser_backend

    if args.manual_auth:
        merged["manual_auth"] = True
        merged["headless"] = False
        if merged.get("platform") == "51job":
            merged["login_51job"] = True

    if args.login_51job:
        merged["login_51job"] = True
        merged["platform"] = "51job"
        merged["headless"] = False

    if args.login_zhaopin:
        merged["login_zhaopin"] = True
        merged["platform"] = "zhaopin"
        merged["manual_auth"] = True
        merged["headless"] = False

    if args.auth_wait_seconds is not None:
        if args.auth_wait_seconds <= 0:
            raise ValueError("--auth-wait-seconds 必须是正整数")
        merged["auth_wait_seconds"] = args.auth_wait_seconds

    if args.auth_state:
        auth_state_path = Path(clean_text(args.auth_state))
        if not auth_state_path.is_absolute():
            auth_state_path = (base_dir / auth_state_path).resolve()
        merged["auth_state_path"] = auth_state_path

    if args.user_data_dir:
        user_data_dir = Path(clean_text(args.user_data_dir))
        if not user_data_dir.is_absolute():
            user_data_dir = (base_dir / user_data_dir).resolve()
        merged["user_data_dir"] = user_data_dir

    if args.zhaopin_user_data_dir:
        zhaopin_user_data_dir = Path(clean_text(args.zhaopin_user_data_dir))
        if not zhaopin_user_data_dir.is_absolute():
            zhaopin_user_data_dir = (base_dir / zhaopin_user_data_dir).resolve()
        merged["zhaopin_user_data_dir"] = zhaopin_user_data_dir

    browser_backend = clean_text(str(os.getenv("BROWSER_BACKEND", merged.get("browser_backend", "playwright")))).lower()
    if browser_backend:
        merged["browser_backend"] = browser_backend

    return merged


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

    crawled_links_dir_raw = clean_text(os.getenv("CRAWLED_LINKS_DIR", ""))
    crawled_links_dir = Path(crawled_links_dir_raw) if crawled_links_dir_raw else output_dir_path / "crawled_links"
    if not crawled_links_dir.is_absolute():
        crawled_links_dir = (base_dir / crawled_links_dir).resolve()
    crawled_links_dir.mkdir(parents=True, exist_ok=True)

    auth_state_raw = clean_text(os.getenv("AUTH_STATE_PATH", DEFAULT_CONFIG["auth_state_path"]))
    auth_state_path = Path(auth_state_raw)
    if not auth_state_path.is_absolute():
        auth_state_path = (base_dir / auth_state_path).resolve()

    user_data_dir_raw = clean_text(os.getenv("USER_DATA_DIR", DEFAULT_CONFIG["user_data_dir"]))
    user_data_dir = Path(user_data_dir_raw)
    if not user_data_dir.is_absolute():
        user_data_dir = (base_dir / user_data_dir).resolve()

    zhaopin_user_data_dir_raw = clean_text(
        os.getenv("ZHAOPIN_USER_DATA_DIR", DEFAULT_CONFIG["zhaopin_user_data_dir"])
    )
    zhaopin_user_data_dir = Path(zhaopin_user_data_dir_raw)
    if not zhaopin_user_data_dir.is_absolute():
        zhaopin_user_data_dir = (base_dir / zhaopin_user_data_dir).resolve()

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
        "crawled_links_dir": crawled_links_dir,
        "platform": clean_text(os.getenv("PLATFORM", DEFAULT_CONFIG["platform"])).lower()
        or DEFAULT_CONFIG["platform"],
        "manual_auth": parse_bool(
            os.getenv("MANUAL_AUTH", str(DEFAULT_CONFIG["manual_auth"])),
            bool(DEFAULT_CONFIG["manual_auth"]),
        ),
        "auth_wait_seconds": parse_positive_int(
            os.getenv("AUTH_WAIT_SECONDS", str(DEFAULT_CONFIG["auth_wait_seconds"])),
            int(DEFAULT_CONFIG["auth_wait_seconds"]),
        ),
        "auth_state_path": auth_state_path,
        "login_51job": parse_bool(
            os.getenv("LOGIN_51JOB", str(DEFAULT_CONFIG["login_51job"])),
            bool(DEFAULT_CONFIG["login_51job"]),
        ),
        "user_data_dir": user_data_dir,
        "login_zhaopin": parse_bool(
            os.getenv("LOGIN_ZHAOPIN", str(DEFAULT_CONFIG["login_zhaopin"])),
            bool(DEFAULT_CONFIG["login_zhaopin"]),
        ),
        "zhaopin_user_data_dir": zhaopin_user_data_dir,
        "browser_backend": clean_text(os.getenv("BROWSER_BACKEND", str(DEFAULT_CONFIG["browser_backend"]))).lower()
        or str(DEFAULT_CONFIG["browser_backend"]),
        "gologin_token": clean_text(os.getenv("GOLOGIN_TOKEN", DEFAULT_CONFIG["gologin_token"])),
        "gologin_profile_id": clean_text(os.getenv("GOLOGIN_PROFILE_ID", DEFAULT_CONFIG["gologin_profile_id"])),
        "adspower_api_key": clean_text(os.getenv("ADSPOWER_API_KEY", DEFAULT_CONFIG["adspower_api_key"])),
        "adspower_api_port": clean_text(os.getenv("ADSPOWER_API_PORT", DEFAULT_CONFIG["adspower_api_port"])),
        "scrapling_real_chrome": parse_bool(
            os.getenv("SCRAPLING_REAL_CHROME", str(DEFAULT_CONFIG["scrapling_real_chrome"])),
            bool(DEFAULT_CONFIG["scrapling_real_chrome"]),
        ),
        "scrapling_google_search": parse_bool(
            os.getenv("SCRAPLING_GOOGLE_SEARCH", str(DEFAULT_CONFIG["scrapling_google_search"])),
            bool(DEFAULT_CONFIG["scrapling_google_search"]),
        ),
        "scrapling_block_webrtc": parse_bool(
            os.getenv("SCRAPLING_BLOCK_WEBRTC", str(DEFAULT_CONFIG["scrapling_block_webrtc"])),
            bool(DEFAULT_CONFIG["scrapling_block_webrtc"]),
        ),
        "scrapling_hide_canvas": parse_bool(
            os.getenv("SCRAPLING_HIDE_CANVAS", str(DEFAULT_CONFIG["scrapling_hide_canvas"])),
            bool(DEFAULT_CONFIG["scrapling_hide_canvas"]),
        ),
        "skip_detail_fetch": parse_bool(
            os.getenv("SKIP_DETAIL_FETCH", str(DEFAULT_CONFIG["skip_detail_fetch"])),
            bool(DEFAULT_CONFIG["skip_detail_fetch"]),
        ),
        "yescaptcha_api_key": clean_text(os.getenv("YESCAPTCHA_API_KEY", "")),
        "yescaptcha_proxy": clean_text(os.getenv("YESCAPTCHA_PROXY", "")),
    }
    if settings["platform"] not in {"zhaopin", "51job"}:
        raise ValueError("PLATFORM 仅支持 zhaopin 或 51job")
    if settings["browser_backend"] not in {"playwright", "scrapling", "gologin", "adspower", "orbita_cdp"}:
        raise ValueError("BROWSER_BACKEND 仅支持 playwright、scrapling、gologin、adspower 或 orbita_cdp")
    return settings


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
    print(f"招聘平台：{settings['platform']}")
    if settings["platform"] == "51job":
        print(f"51job 登录 Profile：{settings['user_data_dir']}")
        print(f"51job 登录初始化模式：{settings['login_51job']}")
    if settings["platform"] == "zhaopin":
        print(f"Zhaopin Profile: {settings['zhaopin_user_data_dir']}")
        print(f"Zhaopin login init mode: {settings['login_zhaopin']}")
    print(f"浏览器后端：{settings['browser_backend']}")
    if settings["browser_backend"] == "gologin":
        token_preview = settings.get("gologin_token", "")
        profile_id = settings.get("gologin_profile_id", "")
        if token_preview:
            print(f"Gologin Token：{token_preview[:8]}...（已配置）")
        if profile_id:
            print(f"Gologin Profile ID：{profile_id}")
        else:
            print("Gologin Profile：自动创建（每次启动生成新指纹）")
    if settings["browser_backend"] == "adspower":
        key = settings.get("adspower_api_key", "")
        port = settings.get("adspower_api_port", "50325")
        if key:
            print(f"AdsPower API：已配置（端口 {port}）")
        else:
            print(f"AdsPower：手动模式（端口 {port}）")
    if settings["browser_backend"] == "orbita_cdp":
        print("Orbita CDP：独立启动 + Playwright 远程连接（无自动化标识条）")
    print(f"稳妥模式（跳过详情页）：{settings['skip_detail_fetch']}")
    print(f"输出目录：{settings['output_dir']}")
    print(f"已爬取链接目录：{settings['crawled_links_dir']}")
    print(f"批量任务数：{len(settings['keywords']) * len(settings['regions'])}")
