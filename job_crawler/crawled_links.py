from pathlib import Path
from typing import Iterable

import pandas as pd

from .constants import EMPTY_CELL_VALUE, OUTPUT_COLUMNS
from .utils import clean_text, normalize_absolute_url


LINK_COLUMN = OUTPUT_COLUMNS[14]


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
        self.file_path = self.links_dir / f"{self.platform}_links.txt"
        self._links: set[str] = set()
        self._dirty = False

    def load(self) -> None:
        self.links_dir.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            return

        try:
            for line in self.file_path.read_text(encoding="utf-8").splitlines():
                normalized = normalize_crawled_link(line)
                if normalized:
                    self._links.add(normalized)
        except OSError as exc:
            print(f"Warning: failed to read crawled link file: {self.file_path}; reason: {exc}")

    def hydrate_from_output_dir(self, output_dir: Path) -> int:
        """Load links from existing xlsx outputs so old runs also take effect."""
        output_path = Path(output_dir)
        if not output_path.exists():
            return 0

        before = len(self._links)
        for workbook in output_path.glob("*.xlsx"):
            try:
                df = pd.read_excel(workbook, dtype=str, engine="openpyxl").fillna("")
            except Exception:
                continue
            if LINK_COLUMN not in df.columns:
                continue
            for value in df[LINK_COLUMN].tolist():
                normalized = normalize_crawled_link(str(value))
                if normalized:
                    self._links.add(normalized)

        if len(self._links) != before:
            self._dirty = True
        return len(self._links) - before

    def contains(self, url: str) -> bool:
        normalized = normalize_crawled_link(url)
        return bool(normalized and normalized in self._links)

    def add(self, url: str) -> bool:
        normalized = normalize_crawled_link(url)
        if not normalized or normalized in self._links:
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
        self.links_dir.mkdir(parents=True, exist_ok=True)
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
    hydrated_count = store.hydrate_from_output_dir(Path(settings["output_dir"]))
    store.save()
    print(
        f"Loaded {len(store)} crawled detail links "
        f"({hydrated_count} hydrated from existing output): {store.file_path}"
    )
    return store
