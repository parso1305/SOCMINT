"""M2: scam scoring per category/language, verdicts, claim matching, privacy of stored data, replies."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from socmint import fixtures
from socmint.core.db import session_scope
from socmint.core.models import Alert, Indicator, Submission
from socmint.core.schemas import Verdict

MESSAGES = fixtures.tipline_messages()
BACKEND = os.environ.get("SOCMINT_TEST_BACKEND", "tfidf")


@pytest.fixture(autouse=True)
def demo_data():
    from socmint.analyze import claim_match
    from socmint.ingest.registry import sync_brands
    from socmint.understand.entities import clear_caches

    sync_brands()
    fixtures.load_factchecks()
    fixtures.load_symbols()
    if BACKEND != "tfidf":
        fixtures.load_items()  # background corpus for centered e5 similarity
    clear_caches()
    claim_match.reset_index()
    yield


def _expected_ok(msg, verdict: str) -> bool:
    if msg["expect"] == "suspicious_or_worse":
        return verdict in ("suspicious", "likely_scam")
    return verdict == msg["expect"]


@pytest.mark.parametrize("msg", [m for m in MESSAGES if m.get("requires_backend", BACKEND) == BACKEND],
                         ids=lambda m: m["id"])
def test_fixture_verdicts(msg):
    from socmint.tipline.pipeline import check

    r = check(msg["text"], store=False)
    assert _expected_ok(msg, r.verdict.value), (r.verdict, r.score, [x.code for x in r.reasons])
    if msg["category"] not in ("benign", "factcheck"):
        assert f"category:{msg['category']}" in [x.code for x in r.reasons]
    if msg.get("expect_fc"):
        assert r.matches[0].url == fixtures.factcheck_url(msg["expect_fc"])


def test_every_category_covered_in_en_hi_mr():
    cats = {m["category"] for m in MESSAGES} - {"benign", "factcheck"}
    assert len(cats) == 9
    for c in cats:
        assert {"en", "hi", "mr"} <= {m["lang"] for m in MESSAGES if m["category"] == c}, c


def test_reasons_carry_evidence_and_weights():
    from socmint.tipline.pipeline import check

    r = check(next(m["text"] for m in MESSAGES if m["id"] == "kyc_en"), store=False)
    assert r.score == sum(x.weight for x in r.reasons if x.weight) or r.score == 100
    kyc = next(x for x in r.reasons if x.code == "category:kyc_block")
    assert kyc.evidence["keywords"]
    look = next(x for x in r.reasons if x.code == "signal:lookalike_domain")
    assert look.evidence["findings"][0]["brand"] == "demobank"


def test_threat_feed_signal():
    from socmint.core.schemas import FetchResult, IndicatorIn
    from socmint.ingest.store import store_result
    from socmint.tipline.pipeline import check

    with session_scope() as s:
        store_result(s, FetchResult(indicators=[IndicatorIn(type="url", value="http://phish.example/x", status="reported",
                                                            source="openphish")]))
    r = check("check this http://phish.example/x", store=False)
    assert "signal:threat_feed_url" in [x.code for x in r.reasons]


def test_submission_is_deidentified_and_indicators_unlinked():
    from socmint.core.privacy import sender_hash
    from socmint.tipline.pipeline import check

    text = ("DemoBank KYC pending! account will be blocked today. call 9000000101, email me at victim.name@example.com, "
            "my aadhaar 2345 6789 0123, pay fee to kyc.fee@demoupi http://demobank-kyc.example/x")
    r = check(text, sender_id=424242, channel="telegram")
    assert r.verdict == Verdict.LIKELY_SCAM
    with session_scope() as s:
        sub = s.get(Submission, r.submission_id)
        assert sub.sender_hash == sender_hash(424242) and "424242" not in (sub.sender_hash or "")
        for secret in ("9000000101", "victim.name@example.com", "2345 6789 0123", "kyc.fee@demoupi"):
            assert secret not in sub.extracted_text
        assert "[PHONE]" in sub.extracted_text and "[EMAIL]" in sub.extracted_text and "[AADHAAR]" in sub.extracted_text
        vals = {(i.type, i.value) for i in s.scalars(select(Indicator)).all()}
        assert ("phone", "+919000000101") in vals and ("upi", "kyc.fee@demoupi") in vals
        assert ("domain", "demobank-kyc.example") in vals
        assert not any("victim" in v for _, v in vals)  # e-mails are PII, never indicators
        assert all(i.status == "suspected" for i in s.scalars(select(Indicator)).all())
        assert s.scalar(select(func.count(Alert.id)).where(Alert.module == "impersonation")) >= 1


def test_no_indicators_for_unverified_messages():
    from socmint.tipline.pipeline import check

    r = check("Meet me at 5, my number is 9876543210", store=True)
    assert r.verdict == Verdict.NOT_VERIFIED and r.status == "queued" and not r.indicators
    with session_scope() as s:
        assert s.scalar(select(func.count(Indicator.id))) == 0


def test_delete_removes_all_sender_submissions():
    from socmint.tipline.pipeline import check, delete_sender

    for t in ("hello one", "hello two"):
        check(t, sender_id=99)
    check("other person", sender_id=100)
    assert delete_sender(99) == 2
    with session_scope() as s:
        assert s.scalar(select(func.count(Submission.id))) == 1


@pytest.mark.parametrize("lang", ["en", "hi", "mr"])
def test_reply_format(lang):
    from socmint.tipline import replies
    from socmint.tipline.pipeline import check

    scam_r = check(next(m["text"] for m in MESSAGES if m["id"] == "lottery_en"), store=False, reply_lang=lang)
    assert "1930" in scam_r.reply_text and "cybercrime.gov.in" in scam_r.reply_text
    assert scam_r.reply_text.strip().endswith(replies.DISCLAIMER[lang])
    bullets = [ln for ln in scam_r.reply_text.splitlines() if ln.startswith("•")]
    assert 1 <= len(bullets) <= 3
    fc_r = check(next(m["text"] for m in MESSAGES if m["id"] == "fc_en"), store=False, reply_lang=lang)
    assert fc_r.verdict == Verdict.FACTCHECK_MATCH and fixtures.factcheck_url("fc_dengue") in fc_r.reply_text


def test_no_true_verdict_wording():
    from socmint.tipline import replies

    for lang, lines in replies.VERDICT_LINE.items():
        for line in lines.values():
            low = line.lower()
            assert "true" not in low and "verified as" not in low and "सच है" not in line and "खरे आहे" not in line, lang
    assert {v.value for v in Verdict} == {"factcheck_match", "seen_before", "likely_scam", "suspicious", "not_verified"}


def test_verdict_precedence():
    from socmint.tipline.pipeline import decide

    assert decide(80, ["m"], ["s"]) == Verdict.LIKELY_SCAM
    assert decide(50, ["m"], []) == Verdict.FACTCHECK_MATCH
    assert decide(50, [], ["s"]) == Verdict.SEEN_BEFORE
    assert decide(50, [], []) == Verdict.SUSPICIOUS
    assert decide(10, [], []) == Verdict.NOT_VERIFIED


def test_cli_check_json():
    import json

    from typer.testing import CliRunner

    from socmint import cli

    res = CliRunner().invoke(cli.app, ["check", "Congratulations you have won a lottery, pay processing fee to a.b@demoupi immediately",
                                       "--json", "--no-store"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["verdict"] == "likely_scam" and out["disclaimer"]


def test_submission_evidence_sanitised_and_review_updates_indicators():
    from socmint.tipline.pipeline import check, review_submission

    r = check("DemoBank customer care refund pending, download AnyDesk, call helpline 9000000201 immediately, "
              "fee to care.fee@demoupi", sender_id=7)
    assert r.verdict == Verdict.LIKELY_SCAM
    with session_scope() as s:
        sub = s.get(Submission, r.submission_id)
        blob = str(sub.reasons)
        assert "9000000201" not in blob and "care.fee@demoupi" not in blob
        assert {"type": "phone", "value": "+919000000201", "linked_brand": "demobank"} in sub.pending_indicators
    out = review_submission(r.submission_id, "confirmed_scam")
    assert out["indicators_updated"] >= 2
    with session_scope() as s:
        sub = s.get(Submission, r.submission_id)
        assert sub.status == "reviewed" and sub.pending_indicators == []
        ind = s.scalar(select(Indicator).where(Indicator.value == "+919000000201"))
        assert ind.status == "confirmed"
