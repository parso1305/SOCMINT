"""URL / domain helpers built on tldextract (offline: bundled public-suffix snapshot, no network)."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

import tldextract

# RFC 2606 / 6761 reserved TLDs used by synthetic fixtures; tldextract does not know them.
_RESERVED_TLDS = {"example", "test", "invalid", "localhost"}

_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


@lru_cache(maxsize=4096)
def split_domain(host: str) -> tuple[str, str, str]:
    """(subdomain, domain, suffix). Handles reserved test TLDs such as `.example`."""
    host = (host or "").lower().strip(".")
    ext = _extract(host)
    if not ext.suffix:
        parts = host.split(".")
        if len(parts) >= 2 and parts[-1] in _RESERVED_TLDS:
            return ".".join(parts[:-2]), parts[-2], parts[-1]
        return "", "", ""
    return ext.subdomain, ext.domain, ext.suffix


def registered_domain(host: str) -> str:
    """eTLD+1, e.g. 'login.sbi.co.in' -> 'sbi.co.in'. Empty string if not a valid domain."""
    _, dom, suf = split_domain(host)
    return f"{dom}.{suf}" if dom and suf else ""


def is_valid_domain(host: str) -> bool:
    return bool(registered_domain(host))


def host_of(url: str) -> str:
    """Lower-cased host of a URL (adds a scheme if missing). Empty string if unparseable."""
    u = url.strip()
    if not u.lower().startswith(("http://", "https://")):
        u = "http://" + u
    try:
        host = urlparse(u).hostname or ""
    except ValueError:
        return ""
    return host.lower().strip(".")


def normalize_url(url: str) -> str:
    """Canonical-ish form for matching: lowercase scheme/host, strip trailing punctuation, add scheme."""
    u = url.strip().rstrip(".,;:!?)]}'\"")
    if u.lower().startswith("www."):
        u = "http://" + u
    p = urlparse(u if "://" in u else "http://" + u)
    host = (p.hostname or "").lower()
    port = f":{p.port}" if p.port else ""
    path = p.path or ""
    q = f"?{p.query}" if p.query else ""
    return f"{p.scheme.lower() or 'http'}://{host}{port}{path}{q}"


def domain_matches(host: str, official: list[str]) -> bool:
    """True if host equals or is a subdomain of any official domain."""
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in official)
