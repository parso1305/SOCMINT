"""Public Telegram channels via Telethon (read-only). Requires TELEGRAM_API_ID / TELEGRAM_API_HASH.

Telethon needs a one-time interactive login to create a session file (`socmint telegram-login`).
Only channels listed in sources.yaml are read; requests are rate-limited.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

from socmint.core.logging import get_logger
from socmint.core.models import utcnow
from socmint.core.schemas import FetchResult, ItemIn
from socmint.core.settings import get_settings
from socmint.ingest.base import Source, clean_summary, register

log = get_logger(__name__)


def session_path() -> str:
    return str(get_settings().data_dir / "telethon")


@register
class TelegramChannelsSource(Source):
    """cfg: channels (list of public usernames), limit_per_channel, rate_limit_s."""

    type_name = "telegram_channels"
    required_settings = ("telegram_api_id", "telegram_api_hash")

    def hosts(self) -> list[str]:
        return []

    def fetch(self, since: datetime | None) -> FetchResult:
        return asyncio.run(self._fetch(since))

    async def _fetch(self, since: datetime | None) -> FetchResult:
        from telethon import TelegramClient

        s = get_settings()
        res = FetchResult()
        cutoff = since or (utcnow() - timedelta(days=2))
        client = TelegramClient(session_path(), int(s.telegram_api_id), s.telegram_api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError("Telethon session not authorised; run `socmint telegram-login` once")
            for ch in self.cfg.get("channels", []):
                async for m in client.iter_messages(ch, limit=int(self.cfg.get("limit_per_channel", 200))):
                    when = m.date.astimezone(UTC).replace(tzinfo=None) if m.date else None
                    if when and when < cutoff:
                        break
                    if not (m.message or "").strip():
                        continue
                    res.items.append(ItemIn(
                        source=self.name, source_type="telegram", external_id=f"{ch}:{m.id}",
                        url=f"https://t.me/{ch}/{m.id}", channel=ch,
                        # author = the public channel itself; post signatures (personal names) are not stored
                        author_handle=ch, published_at=when, text=clean_summary(m.message, 2000),
                        meta={"views": m.views, "forwards": m.forwards,
                              "fwd_from": getattr(getattr(m.fwd_from, "from_id", None), "channel_id", None)}))
                time.sleep(float(self.cfg.get("rate_limit_s", 3)))
        finally:
            await client.disconnect()
        return res
