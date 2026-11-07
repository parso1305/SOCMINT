"""M1: ingestion (offline; HTTP is faked)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime

import pytest
from sqlalchemy import func, select

from socmint.core.db import session_scope
from socmint.core.http import HttpError, RobotsDisallowed
from socmint.core.models import FactCheck, Indicator, Item, MarketDaily, SourceRow

RSS = b"""<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>FC</title>
<item><title>Fact Check: Viral video of flood is old</title><link>https://fc.example/a</link>
<description>&lt;p&gt;The video is from 2019, not 2026. A long body follows.&lt;/p&gt;</description>
<pubDate>Thu, 01 Oct 2026 06:30:05 +0000</pubDate></item>
<item><title>Second</title><link>https://fc.example/b</link><description>short</description></item>
</channel></rss>"""


class FakeClient:
    """Stands in for PoliteClient: maps URL substrings to bodies or exceptions."""

    def __init__(self, routes):
        self.routes = routes

    def set_rate_limit(self, host, seconds):
        pass

    def forget(self, url, **kw):
        pass

    def get(self, url, **kw):
        for key, val in self.routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise HttpError(f"HTTP 404 for {url}", 404)

    def get_json(self, url, **kw):
        return json.loads(self.get(url, **kw))


def test_rss_factcheck_parsing_stores_only_metadata():
    from socmint.ingest.rss import RSSSource

    src = RSSSource({"name": "t", "type": "rss", "kind": "factcheck", "publisher": "FC", "lang": "en",
                     "url": "https://fc.example/feed"}, client=FakeClient({"feed": RSS}))
    res = src.fetch(None)
    assert len(res.factchecks) == 2
    fc = res.factchecks[0]
    assert fc.publisher == "FC" and fc.url == "https://fc.example/a" and fc.lang == "en"
    assert "<p>" not in fc.claim_text and len(fc.claim_text) <= 400
    assert fc.published_at == datetime(2026, 10, 1, 6, 30, 5)


def test_rss_news_becomes_items():
    from socmint.ingest.rss import RSSSource

    res = RSSSource({"name": "n", "type": "rss", "kind": "news", "url": "https://x.example/feed"},
                    client=FakeClient({"feed": RSS})).fetch(None)
    assert len(res.items) == 2 and res.items[0].source_type == "news"


def test_summary_truncation():
    from socmint.ingest.base import clean_summary

    out = clean_summary("<b>word</b> " * 500, 50)
    assert len(out) <= 50 and out.endswith("…") and "<b>" not in out


def test_gdelt_parse_and_plaintext_error():
    from socmint.ingest.gdelt import GdeltSource

    body = json.dumps({"articles": [{"url": "https://n.example/1", "title": "T", "seendate": "20261002T081500Z",
                                     "domain": "n.example", "language": "Hindi", "sourcecountry": "India"}]}).encode()
    res = GdeltSource({"name": "g", "type": "gdelt", "queries": ["q"]}, client=FakeClient({"gdelt": body})).fetch(None)
    assert res.items[0].lang == "hi" and res.items[0].published_at == datetime(2026, 10, 2, 8, 15)
    with pytest.raises(RuntimeError):
        GdeltSource({"name": "g", "type": "gdelt", "queries": ["q"]},
                    client=FakeClient({"gdelt": b"Please limit requests"})).fetch(None)


def test_openphish_shared_hosts_not_tainted():
    from socmint.ingest.threatfeeds import url_indicators

    inds = url_indicators(["https://evil-kyc.example.in/login", "https://someone.github.io/phish"], "openphish")
    pairs = {(i.type, i.value) for i in inds}
    assert ("domain", "evil-kyc.example.in") in pairs
    assert not any(t == "domain" and "github.io" in v for t, v in pairs)
    assert all(i.status == "reported" for i in inds)


def test_nse_bhavcopy_parse_and_holiday_skip():
    from socmint.ingest.nse import NSEBhavcopySource

    csv_text = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,ClsPric,TtlTradgVol,TtlTrfVal\n"
                "2026-10-01,2026-10-01,CM,NSE,STK,1,IN1,DEMOX,EQ,10.5,1000,10500\n"
                "2026-10-01,2026-10-01,CM,NSE,STK,2,IN2,GOLDB,GB,1,1,1\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("b.csv", csv_text)
    src = NSEBhavcopySource({"name": "b", "type": "nse_bhavcopy", "url_template": "https://n.example/{yyyymmdd}.zip"},
                            client=FakeClient({"20261001": buf.getvalue()}))
    rows = src._one_day(datetime(2026, 10, 1).date(), {"EQ"})
    assert [r.symbol for r in rows] == ["DEMOX"] and rows[0].turnover == 10500
    assert src._one_day(datetime(2026, 10, 2).date(), {"EQ"}) == []  # 404 -> holiday


def test_runner_isolates_failures(monkeypatch):
    """A crashing source, a robots-blocked source and a missing-key source must not stop a good one."""
    from socmint.ingest import runner
    from socmint.ingest.rss import RSSSource

    cfgs = [
        {"name": "good", "type": "rss", "kind": "factcheck", "url": "https://fc.example/feed"},
        {"name": "boom", "type": "rss", "kind": "news", "url": "https://bad.example/feed"},
        {"name": "robots", "type": "rss", "kind": "news", "url": "https://robots.example/feed"},
        {"name": "keyed", "type": "youtube", "keywords": ["x"]},
        {"name": "off", "type": "rss", "enabled": False, "disabled_reason": "test"},
    ]
    fake = FakeClient({"fc.example": RSS, "bad.example": ValueError("kaboom"),
                       "robots.example": RobotsDisallowed("no")})
    monkeypatch.setattr(runner, "source_configs", lambda: cfgs)
    monkeypatch.setattr("socmint.ingest.registry.source_configs", lambda: cfgs)
    monkeypatch.setattr("socmint.ingest.base.get_client", lambda: fake)
    monkeypatch.setattr(RSSSource, "__init__", lambda self, cfg, client=None: (
        setattr(self, "cfg", cfg), setattr(self, "name", cfg["name"]), setattr(self, "client", fake))[0])

    out = {r["source"]: r["status"] for r in runner.run_sources("all")}
    assert out == {"good": "ok", "boom": "error", "robots": "error", "keyed": "no_key", "off": "disabled"}
    with session_scope() as s:
        assert s.scalar(select(func.count(FactCheck.id))) == 2
        assert s.scalar(select(SourceRow.last_error).where(SourceRow.name == "boom")).startswith("ValueError")
    # re-running is idempotent
    runner.run_sources("good")
    with session_scope() as s:
        assert s.scalar(select(func.count(FactCheck.id))) == 2


def test_store_idempotent_and_indicator_status_escalation():
    from socmint.core.schemas import FetchResult, IndicatorIn, ItemIn, MarketIn
    from socmint.ingest.store import store_result

    res = FetchResult(
        items=[ItemIn(source="s", external_id="1", text="a"), ItemIn(source="s", external_id="1", text="a")],
        indicators=[IndicatorIn(type="url", value="http://x.example/", status="suspected", source="tipline")],
        market=[MarketIn(symbol="DEMOX", date=datetime(2026, 10, 1), close=1, volume=2)],
    )
    with session_scope() as s:
        store_result(s, res)
    with session_scope() as s:
        store_result(s, FetchResult(indicators=[IndicatorIn(type="url", value="http://x.example/", status="reported",
                                                            source="openphish")]))
    with session_scope() as s:
        assert s.scalar(select(func.count(Item.id))) == 1
        assert s.scalar(select(func.count(MarketDaily.id))) == 1
        ind = s.scalar(select(Indicator))
        assert ind.status == "reported" and ind.seen_count == 2 and set(ind.sources) == {"tipline", "openphish"}


def test_offline_mode_blocks_network():
    from socmint.core.http import OfflineError, PoliteClient

    with pytest.raises(OfflineError):
        PoliteClient().get("https://example.com/")


def test_sources_yaml_is_valid():
    """Every configured source has a known type; disabled ones explain why."""
    from socmint.core.settings import load_config
    from socmint.ingest.base import source_class

    for cfg in load_config("sources")["sources"]:
        assert source_class(cfg["type"]) is not None, cfg["name"]
        if cfg.get("enabled") is False:
            assert cfg.get("disabled_reason"), cfg["name"]
