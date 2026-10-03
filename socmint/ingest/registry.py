"""Sync YAML config (sources, brands) into the database."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from socmint.core.db import session_scope
from socmint.core.models import Brand, SourceRow
from socmint.core.settings import load_config


def source_configs() -> list[dict[str, Any]]:
    """Return the list of source definitions from sources.yaml."""
    return list(load_config("sources").get("sources", []) or [])


def brand_configs() -> list[dict[str, Any]]:
    """Return brand definitions from brands.yaml."""
    return list(load_config("brands").get("brands", []) or [])


def flat_aliases(brand: dict[str, Any]) -> list[str]:
    """All aliases of a brand across languages, plus its name and key."""
    aliases = brand.get("aliases") or {}
    out: list[str] = [brand.get("name", ""), brand.get("key", "")]
    if isinstance(aliases, dict):
        for values in aliases.values():
            out.extend(values or [])
    else:
        out.extend(aliases)
    seen: set[str] = set()
    result = []
    for a in out:
        a = (a or "").strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            result.append(a)
    return result


def sync_sources(url: str | None = None) -> int:
    """Upsert one `sources` row per configured source (enabled flag mirrors YAML)."""
    configs = source_configs()
    with session_scope(url) as s:
        for cfg in configs:
            row = s.scalar(select(SourceRow).where(SourceRow.name == cfg["name"]))
            if row is None:
                row = SourceRow(name=cfg["name"], type=cfg.get("type", ""))
                s.add(row)
            row.type = cfg.get("type", "")
            row.enabled = bool(cfg.get("enabled", True))
            if not row.enabled and row.status == "never_run":
                row.status = "disabled"
                row.last_error = cfg.get("disabled_reason") or row.last_error
    return len(configs)


def sync_brands(url: str | None = None) -> int:
    """Upsert brands from brands.yaml."""
    configs = brand_configs()
    with session_scope(url) as s:
        for b in configs:
            row = s.scalar(select(Brand).where(Brand.key == b["key"]))
            if row is None:
                row = Brand(key=b["key"], name=b.get("name", b["key"]))
                s.add(row)
            row.name = b.get("name", b["key"])
            row.official_domains = [d.lower() for d in b.get("official_domains", [])]
            row.official_handles = b.get("official_handles", [])
            row.support_numbers = b.get("support_numbers", [])
            row.aliases = flat_aliases(b)
            row.synthetic = bool(b.get("synthetic", False))
    return len(configs)
