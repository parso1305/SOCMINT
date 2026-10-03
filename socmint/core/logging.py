"""Structured (JSON-lines) logging using the stdlib.

Usage:
    log = get_logger(__name__)
    log.info("source_failed", extra={"source": "boom", "error": str(e)})
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message", "asctime"}
_configured = False


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object; `extra=` fields are included verbatim."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class KeyValueFormatter(logging.Formatter):
    """Human-friendly `LEVEL logger event key=value` format."""

    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(
            f"{k}={v}" for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS and not k.startswith("_")
        )
        base = f"{record.levelname:<7} {record.name}: {record.getMessage()}"
        out = f"{base} {extras}".rstrip()
        if record.exc_info:
            out += "\n" + self.formatException(record.exc_info)
        return out


def configure_logging(level: str = "INFO", json_mode: bool = True) -> None:
    """Configure root logging once. Logs go to stderr so CLI stdout stays clean."""
    global _configured
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if json_mode else KeyValueFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "urllib3", "telethon", "sentence_transformers", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, configuring logging from settings on first use."""
    if not _configured:
        from socmint.core.settings import get_settings

        s = get_settings()
        configure_logging(s.log_level, s.log_json)
    return logging.getLogger(name)
