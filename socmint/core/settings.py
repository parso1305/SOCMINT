"""Runtime settings (from environment / `.env`) and YAML config loading."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All environment-driven settings. Secrets live only in `.env`, never in YAML."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Storage
    database_url: str = Field(default="", alias="SOCMINT_DB_URL")
    data_dir: Path = Field(default=PROJECT_ROOT / "data", alias="SOCMINT_DATA_DIR")
    config_dir: Path = Field(default=PROJECT_ROOT / "config", alias="SOCMINT_CONFIG_DIR")

    # Identity for polite HTTP
    contact_email: str = Field(default="", alias="CONTACT_EMAIL")

    # Privacy
    privacy_salt: str = Field(default="", alias="PRIVACY_SALT")
    retention_days: int = Field(default=7, alias="RETENTION_DAYS")

    # Local API protection
    api_key: str = Field(default="", alias="SOCMINT_API_KEY")

    # Embeddings: auto | e5 | tfidf
    embedding_backend: str = Field(default="auto", alias="EMBEDDING_BACKEND")
    embedding_model: str = Field(default="intfloat/multilingual-e5-small", alias="EMBEDDING_MODEL")
    whisper_model: str = Field(default="small", alias="WHISPER_MODEL")

    # Free-key sources (all optional)
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_api_id: str = Field(default="", alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(default="", alias="TELEGRAM_API_HASH")
    youtube_api_key: str = Field(default="", alias="YOUTUBE_API_KEY")
    google_factcheck_api_key: str = Field(default="", alias="GOOGLE_FACTCHECK_API_KEY")
    reddit_client_id: str = Field(default="", alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str = Field(default="", alias="REDDIT_CLIENT_SECRET")
    google_safe_browsing_key: str = Field(default="", alias="GOOGLE_SAFE_BROWSING_KEY")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=True, alias="LOG_JSON")

    @property
    def db_url(self) -> str:
        """SQLAlchemy URL; defaults to a SQLite file in the data dir."""
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'socmint.db').as_posix()}"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def user_agent(self) -> str:
        """Descriptive User-Agent with contact email, as required by most public APIs."""
        contact = self.contact_email or "contact-not-configured"
        return f"SOCMINT-India-prototype/0.1 (research; non-commercial; contact: {contact})"

    def has(self, *keys: str) -> bool:
        """True when every named setting attribute is non-empty."""
        return all(bool(getattr(self, k, "")) for k in keys)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Call `reset_settings()` after changing env vars."""
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


def reset_settings() -> None:
    """Clear cached settings and config (used by tests and the demo)."""
    get_settings.cache_clear()
    load_config.cache_clear()


@lru_cache(maxsize=32)
def load_config(name: str) -> dict[str, Any]:
    """Load `config/<name>.yaml` as a dict (empty dict if missing)."""
    path = Path(os.environ.get("SOCMINT_CONFIG_DIR", get_settings().config_dir)) / f"{name}.yaml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def threshold(path: str, default: Any = None) -> Any:
    """Read a dotted key from thresholds.yaml, e.g. `threshold("scam.likely")`."""
    node: Any = load_config("thresholds")
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
