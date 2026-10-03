"""Media helpers: type detection, perceptual hashing, EXIF, video keyframes (ffmpeg if present)."""

from __future__ import annotations

import io
import mimetypes
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import imagehash
from PIL import ExifTags, Image

from socmint.core.logging import get_logger
from socmint.core.settings import threshold

log = get_logger(__name__)

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
AUDIO_EXT = {".ogg", ".oga", ".mp3", ".m4a", ".wav", ".opus", ".aac", ".flac"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".3gp"}


def media_type(path: str | Path) -> str:
    """Classify a file as image / voice / video / text by extension (then MIME guess)."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "voice"
    if ext in VIDEO_EXT:
        return "video"
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        for prefix, kind in (("image/", "image"), ("audio/", "voice"), ("video/", "video")):
            if mime.startswith(prefix):
                return kind
    return "text"


def phash_image(img: Image.Image) -> str:
    """64-bit perceptual hash as 16 hex chars."""
    return str(imagehash.phash(img.convert("RGB")))


def phash_bytes(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as img:
        return phash_image(img)


def phash_file(path: str | Path) -> str:
    with Image.open(path) as img:
        return phash_image(img)


def hamming(a: str, b: str) -> int:
    """Hamming distance between two hex pHashes."""
    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)


def read_exif(path: str | Path) -> dict[str, Any]:
    """Return a small, human-readable subset of EXIF (camera, timestamps, software, GPS presence)."""
    keep = {"Make", "Model", "DateTime", "DateTimeOriginal", "Software", "ImageDescription"}
    try:
        with Image.open(path) as img:
            raw = img.getexif()
            out: dict[str, Any] = {}
            for tag_id, value in raw.items():
                tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                if tag in keep:
                    out[tag] = str(value)[:100]
                if tag == "GPSInfo":
                    out["has_gps"] = True
            try:
                sub = raw.get_ifd(0x8769)
                for tag_id, value in sub.items():
                    tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                    if tag in keep:
                        out[tag] = str(value)[:100]
            except Exception:  # noqa: BLE001
                pass
            return out
    except Exception as e:  # noqa: BLE001
        log.debug("exif_failed", extra={"error": str(e)})
        return {}


def ffmpeg_path() -> str | None:
    """Locate ffmpeg on PATH (winget installs a link in WinGet\\Links)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    links = Path.home() / "AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe"
    return str(links) if links.exists() else None


def video_keyframes(path: str | Path, every_s: float | None = None, max_frames: int | None = None) -> list[Image.Image]:
    """Extract one frame every `every_s` seconds with ffmpeg. Returns [] (with a warning) without ffmpeg."""
    ff = ffmpeg_path()
    if not ff:
        log.warning("ffmpeg_missing", extra={"effect": "video keyframe hashing disabled"})
        return []
    every_s = every_s or float(threshold("media.video_keyframe_every_s", 5))
    max_frames = max_frames or int(threshold("media.video_max_frames", 30))
    frames: list[Image.Image] = []
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf", f"fps=1/{every_s}",
               "-frames:v", str(max_frames), str(Path(tmp) / "f_%04d.png")]
        try:
            subprocess.run(cmd, check=True, timeout=120, capture_output=True)
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("ffmpeg_failed", extra={"error": str(e)[:300]})
            return []
        for p in sorted(Path(tmp).glob("f_*.png")):
            with Image.open(p) as im:
                frames.append(im.copy())
    return frames


def extract_audio(path: str | Path, out_path: str | Path) -> bool:
    """Extract a mono 16 kHz WAV audio track with ffmpeg (for ASR on videos)."""
    ff = ffmpeg_path()
    if not ff:
        return False
    try:
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-vn", "-ac", "1",
                        "-ar", "16000", str(out_path)], check=True, timeout=180, capture_output=True)
        return Path(out_path).exists()
    except (subprocess.SubprocessError, OSError):
        return False
