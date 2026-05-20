"""
AdsPower 浏览器指纹集成模块。

工作原理：
1. 用户在 AdsPower 中手动打开一个 Profile（浏览器窗口会弹出）
2. 脚本自动扫描 localhost 上的 Chrome DevTools 端口
3. Playwright 通过 CDP 连接到已打开的 AdsPower 浏览器
4. 后续爬虫逻辑与原方案完全一致

需要配置的环境变量：
  - BROWSER_BACKEND=adspower  启用 AdsPower 后端
  - ADSPOWER_API_KEY          AdsPower Local API Key
  - ADSPOWER_API_PORT         AdsPower Local API 端口（默认 50325）
  - ADSPOWER_BROWSER_PORT     指定浏览器 CDP 端口（可选，自动扫描）
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

from .utils import clean_text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 公共判断
# ---------------------------------------------------------------------------

def using_adspower(settings: dict[str, Any]) -> bool:
    backend = clean_text(str(settings.get("browser_backend", "playwright"))).lower()
    return backend == "adspower"


def _get_api_key(settings: dict[str, Any]) -> str:
    return clean_text(str(settings.get("adspower_api_key", "")))


def _get_api_port(settings: dict[str, Any]) -> int:
    return int(settings.get("adspower_api_port", "50325") or "50325")


# ---------------------------------------------------------------------------
# AdsPower API 调用
# ---------------------------------------------------------------------------

def _ads_api(method: str, endpoint: str, settings: dict[str, Any], body: dict | None = None) -> dict:
    key = _get_api_key(settings)
    port = _get_api_port(settings)
    url = f"http://local.adspower.net:{port}{endpoint}"
    data = json.dumps(body).encode() if body else None

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"code": -1, "msg": f"HTTP {e.code}", "_error": str(e)}
    except Exception as e:
        return {"code": -1, "msg": str(e), "_error": str(e)}


def ads_api_healthy(settings: dict[str, Any]) -> bool:
    r = _ads_api("GET", "/status", settings)
    return r.get("code") == 0


def ads_list_profiles(settings: dict[str, Any]) -> list[dict]:
    r = _ads_api("GET", "/api/v1/user/list", settings)
    return r.get("data", {}).get("list", [])


def ads_start_browser_api(settings: dict[str, Any], user_id: str = "") -> dict:
    """通过 API 启动 AdsPower 浏览器。返回包含 ws 地址的 dict。"""
    profiles = ads_list_profiles(settings)
    if not user_id and profiles:
        user_id = profiles[0].get("user_id", "")
    if not user_id:
        return {"code": -1, "msg": "No AdsPower profile found"}

    logger.info("AdsPower: starting profile %s via API", user_id)
    return _ads_api("POST", "/api/v1/browser/start", settings, {"user_id": user_id})


# ---------------------------------------------------------------------------
# CDP 端口自动扫描
# ---------------------------------------------------------------------------

def _scan_cdp_ports() -> list[int]:
    """扫描 localhost 上运行中的 Chrome DevTools 端口。"""
    ports = []
    for port in range(9222, 9322):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                try:
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version")
                    with urllib.request.urlopen(req, timeout=1) as r:
                        data = json.loads(r.read().decode())
                        if "Browser" in data.get("Browser", ""):
                            ports.append(port)
                            logger.info("AdsPower: found CDP at port %d (%s)", port, data.get("Browser", "")[:50])
                except Exception:
                    pass
        except (socket.timeout, ConnectionRefusedError, OSError):
            pass
    return ports


def find_adspower_cdp_port() -> str | None:
    """扫描并找一个可用的 AdsPower/Chrome CDP 端口。"""
    ports = _scan_cdp_ports()
    if ports:
        return f"http://127.0.0.1:{ports[0]}"
    return None


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

async def launch_adspower_browser(
    playwright_instance: Any,
    settings: dict[str, Any],
) -> tuple[Any, Any, Any]:
    """启动/连接 AdsPower 浏览器。

    优先尝试 API 启动；失败则要求用户手动打开 Profile，然后自动扫描 CDP 端口。

    Returns:
        (browser, context, None) — 调用方负责 await context.close()
    """
    ws_url = ""

    # 尝试 API
    result = ads_start_browser_api(settings)
    if result.get("code") == 0:
        ws_url = result.get("data", {}).get("ws", "")
        if ws_url:
            logger.info("AdsPower: API returned ws=%s", ws_url[:60])

    # API 失败 — 等待用户手动打开，然后扫描
    if not ws_url:
        print("\n" + "=" * 60)
        print("请在 AdsPower 中手动打开一个浏览器 Profile。")
        print("打开后程序将自动扫描连接。")
        print("=" * 60 + "\n")

        for attempt in range(30):
            await asyncio.sleep(2)
            cdp = find_adspower_cdp_port()
            if cdp:
                # 从 /json/version 获取 WebSocket URL
                try:
                    req = urllib.request.Request(f"{cdp}/json/version")
                    with urllib.request.urlopen(req, timeout=3) as r:
                        data = json.loads(r.read().decode())
                        ws_url = data.get("webSocketDebuggerUrl", "")
                        if ws_url:
                            logger.info("AdsPower: manual CDP connected: %s", ws_url[:60])
                            break
                except Exception:
                    pass
            if attempt % 5 == 4:
                print(f"  等待中... (已等 {(attempt+1)*2} 秒)")

    if not ws_url:
        raise RuntimeError("无法连接到 AdsPower 浏览器。请确保已在 AdsPower 中打开一个 Profile。")

    # Playwright 通过 CDP 连接
    browser = await playwright_instance.chromium.connect_over_cdp(ws_url)
    logger.info("AdsPower: Playwright connected via CDP")
    contexts = browser.contexts
    if not contexts:
        raise RuntimeError("AdsPower browser has no context")
    context = contexts[0]

    return browser, context, None
