"""Upsert FetchResult records into the DB (idempotent; re-running a source never duplicates rows)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from socmint.core.models import FactCheck, Indicator, Item, LookalikeDomain, MarketDaily, Symbol, utcnow
from socmint.core.schemas import DomainIn, FactCheckIn, FetchResult, IndicatorIn, ItemIn, MarketIn

_CHUNK = 400


def _chunks(seq: list[Any], n: int = _CHUNK) -> Iterable[list[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def store_items(s: Session, items: list[ItemIn]) -> int:
    new = 0
    uniq = {(i.source, i.external_id): i for i in items}
    keys = list(uniq)
    for chunk in _chunks(keys):
        existing = set(s.execute(select(Item.source, Item.external_id).where(
            tuple_(Item.source, Item.external_id).in_(chunk))).all())
        for k in chunk:
            if k in existing:
                continue
            d = uniq[k].model_dump()
            s.add(Item(**d))
            new += 1
    return new


def store_factchecks(s: Session, fcs: list[FactCheckIn]) -> int:
    new = 0
    uniq = {f.url: f for f in fcs}
    for chunk in _chunks(list(uniq)):
        existing = set(s.scalars(select(FactCheck.url).where(FactCheck.url.in_(chunk))).all())
        for url in chunk:
            if url not in existing:
                s.add(FactCheck(**uniq[url].model_dump()))
                new += 1
    return new


def store_indicators(s: Session, inds: list[IndicatorIn]) -> int:
    """Insert new indicators; for existing ones bump last_seen/seen_count and merge sources.

    Status only escalates (suspected -> reported); analyst-set confirmed/benign is never overwritten.
    """
    new = 0
    now = utcnow()
    uniq: dict[tuple[str, str], IndicatorIn] = {}
    for i in inds:
        uniq.setdefault((i.type, i.value), i)
    rank = {"benign": 3, "confirmed": 3, "reported": 2, "suspected": 1}
    for chunk in _chunks(list(uniq)):
        rows = {(r.type, r.value): r for r in s.scalars(select(Indicator).where(
            tuple_(Indicator.type, Indicator.value).in_(chunk))).all()}
        for k in chunk:
            src = uniq[k]
            row = rows.get(k)
            if row is None:
                s.add(Indicator(type=src.type, value=src.value, status=src.status, sources=[src.source] if src.source else [],
                                linked_brand=src.linked_brand, first_seen=now, last_seen=now, synthetic=src.synthetic))
                new += 1
            else:
                row.last_seen = now
                row.seen_count = (row.seen_count or 0) + 1
                if src.source and src.source not in (row.sources or []):
                    row.sources = [*(row.sources or []), src.source]
                if rank.get(src.status, 0) > rank.get(row.status, 0) and row.status not in ("confirmed", "benign"):
                    row.status = src.status
                if src.linked_brand and not row.linked_brand:
                    row.linked_brand = src.linked_brand
    return new


def store_domains(s: Session, doms: list[DomainIn]) -> int:
    new = 0
    uniq = {(d.brand, d.domain): d for d in doms}
    for k, d in uniq.items():
        row = s.scalar(select(LookalikeDomain).where(LookalikeDomain.brand == k[0], LookalikeDomain.domain == k[1]))
        if row is None:
            s.add(LookalikeDomain(brand=d.brand, domain=d.domain, method=d.method, evidence=d.evidence, synthetic=d.synthetic))
            new += 1
    return new


def store_market(s: Session, rows: list[MarketIn]) -> int:
    new = 0
    uniq = {(r.symbol, r.date): r for r in rows}
    for chunk in _chunks(list(uniq)):
        existing = {(m.symbol, m.date): m for m in s.scalars(select(MarketDaily).where(
            tuple_(MarketDaily.symbol, MarketDaily.date).in_(chunk))).all()}
        for k in chunk:
            r = uniq[k]
            row = existing.get(k)
            if row is None:
                s.add(MarketDaily(symbol=r.symbol, date=r.date, close=r.close, volume=r.volume, turnover=r.turnover,
                                  synthetic=r.synthetic))
                new += 1
            else:
                row.close, row.volume, row.turnover = r.close, r.volume, r.turnover
    return new


def store_symbols(s: Session, rows: list[dict[str, Any]]) -> int:
    existing = {sym.symbol: sym for sym in s.scalars(select(Symbol)).all()}
    new = 0
    for r in rows:
        row = existing.get(r["symbol"])
        if row is None:
            s.add(Symbol(**r))
            new += 1
        else:
            for k, v in r.items():
                setattr(row, k, v)
    return new


def store_result(s: Session, res: FetchResult) -> dict[str, int]:
    """Store every list in a FetchResult; returns counts of *new* rows per kind."""
    return {
        "items": store_items(s, res.items),
        "factchecks": store_factchecks(s, res.factchecks),
        "indicators": store_indicators(s, res.indicators),
        "domains": store_domains(s, res.domains),
        "market": store_market(s, res.market),
        "symbols": store_symbols(s, res.symbols) if res.symbols else 0,
    }
