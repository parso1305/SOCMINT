"""Upsert FetchResult records into the DB (idempotent; re-running a source never duplicates rows)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from socmint.core.models import (
    Account,
    Edge,
    FactCheck,
    Indicator,
    InvestigationAccount,
    InvestigationItem,
    Item,
    LookalikeDomain,
    MarketDaily,
    Symbol,
    utcnow,
)
from socmint.core.schemas import (
    AccountIn,
    DomainIn,
    FactCheckIn,
    FetchResult,
    IndicatorIn,
    ItemIn,
    MarketIn,
    account_key,
)

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
        "accounts": len(store_accounts(s, res.accounts)[1]) if res.accounts else 0,
    }


# ---------------------------------------------------------------------------------------------
# Investigations (M9): posts with ids, accounts, edges, links
# ---------------------------------------------------------------------------------------------
_REFRESH = ("engagement", "author_display_name")  # fields refreshed when a known post is seen again


def upsert_items(s: Session, items: list[ItemIn]) -> tuple[dict[tuple[str, str], int], set[int]]:
    """Insert new posts and refresh engagement on known ones. Returns ({(source, external_id): id}, new ids)."""
    uniq = {(i.source, i.external_id): i for i in items}
    ids: dict[tuple[str, str], int] = {}
    new_rows: list[Item] = []
    for chunk in _chunks(list(uniq)):
        rows = {(r.source, r.external_id): r for r in s.scalars(select(Item).where(
            tuple_(Item.source, Item.external_id).in_(chunk))).all()}
        for k in chunk:
            row = rows.get(k)
            if row is None:
                row = Item(**uniq[k].model_dump())
                s.add(row)
                new_rows.append(row)
            else:
                for f in _REFRESH:
                    v = getattr(uniq[k], f)
                    if v:
                        setattr(row, f, v)
            rows[k] = row
        s.flush()
        ids.update({k: rows[k].id for k in chunk})
    return ids, {r.id for r in new_rows}


def store_accounts(s: Session, accounts: list[AccountIn]) -> tuple[dict[str, int], set[int]]:
    """Upsert accounts on (platform, platform_id); refresh public profile fields and last_seen.

    Returns ({account key: id}, ids of new rows). Fields the source did not return (None) are left alone.
    """
    now = utcnow()
    uniq: dict[tuple[str, str], AccountIn] = {}
    for a in accounts:  # later entries (e.g. a full profile after a post's author stub) win field by field
        prev = uniq.get((a.platform, a.platform_id))
        uniq[(a.platform, a.platform_id)] = a if prev is None else prev.model_copy(
            update={k: v for k, v in a.model_dump().items() if v not in (None, {}, "")})
    out: dict[str, int] = {}
    new: set[int] = set()
    for chunk in _chunks(list(uniq)):
        rows = {(r.platform, r.platform_id): r for r in s.scalars(select(Account).where(
            tuple_(Account.platform, Account.platform_id).in_(chunk))).all()}
        created: set[tuple[str, str]] = set()
        for k in chunk:
            a = uniq[k]
            row = rows.get(k)
            if row is None:
                row = Account(platform=a.platform, platform_id=a.platform_id, first_seen=now, synthetic=a.synthetic)
                s.add(row)
                rows[k] = row
                created.add(k)
            for f in ("handle", "display_name", "url", "created_at", "followers", "following", "posts_count",
                      "description"):
                v = getattr(a, f)
                if v is not None:
                    setattr(row, f, v)
            if a.meta:
                row.meta = {**(row.meta or {}), **a.meta}
            row.last_seen = now
        s.flush()
        for k in chunk:
            out[account_key(*k)] = rows[k].id
            if k in created:
                new.add(rows[k].id)
    return out, new


def item_account_key(it: Item | ItemIn) -> str | None:
    """Edge key of a post's author: platform = source_type (bluesky, mastodon, telegram, youtube, reddit)."""
    ident = it.author_id or it.author_handle
    return account_key(it.source_type, ident) if ident and it.source_type else None


def edges_for_item(it: Item) -> list[tuple[str, str, str]]:
    """(kind, src, dst) edges a post implies: forward, reply, mention, shares_url, uses_hashtag."""
    src = item_account_key(it)
    if not src:
        return []
    meta = it.meta or {}
    out: list[tuple[str, str, str]] = []
    if it.forwarded_from:
        out.append(("forward", src, account_key(it.source_type, it.forwarded_from)))
    if meta.get("reply_to_author_id"):
        out.append(("reply", src, account_key(it.source_type, meta["reply_to_author_id"])))
    mention_ids = meta.get("mention_ids") or []
    for i, m in enumerate(it.mentions or []):
        ident = mention_ids[i] if i < len(mention_ids) and mention_ids[i] else str(m).lstrip("@")
        out.append(("mention", src, account_key(it.source_type, ident)))
    for u in it.urls or []:
        out.append(("shares_url", src, u))
    for h in it.hashtags or []:
        out.append(("uses_hashtag", src, "#" + str(h).lstrip("#").lower()))
    return list(dict.fromkeys(out))


def store_edges(s: Session, item_ids: Iterable[int], investigation_id: int | None = None) -> int:
    """Derive and insert edges for the given posts (idempotent on kind/src/dst/item)."""
    new = 0
    for chunk in _chunks(list(item_ids)):
        items = s.scalars(select(Item).where(Item.id.in_(chunk))).all()
        have = set(s.execute(select(Edge.kind, Edge.src, Edge.dst, Edge.item_id).where(Edge.item_id.in_(chunk))).all())
        for it in items:
            for kind, src, dst in edges_for_item(it):
                if (kind, src, dst, it.id) in have:
                    continue
                s.add(Edge(kind=kind, src=src, dst=dst, item_id=it.id, investigation_id=investigation_id,
                           created_at=it.published_at or utcnow(), synthetic=it.synthetic))
                have.add((kind, src, dst, it.id))
                new += 1
    return new


def link_items(s: Session, investigation_id: int, matched: dict[int, dict[str, Any]]) -> int:
    """Link posts to an investigation; `matched` maps item id -> matched_by (merged when already linked)."""
    new = 0
    for chunk in _chunks(list(matched)):
        rows = {r.item_id: r for r in s.scalars(select(InvestigationItem).where(
            InvestigationItem.investigation_id == investigation_id, InvestigationItem.item_id.in_(chunk))).all()}
        for iid in chunk:
            mb = matched[iid]
            row = rows.get(iid)
            if row is None:
                s.add(InvestigationItem(investigation_id=investigation_id, item_id=iid, matched_by=mb))
                new += 1
            else:
                merged = dict(row.matched_by or {})
                for k, v in mb.items():
                    if isinstance(v, list):
                        merged[k] = list(dict.fromkeys([*(merged.get(k) or []), *v]))
                    else:
                        merged.setdefault(k, v)
                row.matched_by = merged
    return new


_ROLE_RANK = {"subject": 3, "member": 2, "top_author": 1}


def link_accounts(s: Session, investigation_id: int, links: list[tuple[int, str, str]]) -> int:
    """Link accounts (account_id, role, added_by) to an investigation. A stronger role is never downgraded."""
    new = 0
    existing = {r.account_id: r for r in s.scalars(select(InvestigationAccount).where(
        InvestigationAccount.investigation_id == investigation_id)).all()}
    for acc_id, role, added_by in links:
        row = existing.get(acc_id)
        if row is None:
            row = InvestigationAccount(investigation_id=investigation_id, account_id=acc_id, role=role, added_by=added_by)
            s.add(row)
            existing[acc_id] = row
            new += 1
        elif _ROLE_RANK.get(role, 0) > _ROLE_RANK.get(row.role, 0):
            row.role, row.added_by = role, added_by
    return new
