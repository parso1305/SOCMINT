# Decisions log

Choices made without asking (as permitted by the build prompt), with the reasoning. Newest at the bottom of each section.

## Environment (M0)
- **Python 3.12 venv** (3.13 is the system default). 3.12 has the widest wheel coverage for torch / ctranslate2 / dnstwist on Windows.
- **uv** installed at user level (`pip install --user uv`); not a system package. The venv is `.venv`; `uv sync --extra ml --extra dev`.
- **CPU-only torch** pinned through `[tool.uv.sources]` to the PyTorch CPU index so no CUDA wheels download.
- **ffmpeg** installed with winget (approved). **Tesseract** install was cancelled at the UAC prompt, so OCR is *disabled with a warning* until it is installed manually (see README).
- `CONTACT_EMAIL` is left empty in `.env` on purpose: it is sent in the User-Agent to every source, so the operator must fill it in deliberately. Sources still run, but the UA reads `contact-not-configured` and a warning is logged.

## Data model
- Extra tables beyond the minimum: `media_seen` (perceptual hashes for "seen before"), `lookalike_domains`, `coordination_groups`, `preferences` (reply language keyed by sender_hash only).
- `Indicator` has **no** column linking it to a submission or sender (enforced by a test).
- Indicator status adds **`reported`** (value came from a third-party threat feed such as OpenPhish/URLhaus) alongside suspected/confirmed/benign.
- Every table that can hold demo data has a `synthetic` flag. The demo also uses a **separate DB file** (`data/demo.db`) so synthetic rows never mix with real data.
- Times are stored as naive UTC.
- Embeddings stored as float32 bytes (`LargeBinary`) plus model name, so they are invalidated when the model changes; portable to Postgres.

## Privacy
- `sender_hash` refuses to run without `PRIVACY_SALT` (no unsalted fallback). `socmint init-db` generates the salt and API key when `.env` does not exist.
- Redaction also removes **UPI IDs** from stored submission text (they can identify the sender); suspected UPI IDs claimed by the message still go to `indicators` (extracted from raw text before redaction, no sender link).
- Devanagari digits are converted to ASCII before redaction/extraction so `९८७६…` cannot slip through.

## Ingest (M1)
- **No browser spoofing.** BOOM returns 403 to non-browser User-Agents; we disable it rather than pretend to be a browser.
- **crt.sh disabled**: its robots.txt is `Disallow: /`. dnstwist with DNS resolution covers lookalikes instead.
- **URLhaus disabled until `URLHAUS_AUTH_KEY` is set**: abuse.ch's current terms require an Auth-Key for dataset downloads, even though the legacy URL still answers.
- **OpenPhish** uses the official GitHub-hosted community feed (openphish.com itself timed out). Its terms allow **non-commercial use only**.
- **GDELT**: 10 s spacing, and robots.txt fetches count toward the per-host rate limit. One early burst got the IP throttled (HTTP 429). Retries back off longer on 429.
- **SEBI RA/IA register**: only a paginated HTML listing exists, so v0 uses `config/sebi_registered.csv`, maintained by hand (fictional demo rows only).
- **NSE**: the UDiFF bhavcopy format is used; the first run backfills 45 calendar days. NIFTY 100 membership is the large-cap / liquidity proxy.
- Images in fact-check feeds are hashed **in memory** (pHash) and never written to disk (`cache_ttl=0`).
- The HTTP cache never stores query strings (they may carry API keys).
- Telegram channel posts use the **channel** as author; post signatures (personal names) are not stored.

## Brands
- Official domains were verified by DNS + HTTPS on 2026-10-03. Support numbers are **only** ones visible on the brand's own page (`verified_from`); JS-rendered pages (HDFC, Paytm, ICICI toll-free) are left empty rather than filled from memory.
- `bank.in` / `fin.in` are added as extra public suffixes. They are missing from tldextract's bundled list, which would otherwise make every bank share one "registered domain".
- `lookalike_keywords` are 5 characters or longer. Short brand names like "sbi" would match unrelated domains.

## Understanding & scoring (M2)
- Language ID: script share + small Hindi/Marathi/Hinglish marker lists, then langdetect as a tie-breaker. On short messages langdetect alone confuses hi and mr and can't see Romanised Hindi.
- **Hinglish (hi-Latn) gets English replies**. Romanised Hindi readers read Latin script; a Hinglish template set is a v1 item.
- Bare domains without a scheme are accepted only for a configurable TLD list (`bare_domain_tlds`), to avoid `ok.how`-style false positives.
- Scam score = capped sum of weights from fired rules. A category fires once (weight + bonus per extra keyword). Signals added beyond the prompt's list, all configurable:
  `apk_link`, `link_request` and `contact_mobile` (both count only alongside a scam category), `group_invite` and `sebi_claim_unverified`.
- **Indicators are stored only when the verdict is likely scam / suspicious**, so numbers in ordinary messages never become "suspected". E-mails are never indicators.
- Verdict precedence: likely scam > fact-check match > seen before > suspicious > not verified. A likely scam that also matches a fact-check still shows the fact-check as a reason.
- Hindi/Marathi reply templates were drafted for the prototype and **need native-speaker review**.

## Claim matching calibration (M2)
- multilingual-e5 cosines are compressed: on fixtures, unrelated pairs scored up to 0.88 and true monolingual matches 0.91 or more. The suggested 0.80 would match everything.
- Fix: **mean-centred cosine**. Subtract the mean embedding of a broad background corpus, then cosine. Fixture result: true matches (including cross-lingual en→mr) at least 0.549, worst non-match 0.472. Threshold `e5_centered: 0.52`.
- The background mean comes from 490 real ingested news headlines, shipped as `data/e5_background_mean.npy` (384 floats, no text). A purely local corpus biased the centring when it was dominated by the same topics as the fact-checks. Recompute with `socmint analyze --module background`.
- Without a background, raw cosine with threshold 0.90 is used (cross-lingual matches are then missed).
- TF-IDF fallback (char_wb 2–5): threshold 0.45; true matches 0.76–0.83 on fixtures. Same-language only.
- All 40 tipline fixtures pass on both backends (fc_cross needs e5), including runs against the 90 real fact-checks, with no false matches.

## Media & brands (M4)
- **dnstwist generates permutations only; we resolve them ourselves** with the OS resolver (`getaddrinfo`) in an 8-thread pool. `dnstwist.run(registered=True)` took over 500 s per domain on Windows (dnspython walked slow nameservers), versus 4 s for 400 permutations now. The cap is 400 permutations per domain; IDN homoglyph variants (thousands) are sampled last.
- First live scan: 262 registered lookalikes (HDFC 56, ICICI 86, Paytm 56, SBI 36, PhonePe 23, MSEDCL 5; none under gov.in). Scan-only lookalikes are **low** severity because registration is weak evidence (parked or defensive). Use in a tipline message or public post creates a separate **medium** impersonation alert.
- "Seen before" is a linear scan over `media_seen` (fine up to about 100k hashes; a BK-tree / FAISS binary index is a v1 item). Fact-check context is listed before earlier tipline submissions.
- Video: one ffmpeg keyframe every 5 s (max 30), each pHashed; the audio track goes to ASR.

## Narratives & coordination (M5)
- HDBSCAN (scikit-learn) on L2-normalised embeddings with Euclidean distance (equivalent ordering to cosine). `min_cluster_size: 5`.
- Keywords: class-based TF-IDF with a token pattern that keeps Devanagari vowel signs inside words, plus small en/hi/mr stopword lists (`config/stopwords.yaml`).
- Fact-check linking by **member voting**: the 20 most central posts are each claim-matched; a fact-check is linked if at least max(2, 20%) of them match it. Easy to explain ("7 of 12 posts match").
  In **TF-IDF mode** a link also needs at least 2 cluster keywords in the fact-check text. Character n-grams scored a false link (dam rumour → old "Demonagar bridge" fact-check, 0.463) above a true one (0.457).
- Growth = count in the last 24h ÷ max(daily average of the previous 72h, 0.5). On a **first run** against RSS-only data growth is inflated, because feeds only hold recent items; real baselines build up over days.
- CCS v0: MinHash (128 perms) over word 3-gram shingles; LSH at Jaccard 0.8; union-find groups of at least 3 posts by at least 3 accounts.
  Score = 0.35·accounts (saturating at 10) + 0.35·synchrony (share within ±10 min of the median) + 0.15·channel diversity + 0.15·burstiness. Alert at ≥0.6 (high at ≥0.8).
  Demo set: 0.928. Organic control (same text over 3 days): 0.372.
- Coordination runs only on `telegram/reddit/youtube` items with an author handle. All of those sources need keys, so on real data it currently has nothing to analyse.

## Market risk (M6)
- Mentions per symbol per **UTC day**, counted from all items (ticker extraction needs `$SYM`/`#SYM`, or a listed symbol in capitals next to a stock context word).
- z-scores against the previous 30 trading days (at least 10 required). Volume is z-scored on log(1+volume). Std floors (`min_std_mentions: 1`, `min_std_log_volume: 0.05`) keep a quiet all-zero baseline from producing absurd z values.
- Flag when mention_z ≥ 3 **and** volume_z ≥ 3 **and** mentions ≥ 5. Risk score = 50 base + 15 small/illiquid (not NIFTY 100 and average turnover below ₹5 crore) + 15 promotional language (at least 30% of posts hit the investment-scam keywords) + 15 unverified SEBI claim (number not in the registry CSV, or a claim with no number) + 5 price move of 10% or more.
- **No recommendations**: alert title, summary and disclaimer come from fixed neutral templates. Promoter posts are kept only under `quoted_posts`. A test asserts generated text never contains buy/sell/hold/target/prediction wording (en/hi/mr).
- Real-data note: news RSS headlines almost never contain tickers, so live alerts need the key-gated Telegram/Reddit/YouTube sources.

## Dashboard & review (M7)
- **Privacy fix found while building the review flow**: evidence stored on `Submission.reasons` is now sanitised (no phone or UPI values). The values needed for "review → update indicator status" sit in `pending_indicators`, a deliberately **time-limited** link that is wiped on review and by retention (`RETENTION_DAYS`). After that, nothing ties a sender pseudonym to an indicator.
- Review outcomes: confirmed_scam → indicators `confirmed`; benign → `benign`; misinformation / unclear → no indicator change. Every review is written to `audit_log`, and so is every dashboard page view.
- `socmint dashboard --demo` browses `data/demo.db`. Streamlit runs headless on 127.0.0.1 (the first-run e-mail prompt otherwise blocks the server).
- The hate / communal module's status ("not enabled") is shown on the Sources page.
- Small additive migration in `init_db` (ALTER TABLE ADD COLUMN for new model columns), so existing DBs keep working.

## Investigations: scope change (M9–M13)
- **Changed rule: "aggregate analysis only, no profiling of individuals" → "analyst-directed monitoring of public accounts".** The investor-prototype brief (`prompts/01_investor_prototype.md`) asks for *subject* investigations of named public accounts. We accept it only with the guardrails the brief sets, and enforce them in code:
  - a subject exists only after an explicit analyst action (CLI, YAML file or dashboard form) with a **required, non-blank `purpose`** (validated by `InvestigationConfig`);
  - collection uses only what each platform's official API returns publicly: public posts and public profile fields (handle, display name, bio, counts, creation date). No follower lists, no contacts, no DMs, nothing behind a login;
  - **no identity resolution**: accounts are keyed `platform:platform_id` and never merged across platforms, not even when handles match;
  - **opt-outs**: Mastodon `noindex: true` / `indexable: false` and Bluesky `!no-unauthenticated` accounts are skipped with a note in the collection run;
  - every create, collect, view, export, promote and delete is written to `audit_log`.
  Why: monitoring named public accounts is the core of the analyst use case (scam promoters, stock-tip channels, coordinated networks). The safeguards keep it to public, opt-in-respecting data, recorded with a reason and auditable.
- **Account key** = `platform:platform_id`, where platform equals the post's `source_type`. platform_id is the Bluesky DID, the full Mastodon `user@instance`, the YouTube channelId, the Reddit username, or the Telegram numeric channel id. These ids are stable, unlike handles, which change.
- **Roles never downgrade**: subject > member > top_author. The `top_author` links (the 25 most active authors) make the "People" view useful for events without profiling anyone beyond their posts in scope.
- **Delete-orphan rule**: deleting an investigation removes its links, runs, scoped coordination groups, narratives and alerts. It removes a post only if no other investigation links it **and** the investigation's collection created it (`meta.collected_for`). Posts from the global `socmint ingest` are never deleted. An account is removed when no other investigation links it and no remaining post was written by it.
- **Per-investigation scoping** of "search within" lists: an event's `channels.telegram` / `channels.reddit` override the global `sources.yaml` lists for that run only. Real channels and subreddits are added only with approval.
- **Scoped analysis**: narratives, coordination and market run on the investigation's posts only. Scoped market runs do not write mention counts to shared `market_daily` rows, and their alerts get an `:inv{id}` dedup suffix.
- **Edges**: forward, reply, mention, shares_url, uses_hashtag from post fields; co_posts between members of a possible-coordination group (added by `analyze`). A **Bluesky quote post is treated as a forward edge** (it re-publishes another account's post with commentary, like a Telegram forward).

## Bluesky & Mastodon (M10)
- **Bluesky keyword search**: `public.api.bsky.app/.../searchPosts` returns HTTP 403 without auth (checked 2026-11-06). `api.bsky.app` answers the same call unauthenticated, but switching hosts to get past a 403 is working around a block, so we don't do it. Event search instead listens to the official public **Jetstream** firehose for a bounded time (60 s / 20,000 posts), matches terms locally and hydrates matches with `getPosts`. The trade-off is that only posts made during the listen are found. `search_mode: auto` still tries the public `searchPosts` first, so the source uses it again automatically if Bluesky reopens it.
- **Mastodon events are hashtag-only**: full-text search needs an authenticated account. Each term becomes a tag (`#PuneRains` → `PuneRains`, `Pune flood` → `Puneflood`), and the tag timeline is paginated back to `since`.
- Account lookups go to the account's **home instance** (the domain in `@user@instance`); a bare `@user` is looked up on the first configured instance. Every host goes through `PoliteClient` (robots.txt, rate limit).
- Term matching (`TermMatcher`): Latin terms on word boundaries, Devanagari as substrings (Hindi/Marathi inflect by suffix), `#tag` terms against hashtags.

## Collectors & GDELT (M11, M13)
- **GDELT fail-fast in investigations**: a single OR query per event with `retries=0` (`event_retries` in `sources.yaml`). The first live collect took 7 minutes, 3.5 of them spent on 429 back-off. A throttled GDELT now fails in seconds, and the other sources are unaffected. `PoliteClient.get` gained a per-call `retries` override for this.
- Reddit keyword search runs **only inside the configured subreddits** (`restrict_sr`); with none configured it returns nothing and says so. Telegram search runs only inside listed **public broadcast channels**; groups and private chats are never read.
- YouTube comments: `reply_to_author_id` is the video's channel, so comment threads become reply edges to the channel.

## Categoriser (M12)
- Rules first, always on and offline: keyword labels per category (en/hi/mr/Hinglish) plus signal labels (scam score band, fact-check match, brand impersonation, ticker + promotion language). Each label stores its evidence. `other` only when nothing fires.
- **hate_communal** ships `enabled: false`, `analyst_only: true`, with no keywords. Turning it on is an explicit config change.
- **Optional zero-shot** (`categorize.zero_shot: false` by default): `MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli`, ~107M parameters, ~430 MB, CPU-friendly and covering Hindi/Marathi via XNLI. It is only added at score ≥ 0.80, and degrades gracefully (reported in `degraded`) when the model or the `[ml]` extra is missing.
- Analyst overrides win over rule and zero-shot labels and are audited.

## Showcase (M14–M15)
- **Demo data goes through the real pipeline**: `socmint/scenario.py` provides a `FixtureSource` that stands in for the platform collectors, so `runner.collect → enrich → analyze → promote_group` run unchanged and offline. Synthetic rows are only ever written to `data/demo.db`, and every row has `synthetic=True` (tested).
- The demo event uses a real city (Pune, as the brief asks) but a **fictional dam ("Demosagar")** for the "dam has burst" rumour, so no synthetic text claims a real dam failed. All accounts, channels, UPI IDs and domains are invented (`.example` domains).
- One Streamlit page (`socmint/showcase/app.py`) with built-in charts and `st.graphviz_chart`; logic lives in `showcase/data.py` so it can be tested without Streamlit. It runs headless on 127.0.0.1:8502, next to the analyst dashboard on 8501. Views are audited once per investigation selection, not on every rerun.
- Exports from the dashboard are prepared on a button click (each export is audited), then offered as a zip download.
- Stock-tip fixture posts avoid buy/sell/hold wording, so a test can check that **no** showcase text contains it, quoted evidence included.

## Verification fixes (2026-11-06, see docs/VERIFICATION.md)
- **Global vs scoped runs**: since M13, narratives and coordination groups written by `investigate analyze` carry an `investigation_id`. The analyst dashboard and `GET /narratives` are global views, so they select the newest run with `investigation_id IS NULL` (`socmint/analyze/runs.py`). Alert lists (dashboard and `GET /alerts`) show the alert's investigation, so scoped alerts are not mistaken for global ones.
- **Investigation terms do not categorise**: every post an event collects contains its search terms, so a category keyword that is one of the investigation's own terms (or a word of a multi-word term, normalised as `TermMatcher` does) is not counted; it is kept in the label evidence as `matched_investigation_term`. Global and tipline categorisation are unchanged. Labels are stored per post, so a post collected by two investigations keeps the labels from the investigation that first enriched it. In the demo event `civic_safety` fell from 93% to 63% of posts; the rest still mention rescue, helplines, relief camps, heavy rain and the like.
- **Narrative keywords**: scikit-learn's English stopwords are merged with `config/stopwords.yaml` (which now also lists Hindi, Marathi and Hinglish fillers), tokens under 3 characters are dropped, and ties prefer words used as hashtags or capitalised (places, names).
- **No growth alerts without a baseline**: a scoped narratives run raises growth alerts only once the investigation has been collecting for `narratives.min_baseline_hours` (72 h, measured from its first collection run). Before that the narratives and their growth are stored and shown, and the result says why alerts were held back. This applies to the demo too: its event is collected in one go, so it shows the fastest-growing narrative but raises no narrative alert.
- **Hugging Face cache**: cached models (e5, zero-shot, Whisper) are loaded from the local snapshot path, so a run makes no Hub request; only a model that is not cached is downloaded. The log says which (`hf_model_from_cache` / `hf_model_download`).
- **Showcase start-up**: `showcase --demo` reuses `data/demo.db` when it has every model table and column and the three showcase investigations; `--rebuild` forces a build. Building takes about a minute with e5, reusing a few seconds.
- **Showcase readability**: posts-over-time buckets follow the span (hourly < 48 h, 6-hourly ≤ 7 days, daily beyond); the posts table leads with time, platform, author, text (140 chars) and categories within 1366 px; the post detail shows each label's evidence; source chips use friendly names (`Bluesky (demo) · 77 new`, feed `publisher`s).

## Live collection (M17–M20, 2026-11-07)
- **Parallel collection**: an investigation fetches its sources in a thread pool (`investigate.max_parallel_sources: 4`) and stores them one at a time in the calling thread, so SQLite sees one writer and the dashboard's progress callback stays on its own thread. `PoliteClient` keeps one lock per host (requests to one host are still spaced by its rate limit) and fetches robots.txt once per host.
- **GDELT throttling**: interactive calls use 10 s connect / 30 s read timeouts, retry once after a network error and never retry an HTTP 429. A 429 (or GDELT's plain-text "limit requests" answer) starts a 15-minute cooldown in `data/cache/cooldowns.json`; during it GDELT is skipped as `throttled` without a request. The event query starts at a stable hour, so a second collect within 30 minutes is served from the HTTP cache.
- **One set of status words** (`ok, partial, throttled, no_key, offline, opted_out, error`) everywhere; older rows are shown in the new words. `no_key` names the `.env` variable to set.
- **`socmint doctor`** is read-only: it prints set/missing for keys (never values), sends at most one robots.txt request per host (none for a host in cooldown) and reads `data/demo.db` without migrating it.
- **Bluesky logged-in search (optional)**: with `BLUESKY_HANDLE` + `BLUESKY_APP_PASSWORD`, event search uses Bluesky's documented logged-in route (createSession on bsky.social, searchPosts on the account's PDS with the `atproto-proxy` header). This is not a way around the public 403: it is the route Bluesky documents for authenticated clients, with a dedicated project account and an app password. When the keys are set and the logged-in route fails (after one refresh and one new login), the run is an `error`; it does not quietly fall back to another route, so a broken account is visible. Without keys nothing changes. Opt-outs (`!no-unauthenticated`) are still dropped when logged in.
- **Proposed news feeds and publisher terms**: a feed is only proposed when it comes from the publisher's own RSS page, answers HTTP 200 to our User-Agent and robots.txt allows it. Feeds whose RSS terms say "personal use only" (Times Internet: TOI, Navbharat Times, Maharashtra Times) are not proposed, for the same reason Google News RSS is excluded. Dainik Bhaskar's feeds are proposed but disabled, with its site terms quoted in `terms_note` and SOURCES.md, because that call is yours. A 403 (Lokmat, PIB) is recorded, not bypassed.
- **Suggested terms** (events only, never added automatically): co-occurring hashtags, runs of up to three Capitalised words (names, places) and Devanagari words/bigrams, each counted once per post. A suggestion needs 3+ posts from 2+ accounts (an RSS publisher counts as one account). Excluded: the narratives stopwords, `suggest.generic_tags`, handles, URLs, numbers, anything under 3 characters, the event's own terms (also without `#` or spaces) and single words of its terms. A Devanagari suggestion is added under the language most of its posts carry (hi or mr; hi if unknown); others under en. "Add selected" calls the same validated, audited `runner.edit` as `investigate edit` (one audit row for the whole pick).
