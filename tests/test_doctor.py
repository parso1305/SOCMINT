"""M17.4: `socmint doctor` is a read-only pre-demo check: PASS / WARN / FAIL with a one-line fix per line, and it
never prints a secret value."""

from __future__ import annotations

import socket
import sqlite3
from datetime import timedelta

import httpx
import pytest
from typer.testing import CliRunner

from socmint import doctor
from socmint.core import cooldown
from socmint.core.http import PoliteClient
from socmint.core.models import utcnow


def by_name(checks: list[doctor.Check], prefix: str) -> list[doctor.Check]:
    return [c for c in checks if c.name.startswith(prefix)]


def test_every_line_has_a_level_and_a_fix_and_no_secret_is_printed(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "secret-contact@example.org")
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIzaSECRETVALUE123")
    from socmint.core.settings import reset_settings

    reset_settings()
    checks = doctor.run_checks(network=False)
    assert checks and all(c.level in ("PASS", "WARN", "FAIL") for c in checks)
    assert all(c.fix for c in checks if c.level != "PASS")
    text = doctor.render(checks)
    assert "secret-contact@example.org" not in text and "AIzaSECRETVALUE123" not in text
    assert by_name(checks, "CONTACT_EMAIL")[0].level == "PASS" and "set" in by_name(checks, "CONTACT_EMAIL")[0].detail
    yt = by_name(checks, "YOUTUBE_API_KEY")[0]
    assert yt.level == "PASS" and "set" in yt.detail and "youtube" in yt.detail


def test_missing_contact_email_and_event_keys_warn_with_the_variable(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "")
    from socmint.core.settings import reset_settings

    reset_settings()
    checks = doctor.run_checks(network=False)
    ce = by_name(checks, "CONTACT_EMAIL")[0]
    assert ce.level == "WARN" and "CONTACT_EMAIL" in ce.fix
    yt = by_name(checks, "YOUTUBE_API_KEY")[0]
    assert yt.level == "WARN" and "missing" in yt.detail and "YOUTUBE_API_KEY" in yt.fix
    ev = by_name(checks, "event sources")[0]
    assert "bluesky" in ev.detail and "gdelt" in ev.detail and "youtube" not in ev.detail.split("not used")[0]


def test_port_in_use_is_named():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        c = doctor.port_check(port, "test server")
        assert c.level == "WARN" and "in use" in c.detail and str(port) in c.name
    finally:
        srv.close()
    assert doctor.port_check(port, "test server").level == "PASS"


@pytest.mark.parametrize("hours,level", [(1, "PASS"), (13, "WARN")])
def test_demo_db_age(tmp_path, monkeypatch, hours, level):
    path = tmp_path / "demo.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE investigations (id INTEGER PRIMARY KEY, name TEXT, created_at DATETIME, synthetic BOOLEAN)")
    con.execute("INSERT INTO investigations (name, created_at, synthetic) VALUES (?, ?, 1)",
                ("Pune floods 2026 (demo)", (utcnow() - timedelta(hours=hours)).isoformat(sep=" ")))
    con.commit()
    con.close()
    monkeypatch.setattr("socmint.demo.demo_db_path", lambda: path)
    c = doctor.demo_check()
    assert c.level == level
    if level == "WARN":
        assert "rebuild before a meeting" in c.detail + c.fix and "--rebuild" in c.fix


def test_uv_missing_gives_the_python_m_uv_hint(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    c = doctor.uv_check()
    assert c.level == "WARN" and "python -m uv" in c.fix


def test_reachability_reuses_robots_fetch_and_respects_the_gdelt_cooldown(monkeypatch):
    monkeypatch.setenv("SOCMINT_OFFLINE", "0")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.host}{request.url.path}")
        if request.url.host == "slow.example":
            raise httpx.ConnectTimeout("simulated", request=request)
        if request.url.host == "busy.example":
            return httpx.Response(429)
        return httpx.Response(404)

    c = PoliteClient(rate_limit_s=0)
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    cooldown.start("api.gdeltproject.org", minutes=15, reason="HTTP 429")
    rows = doctor.reachability(["https://ok.example/feed", "https://ok.example/other", "https://slow.example/x",
                                "https://busy.example/y", "https://api.gdeltproject.org/api/v2/doc/doc"], client=c)
    levels = {r.name.split(" ")[-1]: r.level for r in rows}
    assert levels == {"ok.example": "PASS", "slow.example": "WARN", "busy.example": "WARN",
                      "api.gdeltproject.org": "WARN"}
    gd = next(r for r in rows if r.name.endswith("api.gdeltproject.org"))
    assert "cooldown" in gd.detail and "next try after" in gd.detail
    assert seen.count("ok.example/robots.txt") == 1                       # one polite request per host
    assert not any(s.startswith("api.gdeltproject.org") for s in seen)   # nothing sent during the cooldown
    assert c.allowed("https://ok.example/again") and seen.count("ok.example/robots.txt") == 1  # cache reused


def test_doctor_does_not_touch_the_database(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("doctor must not migrate or write the DB")

    monkeypatch.setattr("socmint.core.db.init_db", boom)
    from socmint.cli import app

    res = CliRunner().invoke(app, ["doctor", "--offline"])
    assert res.exit_code in (0, 1), res.output
    assert "PASS" in res.output and "CONTACT_EMAIL" in res.output and "Tesseract" in res.output


def test_bluesky_login_keys_are_reported_and_change_the_event_source_line(monkeypatch):
    from socmint.core.settings import reset_settings

    monkeypatch.setenv("BLUESKY_HANDLE", "")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "")
    reset_settings()
    checks = doctor.run_checks(network=False)
    row = by_name(checks, "BLUESKY_HANDLE + BLUESKY_APP_PASSWORD")[0]
    assert row.level == "WARN" and "BLUESKY_APP_PASSWORD" in row.fix
    assert "live listen 60 s" in by_name(checks, "event sources")[0].detail

    monkeypatch.setenv("BLUESKY_HANDLE", "project.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "abcd-efgh-ijkl-mnop")
    reset_settings()
    checks = doctor.run_checks(network=False)
    row = by_name(checks, "BLUESKY_HANDLE + BLUESKY_APP_PASSWORD")[0]
    assert row.level == "PASS" and "set" in row.detail
    assert "bluesky (search, logged in)" in by_name(checks, "event sources")[0].detail
    assert "abcd-efgh-ijkl-mnop" not in doctor.render(checks)
