"""`socmint demo`: offline, zero-key, end-to-end run on synthetic fixtures in a separate DB (data/demo.db)."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any

from socmint.core.settings import PROJECT_ROOT


def demo_db_path() -> Path:
    return PROJECT_ROOT / "data" / "demo.db"


def use_demo_env(fresh: bool = False) -> str:
    """Point settings at the demo DB, force offline mode, ensure a privacy salt exists for synthetic senders."""
    from socmint.core import db
    from socmint.core.settings import get_settings, reset_settings

    path = demo_db_path()
    if fresh:
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
    os.environ["SOCMINT_DB_URL"] = f"sqlite:///{path.as_posix()}"
    os.environ["SOCMINT_OFFLINE"] = "1"
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    reset_settings()
    if not get_settings().privacy_salt:
        os.environ["PRIVACY_SALT"] = "demo-only-salt-not-for-production"
        reset_settings()
    db.get_engine.cache_clear()
    return os.environ["SOCMINT_DB_URL"]


def run_demo() -> dict[str, Any]:
    url = use_demo_env(fresh=True)

    from sqlalchemy import func, select

    from socmint import fixtures
    from socmint.analyze import claim_match, impersonation
    from socmint.analyze.embeddings import get_embedder
    from socmint.analyze.runner import run_modules
    from socmint.core.db import init_db, session_scope
    from socmint.core.models import (
        Alert,
        CoordinationGroup,
        Indicator,
        LookalikeDomain,
        Narrative,
        SourceRow,
        Submission,
    )
    from socmint.core.schemas import FetchResult, IndicatorIn
    from socmint.ingest.registry import sync_brands, sync_sources
    from socmint.ingest.store import store_result
    from socmint.tipline.pipeline import check
    from socmint.understand.entities import clear_caches

    init_db()
    sync_sources()
    sync_brands()
    impersonation.brands.cache_clear()
    loaded = {"factchecks": fixtures.load_factchecks(), "symbols": fixtures.load_symbols(), "items": fixtures.load_items(),
              "market_rows": fixtures.load_market(), "lookalikes": fixtures.load_lookalikes()}
    with session_scope() as s:  # a synthetic "reported" URL, as a threat feed would provide
        store_result(s, FetchResult(indicators=[IndicatorIn(type="url", value="http://demobank-secure-login.example/verify",
                                                            status="reported", source="fixture_threatfeed", synthetic=True),
                                                IndicatorIn(type="domain", value="demobank-secure-login.example",
                                                            status="reported", source="fixture_threatfeed", synthetic=True)]))
        s.add(SourceRow(name="fixtures (synthetic)", type="fixture", enabled=True, status="ok", last_count=sum(loaded.values())))
    clear_caches()
    claim_match.reset_index()
    embedder = get_embedder().name

    # tipline: every fixture message, from a handful of synthetic senders
    tip = Counter()
    mismatches = []
    for i, m in enumerate(fixtures.tipline_messages()):
        if m.get("requires_backend") and not embedder.startswith(m["requires_backend"]):
            continue
        r = check(m["text"], sender_id=f"demo-sender-{i % 7}", channel="demo", synthetic=True)
        tip[r.verdict.value] += 1
        ok = r.verdict.value == m["expect"] or (m["expect"] == "suspicious_or_worse"
                                                 and r.verdict.value in ("suspicious", "likely_scam"))
        if not ok:
            mismatches.append(f"{m['id']}: expected {m['expect']}, got {r.verdict.value}")
    extra = [check("Urgent: verify your DemoBank login at http://demobank-secure-login.example/verify",
                   sender_id="demo-sender-8", channel="demo", synthetic=True)]
    imgs = fixtures.ensure_images()
    extra.append(check(file_path=imgs["original"], sender_id="demo-sender-9", channel="demo", synthetic=True))
    extra.append(check(file_path=imgs["forwarded"], sender_id="demo-sender-10", channel="demo", synthetic=True))
    for r in extra:
        tip[r.verdict.value] += 1

    modules = {r["module"]: r for r in run_modules("all")}

    with session_scope() as s:
        alerts = Counter(m for (m,) in s.execute(select(Alert.module)).all())
        narrs = s.scalars(select(Narrative)).all()
        top_narr = max(narrs, key=lambda n: (bool(n.matched_factcheck_ids), n.growth_rate, n.size), default=None)
        top_coord = s.scalars(select(CoordinationGroup).order_by(CoordinationGroup.score.desc())).first()
        market = s.scalar(select(Alert).where(Alert.module == "market"))
        looks = s.scalar(select(func.count(LookalikeDomain.id)).where(LookalikeDomain.registered))
        summary = {
            "db": url, "embedder": embedder, "loaded": loaded, "tipline_verdicts": dict(tip),
            "fixture_mismatches": mismatches,
            "threat_feed_check": extra[0].verdict.value,
            "image_first_submission": extra[1].verdict.value, "image_forwarded_copy": extra[2].verdict.value,
            "submissions": s.scalar(select(func.count(Submission.id))),
            "queued_for_review": s.scalar(select(func.count(Submission.id)).where(Submission.status == "queued")),
            "indicators": s.scalar(select(func.count(Indicator.id))),
            "registered_lookalikes": looks, "alerts_by_module": dict(alerts),
            "top_narrative": {"keywords": top_narr.keywords[:6], "size": top_narr.size, "growth": top_narr.growth_rate,
                              "linked_factchecks": top_narr.matched_factcheck_ids} if top_narr else None,
            "top_coordination": {"score": top_coord.score, "accounts": top_coord.n_accounts,
                                 "minutes": top_coord.time_span_minutes, "text": top_coord.sample_text[:70]}
            if top_coord else None,
            "market_alert": {"title": market.title, "risk_score": market.evidence["risk_score"],
                             "factors": list(market.evidence["risk_factors"])} if market else None,
            "modules": {k: v["status"] for k, v in modules.items()},
        }
    return summary
