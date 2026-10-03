"""M4: media provenance (images, video keyframes, EXIF) and brand impersonation."""

from __future__ import annotations

import subprocess

import pytest
from PIL import Image
from sqlalchemy import select

from socmint import fixtures
from socmint.core.db import session_scope
from socmint.core.models import Alert, Indicator, LookalikeDomain
from socmint.core.schemas import Verdict
from socmint.understand.media import ffmpeg_path, hamming, phash_file, video_keyframes


@pytest.fixture(autouse=True)
def demo():
    from socmint.analyze import claim_match, impersonation
    from socmint.ingest.registry import sync_brands

    sync_brands()
    impersonation.brands.cache_clear()
    fixtures.load_factchecks()
    claim_match.reset_index()
    yield


def test_forwarded_image_is_near_duplicate():
    imgs = fixtures.ensure_images()
    assert hamming(phash_file(imgs["original"]), phash_file(imgs["forwarded"])) <= 8
    assert hamming(phash_file(imgs["original"]), phash_file(imgs["unrelated"])) > 8


def test_repeated_image_seen_before_with_factcheck_context():
    from socmint.tipline.pipeline import check

    r = check(file_path=fixtures.ensure_images()["forwarded"])
    assert r.verdict == Verdict.SEEN_BEFORE
    sb = r.seen_before[0]
    assert sb.kind == "factcheck" and "giant snake" in sb.context.lower() and sb.url.endswith("giant-snake-edited-photo")
    assert "ocr_unavailable" in r.degraded or r.degraded == []  # OCR may be absent; never crashes


def test_unrelated_image_not_seen_then_seen_on_resubmission():
    from socmint.tipline.pipeline import check

    img = fixtures.ensure_images()["unrelated"]
    assert check(file_path=img).verdict == Verdict.NOT_VERIFIED
    r2 = check(file_path=img)
    assert r2.verdict == Verdict.SEEN_BEFORE and r2.seen_before[0].kind == "submission"


def test_exif_read(tmp_path):
    from socmint.understand.media import read_exif

    p = tmp_path / "e.jpg"
    img = Image.new("RGB", (32, 32))
    exif = img.getexif()
    exif[0x010F] = "DemoCam"  # Make
    img.save(p, exif=exif)
    assert read_exif(p)["Make"] == "DemoCam"


@pytest.mark.skipif(not ffmpeg_path(), reason="ffmpeg not installed")
def test_video_keyframes_seen_before(tmp_path):
    from socmint.tipline.pipeline import check

    vid = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg_path(), "-loglevel", "error", "-y", "-loop", "1", "-i", str(fixtures.ensure_images()["original"]),
                    "-t", "6", "-vf", "scale=640:480", "-pix_fmt", "yuv420p", str(vid)], check=True, timeout=120)
    assert len(video_keyframes(vid, every_s=2)) >= 2
    r = check(file_path=vid)
    assert r.verdict == Verdict.SEEN_BEFORE and r.seen_before[0].kind == "factcheck"


def test_deepfake_interface_returns_none():
    from socmint.analyze.media_provenance import NoDeepfakeDetector

    assert NoDeepfakeDetector().score("x.jpg") is None


@pytest.mark.parametrize("host,reason", [
    ("dem0bank.example", "typo_or_homoglyph"),
    ("demobank-kyc-update.example", "contains_brand_keyword"),
    ("demobnak.example", "typo_or_homoglyph"),
    ("secure.hdfcbank.com.verify-now.xyz", "contains_brand_keyword"),
    ("onlinesbi-login.in", "contains_brand_keyword"),
])
def test_lookalike_findings(host, reason):
    from socmint.analyze.impersonation import lookalike_findings

    assert reason in [f["reason"] for f in lookalike_findings(host)]


@pytest.mark.parametrize("host", ["demobank.example", "www.hdfc.bank.in", "onlinesbi.sbi.bank.in", "retail.onlinesbi.sbi",
                                  "google.com", "example.org"])
def test_official_and_unrelated_not_lookalikes(host):
    from socmint.analyze.impersonation import lookalike_findings

    assert lookalike_findings(host) == []


def test_demo_lookalike_flagged_in_db_and_tipline():
    from socmint.tipline.pipeline import check

    fixtures.load_lookalikes()
    with session_scope() as s:
        row = s.scalar(select(LookalikeDomain).where(LookalikeDomain.domain == "dem0bank.example"))
        assert row.registered and row.dns_a == ["192.0.2.10"]
        assert s.scalar(select(Alert).where(Alert.dedup_key == "lookalike:demobank:dem0bank.example")) is not None
    r = check("Login to http://dem0bank.example/secure to keep your account active")
    assert "signal:lookalike_domain" in [x.code for x in r.reasons]


def test_record_lookalike_alerts_once():
    from socmint.analyze.impersonation import record_lookalike

    assert record_lookalike("demobank", "demobank-x.example", "test", registered=True, dns_a=["192.0.2.1"])
    assert not record_lookalike("demobank", "demobank-x.example", "test", registered=True, dns_a=["192.0.2.1"])


def test_scan_items_brand_with_unofficial_contact():
    from socmint.analyze.impersonation import scan_items
    from socmint.core.schemas import ItemIn
    from socmint.ingest.store import store_items

    with session_scope() as s:
        store_items(s, [ItemIn(source="t", source_type="telegram", external_id="1", channel="demo_tg",
                               text="DemoBank official helpline 9000000555, refunds at http://demobank-refund.example",
                               synthetic=True)])
    out = scan_items()
    assert out["alerts"] == 1
    with session_scope() as s:
        vals = {(i.type, i.value, i.linked_brand) for i in s.scalars(select(Indicator)).all()}
    assert ("phone", "+919000000555", "demobank") in vals and ("domain", "demobank-refund.example", "demobank") in vals
    assert scan_items()["alerts"] == 0  # deduplicated


def test_dnstwist_scan_offline_is_skipped():
    from socmint.analyze.impersonation import scan_lookalikes

    assert "offline" in scan_lookalikes()["errors"][0]
