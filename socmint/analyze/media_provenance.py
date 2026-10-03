"""Media provenance: "seen before" via perceptual hashes; EXIF; deepfake detector interface (v0: none)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy import select

from socmint.core.db import session_scope
from socmint.core.models import FactCheck, MediaSeen, utcnow
from socmint.core.schemas import SeenBefore
from socmint.core.settings import threshold
from socmint.understand.media import hamming


class DeepfakeDetector(Protocol):
    """Interface for a future deepfake model. Must return a probability in [0,1] or None."""

    def score(self, path: str) -> float | None: ...


class NoDeepfakeDetector:
    """v0: no deepfake model. Always returns None (never claims an image is real or fake)."""

    def score(self, path: str) -> float | None:
        return None


def sync_factcheck_hashes() -> int:
    """Copy fact-check image hashes into media_seen so one table answers 'seen before'."""
    n = 0
    with session_scope() as s:
        have = {(m.phash, m.ref_id) for m in s.scalars(select(MediaSeen).where(MediaSeen.kind == "factcheck")).all()}
        for fc in s.scalars(select(FactCheck).where(FactCheck.image_hashes.is_not(None))).all():
            for h in fc.image_hashes or []:
                if (h, fc.id) not in have:
                    s.add(MediaSeen(phash=h, kind="factcheck", ref_id=fc.id, url=fc.url, synthetic=fc.synthetic,
                                    first_seen=fc.published_at or fc.ingested_at,
                                    context=f"Image used in fact-check by {fc.publisher}: {fc.title[:160]}"))
                    n += 1
    return n


def find_seen_before(hashes: list[str], max_distance: int | None = None) -> list[SeenBefore]:
    """Compare pHashes against everything seen before; closest/oldest first, one entry per earlier record."""
    if not hashes:
        return []
    max_distance = int(threshold("media.phash_max_distance", 8)) if max_distance is None else max_distance
    sync_factcheck_hashes()
    out: dict[tuple[str, int | None], SeenBefore] = {}
    with session_scope() as s:
        rows = s.scalars(select(MediaSeen)).all()  # v0: linear scan (fine to ~100k hashes)
        for h in hashes:
            for r in rows:
                d = hamming(h, r.phash)
                if d <= max_distance:
                    k = (r.kind, r.ref_id)
                    if k not in out or d < out[k].distance:
                        out[k] = SeenBefore(phash=r.phash, distance=d, kind=r.kind, first_seen=r.first_seen,
                                            context=r.context, url=r.url)
    # fact-check context first (most informative), then oldest
    return sorted(out.values(), key=lambda m: (m.kind != "factcheck", m.first_seen, m.distance))


def register_media(hashes: list[str], kind: str, ref_id: int | None, context: str, url: str | None = None,
                   when: datetime | None = None, synthetic: bool = False) -> None:
    """Record hashes so future submissions can be matched. Context must not contain personal data."""
    with session_scope() as s:
        for h in hashes:
            s.add(MediaSeen(phash=h, kind=kind, ref_id=ref_id, context=context[:300], url=url,
                            first_seen=when or utcnow(), synthetic=synthetic))
