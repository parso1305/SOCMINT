# Task: make live event collection strong enough to demo (M17–M20)

You are working in `C:\Project\SOCMINT` (Windows 11, PowerShell 5.1, Python 3.12 venv in `.venv`). M9–M16 and the prompt-2 fixes are committed. `docs/VERIFICATION.md` shows everything passing, with 235 tests and ruff clean.

**The problem this task solves.** The offline demo is strong, but a live event is thin. A live test on 2026-11-06 used terms `Delhi pollution, Delhi AQI, #DelhiPollution, दिल्ली प्रदूषण` with no keys:
- **Posts:** 11 in 131 s (8 Mastodon, 3 news RSS).
- **GDELT:** failed with an SSL handshake timeout. The day before it returned HTTP 429.
- **Bluesky:** 0 posts. Public `searchPosts` returns 403, and the 60 s Jetstream listen matched nothing.
- **Categories:** 10 of 11 posts were labelled `other`, because there is no environment / air-quality category.
- **Speed:** collection is sequential, so the total time is the sum of every source (the Jetstream listen alone is 60 s).

The subject (account) path works well: 52 Bluesky + 3 Mastodon posts in about 10 s. Don't change its behaviour.

Keep every rule in `README.md` → "Legal & privacy notes" and `DECISIONS.md` → "Investigations: scope change":
- Official APIs and public feeds only. No scraping, no spoofing, no getting around blocks.
- Opt-outs are respected.
- A purpose is required.
- No identity resolution across platforms.
- Everything is audited.
- No "true" verdicts. No buy/sell/hold.

## 0. Environment (same as prompt 2)
- **Running commands:** `uv` is not on PATH. Use `python -m uv ...` or `.\.venv\Scripts\socmint.exe`.
- **Live runs:** use a separate DB in every terminal: `$env:SOCMINT_DB_URL = "sqlite:///C:/Project/SOCMINT/data/verify.db"`. Never run live commands against `data/socmint.db`. Delete `verify.db` and any exports when done.
- **Ports:** port 8000 is Splunk's, so use `api --port 8010`. 8501 is the analyst dashboard and 8502 the showcase. Stop every server you start.
- **curl:** use `curl.exe`, never `curl`.
- **OCR:** Tesseract is not installed. OCR results are "SKIPPED", not FAIL.
- **Secrets:** `.env` holds my secrets. Never print, log, copy or commit any value from it. To check a key, print only `set` / `missing`.
- **Git:** `prompts/` and `.claude/` are my files (committed). Don't edit or delete them. Stage by explicit path, never `git add -A` or `git add .`.
- **Before you change anything:** run the live baseline in section 5 (L1) and record it as "before".

## M17. Faster, sturdier collection

**17.1 Collect sources in parallel**
- **Current behaviour:** `socmint/investigate/runner.py:collect()` runs `_collect_one` source by source.
- **Fetch in parallel:** split each source's work into a *fetch* step (network only, returns the `FetchResult`) and a *store* step (DB writes). Run the fetches in a `ThreadPoolExecutor` (`investigate.max_parallel_sources: 4` in `thresholds.yaml`).
- **Store sequentially:** do every store step in the calling thread, in completion order, so SQLite never sees two writers.
- **Progress:** call `progress(...)` only from the calling thread. Streamlit can't be called from worker threads.
- **Per-host rate limiting:** `socmint/core/http.py:PoliteClient._wait` sleeps *while holding the one global lock*, so parallel fetches would still run one at a time. Switch to one lock per host:
  - Two hosts never wait on each other.
  - Requests to the same host stay spaced by its `rate_limit_s`.
  - Make robots.txt caching thread-safe too.
- **Jetstream:** `asyncio.run` inside a worker thread is fine (each thread gets its own loop). Keep it.
- **Tests (offline, with fake sources):**
  - Two sources on different hosts, each taking 1 s, finish in under 1.6 s.
  - Two on the same host stay serialised.
  - One failing source doesn't stop the others.
  - Run rows and the audit are written exactly as before.
- **Target:** a no-key event collect takes about as long as its slowest source (about 60–75 s), down from 131 s.

**17.2 GDELT that fails cleanly and recovers**
- **Timeouts:** `socmint/ingest/gdelt.py` uses the client default of 20 s for everything. Give GDELT a 10 s connect timeout and a 30 s read timeout.
- **Retries:** retry **once** on a timeout or connection error, after waiting the host interval. Never retry on 429 in an interactive run.
- **Cooldown after a 429:** save a cooldown (`gdelt.cooldown_minutes: 15`) to `data/cache/cooldowns.json`. Until it expires, skip GDELT with status `throttled` and the detail "GDELT is limiting this IP; next try after HH:MM". Don't send a request during the cooldown.
- **Caching:** make sure the event query goes through the HTTP cache (`cache_ttl_s`), so pressing "Collect now" twice in 30 minutes doesn't call GDELT again.
- **Test:** mocked transport for timeout→success, 429→cooldown→skip, and cooldown expiry.

**17.3 One set of status words everywhere**
- Statuses: `ok`, `partial`, `throttled`, `no_key`, `offline`, `opted_out`, `error`.
- Use the same words in the CLI lines, showcase chips (colour per status), the collection log and `collection_runs.status`.
- `no_key` explains which `.env` variable enables the source.

**17.4 `socmint doctor`**
A read-only check to run before a demo. It prints a table of PASS / WARN / FAIL with a one-line fix for each:
- **`.env`:** whether `CONTACT_EMAIL` is set (print `set`, never the value).
- **Keys:** each optional key `set` / `missing`, and which sources it enables for events.
- **Event sources:** which sources an event would use right now.
- **Reachability:** one polite request per host, reusing the robots.txt fetch and its cache. Report ok, timeout or HTTP status, plus any GDELT cooldown.
- **Models:** whether the e5 model is cached.
- **Tesseract:** found or not.
- **Ports:** 8000/8501/8502 free (name the process if not).
- **Demo data:** age of `data/demo.db` (WARN if older than 12 h: "rebuild before a meeting").
- **`uv`:** whether it's on PATH (WARN with the `python -m uv` hint).

## M18. More coverage, only from keys I choose to add

**18.1 Bluesky logged-in search (optional, off unless I add the keys)**
- **Settings:** new `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD` in `socmint/core/settings.py`, plus `.env.example` with comments:
  - Use a dedicated project account, never a personal one.
  - Use an **app password** from Settings → Privacy and security → App passwords, never the account password.
  - Don't give the app password DM access.
- **Documented route** (docs.bsky.app, checked 2026-11-07):
  - **Log in:** `POST https://bsky.social/xrpc/com.atproto.server.createSession` with `{identifier, password}`.
  - **Find the account's server:** read its PDS endpoint from the returned `didDoc` (the `#atproto_pds` service). Fall back to `https://bsky.social`.
  - **Search:** `GET {pds}/xrpc/app.bsky.feed.searchPosts` with `Authorization: Bearer <accessJwt>` and the header `atproto-proxy: did:web:api.bsky.app#bsky_appview`.
  - **Parameters:** `q`, `sort=latest`, `since`, `limit` ≤ 100, and follow the `cursor` up to `investigate.per_source_limit`.
  - **Not these hosts:** `api.bsky.app` and `public.api.bsky.app` don't accept auth.
- **Sessions:**
  - `createSession` is limited to **30 per 5 minutes and 300 per day per account**, so reuse the session. Keep `{accessJwt, refreshJwt, did, pds}` in `data/cache/bluesky_session.json` (git-ignored).
  - Refresh with `com.atproto.server.refreshSession`. On a 401, refresh once, then log in again once, then fail with `error`.
  - Tokens must never appear in logs, exceptions, `collection_runs.detail`, exports or the HTTP disk cache. The cache key must not contain them; use a short TTL or no cache for authenticated calls.
- **Order in `search_mode: auto`:** logged-in search (if keys are set) → public `searchPosts` → bounded Jetstream listen. The chip says which was used ("Bluesky · search (logged in)" / "Bluesky · live listen 60 s").
- **Opt-outs:** unchanged. Even when logged in, drop every author labelled `!no-unauthenticated`. Our output leaves the platform, so we treat it like a logged-out view. Keep the existing label handling in `parse_post_view`.
- **Docs:** record in `SOURCES.md` and `DECISIONS.md` that this is Bluesky's documented route for logged-in clients, not a way around the public 403.
- **Tests (mocked):** login, refresh, re-login, pagination, opt-out drop. Use `caplog` and the cache/file contents to prove a token never leaks. With no keys, behaviour is exactly as today.

**18.2 Key-gated sources join events automatically**
- **Problem:** `NO_KEY_SOURCES` in `socmint/investigate/config.py` is the fixed default for events, so a YouTube or Fact Check key I add is never used by an event.
- **New default for an event:** the no-key sources, plus `youtube` if `YOUTUBE_API_KEY` is set, plus `google_factcheck` if `GOOGLE_FACTCHECK_API_KEY` is set.
- **Reddit and Telegram stay as they are:** used only when the investigation lists `channels` (subreddits or public channels), since these need my approval.
- **`investigate create`:** prints each source and, for skipped ones, why ("no_key: set YOUTUBE_API_KEY").
- **Showcase "New investigation" form:**
  - Lists the sources with checkboxes.
  - Sources with missing keys are greyed out with the variable name.
  - Hindi/Marathi terms entered on a separate line are stored under their own language.
- **New `socmint investigate edit NAME`:**
  - Options: `--add-terms`, `--remove-terms`, `--lang`, `--add-source`, `--remove-source`, `--purpose`.
  - Each change is written to `audit_log` with the before and after.
  - The showcase gets the same actions.
- **YouTube quota:** respect `max_units_per_run`; `search.list` costs 100 units.

**18.3 More official news feeds, proposed only**
- **What to propose:** up to 10 official publisher RSS feeds that would help Indian events, each taken from the publisher's own RSS page. Candidates to check:
  - Times of India, Deccan Herald
  - Lokmat, Loksatta, Maharashtra Times (Marathi)
  - Navbharat Times, Dainik Bhaskar (Hindi)
  - PIB press releases in English / Hindi / Marathi
- **For each one:** verify it returns 200 now, that robots.txt allows it, and record the language and publisher.
- **Add them** to `config/sources.yaml` with `enabled: false` and `# PROPOSED - awaiting approval`, and document them in `SOURCES.md`. **Don't enable them.**
- **Don't use Google News RSS.** Its terms are for personal use.

## M19. Analyst-directed ways to grow coverage

**19.1 Suggested terms (never added automatically)**
- **Where:** for an event, compute suggestions from its collected posts and show them in `investigate show` and in the showcase Overview as "Suggested terms (add to widen collection)".
- **What counts:**
  - Co-occurring hashtags.
  - Frequent capitalised names and places.
  - Frequent Devanagari words or bigrams that aren't already terms.
- **Minimum support:** at least 3 posts from at least 2 accounts.
- **Exclude:** stopwords (the narratives list), generic tags (`#india`, `#news`, `#breaking`; keep the list in config), handles, URLs, numbers, and terms shorter than 3 characters.
- **Adding:** the analyst ticks suggestions and clicks "Add selected". This uses the same audited path as `investigate edit`.
- **Expected:** in the Delhi test, suggestions like `#airpollution`, `#delhincr`, `#supremecourt` appear. Adding one and collecting again finds more posts.

**19.2 Watch mode**
- **Command:** `socmint investigate watch NAME --every 15m [--for 8h] [--analyze]`. It repeats collect (and analyze) every cycle.
- **Each cycle:** minimum interval 5 minutes; audited; prints a one-line summary. Sources in cooldown are skipped. Ctrl+C stops it cleanly after the current source.
- **Bluesky without an app password:** keep **one** Jetstream connection open for the whole watch, in a background thread. Match terms as posts arrive, queue matching post URIs (bounded queue), then hydrate and store them each cycle. On a disconnect, reconnect with Jetstream's `cursor` (the `time_us` of the last event received). Never replay further back than the last event received.
- **Purpose:** watch mode turns "only posts made during a 60 s listen" into "every matching Bluesky post since the watch started". It also builds the history that narrative growth alerts need (`narratives.min_baseline_hours`).
- **Docs:** in `docs/DEMO.md`, explain how to start a watch the day before a meeting.

## M20. Categories, honest showcase, docs

**20.1 Environment category and safer Devanagari matching**
- **New `environment` category** ("Environment / air quality") in `config/categories.yaml`, with en/hi/mr/hinglish keywords:
  - **English:** air pollution, AQI, air quality, smog, PM2.5, PM10, stubble burning, crop burning, parali, firecrackers, GRAP, odd-even, heatwave, heat wave, cold wave, water shortage, water crisis, water tanker, garbage, landfill, noise pollution.
  - **Hindi:** वायु प्रदूषण, प्रदूषण, एक्यूआई, स्मॉग, पराली, धुंध, शीतलहर, पानी की किल्लत, कूड़ा.
  - **Marathi:** वायू प्रदूषण, प्रदूषण, धुके, उष्णतेची लाट, पाणीटंचाई, पाणीकपात, कचरा.
  - **Hinglish:** pradushan, aqi kharab, smog.
  - **Add to `health`:** asthma, breathing, respiratory, mask, साँस, दमा, श्वास.
  - **No Devanagari keyword shorter than 3 characters.** For example, लू (heatwave) would match inside countless words. Use "लू चल" or "लू का प्रकोप" instead.
  - Add `zero_shot_label`.
  - Live-test categorisation with the Delhi terms.
- **Root-cause fix for the known substring problem** (`पूर`, flood, matching inside `पूर्ण`, complete; see "Known, not fixed" in `docs/VERIFICATION.md`):
  - A Devanagari keyword must match at the **start of a word** (start of text, or after a space or punctuation).
  - The character right after it must **not** be a virama (`्`) or a consonant.
  - Vowel signs, anusvara, chandrabindu, visarga and nukta may follow, so inflections still match (`पूराचे`).
  - Apply this in `keyword_hits` (shared by scam and categories) and in `TermMatcher`.
  - Where a compound is wanted (`पूरग्रस्त`), add it to the list explicitly.
  - **Tests:** `पूर` matches "पूर आला" and "पूराचे" but not "पूर्ण". `बंद` matches "बंद" and "बंदी" but not "बंदर". `संप` (if still listed) doesn't match "संपर्क".
  - Run the full suite, update scenario expectations only where the new rule is correct, and record the rule in `DECISIONS.md`.

**20.2 Honest growth in key findings**
- **Problem:** `socmint/showcase/data.py:key_findings` still prints "growth x20.0" for an investigation without the 72 h baseline. The prompt-2 fix only held back the *alerts*.
- **Fix:** when history is under `narratives.min_baseline_hours`, print "Most active narrative: … (10 posts in the last 24 h; growth not rated yet: 3 h of history, needs 72 h)".
- **Demo:** make the **synthetic scenario** record its collection runs as if it had been watched every 6 h across its 5-day window, so the demo event has history and its growth is rated. Use backdated `collection_runs`, synthetic only, and say so in `DECISIONS.md`.

**20.3 Chips show totals**
- **Problem:** the group investigation shows "Bluesky (demo) · 0 new" while holding 16 posts.
- **Fix:** chips show this investigation's total posts per source ("Bluesky (demo) · 5 posts"), with the last run's status and new-count in the tooltip or the collection log.

**20.4 Demo freshness**
- **Problem:** on reuse, `data/demo.db` built yesterday shows stale "last 24 h" numbers.
- **Fix:**
  - If it is older than 12 h, `showcase --demo` prints a warning.
  - The demo banner shows "Synthetic data built <date time>".
  - `doctor` reports its age.

**20.5 Small polish**
- `investigate show` prints times to the minute (no microseconds).
- README and `docs/DEMO.md` get a Windows note: if `uv` isn't on PATH, use `python -m uv run socmint …` or `.\.venv\Scripts\socmint.exe …`.
- `docs/DEMO.md` gets a "Live demo recipe" section:
  1. Run `socmint doctor`.
  2. Create the event with specific terms (place + topic + hashtag, in en/hi/mr).
  3. Start `investigate watch` the day before.
  4. Add suggested terms.
  5. Open the showcase.

## 5. Verification (append a "Prompt 3 (date)" section to `docs/VERIFICATION.md`)
Run each check **before** and **after** your changes, with the commands, expected and actual results, PASS/FAIL/SKIPPED, and the commit.

| ID | Check | Expected after |
|---|---|---|
| A1 / A2 | `python -m uv run pytest -q`; `python -m uv run ruff check socmint tests` | all pass, clean |
| L1 | No keys. On a fresh `verify.db`: `investigate create --kind event --name "delhi-air" --terms "Delhi pollution,Delhi AQI,#DelhiPollution" --purpose "verification"`, then `collect`, `analyze`, `show`. Use the same command before and after | wall time ≤ ~75 s; post count ≥ before; GDELT `ok`, or `throttled` with a next-try time; most posts labelled `environment`, not `other`; suggested terms printed |
| L1b | After only: `investigate edit "delhi-air" --add-terms "दिल्ली प्रदूषण,वायु प्रदूषण" --lang hi`, then `collect` | the terms are stored under `hi`; the edit is in `audit_log`; Hindi posts are added if any exist |
| L2 | `collect` again within 30 min | GDELT served from cache or skipped in cooldown; no new 429 |
| L3 | If `BLUESKY_HANDLE`/`BLUESKY_APP_PASSWORD` are set: the same event | Bluesky `ok` via logged-in search, posts > 0, no token in logs, `data/cache` or `collection_runs`. If not set: SKIPPED (no key) |
| L4 | If `YOUTUBE_API_KEY` / `GOOGLE_FACTCHECK_API_KEY` are set: a new event uses them automatically | YouTube posts and/or fact-check matches > 0, quota respected. If not set: SKIPPED (no key) |
| L5 | `investigate watch "delhi-air" --every 5m --for 12m --analyze` | 2–3 cycles; Jetstream kept open between cycles (if no app password); Ctrl+C stops cleanly; audit rows per cycle |
| L6 | Add one suggested term, collect again | more posts; audit shows the edit |
| L7 | `socmint doctor` | every line PASS/WARN/FAIL with a fix; no secret values printed |
| S1 | `showcase --rebuild`, then `showcase --demo` | demo event shows rated growth from synthetic history; chips show totals; banner shows build time; group chips no longer "0 new" |
| S2 | Showcase "New investigation" form, live mode on `verify.db` | sources listed with key status; parallel progress shown per source; suggested terms can be added |
| R1 | Subject regression: `bluesky:bsky.app` + `mastodon:@Mastodon@mastodon.social` | as before (≈50 + ≥1 posts, under ~30 s) |
| R2 | `showcase --demo` offline, every tab, for all 3 investigations; `dashboard --demo`, every page | no exceptions; prompt-2 results unchanged |

## 6. How to work
- Commit after each numbered item with `M17.1: …`, `M18.2: …` and so on, and add tests in the same commit.
- Fix any failure at the root cause, never by loosening a test.
- If a live endpoint misbehaves, make the source fail cleanly, record it in `SOURCES.md`, and tell me. Don't work around it.

## 7. Do NOT (ask me first)
- Create any account, or enter or ask me to paste passwords or keys in chat. I put keys in `.env` myself.
- Enable the proposed news feeds, or add real Telegram channels, subreddits, YouTube channels or accounts to any config.
- Replay Jetstream history beyond the reconnect cursor, run a full firehose archive, or use any unofficial endpoint, scraper or mirror.
- Use Google News RSS, X/Twitter, Instagram or Facebook.
- Add login/auth for the app, Postgres, Docker or a job queue.

## 8. Done means
1. M17–M20 done, each with tests and its own commit. `pytest` green, ruff clean.
2. `docs/VERIFICATION.md` has the before/after table for this prompt. Every row is PASS, or SKIPPED with a reason.
3. README, DEMO.md, DECISIONS.md, SOURCES.md and `.env.example` updated.
4. `data/verify.db`, exports and any Bluesky session cache from testing are deleted. `git status` is clean, and `data/socmint.db` is unchanged.
5. A short final report:
   - live post counts and times before and after;
   - which optional keys were present and what they added;
   - what still fails;
   - the exact commands for a live demo.
