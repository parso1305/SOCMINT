"""Certificate Transparency via crt.sh (brand-lookalike certificates).

As of 2026-10 crt.sh's robots.txt is `Disallow: /` for all agents, so this source is disabled in
sources.yaml. The code is kept (it honours robots.txt and would raise RobotsDisallowed) so it can be
re-enabled if the policy changes or swapped for another CT provider.
"""

from __future__ import annotations

import json
from datetime import datetime

from socmint.core.schemas import DomainIn, FetchResult
from socmint.ingest.base import Source, register
from socmint.ingest.registry import brand_configs
from socmint.understand.urls import domain_matches, registered_domain


@register
class CrtShSource(Source):
    type_name = "crtsh"

    def hosts(self) -> list[str]:
        return ["https://crt.sh/"]

    def fetch(self, since: datetime | None) -> FetchResult:
        res = FetchResult()
        for b in brand_configs():
            official = [d.lower() for d in b.get("official_domains", [])]
            for kw in b.get("lookalike_keywords", []):
                raw = self.client.get("https://crt.sh/", params={"q": f"%{kw}%", "output": "json", "exclude": "expired"},
                                      cache_ttl=86400)
                for row in json.loads(raw or b"[]"):
                    for name in str(row.get("name_value", "")).splitlines():
                        host = name.strip().lstrip("*.").lower()
                        dom = registered_domain(host)
                        if dom and not domain_matches(host, official):
                            res.domains.append(DomainIn(brand=b["key"], domain=dom, method="crt.sh",
                                                        evidence={"cert_id": row.get("id"), "issuer": row.get("issuer_name"),
                                                                  "not_before": row.get("not_before")}))
        return res
