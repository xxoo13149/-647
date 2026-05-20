from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .utils import clean_text
from .stealth_js import STEALTH_INIT_SCRIPT

try:
    from scrapling.fetchers import DynamicFetcher, StealthyFetcher
except ImportError:
    DynamicFetcher = None
    StealthyFetcher = None


def using_scrapling(settings: dict[str, Any]) -> bool:
    backend = clean_text(str(settings.get("browser_backend", "playwright"))).lower()
    return backend == "scrapling"


def using_gologin(settings: dict[str, Any]) -> bool:
    """判断当前是否启用了 Gologin 后端。"""
    backend = clean_text(str(settings.get("browser_backend", "playwright"))).lower()
    return backend == "gologin"


def using_adspower(settings: dict[str, Any]) -> bool:
    """判断当前是否启用了 AdsPower 后端。"""
    backend = clean_text(str(settings.get("browser_backend", "playwright"))).lower()
    return backend == "adspower"


def using_orbita_cdp(settings: dict[str, Any]) -> bool:
    """判断当前是否启用了 Orbita CDP 后端（无自动化标识条）。"""
    backend = clean_text(str(settings.get("browser_backend", "playwright"))).lower()
    return backend == "orbita_cdp"


def _orbita_launch_args(executable_path: str | None = None) -> list[str]:
    """Orbita 浏览器启动参数（最少化，Orbita 二进制已内置完整反检测）。"""
    return [
        "--excludeSwitches=enable-automation",
        "--password-store=basic",
        "--use-mock-keychain",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def _default_launch_args(executable_path: str | None = None) -> list[str]:
    """标准 Chrome 启动参数（含 AutomationControlled 禁用）。"""
    args = ["--disable-blink-features=AutomationControlled"]
    if executable_path:
        # Orbita：使用专用参数
        return _orbita_launch_args(executable_path)
    return args


def scrapling_available() -> bool:
    return DynamicFetcher is not None and StealthyFetcher is not None


def preferred_playwright_channel() -> str | None:
    channel = clean_text(os.getenv("PLAYWRIGHT_BROWSER_CHANNEL", "")).lower()
    if channel:
        return channel
    if os.name == "nt":
        return "msedge"
    return None


async def launch_persistent_context_with_fallback(
    browser_type,
    *,
    user_data_dir: str | Path,
    headless: bool,
    **kwargs: Any,
):
    channels: list[str | None] = []
    # 从 kwargs 中提取 executable_path（如果有）
    executable_path = kwargs.pop("executable_path", None)
    # 提取并合并 args
    custom_args = list(kwargs.pop("args", []))
    has_orbita = bool(executable_path)
    launch_args = _orbita_launch_args() if has_orbita else _default_launch_args()
    # 用户自定义 args 追加在最后
    launch_args = launch_args + custom_args

    env_channel = preferred_playwright_channel()
    if env_channel and not has_orbita:
        channels.append(env_channel)
    if os.name == "nt" and not has_orbita:
        channels.extend(["msedge", "chrome"])
    channels.append(None)

    # 如果指定了 executable_path，跳过 channel 尝试
    if has_orbita:
        channels = [None]

    seen: set[str | None] = set()
    last_error: Exception | None = None

    for channel in channels:
        if channel in seen:
            continue
        seen.add(channel)
        launch_kwargs = dict(kwargs)
        if channel:
            launch_kwargs["channel"] = channel
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        launch_kwargs["args"] = launch_args
        try:
            return await browser_type.launch_persistent_context(
                user_data_dir=str(Path(user_data_dir)),
                headless=headless,
                **launch_kwargs,
            )
        except Exception as exc:
            last_error = exc
            continue

    raise last_error or RuntimeError("Failed to launch a persistent browser context.")


def build_scrapling_common_kwargs(
    settings: dict[str, Any],
    *,
    profile_dir: str | Path | None = None,
    wait_ms: int | None = None,
) -> dict[str, Any]:
    kwargs = {
        "headless": bool(settings.get("headless", True)),
        "timeout": int(settings.get("detail_page_timeout_ms", 90000)),
        "wait": wait_ms if wait_ms is not None else int(float(settings["delays"]["after_open_search"][1]) * 1000),
        "locale": "zh-CN",
        "useragent": clean_text(str(settings.get("user_agent", ""))) or None,
        "timezone_id": "Asia/Shanghai",
        "real_chrome": bool(settings.get("scrapling_real_chrome", False)),
        "google_search": bool(settings.get("scrapling_google_search", False)),
        "block_webrtc": bool(settings.get("scrapling_block_webrtc", True)),
        "hide_canvas": bool(settings.get("scrapling_hide_canvas", True)),
    }
    if profile_dir:
        kwargs["user_data_dir"] = str(Path(profile_dir))
    return kwargs


async def fetch_html_with_scrapling(
    url: str,
    settings: dict[str, Any],
    wait_selector: str = "",
    *,
    profile_dir: str | Path | None = None,
    wait_ms: int | None = None,
) -> str:
    if not scrapling_available():
        raise RuntimeError("Scrapling fetchers are not installed.")

    kwargs = build_scrapling_common_kwargs(settings, profile_dir=profile_dir, wait_ms=wait_ms)
    kwargs["load_dom"] = True
    kwargs["network_idle"] = True
    if wait_selector:
        kwargs["wait_selector"] = wait_selector
        kwargs["wait_selector_state"] = "attached"

    try:
        response = await StealthyFetcher.async_fetch(url, **kwargs)
    except Exception:
        if kwargs.get("real_chrome"):
            kwargs["real_chrome"] = False
            response = await StealthyFetcher.async_fetch(url, **kwargs)
        else:
            raise
    return response.html_content
