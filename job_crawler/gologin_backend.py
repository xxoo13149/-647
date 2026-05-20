"""
Gologin 浏览器指纹集成模块。

支持两种模式（自动检测）：
  1. API 模式：配置了 GOLOGIN_TOKEN 时，通过 pygologin SDK 启动完整指纹 Profile
  2. 本地模式：无 Token 时，直接以本地 Orbita 浏览器作为 Playwright 引擎启动
     （Orbita 内置反检测，无需 API，无需付费）

工作原理：
  - API 模式：SDK 创建/获取 Profile → 启动 Orbita → Playwright 通过 CDP 接入
  - 本地模式：Playwright 以 Orbita chrome.exe 启动 Persistent Context，保留
    所有原有反检测措施 + Orbita 底层的指纹修改

需要配置的环境变量：
  - BROWSER_BACKEND=gologin  启用 Gologin 后端
  - GOLOGIN_TOKEN            API Token（可选，不填则自动走本地模式）
  - GOLOGIN_PROFILE_ID       Profile ID（可选，仅 API 模式有效）
"""
from __future__ import annotations

import asyncio
import logging
import glob as glob_mod
import os
import platform
from pathlib import Path
from typing import Any

from .utils import clean_text
from .stealth_js import STEALTH_INIT_SCRIPT

logger = logging.getLogger(__name__)

try:
    from gologin import GoLogin  # type: ignore[import-untyped]
except ImportError:
    GoLogin = None


# ---------------------------------------------------------------------------
# 公共判断函数
# ---------------------------------------------------------------------------

def gologin_api_available() -> bool:
    """检查 pygologin SDK 是否已安装。"""
    return GoLogin is not None


def using_gologin(settings: dict[str, Any]) -> bool:
    """判断当前是否启用了 Gologin 后端。"""
    backend = clean_text(str(settings.get("browser_backend", "playwright"))).lower()
    return backend == "gologin"


def using_gologin_api(settings: dict[str, Any]) -> bool:
    """判断是否启用 Gologin API 模式（有 Token 且 SDK 可用）。"""
    if not using_gologin(settings):
        return False
    if not gologin_api_available():
        return False
    token = clean_text(str(settings.get("gologin_token", "")))
    return bool(token)


# ---------------------------------------------------------------------------
# 本地 Orbita 浏览器检测
# ---------------------------------------------------------------------------

_ORBITA_CACHE: str | None = None


def find_orbita_executable() -> str | None:
    """自动搜索 Gologin 桌面版自带的 Orbita 浏览器路径。

    搜索顺序：~/.gologin/browser/ → Gologin 安装目录
    """
    global _ORBITA_CACHE
    if _ORBITA_CACHE is not None:
        return _ORBITA_CACHE

    candidates: list[Path] = []

    # 1) Gologin 下载的 Orbita 浏览器（最常见）
    home = Path.home()
    browser_dir = home / ".gologin" / "browser"
    if browser_dir.is_dir():
        for exe in browser_dir.rglob("chrome.exe"):
            candidates.append(exe)

    # 2) Gologin 桌面应用的安装目录
    if os.name == "nt":
        import winreg
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GoLogin",
            )
            install_location = winreg.QueryValueEx(key, "InstallLocation")[0]
            winreg.CloseKey(key)
            p = Path(install_location)
            if p.is_dir():
                for exe in p.rglob("chrome.exe"):
                    candidates.append(exe)
        except Exception:
            pass

        # 常见安装路径
        for loc in [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Gologin",
            Path(os.environ.get("PROGRAMFILES", "")) / "GoLogin",
        ]:
            if loc.is_dir():
                for exe in loc.rglob("chrome.exe"):
                    if exe not in candidates:
                        candidates.append(exe)

    for c in candidates:
        if c.is_file():
            _ORBITA_CACHE = str(c)
            logger.info("Gologin: found Orbita browser at %s", _ORBITA_CACHE)
            return _ORBITA_CACHE

    logger.warning("Gologin: Orbita browser not found. 请确保已安装 Gologin 桌面版。")
    return None


# ---------------------------------------------------------------------------
# API 模式（需要 Token + pygologin）
# ---------------------------------------------------------------------------

def _get_gologin_token(settings: dict[str, Any]) -> str:
    token = clean_text(str(settings.get("gologin_token", "")))
    if not token:
        raise RuntimeError(
            "Gologin API token 未配置。请在 .env 中设置 GOLOGIN_TOKEN。\n"
            "获取方式：登录 https://app.gologin.com → 个人中心 → API Token"
        )
    return token


def _get_gologin_profile_id(settings: dict[str, Any]) -> str | None:
    pid = clean_text(str(settings.get("gologin_profile_id", "")))
    return pid or None


def _get_os_for_gologin() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "win"
    elif system == "darwin":
        return "mac"
    else:
        return "lin"


def _build_gologin_options(settings: dict[str, Any]) -> dict[str, Any]:
    token = _get_gologin_token(settings)
    profile_id = _get_gologin_profile_id(settings)

    opts: dict[str, Any] = {
        "token": token,
        "local": True,
        "spawn_browser": True,
        "uploadCookiesToServer": False,
        "writeCookiesFromServer": False,
    }

    if profile_id:
        opts["profile_id"] = profile_id

    if settings.get("headless", False):
        opts["extra_params"] = ["--headless"]

    return opts


async def _ensure_gologin_profile(gl: Any, settings: dict[str, Any]) -> None:
    profile_id = _get_gologin_profile_id(settings)
    if profile_id:
        logger.info("Gologin API: using profile_id=%s", profile_id)
        return

    loop = asyncio.get_running_loop()
    os_name = _get_os_for_gologin()
    profile_name = f"sybg_crawler_{platform.node()}"
    logger.info("Gologin API: creating random fingerprint profile (os=%s)", os_name)
    profile = await loop.run_in_executor(
        None,
        lambda: gl.createProfileRandomFingerprint({"os": os_name, "name": profile_name}),
    )
    new_id = profile.get("id", "")
    if not new_id:
        raise RuntimeError("Gologin profile 创建失败。")
    gl.setProfileId(new_id)
    settings["_gologin_auto_profile_id"] = new_id
    logger.info("Gologin API: auto-created profile_id=%s", new_id)


async def launch_gologin_api_browser(
    playwright_instance: Any,
    settings: dict[str, Any],
) -> tuple[Any, Any, Any]:
    """API 模式：启动 Gologin Profile，通过 CDP 接入 Playwright。

    Returns: (browser, context, gl_instance)
    """
    if not gologin_api_available():
        raise RuntimeError("gologin (pygologin) 未安装。请运行：pip install gologin")

    opts = _build_gologin_options(settings)
    gl = GoLogin(opts)

    try:
        await _ensure_gologin_profile(gl, settings)

        loop = asyncio.get_running_loop()
        debugger_address: str = await loop.run_in_executor(None, gl.start)
        cdp_url = f"http://{debugger_address}"
        logger.info("Gologin API: browser started, CDP=%s", cdp_url)

        browser = await playwright_instance.chromium.connect_over_cdp(cdp_url)
        logger.info("Gologin API: Playwright connected via CDP")

        if len(browser.contexts) == 0:
            raise RuntimeError("Gologin browser started but no context found.")
        context = browser.contexts[0]

        await context.add_init_script(STEALTH_INIT_SCRIPT)

        return browser, context, gl

    except Exception:
        try:
            gl.stop()
        except Exception:
            pass
        raise


def stop_gologin_api(gl: Any) -> None:
    """停止 Gologin API session。"""
    try:
        gl.stop()
        logger.info("Gologin API: session stopped")
    except Exception as exc:
        logger.warning("Gologin API: stop failed (ignored): %s", exc)


# ---------------------------------------------------------------------------
# 统一入口：自动选择 API 或本地模式
# ---------------------------------------------------------------------------

async def launch_gologin_browser(
    playwright_instance: Any,
    settings: dict[str, Any],
) -> tuple[Any, Any, Any]:
    """智能入口：有 Token 用 API 模式，否则用本地 Orbita 模式。

    Returns:
        (browser, context, gl_or_None)
        - API 模式: browser=Playwright Browser, context=Context, gl_or_None=GoLogin
        - 本地模式: browser=None, context=BrowserContext, gl_or_None=None

    调用方规范:
        context = ...  # 统一用 context
        gl = ...
        try:
            # 爬虫逻辑
        finally:
            await context.close()  # 两种模式都需要
            if gl is not None:
                stop_gologin_api(gl)
    """
    if using_gologin_api(settings):
        logger.info("Gologin: API 模式")
        return await launch_gologin_api_browser(playwright_instance, settings)

    # 本地模式：返回 browser=None, context 稍后由 launch_persistent_context_with_fallback 创建
    logger.info("Gologin: 本地模式（无 API Token，使用 Orbita 引擎）")
    return None, None, None


def get_gologin_executable_path() -> str | None:
    """获取 Orbita 浏览器的可执行文件路径（供 launch_persistent_context_with_fallback 使用）。"""
    return find_orbita_executable()
