"""NSE public archive files: equity symbol list, large-cap index list, and daily UDiFF bhavcopy.

Polite headers, caching, and a rate limit; a missing bhavcopy (holiday/weekend -> 404) is skipped.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime, timedelta

from socmint.core.http import HttpError
from socmint.core.logging import get_logger
from socmint.core.models import utcnow
from socmint.core.schemas import FetchResult, MarketIn
from socmint.ingest.base import Source, register

log = get_logger(__name__)


@register
class NSESymbolsSource(Source):
    """cfg: url (EQUITY_L.csv), large_cap_url (index constituents csv)."""

    type_name = "nse_symbols"

    def hosts(self) -> list[str]:
        return [self.cfg["url"]]

    def fetch(self, since: datetime | None) -> FetchResult:
        body = self.client.get(self.cfg["url"], cache_ttl=86400).decode("utf-8", "replace")
        large: set[str] = set()
        if self.cfg.get("large_cap_url"):
            try:
                lc = self.client.get(self.cfg["large_cap_url"], cache_ttl=86400).decode("utf-8", "replace")
                large = {r.get("Symbol", "").strip() for r in csv.DictReader(io.StringIO(lc))}
            except HttpError as e:
                log.warning("nse_large_cap_list_failed", extra={"error": str(e)})
        rows = []
        for r in csv.DictReader(io.StringIO(body)):
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            sym = r.get("SYMBOL")
            if sym:
                rows.append({"symbol": sym, "name": r.get("NAME OF COMPANY", ""), "series": r.get("SERIES", ""),
                             "isin": r.get("ISIN NUMBER", ""), "large_cap": sym in large, "source": self.name})
        if not rows:
            raise ValueError("EQUITY_L.csv parsed to zero symbols (format changed?)")
        return FetchResult(symbols=rows)


@register
class NSEBhavcopySource(Source):
    """cfg: url_template with {yyyymmdd}, backfill_days (first run), series (list)."""

    type_name = "nse_bhavcopy"

    def hosts(self) -> list[str]:
        return [self.cfg["url_template"]]

    def fetch(self, since: datetime | None) -> FetchResult:
        today = utcnow().date()
        start = (since.date() if since else today - timedelta(days=int(self.cfg.get("backfill_days", 45))))
        series = set(self.cfg.get("series", ["EQ", "BE", "BZ", "SM", "ST"]))
        res = FetchResult()
        d = start
        fetched = 0
        while d <= today:
            if d.weekday() < 5:
                rows = self._one_day(d, series)
                if rows:
                    res.market.extend(rows)
                    fetched += 1
            d += timedelta(days=1)
        log.info("nse_bhavcopy_days", extra={"source": self.name, "days_with_data": fetched, "rows": len(res.market)})
        return res

    def _one_day(self, d: date, series: set[str]) -> list[MarketIn]:
        url = self.cfg["url_template"].format(yyyymmdd=d.strftime("%Y%m%d"))
        try:
            blob = self.client.get(url, cache_ttl=30 * 86400)
        except HttpError as e:
            if e.status in (403, 404):
                return []  # holiday, not yet published, or blocked; logged at summary level
            raise
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = z.namelist()[0]
            text = z.read(name).decode("utf-8", "replace")
        out = []
        for r in csv.DictReader(io.StringIO(text)):
            if r.get("SctySrs") not in series:
                continue
            try:
                out.append(MarketIn(symbol=r["TckrSymb"], date=datetime.strptime(r["TradDt"], "%Y-%m-%d"),
                                    close=float(r["ClsPric"]), volume=float(r["TtlTradgVol"]),
                                    turnover=float(r["TtlTrfVal"])))
            except (KeyError, ValueError):
                continue
        return out
