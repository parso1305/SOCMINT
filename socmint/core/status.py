"""The one set of collection status words, used in collection_runs.status, CLI lines, the collection log and the
showcase chips.

ok         everything the source was asked for was fetched
partial    something was fetched but not everything (a quota ran out, some subject accounts opted out, ...)
throttled  the source is limiting this IP (HTTP 429 or an active cooldown); nothing was sent or kept
no_key     the source needs a key in .env; the detail names the variable
offline    offline mode (SOCMINT_OFFLINE=1, the demo) or no network
opted_out  every requested account opted out of unauthenticated access / indexing
error      anything else (unknown source, robots.txt disallows, upstream failure); the detail says what
"""

from __future__ import annotations

STATUSES = ("ok", "partial", "throttled", "no_key", "offline", "opted_out", "error")
# words written before M17.3 (older databases still hold them)
_LEGACY = {"missing_key": "no_key", "unsupported": "error", "blocked_by_robots": "error"}


def normalize(status: str | None) -> str:
    """A stored status in today's words (older rows may say missing_key / unsupported / blocked_by_robots)."""
    s = _LEGACY.get(status or "", status or "error")
    return s if s in STATUSES else "error"
