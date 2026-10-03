"""M3: FastAPI endpoints and Telegram handler logic (no network, no token needed)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from socmint import fixtures
from socmint.core.db import session_scope
from socmint.core.models import AuditLog, Submission


@pytest.fixture()
def client():
    from socmint.analyze import claim_match
    from socmint.api.app import app
    from socmint.ingest.registry import sync_brands

    sync_brands()
    fixtures.load_factchecks()
    claim_match.reset_index()
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_check_text(client):
    r = client.post("/check", data={"text": "Congratulations! You have won a lottery. Pay processing fee to win.fee@demoupi "
                                            "within 24 hours"})
    body = r.json()
    assert r.status_code == 200 and body["verdict"] == "likely_scam"
    assert set(body) >= {"verdict", "score", "reasons", "matches", "indicators", "disclaimer"}
    with session_scope() as s:
        assert s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "check")) == 1


def test_check_image_file(client):
    imgs = fixtures.ensure_images()
    with open(imgs["forwarded"], "rb") as f:
        r = client.post("/check", files={"file": ("photo.jpg", f, "image/jpeg")})
    assert r.status_code == 200
    assert r.json()["verdict"] == "seen_before"


def test_check_requires_input(client):
    assert client.post("/check", data={}).status_code == 400


def test_protected_endpoints(client):
    assert client.get("/alerts").status_code == 401
    assert client.get("/alerts", headers={"X-API-Key": "wrong"}).status_code == 401
    r = client.get("/alerts", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200 and isinstance(r.json(), list)
    assert client.get("/narratives", headers={"X-API-Key": "test-key"}).status_code == 200


def test_bot_handlers_end_to_end():
    from socmint.ingest.registry import sync_brands
    from socmint.tipline import telegram_bot as tb

    sync_brands()
    fixtures.load_factchecks()
    chat = 555001
    assert "SOCMINT-India" in tb.reply_for_start(chat) and "/delete" in tb.reply_for_start(chat)
    assert tb.reply_for_lang(chat, "hi") == "भाषा हिंदी पर सेट की गई।"
    out = tb.reply_for_message(chat, "Dear DemoBank customer, account will be blocked today, update KYC at "
                                     "http://demobank-kyc.example/x")
    assert "स्कैम" in out and "1930" in out  # replied in the chosen language
    with session_scope() as s:
        sub = s.scalar(select(Submission))
        assert sub.channel == "telegram" and str(chat) not in (sub.sender_hash or "")
    assert "1" in tb.reply_for_delete(chat)
    with session_scope() as s:
        assert s.scalar(select(func.count(Submission.id))) == 0
