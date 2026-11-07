# Verification of M9–M16 in the real app (2026-11-06)

Every check was run once **before** any fix (the "before" state), then again **after** all fixes. Live checks used a
separate `data/verify.db` (`SOCMINT_DB_URL=sqlite:///C:/Project/SOCMINT/data/verify.db`). One slip: for E3 the API was
first started without `SOCMINT_DB_URL`, and its start-up `init_db()` migrated the main DB's schema into
`data/socmint.db-wal` (7 new empty tables and new nullable columns; no rows changed) before it was stopped. E3/E4 were
then run against a copy of `data/demo.db`. The WAL, which did not exist before this session, was removed at the end, so
`data/socmint.db` is back to its original state (SHA-256 `9126e931…` before and after, no WAL). Browser checks drove headless Chrome over the DevTools protocol at
1366 × 900 (screenshots, page text, clicks, downloads); form and filter checks used Streamlit's AppTest on a copy of
`data/demo.db`. Environment: Windows 11, Python 3.12 venv with the `[ml]` extra (e5 embedder), Tesseract not installed.

`$X` = `.\.venv\Scripts\socmint.exe`.

## A. Automated

| ID | What | Command | Expected | Actual | PASS/FAIL | Fix commit |
|---|---|---|---|---|---|---|
| A1 | Test suite | `python -m uv run pytest -q` | all pass | Before: 211 passed. After: 235 passed (24 new regression tests) | PASS → PASS | — |
| A2 | Lint | `python -m uv run ruff check socmint tests` | clean | Before and after: All checks passed | PASS → PASS | — |

## B. Showcase demo (offline)

| ID | What | Command | Expected | Actual | PASS/FAIL | Fix commit |
|---|---|---|---|---|---|---|
| B1 | Build + serve | `$X showcase --demo` (timed) | 3 investigations (event ≈288, subject ≈59, group ≈16), serves :8502 | Before: 288 / 59 / 16 posts, healthy after 46 s, and every start rebuilt the DB (P4). After: `--rebuild` builds in 59 s and prints the duration; `--demo` reuses a current DB and was healthy after ~10 s | PASS → PASS (P4 fixed) | fcac487 |
| B2 | Header | browser | 4 numbers + source chips + DEMO banner | Before: 288 posts · 185 accounts · 76 flagged · 6 sources, banner shown, chips used internal names (`demo_bluesky: ok (77 new)`). After: chips read `Bluesky (demo) · 77 new` | PASS → PASS (P10 fixed) | f9edf6f |
| B3 | Overview | browser | readable chart; findings: coordination 0.93, fact-check, UPI/domain, meaningful narrative, category not just the theme | Before: hourly bars of 1–4 posts; narrative "going, getting, worse, burst"; "Most common category: civic safety (268 of 288)" (93%, the event's own terms). After: posts per 6 hours (labelled); narrative "burst, demosagar, dam, flood"; civic safety 181 of 288 (63%), each label backed by a keyword beyond the event terms; coordination 0.93, fact-check (34 posts), UPI + domain findings unchanged | FAIL → PASS | 3f9823b (P2), 8219bd1 (P3), d581b0e (P8), b06ee9b |
| B4 | Posts | browser + AppTest | filters work; text + categories visible at 1366 px; detail shows labels + evidence + why | Before: filters OK (scam → 18, + mastodon → 4, + "okdemo" → 4); table led with `id`, `text` was the last column and cut off at 1366 px; detail showed labels without their evidence. After: columns time · platform · author · text (140 chars) · categories · lang · link, grid 884 px = 884 px visible (no horizontal scroll); detail shows `Evidence:` per label (keywords, signals, terms not counted) and the "why" JSON expanded | FAIL → PASS | 4711add (P7) |
| B5 | People & accounts | browser | profile cards; subject per-day activity | Before and after: profile cards for both fictional subject channels, activity per day (quiet month, burst today), top-authors table, group members with `added by coordination` | PASS → PASS | — |
| B6 | Network + promote | browser + AppTest | graph renders; coordination table; Promote to Group creates a group | Before and after: neato graph with the red 8-account clique, groups table (top 0.93); promoting the top group with a purpose created a 4th investigation and switched to it, no exception | PASS → PASS | — |
| B7 | Evidence & export | browser (Chrome download) | indicators, fact-checks, narratives, log; JSON + CSV zips download, manifest hashes match | Before and after: all sections render; both zips downloaded; all 6 files per zip match `manifest.json` SHA-256. (Before, the export folders appeared as untracked files in git: see D7.) | PASS → PASS | — |
| B8 | New investigation in demo mode | AppTest (sidebar form) | each source `offline`, no crash, run logged | Before and after: 10 collection runs (Bluesky, Mastodon, 7 news feeds, GDELT) all `offline`, status "Collected 0 new posts", `investigation_create` / `investigation_collect` in `audit_log` | PASS → PASS | — |
| B9 | Subject + group | browser | every tab renders; DEMOPUMP alert with disclaimer; no buy/sell/hold | Before and after: no exceptions on any tab; "Manipulation-risk alert: DEMOPUMP … risk score 100" with "Risk indicator for analysts only. This is not investment advice…"; no `buy`/`sell`/`hold` in any page text | PASS → PASS | — |

## C. Analyst dashboard

| ID | What | Command | Expected | Actual | PASS/FAIL | Fix commit |
|---|---|---|---|---|---|---|
| C1 | All 7 pages + home | `$X dashboard --demo`, open each page | no exceptions | Before and after: 8 pages, 0 exceptions | PASS → PASS | — |
| C2 | Narratives page | browser | global run (13 narratives) | Before: showed the event investigation's scoped run `…-c2e297` (28 narratives). After: "Latest global run: `…-0b5468`" = the 13-narrative global run | FAIL → PASS | 2830b74 (P1) |
| C3 | Coordination page | browser | global run (20 groups) | Before: showed the group investigation's 3 groups. After: "Latest global run: `…-ef4c74`" = the 20-group global run; alerts table has an `investigation` column | FAIL → PASS | 2830b74 (P1) |

## D. Live collection, no keys (`data/verify.db`)

Commands as in the task (`verify-event` with `#PuneRains,Pune flood,पुणे पूर`; `verify-subject` with `bluesky:bsky.app`
and `mastodon:@Mastodon@mastodon.social`). Because no Pune flood is happening, `verify-event` found 0 posts, so an extra
event `verify-broad` (`flood,बाढ़,#India`) was collected the same way to exercise categories, narratives and exports
with real data.

| ID | What | Command | Expected | Actual | PASS/FAIL | Fix commit |
|---|---|---|---|---|---|---|
| D1 | Event collect | `investigate collect "verify-event"` | < ~3 min; Mastodon, news, Bluesky `ok`; GDELT `ok` or clean 429; per-source lines | Before and after: 114 s; Bluesky `ok` (searchPosts 403 → 60 s Jetstream listen), Mastodon `ok`, 7 news feeds `ok`, GDELT `error` HTTP 429 in seconds; one line per source. Before: `verify-broad` analyze raised 3 narrative growth alerts on its first collection (P9). After: 0 alerts, "growth alerts held back: 0 h of collected history, a baseline needs 72 h"; the module's own note ("not enough items in window") is kept | PASS → PASS (P9 fixed) | 1210f20 (P9), c147754 |
| D2 | Subject collect | `investigate collect "verify-subject"` | Bluesky ≈50, Mastodon ≥1, both profiles, < ~30 s | Before: 51 + 3 posts, both profiles, 12 s. After: 51 + 3 posts, both profiles (35.5 M and 879 k followers), 11 s | PASS → PASS | — |
| D3 | Show | `investigate show` | sources, languages, categories, hashtags, domains, accounts | Before and after: all printed. `verify-broad` before: civic_safety 28 of 64 posts, partly because "flood" was a search term; after: 27 of 67, each backed by a keyword beyond the terms | PASS → PASS | — |
| D4 | Export | `investigate export … --format json/csv` | posts/accounts/edges/indicators/summary/config + manifest; hashes verify | Before and after: 4 folders, 7 files each, all SHA-256 match | PASS → PASS | — |
| D5 | Delete + audit | `investigate delete "verify-subject"` (answers the y/N prompt) + DB query | links and orphan posts/accounts removed; audit has create/collect/view/export/delete | Before and after: removed 54 links, 54 orphan posts, 2 accounts, 98 edges, 2 runs; `bsky.app` account gone; `audit_log`: create 3, collect 3, analyze 2, view 3, export 4, delete 1 | PASS → PASS | — |
| D6 | No Hugging Face call once cached | collect/analyze/check output | no HF request or warning | Before: "You are sending unauthenticated requests to the HF Hub" on every collect, analyze and `check`. After: log says `hf_model_from_cache`, no warning; `investigate analyze` still succeeds with `HTTPS_PROXY` pointed at a dead port (so no network was needed) | FAIL → PASS | 6862328 (P6) |
| D7 | Nothing committable | `git status` | no export or DB files untracked | Before: `data/exports/` untracked. After: only `.claude/` and `prompts/`; a test checks exports, DBs and `.env` are ignored | FAIL → PASS | f268fab (P5) |

## E. Older features

| ID | What | Command | Expected | Actual | PASS/FAIL | Fix commit |
|---|---|---|---|---|---|---|
| E1 | Tipline check | `$X check "Your SBI account will be blocked today, update KYC at sbi-kyc.in"` | `likely_scam` with reasons | Before and after: likely_scam, score 100 (KYC/block, lookalike domain, unofficial brand contact, link request, new domain). Before it also printed the HF Hub warning (D6) | PASS → PASS | — |
| E2 | Offline demo | `$X demo` | completes, prints summary | Before: 20 s; after: 27 s; fixture expectations met, all modules ok | PASS → PASS | — |
| E3 | API health + check | `$X api --port 8010`; `curl.exe …/health`; `curl.exe -X POST …/check -F "text=You won a lottery, pay fee to x@ybl"` | 200 and a verdict | Before and after: 200; verdict `suspicious` (lottery/prize). Run against a copy of the demo DB so `/check` did not write to the main DB | PASS → PASS | — |
| E4 | API narratives | `curl.exe -H "X-API-Key: …" …/narratives` | global run | Before: 28 narratives (the event's scoped run). After: 13 (the global run); `/alerts` rows carry `investigation_id` | FAIL → PASS | 2830b74 (P1) |

## Other findings

- **Fixed (b06ee9b)**: the Marathi protest keyword `संप` (strike) matched inside `संपर्क` (contact), so helpline posts got
  a protest sub-label. It is now replaced by strike phrases (`संप सुरू`, `संपावर`, `संप पुकारला`, `संपात`).
- **Known, not fixed**: other short Devanagari stems can match inside unrelated words (`पूर`, flood, inside `पूर्ण`,
  complete), because Devanagari keywords match as substrings so stems catch inflections. Noted in README. *(Fixed
  later in M20.1: Devanagari keywords now match at the start of a word; see Prompt 3 below.)*
- **By design**: `investigate delete` asks for confirmation (use `--yes` in scripts). Exports and the People tab list the
  top 25 authors (`investigate.top_authors`), while the header counts every author (185 in the demo event).
- **Not fixable here**: GDELT keeps answering HTTP 429 to this IP (fails cleanly, see SOURCES.md). Bluesky keyword
  search is limited to the 60 s Jetstream listen until you decide on `api.bsky.app` or an app password.
- **Skipped**: OCR-dependent checks. SKIPPED: Tesseract missing (expected on this machine).

## Prompt 3 (2026-11-07): live collection, M17–M20

Each check was run on branch `feat/live-collection`: **before** the changes (at `94ca9a8`), then **after** them. Live checks
used a separate database (`$env:SOCMINT_DB_URL = "sqlite:///C:/Project/SOCMINT/data/verify.db"`), which was created fresh
for L1 and deleted at the end. `data/socmint.db` was not touched (SHA-256 `9126e931…` before and after). `$X` =
`.\.venv\Scripts\socmint.exe`.

Run notes:
- `uv` is not on PATH on this machine, so A1/A2 used the venv directly
  (`.venv\Scripts\python.exe -m pytest -q`, `… -m ruff check socmint tests`). `python -m uv run` would also work, but it
  can re-sync the venv in the middle of a check.
- Keys: only `CONTACT_EMAIL` is set in `.env` (it goes into the User-Agent). No optional key is set, so L3 and L4 are
  SKIPPED.
- Ctrl+C (L5): this tool's shell starts processes with Ctrl+C ignored, so the watch was started through a small wrapper
  that turns Ctrl+C handling back on, and the Ctrl+C was then sent to its console. A normal terminal needs no wrapper.

| ID | Check | Command | Expected after | Before (`94ca9a8`) | After | Result | Commit |
|---|---|---|---|---|---|---|---|
| A1 | Test suite | `.venv\Scripts\python.exe -m pytest -q` | all pass | 235 passed | 370 passed (135 new tests) | PASS → PASS | — |
| A2 | Lint | `.venv\Scripts\python.exe -m ruff check socmint tests` | clean | All checks passed | All checks passed | PASS → PASS | — |
| L1 | Event, no keys, fresh `verify.db` | `$X investigate create --kind event --name "delhi-air" --terms "Delhi pollution,Delhi AQI,#DelhiPollution" --purpose "verification"`, then `collect`, `analyze`, `show` | ≤ ~75 s; posts ≥ before; GDELT `ok` or `throttled` with a next-try time; most posts `environment`, not `other`; suggested terms printed | 11 posts (Mastodon 8, news 3) in 156.3 s. Bluesky 0 (search HTTP 403, then a 60 s Jetstream listen, 0 matched). GDELT `error` HTTP 429. Categories: other 10, misinformation 1. No suggested terms | 36 posts (GDELT 26, Mastodon 10; Bluesky scanned 1,733 posts in its 60 s listen, 0 matched; no news feed carried a matching item at the time). The first run took 116.9 s: the sources finished in 63 s in parallel, then enrichment (with the cold model load) waited for the slowest source. Fixed (44277e7), and a rerun on a fresh DB took **65.2 s**. GDELT `ok`: 26 articles in 21.0 s (0.0 s from the cache in the rerun). Suggested terms: Diwali (8 posts, 6 accounts), दिल्ली (4, 4), प्रदूषण (3, 3), Stage (3, 2). Categories: **environment 17, other 18**, civic_safety 2 | **FAIL** on categories only (time, count, GDELT and suggestions PASS); see "L1 categories" below | fed2bcc, 72225e2, bb9cc07, 8089f86, 44277e7 |
| L1b | Hindi terms | `$X investigate edit "delhi-air" --add-terms "दिल्ली प्रदूषण,वायु प्रदूषण" --lang hi`, then `collect` | terms stored under `hi`; edit in `audit_log`; Hindi posts added if any exist | n/a (no `edit` command) | Terms stored under `hi`. An `investigation_edit` audit row holds the before and after terms. Collect took 63.7 s: Mastodon searched `#दिल्लीप्रदूषण`, `#वायुप्रदूषण`, and GDELT queried both Hindi phrases (23.2 s). 0 new posts. (This run searched the new terms only since the last collect, see L6. The L6 rerun after the fix searched every term over the whole window.) | PASS | a4b4fca |
| L2 | Collect again within 30 min | `$X investigate collect "delhi-air"` | GDELT from the cache or skipped in a cooldown; no new 429 | 74.8 s. GDELT was requested again: `error` "Server disconnected without sending a response" (no cache, no cooldown) | 63.6 s. GDELT answered from the HTTP cache (0.0 s, 26 fetched, 0 new): no request, no 429 | FAIL → PASS | 72225e2 |
| L3 | Bluesky logged-in search | same event with `BLUESKY_HANDLE` + `BLUESKY_APP_PASSWORD` | Bluesky `ok` via logged-in search, posts > 0, no token anywhere | SKIPPED (no key) | SKIPPED (no key): neither variable is set. Offline tests cover login, session reuse, refresh then one new login, and that no token reaches logs, error messages, `collection_runs` or the HTTP cache | SKIPPED | 49884ee, c98d217 |
| L4 | YouTube / fact-check keys | a new event | YouTube posts or fact-check matches > 0, quota respected | SKIPPED (no key) | SKIPPED (no key). `investigate create` lists both as `skipped (no_key: set YOUTUBE_API_KEY)` / `…GOOGLE_FACTCHECK_API_KEY` | SKIPPED | a4b4fca |
| L5 | Watch | `$X investigate watch "delhi-air" --every 5m --for 12m --analyze`, then the same with `--for 6m` and Ctrl+C | 2–3 cycles; Jetstream kept open between cycles; Ctrl+C stops cleanly; audit rows per cycle | n/a (no `watch` command) | 3 cycles (08:55, 08:59, 09:04 UTC), then "watch ended (finished): 3 cycles". Bluesky ran as a live listen on **one** Jetstream connection for the whole watch: 9, then 8,899, then 11,899 posts scanned in total, 0 matched. Audit: `watch_start`, then collect + analyze for each cycle, then `watch_stop` (cycles=3, reason=finished). Ctrl+C 2.7 s into the second watch printed "Ctrl+C: stopping after the current source…". Cycle 1 finished its sources, then "watch ended (stopped): 1 cycles"; audit reason=stopped; exit code 0 | PASS | d90b7f0, ac5f1fd |
| L6 | Add one suggested term, collect again | `$X investigate edit "delhi-air" --add-terms "Diwali" --lang en` (the top suggestion; `show` prints this command), then `collect` | more posts; audit shows the edit | n/a (no suggestions, no `edit`) | First run: `edit` printed the before and after terms and wrote an `investigation_edit` audit row; Diwali left the suggestions. Collect: 36 → 37 posts (+1 Bluesky post from its listen) in 102.8 s. The only new post arrived at the end of the 60 s listen, so its enrichment, with the cold model load, could not overlap the fetching. But Mastodon and the news feeds searched "Diwali" only since the previous collect, minutes earlier, so an added term never reached its earlier posts. Fixed (64fb522). Rerun after the fix (Diwali removed and added again, two more audit rows): the next collect searched every term over the whole window. 37 → **265 posts** (+228: GDELT 190, Mastodon 23, five news feeds 15) in 65.3 s, GDELT `ok` again after its cooldown. "Diwali" is broad, so most new posts are `other` (216 of 265): a suggestion is for the analyst to judge | PASS (after a fix) | 8089f86, a4b4fca, 64fb522 |
| L7 | Pre-demo check | `$X doctor` | every line PASS/WARN/FAIL with a fix; no secret values printed | n/a (no `doctor`) | 13.5 s: 19 PASS · 9 WARN · 0 FAIL. Each WARN carries its fix: 5 optional keys missing (with what each would add), Tesseract missing, port 8000 used by `splunkd.exe`, demo data 17 h old (rebuild), `uv` not on PATH. Keys are shown as `set` or `missing` only | PASS | 542fad4 |
| S1 | Showcase demo | `$X showcase --rebuild`, then `$X showcase --demo` | demo event shows rated growth from synthetic history; chips show totals; banner shows build time; group chips no longer "0 new" | "growth x20.0" from a single collection (unrated). Chips "Bluesky (demo) · 77 new"; the group's chips "· 0 new" (16 posts). No build time | `--rebuild` built the demo in 60 s (288 / 59 / 16 posts), healthy after 62 s. `--demo` printed "Using existing data/demo.db", healthy after 2 s, no stale warning. The event's growth is rated from backdated synthetic runs (every 6 h over 5 days): "Fastest-growing narrative: burst, demosagar, dam, flood (10 posts in the last 24 h, growth x20.0)". Chips read "Bluesky (demo) · 77 posts"; group chips 6 / 6 / 4 posts. Banner: "Synthetic data built 07 Nov 2026 … UTC". Both servers were stopped | FAIL → PASS | 624f268, 45fb5d1, 9894f85, 0e06700 |
| S2 | "New investigation" form, live, `verify.db` | AppTest of the showcase sidebar form on `verify.db` | sources listed with key status; progress per source; suggested terms can be added | No key status and no source choice; no suggestions | Checkboxes: bluesky, mastodon, news_rss, gdelt ticked. youtube, google_factcheck, reddit and telegram are greyed out with "needs X in .env". factcheck_rss is offered, unticked. Collect took 63 s with one status line per source; `gdelt_india` showed **throttled** (a live HTTP 429 started a 15-minute cooldown). "s2-delhi-air" was created with 7 posts. Picking "#delhipollution · 7 posts · 2 accounts" gave "Added #delhipollution (audited)" | PASS | 33bb2b5, a4b4fca, 8089f86 |
| R1 | Subject regression | `$X investigate create --kind subject --name "verify-subject-after" --accounts "bluesky:bsky.app,mastodon:@Mastodon@mastodon.social" --purpose "verification"`, then `collect`, `show` | as before (≈50 + ≥1 posts, under ~30 s) | 52 + 3 posts, both profiles, 12.8 s | 52 + 3 posts, both profiles (35.6 M and 879 k followers), **7.6 s** (sources in parallel) | PASS → PASS | fed2bcc |
| R2 | Offline regression | `$X showcase --demo`: every tab of all 3 investigations; `$X dashboard --demo`: every page (Streamlit AppTest) | no exceptions; prompt-2 results unchanged | 0 exceptions | 0 exceptions (3 × 5 tabs, 8 dashboard pages). Prompt-2 results unchanged: coordination score 0.93, 34 posts matching the fact-check, UPI + domain indicators, civic safety 181 of 288 backed by keywords beyond the event terms. The demo event now also raises 6 "Debunked narrative resurfacing fast" alerts, because its growth is rated (by design, see S1) | PASS → PASS | — |

### L1 categories: a trade-off for you to decide

18 of the 36 Delhi posts are `other`. For most of them the only air-quality keyword ("aqi", "pollution") is a word
of the event's own terms ("Delhi AQI", "Delhi pollution"). Prompt 2 introduced a rule (P3) that does not count such a
keyword, so the flood demo does not label every post "civic safety" just because "flood" and "dam" are its search
terms. Counting the keyword when it appears outside the matched term phrase gives about 27 of 36 `environment` for
Delhi. But that variant, like the other narrower one I tried (ignore only the term that matched the post), counts
"flood"/"dam" again in the demo and breaks the prompt-2 test `test_event_categories_are_not_just_the_event_theme`. I
kept the prompt-2 rule. If you prefer the Delhi result, the "outside the matched phrase" variant is a small change,
and the demo's civic-safety share rises again.

### Found during verification and fixed

- L1 timing: enrichment started only after the slowest source (the 60 s Bluesky listen). Each source's posts are now
  enriched as soon as they are stored (44277e7), so L1 went from 116.9 s to 65.2 s.
- L6: a term added by `edit` or "Add selected" was searched only since the last collect, so its earlier posts were never
  found. The next collect after such an edit now searches the whole window (64fb522).
- Final self-review: terms added during a watch now reach its Jetstream connection (ac5f1fd). The "investigation's own
  term" check now uses the Devanagari word-start rule (1a3763c). The `bluesky_auth` docstring was corrected (c98d217).

### Still failing or limited (not fixable here)

- Bluesky public search answers HTTP 403. Without an app password an event gets only posts made during the 60 s listen
  (or during a watch). With `BLUESKY_HANDLE` + `BLUESKY_APP_PASSWORD` it searches (L3, not run).
- GDELT still answers HTTP 429 at times (once during S2 today). It is now reported as `throttled` with the next-try time,
  and no request is sent until then.
- PIB and Lokmat RSS answer HTTP 403 (not bypassed). The proposed Dainik Bhaskar feeds stay disabled until you approve
  them. There is still no Marathi news feed beyond BBC Marathi. See SOURCES.md.
- OCR: SKIPPED, because Tesseract is not installed on this machine.
