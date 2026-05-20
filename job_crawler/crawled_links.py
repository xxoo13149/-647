from pathlib import Path
import threading
from typing import Iterable

import pandas as pd

from .constants import EMPTY_CELL_VALUE, OUTPUT_COLUMNS
from .utils import clean_text, normalize_absolute_url


LINK_COLUMN = "岗位链接"
GLOBAL_LINKS_FILE_NAME = "all_links.txt"
_links_file_lock = threading.RLock()


def normalize_crawled_link(url: str) -> str:
    """Normalize a job detail URL before storing or comparing it."""
    text = clean_text(url)
    if not text or text == EMPTY_CELL_VALUE:
        return ""
    return normalize_absolute_url(text)


class CrawledLinkStore:
    """Persistent text-file store used to skip already fetched detail pages."""

    def __init__(self, links_dir: Path, platform: str) -> None:
        self.links_dir = Path(links_dir)
        self.platform = clean_text(platform).lower() or "default"
        self.file_path = self.links_dir / GLOBAL_LINKS_FILE_NAME
        self._links: set[str] = set()
        self._dirty = False

    def load(self) -> None:
        self.links_dir.mkdir(parents=True, exist_ok=True)
        with _links_file_lock:
            for path in sorted(self.links_dir.glob("*.txt")):
                self._read_links_from_file(path)
            if self.file_path.exists():
                self._read_links_from_file(self.file_path)

    def _read_links_from_file(self, path: Path) -> None:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                normalized = normalize_crawled_link(line)
                if normalized:
                    self._links.add(normalized)
        except OSError as exc:
            print(f"Warning: failed to read crawled link file: {path}; reason: {exc}")

    def load_from_dirs(self, links_dirs: Iterable[Path]) -> int:
        before = len(self._links)
        with _links_file_lock:
            for links_dir in links_dirs:
                path = Path(links_dir)
                if not path.exists():
                    continue
                for file_path in sorted(path.glob("*.txt")):
                    self._read_links_from_file(file_path)
        if len(self._links) != before:
            self._dirty = True
        return len(self._links) - before

    def hydrate_from_output_dir(self, output_dir: Path) -> int:
        """Load links from existing xlsx outputs so old runs also take effect."""
        output_path = Path(output_dir)
        if not output_path.exists():
            return 0

        before = len(self._links)
        for workbook in output_path.glob("*.xlsx"):
            try:
                sheets = pd.read_excel(workbook, dtype=str, engine="openpyxl", sheet_name=None)
            except Exception:
                continue
            for df in sheets.values():
                if df is None or LINK_COLUMN not in df.columns:
                    continue
                df = df.fillna("")
                for value in df[LINK_COLUMN].tolist():
                    normalized = normalize_crawled_link(str(value))
                    if normalized:
                        self._links.add(normalized)

        if len(self._links) != before:
            self._dirty = True
        return len(self._links) - before

    def contains(self, url: str) -> bool:
        normalized = normalize_crawled_link(url)
        if not normalized:
            return False
        with _links_file_lock:
            if self.file_path.exists():
                self._read_links_from_file(self.file_path)
            return normalized in self._links

    def add(self, url: str) -> bool:
        normalized = normalize_crawled_link(url)
        if not normalized:
            return False
        with _links_file_lock:
            if self.file_path.exists():
                self._read_links_from_file(self.file_path)
            if normalized in self._links:
                return False
            self._links.add(normalized)
            self._dirty = True
            return True

    def add_many(self, urls: Iterable[str]) -> int:
        added = 0
        for url in urls:
            if self.add(url):
                added += 1
        return added

    def save(self, force: bool = False) -> None:
        if not force and not self._dirty:
            return
        with _links_file_lock:
            self.links_dir.mkdir(parents=True, exist_ok=True)
            if self.file_path.exists():
                self._read_links_from_file(self.file_path)
            content = "\n".join(sorted(self._links))
            if content:
                content += "\n"
            self.file_path.write_text(content, encoding="utf-8")
            self._dirty = False

    def __len__(self) -> int:
        return len(self._links)


def build_crawled_link_store(settings: dict) -> CrawledLinkStore:
    store = CrawledLinkStore(
        links_dir=Path(settings["crawled_links_dir"]),
        platform=str(settings["platform"]),
    )
    store.load()
    legacy_dirs = [Path(path) for path in settings.get("legacy_crawled_links_dirs", [])]
    migrated_count = store.load_from_dirs(legacy_dirs)
    hydrated_count = store.hydrate_from_output_dir(Path(settings["output_dir"]))
    store.save()
    print(
        f"Loaded {len(store)} crawled detail links "
        f"({hydrated_count} hydrated from existing output, {migrated_count} migrated): {store.file_path}"
    )
    return store
