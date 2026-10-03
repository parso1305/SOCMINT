# Sources

Every source, how it is accessed, its terms notes, and its live status. URLs were verified with live
requests on **2026-10-03** from a residential connection in India, using our descriptive User-Agent.
Status column: **live** = verified working; **disabled** = `enabled: false` in `config/sources.yaml`;
**needs key** = auto-skipped until the key is in `.env`; **manual** = maintained by hand.

All network access goes through `socmint/core/http.py`: robots.txt is checked, per-host rate limits
apply (robots.txt fetches count too), retries use exponential backoff (longer on HTTP 429), responses are cached on disk
(except images, which are hashed in memory and never written), and the User-Agent carries
`CONTACT_EMAIL` from `.env`.

## Fact-checkers (RSS, no key)
Only title, URL, publisher, date, language and the short RSS summary are stored. Article images are
reduced to perceptual hashes in memory; image files are never stored.

| Name | URL | Lang | Status | Notes |
|---|---|---|---|---|
| altnews_en | https://www.altnews.in/feed/ | en | live | 10 items/feed |
| altnews_hi | https://www.altnews.in/hindi/feed/ | hi | live | |
| factly_en | https://factly.in/feed/ | en | live | |
| vishvas_hi | https://www.vishvasnews.com/feed/ | hi | live | main feed is Hindi |
| vishvas_en | https://www.vishvasnews.com/english/feed/ | en | live | |
| vishvas_mr | https://www.vishvasnews.com/marathi/feed/ | mr | live | |
| newschecker_en | https://newschecker.in/feed/ | en | live | |
| newschecker_hi | https://newschecker.in/hi/feed/ | hi | live | |
| newschecker_mr | https://newschecker.in/mr/feed/ | mr | live | low frequency |
| boomlive | https://www.boomlive.in/feeds/fact-check | en | **disabled** | HTTP 403 for any non-browser UA (robots.txt allows it; bot protection blocks it). We do not spoof browsers. Ask BOOM for feed access. |
| indiatoday_factcheck | (none) | en | **disabled** | `/rss/fact-check` → 404; the only "Fact Check" section id (1800975) is the NewsMo short-video feed. |

RSS feeds carry about 10 recent items, so the fact-check corpus grows with each scheduled `socmint ingest` run.
The Google Fact Check Tools API (below) adds ClaimReview-backed history once a key is added.

## News (RSS, no key)
| Name | URL | Lang | Status |
|---|---|---|---|
| thehindu_national | https://www.thehindu.com/news/national/feeder/default.rss | en | live |
| indianexpress_india | https://indianexpress.com/section/india/feed/ | en | live |
| ndtv_india | https://feeds.feedburner.com/ndtvnews-india-news | en | live |
| hindustantimes_india | https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml | en | live |
| bbc_hindi | https://feeds.bbci.co.uk/hindi/rss.xml | hi | live |
| amarujala_india | https://www.amarujala.com/rss/india-news.xml | hi | live |
| bbc_marathi | https://feeds.bbci.co.uk/marathi/rss.xml | mr | live |

Checked but not used: Times of India (works, but redundant), PIB RSS (press releases, not news), and
Loksatta / Lokmat (the feed URLs tried redirect to HTML pages).

## GDELT DOC 2.0 API (no key)
`https://api.gdeltproject.org/api/v2/doc/doc?mode=artlist&format=json` with four India keyword queries
(see sources.yaml). Stores article title, URL, domain and language only. GDELT asks for **≤1 request
per 5 s** and penalises bursts per IP. The query format was verified live (HTTP 200), but our first run
hit HTTP 429 and the IP stayed throttled for a while. Spacing is now 10 s and robots.txt
fetches are rate-limited too. **Status: live (endpoint verified); see `socmint ingest` output for current state.**

## Threat feeds
| Name | Access | Status | Terms |
|---|---|---|---|
| openphish | https://raw.githubusercontent.com/openphish/public_feed/refs/heads/main/feed.txt (official community feed, linked from openphish.com/phishing_feeds.html; openphish.com itself timed out) | live | **Non-commercial use only** under OpenPhish ToU. Fine for this prototype; a commercial deployment needs a licence. |
| urlhaus | `https://urlhaus-api.abuse.ch/v2/files/exports/{AUTH_KEY}/recent.csv` | **needs key** (`URLHAUS_AUTH_KEY`) | abuse.ch now states that dataset downloads require a free Auth-Key (fair-use, non-commercial). The legacy keyless `/downloads/` path still responds, but we follow the documented terms. |

Feed entries become `indicators` with status **reported** (a third party reported them). The domain of
a reported URL is added as a domain indicator, except shared hosting and shortener domains
(`shared_hosts` in scam_rules.yaml).

## Certificate Transparency
| Name | Status | Notes |
|---|---|---|
| crtsh | **disabled** | crt.sh `robots.txt` is `User-agent: * / Disallow: /` (checked 2026-10-03). Respected. Code kept in `ingest/ct_logs.py`. Lookalike coverage comes from dnstwist + DNS resolution instead. |

## NSE / SEBI
| Name | URL | Status | Notes |
|---|---|---|---|
| nse_symbols | https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv | live | 2,593 symbols. |
| (large-cap list) | https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv | live | NIFTY 100 members are flagged `large_cap` and get no small-cap weight in market alerts. |
| nse_bhavcopy | `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip` | live | New UDiFF format (`TckrSymb, SctySrs, ClsPric, TtlTradgVol, TtlTrfVal`). First run backfills 45 days (31 trading days, about 105k rows) at 1 request per 3 s; holidays return 404 and are skipped. nseindia.com robots.txt allows these paths. |
| sebi_registered | `config/sebi_registered.csv` | manual | SEBI's RA/IA register (https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognisedFpi=yes&intmId=14 for RA, intmId=13 for IA) is a paginated HTML listing (25 per page, 2,266 RAs) with no bulk download. v0 uses a hand-maintained CSV. Only fictional demo rows ship with the repo. |

## Free-key sources (enabled automatically when the key is in `.env`)
| Name | Key(s) | Notes |
|---|---|---|
| google_factcheck | `GOOGLE_FACTCHECK_API_KEY` | Fact Check Tools `claims:search` by keyword × language (en/hi/mr). Stores claim text, rating, publisher, URL. |
| youtube_keywords | `YOUTUBE_API_KEY` | `search.list` (100 units) + `commentThreads.list` (1 unit). The per-run budget (`max_units_per_run: 1500`) stays far below the 10,000/day quota. **Keywords are proposals awaiting approval.** |
| reddit_india | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | App-only OAuth, `/r/{sub}/new`. Non-commercial use under Reddit Data API terms. **Subreddits are proposals awaiting approval.** |
| telegram_public | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Telethon, read-only, only channels listed in sources.yaml (empty), plus a one-time `socmint telegram-login`. Post signatures (personal names) are not stored; the channel is the author. |
| tipline bot | `TELEGRAM_BOT_TOKEN` | Bot API via python-telegram-bot. |
| Safe Browsing | `GOOGLE_SAFE_BROWSING_KEY` | Optional URL reputation during scam scoring. |

## Excluded in v0
X/Twitter (no free tier), Instagram/Facebook (no lawful free access to others' content), WhatsApp
(later, tipline only, through the Business API). Nothing is scraped from login-walled or CAPTCHA-protected pages.
