# Task: turn SOCMINT-India v0 into an investor-demo prototype (Investigations + categorised collection + simple showcase dashboard)

You are working in an existing Python repository at `C:\Project\SOCMINT` (Windows 11, PowerShell, Python 3.12 venv in `.venv`, managed with `uv`). It is a working prototype with milestones M0–M8 already built and about 160 passing offline tests. Do **not** rewrite it. Extend it.

## 0. Read before you write any code
Read these files completely and follow their conventions:
- `README.md`: what exists, the commands, and the legal/privacy rules
- `DECISIONS.md`: every non-obvious choice made so far (append new decisions here)
- `SOURCES.md`: every data source, its terms and its live status (append new sources here)
- `socmint/core/models.py`, `socmint/core/schemas.py`: the data model (`Item`, `FactCheck`, `Indicator`, `CoordinationGroup`, `Alert`, ...)
- `socmint/ingest/base.py`, `socmint/ingest/runner.py`, `socmint/ingest/store.py`: the `Source` plugin interface, per-source isolation and idempotent upserts
- `socmint/core/http.py`: `PoliteClient` (robots.txt, per-host rate limits, backoff, cache, User-Agent with CONTACT_EMAIL). **All new HTTP goes through it.**
- `socmint/analyze/*`: claim matching, scam scoring, narratives, coordination (MinHash + CCS), market risk
- `socmint/demo.py`, `socmint/fixtures.py`: the offline synthetic demo that uses a separate DB (`data/demo.db`)
- `socmint/dashboard/*`: the existing 7-page Streamlit analyst dashboard and `tests/test_dashboard.py` (Streamlit AppTest)

Then run `uv run pytest` and `uv run ruff check socmint tests` and confirm both are green **before** you change anything.

## 1. Goal
Make a prototype that shows investors this system can **collect, organise, categorise and keep** public social-media information about three kinds of target, and hold it for later analysis:

1. **Event**: a named happening (e.g. "Pune floods 2026"), defined by keywords/hashtags per language, places and a time window.
2. **Subject**: a specific **public** account, channel or page (a person's public account, a public Telegram channel, a YouTube channel, an organisation's handle), defined by handles per platform.
3. **Group**: a set of accounts/channels watched together. It is either listed by the analyst or **promoted** from a detected "possible coordination" group.

Each of these is an **Investigation**. An investigation is created once, can be re-collected any time ("Collect now"), and all collected posts, accounts, extracted entities and categories stay in the DB for later analysis and export.

It must work in two modes:
- **Demo mode (offline, zero keys)**: a rich synthetic scenario that always works on an investor's laptop with no internet.
- **Live mode**: real collection from the sources below. Sources that need no key must work out of the box; key-gated sources auto-enable when their key is in `.env` (the existing behaviour).

## 2. Out of scope for this task (do NOT build)
- No login, sign-in, users, roles, multi-tenancy or SSO.
- No Postgres migration, no Docker and no job queue. Keep SQLite and SQLAlchemy 2.x with portable types (as now).
- No WhatsApp, X/Twitter, Instagram, Facebook or LinkedIn, and no scraping of any login-walled or CAPTCHA-protected page.
- No React/JS frontend. The showcase dashboard is Streamlit.
- Do not merge the tipline `Submission` table into `items` yet.

## 3. Rules that must stay true (existing design; tests already enforce some)
- Official APIs and public feeds only. Honour robots.txt. Use per-host rate limits via `PoliteClient`. No browser spoofing, no fake accounts, no getting around blocks or rate limits.
- Tipline privacy is unchanged: `sender_hash` only, PII redaction, and `Indicator` never links to a submission or sender.
- Citizens never get a "true" verdict. The market module never gives buy/sell/hold advice (keep the existing test passing).
- The hate/communal category is **analyst-only** and stays off by default.
- Synthetic rows always have `synthetic=True` and live only in `data/demo.db`.
- One failing source or module never stops the others (keep per-source and per-module isolation).

### Subject (person/account) guardrails: new, and they must be implemented
The README currently says "aggregate analysis only, no profiling of individuals". This task deliberately changes that to **"analyst-directed monitoring of public accounts"**, with these guardrails. Record the change and the reasons in `DECISIONS.md`, and update the README's legal/privacy section to match.
- A Subject is created only by an explicit analyst action. It needs a **required `purpose` text** (why this account is being monitored).
- Collect only what the platform's official API returns publicly for that account (public profile fields and public posts). Never collect private/protected content, contact lists, followers' identities, DMs or anything behind login.
- **No automatic identity resolution** across platforms. Accounts on different platforms belong to the same Subject only because the analyst added them.
- Respect platform opt-outs: on Mastodon skip accounts with `noindex: true` or `indexable: false`. If a Bluesky account carries the `!no-unauthenticated` label, don't collect it.
- Every create, collect, view, export and delete of an investigation is written to `audit_log`.
- Deleting an investigation removes its links, plus any posts and accounts that no other investigation references.
- Demo-mode subjects are **fictional** (invented names and handles). Never put a real private person in fixtures.

## 4. Data model additions (in `socmint/core/models.py`, plus the existing additive migration in `db.py`)
Keep the `items` table as the single "Post" table and extend it. Add new tables. Give every new table a `synthetic` flag where demo data can land.

**Extend `Item`** (all nullable or defaulted, so existing rows stay valid):
`author_id`, `author_display_name`, `hashtags` (JSON list), `mentions` (JSON list), `urls` (JSON list), `engagement` (JSON: views/likes/shares/replies/forwards, whatever the platform gives), `reply_to_external_id`, `forwarded_from` (platform id/handle), `media` (JSON list: `{type, url, phash}`; do not download media for public items in this task, store URLs and hashes only where the existing code already hashes in memory), `text_en` (null for now; translation comes later).

**New tables:**
- `accounts`: `platform`, `platform_id`, `handle`, `display_name`, `url`, `created_at` (account creation date if the API gives it), `followers`, `following`, `posts_count`, `description` (public bio), `meta` JSON, `first_seen`, `last_seen`, `synthetic`. Unique on (`platform`, `platform_id`).
- `investigations`: `kind` (`event` | `subject` | `group`), `name`, `description`, `purpose`, `status` (`active` | `paused` | `archived`), `config` JSON (see section 5), `created_at`, `last_collected_at`, `synthetic`.
- `investigation_items`: `investigation_id`, `item_id`, `matched_by` JSON (which term/handle/source matched, so the match can be explained), `matched_at`. Unique pair.
- `investigation_accounts`: `investigation_id`, `account_id`, `role` (`subject` | `member` | `top_author`), `added_by` (`analyst` | `coordination` | `collector`). Unique pair.
- `item_labels`: `item_id`, `label`, `sublabel`, `confidence` (0–1), `method` (`rule` | `zero_shot` | `analyst`), `evidence` JSON (rules or keywords fired), `created_at`. Unique on (`item_id`, `label`, `method`).
- `edges`: `kind` (`forward` | `reply` | `mention` | `shares_url` | `uses_hashtag` | `co_posts`), `src` (account key), `dst` (account key, URL or hashtag), `item_id`, `created_at`, `investigation_id` (nullable). Use this for network views.
- `collection_runs`: `investigation_id`, `source`, `started_at`, `finished_at`, `status` (`ok` | `missing_key` | `error` | `blocked_by_robots` | `offline` | `unsupported`), `fetched`, `new`, `detail`. The dashboard uses it to show "collected X posts from Y sources".

Write store functions in `socmint/ingest/store.py` style (idempotent upserts, chunked).

## 5. Investigation config format
Investigations can be created from YAML (`config/investigations/*.yaml`, loaded by a CLI command) or from the dashboard. Example:

```yaml
kind: event
name: "Pune floods 2026"
description: "Flooding in Pune district; watch for rumours, fake relief appeals, coordinated posting"
purpose: "Public-safety misinformation and fraud monitoring"
window: {start: "2026-07-20", end: null}
languages: [en, hi, mr]
terms:
  en: ["Pune flood", "#PuneRains", "Khadakwasla dam"]
  hi: ["पुणे बाढ़"]
  mr: ["पुणे पूर", "खडकवासला"]
places: ["Pune", "Pimpri-Chinchwad"]
sources: [bluesky, mastodon, news_rss, gdelt, google_factcheck, youtube, reddit, telegram]
---
kind: subject
name: "Demo: stock-tip channel"
purpose: "Suspected pump-and-dump promotion"
handles:
  telegram: ["multibagger_tips_demo"]
  youtube: ["UCxxxxxxxx"]
  bluesky: ["tips-demo.bsky.social"]
---
kind: group
name: "Relief-appeal copy network"
purpose: "Possible coordinated fake donation appeals"
handles:
  bluesky: ["acct1.bsky.social", "acct2.bsky.social"]
  mastodon: ["@user@mastodon.social"]
```

## 6. Collectors: make them investigation-aware
Extend the `Source` interface in `socmint/ingest/base.py` without breaking existing sources:
- `capabilities: ClassVar[set[str]]` drawn from `{"terms", "accounts"}`.
- `search_terms(terms_by_lang, since, limit) -> FetchResult` for event collection.
- `account_posts(handle, since, limit) -> FetchResult`, plus `account_profile(handle) -> AccountIn` for subject/group collection.
- `FetchResult` gets an `accounts: list[AccountIn]` list. Add an `AccountIn` schema in `schemas.py`.
- A source without a capability reports `unsupported` in `collection_runs` (not an error).

Implement or extend these collectors. **Verify every endpoint live before relying on it**, record its status in `SOURCES.md` (URL, auth needed, rate limit, terms), and use recorded JSON fixtures for tests (no network in tests).

| Source | Key needed | terms (Event) | accounts (Subject/Group) | Notes |
|---|---|---|---|---|
| **bluesky** (new) | none | `app.bsky.feed.searchPosts` on the public AppView if it works without auth. If it doesn't, implement a bounded **Jetstream** listen (`wantedCollections=app.bsky.feed.post`, client-side keyword match, max N minutes / M posts) | `app.bsky.actor.getProfile`, `app.bsky.feed.getAuthorFeed` | Parse replies, quotes, facets (mentions, links, hashtags) and engagement counts. Respect the `!no-unauthenticated` label. |
| **mastodon** (new) | none | `GET /api/v1/timelines/tag/:hashtag` on a configured list of instances (start with `mastodon.social`) | `GET /api/v1/accounts/lookup`, `GET /api/v1/accounts/:id/statuses` | Strip HTML. Skip `noindex`/non-`indexable` accounts. Low rate limit. |
| news RSS (existing) | none | keyword filter over feed items | n/a | reuse `rss.py` |
| gdelt (existing) | none | build the query from event terms | n/a | keep the 10 s spacing |
| google_factcheck (existing) | key | event terms → fact-checks (store as `FactCheck`, link via claim matching) | n/a | |
| youtube (existing) | key | `search.list` with event terms (respect `max_units_per_run`) | channel → uploads playlist → videos, plus top comments | |
| reddit (existing) | key | search within configured subreddits | `/user/{name}/submitted` | |
| telegram (existing) | key + login | search **within channels listed in the investigation or sources.yaml** (`iter_messages(search=term)`) | public channel → messages, plus channel info as account | public channels only. Parse forwards into `edges` |

Every collector fills the new `Item` fields (`hashtags`, `mentions`, `urls`, `engagement`, `reply_to_external_id`, `forwarded_from`, `author_id`) and emits `accounts` for each author it sees. It also writes `edges` for forwards, replies, mentions, shared URLs and hashtags.

## 7. Investigation runner and enrichment
New module `socmint/investigate/` with:
- `runner.collect(investigation_id)`: for each enabled source in the investigation config, call `search_terms` and/or `account_posts`/`account_profile` with per-source isolation. Store results, link items to the investigation (`matched_by`), upsert accounts and edges, and write `collection_runs` and `audit_log` rows. Use `since = last_collected_at` (or the event window start).
- `enrich(item_ids)`, run right after collection, synchronously, reusing existing code wherever it exists:
  - language detection (`understand/lang.py`)
  - entity extraction (`understand/entities.py`): phones, UPI IDs, URLs, emails, tickers. Scam artifacts go to `indicators` exactly as today, with no link to submissions.
  - **categorisation** (section 8)
  - claim matching against fact-checks (`analyze/claim_match.py`) for event items, with matches recorded as evidence on the item label
  - brand impersonation check (`analyze/impersonation.py`) on URLs
- `analyze(investigation_id)`: run the existing narratives (HDBSCAN + c-TF-IDF), coordination (MinHash/CCS) and market modules **scoped to the investigation's items**. Refactor them to accept an optional list of item ids; don't copy them. Any coordination group found can be **promoted** to a new `group` investigation (with `added_by="coordination"` and the CCS evidence kept).
- `summary(investigation_id) -> dict`: counts by source, language, category and hour; top accounts; top hashtags, URLs and domains; indicators; fact-check matches; coordination groups; first and last seen. Both the dashboard and the export use it.
- `export(investigation_id, fmt)`: JSON and CSV bundle (posts with labels, accounts, edges, indicators, summary, the investigation config, and a manifest with SHA-256 of each file and the export time). Write it to `data/exports/`.

## 8. Categorisation
Create `config/categories.yaml` with this taxonomy (multi-label; one post can have several):
`misinformation` (sub: `old_or_out_of_context_media`, `fabricated_claim`, `manipulated_media`, `factcheck_matched`) · `scam` (sub: reuse the 9 categories in `config/scam_rules.yaml`) · `civic_safety` (disaster, accident, law_and_order, protest) · `election_political` · `health` · `market_manipulation` · `brand_impersonation` · `hate_communal` (**analyst-only, disabled by default**) · `other`.

Implement `socmint/analyze/categorize.py`:
1. **Rules first** (always available, offline): multilingual keyword lists (en/hi/mr + common Hinglish) per category in the YAML, plus signals the system already computes (scam score band, fact-check match, impersonation hit, market promo language). Every label stores the evidence that produced it.
2. **Optional zero-shot**: if the `[ml]` extra is installed and `categorize.zero_shot: true` is set in `thresholds.yaml`, add a multilingual NLI zero-shot classifier (pick a small, CPU-friendly multilingual NLI model, document the choice and its size in `DECISIONS.md`) with `method="zero_shot"` and its score. It must degrade gracefully (logged in `degraded`) when it's unavailable.
3. Analysts can add or remove a label from the dashboard (`method="analyst"`). Analyst labels override the others in views.
4. Put all thresholds in `thresholds.yaml`.

## 9. CLI (extend `socmint/cli.py`)
```
socmint investigate create --file config/investigations/pune_floods.yaml
socmint investigate create --kind event --name "..." --terms "a,b" --lang en --purpose "..."
socmint investigate create --kind subject --name "..." --handle bluesky:x.bsky.social --handle telegram:chan --purpose "..."
socmint investigate list
socmint investigate collect <id|name> [--source bluesky]
socmint investigate analyze <id|name>
socmint investigate show <id|name> [--json]
socmint investigate export <id|name> --format json|csv
socmint investigate promote-group <coordination_group_id> --name "..." --purpose "..."
socmint investigate delete <id|name>
socmint showcase [--demo]        # opens the simple dashboard (section 10)
```

## 10. Simple showcase dashboard (new, separate from the existing analyst dashboard)
Create `socmint/showcase/app.py`: **one Streamlit page**, clean and simple, built for a 5-minute investor demo. Keep the existing 7-page dashboard untouched. Bind to 127.0.0.1, headless, like the existing one. Show a clear "DEMO DATA (synthetic)" banner in demo mode.

Layout:
- **Header**: product name and four big numbers: posts collected · sources · investigations · accounts tracked. Below them, a row of small source-status chips (green ok / grey missing key / red error) from `collection_runs`.
- **Sidebar, "New investigation"**: a choice of Event / Person-Account / Group, then a name, a purpose (required), and keywords (Event) or `platform:handle` lines (Subject/Group). Source checkboxes default to the no-key sources. A **Collect now** button shows per-source progress with `st.status`, then counts.
- **Sidebar**: a list of existing investigations to pick from.
- **Main area, for the selected investigation, five tabs:**
  1. **Overview**: posts over time by source (line or bar chart), a category breakdown (bar chart), languages, a top-5 "key findings" list generated from `summary()` (e.g. "12 posts match an Alt News fact-check", "8 accounts posted the same text within 6 minutes", "3 UPI IDs reported as suspected scam").
  2. **Posts**: a filterable table (source, time, author, text, categories with confidence, link), filtered by category, source and language. Clicking a row shows full text, labels with evidence, fact-check matches and extracted entities.
  3. **People & accounts**: account cards (platform, handle, display name, created date, followers, post count, bio, first and last seen in this investigation), plus top authors by post count. For a Subject, show its profile and a per-day activity chart.
  4. **Network**: a simple graph of accounts ↔ shared URLs/hashtags/forwards from `edges` (reuse `st.graphviz_chart` as in `pages/5_Coordination.py`, capped to the top 40 nodes), plus the coordination groups found with their score and evidence, and a **Promote to Group** button.
  5. **Evidence & export**: indicators (phones, UPI, domains) with status, fact-check matches, collection-run log, and buttons for **Export JSON** / **Export CSV** (`st.download_button`).
- Use Streamlit's built-in charts only (no new heavy chart dependencies). Keep the whole app under about 400 lines with helpers in `socmint/showcase/data.py`.

## 11. Demo scenario (offline, synthetic, used by `socmint showcase --demo`)
Extend `socmint/fixtures.py` / `socmint/demo.py` so `socmint showcase --demo` rebuilds `data/demo.db` and seeds **three investigations** that make every tab rich:
1. **Event: "Pune floods 2026"** (fictional), about 300 posts across bluesky, mastodon, telegram, reddit, youtube and news, in en/hi/mr/Hinglish, spread over 5 days with a visible spike. Include:
   - an old-video rumour cluster ("dam has burst", a reused video) that matches a synthetic fact-check
   - a fake relief-fund appeal with a UPI ID and a lookalike donation domain (scam + brand impersonation)
   - a coordinated group of 8 accounts posting near-identical text within 6 minutes (it should score high on CCS)
   - ordinary civic-safety posts (road closures, helplines) so not everything looks malicious
2. **Subject: "Demo: stock-tip channel"**: a fictional public Telegram channel plus a linked fictional YouTube channel posting pump messages about a small-cap symbol, lined up with synthetic NSE volume so the market-risk alert fires.
3. **Group**: the 8 coordinated accounts, promoted from the event's coordination group.

All names and handles are invented and marked synthetic. The demo must run with no internet and no keys in under about 60 seconds on the TF-IDF backend.

Also write `docs/DEMO.md`: a click-by-click 5-minute investor walkthrough (what to click and what to say on each tab), and a "live mode" section showing how to create a real event with only the no-key sources.

## 12. Tests (offline; keep all existing tests green)
- Parsing tests for the bluesky and mastodon collectors, using recorded JSON fixtures in `tests/fixtures/` (strip any real personal data from recorded responses, or use synthetic ones in the real response shape).
- Opt-out tests: a Mastodon `noindex` account and a Bluesky `!no-unauthenticated` account are skipped.
- Investigation lifecycle: create → collect (with mocked sources) → items linked with `matched_by` → summary counts → export manifest hashes match the files → delete cleans up orphans → audit_log rows exist.
- Categoriser: the rule labels for each category carry evidence; hate_communal is off by default; analyst labels override.
- Scoped analysis: coordination on the demo event finds the 8-account group; promoting it creates a `group` investigation.
- A Showcase AppTest: the app loads on the demo DB, every tab renders, and there are no exceptions.
- The existing market no-recommendation test still passes, and the showcase text never contains buy/sell/hold wording.

## 13. Working method
- Work in small milestones and commit after each one, using the existing commit style (`M9: ...`, `M10: ...`):
  M9 data model + store · M10 investigation-aware Source interface + bluesky + mastodon · M11 existing collectors extended (rss, gdelt, youtube, reddit, telegram) · M12 categoriser · M13 investigation runner + scoped analysis + export + CLI · M14 demo scenario · M15 showcase dashboard · M16 docs (README, DECISIONS, SOURCES, DEMO).
- After each milestone, run `uv run pytest` and `uv run ruff check socmint tests`. Don't move on while either is red.
- **Ask me before you**: add any real Telegram channel, subreddit, YouTube channel or real person's account to config files; add a new data source not listed above; install system-level software; or change any rule in section 3.
- If a live endpoint doesn't behave as described here, don't work around it with scraping. Mark it in `SOURCES.md`, make the source fail cleanly, and tell me.
- Python stays at 3.11+ compatible. Add new dependencies to `pyproject.toml` only if they're needed (prefer `httpx` and `websockets` for Bluesky/Mastodon over new SDKs).

## 14. Done means
1. `uv sync --extra dev`, then `uv run socmint showcase --demo`, opens a dashboard with the three demo investigations and every tab populated. No internet and no keys needed.
2. With internet and **no keys**, `uv run socmint investigate create --kind event --name "test" --terms "flood,बाढ़" --purpose "demo"` followed by `collect` stores real posts from at least Bluesky, Mastodon, news RSS and GDELT (GDELT may be throttled; that's fine if it fails cleanly), categorised and visible in the showcase.
3. A real public Bluesky or Mastodon account can be added as a Subject and its profile and recent public posts appear in "People & accounts".
4. All tests pass and ruff is clean. README, DECISIONS.md, SOURCES.md and docs/DEMO.md are updated.
5. End with a short report: what you built, the exact commands to run the demo, which live sources you verified (with dates), and anything that doesn't work yet.
