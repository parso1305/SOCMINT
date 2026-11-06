"""Pydantic schemas shared across ingest, analysis, tipline and API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    """The only verdicts a citizen can receive. There is deliberately no 'true' verdict."""

    FACTCHECK_MATCH = "factcheck_match"
    SEEN_BEFORE = "seen_before"
    LIKELY_SCAM = "likely_scam"
    SUSPICIOUS = "suspicious"
    NOT_VERIFIED = "not_verified"


class ItemIn(BaseModel):
    """Normalised record produced by a Source for the `items` table."""

    source: str
    source_type: str = ""
    external_id: str
    url: str | None = None
    channel: str | None = None
    author_handle: str | None = None
    published_at: datetime | None = None
    lang: str | None = None
    text: str = ""
    media_hash: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    synthetic: bool = False
    # post fields (filled by social collectors; empty for news/feeds)
    author_id: str | None = None
    author_display_name: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    engagement: dict[str, Any] = Field(default_factory=dict)
    reply_to_external_id: str | None = None
    forwarded_from: str | None = None
    media: list[dict[str, Any]] = Field(default_factory=list)
    text_en: str | None = None


class AccountIn(BaseModel):
    """Public profile of an account/channel, exactly as far as the platform's official API exposes it."""

    platform: str
    platform_id: str
    handle: str | None = None
    display_name: str | None = None
    url: str | None = None
    created_at: datetime | None = None
    followers: int | None = None
    following: int | None = None
    posts_count: int | None = None
    description: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    synthetic: bool = False

    @property
    def key(self) -> str:
        return account_key(self.platform, self.platform_id)


def account_key(platform: str, platform_id: str) -> str:
    """Stable account key used in `edges`: "platform:platform_id"."""
    return f"{platform}:{platform_id}"


class FactCheckIn(BaseModel):
    publisher: str
    title: str
    url: str
    claim_text: str | None = None
    rating: str | None = None
    lang: str | None = None
    published_at: datetime | None = None
    source: str = ""
    image_hashes: list[str] = Field(default_factory=list)
    synthetic: bool = False


class IndicatorIn(BaseModel):
    type: str
    value: str
    status: str = "suspected"
    source: str = ""
    linked_brand: str | None = None
    synthetic: bool = False


class DomainIn(BaseModel):
    brand: str
    domain: str
    method: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    synthetic: bool = False


class MarketIn(BaseModel):
    symbol: str
    date: datetime
    close: float | None = None
    volume: float | None = None
    turnover: float | None = None
    synthetic: bool = False


class FetchResult(BaseModel):
    """What a Source returns. Any list may be empty."""

    items: list[ItemIn] = Field(default_factory=list)
    factchecks: list[FactCheckIn] = Field(default_factory=list)
    indicators: list[IndicatorIn] = Field(default_factory=list)
    domains: list[DomainIn] = Field(default_factory=list)
    market: list[MarketIn] = Field(default_factory=list)
    symbols: list[dict[str, Any]] = Field(default_factory=list)
    accounts: list[AccountIn] = Field(default_factory=list)

    def count(self) -> int:
        return sum(len(x) for x in (self.items, self.factchecks, self.indicators, self.domains, self.market, self.symbols))

    def extend(self, other: FetchResult) -> FetchResult:
        """Append every list of `other` (used when a source runs several queries)."""
        for f in type(self).model_fields:
            getattr(self, f).extend(getattr(other, f))
        return self


class Reason(BaseModel):
    """One piece of evidence behind a verdict or alert."""

    code: str
    message: str
    weight: float = 0.0
    evidence: dict[str, Any] = Field(default_factory=dict)


class FactCheckMatch(BaseModel):
    factcheck_id: int
    publisher: str
    title: str
    url: str
    rating: str | None = None
    published_at: datetime | None = None
    lang: str | None = None
    score: float


class SeenBefore(BaseModel):
    phash: str
    distance: int
    kind: str
    first_seen: datetime
    context: str
    url: str | None = None


class ExtractedIndicator(BaseModel):
    type: str
    value: str
    linked_brand: str | None = None


class CheckResult(BaseModel):
    """Result returned by the tipline pipeline (CLI, API and bot share this)."""

    verdict: Verdict
    score: float = 0.0
    reasons: list[Reason] = Field(default_factory=list)
    matches: list[FactCheckMatch] = Field(default_factory=list)
    seen_before: list[SeenBefore] = Field(default_factory=list)
    indicators: list[ExtractedIndicator] = Field(default_factory=list)
    lang: str = "en"
    reply_lang: str = "en"
    reply_text: str = ""
    disclaimer: str = ""
    submission_id: int | None = None
    status: str = "auto"
    timings_ms: dict[str, float] = Field(default_factory=dict)
    degraded: list[str] = Field(default_factory=list)
