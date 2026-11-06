# Task: verify every new feature (M9–M16) in the real app, then fix what is broken

You are working in `C:\Project\SOCMINT` (Windows 11, PowerShell, Python 3.12 venv in `.venv`). Milestones M9–M16 (investigations, Bluesky/Mastodon collectors, categoriser, scoped analysis, export, `socmint showcase`) are built and committed. The automated tests pass (211 tests, ruff clean), but those tests use fixtures. This task checks the **real app**, fixes the problems listed in section 3 and any new ones you find, and records the results.

**No new features in this task.** Only verification, bug fixes and polish of what exists. Keep every rule in `README.md` → "Legal & privacy notes" and `DECISIONS.md` → "Investigations: scope change".

## 0. Environment (do this first)
- `uv` is **not on PATH** on this machine (it lives at `%APPDATA%\Python\Python313\Scripts\uv.exe`). Use `python -m uv ...` or call `.\.venv\Scripts\socmint.exe` directly. Do not change the system PATH.
- `CONTACT_EMAIL` is set in `.env`. Never print it, log it, copy it into a file or commit it. `.env` is git-ignored; keep it that way.
- Run live tests against a **separate database** so the main `data/socmint.db` stays clean:
  ```powershell
  $env:SOCMINT_DB_URL = "sqlite:///C:/Project/SOCMINT/data/verify.db"
  ```
  Set it in **every** new terminal before running a live command. Never run `init-db`, `collect`, `analyze` or `delete` against `data/socmint.db`. Delete `data/verify.db` and anything you exported when you finish.
- Port 8502 is the showcase and 8501 the analyst dashboard. **Port 8000 is taken by Splunk (`splunkd.exe`) on this machine**, so start the API with `--port 8010` and don't stop Splunk. Stop any server you start.
- In Windows PowerShell 5.1, `curl` is an alias for `Invoke-WebRequest` and rejects `-X`/`-F`. Always type `curl.exe`.
- **Tesseract is not installed** here, so OCR is disabled with a warning (`socmint/understand/ocr.py`). That's expected. Record OCR-dependent results as "SKIPPED: Tesseract missing", not FAIL, and don't install software.
- `prompts/` and `.claude/` are untracked files of mine. Don't stage, edit or delete them. Stage files by explicit path (`git add <file> ...`), never `git add -A` or `git add .`.

## 1. Verification checklist
Create `docs/VERIFICATION.md` with one table row per check: **ID · what · command · expected · actual · PASS/FAIL · fix commit**. Run every check before you fix anything, so the report shows the "before" state. Then re-run each check after the fixes.

### A. Automated
| ID | Command | Expected |
|---|---|---|
| A1 | `python -m uv run pytest -q` | all pass |
| A2 | `python -m uv run ruff check socmint tests` | clean |

### B. Showcase demo (offline)
| ID | Check | Expected |
|---|---|---|
| B1 | `.\.venv\Scripts\socmint.exe showcase --demo`, time it | builds 3 investigations (event ≈288 posts, subject ≈59, group ≈16) and serves http://127.0.0.1:8502 |
| B2 | Header | 4 numbers + source chips, yellow DEMO banner |
| B3 | Overview | posts-over-time chart readable (not 1–4 posts per bar), key findings mention coordination 0.93, fact-check match, UPI/domain indicators, a narrative with **meaningful** keywords, a category that is **not** just the event theme |
| B4 | Posts | filters work; text and categories visible without horizontal scrolling at 1366 px width; post detail shows labels + evidence + "why in this investigation" |
| B5 | People & accounts | profile cards; subject shows per-day activity |
| B6 | Network | graph renders; coordination table; **Promote to Group** creates a new group investigation |
| B7 | Evidence & export | indicators, fact-checks, narratives, collection log; JSON and CSV zips download, and `manifest.json` hashes match the files |
| B8 | Sidebar → New investigation in demo mode | each source reports `offline`, nothing crashes, run is logged |
| B9 | Subject and Group investigations | every tab renders; DEMOPUMP market alert shows disclaimer, no buy/sell/hold wording |

### C. Analyst dashboard (original 7 pages)
| ID | Check | Expected |
|---|---|---|
| C1 | `.\.venv\Scripts\socmint.exe dashboard --demo`, open every page | no exceptions |
| C2 | Narratives page | shows the **global** run (13 narratives in the demo), not an investigation's scoped run |
| C3 | Coordination page | shows the **global** run (20 groups in the demo), not the group investigation's 3 |

### D. Live collection, no keys (internet needed; use `data/verify.db`)
```powershell
$X = ".\.venv\Scripts\socmint.exe"
& $X init-db
& $X investigate create --kind event --name "verify-event" --terms "#PuneRains,Pune flood,पुणे पूर" --purpose "verification"
& $X investigate collect "verify-event"
& $X investigate analyze "verify-event"
& $X investigate show "verify-event"
& $X investigate create --kind subject --name "verify-subject" --purpose "verification of public-account monitoring" --handle bluesky:bsky.app --handle mastodon:@Mastodon@mastodon.social
& $X investigate collect "verify-subject"
& $X investigate show "verify-subject"
& $X investigate list
& $X investigate export "verify-event" --format json
& $X investigate export "verify-event" --format csv
& $X investigate delete "verify-subject"
```
| ID | Expected |
|---|---|
| D1 | event collect finishes in under ~3 min; Mastodon, news RSS and Bluesky report `ok`; GDELT `ok` or a clean `error` (429); per-source lines printed |
| D2 | subject collect: Bluesky ≈50 posts, Mastodon ≥1, both profiles stored, under ~30 s |
| D3 | `show` prints sources, languages, categories, hashtags, domains, accounts |
| D4 | export folders contain posts/accounts/edges/indicators/summary/config + `manifest.json`; hashes verify |
| D5 | delete removes the subject's links and orphan posts/accounts; `audit_log` has create/collect/view/export/delete rows (query the DB to prove it) |
| D6 | no Hugging Face network call or "unauthenticated requests to the HF Hub" warning once the e5 model is already cached |
| D7 | `git status` shows **no** export files or DB files as untracked |

### E. Older features still work
| ID | Command | Expected |
|---|---|---|
| E1 | `& $X check "Your SBI account will be blocked today, update KYC at sbi-kyc.in"` | `likely_scam` with reasons |
| E2 | `& $X demo` | completes, prints summary |
| E3 | `& $X api --port 8010`, then `curl.exe http://127.0.0.1:8010/health` and `curl.exe -X POST http://127.0.0.1:8010/check -F "text=You won a lottery, pay fee to x@ybl"` | 200 and a verdict |
| E4 | `curl.exe -H "X-API-Key: <SOCMINT_API_KEY from .env>" http://127.0.0.1:8010/narratives`. Read the key from `.env` into a variable; don't print it | returns the **global** run, not a scoped one |

## 2. How to fix anything that fails
For every FAIL, in this order:
1. Reproduce it with the smallest command, and write down the exact error or wrong output.
2. Find the **root cause** in the code. Don't patch the symptom, and don't loosen a test to make it pass.
3. Write a failing test first (offline, fixtures only), then fix the code until it passes.
4. Run the full suite plus ruff.
5. Commit with `FIX: <what> (<check ID>)`, and fill in the `fix commit` column.
6. Re-run the original check and mark it PASS.

If a live endpoint misbehaves, don't work around it with scraping, other hosts or spoofing. Make the source fail cleanly, record it in `SOURCES.md`, and tell me.

## 3. Known problems found during review (fix these)

**P1. The analyst dashboard and the API show investigation-scoped runs instead of the global run** (C2, C3, E4)
- Cause: `socmint/dashboard/pages/4_Narratives.py:20`, `pages/5_Coordination.py:22` and `socmint/api/app.py:118` pick "the latest `run_id` by `created_at`". Since M13, scoped runs write rows with `investigation_id` set, so after any `investigate analyze` these views show that investigation's results. In `data/demo.db` the Coordination page shows the group investigation's 3 groups instead of the global 20.
- Fix: these global views must select only rows with `investigation_id IS NULL`. Look for the same pattern anywhere else. In alert lists (`dashboard/common.py:alerts_section`, `/alerts`), show which investigation an alert belongs to (`evidence.investigation_id`), so scoped alerts aren't mistaken for global ones.
- Test: after seeding the showcase scenario, the global coordination/narrative selectors return the global run's ids.

**P2. Categories echo the event's own search terms** (B3)
- Symptom: in the demo event, 268 of 288 posts (93%) are labelled `civic_safety`, so the category chart and the "most common category" finding say nothing. Live, the same happens for any flood event.
- Cause: `config/categories.yaml` → `civic_safety.disaster` contains `flood`, `पूर`, `बाढ़`, `dam`, `धरण`..., which are exactly the event's terms. Every collected post matches them because of how it was collected.
- Fix: when `enrich()` (`socmint/investigate/runner.py:315`) categorises posts for an investigation, pass the investigation's terms to `rule_labels()` (`socmint/analyze/categorize.py:87`). A keyword hit that is one of the investigation's own terms (after the same normalisation `TermMatcher` uses) must **not** count toward a category. Record it in the evidence as `matched_investigation_term` so it's still explainable. A post then gets `civic_safety` only if it has a civic-safety keyword beyond the event terms (helpline, evacuation, road closed, rescue...). Leave the global/tipline categorisation unchanged.
- Expected after the fix: the demo event still shows `scam`, `misinformation` and `brand_impersonation` posts, `civic_safety` is clearly below 93%, and the "most common category" finding is meaningful. Update the scenario test expectations to match.

**P3. Narrative keywords are filler words** (B3)
- Symptom: the top demo narrative is described as "going, getting, worse, burst".
- Cause: `config/stopwords.yaml` has a short English list (about 70 words).
- Fix: merge scikit-learn's `ENGLISH_STOP_WORDS` into the English list in code, add common Hindi/Marathi function words and Hinglish fillers (hai, ka, ki, ke, aur, nahi, hoga, raha...), and drop tokens shorter than 3 characters. Prefer hashtags and named words if c-TF-IDF ties.
- Test: the demo dam-rumour narrative's top keywords include `demosagar` or `dam` / `burst`, and none of `going`, `getting`, `worse`.

**P4. `socmint showcase --demo` rebuilds every time (about 2 min with the e5 model)**
- That's too slow to start in front of an investor.
- Fix: reuse `data/demo.db` if it exists and its schema is current. Add `--rebuild` to force a rebuild. Print how long the build took. Update `docs/DEMO.md` (build once before the meeting with `--rebuild`, then launch instantly).

**P5. Exports and the verify DB are not git-ignored** (D7)
- `data/exports/` isn't in `.gitignore`, so exported real posts could be committed. Add `data/exports/`. Check that `data/*.db` already covers `verify.db`.

**P6. The Hugging Face Hub is contacted on every live run** (D6)
- Every live collect and analyze warns "You are sending unauthenticated requests to the HF Hub". Once the e5 (and optional zero-shot) model is in the local cache, load it with `local_files_only=True` (or set `HF_HUB_OFFLINE=1` for the process). Fall back to downloading only when it isn't cached. Log which one happened.

**P7. Showcase Posts table: the important columns are off-screen** (B4)
- Order the columns: time, platform, author, **text (truncated to ~140 chars)**, categories, lang, link; put `id` last or hide it. Text and categories must be visible at 1366 px without horizontal scrolling.

**P8. Showcase posts-over-time chart is too fine-grained** (B3)
- The demo spreads 288 posts over 5 days in hourly bars of 1–4 posts each, which looks empty. Choose the bucket from the span: hourly if under 48 h, 6-hourly up to 7 days, daily beyond that. Label the bucket size on the chart.

**P9. Narrative growth alerts fire on a first collection** (D1)
- A live first collect + analyze created 14 narrative alerts from 16 clusters, because there is no baseline yet (the growth ratio is inflated, as noted in DECISIONS.md). For scoped runs, don't raise growth alerts until the investigation has at least `narratives.min_baseline_hours` (new threshold, default 72) of collected history. Still show the narratives.

**P10. Source chips show internal names**
- `demo_bluesky: ok (77 new)` → show `Bluesky (demo) · 77 new`, and give the same friendly names to live sources (`indianexpress_india` → `Indian Express`, using the `publisher` already in `sources.yaml`).

## 4. Do NOT do in this task (ask me first)
- Switching Bluesky keyword search to `api.bsky.app` or an authenticated app password. Today event search on Bluesky only finds posts made during the 60-second Jetstream listen (often 0–1 posts). That's a decision I'll make separately.
- Adding real Telegram channels, subreddits, YouTube channels or accounts to any config.
- New data sources, login/auth, Postgres, Docker.

## 5. Done means
1. Every check in `docs/VERIFICATION.md` is PASS, or has a clear reason it can't pass yet (e.g. GDELT throttling the IP), with the before and after results.
2. P1–P10 fixed, each with a regression test and its own `FIX:` commit.
3. `pytest` green and ruff clean.
4. README, DEMO.md, DECISIONS.md and SOURCES.md updated where behaviour changed.
5. `data/verify.db` and test exports deleted. `git status` shows nothing except my untracked `prompts/` and `.claude/`, and `data/socmint.db` is unchanged.
6. A short final report: what failed before, what you fixed, what still fails, and the exact commands to run the demo.
