"""Investigation config: what to collect for an event, a subject (public account(s)) or a group of accounts.

Created from YAML (`config/investigations/*.yaml`, several documents per file allowed), the CLI or the
showcase dashboard. A `purpose` is always required: it records why the target is being monitored.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

NO_KEY_SOURCES = ["bluesky", "mastodon", "news_rss", "gdelt"]
# logical source name -> (sources.yaml type, rss kind or None)
LOGICAL_SOURCES: dict[str, tuple[str, str | None]] = {
    "bluesky": ("bluesky", None), "mastodon": ("mastodon", None), "news_rss": ("rss", "news"),
    "factcheck_rss": ("rss", "factcheck"), "gdelt": ("gdelt", None), "google_factcheck": ("google_factcheck", None),
    "youtube": ("youtube", None), "reddit": ("reddit", None), "telegram": ("telegram_channels", None),
}
# platform used in `handles:` -> logical source that can collect accounts on it
PLATFORMS = {"bluesky": "bluesky", "mastodon": "mastodon", "youtube": "youtube", "reddit": "reddit",
             "telegram": "telegram"}


class Window(BaseModel):
    start: date | datetime | None = None
    end: date | datetime | None = None


class InvestigationConfig(BaseModel):
    kind: Literal["event", "subject", "group"]
    name: str = Field(min_length=1, max_length=300)
    description: str = ""
    purpose: str
    window: Window = Field(default_factory=Window)
    languages: list[str] = Field(default_factory=list)
    terms: dict[str, list[str]] = Field(default_factory=dict)       # event: per language
    places: list[str] = Field(default_factory=list)
    handles: dict[str, list[str]] = Field(default_factory=dict)     # subject/group: per platform
    channels: dict[str, list[str]] = Field(default_factory=dict)    # event: telegram channels / subreddits to search in
    sources: list[str] = Field(default_factory=list)
    promoted_from: dict[str, Any] | None = None                     # group promoted from a coordination group
    synthetic: bool = False

    @field_validator("purpose")
    @classmethod
    def _purpose_required(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("purpose is required: say why this target is being monitored")
        return v.strip()

    @field_validator("terms", "handles", "channels", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> Any:
        return {k: ([x] if isinstance(x, str) else list(x or [])) for k, x in (v or {}).items()}

    @model_validator(mode="after")
    def _check(self) -> InvestigationConfig:
        if self.kind == "event" and not any(self.terms.values()):
            raise ValueError("an event needs at least one keyword/hashtag in `terms`")
        if self.kind in ("subject", "group"):
            if not any(self.handles.values()):
                raise ValueError(f"a {self.kind} needs at least one `platform: [handle]` in `handles`")
            unknown = sorted(set(self.handles) - set(PLATFORMS))
            if unknown:
                raise ValueError(f"unknown platform(s) in handles: {unknown}; choose from {sorted(PLATFORMS)}")
        if not self.sources:
            self.sources = (list(NO_KEY_SOURCES) if self.kind == "event"
                            else [PLATFORMS[p] for p in self.handles if self.handles[p]])
        bad = sorted(set(self.sources) - set(LOGICAL_SOURCES))
        if bad:
            raise ValueError(f"unknown source(s) {bad}; choose from {sorted(LOGICAL_SOURCES)}")
        return self

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"name", "kind", "description", "purpose", "synthetic"})


def load_file(path: str | Path) -> list[InvestigationConfig]:
    """All investigation documents in a YAML file (documents separated by `---`)."""
    with Path(path).open(encoding="utf-8") as f:
        return [InvestigationConfig(**d) for d in yaml.safe_load_all(f) if d]


def parse_handles(values: list[str]) -> dict[str, list[str]]:
    """CLI `--handle bluesky:x.bsky.social --handle mastodon:@u@mastodon.social` -> {platform: [handles]}."""
    out: dict[str, list[str]] = {}
    for v in values:
        platform, sep, handle = v.partition(":")
        if not sep or not handle.strip():
            raise ValueError(f"handle {v!r} must look like platform:handle")
        out.setdefault(platform.strip().lower(), []).append(handle.strip())
    return out
