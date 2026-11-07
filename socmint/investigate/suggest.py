"""Suggested terms for an event (M19.1): ways to widen collection that the analyst can accept, never added
automatically.

Candidates come from the event's collected posts: co-occurring hashtags, frequent capitalised names and places
(runs of up to three Capitalised words), and frequent Devanagari words or bigrams. A candidate needs at least
`suggest.min_posts` posts (3) from at least `suggest.min_accounts` accounts (2). Excluded: the narratives stopword
list, generic tags (`suggest.generic_tags` in thresholds.yaml), handles, URLs, numbers, anything shorter than three
characters, the event's own terms and single words of its multi-word terms. Accepting suggestions goes through
`runner.edit` (validated and audited like `investigate edit`).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from socmint.core.settings import threshold
from socmint.investigate.config import InvestigationConfig
from socmint.understand.normalize import match_text

_STRIP = re.compile(r"https?://\S+|www\.\S+|@[\w.]+")
_TAG = re.compile(r"#([\wऀ-ॣ०-ॿ]+)")
_NAME = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2}\b")
_DEVA_WORD = re.compile(r"[ऀ-ॣ०-ॿ]+")  # Devanagari letters/marks, without the danda (U+0964/U+0965)
_DEVA = re.compile(r"[ऀ-ॿ]")
_KIND_ORDER = {"hashtag": 0, "name": 1, "devanagari": 2}


def _stop() -> frozenset[str]:
    from socmint.analyze.narratives import _stopwords

    return _stopwords()


def _generic() -> set[str]:
    tags = threshold("suggest.generic_tags", ["#india", "#news", "#breaking"]) or []
    return {"#" + str(t).lstrip("#").casefold() for t in tags}


def _existing(cfg: InvestigationConfig) -> tuple[set[str], set[str]]:
    """(whole terms in several spellings, single words of the terms), all casefolded."""
    full: set[str] = set()
    words: set[str] = set()
    for t in (t for v in cfg.terms.values() for t in v):
        core = t.strip().lstrip("#").casefold()
        full |= {core, core.replace(" ", "")}
        words |= {w.lstrip("#") for w in core.split()}
    return full, words


def _account(p: dict[str, Any]) -> tuple[str, str]:
    return p.get("platform") or "", p.get("author_id") or p.get("author_handle") or p.get("channel") or p["source"]


def _is_digits(s: str) -> bool:
    return s.replace(" ", "").isdigit()


def _candidates(p: dict[str, Any], stop: frozenset[str]) -> set[tuple[str, str, str]]:
    """(kind, key, surface form) for one post; each candidate counted once per post."""
    out: set[tuple[str, str, str]] = set()
    text = _STRIP.sub(" ", p.get("text") or "")
    for tag in [*(h.lstrip("#") for h in p.get("hashtags") or []), *_TAG.findall(text)]:
        out.add(("hashtag", tag.casefold(), "#" + tag.casefold()))
    for m in _NAME.finditer(_TAG.sub(" ", text)):
        ws = m.group(0).split()
        while ws and match_text(ws[0]) in stop:
            ws = ws[1:]
        while ws and match_text(ws[-1]) in stop:
            ws = ws[:-1]
        if ws:
            name = " ".join(ws)
            out.add(("name", name.casefold(), name))
    deva = _DEVA_WORD.findall(text)
    for i, w in enumerate(deva):
        if match_text(w) not in stop:
            out.add(("devanagari", w, w))
            if i + 1 < len(deva) and match_text(deva[i + 1]) not in stop:
                bigram = f"{w} {deva[i + 1]}"
                out.add(("devanagari", bigram, bigram))
    return out


def from_posts(prows: list[dict[str, Any]], cfg: InvestigationConfig, limit: int | None = None) -> list[dict[str, Any]]:
    """Suggestions for an event from its post rows (`report.posts`). Empty for subjects and groups."""
    if cfg.kind != "event":
        return []
    stop = _stop()
    generic = _generic()
    full, words = _existing(cfg)
    min_posts = int(threshold("suggest.min_posts", 3))
    min_accounts = int(threshold("suggest.min_accounts", 2))
    limit = int(threshold("suggest.max_suggestions", 15)) if limit is None else limit
    posts: Counter = Counter()
    accounts: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    forms: dict[tuple[str, str], Counter] = defaultdict(Counter)
    langs: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for p in prows:
        for kind, key, form in _candidates(p, stop):
            posts[(kind, key)] += 1
            accounts[(kind, key)].add(_account(p))
            forms[(kind, key)][form] += 1
            langs[(kind, key)][p.get("lang") or ""] += 1
    out = []
    for (kind, key), n in posts.items():
        core = key.lstrip("#")
        if n < min_posts or len(accounts[(kind, key)]) < min_accounts:
            continue
        if len(core) < 3 or _is_digits(core) or match_text(core) in stop:
            continue
        if kind == "hashtag" and "#" + core in generic:
            continue
        if core in full or core.replace(" ", "") in full or (" " not in core and core in words):
            continue
        term = forms[(kind, key)].most_common(1)[0][0]
        if _DEVA.search(term):
            lang = next((lg for lg, _ in langs[(kind, key)].most_common() if lg in ("hi", "mr")), "hi")
        else:
            lang = "en"
        out.append({"term": term, "kind": kind, "posts": n, "accounts": len(accounts[(kind, key)]), "lang": lang})
    out.sort(key=lambda s: (-s["posts"], -s["accounts"], _KIND_ORDER[s["kind"]], s["term"]))
    return out[:limit]


def suggest_terms(ref: int | str, limit: int | None = None) -> list[dict[str, Any]]:
    from socmint.investigate import report, runner

    inv = runner.get(ref)
    return from_posts(report.posts(inv.id), runner.config_of(inv), limit)


def add_suggested(ref: int | str, terms: list[str], who: str | None = None) -> dict[str, Any]:
    """Add the picked suggestions as terms, each under its language, through the audited `runner.edit` path."""
    from socmint.investigate import runner

    lang_of = {s["term"]: s["lang"] for s in suggest_terms(ref, limit=10_000)}
    by_lang: dict[str, list[str]] = {}
    for t in terms:
        by_lang.setdefault(lang_of.get(t) or ("hi" if _DEVA.search(t) else "en"), []).append(t)
    return runner.edit(ref, add_terms_by_lang=by_lang, who=who)
