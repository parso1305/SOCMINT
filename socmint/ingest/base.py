"""Source interface. Every source implements `fetch(since) -> FetchResult` and never touches the DB."""

from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, ClassVar

from socmint.core.http import PoliteClient, get_client
from socmint.core.schemas import FetchResult
from socmint.core.settings import get_settings, threshold


class Source(ABC):
    """Base class for all sources.

    Subclasses set `type_name` and, if they need credentials, `required_settings`
    (Settings attribute names); a source with missing credentials is skipped, not crashed.
    """

    type_name: ClassVar[str] = ""
    required_settings: ClassVar[tuple[str, ...]] = ()

    def __init__(self, cfg: dict[str, Any], client: PoliteClient | None = None):
        self.cfg = cfg
        self.name: str = cfg["name"]
        self.client = client or get_client()
        rate = cfg.get("rate_limit_s")
        if rate is not None:
            from urllib.parse import urlparse

            for u in self.hosts():
                self.client.set_rate_limit(urlparse(u).netloc, float(rate))

    def hosts(self) -> list[str]:
        """URLs whose hosts get this source's rate limit."""
        return [u for u in [self.cfg.get("url")] if u]

    def availability(self) -> tuple[bool, str]:
        """(available, reason). Default: all required settings present."""
        s = get_settings()
        missing = [k.upper() for k in self.required_settings if not getattr(s, k, "")]
        if missing:
            return False, f"missing key(s) in .env: {', '.join(missing)}"
        return True, ""

    @abstractmethod
    def fetch(self, since: datetime | None) -> FetchResult:
        """Fetch new records published after `since` (None = first run)."""


_REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """Class decorator adding a Source subclass to the type registry."""
    _REGISTRY[cls.type_name] = cls
    return cls


def source_class(type_name: str) -> type[Source] | None:
    from socmint.ingest import (  # noqa: F401  (import for registration side-effects)
        ct_logs,
        factcheck_api,
        gdelt,
        nse,
        reddit,
        rss,
        sebi,
        telegram_channels,
        threatfeeds,
        youtube,
    )

    return _REGISTRY.get(type_name)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_summary(raw: str | None, max_chars: int | None = None) -> str:
    """Strip HTML and truncate to the configured short-summary length (no full article bodies)."""
    max_chars = max_chars or int(threshold("ingest.max_summary_chars", 400))
    text = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", raw or ""))).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return text


def to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt
