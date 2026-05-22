import asyncio
import concurrent.futures
import contextlib
import datetime as dt
import json
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, abort, jsonify, render_template, request, send_file

from .config import load_env_config
from .category_presets import expand_zhaopin_keyword_groups
from .constants import ENV_FILE_NAME, OUTPUT_COLUMNS
from .crawled_links import build_crawled_link_store
from .fiftyone import crawl_51job, login_51job_profile
from .output import save_jobs_by_keyword
from .utils import clean_text, parse_bool
from .zhaopin import crawl_zhaopin, login_zhaopin_profile


BASE_DIR = Path(__file__).resolve().parents[1]
TASKS_DIR = BASE_DIR / "web_tasks"
TASKS_DIR.mkdir(parents=True, exist_ok=True)
WEB_UI_CONFIG_PATH = TASKS_DIR / "ui_defaults.json"
UI_DEFAULT_KEYS = {
    "keywords",
    "regions",
    "platform",
    "browser_backend",
    "max_pages",
    "headless",
    "max_empty_retries",
    "max_detail_retries",
    "detail_timeout_ms",
    "delay_between_pages",
    "scrapling_real_chrome",
    "scrapling_google_search",
    "scrapling_block_webrtc",
    "scrapling_hide_canvas",
    "skip_detail_fetch",
    "refetch_crawled_details",
    "filter_existing_output_early",
}

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
tasks_lock = threading.RLock()
tasks: dict[str, "CrawlerTask"] = {}
auth_lock = threading.RLock()
auth_state: dict[str, Any] = {
    "status": "idle",
    "started_at": "",
    "finished_at": "",
    "error": "",
    "logs": [],
    "run_id": "",
}
zhaopin_auth_state: dict[str, Any] = {
    "status": "idle",
    "started_at": "",
    "finished_at": "",
    "error": "",
    "logs": [],
    "run_id": "",
}


def profile_ready(user_data_dir: Path) -> bool:
    return user_data_dir.exists() and any(user_data_dir.rglob("Cookies"))


def auth_log(message: str) -> None:
    timestamp = dt.datetime.now().strftime("%H:%M:%S")
    with auth_lock:
        auth_state["logs"].insert(0, f"[{timestamp}] {message}")
        auth_state["logs"] = auth_state["logs"][:120]


def zhaopin_auth_log(message: str) -> None:
    timestamp = dt.datetime.now().strftime("%H:%M:%S")
    with auth_lock:
        zhaopin_auth_state["logs"].insert(0, f"[{timestamp}] {message}")
        zhaopin_auth_state["logs"] = zhaopin_auth_state["logs"][:120]


def auth_state_payload() -> dict[str, Any]:
    try:
        settings = load_base_settings()
        user_data_dir = Path(settings["user_data_dir"])
        auth_wait_seconds = int(settings["auth_wait_seconds"])
    except Exception:
        user_data_dir = BASE_DIR / "auth" / "51job_profile"
        auth_wait_seconds = 120

    with auth_lock:
        payload = dict(auth_state)
        payload["logs"] = list(auth_state.get("logs") or [])
    payload["profile_ready"] = profile_ready(user_data_dir)
    payload["user_data_dir"] = str(user_data_dir)
    payload["auth_wait_seconds"] = auth_wait_seconds
    return payload


def zhaopin_auth_state_payload() -> dict[str, Any]:
    try:
        settings = load_base_settings()
        user_data_dir = Path(settings["zhaopin_user_data_dir"])
        auth_wait_seconds = int(settings["auth_wait_seconds"])
    except Exception:
        user_data_dir = BASE_DIR / "auth" / "zhaopin_profile"
        auth_wait_seconds = 120

    with auth_lock:
        payload = dict(zhaopin_auth_state)
        payload["logs"] = list(zhaopin_auth_state.get("logs") or [])
    payload["profile_ready"] = profile_ready(user_data_dir)
    payload["user_data_dir"] = str(user_data_dir)
    payload["auth_wait_seconds"] = auth_wait_seconds
    return payload


def run_51job_login(run_id: str) -> None:
    with auth_lock:
        auth_state["status"] = "running"
        auth_state["started_at"] = now_iso()
        auth_state["finished_at"] = ""
        auth_state["error"] = ""
        auth_state["logs"] = []
    try:
        settings = load_base_settings()
        settings["platform"] = "51job"
        settings["login_51job"] = True
        settings["manual_auth"] = True
        settings["headless"] = False
        auth_log("Opening 51job login browser. Finish login in the opened window.")
        asyncio.run(login_51job_profile(settings))
        with auth_lock:
            if auth_state.get("run_id") == run_id:
                auth_state["status"] = "completed"
                auth_state["finished_at"] = now_iso()
        auth_log("51job login browser closed. Profile has been saved.")
    except Exception as exc:
        with auth_lock:
            if auth_state.get("run_id") == run_id:
                auth_state["status"] = "failed"
                auth_state["finished_at"] = now_iso()
                auth_state["error"] = str(exc)
        auth_log(f"51job login failed: {exc}")


def run_zhaopin_login(run_id: str) -> None:
    with auth_lock:
        zhaopin_auth_state["status"] = "running"
        zhaopin_auth_state["started_at"] = now_iso()
        zhaopin_auth_state["finished_at"] = ""
        zhaopin_auth_state["error"] = ""
        zhaopin_auth_state["logs"] = []
    try:
        settings = load_base_settings()
        settings["platform"] = "zhaopin"
        settings["login_zhaopin"] = True
        settings["manual_auth"] = True
        settings["headless"] = False
        zhaopin_auth_log("Opening Zhaopin login browser. Finish login/verification in the opened window.")
        asyncio.run(login_zhaopin_profile(settings))
        with auth_lock:
            if zhaopin_auth_state.get("run_id") == run_id:
                zhaopin_auth_state["status"] = "completed"
                zhaopin_auth_state["finished_at"] = now_iso()
        zhaopin_auth_log("Zhaopin login browser closed. Profile has been saved.")
    except Exception as exc:
        with auth_lock:
            if zhaopin_auth_state.get("run_id") == run_id:
                zhaopin_auth_state["status"] = "failed"
                zhaopin_auth_state["finished_at"] = now_iso()
                zhaopin_auth_state["error"] = str(exc)
        zhaopin_auth_log(f"Zhaopin login failed: {exc}")
        return


def newest_first_logs(logs: list[str]) -> list[str]:
    if len(logs) < 2:
        return logs
    first = str(logs[0])
    last = str(logs[-1])
    if len(first) >= 10 and len(last) >= 10 and first.startswith("[") and last.startswith("["):
        if first[:10] < last[:10]:
            return list(reversed(logs))
    return logs


@dataclass
class CrawlerTask:
    id: str
    name: str
    status: str
    platform: str
    keywords: list[str]
    regions: list[str]
    output_dir: Path
    created_at: str
    started_at: str = ""
    finished_at: str = ""
    max_pages: int = 1
    headless: bool = True
    skip_detail_fetch: bool = False
    refetch_crawled_details: bool = False
    filter_existing_output_early: bool = False
    raw_count: int = 0
    appended_count: int = 0
    updated_count: int = 0
    saved_files: list[str] = field(default_factory=list)
    error: str = ""
    logs: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    stop_requested_at: str = ""

    def log(self, message: str) -> None:
        timestamp = dt.datetime.now().strftime("%H:%M:%S")
        with tasks_lock:
            self.logs.insert(0, f"[{timestamp}] {message}")
            self.logs = self.logs[:300]
            persist_task(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "platform": self.platform,
            "keywords": self.keywords,
            "regions": self.regions,
            "output_dir": str(self.output_dir),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "max_pages": self.max_pages,
            "headless": self.headless,
            "skip_detail_fetch": self.skip_detail_fetch,
            "refetch_crawled_details": self.refetch_crawled_details,
            "filter_existing_output_early": self.filter_existing_output_early,
            "raw_count": self.raw_count,
            "appended_count": self.appended_count,
            "updated_count": self.updated_count,
            "saved_files": self.saved_files,
            "error": self.error,
            "logs": self.logs,
            "cancel_requested": self.cancel_requested,
            "stop_requested_at": self.stop_requested_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CrawlerTask":
        task = cls(
            id=str(payload["id"]),
            name=str(payload.get("name") or payload["id"]),
            status=str(payload.get("status") or "failed"),
            platform=str(payload.get("platform") or "zhaopin"),
            keywords=list(payload.get("keywords") or []),
            regions=list(payload.get("regions") or []),
            output_dir=Path(payload.get("output_dir") or TASKS_DIR / str(payload["id"]) / "output"),
            created_at=str(payload.get("created_at") or ""),
            started_at=str(payload.get("started_at") or ""),
            finished_at=str(payload.get("finished_at") or ""),
            max_pages=int(payload.get("max_pages") or 1),
            headless=bool(payload.get("headless", True)),
            skip_detail_fetch=bool(payload.get("skip_detail_fetch", False)),
            refetch_crawled_details=bool(payload.get("refetch_crawled_details", False)),
            filter_existing_output_early=bool(payload.get("filter_existing_output_early", False)),
            raw_count=int(payload.get("raw_count") or 0),
            appended_count=int(payload.get("appended_count") or 0),
            updated_count=int(payload.get("updated_count") or 0),
            saved_files=list(payload.get("saved_files") or []),
            error=str(payload.get("error") or ""),
            logs=newest_first_logs(list(payload.get("logs") or [])),
            cancel_requested=bool(payload.get("cancel_requested", False)),
            stop_requested_at=str(payload.get("stop_requested_at") or ""),
        )
        if task.status in {"queued", "running"}:
            task.status = "failed"
            task.finished_at = task.finished_at or now_iso()
            task.error = task.error or "服务重启前任务未完成，已标记为失败。"
        if str(payload.get("status") or "") == "stopping" or (
            task.cancel_requested and str(payload.get("status") or "") in {"queued", "running", "stopping"}
        ):
            task.status = "stopped"
            task.finished_at = task.finished_at or now_iso()
            task.error = ""
        return task


def task_metadata_path(task_id: str) -> Path:
    return TASKS_DIR / task_id / "task.json"


def persist_task(task: CrawlerTask) -> None:
    path = task_metadata_path(task.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def infer_legacy_task_platform(task_dir: Path) -> str:
    crawled_links_dir = task_dir / "output" / "crawled_links"
    if not crawled_links_dir.exists():
        return "zhaopin"
    for path in crawled_links_dir.glob("*_links.txt"):
        platform = path.name.removesuffix("_links.txt")
        if platform in {"zhaopin", "51job"}:
            return platform
    return "zhaopin"


def count_excel_rows(path: Path) -> int:
    try:
        return len(pd.read_excel(path, dtype=str, engine="openpyxl"))
    except Exception:
        return 0


def migrate_legacy_task_dirs() -> None:
    task_dirs = TASKS_DIR.iterdir() if TASKS_DIR.exists() else []
    for task_dir in task_dirs:
        if not task_dir.is_dir() or (task_dir / "task.json").exists():
            continue
        output_dir = task_dir / "output"
        excel_files = sorted(output_dir.glob("*.xlsx")) if output_dir.exists() else []
        if not excel_files:
            continue

        row_count = sum(count_excel_rows(path) for path in excel_files)
        task = CrawlerTask(
            id=task_dir.name,
            name=f"历史任务-{task_dir.name}",
            status="completed",
            platform=infer_legacy_task_platform(task_dir),
            keywords=[path.stem for path in excel_files],
            regions=[],
            output_dir=output_dir,
            created_at=dt.datetime.fromtimestamp(task_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            finished_at=dt.datetime.fromtimestamp(
                max(path.stat().st_mtime for path in excel_files)
            ).strftime("%Y-%m-%d %H:%M:%S"),
            raw_count=row_count,
            appended_count=row_count,
            saved_files=[str(path) for path in excel_files],
        )
        task.logs.append("从旧版 web_tasks 目录自动导入的历史任务。")
        persist_task(task)


def migrate_global_crawled_links() -> None:
    try:
        settings = load_base_settings()
    except Exception:
        settings = {
            "platform": "zhaopin",
            "output_dir": BASE_DIR / "output",
            "crawled_links_dir": BASE_DIR / "output" / "crawled_links",
        }

    global_crawled_links_dir = Path(settings["crawled_links_dir"])
    legacy_dirs = [
        path
        for path in TASKS_DIR.glob("*/output/crawled_links")
        if path.resolve() != global_crawled_links_dir.resolve()
    ]
    settings["legacy_crawled_links_dirs"] = legacy_dirs
    build_crawled_link_store(settings)


def load_persisted_tasks() -> None:
    migrate_legacy_task_dirs()
    migrate_global_crawled_links()
    refresh_persisted_tasks()


def refresh_persisted_tasks() -> None:
    with tasks_lock:
        for path in TASKS_DIR.glob("*/task.json"):
            try:
                task = CrawlerTask.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            current_task = tasks.get(task.id)
            if current_task and current_task.status in {"queued", "running", "stopping"}:
                continue
            if task.name.startswith("历史任务-") and not task.raw_count:
                row_count = sum(count_excel_rows(excel_path) for excel_path in list_task_excels(task))
                task.raw_count = row_count
                task.appended_count = row_count
            tasks[task.id] = task
            persist_task(task)


def now_iso() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def task_cancel_requested(task: CrawlerTask) -> bool:
    with tasks_lock:
        return task.cancel_requested or task.status in {"stopping", "stopped"}


def parse_csv(value: Any) -> list[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = str(value or "").split(",")
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = clean_text(str(part))
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def parse_delay_range(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    parts = str(value or "").split(",")
    if len(parts) != 2:
        return default
    try:
        low = float(parts[0].strip())
        high = float(parts[1].strip())
    except ValueError:
        return default
    if low <= 0 or high <= 0:
        return default
    if low > high:
        low, high = high, low
    return low, high


def load_base_settings() -> dict[str, Any]:
    env_path = BASE_DIR / ENV_FILE_NAME
    return load_env_config(env_path)


def build_defaults_payload(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "keywords": ",".join(settings["keywords"]),
        "regions": ",".join(settings["regions"]),
        "platform": settings["platform"],
        "browser_backend": settings["browser_backend"],
        "max_pages": settings["max_pages_per_region"],
        "headless": settings["headless"],
        "max_empty_retries": settings["max_empty_page_retries"],
        "max_detail_retries": settings["max_detail_retries"],
        "detail_timeout_ms": settings["detail_page_timeout_ms"],
        "delay_between_pages": ",".join(str(value) for value in settings["delays"]["between_pages"]),
        "auth_wait_seconds": settings["auth_wait_seconds"],
        "user_data_dir": str(settings["user_data_dir"]),
        "profile_ready": profile_ready(Path(settings["user_data_dir"])),
        "zhaopin_user_data_dir": str(settings["zhaopin_user_data_dir"]),
        "zhaopin_profile_ready": profile_ready(Path(settings["zhaopin_user_data_dir"])),
        "scrapling_real_chrome": settings["scrapling_real_chrome"],
        "scrapling_google_search": settings["scrapling_google_search"],
        "scrapling_block_webrtc": settings["scrapling_block_webrtc"],
        "scrapling_hide_canvas": settings["scrapling_hide_canvas"],
        "skip_detail_fetch": settings["skip_detail_fetch"],
        "refetch_crawled_details": settings["refetch_crawled_details"],
        "filter_existing_output_early": settings["filter_existing_output_early"],
        "gologin_token_set": bool(settings.get("gologin_token", "")),
        "gologin_profile_id": settings.get("gologin_profile_id", ""),
    }


def ui_defaults_fallback_payload() -> dict[str, Any]:
    return {
        "keywords": "",
        "regions": "",
        "platform": "zhaopin",
        "browser_backend": "playwright",
        "max_pages": 1,
        "headless": True,
        "max_empty_retries": 2,
        "max_detail_retries": 1,
        "detail_timeout_ms": 90000,
        "delay_between_pages": "1.8,3.0",
        "auth_wait_seconds": 120,
        "user_data_dir": str(BASE_DIR / "auth" / "51job_profile"),
        "profile_ready": False,
        "zhaopin_user_data_dir": str(BASE_DIR / "auth" / "zhaopin_profile"),
        "zhaopin_profile_ready": False,
        "scrapling_real_chrome": False,
        "scrapling_google_search": False,
        "scrapling_block_webrtc": True,
        "scrapling_hide_canvas": True,
        "skip_detail_fetch": False,
        "refetch_crawled_details": False,
        "filter_existing_output_early": False,
        "gologin_token_set": False,
        "gologin_profile_id": "",
    }


def load_ui_defaults() -> dict[str, Any]:
    try:
        payload = build_defaults_payload(load_base_settings())
    except Exception:
        payload = ui_defaults_fallback_payload()

    if WEB_UI_CONFIG_PATH.exists():
        try:
            saved = json.loads(WEB_UI_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for key in UI_DEFAULT_KEYS:
                    if key in saved:
                        payload[key] = saved[key]
        except Exception:
            pass
    return payload


def save_ui_defaults(payload: dict[str, Any]) -> None:
    current: dict[str, Any] = {}
    if WEB_UI_CONFIG_PATH.exists():
        try:
            saved = json.loads(WEB_UI_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for key in UI_DEFAULT_KEYS:
                    if key in saved:
                        current[key] = saved[key]
        except Exception:
            current = {}

    for key in UI_DEFAULT_KEYS:
        if key in payload:
            current[key] = payload[key]
    WEB_UI_CONFIG_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_ui_defaults() -> None:
    if WEB_UI_CONFIG_PATH.exists():
        WEB_UI_CONFIG_PATH.unlink()


def build_settings_for_task(task: CrawlerTask, payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_base_settings()
    global_crawled_links_dir = Path(settings["crawled_links_dir"])
    settings["platform"] = task.platform
    settings["keywords"] = task.keywords
    settings["regions"] = task.regions
    settings["max_pages_per_region"] = task.max_pages
    settings["headless"] = task.headless
    settings["output_dir"] = task.output_dir
    settings["crawled_links_dir"] = global_crawled_links_dir
    settings["legacy_crawled_links_dirs"] = [
        path
        for path in TASKS_DIR.glob("*/output/crawled_links")
        if path.resolve() != global_crawled_links_dir.resolve()
    ]
    settings["login_51job"] = False
    if task.platform == "zhaopin":
        # Web 控制台与 CLI 保持同一套浏览器后端选择，不再强制改成 playwright
        settings["headless"] = False
        settings["manual_auth"] = True

    if payload.get("max_empty_retries"):
        settings["max_empty_page_retries"] = max(1, int(payload["max_empty_retries"]))
    if payload.get("max_detail_retries") is not None:
        settings["max_detail_retries"] = max(0, int(payload["max_detail_retries"]))
    if payload.get("detail_timeout_ms"):
        settings["detail_page_timeout_ms"] = max(5000, int(payload["detail_timeout_ms"]))
    if payload.get("delay_between_pages"):
        settings["delays"]["between_pages"] = parse_delay_range(
            payload.get("delay_between_pages"),
            tuple(settings["delays"]["between_pages"]),
        )
    if payload.get("browser_backend"):
        settings["browser_backend"] = clean_text(str(payload.get("browser_backend"))).lower()
    if payload.get("scrapling_real_chrome") is not None:
        settings["scrapling_real_chrome"] = parse_bool(payload.get("scrapling_real_chrome"), False)
    if payload.get("scrapling_google_search") is not None:
        settings["scrapling_google_search"] = parse_bool(payload.get("scrapling_google_search"), False)
    if payload.get("scrapling_block_webrtc") is not None:
        settings["scrapling_block_webrtc"] = parse_bool(payload.get("scrapling_block_webrtc"), True)
    if payload.get("scrapling_hide_canvas") is not None:
        settings["scrapling_hide_canvas"] = parse_bool(payload.get("scrapling_hide_canvas"), True)
    if payload.get("skip_detail_fetch") is not None:
        settings["skip_detail_fetch"] = parse_bool(payload.get("skip_detail_fetch"), False)
    if payload.get("refetch_crawled_details") is not None:
        settings["refetch_crawled_details"] = parse_bool(payload.get("refetch_crawled_details"), False)
    if payload.get("filter_existing_output_early") is not None:
        settings["filter_existing_output_early"] = parse_bool(payload.get("filter_existing_output_early"), False)

    settings["output_dir"].mkdir(parents=True, exist_ok=True)
    settings["crawled_links_dir"].mkdir(parents=True, exist_ok=True)
    return settings


def format_progress_message(progress: dict[str, Any]) -> str:
    page = progress.get("page")
    total_pages = progress.get("total_pages")
    keyword = progress.get("keyword") or "-"
    region = progress.get("region") or "不限地区"
    parsed_count = progress.get("parsed_count")
    kept_count = progress.get("kept_count")
    cumulative_count = progress.get("saved_count")
    if cumulative_count is None:
        cumulative_count = progress.get("cumulative_count")
    detail_url = progress.get("current_detail_url") or ""

    parts = [f"关键词={keyword}", f"地区={region}"]
    if page and total_pages:
        parts.append(f"当前第 {page}/{total_pages} 页")
    if parsed_count is not None:
        parts.append(f"本页解析 {parsed_count} 条")
    if kept_count is not None:
        parts.append(f"本页待处理 {kept_count} 条")
    if cumulative_count is not None:
        parts.append(f"累计新增 {cumulative_count} 条")
    if detail_url:
        parts.append(f"正在分析链接：{detail_url}")
    return "；".join(parts)


async def heartbeat_task_log(task: CrawlerTask, settings: dict[str, Any], stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=6)
        except asyncio.TimeoutError:
            if task.status not in {"running", "stopping"}:
                return
            progress = settings.get("progress", {})
            if isinstance(progress, dict) and progress:
                task.log(f"仍在工作：{format_progress_message(progress)}")
            else:
                task.log("仍在工作：正在等待页面响应或浏览器操作完成。")


def record_incremental_save(
    task: CrawlerTask,
    settings: dict[str, Any],
    keyword: str,
    jobs: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    if not jobs:
        return {
            "file_count": 0,
            "raw_count": 0,
            "appended_count": 0,
            "updated_count": 0,
            "saved_files": [],
        }

    export_keyword = clean_text(
        str(
            context.get("export_keyword")
            or settings.get("export_keyword")
            or keyword
            or task.name
            or "未知关键词"
        )
    )
    summary = save_jobs_by_keyword(
        jobs,
        output_dir=settings["output_dir"],
        keyword=export_keyword,
    )
    page = context.get("page")
    detail_index = context.get("detail_index")
    region = context.get("region") or "不限地区"
    total_pages = context.get("total_pages") or settings.get("max_pages_per_region")
    page_text = f"第 {page}/{total_pages} 页" if page and total_pages else "当前批次"
    detail_text = f"，第 {detail_index} 条" if detail_index else ""

    with tasks_lock:
        task.raw_count += int(summary["raw_count"])
        task.appended_count += int(summary["appended_count"])
        task.updated_count += int(summary["updated_count"])
        existing_files = set(task.saved_files)
        for path in summary["saved_files"]:
            if path not in existing_files:
                task.saved_files.append(path)
                existing_files.add(path)
        progress = settings.get("progress")
        if isinstance(progress, dict):
            progress["saved_count"] = task.appended_count
            progress["cumulative_count"] = task.appended_count
            progress["updated_count"] = task.updated_count
        persist_task(task)

    task.log(
        f"Excel 已更新：导出关键词={export_keyword}，来源关键词={keyword}，地区={region}，{page_text}{detail_text}，"
        f"本次写入 {summary['raw_count']} 条，新增 {summary['appended_count']} 条，"
        f"更新 {summary['updated_count']} 条，累计新增 {task.appended_count} 条"
    )
    return summary


async def run_task_async(task: CrawlerTask, payload: dict[str, Any]) -> None:
    settings = build_settings_for_task(task, payload)
    settings["log_callback"] = task.log
    settings["cancel_check_callback"] = lambda: task_cancel_requested(task)
    settings["progress"] = {}
    settings["page_result_callback"] = lambda keyword, jobs, context=None: record_incremental_save(
        task=task,
        settings=settings,
        keyword=keyword,
        jobs=jobs,
        context=context,
    )
    crawled_link_store = build_crawled_link_store(settings)
    stop_heartbeat = asyncio.Event()
    heartbeat = asyncio.create_task(heartbeat_task_log(task, settings, stop_heartbeat))
    crawl_regions = task.regions or [""]
    keyword_groups = expand_zhaopin_keyword_groups(task.keywords) if task.platform == "zhaopin" else [
        {"label": keyword, "searches": [{"search_keyword": keyword, "primary_category": keyword, "secondary_category": ""}]}
        for keyword in task.keywords
    ]

    try:
        total = sum(len(group["searches"]) for group in keyword_groups) * len(crawl_regions)
        current = 0
        task.log(f"Task started. {total} keyword-region combinations.")

        for group in keyword_groups:
            keyword = str(group["label"])
            if task_cancel_requested(task):
                task.log("收到中止请求，停止启动新的关键词。")
                return
            keyword_total = 0
            for search in group["searches"]:
                search_keyword = str(search["search_keyword"])
                primary_category = str(search.get("primary_category") or keyword)
                secondary_category = str(search.get("secondary_category") or "")
                for city in crawl_regions:
                    if task_cancel_requested(task):
                        task.log("收到中止请求，停止启动新的地区。")
                        return
                    current += 1
                    region_label = city or "不限地区"
                    settings["progress"].update(
                        {
                            "keyword": search_keyword,
                            "region": region_label,
                            "page": 1,
                            "total_pages": task.max_pages,
                            "parsed_count": 0,
                            "kept_count": 0,
                            "cumulative_count": keyword_total,
                            "current_detail_url": "",
                        }
                    )
                    task.log(f"({current}/{total}) crawling {task.platform}: keyword={search_keyword}, region={region_label}")
                    settings["export_keyword"] = keyword
                    if task.platform == "51job":
                        region_jobs = await crawl_51job(
                            keyword=search_keyword,
                            city=city,
                            settings=settings,
                            crawled_link_store=crawled_link_store,
                        )
                    else:
                        region_jobs = await crawl_zhaopin(
                            keyword=search_keyword,
                            city=city,
                            settings=settings,
                            crawled_link_store=crawled_link_store,
                        )
                        for item in region_jobs:
                            if primary_category:
                                item["岗位类型一级"] = primary_category
                            if secondary_category:
                                item["岗位类型二级"] = secondary_category
                            item["__export_keyword"] = keyword
                            item["__source_keyword"] = search_keyword
                    keyword_total += len(region_jobs)
                    task.log(f"Fetched {len(region_jobs)} rows for keyword={search_keyword}, region={region_label}")
                    settings["progress"]["cumulative_count"] = keyword_total
                    if task_cancel_requested(task):
                        task.log("当前地区已按中止请求结束，停止后续任务组合。")
                        return

            if not keyword_total:
                task.log(f"No rows for keyword={keyword}; skipped Excel write.")
            else:
                task.log(f"Keyword finished: keyword={keyword}, fetched={keyword_total}, saved_incrementally=true")
    finally:
        stop_heartbeat.set()
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat


def run_task(task_id: str, payload: dict[str, Any]) -> None:
    task = tasks[task_id]
    with tasks_lock:
        if task.cancel_requested or task.status == "stopped":
            task.status = "stopped"
            task.finished_at = task.finished_at or now_iso()
            persist_task(task)
            return
        task.status = "running"
        task.started_at = now_iso()
        persist_task(task)
    try:
        asyncio.run(run_task_async(task, payload))
        with tasks_lock:
            task.status = "stopped" if task.cancel_requested else "completed"
            task.finished_at = now_iso()
            persist_task(task)
        if task.status == "stopped":
            task.log("任务已中止。已保留当前已写入的 Excel 数据与日志。")
        else:
            task.log("Task completed.")
    except Exception as exc:
        with tasks_lock:
            task.status = "failed"
            task.finished_at = now_iso()
            task.error = str(exc)
            persist_task(task)
        task.log("Task failed.")
        task.log(traceback.format_exc())


def get_task_or_404(task_id: str) -> CrawlerTask:
    refresh_persisted_tasks()
    with tasks_lock:
        task = tasks.get(task_id)
    if not task:
        abort(404)
    return task


def list_task_excels(task: CrawlerTask) -> list[Path]:
    if not task.output_dir.exists():
        return []
    return sorted(task.output_dir.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)


load_persisted_tasks()


@app.get("/")
@app.get("/tasks")
@app.get("/tasks/<task_id>")
@app.get("/history")
@app.get("/history/<task_id>")
def index(task_id: str | None = None):
    return render_template("index.html")


@app.get("/api/defaults")
def api_defaults():
    return jsonify(load_ui_defaults())


@app.post("/api/defaults")
def api_save_defaults():
    payload = request.get_json(force=True) or {}
    save_ui_defaults(payload)
    return jsonify(load_ui_defaults())


@app.post("/api/defaults/reset")
def api_reset_defaults():
    reset_ui_defaults()
    return jsonify(load_ui_defaults())


@app.get("/api/51job/auth")
def api_51job_auth_status():
    return jsonify(auth_state_payload())


@app.post("/api/51job/auth")
def api_51job_auth_start():
    force = parse_bool(request.args.get("force"), False)
    with auth_lock:
        if auth_state.get("status") == "running" and not force:
            return jsonify(auth_state_payload()), 202
        run_id = uuid.uuid4().hex
        auth_state["run_id"] = run_id
        auth_state["status"] = "queued"
        auth_state["started_at"] = ""
        auth_state["finished_at"] = ""
        auth_state["error"] = ""
        auth_state["logs"] = []
    auth_log("51job login job queued.")
    executor.submit(run_51job_login, run_id)
    return jsonify(auth_state_payload()), 202


@app.get("/api/zhaopin/auth")
def api_zhaopin_auth_status():
    return jsonify(zhaopin_auth_state_payload())


@app.post("/api/zhaopin/auth")
def api_zhaopin_auth_start():
    force = parse_bool(request.args.get("force"), False)
    with auth_lock:
        if zhaopin_auth_state.get("status") == "running" and not force:
            return jsonify(zhaopin_auth_state_payload()), 202
        run_id = uuid.uuid4().hex
        zhaopin_auth_state["run_id"] = run_id
        zhaopin_auth_state["status"] = "queued"
        zhaopin_auth_state["started_at"] = ""
        zhaopin_auth_state["finished_at"] = ""
        zhaopin_auth_state["error"] = ""
        zhaopin_auth_state["logs"] = []
    zhaopin_auth_log("Zhaopin login job queued.")
    future = executor.submit(run_zhaopin_login, run_id)
    try:
        future.result(timeout=2)
    except Exception:
        pass
    return jsonify(zhaopin_auth_state_payload()), 202


@app.get("/api/tasks")
def api_tasks():
    refresh_persisted_tasks()
    with tasks_lock:
        ordered = sorted(tasks.values(), key=lambda item: item.created_at, reverse=True)
        return jsonify([task.to_dict() for task in ordered])


@app.post("/api/tasks")
def api_create_task():
    payload = request.get_json(force=True) or {}
    save_ui_defaults(payload)
    keywords = parse_csv(payload.get("keywords"))
    regions = parse_csv(payload.get("regions"))
    if not keywords:
        return jsonify({"error": "Please provide at least one keyword."}), 400

    platform = clean_text(payload.get("platform", "zhaopin")).lower()
    if platform not in {"zhaopin", "51job"}:
        return jsonify({"error": "Platform must be zhaopin or 51job."}), 400
    if platform in {"zhaopin", "51job"}:
        settings = load_base_settings()
        if platform == "51job":
            login_state = auth_state
            profile_dir = Path(settings["user_data_dir"])
            platform_label = "51job"
            login_action = "51job login button"
        else:
            login_state = zhaopin_auth_state
            profile_dir = Path(settings["zhaopin_user_data_dir"])
            platform_label = "Zhaopin"
            login_action = "Zhaopin login button"
        with auth_lock:
            auth_running = login_state.get("status") in {"queued", "running"}
        if auth_running:
            return jsonify(
                {
                    "error": (
                        f"{platform_label} login is still running. "
                        "Please finish login in the opened browser before creating the crawl task."
                    )
                }
            ), 400
        if not profile_ready(profile_dir):
            return jsonify(
                {
                    "error": (
                        f"{platform_label} needs a saved login profile first. "
                        f"Please use the {login_action}, finish login/verification, then create the task again."
                    )
                }
            ), 400

    task_id = uuid.uuid4().hex[:12]
    name = clean_text(payload.get("name", "")) or f"{platform}-{keywords[0]}-{dt.datetime.now():%m%d%H%M}"
    max_pages = max(1, int(payload.get("max_pages") or 1))
    headless = parse_bool(payload.get("headless"), True)
    if platform == "zhaopin":
        headless = False
    output_dir = TASKS_DIR / task_id / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    task = CrawlerTask(
        id=task_id,
        name=name,
        status="queued",
        platform=platform,
        keywords=keywords,
        regions=regions,
        output_dir=output_dir,
        created_at=now_iso(),
        max_pages=max_pages,
        headless=headless,
        skip_detail_fetch=parse_bool(payload.get("skip_detail_fetch"), False),
        refetch_crawled_details=parse_bool(payload.get("refetch_crawled_details"), False),
        filter_existing_output_early=parse_bool(payload.get("filter_existing_output_early"), False),
    )
    task.log("Task queued.")
    with tasks_lock:
        tasks[task_id] = task
        persist_task(task)
    executor.submit(run_task, task_id, payload)
    return jsonify(task.to_dict()), 201


@app.get("/api/tasks/<task_id>")
def api_task_detail(task_id: str):
    return jsonify(get_task_or_404(task_id).to_dict())


@app.post("/api/tasks/<task_id>/cancel")
def api_cancel_task(task_id: str):
    task = get_task_or_404(task_id)
    with tasks_lock:
        if task.status in {"completed", "failed", "stopped"}:
            return jsonify(task.to_dict())

        task.cancel_requested = True
        task.stop_requested_at = task.stop_requested_at or now_iso()
        if task.status == "queued":
            task.status = "stopped"
            task.finished_at = task.finished_at or now_iso()
        else:
            task.status = "stopping"
        persist_task(task)

    if task.status == "stopped":
        task.log("任务已在排队阶段中止。已保留现有数据与日志。")
    else:
        task.log("收到中止请求：当前详情/写入步骤完成后会停止，已写入的数据与日志会保留。")
    return jsonify(task.to_dict())


@app.get("/api/tasks/<task_id>/files")
def api_task_files(task_id: str):
    task = get_task_or_404(task_id)
    files = []
    for path in list_task_excels(task):
        files.append(
            {
                "name": path.name,
                "size": path.stat().st_size,
                "modified_at": dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "download_url": f"/api/tasks/{task.id}/files/{path.name}",
            }
        )
    return jsonify(files)


@app.get("/api/tasks/<task_id>/files/<path:file_name>")
def api_download_file(task_id: str, file_name: str):
    task = get_task_or_404(task_id)
    path = (task.output_dir / file_name).resolve()
    if task.output_dir.resolve() not in path.parents or path.suffix.lower() != ".xlsx" or not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.get("/api/tasks/<task_id>/data")
def api_task_data(task_id: str):
    task = get_task_or_404(task_id)
    file_name = clean_text(request.args.get("file", ""))
    limit = max(1, min(500, int(request.args.get("limit", 100))))
    sheet_name = clean_text(request.args.get("sheet", ""))

    excel_files = list_task_excels(task)
    if file_name:
        selected = [path for path in excel_files if path.name == file_name]
    else:
        selected = excel_files[:1]
    if not selected:
        return jsonify({"columns": OUTPUT_COLUMNS, "rows": [], "file": ""})

    path = selected[0]
    if sheet_name:
        try:
            df = pd.read_excel(path, dtype=str, engine="openpyxl", sheet_name=sheet_name).fillna("")
        except Exception:
            df = pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
    else:
        df = pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
    rows = df.head(limit).to_dict(orient="records")
    return jsonify({"columns": list(df.columns), "rows": rows, "file": path.name})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
