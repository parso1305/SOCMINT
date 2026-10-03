"""Brand impersonation.

1. `lookalike_findings(host)`: is this host a lookalike of an official brand domain?
   (brand keyword inside a non-official host, typo/homoglyph distance, or found by a dnstwist scan)
2. `unofficial_contact_findings(entities)`: text mentions a brand but gives a non-official link/number.
3. `scan_lookalikes()`: batch job - dnstwist permutations of official domains + DNS resolution;
   registered/live lookalikes become `lookalike_domains` rows and alerts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from sqlalchemy import select

from socmint.core.db import session_scope
from socmint.core.logging import get_logger
from socmint.core.models import Alert, LookalikeDomain, utcnow
from socmint.core.settings import load_config
from socmint.ingest.registry import brand_configs
from socmint.understand.entities import Entities, canonical_phone
from socmint.understand.urls import domain_matches, registered_domain, split_domain

log = get_logger(__name__)

_HOMOGLYPHS = [("rn", "m"), ("vv", "w"), ("0", "o"), ("1", "l"), ("3", "e"), ("5", "s"), ("@", "a"), ("$", "s")]


def _deglyph(s: str) -> str:
    s = s.lower()
    for a, b in _HOMOGLYPHS:
        s = s.replace(a, b)
    return s.replace("-", "")


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass
class BrandInfo:
    key: str
    name: str
    official: list[str]
    labels: list[str]          # second-level labels of official domains, e.g. "hdfcbank"
    keywords: list[str]
    numbers: set[str] = field(default_factory=set)
    synthetic: bool = False


@lru_cache(maxsize=1)
def brands() -> list[BrandInfo]:
    out = []
    for b in brand_configs():
        official = [d.lower() for d in b.get("official_domains", [])]
        labels = [split_domain(d)[1] for d in official if split_domain(d)[1]]
        out.append(BrandInfo(key=b["key"], name=b.get("name", b["key"]), official=official, labels=labels,
                             keywords=[k.lower() for k in b.get("lookalike_keywords", [])],
                             numbers={canonical_phone(n) for n in b.get("support_numbers", [])},
                             synthetic=bool(b.get("synthetic"))))
    return out


def brand_by_key(key: str) -> BrandInfo | None:
    return next((b for b in brands() if b.key == key), None)


def is_official(host: str) -> str | None:
    """Brand key if host is (a subdomain of) an official domain."""
    for b in brands():
        if domain_matches(host, b.official):
            return b.key
    return None


@lru_cache(maxsize=1)
def _known_domains() -> tuple[str, ...]:
    return tuple(d.lower() for d in load_config("scam_rules").get("known_domains", []))


def lookalike_findings(host: str, check_db: bool = True) -> list[dict[str, Any]]:
    """Explainable reasons why `host` imitates a brand domain. Empty list = not a lookalike."""
    host = host.lower().strip(".")
    if not host or is_official(host) or domain_matches(host, list(_known_domains())):
        return []
    sub, label, _suffix = split_domain(host)
    findings: list[dict[str, Any]] = []
    flat = host.replace("-", "").replace(".", "")
    for b in brands():
        hit = next((k for k in b.keywords if k.replace("-", "") in flat), None)
        if hit:
            findings.append({"brand": b.key, "reason": "contains_brand_keyword", "keyword": hit, "host": host})
            continue
        for off_label in b.labels:
            if len(off_label) < 5 or not label:
                continue
            d = levenshtein(_deglyph(label), _deglyph(off_label))
            if label != off_label and d <= (1 if len(off_label) <= 6 else 2):
                findings.append({"brand": b.key, "reason": "typo_or_homoglyph", "official_label": off_label,
                                 "distance": d, "host": host})
                break
            if off_label in (sub or ""):
                findings.append({"brand": b.key, "reason": "official_name_in_subdomain", "host": host})
                break
    if check_db and not findings:
        dom = registered_domain(host)
        with session_scope() as s:
            row = s.scalar(select(LookalikeDomain).where(LookalikeDomain.domain.in_([host, dom])))
            if row:
                findings.append({"brand": row.brand, "reason": "found_by_lookalike_scan", "method": row.method,
                                 "registered": row.registered, "host": host})
    return findings


def unofficial_contact_findings(ent: Entities) -> list[dict[str, Any]]:
    """For each brand mentioned: links on non-official domains and phone numbers not on the official list.

    Mobile numbers are always treated as non-official for a brand unless explicitly listed.
    Toll-free/landline numbers are only judged when the brand has a verified number list.
    """
    shorteners = {d.lower() for d in load_config("scam_rules").get("shorteners", [])}
    known = list(_known_domains())
    findings: list[dict[str, Any]] = []
    for key in ent.brands:
        b = brand_by_key(key)
        if b is None:
            continue
        for host in ent.domains:
            if domain_matches(host, b.official):
                continue
            if domain_matches(host, known) and registered_domain(host) not in shorteners:
                continue
            findings.append({"brand": key, "type": "domain", "value": host})
        for ph in ent.phones:
            if ph in b.numbers:
                continue
            if ph.startswith("+91") or b.numbers:
                findings.append({"brand": key, "type": "phone", "value": ph})
        for upi in ent.upi_ids:
            findings.append({"brand": key, "type": "upi", "value": upi})
    return findings


# ---------------------------------------------------------------------------------------------
# Batch scan: dnstwist permutations + DNS resolution
# ---------------------------------------------------------------------------------------------
def _resolve_a(host: str) -> list[str]:
    """A records via the OS resolver (fast on Windows; dnspython's nameserver walk was timing out)."""
    import socket

    try:
        ascii_host = host.encode("idna").decode("ascii")
        return sorted({a[4][0] for a in socket.getaddrinfo(ascii_host, None, socket.AF_INET)})
    except (OSError, UnicodeError):
        return []


def _dnstwist_scan(domain: str, threads: int, fuzzers: list[str] | None, max_perms: int = 400) -> list[dict[str, Any]]:
    """dnstwist generates permutations; we resolve them ourselves (bounded, polite thread pool)."""
    from concurrent.futures import ThreadPoolExecutor

    import dnstwist

    fz = dnstwist.Fuzzer(domain)
    fz.generate()
    perms = [p for p in fz.permutations() if p["fuzzer"] != "*original" and (not fuzzers or p["fuzzer"] in fuzzers)]
    # cheap, high-signal fuzzers first; homoglyph produces thousands of IDN variants, so it is sampled last
    perms.sort(key=lambda p: p["fuzzer"] == "homoglyph")
    perms = perms[:max_perms]
    with ThreadPoolExecutor(max_workers=threads) as pool:
        answers = list(pool.map(lambda p: _resolve_a(p["domain"]), perms))
    return [{**p, "dns_a": a} for p, a in zip(perms, answers, strict=True) if a]


def scan_lookalikes(threads: int | None = None) -> dict[str, Any]:
    """Run dnstwist for brands with `dnstwist: true`. Registered lookalikes -> lookalike_domains + alerts."""
    from socmint.core.http import is_offline

    cfg = load_config("thresholds").get("impersonation", {}) or {}
    threads = threads or int(cfg.get("dnstwist_threads", 8))
    fuzzers = cfg.get("dnstwist_fuzzers")
    max_perms = int(cfg.get("max_permutations_per_domain", 400))
    summary: dict[str, Any] = {"brands": 0, "registered": 0, "new_alerts": 0, "errors": []}
    if is_offline():
        summary["errors"].append("offline: dnstwist scan skipped")
        return summary
    for b in brand_configs():
        if not b.get("dnstwist"):
            continue
        for dom in b.get("dnstwist_domains", b.get("official_domains", [])[:1]):
            summary["brands"] += 1
            try:
                rows = _dnstwist_scan(dom, threads, fuzzers, max_perms)
            except Exception as e:  # noqa: BLE001  (one brand failing never stops the scan)
                summary["errors"].append(f"{dom}: {type(e).__name__}: {e}")
                log.warning("dnstwist_failed", extra={"domain": dom, "error": str(e)})
                continue
            for r in rows:
                if r.get("fuzzer") == "*original":
                    continue
                cand = str(r.get("domain", "")).lower()
                if not cand or is_official(cand):
                    continue
                a = r.get("dns_a") or r.get("dns_aaaa") or []
                mx = r.get("dns_mx") or []
                ns = r.get("dns_ns") or []
                if not (a or mx or ns):
                    continue
                summary["registered"] += 1
                if record_lookalike(b["key"], cand, f"dnstwist:{r.get('fuzzer')}", registered=True,
                                    dns_a=list(a), dns_mx=list(mx), evidence={"fuzzer": r.get("fuzzer"), "dns_ns": list(ns),
                                                                              "official": dom}):
                    summary["new_alerts"] += 1
    return summary


def scan_items(days: int = 7) -> dict[str, Any]:
    """Public items that mention a brand alongside non-official links/numbers -> indicators + alerts."""
    from datetime import timedelta

    from socmint.core.models import Item
    from socmint.core.schemas import IndicatorIn
    from socmint.ingest.store import store_indicators
    from socmint.understand.entities import extract_all

    since = utcnow() - timedelta(days=days)
    out = {"items_scanned": 0, "findings": 0, "alerts": 0}
    with session_scope() as s:
        items = s.scalars(select(Item).where((Item.published_at >= since) | (Item.published_at.is_(None)))).all()
        existing = set(s.scalars(select(Alert.dedup_key).where(Alert.module == "impersonation")).all())
        for it in items:
            out["items_scanned"] += 1
            ent = extract_all(it.text or "")
            if not ent.brands:
                continue
            fs = unofficial_contact_findings(ent)
            fs += [{"brand": f["brand"], "type": "domain", "value": f["host"]} for h in ent.domains
                   for f in lookalike_findings(h, check_db=False)]
            if not fs:
                continue
            out["findings"] += len(fs)
            store_indicators(s, [IndicatorIn(type=f["type"], value=f["value"], status="suspected", source=it.source,
                                             linked_brand=f["brand"], synthetic=it.synthetic) for f in fs])
            for bkey in sorted({f["brand"] for f in fs}):
                key = f"item-imp:{bkey}:{it.source}:{it.external_id}"
                if key in existing:
                    continue
                vals = [{"type": f["type"], "value": f["value"]} for f in fs if f["brand"] == bkey]
                s.add(Alert(module="impersonation", severity="medium", dedup_key=key, synthetic=it.synthetic,
                            title=f"Public post mentions {bkey} with non-official contact details",
                            evidence={"brand": bkey, "non_official": vals, "item_url": it.url, "channel": it.channel,
                                      "published_at": str(it.published_at), "source": it.source}))
                existing.add(key)
                out["alerts"] += 1
    return out


def record_lookalike(brand: str, domain: str, method: str, registered: bool, dns_a: list[str] | None = None,
                     dns_mx: list[str] | None = None, evidence: dict[str, Any] | None = None,
                     synthetic: bool = False) -> bool:
    """Upsert a lookalike domain; create an alert the first time a registered one is seen. Returns True if alerted."""
    now = utcnow()
    with session_scope() as s:
        row = s.scalar(select(LookalikeDomain).where(LookalikeDomain.brand == brand, LookalikeDomain.domain == domain))
        first = row is None or not row.registered
        if row is None:
            row = LookalikeDomain(brand=brand, domain=domain, method=method, synthetic=synthetic, first_seen=now)
            s.add(row)
        row.registered = registered
        row.dns_a = dns_a or []
        row.dns_mx = dns_mx or []
        row.last_checked = now
        row.evidence = {**(row.evidence or {}), **(evidence or {})}
        if registered and first:
            live = "resolves (A record)" if dns_a else "has DNS records"
            # low: registration alone is weak evidence (parked / defensive registrations are common);
            # actual use in a tipline message or public post raises a separate medium alert.
            s.add(Alert(module="impersonation", severity="low",
                        title=f"Registered lookalike of {brand}: {domain} ({live})",
                        dedup_key=f"lookalike:{brand}:{domain}", synthetic=synthetic,
                        evidence={"brand": brand, "domain": domain, "method": method, "dns_a": dns_a or [],
                                  "dns_mx": dns_mx or [], "checked_at": now.isoformat(), **(evidence or {})}))
            return True
    return False

