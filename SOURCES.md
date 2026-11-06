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
**Investigations (2026-11-06):** an event makes a single OR query of its terms with `startdatetime` (at most 89 days
back) and **no retries** (`event_retries: 0`), so a 429 fails that source in seconds instead of minutes. On
2026-11-06 GDELT still answered HTTP 429 to this IP; the source failed cleanly and the other sources completed.

`https://api.gdeltproject.org/api/v2/doc/doc?mode=artlist&format=json` with four India keyword queries
(see sources.yaml). Stores article title, URL, domain and language only. GDELT asks for **≤1 request
per 5 s** and penalises bursts per IP. The query format was verified live (HTTP 200), but our first run
hit HTTP 429 and the IP stayed throttled for a while. Spacing is now 10 s and robots.txt
fetches are rate-limited too. **Status: endpoint verified working (HTTP 200 on 2026-10-03), but this IP has
been throttled (HTTP 429) for over 2 hours since that first burst, even for single requests.** The source stays
enabled and fails cleanly; it should recover once GDELT lifts the penalty. If it doesn't, write to the address
in GDELT's 429 message, or switch to their bulk files.

## Bluesky (no key) — verified 2026-11-06
Official AT Protocol AppView and Jetstream endpoints only; no login, no app password.

| Use | Endpoint | Auth | Status |
|---|---|---|---|
| Profile | `https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile` (`getProfiles`) | none | live |
| Account posts | `https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed` (pinned posts and reposts skipped) | none | live |
| Hydrate posts | `https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts` (25 URIs per call) | none | live |
| Keyword search | `https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts` | — | **HTTP 403** without auth (checked 2026-11-06) |
| Keyword search fallback | `wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post` | none | live: about 26–35 posts/s; bounded listen (60 s / 20,000 posts), terms matched locally, matches hydrated with `getPosts` |

- robots.txt: `public.api.bsky.app` allows all; the Jetstream host returns 404 for robots.txt (treated as allowed).
- Rate limit used: 1 request/s per host (`rate_limit_s: 1`).
- `api.bsky.app/xrpc/app.bsky.feed.searchPosts` currently answers without auth, but we **do not** use it: switching
  hosts to get past the 403 on the public endpoint would be working around a block. Using it needs an explicit decision
  (or an authenticated app password for a project account).
- Opt-out: accounts labelled `!no-unauthenticated` are not collected (profile check and per post).
- Limitation: the Jetstream fallback only sees posts made while it listens, so it rarely finds rare terms. On
  2026-11-06 a 60 s listen scanned 2,140 posts and none matched "flood / बाढ़".

## Mastodon (no key) — verified 2026-11-06
Instance REST API, unauthenticated, read-only. Instances are listed in `sources.yaml` (`mastodon.social`).

| Use | Endpoint | Status |
|---|---|---|
| Event hashtags | `GET /api/v1/timelines/tag/{tag}` (paginated by `max_id`, stops at `since`) | live |
| Account lookup | `GET /api/v1/accounts/lookup?acct=user` on the account's home instance | live |
| Account posts | `GET /api/v1/accounts/{id}/statuses?exclude_reblogs=true` | live |

- Rate limit: mastodon.social allows 300 unauthenticated requests per 5 minutes per IP; we use 1 request per 2 s.
- Keyword (full-text) search needs an authenticated account, so events search **hashtags only**: each term becomes
  one tag (`Pune flood` → `#Puneflood`), and the matched terms are recorded per post.
- Opt-out: accounts with `noindex: true` or `indexable: false` are skipped. Many remote accounts set this: in the
  2026-11-06 live test 63 tag-timeline posts were skipped for that reason and 37 were stored.
- The instance's own terms apply; robots.txt is honoured.

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
| (investigations) | as below | YouTube, Reddit, Telegram and the Fact Check API also serve investigations: keyword search (YouTube `search.list`; Reddit search **only within configured subreddits**; Telegram search **only within listed public channels**) and accounts (YouTube `channels.list` + uploads playlist; Reddit `/user/{name}/about` + `/submitted`; Telegram public broadcast channels only). Not tested live (no keys on the build machine); tested against recorded fixtures. Real channels/subreddits are added only with approval. |
| google_factcheck | `GOOGLE_FACTCHECK_API_KEY` | Fact Check Tools `claims:search` by keyword × language (en/hi/mr). Stores claim text, rating, publisher, URL. |
| youtube_keywords | `YOUTUBE_API_KEY` | `search.list` (100 units) + `commentThreads.list` (1 unit). The per-run budget (`max_units_per_run: 1500`) stays far below the 10,000/day quota. **Keywords are proposals awaiting approval.** |
| reddit_india | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | App-only OAuth, `/r/{sub}/new`. Non-commercial use under Reddit Data API terms. **Subreddits are proposals awaiting approval.** |
| telegram_public | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Telethon, read-only, only channels listed in sources.yaml (empty), plus a one-time `socmint telegram-login`. Post signatures (personal names) are not stored; the channel is the author. |
| tipline bot | `TELEGRAM_BOT_TOKEN` | Bot API via python-telegram-bot. |
| Safe Browsing | `GOOGLE_SAFE_BROWSING_KEY` | Optional URL reputation during scam scoring. |

## Excluded
X/Twitter (no free tier), Instagram/Facebook (no lawful free access to others' content), LinkedIn, WhatsApp
(later, tipline only, through the Business API). Nothing is scraped from login-walled or CAPTCHA-protected pages.
