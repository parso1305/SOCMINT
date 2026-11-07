# SOCMINT-India showcase: 5-minute demo

Everything in demo mode is **synthetic**: invented people, handles, channels, UPI IDs, domains (`.example`) and stock
symbols, plus a fictional dam ("Demosagar"). It runs offline after installation and needs no keys.

## Start

```bash
uv sync --extra dev                    # once
uv run socmint showcase --rebuild      # before the meeting: builds data/demo.db (about 45 s with the e5 model)
uv run socmint showcase --demo         # in the meeting: reuses data/demo.db and starts in a few seconds
# open http://127.0.0.1:8502
```

`--demo` reuses `data/demo.db` when it exists, has the current schema and holds the three showcase investigations;
otherwise it builds it first. `--rebuild` always builds it from scratch and prints how long that took. The build runs
the v0 demo (tipline, scams, narratives, market) and then three investigations through the real collect → categorise
→ analyse → promote pipeline, with fixture sources standing in for the platforms. The main database
(`data/socmint.db`) is never touched. (`socmint demo` also rebuilds `data/demo.db`, without the investigations, so the
next `showcase --demo` builds again.)

The yellow **DEMO DATA (synthetic)** banner is shown on every investigation.

## Walkthrough (about 5 minutes)

**1. Event: "Pune floods 2026 (demo)"** (selected by default)
- *Header*: about 290 posts from six sources (Bluesky, Mastodon, Telegram, Reddit, YouTube, news). The chips show each
  source's last collection status (`Bluesky (demo) · 77 new`).
- *Overview*: the posts-per-6-hours chart peaks about 2½ days ago, when the rain was heaviest. **Key findings** lists,
  in order: the possible-coordination group, the fact-check match, the scam indicators, the fastest-growing narrative
  ("burst, demosagar, dam, flood") and the most common category. Categories ignore the event's own search terms, so
  "civic safety" counts only posts that mention rescue, helplines, relief camps, heavy rain and so on. Language chart:
  en / hi / mr / Hinglish.
- *Posts*: filter Category = `scam`. These are the fake relief appeals using UPI `punerelief.help@okdemo` and the
  lookalike domain `demobank-relief.example`. Pick one under **Post detail** to show its labels with evidence, *why it
  is in this investigation* (matched terms and places), and the extracted entities.
  Then filter Category = `misinformation` for the old-video "Demosagar dam has burst" rumour, which matches a
  synthetic fact-check. Each label shows its evidence; search terms that were not counted are listed as such.
- *People & accounts*: the most active authors, with public profile fields only.
- *Network*: red lines join the **8 accounts that posted near-identical text within 6 minutes** across three
  platforms. The table below lists the possible-coordination groups; the top one scores about 0.93.
  **Promote to Group** turns a group into its own investigation (the demo has already done this once; see 3).
- *Evidence & export*: suspected indicators, fact-check matches, narratives, the collection log, and **Prepare JSON /
  CSV export**. There is no narrative growth *alert*: investigations raise those only after 72 h of collecting (a
  first collection has no baseline), and the demo event is collected in one go. The download is a zip of posts, accounts, edges, indicators, the summary and the config, with a
  SHA-256 `manifest.json`.

**2. Subject: "Demo: stock-tip channel"** (pick it in the sidebar)
- The purpose is shown under the title. Subjects always carry a recorded purpose.
- *People & accounts*: profile cards for the fictional Telegram channel `multibagger_tips_demo` and its linked YouTube
  channel, plus a per-day activity chart: a quiet month, then a burst today.
- *Evidence & export*: **Manipulation-risk alert: DEMOPUMP**. Mentions in this channel spiked on the same day as an
  11× jump in (synthetic) traded volume. The alert lists its risk factors and carries the disclaimer; it is a risk
  indicator, never investment advice.

**3. Group: "Demo: dam-rumour copy network"**
- Created by promoting the event's top coordination group. The 8 members are shown with role `member` and
  `added by coordination`; the collection pulled each member's other public posts.
- *Network*: the co-posting clique.

**Create one live** (sidebar → *New investigation*): in demo mode the app is offline, so every source reports
`offline`. That shows that a failing source never stops the others and that every run is logged. For live
collection use the main database (below).

## Live mode

```bash
uv run socmint init-db               # once: creates data/socmint.db and .env
# put your e-mail in CONTACT_EMAIL in .env (it goes in the User-Agent sent to every source)
uv run socmint showcase              # http://127.0.0.1:8502 on the main DB
```

Or from the CLI:

```bash
uv run socmint investigate create --kind event --name "test" --terms "flood,बाढ़" --purpose "demo"
uv run socmint investigate collect "test"
uv run socmint investigate analyze "test"
uv run socmint investigate show "test"

uv run socmint investigate create --kind subject --name "Platform accounts" --purpose "demo of public-account monitoring" \
    --handle bluesky:bsky.app --handle mastodon:@Mastodon@mastodon.social
uv run socmint investigate collect "Platform accounts"
```

What to expect live (as of 2026-11-06):

| Source | Event (keywords) | Subject (accounts) |
|---|---|---|
| Mastodon | Hashtag timelines on mastodon.social. Accounts that opted out of indexing are skipped (often more than half). | Works |
| Bluesky | Public search is closed (HTTP 403), so the source listens to the Jetstream firehose for 60 s and only finds posts made in that minute. Often 0 for specific terms. | Works |
| News RSS | Configured Indian feeds, filtered by keyword | n/a |
| GDELT | One query; currently fails cleanly with HTTP 429 on throttled IPs | n/a |
| YouTube / Reddit / Telegram | Need keys in `.env`; otherwise `no_key` | Need keys |

A collect takes from about 20 s (subject) to 1–2 minutes (event, mostly the Bluesky listen). Then run *analyze*
(CLI), or simply reopen the investigation in the showcase.

## Guardrails to point out
- A purpose is required for every investigation and is stored with it.
- Only public posts and public profile fields from official APIs. No identity resolution across platforms. Opt-outs
  are respected.
- Every create, collect, view, export and delete is in `audit_log`.
- `investigate delete` removes the investigation's links, plus any posts and accounts no other investigation uses.
