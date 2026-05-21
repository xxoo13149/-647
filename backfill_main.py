from __future__ import annotations

import asyncio
import os

from job_crawler.cli import main


BACKFILL_ENV = {
    "FILTER_EXISTING_OUTPUT_EARLY": "true",
    "SKIP_DETAIL_FETCH": "false",
    "REFETCH_CRAWLED_DETAILS": "true",
    "HEADLESS": "false",
    "MANUAL_AUTH": "true",
}


if __name__ == "__main__":
    for key, value in BACKFILL_ENV.items():
        os.environ.setdefault(key, value)
    asyncio.run(main())
