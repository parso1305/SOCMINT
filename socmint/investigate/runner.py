"""Investigation lifecycle: create -> collect (per-source isolation) -> enrich -> analyze (scoped) -> promote / delete.

Every create, collect, view, export, promote and delete is written to `audit_log`.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from socmint.core.db import audit, session_scope
from socmint.core.http import HttpError, OfflineError, RobotsDisallowed
from socmint.core.logging import get_logger
from socmint.core.models import (
    Account,
    Alert,
    CollectionRun,
    CoordinationGroup,
    Edge,
    Investigation,
    InvestigationAccount,
    InvestigationItem,
    Item,
    ItemLabel,
    Narrative,
    utcnow,
)
from socmint.core.schemas import AccountIn, FetchResult, IndicatorIn, account_key
from socmint.core.settings import threshold
from socmint.ingest.base import OptedOut, Source, TermMatcher, UnsupportedCapability, source_class
from socmint.ingest.registry import source_configs
from socmint.ingest.store import (
    item_account_key,
    link_accounts,
    link_items,
    store_accounts,
    store_edges,
    store_factchecks,
    store_indicators,
    upsert_items,
)
from socmint.investigate.config import LOGICAL_SOURCES, PLATFORMS, InvestigationConfig

log = get_logger(__name__)
SourceFactory = Callable[[str, dict[str, Any]], Source]


class InvestigationNotFound(KeyError):
    pass


def actor() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "analyst"


# ------------------------------------------------------------------------------------------- create / get
def create(cfg: InvestigationConfig, who: str | None = None) -> int:
    """Create an investigation (explicit analyst action; `purpose` is required by the config model)."""
    with session_scope() as s:
        if s.scalar(select(Investigation.id).where(Investigation.name == cfg.name)):
            raise ValueError(f"an investigation named {cfg.name!r} already exists")
        inv = Investigation(kind=cfg.kind, name=cfg.name, description=cfg.description, purpose=cfg.purpose,
                            config=cfg.to_json(), synthetic=cfg.synthetic)
        s.add(inv)
        s.flush()
        audit(s, who or actor(), "investigation_create",
              f"id={inv.id} kind={cfg.kind} name={cfg.name} purpose={cfg.purpose}")
        return inv.id


def get(ref: int | str) -> Investigation:
    """By id or exact name (detached copy)."""
    with session_scope() as s:
        inv = None
        if isinstance(ref, int) or str(ref).isdigit():
            inv = s.get(Investigation, int(ref))
        if inv is None:
            inv = s.scalar(select(Investigation).where(Investigation.name == str(ref)))
        if inv is None:
            raise InvestigationNotFound(f"no investigation {ref!r}")
        s.expunge(inv)
        return inv


def config_of(inv: Investigation) -> InvestigationConfig:
    return InvestigationConfig(kind=inv.kind, name=inv.name, description=inv.description, purpose=inv.purpose,
                               synthetic=inv.synthetic, **(inv.config or {}))


def list_all() -> list[dict[str, Any]]:
    with session_scope() as s:
        posts = dict(s.execute(select(InvestigationItem.investigation_id, func.count())
                               .group_by(InvestigationItem.investigation_id)).all())
        accs = dict(s.execute(select(InvestigationAccount.investigation_id, func.count())
                              .group_by(InvestigationAccount.investigation_id)).all())
        return [{"id": i.id, "kind": i.kind, "name": i.name, "status": i.status, "purpose": i.purpose,
                 "posts": posts.get(i.id, 0), "accounts": accs.get(i.id, 0), "created_at": i.created_at,
                 "last_collected_at": i.last_collected_at, "synthetic": i.synthetic}
                for i in s.scalars(select(Investigation).order_by(Investigation.id)).all()]


def record_view(inv_id: int, where: str, who: str | None = None) -> None:
    with session_scope() as s:
        audit(s, who or actor(), "investigation_view", f"id={inv_id} via={where}")


# ------------------------------------------------------------------------------------------- sources
def resolve_sources(logical: str) -> list[dict[str, Any]]:
    """sources.yaml entries behind a logical source name (e.g. news_rss -> every enabled news RSS feed)."""
    stype, kind = LOGICAL_SOURCES[logical]
    return [c for c in source_configs() if c.get("type") == stype and c.get("enabled", True)
            and (kind is None or c.get("kind", "news") == kind)]


def default_factory(logical: str, cfg: dict[str, Any]) -> Source:
    cls = source_class(cfg.get("type", ""))
    if cls is None:
        raise UnsupportedCapability(f"unknown source type {cfg.get('type')!r}")
    return cls(cfg)


def _since(inv: Investigation, cfg: InvestigationConfig) -> datetime:
    if inv.last_collected_at:
        return inv.last_collected_at
    if cfg.window.start:
        st = cfg.window.start
        return st if isinstance(st, datetime) else datetime(st.year, st.month, st.day)
    days = int(threshold("investigate.default_window_days", 7)) if cfg.kind == "event" \
        else int(threshold("investigate.subject_window_days", 30))
    return utcnow() - timedelta(days=days)


def _window_ok(when: datetime | None, cfg: InvestigationConfig) -> bool:
    end = cfg.window.end
    if not end or when is None:
        return True
    end_dt = end if isinstance(end, datetime) else datetime(end.year, end.month, end.day) + timedelta(days=1)
    return when <= end_dt


# ------------------------------------------------------------------------------------------- collect
def collect(ref: int | str, only_source: str | None = None, factory: SourceFactory | None = None,
            progress: Callable[[str, dict[str, Any]], None] | None = None, enrich_after: bool = True,
            who: str | None = None, resolver: Callable[[str], list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Collect for one investigation: every configured source in isolation (one failure never stops the rest).

    Writes collection_runs + audit_log rows, links posts (with `matched_by`) and accounts, derives edges,
    then enriches the new posts. `progress(source, run_dict)` is called after each source (dashboard status).
    `factory` / `resolver` replace real sources (the offline demo and tests use them).
    """
    inv = get(ref)
    cfg = config_of(inv)
    factory = factory or default_factory
    resolve = resolver or resolve_sources
    since = _since(inv, cfg)
    limit = int(threshold("investigate.per_source_limit", 200))
    sources = [only_source] if only_source else cfg.sources
    runs: list[dict[str, Any]] = []
    new_ids: set[int] = set()
    with session_scope() as s:
        audit(s, who or actor(), "investigation_collect", f"id={inv.id} sources={','.join(sources)} since={since}")
    def report(name: str, run: dict[str, Any]) -> None:
        runs.append(run)
        if progress:
            progress(name, run)

    jobs: list[tuple[str, dict[str, Any]]] = []
    for logical in sources:
        if logical not in LOGICAL_SOURCES:
            report(logical, _finish_run(inv, logical, "unsupported", detail=f"unknown source {logical!r}"))
            continue
        cfgs = resolve(logical)
        if not cfgs:
            report(logical, _finish_run(inv, logical, "unsupported",
                                        detail="no enabled source of this kind in config/sources.yaml"))
        jobs += [(logical, scfg) for scfg in cfgs]
    # Fetch in parallel (network only); store sequentially here, in completion order, so SQLite sees one writer
    # and `progress` (Streamlit) is only ever called from this thread.
    workers = max(1, int(threshold("investigate.max_parallel_sources", 4)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="collect") as pool:
        futures = [pool.submit(_fetch_one, inv, cfg, logical, scfg, factory, since, limit) for logical, scfg in jobs]
        for fut in as_completed(futures):
            fetched = fut.result()
            run, ids = _store_fetched(inv, fetched)
            new_ids |= ids
            report(fetched.name, run)
    _link_top_authors(inv.id)
    enriched: dict[str, Any] = {}
    if enrich_after and new_ids:
        enriched = enrich(sorted(new_ids), event=(cfg.kind == "event"), terms=cfg.terms)
    with session_scope() as s:
        row = s.get(Investigation, inv.id)
        row.last_collected_at = utcnow()
    return {"investigation_id": inv.id, "since": since.isoformat(), "runs": runs, "new_posts": len(new_ids),
            "enrich": enriched}


def _finish_run(inv: Investigation, source: str, status: str, started: datetime | None = None, fetched: int = 0,
                new: int = 0, detail: str | None = None) -> dict[str, Any]:
    with session_scope() as s:
        s.add(CollectionRun(investigation_id=inv.id, source=source, started_at=started or utcnow(), finished_at=utcnow(),
                            status=status, fetched=fetched, new=new, detail=(detail or None) and detail[:2000],
                            synthetic=inv.synthetic))
    return {"source": source, "status": status, "fetched": fetched, "new": new, "detail": detail}


@dataclass
class _Fetched:
    """One source's fetch step result, handed from a worker thread to the storing thread."""

    name: str
    started: datetime
    t0: float
    status: str = "ok"
    detail: str | None = None
    res: FetchResult | None = None
    matched: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    account_links: list[tuple[str, str, str]] = field(default_factory=list)


def _store_fetched(inv: Investigation, f: _Fetched) -> tuple[dict[str, Any], set[int]]:
    """Store step (calling thread only): DB writes + the collection_runs row."""
    if f.res is None:
        return _finish_run(inv, f.name, f.status, f.started, detail=f.detail), set()
    try:
        new_ids = _store(inv, f.res, f.matched, f.account_links)
    except Exception as e:  # noqa: BLE001  (a storage failure stays this source's failure)
        log.warning("investigation_store_failed", extra={"source": f.name, "error": str(e)[:300]})
        return _finish_run(inv, f.name, "error", f.started, detail=f"{type(e).__name__}: {e}"[:1000]), set()
    detail = "; ".join(f.res.notes) or None
    return _finish_run(inv, f.name, f.status, f.started, fetched=len(f.res.items), new=len(new_ids),
                       detail=(detail or "") + f" ({time.monotonic() - f.t0:.1f} s)"), new_ids


def _fetch_one(inv: Investigation, cfg: InvestigationConfig, logical: str, scfg: dict[str, Any],
               factory: SourceFactory, since: datetime, limit: int) -> _Fetched:
    """Fetch step (worker thread): network only, no DB access, never raises."""
    out = _Fetched(name=scfg["name"], started=utcnow(), t0=time.monotonic())
    # per-investigation scoping of "search within" lists (Telegram channels, subreddits)
    scfg = dict(scfg)
    if logical == "telegram" and (cfg.channels.get("telegram") or cfg.handles.get("telegram")):
        scfg["channels"] = cfg.channels.get("telegram") or cfg.handles.get("telegram")
    if logical == "reddit" and cfg.channels.get("reddit"):
        scfg["subreddits"] = cfg.channels["reddit"]

    def fail(status: str, detail: str) -> _Fetched:
        out.status, out.detail = status, detail
        return out

    name = out.name
    try:
        src = factory(logical, scfg)
        ok, why = src.availability()
        if not ok:
            return fail("missing_key", why)
        res = FetchResult()
        matched: dict[tuple[str, str], dict[str, Any]] = {}
        account_links: list[tuple[str, str, str]] = []  # (account key, role, added_by)
        if cfg.kind == "event":
            if "terms" not in src.capabilities:
                raise UnsupportedCapability(f"{name} cannot search by terms")
            res = src.search_terms(cfg.terms, since, limit)
            matcher = TermMatcher(cfg.terms)
            places = TermMatcher(cfg.places) if cfg.places else None
            for it in res.items:
                terms = it.meta.get("matched_terms") or matcher.match(it.text, it.hashtags)
                mb: dict[str, Any] = {"source": name, "terms": terms}
                if not terms and it.meta.get("query"):
                    mb["query"] = it.meta["query"]
                if places:
                    mb["places"] = places.match(it.text)
                matched[(it.source, it.external_id)] = mb
        else:
            platform = next((p for p, lg in PLATFORMS.items() if lg == logical), None)
            handles = cfg.handles.get(platform or "", [])
            if not handles:
                return fail("ok", f"no {platform} handles in this investigation")
            if "accounts" not in src.capabilities:
                raise UnsupportedCapability(f"{name} cannot collect accounts")
            role = "subject" if cfg.kind == "subject" else "member"
            added_by = "coordination" if cfg.promoted_from else "analyst"
            for h in handles:
                try:
                    part = src.account_posts(h, since, limit)
                except OptedOut as e:
                    res.notes.append(f"skipped {h}: {e}")
                    continue
                prof = part.accounts[0] if part.accounts else src.account_profile(h)
                if not part.accounts:
                    part.accounts.append(prof)
                account_links.append((prof.key, role, added_by))
                for it in part.items:
                    matched[(it.source, it.external_id)] = {"source": name, "handle": h}
                res.extend(part)
        res.items = [it for it in res.items if _window_ok(it.published_at, cfg)]
        for it in res.items:
            it.meta.setdefault("collected_for", inv.id)  # kept only on rows this collection creates
            it.synthetic = it.synthetic or inv.synthetic
        out.res, out.matched, out.account_links = res, matched, account_links
        return out
    except UnsupportedCapability as e:
        return fail("unsupported", str(e))
    except RobotsDisallowed as e:
        return fail("blocked_by_robots", str(e))
    except OfflineError as e:
        return fail("offline", str(e))
    except (HttpError, Exception) as e:  # noqa: BLE001  (per-source isolation is the whole point)
        log.warning("investigation_source_failed", extra={"source": name, "error": str(e)[:300]})
        return fail("error", f"{type(e).__name__}: {e}"[:1000])


def _store(inv: Investigation, res: FetchResult, matched: dict[tuple[str, str], dict[str, Any]],
           account_links: list[tuple[str, str, str]]) -> set[int]:
    with session_scope() as s:
        ids, new_ids = upsert_items(s, res.items)
        if res.factchecks:
            store_factchecks(s, res.factchecks)
        authors = [AccountIn(platform=it.source_type, platform_id=it.author_id or it.author_handle,
                             handle=it.author_handle, display_name=it.author_display_name, synthetic=it.synthetic)
                   for it in res.items if (it.author_id or it.author_handle) and it.source_type in PLATFORMS]
        for a in res.accounts:
            a.synthetic = a.synthetic or inv.synthetic
        acc_ids, _ = store_accounts(s, authors + res.accounts)  # full profiles win over post-author stubs
        link_items(s, inv.id, {ids[k]: mb for k, mb in matched.items() if k in ids})
        link_accounts(s, inv.id, [(acc_ids[k], role, by) for k, role, by in account_links if k in acc_ids])
        store_edges(s, ids.values(), investigation_id=inv.id)
    if res.factchecks:
        from socmint.analyze import claim_match

        claim_match.reset_index()
    return new_ids


def _link_top_authors(inv_id: int) -> None:
    n = int(threshold("investigate.top_authors", 25))
    with session_scope() as s:
        rows = s.execute(select(Item.source_type, Item.author_id, Item.author_handle).join(
            InvestigationItem, InvestigationItem.item_id == Item.id).where(
            InvestigationItem.investigation_id == inv_id)).all()
        counts = Counter((r.source_type, r.author_id or r.author_handle) for r in rows if (r.author_id or r.author_handle))
        top = [k for k, _ in counts.most_common(n)]
        accs = {(a.platform, a.platform_id): a.id for a in s.scalars(select(Account).where(
            Account.platform.in_({p for p, _ in top} or {""}))).all()}
        link_accounts(s, inv_id, [(accs[k], "top_author", "collector") for k in top if k in accs])


# ------------------------------------------------------------------------------------------- enrich
def enrich(item_ids: list[int], event: bool = True, terms: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Language, entities, scam/impersonation signals, claim matching (events) and categories for posts.

    Scam artifacts become `indicators` exactly as in the tipline: only for likely/suspicious posts, never linked
    to a submission or sender. Zero-shot categories are added when enabled and available (else `degraded`).
    `terms` (the investigation's keywords) never count toward a category: every collected post contains them.
    """
    from socmint.analyze import categorize as cz
    from socmint.understand.lang import detect_lang

    out: dict[str, Any] = {"posts": 0, "labels": Counter(), "indicators": 0, "degraded": []}
    zs_on = bool(threshold("categorize.zero_shot", False))
    texts: dict[int, str] = {}
    for i in range(0, len(item_ids), 200):
        with session_scope() as s:
            for it in s.scalars(select(Item).where(Item.id.in_(item_ids[i : i + 200]))).all():
                det = detect_lang(it.text or "")
                plat = (it.lang or "").split("-")[0].lower() or None
                it.lang = det.code if (plat is None or det.code in ("hi", "mr", "hi-Latn")) else plat
                sig, (ent, sres) = cz.compute_signals(it.text or "", claim_matching=event)
                labels = cz.rule_labels(it.text or "", sig, investigation_terms=terms)
                cz.store_labels(s, it.id, labels, "rule", synthetic=it.synthetic)
                out["labels"].update(lab.label for lab in labels)
                ents = ent.as_dict()
                it.meta = {**(it.meta or {}), "entities": ents,
                           "signals": {"scam_score": sig.scam_score, "scam_band": sig.scam_band,
                                       "factchecks": sig.factchecks, "impersonation": sig.impersonation[:6]}}
                inds = _indicators(it, ent, sig)
                if inds:
                    out["indicators"] += store_indicators(s, inds)
                texts[it.id] = it.text or ""
                out["posts"] += 1
    if zs_on and texts:
        try:
            ids = list(texts)
            results = cz.zero_shot_labels([texts[i] for i in ids])
            with session_scope() as s:
                for iid, labs in zip(ids, results, strict=True):
                    cz.store_labels(s, iid, labs, "zero_shot")
                    out["labels"].update(lab.label for lab in labs)
        except cz.ZeroShotUnavailable as e:
            out["degraded"].append(f"zero_shot: {e}")
            log.warning("zero_shot_unavailable", extra={"error": str(e)[:200]})
    out["labels"] = dict(out["labels"])
    return out


def _indicators(it: Item, ent: Any, sig: Any) -> list[IndicatorIn]:
    """Same rule as the tipline: indicators only for likely/suspicious posts; plus brand-impersonation findings."""
    out: list[IndicatorIn] = []
    if sig.scam_band in ("likely", "suspicious"):
        brand_for = {f["value"]: f["brand"] for f in sig.impersonation}
        out += [IndicatorIn(type="url", value=u, source=it.source, synthetic=it.synthetic) for u in ent.urls]
        out += [IndicatorIn(type="domain", value=h, linked_brand=brand_for.get(h), source=it.source,
                            synthetic=it.synthetic) for h in ent.domains]
        out += [IndicatorIn(type="phone", value=p, linked_brand=brand_for.get(p), source=it.source,
                            synthetic=it.synthetic) for p in ent.phones if p.startswith("+91")]
        out += [IndicatorIn(type="upi", value=u, linked_brand=brand_for.get(u), source=it.source,
                            synthetic=it.synthetic) for u in ent.upi_ids]
    out += [IndicatorIn(type="domain" if f["type"] == "lookalike_domain" else f["type"], value=f["value"],
                        linked_brand=f["brand"], source=it.source, synthetic=it.synthetic)
            for f in sig.impersonation if f.get("type") in ("domain", "phone", "upi", "lookalike_domain")]
    return out


# ------------------------------------------------------------------------------------------- analyze
def item_ids_of(inv_id: int) -> list[int]:
    with session_scope() as s:
        return list(s.scalars(select(InvestigationItem.item_id).where(InvestigationItem.investigation_id == inv_id)
                              .order_by(InvestigationItem.item_id)).all())


def analyze(ref: int | str, modules: tuple[str, ...] = ("narratives", "coordination", "market"),
            who: str | None = None) -> dict[str, Any]:
    """Run the existing batch modules scoped to this investigation's posts (per-module isolation)."""
    from socmint.analyze import coordination, market, narratives

    inv = get(ref)
    ids = item_ids_of(inv.id)
    fns = {"narratives": narratives.run, "coordination": coordination.run, "market": market.run}
    out: dict[str, Any] = {"investigation_id": inv.id, "posts": len(ids), "modules": {}}
    history_h, need_h = collected_hours(inv.id), float(threshold("narratives.min_baseline_hours", 72))
    for m in modules:
        t0 = time.monotonic()
        try:
            kw = {"growth_alerts": history_h >= need_h} if m == "narratives" else {}
            res = fns[m](item_ids=ids, investigation_id=inv.id, **kw)
            if kw and not kw["growth_alerts"]:
                held = (f"growth alerts held back: {history_h:.0f} h of collected history, a baseline needs "
                        f"{need_h:.0f} h (narratives.min_baseline_hours)")
                res["note"] = f"{res['note']}; {held}" if res.get("note") else held
                log.info("narrative_growth_alerts_held_back", extra={"investigation_id": inv.id,
                                                                     "history_hours": round(history_h, 1)})
            out["modules"][m] = {"status": "ok", "result": res, "seconds": round(time.monotonic() - t0, 1)}
        except Exception as e:  # noqa: BLE001  (module isolation)
            out["modules"][m] = {"status": "error", "error": f"{type(e).__name__}: {e}"[:500]}
            log.warning("investigation_module_failed", extra={"analysis_module": m, "error": str(e)[:300]})
    coord = out["modules"].get("coordination", {})
    if coord.get("status") == "ok":
        _co_post_edges(inv.id, coord["result"].get("group_ids", []))
    with session_scope() as s:
        s.get(Investigation, inv.id).last_analysis = {
            "at": utcnow().isoformat(), **{m: {"status": v["status"], "run_id": (v.get("result") or {}).get("run_id")}
                                          for m, v in out["modules"].items()}}
        audit(s, who or actor(), "investigation_analyze", f"id={inv.id} modules={','.join(modules)}")
    return out


def collected_hours(inv_id: int) -> float:
    """How long this investigation has been collecting: hours since its first collection run."""
    with session_scope() as s:
        first = s.scalar(select(func.min(CollectionRun.started_at)).where(CollectionRun.investigation_id == inv_id))
    return (utcnow() - first).total_seconds() / 3600 if first else 0.0


def _co_post_edges(inv_id: int, group_ids: list[int], min_score: float | None = None) -> None:
    """`co_posts` edges between accounts that posted near-identical text, for groups at or above the coordination
    alert score (lower-scoring groups are usually organic resharing and stay in the group table only).
    A promoted group always gets its edges (`min_score=0`): the analyst chose it."""
    if min_score is None:
        min_score = float(threshold("coordination.alert_score", 0.6))
    with session_scope() as s:
        have = set(s.execute(select(Edge.src, Edge.dst).where(Edge.kind == "co_posts",
                                                              Edge.investigation_id == inv_id)).all())
        for g in s.scalars(select(CoordinationGroup).where(CoordinationGroup.id.in_(group_ids or [-1]),
                                                           CoordinationGroup.score >= min_score)).all():
            items = s.scalars(select(Item).where(Item.id.in_(g.item_ids))).all()
            keys = sorted({k for k in (item_account_key(it) for it in items) if k})[:12]
            for i, a in enumerate(keys):
                for b in keys[i + 1 :]:
                    if (a, b) in have:  # re-running analysis does not duplicate the pair
                        continue
                    have.add((a, b))
                    s.add(Edge(kind="co_posts", src=a, dst=b, item_id=None, investigation_id=inv_id,
                               synthetic=all(it.synthetic for it in items)))


# ------------------------------------------------------------------------------------------- promote / delete
def promote_group(group_id: int, name: str, purpose: str, who: str | None = None) -> int:
    """Turn a detected possible-coordination group into a `group` investigation (CCS evidence kept)."""
    with session_scope() as s:
        g = s.get(CoordinationGroup, group_id)
        if g is None:
            raise KeyError(f"no coordination group {group_id}")
        items = s.scalars(select(Item).where(Item.id.in_(g.item_ids))).all()
        handles: dict[str, list[str]] = {}
        stubs = []
        for it in items:
            if it.source_type in PLATFORMS and (it.author_id or it.author_handle):
                handles.setdefault(it.source_type, [])
                if (it.author_handle or it.author_id) not in handles[it.source_type]:
                    handles[it.source_type].append(it.author_handle or it.author_id)
                stubs.append(AccountIn(platform=it.source_type, platform_id=it.author_id or it.author_handle,
                                       handle=it.author_handle, display_name=it.author_display_name,
                                       synthetic=it.synthetic))
        if not handles:
            raise ValueError("this group has no accounts on a supported platform to promote")
        promoted = {"coordination_group_id": g.id, "score": g.score, "n_accounts": g.n_accounts,
                    "time_span_minutes": g.time_span_minutes, "synchrony": g.synchrony, "sample_text": g.sample_text,
                    "source_investigation_id": g.investigation_id,
                    "evidence": {k: v for k, v in (g.evidence or {}).items() if k != "posts"}}
        synthetic = all(it.synthetic for it in items)
        item_ids = [it.id for it in items]
    cfg = InvestigationConfig(kind="group", name=name, purpose=purpose, handles=handles, promoted_from=promoted,
                              description=f"Promoted from possible-coordination group #{group_id} "
                                          f"(score {promoted['score']:.2f}).", synthetic=synthetic)
    inv_id = create(cfg, who)
    with session_scope() as s:
        acc_ids, _ = store_accounts(s, stubs)
        link_accounts(s, inv_id, [(a, "member", "coordination") for a in acc_ids.values()])
        link_items(s, inv_id, {i: {"coordination_group": group_id} for i in item_ids})
        store_edges(s, item_ids, investigation_id=inv_id)
        audit(s, who or actor(), "investigation_promote", f"group={group_id} -> investigation={inv_id}")
    _co_post_edges(inv_id, [group_id], min_score=0.0)
    return inv_id


def delete_investigation(ref: int | str, who: str | None = None) -> dict[str, int]:
    """Remove the investigation, its links, runs and scoped analysis, plus posts/accounts nobody else references.

    Posts that existed before this investigation (e.g. from the global `socmint ingest`) are never deleted.
    """
    inv = get(ref)
    out: dict[str, int] = {}
    with session_scope() as s:
        mine = set(s.scalars(select(InvestigationItem.item_id).where(InvestigationItem.investigation_id == inv.id)).all())
        others = set(s.scalars(select(InvestigationItem.item_id).where(InvestigationItem.investigation_id != inv.id,
                                                                       InvestigationItem.item_id.in_(mine or {-1}))).all())
        candidates = s.scalars(select(Item).where(Item.id.in_((mine - others) or {-1}))).all()
        orphans = [it for it in candidates if (it.meta or {}).get("collected_for") is not None]
        orphan_ids = [it.id for it in orphans]
        my_accs = set(s.scalars(select(InvestigationAccount.account_id).where(
            InvestigationAccount.investigation_id == inv.id)).all())
        other_accs = set(s.scalars(select(InvestigationAccount.account_id).where(
            InvestigationAccount.investigation_id != inv.id)).all())
        out["links"] = s.execute(delete(InvestigationItem).where(InvestigationItem.investigation_id == inv.id)).rowcount
        out["account_links"] = s.execute(delete(InvestigationAccount).where(
            InvestigationAccount.investigation_id == inv.id)).rowcount
        out["runs"] = s.execute(delete(CollectionRun).where(CollectionRun.investigation_id == inv.id)).rowcount
        out["edges"] = s.execute(delete(Edge).where((Edge.investigation_id == inv.id)
                                                    | Edge.item_id.in_(orphan_ids or [-1]))).rowcount
        out["labels"] = s.execute(delete(ItemLabel).where(ItemLabel.item_id.in_(orphan_ids or [-1]))).rowcount
        out["posts"] = s.execute(delete(Item).where(Item.id.in_(orphan_ids or [-1]))).rowcount
        out["coordination_groups"] = s.execute(delete(CoordinationGroup).where(
            CoordinationGroup.investigation_id == inv.id)).rowcount
        out["narratives"] = s.execute(delete(Narrative).where(Narrative.investigation_id == inv.id)).rowcount
        alerts = [a for a in s.scalars(select(Alert).where(Alert.module.in_(["coordination", "narratives", "market"])))
                  .all() if (a.evidence or {}).get("investigation_id") == inv.id]
        for a in alerts:
            s.delete(a)
        out["alerts"] = len(alerts)
        # accounts: drop those no other investigation links and no remaining post was written by
        still_authors = {account_key(p, a) for p, a in s.execute(
            select(Item.source_type, func.coalesce(Item.author_id, Item.author_handle))).all() if a}
        gone = 0
        for acc in s.scalars(select(Account).where(Account.id.in_((my_accs - other_accs) or {-1}))).all():
            if account_key(acc.platform, acc.platform_id) not in still_authors:
                s.delete(acc)
                gone += 1
        out["accounts"] = gone
        s.delete(s.get(Investigation, inv.id))
        audit(s, who or actor(), "investigation_delete", f"id={inv.id} name={inv.name} removed={out}")
    return out
