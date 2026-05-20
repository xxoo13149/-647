"""
Orbita CDP 后端：先以普通方式启动 Orbita 浏览器（无自动化标识条），
Playwright 再通过 CDP 远程连接接管，实现「肉眼看起来是手动打开，
但程序可以操控」的效果。
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

# 默认 CDP 端口范围
CDP_PORT_START = 9223
CDP_PORT_END = 9230


def _find_free_port(start: int = CDP_PORT_START, end: int = CDP_PORT_END) -> int:
    for port in range(start, end):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.2)
            s.connect(("127.0.0.1", port))
            s.close()
        except (socket.timeout, ConnectionRefusedError, OSError):
            return port
    return CDP_PORT_START


def _cdp_ready(port: int, timeout: float = 1.0) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
            return "webSocketDebuggerUrl" in data
    except Exception:
        return False


def _get_cdp_endpoint(port: int) -> str | None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version")
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read().decode())
            return data.get("webSocketDebuggerUrl")
    except Exception:
        return None


async def launch_orbita_browser(
    orbita_exe: str,
    user_data_dir: str | Path,
    cdp_port: int | None = None,
    headless: bool = False,
) -> tuple[subprocess.Popen, str]:
    """启动 Orbita 浏览器进程，返回 (进程句柄, CDP WebSocket URL)。
    
    非 Playwright 启动 => 无「受自动化控制」标识条。
    """
    port = cdp_port or _find_free_port()

    # 清理锁文件
    profile = Path(user_data_dir)
    for lock_name in ["lockfile", "SingletonLock", "SingletonSocket"]:
        (profile / lock_name).unlink(missing_ok=True)

    cmd = [
        orbita_exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--use-mock-keychain",
        "about:blank",
    ]
    if headless:
        cmd.append("--headless=new")

    logger.info("Orbita CDB: starting %s", " ".join(str(c) for c in cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 等待 CDP 就绪（最多 30 秒）
    for i in range(60):
        await asyncio.sleep(0.5)
        if _cdp_ready(port, timeout=0.5):
            break
    else:
        proc.terminate()
        raise RuntimeError(f"Orbita browser failed to start on CDP port {port}")

    ws_url = _get_cdp_endpoint(port)
    if not ws_url:
        proc.terminate()
        raise RuntimeError(f"Orbita CDP port {port} ready but no WebSocket URL")

    logger.info("Orbita CDP: ready at %s", ws_url[:60])
    # 给浏览器一点额外时间初始化
    await asyncio.sleep(1.0)
    return proc, ws_url


def stop_orbita_browser(proc: subprocess.Popen | None) -> None:
    """安全关闭 Orbita 浏览器进程。"""
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


async def connect_playwright_to_orbita(playwright_instance: Any, ws_url: str) -> tuple[Any, Any]:
    """Playwright 通过 CDP WebSocket 连接到已运行的 Orbita 浏览器。"""
    browser = await playwright_instance.chromium.connect_over_cdp(ws_url)
    contexts = browser.contexts
    if not contexts:
        context = await browser.new_context()
    else:
        context = contexts[0]
    logger.info("Playwright connected to Orbita via CDP")
    return browser, context
