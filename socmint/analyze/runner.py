"""Run batch analysis modules with per-module isolation (one failure never stops the others)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from socmint.core.logging import get_logger

log = get_logger(__name__)


def _media() -> dict:
    from socmint.analyze.media_provenance import sync_factcheck_hashes

    return {"factcheck_hashes_added": sync_factcheck_hashes()}


def _embeddings() -> dict:
    from socmint.analyze.claim_match import ensure_embeddings

    return {"factcheck_embeddings_updated": ensure_embeddings()}


def _impersonation() -> dict:
    from socmint.analyze.impersonation import scan_items, scan_lookalikes

    return {"items": scan_items(), "dnstwist": scan_lookalikes()}


def _narratives() -> dict:
    from socmint.analyze.narratives import run

    return run()


def _coordination() -> dict:
    from socmint.analyze.coordination import run

    return run()


def _market() -> dict:
    from socmint.analyze.market import run

    return run()


def _hate() -> dict:
    from socmint.analyze.hate import run

    return run()


def _background() -> dict:
    from socmint.analyze.claim_match import compute_background_mean

    return compute_background_mean()


MODULES: dict[str, Callable[[], dict]] = {
    "embeddings": _embeddings, "media": _media, "impersonation": _impersonation, "narratives": _narratives,
    "coordination": _coordination, "market": _market, "hate": _hate,
}
EXTRA = {"background": _background}  # explicit only, not part of "all"


def run_modules(name: str = "all") -> list[dict[str, Any]]:
    if name == "all":
        selected = list(MODULES.items())
    else:
        fn = MODULES.get(name) or EXTRA.get(name)
        if fn is None:
            raise KeyError(f"unknown module {name!r}; choose from {sorted(MODULES) + sorted(EXTRA)} or 'all'")
        selected = [(name, fn)]
    out = []
    for mod, fn in selected:
        t0 = time.monotonic()
        try:
            res = fn()
            out.append({"module": mod, "status": "ok", "result": res, "seconds": round(time.monotonic() - t0, 1)})
            log.info("module_ok", extra={"module": mod})
        except Exception as e:  # noqa: BLE001  (module isolation)
            out.append({"module": mod, "status": "error", "error": f"{type(e).__name__}: {e}"[:500]})
            log.warning("module_failed", extra={"module": mod, "error": str(e)[:300]})
    return out
