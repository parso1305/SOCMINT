"""Post categorisation (multi-label), taxonomy in config/categories.yaml, thresholds under `categorize:`.

1. Rules (always, offline): multilingual keyword lists per category/sublabel plus signals the system already
   computes - scam score band, fact-check match, brand impersonation, market promotion language.
2. Optional zero-shot (multilingual NLI) when the [ml] extra is installed and `categorize.zero_shot: true`;
   it degrades gracefully (reported in `degraded`) when unavailable.
3. Analyst labels (`method="analyst"`) override both: confidence 1.0 adds a label, 0.0 removes it in views.
Every stored label carries the evidence that produced it. `hate_communal` is analyst-only and off by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from socmint.core.logging import get_logger
from socmint.core.models import ItemLabel, utcnow
from socmint.core.settings import load_config, threshold
from socmint.understand.normalize import devanagari_word, match_text

log = get_logger(__name__)


@dataclass
class Label:
    label: str
    sublabel: str | None
    confidence: float
    method: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Signals:
    """Per-post signals from existing modules (filled by `compute_signals`)."""

    scam_score: float = 0.0
    scam_band: str = "low"
    scam_categories: list[str] = field(default_factory=list)
    scam_reasons: list[str] = field(default_factory=list)
    factchecks: list[dict[str, Any]] = field(default_factory=list)
    impersonation: list[dict[str, Any]] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    promo_keywords: list[str] = field(default_factory=list)


@dataclass
class _Cat:
    key: str
    label: str
    enabled: bool
    analyst_only: bool
    latin: list[tuple[str, re.Pattern[str]]]
    deva: list[str]
    subs: dict[str, tuple[list[tuple[str, re.Pattern[str]]], list[str]]]
    zero_shot_label: str | None


@lru_cache(maxsize=1)
def taxonomy() -> dict[str, _Cat]:
    from socmint.analyze.scam import _compile_keywords

    out: dict[str, _Cat] = {}
    for key, c in (load_config("categories").get("categories") or {}).items():
        latin, deva = _compile_keywords(c.get("keywords"))
        subs = {s: _compile_keywords((sc or {}).get("keywords")) for s, sc in (c.get("sublabels") or {}).items()}
        out[key] = _Cat(key, c.get("label", key), bool(c.get("enabled", True)), bool(c.get("analyst_only", False)),
                        latin, deva, subs, c.get("zero_shot_label"))
    return out


def enabled_categories(include_analyst_only: bool = False) -> list[str]:
    return [k for k, c in taxonomy().items() if c.enabled and (include_analyst_only or not c.analyst_only)]


def _kw_conf(n_hits: int) -> float:
    base, step, cap = (float(threshold(f"categorize.keyword_{k}", d)) for k, d in
                       (("base", 0.55), ("step", 0.1), ("cap", 0.85)))
    return round(min(base + step * max(n_hits - 1, 0), cap), 2)


def _investigation_term_keys(terms: dict[str, list[str]] | list[str] | None) -> list[str]:
    """Investigation terms normalised the way `TermMatcher` normalises them ("#" dropped)."""
    from socmint.ingest.base import flat_terms

    keys = (match_text(t).lstrip("#").strip() for t in flat_terms(terms or []))
    return [k for k in keys if k]


def _is_investigation_term(keyword: str, term_keys: list[str]) -> bool:
    """A category keyword is one of the investigation's own terms (or a word of a multi-word term)."""
    for k in term_keys:
        if keyword == k:
            return True
        if keyword.isascii() and re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", k):
            return True
        if not keyword.isascii() and devanagari_word(keyword).search(k):  # word-start rule, as in TermMatcher
            return True
    return False


def rule_labels(text: str, sig: Signals | None = None,
                investigation_terms: dict[str, list[str]] | list[str] | None = None) -> list[Label]:
    """Keyword + signal rules. Pure function (no DB); returns at most one label per category.

    `investigation_terms`: the event's own search terms. Every collected post contains them, so a keyword hit
    that is one of them does not count toward a category; it is kept in the evidence as
    `matched_investigation_term` instead.
    """
    from socmint.analyze.scam import keyword_hits

    sig = sig or Signals()
    t = match_text(text or "")
    cats = taxonomy()
    found: dict[str, Label] = {}
    term_keys = _investigation_term_keys(investigation_terms)
    skipped: dict[str, list[str]] = {}

    def own(key: str, hits: list[str]) -> list[str]:
        if not term_keys:
            return hits
        mine = [h for h in hits if _is_investigation_term(h, term_keys)]
        if mine:
            skipped.setdefault(key, []).extend(h for h in mine if h not in skipped.get(key, []))
        return [h for h in hits if h not in mine]

    def put(lab: Label) -> None:
        cur = found.get(lab.label)
        if cur is None or lab.confidence > cur.confidence:
            if cur is not None:  # keep the weaker rule's evidence too
                lab.evidence = {**cur.evidence, **lab.evidence}
            found[lab.label] = lab
        else:
            cur.evidence = {**lab.evidence, **cur.evidence}

    for key, c in cats.items():
        if not c.enabled or key == "other":
            continue
        hits = own(key, keyword_hits(t, c.latin, c.deva))
        sub_hits = {s: own(key, keyword_hits(t, lat, dev)) for s, (lat, dev) in c.subs.items()}
        sub_hits = {s: h for s, h in sub_hits.items() if h}
        n = len(hits) + sum(len(h) for h in sub_hits.values())
        if n:
            best = max(sub_hits, key=lambda s: len(sub_hits[s])) if sub_hits else None
            ev = {"rule": "keywords", "keywords": hits[:8], "sublabel_keywords": {s: h[:6] for s, h in sub_hits.items()}}
            if key in skipped:
                ev["matched_investigation_term"] = skipped[key][:8]
            put(Label(key, best, _kw_conf(n), "rule", ev))

    def enabled(key: str) -> bool:
        return key in cats and cats[key].enabled

    if enabled("scam") and sig.scam_band in ("likely", "suspicious"):
        conf = float(threshold(f"categorize.scam_{sig.scam_band}", 0.9 if sig.scam_band == "likely" else 0.65))
        put(Label("scam", sig.scam_categories[0] if sig.scam_categories else None, conf, "rule",
                  {"rule": "scam_score", "score": sig.scam_score, "band": sig.scam_band,
                   "scam_categories": sig.scam_categories, "reasons": sig.scam_reasons[:8]}))
    if enabled("misinformation") and sig.factchecks:
        top = max(sig.factchecks, key=lambda m: m.get("score", 0))
        put(Label("misinformation", "factcheck_matched", float(threshold("categorize.factcheck_match", 0.9)), "rule",
                  {"rule": "factcheck_match", "factchecks": sig.factchecks[:3], "best_score": top.get("score")}))
    if enabled("brand_impersonation") and sig.impersonation:
        put(Label("brand_impersonation", sorted({f.get("brand", "") for f in sig.impersonation})[0] or None,
                  float(threshold("categorize.impersonation", 0.85)), "rule",
                  {"rule": "impersonation", "findings": sig.impersonation[:6]}))
    if enabled("market_manipulation") and sig.tickers and sig.promo_keywords:
        put(Label("market_manipulation", None, float(threshold("categorize.market_promo", 0.75)), "rule",
                  {"rule": "ticker_with_promo_language", "tickers": sig.tickers[:5], "promo_keywords": sig.promo_keywords[:6]}))

    min_c = float(threshold("categorize.min_confidence", 0.5))
    labels = [lab for lab in found.values() if lab.confidence >= min_c]
    if not labels and enabled("other"):
        ev = {"rule": "fallback", "note": "no category rule fired"}
        if skipped:
            ev["matched_investigation_term"] = {k: v[:8] for k, v in skipped.items()}
        labels = [Label("other", None, 1.0, "rule", ev)]
    return sorted(labels, key=lambda lab: -lab.confidence)


def compute_signals(text: str, claim_matching: bool = True) -> tuple[Signals, Any]:
    """Run the existing extractors/scorers on one post. Returns (signals, entities)."""
    from socmint.analyze.impersonation import lookalike_findings, unofficial_contact_findings
    from socmint.analyze.scam import band, keyword_hits, rules, score_text
    from socmint.understand.entities import extract_all

    ent = extract_all(text or "")
    sres = score_text(text or "", ent)
    imp = list(unofficial_contact_findings(ent)) if ent.brands else []
    for h in ent.domains:
        imp += [{"brand": f["brand"], "type": "lookalike_domain", "value": h, "reason": f["reason"]}
                for f in lookalike_findings(h)]
    rule_list, _cfg = rules()
    inv = next((r for r in rule_list if r.key == "investment"), None)
    promo = keyword_hits(match_text(text or ""), inv.latin, inv.deva) if inv and ent.tickers else []
    fcs: list[dict[str, Any]] = []
    if claim_matching:
        from socmint.analyze.claim_match import match_claims

        fcs = [{"id": m.factcheck_id, "title": m.title, "url": m.url, "publisher": m.publisher,
                "score": round(m.score, 3)} for m in match_claims(text or "", top_k=3)]
    return Signals(scam_score=sres.score, scam_band=band(sres.score), scam_categories=list(sres.categories),
                   scam_reasons=[r.code for r in sres.reasons], factchecks=fcs, impersonation=imp,
                   tickers=list(ent.tickers), promo_keywords=promo), (ent, sres)


# ------------------------------------------------------------------------------------------- zero-shot
class ZeroShotUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _zero_shot_pipeline():  # noqa: ANN202
    try:
        from transformers import pipeline
    except ImportError as e:
        raise ZeroShotUnavailable("transformers not installed (install the [ml] extra)") from e
    try:
        from socmint.core.hf import resolve

        return pipeline("zero-shot-classification", model=resolve(str(threshold("categorize.zero_shot_model"))),
                        device=-1)
    except Exception as e:  # noqa: BLE001  (no network for the first download, disk, ...)
        raise ZeroShotUnavailable(f"zero-shot model unavailable: {type(e).__name__}: {e}"[:300]) from e


def zero_shot_labels(texts: list[str]) -> list[list[Label]]:
    """Multilingual NLI zero-shot labels for enabled, non-analyst-only categories. Raises ZeroShotUnavailable."""
    cats = {c.zero_shot_label: k for k, c in taxonomy().items()
            if c.enabled and not c.analyst_only and c.zero_shot_label}
    if not cats:
        return [[] for _ in texts]
    clf = _zero_shot_pipeline()
    min_s = float(threshold("categorize.zero_shot_min_score", 0.8))
    model = str(threshold("categorize.zero_shot_model"))
    out = []
    for t in texts:
        r = clf(t[:1000], candidate_labels=list(cats), multi_label=True,
                hypothesis_template=str(threshold("categorize.zero_shot_hypothesis", "This text is about {}.")))
        out.append([Label(cats[lab], None, round(float(s), 3), "zero_shot", {"model": model, "hypothesis": lab})
                    for lab, s in zip(r["labels"], r["scores"], strict=True) if s >= min_s])
    return out


# ------------------------------------------------------------------------------------------- storage
def store_labels(s: Session, item_id: int, labels: list[Label], method: str, synthetic: bool = False) -> int:
    """Replace this method's labels on a post (rule / zero_shot). Analyst labels are never touched here."""
    assert method != "analyst"
    s.execute(delete(ItemLabel).where(ItemLabel.item_id == item_id, ItemLabel.method == method))
    for lab in labels:
        s.add(ItemLabel(item_id=item_id, label=lab.label, sublabel=lab.sublabel, confidence=lab.confidence,
                        method=method, evidence=lab.evidence, synthetic=synthetic))
    return len(labels)


def set_analyst_label(s: Session, item_id: int, label: str, present: bool, actor: str = "analyst",
                      sublabel: str | None = None, note: str = "") -> None:
    """Analyst adds (present=True) or removes (present=False) a label; overrides rule/zero-shot in views."""
    from socmint.core.db import audit

    row = s.scalar(select(ItemLabel).where(ItemLabel.item_id == item_id, ItemLabel.label == label,
                                           ItemLabel.method == "analyst"))
    if row is None:
        row = ItemLabel(item_id=item_id, label=label, method="analyst")
        s.add(row)
    row.confidence = 1.0 if present else 0.0
    row.sublabel = sublabel
    row.evidence = {"actor": actor, "action": "added" if present else "removed", "note": note}
    row.created_at = utcnow()
    audit(s, actor, "label_" + ("add" if present else "remove"), f"item={item_id} label={label}")


def effective_labels(rows: list[ItemLabel]) -> list[dict[str, Any]]:
    """Labels as views show them: an analyst decision on a label wins; otherwise the most confident method."""
    by_label: dict[str, list[ItemLabel]] = {}
    for r in rows:
        by_label.setdefault(r.label, []).append(r)
    out = []
    for lab, rs in by_label.items():
        analyst = next((r for r in rs if r.method == "analyst"), None)
        if analyst is not None:
            if analyst.confidence > 0:
                out.append({"label": lab, "sublabel": analyst.sublabel, "confidence": 1.0, "method": "analyst",
                            "evidence": analyst.evidence})
            continue
        best = max(rs, key=lambda r: r.confidence)
        out.append({"label": lab, "sublabel": best.sublabel, "confidence": best.confidence, "method": best.method,
                    "evidence": best.evidence})
    if len(out) > 1:
        out = [o for o in out if o["label"] != "other"]
    return sorted(out, key=lambda o: -o["confidence"])
