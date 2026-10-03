"""Public threat feeds (OpenPhish community feed; URLhaus with a free Auth-Key).

Entries become indicators with status `reported` (a third party reported them; we did not confirm them).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from socmint.core.schemas import FetchResult, IndicatorIn
from socmint.core.settings import get_settings, load_config
from socmint.ingest.base import Source, register
from socmint.understand.urls import host_of, registered_domain


def shared_hosts() -> set[str]:
    """Hosting/shortener domains where one bad URL must not taint the whole domain."""
    return {d.lower() for d in load_config("scam_rules").get("shared_hosts", [])}


def url_indicators(urls: list[str], source: str) -> list[IndicatorIn]:
    """Make url (+ domain, unless it is a shared host) indicators from reported URLs."""
    shared = shared_hosts()
    out: list[IndicatorIn] = []
    seen_domains: set[str] = set()
    for u in urls:
        u = u.strip()
        if not u or u.startswith("#"):
            continue
        out.append(IndicatorIn(type="url", value=u, status="reported", source=source))
        host = host_of(u)
        dom = registered_domain(host) if host else ""
        if host and dom and dom not in shared and host not in shared and dom not in seen_domains:
            seen_domains.add(dom)
            out.append(IndicatorIn(type="domain", value=host, status="reported", source=source))
    return out


@register
class OpenPhishSource(Source):
    """cfg: url (community feed, text list of URLs)."""

    type_name = "openphish"

    def fetch(self, since: datetime | None) -> FetchResult:
        body = self.client.get(self.cfg["url"], cache_ttl=self.cfg.get("cache_ttl_s", 3600)).decode("utf-8", "replace")
        return FetchResult(indicators=url_indicators(body.splitlines(), self.name))


@register
class URLhausSource(Source):
    """cfg: url_template with {key}. Requires URLHAUS_AUTH_KEY (free, abuse.ch account) per current terms."""

    type_name = "urlhaus"

    required_settings = ("urlhaus_auth_key",)

    def fetch(self, since: datetime | None) -> FetchResult:
        url = self.cfg["url_template"].format(key=get_settings().urlhaus_auth_key)
        body = self.client.get(url, cache_ttl=self.cfg.get("cache_ttl_s", 3600)).decode("utf-8", "replace")
        rows = csv.reader(io.StringIO("\n".join(ln for ln in body.splitlines() if not ln.startswith("#"))))
        urls = [r[2] for r in rows if len(r) > 3 and r[3] == "online"]
        return FetchResult(indicators=url_indicators(urls, self.name))
