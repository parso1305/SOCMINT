"""Source interface. Every source implements `fetch(since) -> FetchResult` and never touches the DB.

Investigation-aware sources also declare `capabilities` and implement `search_terms` ("terms", for events)
and/or `account_posts` + `account_profile` ("accounts", for subjects and groups).
"""

from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, ClassVar

from socmint.core.http import PoliteClient, get_client
from socmint.core.schemas import AccountIn, FetchResult
from socmint.core.settings import get_settings, threshold


class UnsupportedCapability(RuntimeError):
    """The source cannot do this (e.g. RSS has no accounts). Reported as `unsupported`, not as an error."""


class OptedOut(RuntimeError):
    """The account opted out of indexing/unauthenticated access (Mastodon noindex, Bluesky !no-unauthenticated)."""


class Source(ABC):
    """Base class for all sources.

    Subclasses set `type_name` and, if they need credentials, `required_settings`
    (Settings attribute names); a source with missing credentials is skipped, not crashed.
    """

    type_name: ClassVar[str] = ""
    required_settings: ClassVar[tuple[str, ...]] = ()
    capabilities: ClassVar[set[str]] = set()  # subset of {"terms", "accounts"}

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
            return False, f"set {' and '.join(missing)} in .env to enable this source"
        return True, ""

    @abstractmethod
    def fetch(self, since: datetime | None) -> FetchResult:
        """Fetch new records published after `since` (None = first run)."""

    def search_terms(self, terms_by_lang: dict[str, list[str]], since: datetime | None, limit: int) -> FetchResult:
        """Posts matching any of the event terms, published after `since`. Items carry meta["matched_terms"]."""
        raise UnsupportedCapability(f"{self.type_name} cannot search by terms")

    def account_posts(self, handle: str, since: datetime | None, limit: int) -> FetchResult:
        """Recent public posts of one public account. Raises OptedOut when the account opted out."""
        raise UnsupportedCapability(f"{self.type_name} cannot collect account posts")

    def account_profile(self, handle: str) -> AccountIn:
        """Public profile of one account. Raises OptedOut when the account opted out."""
        raise UnsupportedCapability(f"{self.type_name} cannot read account profiles")


_REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """Class decorator adding a Source subclass to the type registry."""
    _REGISTRY[cls.type_name] = cls
    return cls


def source_class(type_name: str) -> type[Source] | None:
    from socmint.ingest import (  # noqa: F401  (import for registration side-effects)
        bluesky,
        ct_logs,
        factcheck_api,
        gdelt,
        mastodon,
        nse,
        reddit,
        rss,
        sebi,
        telegram_channels,
        threatfeeds,
        youtube,
    )

    return _REGISTRY.get(type_name)


def flat_terms(terms_by_lang: dict[str, list[str]] | list[str]) -> list[str]:
    """All terms across languages, de-duplicated, original spelling kept."""
    vals: Iterable[str] = (terms_by_lang if isinstance(terms_by_lang, list)
                           else [t for ts in terms_by_lang.values() for t in (ts or [])])
    return list(dict.fromkeys(str(t).strip() for t in vals if str(t).strip()))


class TermMatcher:
    """Client-side keyword matcher for event terms (en/hi/mr/Hinglish).

    Latin terms match on word boundaries of the normalised text; Devanagari terms match as substrings
    (stems catch inflections); "#tag" terms match the post's hashtags or the literal "#tag" in the text.
    """

    def __init__(self, terms: dict[str, list[str]] | list[str]):
        from socmint.understand.normalize import match_text

        self._norm = match_text
        self.terms = flat_terms(terms)
        self._compiled: list[tuple[str, str, re.Pattern[str] | None]] = []
        for t in self.terms:
            n = match_text(t)
            if not n:
                continue
            if n.startswith("#"):
                self._compiled.append((t, n[1:], None))
            elif n.isascii():
                self._compiled.append((t, n, re.compile(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])")))
            else:
                self._compiled.append((t, n, None))

    def match(self, text: str, hashtags: Iterable[str] = ()) -> list[str]:
        mt = self._norm(text or "")
        tags = {self._norm(h).lstrip("#") for h in hashtags}
        out = []
        for orig, n, pat in self._compiled:
            if orig.lstrip().startswith("#"):
                hit = n in tags or re.search(rf"#{re.escape(n)}(?![\w])", mt) is not None
            elif pat is not None:
                hit = pat.search(mt) is not None or n.replace(" ", "") in tags
            else:
                hit = n in mt or n.replace(" ", "") in tags
            if hit:
                out.append(orig)
        return out


_HASHTAG_RE = re.compile(r"(?<![\w#&])#([\wऀ-ॿ]{2,64})")
_MENTION_RE = re.compile(r"(?<![\w@.])@([A-Za-z0-9_][A-Za-z0-9_.\-]{1,60}[A-Za-z0-9_])")


def text_entities(text: str) -> dict[str, list[str]]:
    """Hashtags, @mentions and URLs found in plain text (for sources without structured entities)."""
    from socmint.understand.entities import extract_urls

    urls, _hosts = extract_urls(text or "")
    tags = list(dict.fromkeys(m.group(1) for m in _HASHTAG_RE.finditer(text or "") if not m.group(1).isdigit()))
    mentions = list(dict.fromkeys("@" + m.group(1) for m in _MENTION_RE.finditer(text or "")))
    return {"hashtags": tags, "mentions": mentions, "urls": urls}


def parse_iso(s: str | None) -> datetime | None:
    """ISO-8601 (with Z or offset) -> naive UTC."""
    if not s:
        return None
    try:
        return to_naive_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return None


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
