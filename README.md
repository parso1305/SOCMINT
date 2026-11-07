# SOCMINT-India v0 (prototype)

An India-focused open-source intelligence prototype built only on free, lawful sources. It:

- **answers citizens through a tipline** ("is this a scam / fake?") on Telegram, a local API or the CLI;
- **detects scams and brand impersonation** (KYC/account-block, digital arrest, investment tips, task jobs,
  lottery, fake customer care, loan apps, utility disconnection and courier scams; lookalike domains);
- **flags stock-promotion (pump-and-dump) manipulation risk** from mention spikes combined with NSE volume;
- **maps misinformation narratives and possible coordinated posting**;
- **runs analyst-directed investigations** of three kinds: an **event** (keywords/hashtags), a **subject** (named
  public accounts) or a **group** (several accounts, e.g. promoted from a possible-coordination group). Each
  investigation collects posts and public profiles from Bluesky, Mastodon, news RSS and GDELT (no keys), plus
  YouTube, Reddit and Telegram when keys are present, then categorises, links and analyses them, and exports an
  evidence bundle.

Every output carries its evidence (rules fired, sources, scores, timestamps). Simple, explainable rules
come first, and each analysis step is a swappable module.

> Citizen replies are only ever one of: *matches an existing fact-check* · *seen before in an older
> context* · *likely scam / suspicious* · *not verified yet (queued)*. The system **never** says something is true.

---

## Quick start (offline demo, no keys, no internet after install)

```bash
git clone <this repo> socmint && cd socmint
pip install uv                      # or: python -m pip install --user uv
uv sync --extra dev                 # core install (TF-IDF fallback, ~1 GB)
uv run socmint demo                 # offline end-to-end run on synthetic data -> data/demo.db
uv run socmint dashboard --demo     # browse every view at http://127.0.0.1:8501
uv run socmint showcase --demo      # data/demo.db + 3 synthetic investigations -> http://127.0.0.1:8502
```

`showcase --demo` is the investor demo: one page with a fictional flood event, a fictional stock-tip channel and a
group promoted from a coordination finding. The first run builds `data/demo.db` (about a minute with the e5 model);
later runs reuse it and start in seconds (`--rebuild` builds it again). Walkthrough: [`docs/DEMO.md`](docs/DEMO.md).

For real multilingual embeddings (cross-lingual claim matching) and voice-note transcription:

```bash
uv sync --extra ml --extra dev      # + CPU torch, sentence-transformers, faster-whisper (~2 GB)
uv run socmint check "test"         # first run downloads multilingual-e5-small (~470 MB); later runs work offline
```

Without `uv`: `python -m venv .venv`, activate it, then `pip install -e ".[dev]"` (add `,ml` for the ML extra).

### Platform notes
| | Windows | macOS | Linux |
|---|---|---|---|
| Python 3.11+ | python.org installer, or `winget install Python.Python.3.12` | `brew install python@3.12` | distro package / pyenv |
| ffmpeg (video keyframes, optional) | `winget install Gyan.FFmpeg` | `brew install ffmpeg` | `apt install ffmpeg` |
| Tesseract OCR (optional) | `winget install UB-Mannheim.TesseractOCR` (admin prompt). Pick Hindi + Marathi in "Additional language data", or copy `hin.traineddata` and `mar.traineddata` from [tessdata](https://github.com/tesseract-ocr/tessdata) into `C:\Program Files\Tesseract-OCR\tessdata` | `brew install tesseract tesseract-lang` | `apt install tesseract-ocr tesseract-ocr-hin tesseract-ocr-mar` |
| Console | Hindi/Marathi output needs a UTF-8 terminal (Windows Terminal is fine; the CLI forces UTF-8) | ✓ | ✓ |

Without Tesseract, OCR is disabled with a warning. Without ffmpeg, video keyframes are disabled.
Without the `[ml]` extra, embeddings fall back to TF-IDF and ASR is disabled. Everything else still runs.

---

## Using it with real sources

```bash
uv run socmint init-db              # creates data/socmint.db and .env (generates PRIVACY_SALT + SOCMINT_API_KEY)
# edit .env: set CONTACT_EMAIL (it goes in the User-Agent sent to every source) and any keys you have
uv run socmint ingest --source all  # fact-check + news RSS, GDELT, OpenPhish, NSE, ... (failing sources are skipped)
uv run socmint analyze --module all # embeddings, media, impersonation (dnstwist), narratives, coordination, market
uv run socmint check "Your SBI account will be blocked today, update KYC at sbi-kyc.in"
uv run socmint check --file suspicious.jpg
uv run socmint api                  # http://127.0.0.1:8000/docs
uv run socmint bot                  # Telegram tipline (needs TELEGRAM_BOT_TOKEN)
uv run socmint dashboard            # analyst dashboard on the main DB
uv run socmint purge                # apply media retention (run daily, e.g. via cron / Task Scheduler)
```

Investigations (live, no keys needed for the default sources):
```bash
uv run socmint investigate create --kind event --name "test" --terms "flood,बाढ़" --purpose "demo"
uv run socmint investigate collect "test"           # Bluesky, Mastodon, news RSS, GDELT (+ YouTube / Fact Check with keys)
uv run socmint investigate edit "test" --add-terms "पूर, धरण" --lang mr --add-source youtube   # audited before/after
uv run socmint investigate analyze "test"           # narratives, possible coordination, market risk (scoped)
uv run socmint investigate watch "test" --every 15m --for 8h --analyze   # repeat; Ctrl+C stops after the current source
uv run socmint investigate show "test"              # summary + suggested terms to widen an event (--json for everything)
uv run socmint investigate export "test" --format csv   # data/exports/... with a SHA-256 manifest
uv run socmint investigate create --kind subject --name "Platform accounts" --purpose "why" --handle bluesky:bsky.app --handle mastodon:@Mastodon@mastodon.social
uv run socmint investigate promote-group 12 --name "Copy network" --purpose "why"
uv run socmint investigate delete "test" --yes      # links + posts/accounts nobody else references
uv run socmint showcase                             # the same, in the browser (main DB)
```
Investigations can also be written as YAML (`--file`, several documents per file). Fields: `kind`, `name`,
`purpose` (required), `description`, `terms` (per language), `places`, `handles` (per platform), `channels`
(Telegram channels / subreddits to search in), `sources`, `window`.

API examples:
```bash
curl -X POST http://127.0.0.1:8000/check -F "text=You won a lottery, pay fee to x@ybl"
curl -X POST http://127.0.0.1:8000/check -F "file=@photo.jpg"
curl -H "X-API-Key: <SOCMINT_API_KEY from .env>" http://127.0.0.1:8000/alerts
```

| Command | Purpose |
|---|---|
| `socmint init-db` | Create the database (and `.env` with generated secrets) |
| `socmint ingest [--source NAME\|all]` | Run sources (see `config/sources.yaml`, `SOURCES.md`) |
| `socmint analyze [--module NAME\|all]` | embeddings · media · impersonation · narratives · coordination · market · hate · (background) |
| `socmint check "text"` / `--file path` | Run the tipline pipeline locally (`--json`, `--lang hi`, `--no-store`) |
| `socmint bot` / `api` / `dashboard` | Interfaces (all bind to localhost) |
| `socmint purge` | Delete raw media older than `RETENTION_DAYS` |
| `socmint demo` | Offline synthetic end-to-end demo |
| `socmint investigate create\|list\|edit\|collect\|watch\|analyze\|show\|export\|promote-group\|delete` | Investigations (event / subject / group) |
| `socmint showcase [--demo] [--rebuild]` | One-page showcase dashboard on 127.0.0.1:8502 (`--demo` opens data/demo.db, building it offline if needed; `--rebuild` forces a build) |
| `socmint telegram-login` | One-time Telethon login for reading public channels |

## Getting the free keys (all optional)

| `.env` key | How to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | In Telegram, message **@BotFather** → `/newbot` → copy the token. |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | https://my.telegram.org → *API development tools* → create an app. Then run `socmint telegram-login` once (phone + code). Add public channel usernames to `telegram_public.channels` in `sources.yaml`. |
| `YOUTUBE_API_KEY` | Google Cloud Console → create a project → enable **YouTube Data API v3** → *Credentials* → API key. The free quota is 10,000 units/day; the source uses ≤1,500 per run. |
| `GOOGLE_FACTCHECK_API_KEY` | Same console → enable **Fact Check Tools API** → API key. |
| `GOOGLE_SAFE_BROWSING_KEY` | Same console → enable **Safe Browsing API** → API key (non-commercial use). |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | https://www.reddit.com/prefs/apps → *create app* → type **script**. Non-commercial use under Reddit's Data API terms. |
| `URLHAUS_AUTH_KEY` | https://auth.abuse.ch → sign in → create an Auth-Key (free, fair use). |
| `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | A **dedicated project account** (never a personal one) → bsky.app *Settings → Privacy and security → App passwords* → create one **without DM access**. Enables logged-in post search instead of a 60 s live listen. |

A source whose key is missing is reported as `no_key` (the detail names the `.env` variable) and skipped. Nothing crashes.
A new event uses the no-key sources plus `youtube` when `YOUTUBE_API_KEY` is set and `google_factcheck` when
`GOOGLE_FACTCHECK_API_KEY` is set; `investigate create` lists each source and why any is skipped. Reddit and Telegram
only join an event that lists subreddits / public channels under `channels:` (and names the source).

Every collection reports one of seven statuses, the same in the CLI, the showcase chips, the collection log and
`collection_runs.status`: `ok`, `partial` (some but not all, e.g. a quota ran out or some subject accounts opted out),
`throttled` (the source is limiting this IP; GDELT then cools down for 15 min and sends nothing), `no_key`, `offline`,
`opted_out` (every requested account opted out) and `error` (anything else, including robots.txt disallows).

## Configuration
- `config/sources.yaml`: every source (URL, enabled, rate limit, notes). Verified URLs are listed in `SOURCES.md`.
- `config/brands.yaml`: monitored brands: official domains, verified support numbers, aliases (en/hi/mr).
- `config/scam_rules.yaml`: scam categories, multilingual keywords, signal weights, shorteners. Edit without code changes.
- `config/thresholds.yaml`: every threshold and weight (scam bands, claim-match cut-offs, clustering, CCS, market,
  categoriser confidences, investigation windows and limits).
- `config/categories.yaml`: post categories (misinformation, scam, civic safety, election/political, health, market
  manipulation, brand impersonation, hate/communal (off), other) with en/hi/mr/Hinglish keywords and signal rules.
- `config/sebi_registered.csv`: hand-maintained SEBI RA/IA registrations (SEBI publishes no bulk file).

## Architecture
```
ingest/  Source.fetch(since) -> FetchResult   (one failing source never stops the run)
   |  rss · gdelt · threatfeeds · nse · sebi · factcheck_api · youtube · reddit · telegram_channels · ct_logs
   |  + search_terms(terms, since, limit) / account_posts(handle, ...) for investigations; bluesky · mastodon
   v
investigate/  create -> collect (per-source isolation, collection_runs) -> enrich (lang, entities, categories,
              indicators) -> scoped analysis -> summary / export / promote-group / delete (all audited)
   v
SQLite (SQLAlchemy 2.x, Postgres-ready)  items · factchecks · submissions · indicators · narratives · alerts · ...
   ^                                   ^
tipline/pipeline.check()          analyze/runner (batch, per-module isolation)
  OCR/ASR/keyframes -> entities     narratives (HDBSCAN + c-TF-IDF) · coordination (MinHash, CCS v0)
  -> redact -> lang -> [claims |     market (mention/volume z) · impersonation (dnstwist) · hate (stub)
  media | scam | brand] -> verdict
   |
   +-- CLI · FastAPI · Telegram bot          dashboard/ (Streamlit, evidence on every view) · showcase/ (one page)
```
Pluggable points: `Embedder` (e5 / TF-IDF), `DeepfakeDetector` (returns None in v0), `HateClassifier`
(disabled), `Source` subclasses, and all scoring rules in YAML.

## Legal & privacy notes
- **Sources**: only official APIs within free tiers, public RSS, public datasets and public regulatory files.
  robots.txt is honoured (crt.sh is disabled because it disallows all crawlers), per-host rate limits and caching
  apply, and the User-Agent names the project and `CONTACT_EMAIL`. No scraping of X, Instagram, Facebook or WhatsApp.
  No login walls, no CAPTCHA bypass, no browser spoofing (BOOM stays disabled for that reason). Some feeds are
  **non-commercial only** (OpenPhish, URLhaus, Reddit API); a commercial deployment needs licences.
- **Copyright**: fact-checks and news store only title, URL, publisher, date, language and the short RSS/ClaimReview
  summary. Article images are reduced to perceptual hashes in memory and never saved.
- **Tipline privacy**: no phone number, username or name is stored. The sender is `HMAC(PRIVACY_SALT, chat_id)`.
  Stored text has phone numbers, e-mails, Aadhaar-like and PAN-like numbers, and UPI IDs redacted. Suspected
  scam artifacts go to `indicators` with no link to the sender (a temporary review link is wiped on review or
  retention; see DECISIONS.md). Raw media is deleted after `RETENTION_DAYS` (default 7). `/delete` erases all
  of a sender's submissions.
- **Analyst-directed monitoring of public accounts** (changed from v0's "aggregate analysis only"; see DECISIONS.md).
  An analyst may now open a *subject* or *group* investigation on named public accounts, under these guardrails:
  - a subject is created only by an explicit analyst action and needs a **required `purpose`**, stored with it;
  - only what each platform's **official API returns publicly** is collected: public posts and public profile
    fields. Never private/protected content, contact lists, followers' identities, DMs or anything only a logged-in
    account can see (the optional Bluesky login is used for public post search only, and opted-out authors are still
    dropped);
  - **no automatic identity resolution** across platforms: accounts are keyed `platform:platform_id` and are never
    merged;
  - platform **opt-outs are respected**: Mastodon accounts with `noindex: true` or `indexable: false` and Bluesky
    accounts carrying the `!no-unauthenticated` label are skipped (and the skip is logged in the collection run);
  - every create, collect, view, export and delete of an investigation is written to `audit_log`;
  - deleting an investigation removes its links, runs and scoped analysis, plus posts and accounts that no other
    investigation references (posts from the global `socmint ingest` are kept);
  - demo subjects are **fictional**; no real private person appears in fixtures.
  Coordination findings stay "possible coordination", never "bots". Indicators are "reported" or "suspected",
  never "scammer".
- **Market module**: manipulation-risk alerts with evidence only. Never buy/sell/hold advice, price targets or
  predictions (enforced by a test).
- **Hate/communal**: interface only, disabled, analyst-only, never shown to citizens. The `hate_communal` post
  category is also off by default, analyst-only, and has no shipped keywords.
- This is a prototype. Its outputs can be wrong and must be reviewed by people before any action.

## Known limitations
- Small fact-check corpus: RSS feeds hold about 10 recent items each. History accumulates over scheduled runs, or
  faster with the Google Fact Check API key. BOOM and India Today feeds are unavailable (see SOURCES.md).
- Live public-channel data comes from Bluesky and Mastodon without keys; Telegram/Reddit/YouTube need keys. Market
  alerts need ticker mentions in collected posts (news headlines rarely contain tickers), so they have fired only on
  demo data so far.
- Narrative growth is inflated on a first run (no baseline history yet). Investigations therefore raise no narrative
  growth alerts until they have collected for `narratives.min_baseline_hours` (72 h); the narratives are still shown.
- Investigation categories ignore the investigation's own search terms (a flood event's posts are not "civic safety"
  just for saying "flood"; see DECISIONS.md). Devanagari keywords match at the start of a word and may be followed
  by vowel signs (so `पूर` matches `पूराचे` but not `पूर्ण`, complete); suffixes that start with a consonant (`पूरग्रस्त`)
  need their own keyword.
- Scam rules are keyword-based: novel scripts or heavily misspelt messages can slip through, and regional
  languages beyond en/hi/mr (and Hinglish) are not covered. Hindi/Marathi reply text needs native-speaker review.
- e5 claim matching uses a shipped background-mean vector computed from real headlines. Thresholds are calibrated
  on a small fixture set; recalibrate on real reviewed data.
- OCR needs Tesseract (+ hin/mar data). ASR needs the `[ml]` extra and a one-time model download.
- dnstwist scans are capped at 400 permutations per domain. Registered lookalikes include parked and defensive
  registrations (shown as low severity).
- "Seen before" is a linear pHash scan (fine up to about 100k images). There is no deepfake detection.
- Single-user local tool: no auth beyond the API key, SQLite only. The showcase has no login: keep it on localhost.
- **Bluesky keyword search**: the public AppView's `searchPosts` returns HTTP 403 without auth, so without
  `BLUESKY_HANDLE` / `BLUESKY_APP_PASSWORD` event search falls back to listening to the public Jetstream firehose for
  up to 60 s and matching terms locally. It only finds posts made *during* that window, so rare terms often return
  nothing. With the two keys it searches through Bluesky's documented logged-in route (see SOURCES.md). Account
  (subject) collection works either way.
- **Mastodon events** use hashtag timelines only (full-text search needs an authenticated account), and many
  accounts are skipped because they opted out of indexing.
- Telegram, Reddit and YouTube collection needs keys and has been tested only against recorded fixtures.

## Next steps (v1)
See the build report / DECISIONS.md. In short: grow the fact-check corpus and calibrate thresholds on reviewed
data, add public-channel sources, replace keyword rules with a small supervised classifier trained on reviewed
tipline data (keeping rule explanations), add regional languages and a Hinglish reply set, and move to Postgres
with scheduled jobs and analyst auth.

## Development
```bash
uv run pytest                        # 230+ tests, offline (TF-IDF backend)
SOCMINT_TEST_BACKEND=e5 uv run pytest tests/test_tipline.py   # same fixtures on e5
uv run ruff check socmint tests
```
Docs: `DECISIONS.md` (every non-obvious choice), `SOURCES.md` (every source, terms, live status),
`docs/DEMO.md` (5-minute showcase walkthrough and live mode). `docs/VERIFICATION.md` records the end-to-end checks
of the real app (before and after fixes).
