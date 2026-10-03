"""M0: settings, DB, privacy utilities, CLI init-db."""

from __future__ import annotations

import os
import time

import pytest
from sqlalchemy import inspect

from socmint.core.privacy import PrivacyConfigError, purge_media, redact_pii, sender_hash


def test_init_db_creates_all_tables():
    from socmint.core.db import get_engine

    tables = set(inspect(get_engine()).get_table_names())
    expected = {
        "sources", "items", "factchecks", "submissions", "indicators", "narratives", "alerts",
        "brands", "market_daily", "audit_log", "media_seen", "lookalike_domains", "coordination_groups",
    }
    assert expected <= tables


def test_indicator_table_has_no_sender_link():
    from socmint.core.models import Indicator

    cols = {c.name for c in Indicator.__table__.columns}
    assert not cols & {"sender_hash", "submission_id", "chat_id", "sender"}


def test_submission_table_stores_no_identity():
    from socmint.core.models import Submission

    cols = {c.name for c in Submission.__table__.columns}
    assert not cols & {"chat_id", "phone", "username", "name", "first_name"}


def test_sender_hash_is_hmac_and_stable():
    a = sender_hash(12345, salt="s1")
    assert a == sender_hash("12345", salt="s1")
    assert a != sender_hash(12345, salt="s2")
    assert len(a) == 64 and "12345" not in a


def test_sender_hash_requires_salt():
    with pytest.raises(PrivacyConfigError):
        sender_hash(1, salt="")


@pytest.mark.parametrize(
    "text,label",
    [
        ("call me on +91 98765 43210 now", "PHONE"),
        ("call 9876543210", "PHONE"),
        ("नंबर ९८७६५४३२१० पर कॉल करें", "PHONE"),
        ("helpline 1800-123-4567", "PHONE"),
        ("office 022-23456789", "PHONE"),
        ("mail raj.k@example.com please", "EMAIL"),
        ("aadhaar 2345 6789 0123 linked", "AADHAAR"),
        ("PAN ABCDE1234F", "PAN"),
        ("pay to someone@okaxis", "UPI"),
    ],
)
def test_redaction(text, label):
    res = redact_pii(text)
    assert f"[{label}]" in res.text
    assert res.counts.get(label) == 1


def test_redaction_leaves_normal_text():
    res = redact_pii("Your KYC is pending, visit the branch. Price 500 rupees, order 12345.")
    assert res.counts == {}
    assert "12345" in res.text


def test_purge_media(tmp_path):
    media = tmp_path / "m"
    media.mkdir()
    old, new = media / "old.jpg", media / "new.jpg"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    past = time.time() - 10 * 86400
    os.utime(old, (past, past))
    deleted = purge_media(media, retention_days=7)
    assert old in deleted and not old.exists() and new.exists()


def test_cli_init_db(monkeypatch):
    from typer.testing import CliRunner

    from socmint import cli

    monkeypatch.setattr(cli, "ensure_env_file", lambda: "dummy.env")
    result = CliRunner().invoke(cli.app, ["init-db"])
    assert result.exit_code == 0, result.output
    assert "Database ready" in result.output
