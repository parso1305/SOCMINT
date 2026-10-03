"""FastAPI service (bind to localhost).

POST /check        - public tipline check (text and/or file) -> verdict JSON
GET  /health       - liveness + degraded features
GET  /alerts       - analyst only (X-API-Key)
GET  /narratives   - analyst only (X-API-Key)
Every request is written to audit_log.
"""

from __future__ import annotations

import hmac
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from sqlalchemy import desc, select

from socmint.core.db import audit, init_db, session_scope
from socmint.core.logging import get_logger
from socmint.core.models import Alert, Narrative
from socmint.core.settings import get_settings

log = get_logger(__name__)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:  # warm the embedder so the first /check meets the ~10 s target
        from socmint.analyze.claim_match import match_claims

        match_claims("warm up")
    except Exception as e:  # noqa: BLE001
        log.warning("warmup_failed", extra={"error": str(e)})
    yield


app = FastAPI(title="SOCMINT-India v0", version="0.1.0", lifespan=lifespan,
              description="Prototype. Verdicts never state that anything is true.")


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    key = get_settings().api_key
    if not key:
        raise HTTPException(503, "SOCMINT_API_KEY not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, key):
        raise HTTPException(401, "invalid or missing X-API-Key")


@app.get("/health")
def health(request: Request) -> dict:
    from socmint.analyze.embeddings import get_embedder
    from socmint.understand.asr import asr_status
    from socmint.understand.media import ffmpeg_path
    from socmint.understand.ocr import ocr_status

    with session_scope() as s:
        audit(s, f"api:{_client(request)}", "health")
    ocr_ok, _, ocr_reason = ocr_status()
    return {"status": "ok", "embedder": get_embedder().name, "ocr": ocr_ok or ocr_reason,
            "asr": asr_status()[0] or asr_status()[1], "ffmpeg": bool(ffmpeg_path())}


@app.post("/check")
async def check_endpoint(request: Request, text: str | None = Form(default=None),
                         file: UploadFile | None = File(default=None), lang: str | None = Form(default=None)) -> dict:
    from socmint.tipline.pipeline import check

    if not text and not file:
        raise HTTPException(400, "provide 'text' and/or 'file'")
    tmp_path: Path | None = None
    try:
        if file is not None:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "file too large (20 MB max)")
            suffix = Path(file.filename or "upload").suffix.lower()[:8]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tf:
                tf.write(data)
                tmp_path = Path(tf.name)
        res = check(text=text, file_path=tmp_path, channel="api", reply_lang=lang)
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
    with session_scope() as s:
        audit(s, f"api:{_client(request)}", "check", f"submission={res.submission_id} verdict={res.verdict.value}")
    return {"verdict": res.verdict.value, "score": res.score, "reasons": [r.model_dump() for r in res.reasons],
            "matches": [m.model_dump(mode="json") for m in res.matches],
            "seen_before": [m.model_dump(mode="json") for m in res.seen_before],
            "indicators": [i.model_dump() for i in res.indicators], "lang": res.lang, "reply": res.reply_text,
            "disclaimer": res.disclaimer, "status": res.status, "degraded": res.degraded, "timings_ms": res.timings_ms}


@app.get("/alerts", dependencies=[Depends(require_key)])
def alerts(request: Request, module: str | None = None, limit: int = 100) -> list[dict]:
    with session_scope() as s:
        q = select(Alert).order_by(desc(Alert.created_at)).limit(min(limit, 1000))
        if module:
            q = q.where(Alert.module == module)
        if module != "hate":
            q = q.where(Alert.module != "hate")  # hate module is analyst-only and never listed by default
        rows = s.scalars(q).all()
        audit(s, f"api:{_client(request)}", "list_alerts", f"module={module} n={len(rows)}")
        return [{"id": a.id, "module": a.module, "severity": a.severity, "title": a.title, "evidence": a.evidence,
                 "created_at": a.created_at.isoformat(), "status": a.status, "synthetic": a.synthetic} for a in rows]


@app.get("/narratives", dependencies=[Depends(require_key)])
def narratives(request: Request) -> list[dict]:
    with session_scope() as s:
        latest = s.scalar(select(Narrative.run_id).order_by(desc(Narrative.created_at)).limit(1))
        rows = s.scalars(select(Narrative).where(Narrative.run_id == latest).order_by(desc(Narrative.growth_rate))).all() \
            if latest else []
        audit(s, f"api:{_client(request)}", "list_narratives", f"run={latest} n={len(rows)}")
        return [{"cluster_id": n.cluster_id, "keywords": n.keywords, "size": n.size, "count_24h": n.count_24h,
                 "growth_rate": n.growth_rate, "first_seen": n.first_seen, "last_seen": n.last_seen,
                 "matched_factcheck_ids": n.matched_factcheck_ids} for n in rows]
