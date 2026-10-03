"""Rule-based scam scoring (rules in config/scam_rules.yaml). Every point is backed by a Reason."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from sqlalchemy import or_, select

from socmint.analyze.impersonation import is_official, lookalike_findings, unofficial_contact_findings
from socmint.core.db import session_scope
from socmint.core.logging import get_logger
from socmint.core.models import Indicator, Item
from socmint.core.schemas import Reason
from socmint.core.settings import get_settings, load_config, threshold
from socmint.understand.entities import Entities
from socmint.understand.normalize import match_text
from socmint.understand.urls import domain_matches, host_of, registered_domain

log = get_logger(__name__)


@dataclass
class _Rule:
    key: str
    label: str
    weight: float
    bonus: float
    max_bonus: float
    latin: list[tuple[str, re.Pattern[str]]]
    deva: list[str]


@dataclass
class ScamResult:
    score: float
    reasons: list[Reason] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    impersonation: list[dict[str, Any]] = field(default_factory=list)
    lookalikes: list[dict[str, Any]] = field(default_factory=list)


def _compile_keywords(kw_by_lang: dict[str, list[str]] | None) -> tuple[list[tuple[str, re.Pattern[str]]], list[str]]:
    latin: list[tuple[str, re.Pattern[str]]] = []
    deva: list[str] = []
    for words in (kw_by_lang or {}).values():
        for w in words or []:
            n = match_text(str(w))
            if not n:
                continue
            if n.isascii():
                latin.append((n, re.compile(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])")))
            else:
                deva.append(n)
    return latin, sorted(set(deva))


@lru_cache(maxsize=1)
def rules() -> tuple[list[_Rule], dict[str, Any]]:
    cfg = load_config("scam_rules")
    out = []
    for key, c in (cfg.get("categories") or {}).items():
        latin, deva = _compile_keywords(c.get("keywords"))
        out.append(_Rule(key, c.get("label", key), float(c.get("weight", 30)), float(c.get("extra_hit_bonus", 0)),
                         float(c.get("max_bonus", 0)), latin, deva))
    return out, cfg


def keyword_hits(text_norm: str, latin: list[tuple[str, re.Pattern[str]]], deva: list[str]) -> list[str]:
    hits = [w for w, p in latin if p.search(text_norm)]
    hits += [d for d in deva if d in text_norm]
    # drop hits that are substrings of longer hits ("kyc" inside "update kyc")
    return [h for h in hits if not any(h != o and h in o for o in hits)]


_KNOWN_HOSTS: dict[str, Any] = {"at": 0.0, "hosts": frozenset()}


def _known_hosts() -> frozenset[str]:
    """Hosts seen in ingested items (cached 10 min) - basis of the 'new domain' signal."""
    if time.monotonic() - _KNOWN_HOSTS["at"] > 600:
        with session_scope() as s:
            urls = s.scalars(select(Item.url).where(Item.url.is_not(None))).all()
        _KNOWN_HOSTS["hosts"] = frozenset(registered_domain(host_of(u)) for u in urls if u)
        _KNOWN_HOSTS["at"] = time.monotonic()
    return _KNOWN_HOSTS["hosts"]


def threat_feed_hits(ent: Entities) -> list[dict[str, Any]]:
    """URLs / hosts present in reported or confirmed indicators."""
    if not ent.urls and not ent.domains:
        return []
    variants: set[str] = set()
    for u in ent.urls:
        base = u.rstrip("/")
        for v in (u, base, base + "/"):
            variants.add(v)
            variants.add(v.replace("http://", "https://", 1))
            variants.add(v.replace("https://", "http://", 1))
    hosts = set(ent.domains) | {registered_domain(h) for h in ent.domains}
    with session_scope() as s:
        rows = s.scalars(select(Indicator).where(
            Indicator.status.in_(["reported", "confirmed"]),
            or_((Indicator.type == "url") & Indicator.value.in_(variants),
                (Indicator.type == "domain") & Indicator.value.in_(hosts)))).all()
        return [{"type": r.type, "value": r.value, "status": r.status, "sources": r.sources} for r in rows]


def safe_browsing_hits(urls: list[str]) -> list[dict[str, Any]]:
    """Optional Google Safe Browsing v4 lookup (only with GOOGLE_SAFE_BROWSING_KEY and online)."""
    from socmint.core.http import get_client, is_offline

    key = get_settings().google_safe_browsing_key
    if not key or not urls or is_offline():
        return []
    import json

    body = {"client": {"clientId": "socmint-india-prototype", "clientVersion": "0.1"},
            "threatInfo": {"threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE"],
                           "platformTypes": ["ANY_PLATFORM"], "threatEntryTypes": ["URL"],
                           "threatEntries": [{"url": u} for u in urls[:20]]}}
    try:
        raw = get_client().get(f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={key}", method="POST",
                               json_body=body, cache_ttl=1800, check_robots=False)
        return [{"url": m["threat"]["url"], "threat": m.get("threatType")} for m in json.loads(raw).get("matches", [])]
    except Exception as e:  # noqa: BLE001
        log.warning("safe_browsing_failed", extra={"error": str(e)})
        return []


def score_text(text: str, ent: Entities) -> ScamResult:
    """Score a message 0-100 with explainable reasons."""
    rule_list, cfg = rules()
    sig = cfg.get("signals", {}) or {}
    t = match_text(text)
    reasons: list[Reason] = []
    res = ScamResult(score=0.0)

    # 1. category keyword rules
    for r in rule_list:
        hits = keyword_hits(t, r.latin, r.deva)
        if hits:
            w = r.weight + min(r.bonus * (len(hits) - 1), r.max_bonus)
            reasons.append(Reason(code=f"category:{r.key}", message=r.label, weight=w,
                                  evidence={"keywords": hits[:8]}))
            res.categories.append(r.key)

    def add_signal(key: str, evidence: dict[str, Any]) -> None:
        s = sig.get(key, {}) or {}
        reasons.append(Reason(code=f"signal:{key}", message=s.get("label", key), weight=float(s.get("weight", 0)),
                              evidence=evidence))
        res.signals.append(key)

    # 2. link signals
    tf = threat_feed_hits(ent)
    if tf:
        add_signal("threat_feed_url", {"matches": tf[:5]})
    sb = safe_browsing_hits(ent.urls)
    if sb:
        add_signal("safe_browsing_hit", {"matches": sb[:5]})
    looks = [f for h in ent.domains for f in lookalike_findings(h)]
    if looks:
        res.lookalikes = looks
        add_signal("lookalike_domain", {"findings": looks[:5]})
    shorteners = {d.lower() for d in cfg.get("shorteners", [])}
    short = [h for h in ent.domains if h in shorteners or registered_domain(h) in shorteners]
    if short:
        add_signal("shortener", {"hosts": short})
    known_cfg = [d.lower() for d in cfg.get("known_domains", [])]
    try:
        known_hosts = _known_hosts()
    except Exception:  # noqa: BLE001
        known_hosts = frozenset()
    new = [h for h in ent.domains if not is_official(h) and not domain_matches(h, known_cfg)
           and registered_domain(h) not in known_hosts and h not in short]
    if new:
        add_signal("new_domain", {"hosts": new})
    if any(u.lower().split("?")[0].endswith(".apk") for u in ent.urls) or re.search(r"(?<![a-z])apk(?![a-z])", t):
        add_signal("apk_link", {"urls": [u for u in ent.urls if ".apk" in u.lower()]})

    # 3. language signals
    urg = sig.get("urgency", {}) or {}
    ul, ud = _compile_keywords(urg.get("keywords"))
    urgent = keyword_hits(t, ul, ud)
    if urgent:
        add_signal("urgency", {"keywords": urgent[:6]})
    if ent.upi_ids:
        pl, pd = _compile_keywords((sig.get("upi_payment_request", {}) or {}).get("keywords"))
        pay = keyword_hits(t, pl, pd)
        if pay:
            add_signal("upi_payment_request", {"upi_ids": ent.upi_ids, "keywords": pay[:5]})

    gi = sig.get("group_invite", {}) or {}
    gl, gd = _compile_keywords(gi.get("keywords"))
    invite_hosts = [h for h in ent.domains if domain_matches(h, [x.lower() for x in gi.get("hosts", [])])]
    invite_words = keyword_hits(t, gl, gd)
    if invite_hosts or invite_words:
        add_signal("group_invite", {"hosts": invite_hosts, "keywords": invite_words[:4]})

    sebi = sig.get("sebi_claim_unverified", {}) or {}
    claims = [c for c in (match_text(k) for k in sebi.get("claim_keywords", [])) if c and c in t]
    reg_nos = sorted(set(re.findall(r"\b(IN[AH]\d{9})\b", text.upper())))
    if claims or reg_nos:
        from socmint.ingest.sebi import load_sebi_registry

        registry = load_sebi_registry()
        unknown = [r for r in reg_nos if r not in registry]
        if unknown or (claims and not reg_nos):
            add_signal("sebi_claim_unverified", {"claimed_numbers": reg_nos, "not_in_registry": unknown,
                                                 "claim_words": claims, "registry_rows": len(registry)})

    # 4. brand claims with non-official contact
    imp = unofficial_contact_findings(ent)
    if imp:
        res.impersonation = imp
        add_signal("brand_unofficial_contact", {"findings": imp[:6]})

    # 5. context signals that only count alongside a scam category
    if res.categories:
        nonofficial_links = [u for u in ent.urls if not is_official(host_of(u))]
        if nonofficial_links:
            add_signal("link_request", {"urls": nonofficial_links[:3]})
        mobiles = [p for p in ent.phones if p.startswith("+91")]
        cm = sig.get("contact_mobile", {}) or {}
        cl, cd = _compile_keywords(cm.get("keywords"))
        if mobiles and keyword_hits(t, cl, cd):
            add_signal("contact_mobile", {"numbers": mobiles[:3]})

    res.reasons = sorted(reasons, key=lambda r: -r.weight)
    res.score = float(min(sum(r.weight for r in reasons), float(threshold("scam.max_score", 100))))
    return res


def band(score: float) -> str:
    """likely / suspicious / low according to thresholds.yaml."""
    if score >= float(threshold("scam.likely", 70)):
        return "likely"
    if score >= float(threshold("scam.suspicious", 40)):
        return "suspicious"
    return "low"
