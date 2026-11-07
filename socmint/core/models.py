"""SQLAlchemy 2.x ORM models.

Portable types only (String, Text, Integer, Float, Boolean, DateTime, JSON, LargeBinary)
so SQLite can be swapped for Postgres without schema changes.

Privacy notes:
- `Submission` never stores phone numbers, usernames or names; only `sender_hash`.
- `Indicator` has no foreign key or column linking it to a submission or sender.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-naive UTC now (SQLite stores naive datetimes; all times are UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class SourceRow(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    type: Mapped[str] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="never_run")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_count: Mapped[int] = mapped_column(Integer, default=0)


class Item(Base):
    """Anything ingested from a public source (news, GDELT, public channels, ...)."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_item_source_ext"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    source_type: Mapped[str] = mapped_column(String(50), default="")
    external_id: Mapped[str] = mapped_column(String(512))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lang: Mapped[str | None] = mapped_column(String(16), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    media_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    # Post fields (M9). All nullable so rows from before the migration stay valid.
    author_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    author_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashtags: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    mentions: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    urls: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    engagement: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reply_to_external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    forwarded_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)  # [{type, url, phash}]; files never stored
    text_en: Mapped[str | None] = mapped_column(Text, nullable=True)  # translation: later


class Account(Base):
    """A public account/channel/page as the platform's official API returns it (public fields only)."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("platform", "platform_id", name="uq_account_platform_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(30), index=True)
    platform_id: Mapped[str] = mapped_column(String(255))
    handle: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # account creation, if the API gives it
    followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    following: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posts_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # public bio
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Investigation(Base):
    """An analyst-defined target: an event, a subject (public account(s)) or a group of accounts."""

    __tablename__ = "investigations"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # event | subject | group
    name: Mapped[str] = mapped_column(String(300), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    purpose: Mapped[str] = mapped_column(Text)  # required: why this target is monitored
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused | archived
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class InvestigationItem(Base):
    __tablename__ = "investigation_items"
    __table_args__ = (UniqueConstraint("investigation_id", "item_id", name="uq_inv_item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(Integer, index=True)
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    matched_by: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # which source/term/handle matched
    matched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InvestigationAccount(Base):
    __tablename__ = "investigation_accounts"
    __table_args__ = (UniqueConstraint("investigation_id", "account_id", name="uq_inv_account"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(Integer, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(20))  # subject | member | top_author
    added_by: Mapped[str] = mapped_column(String(20))  # analyst | coordination | collector


class ItemLabel(Base):
    """Category label on a post. Analyst labels override rule / zero-shot labels in views."""

    __tablename__ = "item_labels"
    __table_args__ = (UniqueConstraint("item_id", "label", "method", name="uq_item_label_method"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    label: Mapped[str] = mapped_column(String(50), index=True)
    sublabel: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # analyst removal = 0.0
    method: Mapped[str] = mapped_column(String(20))  # rule | zero_shot | analyst
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Edge(Base):
    """Network edge for graph views: account -> account / URL / hashtag, derived from one post."""

    __tablename__ = "edges"
    __table_args__ = (UniqueConstraint("kind", "src", "dst", "item_id", name="uq_edge"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # forward|reply|mention|shares_url|uses_hashtag|co_posts
    src: Mapped[str] = mapped_column(String(300), index=True)  # account key "platform:platform_id"
    dst: Mapped[str] = mapped_column(Text)  # account key, URL or "#hashtag"
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    investigation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # socmint.core.status.STATUSES: ok | partial | throttled | no_key | offline | opted_out | error
    status: Mapped[str] = mapped_column(String(30))
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    new: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class FactCheck(Base):
    """Fact-check metadata only (title/url/publisher/date/lang/short claim). No article bodies."""

    __tablename__ = "factchecks"
    id: Mapped[int] = mapped_column(primary_key=True)
    publisher: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, unique=True)
    claim_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lang: Mapped[str | None] = mapped_column(String(16), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(100), default="")
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_hashes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Submission(Base):
    """Tipline submission. De-identified: sender_hash only, redacted text."""

    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    sender_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    channel: Mapped[str] = mapped_column(String(30), default="cli")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    type: Mapped[str] = mapped_column(String(10), default="text")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    lang: Mapped[str | None] = mapped_column(String(16), nullable=True)
    media_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str] = mapped_column(String(30))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reasons: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="auto")
    review_outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Indicator values awaiting analyst review (so the outcome can update their status). Time-limited link:
    # wiped on review and by retention. Evidence in `reasons` is sanitised and never holds phone/UPI values.
    pending_indicators: Mapped[list[Any]] = mapped_column(JSON, default=list)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Indicator(Base):
    """Suspected/reported scam artifact. Deliberately has NO link to any submission or sender."""

    __tablename__ = "indicators"
    __table_args__ = (UniqueConstraint("type", "value", name="uq_indicator_type_value"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(30), index=True)  # url/domain/phone/upi/telegram_handle
    value: Mapped[str] = mapped_column(String(1024), index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="suspected")  # reported/suspected/confirmed/benign
    linked_brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Narrative(Base):
    __tablename__ = "narratives"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    cluster_id: Mapped[int] = mapped_column(Integer)
    keywords: Mapped[list[Any]] = mapped_column(JSON, default=list)
    size: Mapped[int] = mapped_column(Integer)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    growth_rate: Mapped[float] = mapped_column(Float, default=0.0)
    count_24h: Mapped[int] = mapped_column(Integer, default=0)
    matched_factcheck_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    sample_item_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    investigation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # set by scoped runs


class CoordinationGroup(Base):
    """A group of near-duplicate public posts: 'possible coordination' (never 'bots')."""

    __tablename__ = "coordination_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    score: Mapped[float] = mapped_column(Float)
    n_items: Mapped[int] = mapped_column(Integer)
    n_accounts: Mapped[int] = mapped_column(Integer)
    n_channels: Mapped[int] = mapped_column(Integer)
    time_span_minutes: Mapped[float] = mapped_column(Float)
    synchrony: Mapped[float] = mapped_column(Float)
    sample_text: Mapped[str] = mapped_column(Text, default="")
    item_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    edges: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    investigation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # set by scoped runs


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(String(10))  # low/medium/high
    title: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedup_key: Mapped[str | None] = mapped_column(String(300), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Brand(Base):
    __tablename__ = "brands"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    official_domains: Mapped[list[Any]] = mapped_column(JSON, default=list)
    official_handles: Mapped[list[Any]] = mapped_column(JSON, default=list)
    support_numbers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class LookalikeDomain(Base):
    __tablename__ = "lookalike_domains"
    __table_args__ = (UniqueConstraint("brand", "domain", name="uq_lookalike_brand_domain"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    brand: Mapped[str] = mapped_column(String(100), index=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    method: Mapped[str] = mapped_column(String(50))  # dnstwist:<fuzzer> / crt.sh / fixture
    registered: Mapped[bool] = mapped_column(Boolean, default=False)
    dns_a: Mapped[list[Any]] = mapped_column(JSON, default=list)
    dns_mx: Mapped[list[Any]] = mapped_column(JSON, default=list)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_checked: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class MediaSeen(Base):
    """Perceptual hashes of media seen before (submissions, fact-check images, video frames)."""

    __tablename__ = "media_seen"
    id: Mapped[int] = mapped_column(primary_key=True)
    phash: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # submission/factcheck/item/video_frame
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class MarketDaily(Base):
    __tablename__ = "market_daily"
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_market_symbol_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    mentions: Mapped[int] = mapped_column(Integer, default=0)
    mention_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Symbol(Base):
    """Listed equity symbols (NSE EQUITY_L.csv) used for ticker extraction and liquidity flags."""

    __tablename__ = "symbols"
    symbol: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    series: Mapped[str] = mapped_column(String(10), default="")
    isin: Mapped[str] = mapped_column(String(20), default="")
    large_cap: Mapped[bool] = mapped_column(Boolean, default=False)  # member of the configured large-cap index
    source: Mapped[str] = mapped_column(String(50), default="")
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Preference(Base):
    """Per-sender preferences (reply language), keyed by sender_hash only."""

    __tablename__ = "preferences"
    sender_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    lang: Mapped[str] = mapped_column(String(8), default="en")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
