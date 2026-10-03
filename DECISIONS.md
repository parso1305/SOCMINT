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
