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
