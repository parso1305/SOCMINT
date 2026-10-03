"""Market-manipulation RISK alerts (pump-and-dump patterns). Evidence only.

This module NEVER produces buy/sell/hold recommendations, price targets or predictions. Generated text
comes only from the neutral templates below; promoter posts appear solely as quoted evidence.

Per symbol and day:
  mentions   = posts mentioning the symbol (Telegram, Reddit, YouTube, news)
  mention_z  = (mentions - mean) / max(std, min_std) over the previous `baseline_days`
  volume_z   = same on log(1 + volume) from the NSE bhavcopy
  flag       = mention_z >= 3 and volume_z >= 3 and mentions >= min_mentions
  risk score = base + small/illiquid + promotional language + unverified SEBI claim + price jump
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select

from socmint.analyze.scam import _compile_keywords, keyword_hits, rules
from socmint.core.db import session_scope
from socmint.core.logging import get_logger
from socmint.core.models import Alert, Item, MarketDaily, Symbol
from socmint.core.settings import threshold
from socmint.understand.entities import extract_tickers, known_symbols
from socmint.understand.normalize import match_text

log = get_logger(__name__)

DISCLAIMER = ("Risk indicator for analysts only. This is not investment advice and makes no statement about "
              "future prices.")
SUMMARY_TEMPLATE = ("Mentions of {symbol} on {date} were {mentions} (z={mention_z:.1f} vs a {n}-day baseline) while "
                    "traded volume was {volume_ratio:.1f}x its baseline (z={volume_z:.1f}); price change {pct:+.1f}%.")


def _z(value: float, history: list[float], min_std: float) -> float | None:
    if len(history) < int(threshold("market.min_baseline_days", 10)):
        return None
    mu, sd = float(np.mean(history)), float(np.std(history))
    return (value - mu) / max(sd, min_std)


def _day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def count_mentions(since: datetime) -> tuple[dict[tuple[str, datetime], int], dict[tuple[str, datetime], list[int]]]:
    """(symbol, day) -> mention count, and -> item ids."""
    syms = known_symbols()
    counts: Counter = Counter()
    ids: dict[tuple[str, datetime], list[int]] = defaultdict(list)
    with session_scope() as s:
        rows = s.execute(select(Item.id, Item.text, Item.published_at, Item.ingested_at)
                         .where((Item.published_at >= since) | (Item.published_at.is_(None)))).all()
    for r in rows:
        when = r.published_at or r.ingested_at
        for sym in extract_tickers(r.text or "", syms):
            k = (sym, _day(when))
            counts[k] += 1
            ids[k].append(r.id)
    return dict(counts), ids


def _promo_hits(texts: list[str]) -> tuple[float, list[str]]:
    rule_list, cfg = rules()
    inv = next((r for r in rule_list if r.key == "investment"), None)
    hits: Counter = Counter()
    flagged = 0
    for t in texts:
        h = keyword_hits(match_text(t), inv.latin, inv.deva) if inv else []
        if h:
            flagged += 1
            hits.update(h)
    return (flagged / len(texts) if texts else 0.0), [w for w, _ in hits.most_common(8)]


def _sebi_claims(texts: list[str]) -> dict[str, Any]:
    from socmint.ingest.sebi import load_sebi_registry

    sebi_cfg = (rules()[1].get("signals", {}) or {}).get("sebi_claim_unverified", {}) or {}
    claim_l, claim_d = _compile_keywords({"all": sebi_cfg.get("claim_keywords", [])})
    reg = load_sebi_registry()
    claimed: set[str] = set()
    claim_posts = 0
    for t in texts:
        nums = set(re.findall(r"\b(IN[AH]\d{9})\b", t.upper()))
        if nums or keyword_hits(match_text(t), claim_l, claim_d):
            claim_posts += 1
            claimed |= nums
    return {"claim_posts": claim_posts, "claimed_numbers": sorted(claimed),
            "not_in_registry": sorted(n for n in claimed if n not in reg), "registry_rows": len(reg)}


def run(as_of: datetime | None = None) -> dict[str, Any]:
    base_days = int(threshold("market.baseline_days", 30))
    mz_t, vz_t = float(threshold("market.mention_z", 3.0)), float(threshold("market.volume_z", 3.0))
    min_m = int(threshold("market.min_mentions", 5))
    w = threshold("market.weights", {}) or {}
    with session_scope() as s:
        latest = as_of or s.scalar(select(MarketDaily.date).order_by(MarketDaily.date.desc()).limit(1))
    if latest is None:
        return {"note": "no market data (run `socmint ingest --source nse_bhavcopy` or the demo)", "alerts": 0}
    latest = _day(latest)
    since = latest - timedelta(days=base_days + 7)
    counts, ids = count_mentions(since)
    summary = {"as_of": latest.date().isoformat(), "symbols_with_mentions": len({k[0] for k in counts}), "flagged": 0,
               "alerts": 0}

    with session_scope() as s:
        # store mention counts on existing market rows
        for (sym, day), n in counts.items():
            row = s.scalar(select(MarketDaily).where(MarketDaily.symbol == sym, MarketDaily.date == day))
            if row:
                row.mentions = n
        s.flush()
        candidates = sorted({sym for (sym, day) in counts if day == latest})
        large = {x.symbol: x.large_cap for x in s.scalars(select(Symbol).where(Symbol.symbol.in_(candidates))).all()}
        existing = set(s.scalars(select(Alert.dedup_key).where(Alert.module == "market")).all())
        for sym in candidates:
            hist = s.scalars(select(MarketDaily).where(MarketDaily.symbol == sym, MarketDaily.date <= latest,
                                                       MarketDaily.date >= latest - timedelta(days=base_days + 14))
                             .order_by(MarketDaily.date)).all()
            today = next((h for h in hist if h.date == latest), None)
            if today is None or today.volume is None:
                continue
            prev = [h for h in hist if h.date < latest][-base_days:]
            m_hist = [float(counts.get((sym, h.date), 0)) for h in prev]
            v_hist = [math.log1p(h.volume or 0) for h in prev]
            mentions = counts.get((sym, latest), 0)
            mz = _z(float(mentions), m_hist, float(threshold("market.min_std_mentions", 1.0)))
            vz = _z(math.log1p(today.volume), v_hist, float(threshold("market.min_std_log_volume", 0.05)))
            pct = ((today.close / prev[-1].close) - 1) * 100 if prev and prev[-1].close and today.close else 0.0
            today.mention_z, today.volume_z, today.price_change = mz, vz, round(pct, 2)
            if mz is None or vz is None or mz < mz_t or vz < vz_t or mentions < min_m:
                continue
            summary["flagged"] += 1
            texts = s.scalars(select(Item.text).where(Item.id.in_(ids[(sym, latest)]))).all()
            promo_share, promo_words = _promo_hits(list(texts))
            sebi = _sebi_claims(list(texts))
            avg_turnover = float(np.mean([h.turnover or 0 for h in prev])) if prev else 0.0
            small = (not large.get(sym, False)) and avg_turnover < float(threshold("market.small_cap_turnover_inr", 5e7))
            factors = {"base": float(w.get("base", 50))}
            if small:
                factors["small_or_illiquid"] = float(w.get("small_cap", 15))
            if promo_share >= 0.3:
                factors["promotional_language"] = float(w.get("promo_language", 15))
            if sebi["not_in_registry"] or (sebi["claim_posts"] and not sebi["claimed_numbers"]):
                factors["sebi_claim_unverified"] = float(w.get("sebi_claim_unverified", 15))
            if abs(pct) >= float(threshold("market.price_jump_pct", 10)):
                factors["price_jump"] = float(w.get("price_jump", 5))
            score = min(sum(factors.values()), 100.0)
            vol_ratio = math.expm1(math.log1p(today.volume)) / max(math.expm1(float(np.mean(v_hist))), 1.0)
            key = f"market:{sym}:{latest.date().isoformat()}"
            if key in existing:
                continue
            synthetic = bool(today.synthetic)
            summary_text = SUMMARY_TEMPLATE.format(symbol=sym, date=latest.date().isoformat(), mentions=mentions,
                                                   mention_z=mz, n=len(prev), volume_ratio=vol_ratio, volume_z=vz, pct=pct)
            s.add(Alert(module="market", severity="high" if score >= 80 else "medium", dedup_key=key, synthetic=synthetic,
                        title=f"Manipulation-risk alert: {sym} - unusual mentions together with unusual trading volume",
                        evidence={"summary": summary_text, "disclaimer": DISCLAIMER, "risk_score": score,
                                  "risk_factors": factors, "symbol": sym, "date": latest.date().isoformat(),
                                  "mentions": mentions, "mention_z": round(mz, 2), "volume": today.volume,
                                  "volume_z": round(vz, 2), "price_change_pct": round(pct, 2),
                                  "baseline_days": len(prev), "avg_turnover_inr": round(avg_turnover),
                                  "large_cap": bool(large.get(sym, False)), "promo_share": round(promo_share, 2),
                                  "promo_keywords": promo_words, "sebi_check": sebi,
                                  "mention_item_ids": ids[(sym, latest)][:50],
                                  "quoted_posts": [t[:200] for t in list(texts)[:5]]}))
            existing.add(key)
            summary["alerts"] += 1
    log.info("market_done", extra=summary)
    return summary
