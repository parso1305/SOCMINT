"""OCR via Tesseract (eng+hin+mar). Disabled with a warning if Tesseract or the language packs are missing."""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from socmint.core.logging import get_logger

log = get_logger(__name__)
WANTED_LANGS = ["eng", "hin", "mar"]
_WINDOWS_PATHS = [Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
                  Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
                  Path.home() / "AppData/Local/Programs/Tesseract-OCR/tesseract.exe"]


@lru_cache(maxsize=1)
def ocr_status() -> tuple[bool, str, str]:
    """(available, language string to use, human-readable reason)."""
    try:
        import pytesseract
    except ImportError:
        return False, "", "pytesseract not installed"
    exe = shutil.which("tesseract") or next((str(p) for p in _WINDOWS_PATHS if p.exists()), None)
    if not exe:
        return False, "", "Tesseract binary not found (install it; see README)"
    pytesseract.pytesseract.tesseract_cmd = exe
    try:
        have = set(pytesseract.get_languages(config=""))
    except Exception as e:  # noqa: BLE001
        return False, "", f"tesseract not runnable: {e}"
    langs = [lang for lang in WANTED_LANGS if lang in have]
    if not langs:
        return False, "", f"no eng/hin/mar traineddata (have: {sorted(have)})"
    missing = [lang for lang in WANTED_LANGS if lang not in have]
    reason = f"missing language packs: {missing}" if missing else ""
    return True, "+".join(langs), reason


def ocr_image(path: str | Path) -> str | None:
    """Return OCR text, or None if OCR is unavailable (warning logged once)."""
    ok, langs, reason = ocr_status()
    if not ok:
        _warn_once(reason)
        return None
    import pytesseract
    from PIL import Image

    with Image.open(path) as img:
        return pytesseract.image_to_string(img.convert("RGB"), lang=langs).strip()


_warned: set[str] = set()


def _warn_once(reason: str) -> None:
    if reason not in _warned:
        _warned.add(reason)
        log.warning("ocr_disabled", extra={"reason": reason})
