"""SEBI registered Research Analysts / Investment Advisers.

SEBI publishes these as a paginated HTML listing (25 rows/page, no bulk download), so v0 uses a
manually maintained CSV (`config/sebi_registered.csv`). This source only validates and counts it.
"""

from __future__ import annotations

import csv
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from socmint.core.schemas import FetchResult
from socmint.core.settings import get_settings
from socmint.ingest.base import Source, register

REQUIRED_COLUMNS = {"reg_no", "name", "type"}


def sebi_csv_path() -> Path:
    return get_settings().config_dir / "sebi_registered.csv"


@lru_cache(maxsize=1)
def load_sebi_registry(path: str | None = None) -> dict[str, dict[str, str]]:
    """reg_no (upper-case) -> row. Empty dict if the file is missing."""
    p = Path(path) if path else sebi_csv_path()
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(ln for ln in f if not ln.startswith("#"))]
    return {r["reg_no"].strip().upper(): r for r in rows if r.get("reg_no")}


@register
class SebiManualSource(Source):
    type_name = "sebi_manual"

    def fetch(self, since: datetime | None) -> FetchResult:
        p = sebi_csv_path()
        if not p.exists():
            raise FileNotFoundError(f"{p} not found")
        with p.open(encoding="utf-8") as f:
            header = next(csv.reader(ln for ln in f if not ln.startswith("#")), [])
        missing = REQUIRED_COLUMNS - set(header)
        if missing:
            raise ValueError(f"sebi_registered.csv missing columns: {sorted(missing)}")
        load_sebi_registry.cache_clear()
        return FetchResult()  # nothing to store; market module reads the CSV directly
