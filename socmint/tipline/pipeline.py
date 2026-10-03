"""Tipline pipeline shared by the Telegram bot, API and CLI.

input -> (temp media store, OCR/ASR/keyframes) -> entities on raw text -> PII redaction -> language
-> [claim match | media provenance | scam score | brand impersonation] in parallel -> verdict
-> store de-identified submission + unlinked indicators -> reply in the user's language.
"""

from __future__ import annotations

import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from socmint.analyze import claim_match, media_provenance, scam
from socmint.analyze.impersonation import brand_by_key
from socmint.core.db import session_scope
from socmint.core.logging import get_logger
from socmint.core.models import Alert, Preference, Submission, utcnow
from socmint.core.privacy import purge_media, redact_pii, sender_hash
from socmint.core.schemas import CheckResult, ExtractedIndicator, IndicatorIn, Reason, Verdict
from socmint.core.settings import get_settings
from socmint.ingest.store import store_indicators
from socmint.tipline import replies
from socmint.understand import asr, media, ocr
from socmint.understand.entities import extract_all
from socmint.understand.lang import detect_lang, reply_language

log = get_logger(__name__)


def _store_media(file_path: Path) -> Path:
    """Copy an uploaded file into the retention-managed media dir under a random name."""
    dest_dir = get_settings().media_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}{file_path.suffix.lower()}"
    shutil.copyfile(file_path, dest)
    return dest


def _understand_media(path: Path, kind: str, degraded: list[str]) -> tuple[str, list[str], dict[str, Any]]:
    """Return (extracted text, perceptual hashes, extra evidence) for a media file."""
    text, hashes, extra = "", [], {}
    if kind == "image":
        hashes.append(media.phash_file(path))
        extra["exif"] = media.read_exif(path)
        t = ocr.ocr_image(path)
        if t is None:
            degraded.append("ocr_unavailable")
        else:
            text = t
    elif kind == "voice":
        r = asr.transcribe(path)
        if r is None:
            degraded.append("asr_unavailable")
        else:
            text, extra["asr_lang"] = r
    elif kind == "video":
        frames = media.video_keyframes(path)
        if not frames:
            degraded.append("video_keyframes_unavailable")
        hashes.extend(dict.fromkeys(media.phash_image(f) for f in frames))
        wav = path.with_suffix(".wav")
        if media.extract_audio(path, wav):
            r = asr.transcribe(wav)
            wav.unlink(missing_ok=True)
            if r:
                text, extra["asr_lang"] = r
            else:
                degraded.append("asr_unavailable")
        extra["keyframes"] = len(frames)
    return text, hashes, extra


def decide(score: float, matches: list, seen: list) -> Verdict:
    """Verdict precedence: likely scam > fact-check match > seen before > suspicious > not verified."""
    b = scam.band(score)
    if b == "likely":
        return Verdict.LIKELY_SCAM
    if matches:
        return Verdict.FACTCHECK_MATCH
    if seen:
        return Verdict.SEEN_BEFORE
    if b == "suspicious":
        return Verdict.SUSPICIOUS
    return Verdict.NOT_VERIFIED


def _reply_lang(detected: str, sender: str | None, explicit: str | None) -> str:
    if explicit in ("en", "hi", "mr"):
        return explicit
    if sender:
        with session_scope() as s:
            pref = s.get(Preference, sender)
            if pref:
                return pref.lang
    return reply_language(detected)


def check(
    text: str | None = None,
    file_path: str | Path | None = None,
    sender_id: str | int | None = None,
    channel: str = "cli",
    reply_lang: str | None = None,
    store: bool = True,
    synthetic: bool = False,
) -> CheckResult:
    """Run the full tipline pipeline on text and/or a media file. Never raises for analysis failures."""
    t0 = time.monotonic()
    timings: dict[str, float] = {}
    degraded: list[str] = []
    sh = sender_hash(sender_id) if sender_id is not None else None
    kind = "text"
    hashes: list[str] = []
    media_extra: dict[str, Any] = {}
    stored_path: Path | None = None
    raw = (text or "").strip()

    if file_path:
        src = Path(file_path)
        kind = media.media_type(src)
        stored_path = _store_media(src) if store else src
        try:
            mtext, hashes, media_extra = _understand_media(stored_path, kind, degraded)
        except Exception as e:  # noqa: BLE001
            log.warning("media_understanding_failed", extra={"error": str(e)[:300]})
            degraded.append("media_unreadable")
            mtext = ""
        raw = "\n".join(x for x in (raw, mtext) if x)
        timings["media_ms"] = round((time.monotonic() - t0) * 1000, 1)

    ent = extract_all(raw)
    lang = detect_lang(raw).code if raw else "en"
    red = redact_pii(raw)

    def _claims():
        return claim_match.match_claims(raw) if raw else []

    def _media():
        return media_provenance.find_seen_before(hashes) if hashes else []

    def _scam():
        return scam.score_text(raw, ent)

    t1 = time.monotonic()
    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {"claims": pool.submit(_claims), "media": pool.submit(_media), "scam": pool.submit(_scam)}
        for k, f in futs.items():
            try:
                results[k] = f.result(timeout=60)
            except Exception as e:  # noqa: BLE001  (module isolation)
                log.warning("module_failed", extra={"module": k, "error": f"{type(e).__name__}: {e}"[:300]})
                degraded.append(f"{k}_failed")
                results[k] = [] if k != "scam" else scam.ScamResult(score=0.0)
    timings["analysis_ms"] = round((time.monotonic() - t1) * 1000, 1)

    sres: scam.ScamResult = results["scam"]
    for f in sres.impersonation:
        b = brand_by_key(f["brand"])
        f["brand_name"] = b.name if b else f["brand"]
    verdict = decide(sres.score, results["claims"], results["media"])

    # indicators: only for scam-ish messages, never linked to the sender
    indicators: list[ExtractedIndicator] = []
    if verdict in (Verdict.LIKELY_SCAM, Verdict.SUSPICIOUS):
        brand_for = {f["value"]: f["brand"] for f in sres.impersonation}
        brand_for.update({f["host"]: f["brand"] for f in sres.lookalikes})
        for u in ent.urls:
            indicators.append(ExtractedIndicator(type="url", value=u))
        for h in ent.domains:
            indicators.append(ExtractedIndicator(type="domain", value=h, linked_brand=brand_for.get(h)))
        for p in ent.phones:
            b = brand_by_key(ent.brands[0]) if ent.brands else None
            if b is None or p not in b.numbers:
                indicators.append(ExtractedIndicator(type="phone", value=p, linked_brand=brand_for.get(p)))
        for u in ent.upi_ids:
            indicators.append(ExtractedIndicator(type="upi", value=u, linked_brand=brand_for.get(u)))
        for th in ent.telegram_handles:
            indicators.append(ExtractedIndicator(type="telegram_handle", value=th))

    reasons: list[Reason] = list(sres.reasons)
    for m in results["claims"]:
        reasons.append(Reason(code="factcheck_match", message=f"Matches fact-check: {m.title}", weight=0,
                              evidence={"url": m.url, "publisher": m.publisher, "score": m.score}))
    for sb in results["media"]:
        reasons.append(Reason(code="media_seen_before", message=f"Seen before ({sb.kind}) on {sb.first_seen:%Y-%m-%d}",
                              weight=0, evidence={"distance": sb.distance, "context": sb.context, "url": sb.url}))
    if media_extra.get("exif"):
        reasons.append(Reason(code="exif", message="EXIF metadata present", evidence=media_extra["exif"]))

    rlang = _reply_lang(lang, sh, reply_lang)
    result = CheckResult(
        verdict=verdict, score=sres.score, reasons=reasons, matches=results["claims"], seen_before=results["media"],
        indicators=indicators, lang=lang, reply_lang=rlang, disclaimer=replies.disclaimer(rlang),
        status="queued" if verdict == Verdict.NOT_VERIFIED else "auto", degraded=sorted(set(degraded)),
    )

    if store:
        result.submission_id = _persist(result, red.text, kind, hashes, stored_path, sh, channel, ent, sres, synthetic)
    result.reply_text = replies.render_reply(result, rlang)
    timings["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
    result.timings_ms = timings
    return result


def _persist(result: CheckResult, redacted: str, kind: str, hashes: list[str], stored_path: Path | None,
             sh: str | None, channel: str, ent, sres: scam.ScamResult, synthetic: bool) -> int:
    with session_scope() as s:
        sub = Submission(
            sender_hash=sh, channel=channel, type=kind, extracted_text=redacted, lang=result.lang,
            media_hash=hashes[0] if hashes else None, media_path=str(stored_path) if stored_path else None,
            verdict=result.verdict.value, score=result.score, status=result.status, synthetic=synthetic,
            reasons={"reasons": [r.model_dump() for r in result.reasons],
                     "matches": [m.model_dump(mode="json") for m in result.matches],
                     "seen_before": [m.model_dump(mode="json") for m in result.seen_before],
                     "entity_types": {k: len(v) for k, v in ent.as_dict().items()}, "degraded": result.degraded},
        )
        s.add(sub)
        s.flush()
        sub_id = sub.id
        if result.indicators:
            store_indicators(s, [IndicatorIn(type=i.type, value=i.value, status="suspected", source="tipline",
                                             linked_brand=i.linked_brand, synthetic=synthetic) for i in result.indicators])
        if sres.impersonation and result.verdict in (Verdict.LIKELY_SCAM, Verdict.SUSPICIOUS):
            brands_hit = sorted({f["brand"] for f in sres.impersonation})
            for bkey in brands_hit:
                fs = [f for f in sres.impersonation if f["brand"] == bkey]
                s.add(Alert(module="impersonation", severity="medium", synthetic=synthetic,
                            title=f"Tipline message claims to be {fs[0].get('brand_name', bkey)} with non-official contact",
                            dedup_key=f"tipline-imp:{bkey}:{'|'.join(sorted(f['value'] for f in fs))}",
                            evidence={"brand": bkey, "non_official": [{"type": f["type"], "value": f["value"]} for f in fs],
                                      "score": result.score, "rules": [r.code for r in sres.reasons],
                                      "at": utcnow().isoformat(), "source": "tipline (de-identified)"}))
    if hashes:
        media_provenance.register_media(hashes, kind="submission", ref_id=sub_id,
                                        context=f"Sent to the tipline ({kind})", synthetic=synthetic)
    return sub_id


def delete_sender(sender_id: str | int | None = None, sender_hash_value: str | None = None) -> int:
    """`/delete`: remove every submission (and its media file) for this sender. Returns the count."""
    sh = sender_hash_value or sender_hash(sender_id)  # type: ignore[arg-type]
    with session_scope() as s:
        rows = s.scalars(select(Submission).where(Submission.sender_hash == sh)).all()
        for r in rows:
            if r.media_path:
                Path(r.media_path).unlink(missing_ok=True)
        n = len(rows)
        s.execute(delete(Submission).where(Submission.sender_hash == sh))
        s.execute(delete(Preference).where(Preference.sender_hash == sh))
    return n


def set_language(sender_id: str | int, lang: str) -> str:
    sh = sender_hash(sender_id)
    lang = lang if lang in ("en", "hi", "mr") else "en"
    with session_scope() as s:
        pref = s.get(Preference, sh)
        if pref is None:
            s.add(Preference(sender_hash=sh, lang=lang))
        else:
            pref.lang = lang
    return lang


def apply_retention() -> dict[str, Any]:
    """Delete raw media older than RETENTION_DAYS and null out the stored paths."""
    s = get_settings()
    deleted = purge_media(s.media_dir, s.retention_days)
    cutoff = utcnow() - timedelta(days=s.retention_days)
    cleared = 0
    with session_scope() as db:
        rows = db.scalars(
            select(Submission).where(Submission.media_path.is_not(None), Submission.received_at < cutoff)
        ).all()
        for r in rows:
            if r.media_path:
                Path(r.media_path).unlink(missing_ok=True)
            r.media_path = None
            cleared += 1
    log.info("retention_applied", extra={"files_deleted": len(deleted), "paths_cleared": cleared})
    return {"files_deleted": len(deleted), "paths_cleared": cleared, "retention_days": s.retention_days}
