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
