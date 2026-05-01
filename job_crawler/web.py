import asyncio
import concurrent.futures
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
from .constants import ENV_FILE_NAME, OUTPUT_COLUMNS
from .crawled_links import build_crawled_link_store
from .fiftyone import crawl_51job
from .output import save_jobs_by_keyword
from .utils import clean_text, parse_bool
from .zhaopin import crawl_zhaopin


BASE_DIR = Path(__file__).resolve().parents[1]
TASKS_DIR = BASE_DIR / "web_tasks"
TASKS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
tasks_lock = threading.RLock()
tasks: dict[str, "CrawlerTask"] = {}


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
    raw_count: int = 0
    appended_count: int = 0
    updated_count: int = 0
    saved_files: list[str] = field(default_factory=list)
    error: str = ""
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        timestamp = dt.datetime.now().strftime("%H:%M:%S")
        with tasks_lock:
            self.logs.append(f"[{timestamp}] {message}")
            self.logs = self.logs[-300:]
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
            "raw_count": self.raw_count,
            "appended_count": self.appended_count,
            "updated_count": self.updated_count,
            "saved_files": self.saved_files,
            "error": self.error,
            "logs": self.logs,
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
            raw_count=int(payload.get("raw_count") or 0),
            appended_count=int(payload.get("appended_count") or 0),
            updated_count=int(payload.get("updated_count") or 0),
            saved_files=list(payload.get("saved_files") or []),
            error=str(payload.get("error") or ""),
            logs=list(payload.get("logs") or []),
        )
        if task.status in {"queued", "running"}:
            task.status = "failed"
            task.finished_at = task.finished_at or now_iso()
            task.error = task.error or "服务重启前任务未完成，已标记为失败。"
        return task


def task_metadata_path(task_id: str) -> Path:
    return TASKS_DIR / task_id / "task.json"


def persist_task(task: CrawlerTask) -> None:
    path = task_metadata_path(task.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_persisted_tasks() -> None:
    with tasks_lock:
        for path in TASKS_DIR.glob("*/task.json"):
            try:
                task = CrawlerTask.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            tasks[task.id] = task


def now_iso() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def load_base_settings() -> dict[str, Any]:
    env_path = BASE_DIR / ENV_FILE_NAME
    return load_env_config(env_path)


def build_settings_for_task(task: CrawlerTask, payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_base_settings()
    settings["platform"] = task.platform
    settings["keywords"] = task.keywords
    settings["regions"] = task.regions
    settings["max_pages_per_region"] = task.max_pages
    settings["headless"] = task.headless
    settings["output_dir"] = task.output_dir
    settings["crawled_links_dir"] = task.output_dir / "crawled_links"
    settings["login_51job"] = False

    if payload.get("max_empty_retries"):
        settings["max_empty_page_retries"] = max(1, int(payload["max_empty_retries"]))
    if payload.get("max_detail_retries") is not None:
        settings["max_detail_retries"] = max(0, int(payload["max_detail_retries"]))
    if payload.get("detail_timeout_ms"):
        settings["detail_page_timeout_ms"] = max(5000, int(payload["detail_timeout_ms"]))

    settings["output_dir"].mkdir(parents=True, exist_ok=True)
    settings["crawled_links_dir"].mkdir(parents=True, exist_ok=True)
    return settings


async def run_task_async(task: CrawlerTask, payload: dict[str, Any]) -> None:
    settings = build_settings_for_task(task, payload)
    crawled_link_store = build_crawled_link_store(settings)

    total = len(task.keywords) * len(task.regions)
    current = 0
    task.log(f"Task started. {total} keyword-region combinations.")

    for keyword in task.keywords:
        keyword_jobs: list[dict[str, Any]] = []
        for city in task.regions:
            current += 1
            task.log(f"({current}/{total}) crawling {task.platform}: keyword={keyword}, region={city}")
            if task.platform == "51job":
                region_jobs = await crawl_51job(
                    keyword=keyword,
                    city=city,
                    settings=settings,
                    crawled_link_store=crawled_link_store,
                )
            else:
                region_jobs = await crawl_zhaopin(
                    keyword=keyword,
                    city=city,
                    settings=settings,
                    crawled_link_store=crawled_link_store,
                )
            task.log(f"Fetched {len(region_jobs)} rows for keyword={keyword}, region={city}")
            keyword_jobs.extend(region_jobs)

        if not keyword_jobs:
            task.log(f"No rows for keyword={keyword}; skipped Excel write.")
            continue

        summary = save_jobs_by_keyword(
            keyword_jobs,
            output_dir=settings["output_dir"],
            keyword=keyword,
        )
        with tasks_lock:
            task.raw_count += int(summary["raw_count"])
            task.appended_count += int(summary["appended_count"])
            task.updated_count += int(summary["updated_count"])
            task.saved_files.extend(summary["saved_files"])
        task.log(
            f"Saved keyword={keyword}: raw={summary['raw_count']}, "
            f"new={summary['appended_count']}, updated={summary['updated_count']}"
        )


def run_task(task_id: str, payload: dict[str, Any]) -> None:
    task = tasks[task_id]
    with tasks_lock:
        task.status = "running"
        task.started_at = now_iso()
        persist_task(task)
    try:
        asyncio.run(run_task_async(task, payload))
        with tasks_lock:
            task.status = "completed"
            task.finished_at = now_iso()
            persist_task(task)
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
def index():
    return render_template("index.html")


@app.get("/api/defaults")
def api_defaults():
    try:
        settings = load_base_settings()
        payload = {
            "keywords": ",".join(settings["keywords"]),
            "regions": ",".join(settings["regions"]),
            "platform": settings["platform"],
            "max_pages": settings["max_pages_per_region"],
            "headless": settings["headless"],
            "max_empty_retries": settings["max_empty_page_retries"],
            "max_detail_retries": settings["max_detail_retries"],
            "detail_timeout_ms": settings["detail_page_timeout_ms"],
        }
    except Exception:
        payload = {
            "keywords": "",
            "regions": "",
            "platform": "zhaopin",
            "max_pages": 1,
            "headless": True,
            "max_empty_retries": 2,
            "max_detail_retries": 1,
            "detail_timeout_ms": 90000,
        }
    return jsonify(payload)


@app.get("/api/tasks")
def api_tasks():
    with tasks_lock:
        ordered = sorted(tasks.values(), key=lambda item: item.created_at, reverse=True)
        return jsonify([task.to_dict() for task in ordered])


@app.post("/api/tasks")
def api_create_task():
    payload = request.get_json(force=True) or {}
    keywords = parse_csv(payload.get("keywords"))
    regions = parse_csv(payload.get("regions"))
    if not keywords:
        return jsonify({"error": "Please provide at least one keyword."}), 400
    if not regions:
        return jsonify({"error": "Please provide at least one region."}), 400

    platform = clean_text(payload.get("platform", "zhaopin")).lower()
    if platform not in {"zhaopin", "51job"}:
        return jsonify({"error": "Platform must be zhaopin or 51job."}), 400

    task_id = uuid.uuid4().hex[:12]
    name = clean_text(payload.get("name", "")) or f"{platform}-{keywords[0]}-{dt.datetime.now():%m%d%H%M}"
    max_pages = max(1, int(payload.get("max_pages") or 1))
    headless = parse_bool(payload.get("headless"), True)
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

    excel_files = list_task_excels(task)
    if file_name:
        selected = [path for path in excel_files if path.name == file_name]
    else:
        selected = excel_files[:1]
    if not selected:
        return jsonify({"columns": OUTPUT_COLUMNS, "rows": [], "file": ""})

    path = selected[0]
    df = pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
    rows = df.head(limit).to_dict(orient="records")
    return jsonify({"columns": list(df.columns), "rows": rows, "file": path.name})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
