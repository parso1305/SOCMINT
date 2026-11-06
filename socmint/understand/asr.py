"""Speech-to-text for voice notes via faster-whisper (optional `[ml]` extra)."""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path

from socmint.core.logging import get_logger
from socmint.core.settings import get_settings

log = get_logger(__name__)
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel

    if os.environ.get("SOCMINT_OFFLINE"):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from faster_whisper.utils import download_model

    name = get_settings().whisper_model
    try:  # cached copy: no Hub request
        path = download_model(name, local_files_only=True)
        log.info("hf_model_from_cache", extra={"model": name})
    except Exception:  # noqa: BLE001  (not cached yet)
        path = name
        log.info("hf_model_download", extra={"model": name})
    return WhisperModel(path, device="cpu", compute_type="int8")


def asr_status() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False, "faster-whisper not installed (install the [ml] extra)"
    return True, ""


def transcribe(path: str | Path) -> tuple[str, str] | None:
    """Return (text, detected_language) or None if ASR is unavailable."""
    ok, reason = asr_status()
    if not ok:
        log.warning("asr_disabled", extra={"reason": reason})
        return None
    try:
        with _lock:
            segments, info = _model().transcribe(str(path), beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
        return text, info.language
    except Exception as e:  # noqa: BLE001  (model download failed offline, bad audio, ...)
        log.warning("asr_failed", extra={"error": str(e)[:300]})
        return None
