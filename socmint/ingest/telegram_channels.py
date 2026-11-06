"""Public Telegram channels via Telethon (read-only). Requires TELEGRAM_API_ID / TELEGRAM_API_HASH.

Telethon needs a one-time interactive login to create a session file (`socmint telegram-login`).
Only channels listed in sources.yaml or in the investigation are read, only public *broadcast* channels
(no groups, no private chats), and requests are rate-limited.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from socmint.core.logging import get_logger
from socmint.core.models import utcnow
from socmint.core.schemas import AccountIn, FetchResult, ItemIn
from socmint.core.settings import get_settings
from socmint.ingest.base import Source, TermMatcher, clean_summary, flat_terms, register, text_entities

log = get_logger(__name__)


def session_path() -> str:
    return str(get_settings().data_dir / "telethon")


def _when(m: Any) -> datetime | None:
    return m.date.astimezone(UTC).replace(tzinfo=None) if getattr(m, "date", None) else None


def message_item(m: Any, ch: str, channel_id: str | None, source: str) -> ItemIn | None:
    """Telethon Message -> post. The public channel is the author; post signatures (names) are not stored."""
    text = (getattr(m, "message", None) or "").strip()
    if not text:
        return None
    fwd = getattr(m, "fwd_from", None)
    fwd_id = getattr(getattr(fwd, "from_id", None), "channel_id", None) if fwd else None
    reply = getattr(m, "reply_to", None)
    ents = text_entities(text)
    replies = getattr(getattr(m, "replies", None), "replies", None)
    reactions = getattr(m, "reactions", None)
    n_react = sum(getattr(r, "count", 0) for r in getattr(reactions, "results", []) or []) if reactions else None
    media = [{"type": "photo" if getattr(m, "photo", None) else "media", "url": f"https://t.me/{ch}/{m.id}", "phash": None}] \
        if getattr(m, "media", None) else []
    return ItemIn(
        source=source, source_type="telegram", external_id=f"{ch}:{m.id}", url=f"https://t.me/{ch}/{m.id}",
        channel=ch, author_handle=ch, author_id=channel_id or ch, author_display_name=None,
        published_at=_when(m), text=clean_summary(text, 2000),
        hashtags=ents["hashtags"], mentions=ents["mentions"], urls=ents["urls"],
        engagement={"views": getattr(m, "views", None), "forwards": getattr(m, "forwards", None),
                    "replies": replies, "reactions": n_react},
        reply_to_external_id=f"{ch}:{reply.reply_to_msg_id}" if getattr(reply, "reply_to_msg_id", None) else None,
        forwarded_from=str(fwd_id) if fwd_id else None, media=media,
        meta={"views": getattr(m, "views", None), "forwards": getattr(m, "forwards", None), "fwd_from": fwd_id,
              "fwd_from_name": getattr(fwd, "from_name", None) if fwd else None})


def channel_account(entity: Any, full: Any = None) -> AccountIn:
    fc = getattr(full, "full_chat", None)
    return AccountIn(platform="telegram", platform_id=str(entity.id), handle=entity.username,
                     display_name=getattr(entity, "title", None), url=f"https://t.me/{entity.username}",
                     created_at=entity.date.astimezone(UTC).replace(tzinfo=None) if getattr(entity, "date", None) else None,
                     followers=getattr(fc, "participants_count", None) or getattr(entity, "participants_count", None),
                     description=clean_summary(getattr(fc, "about", None), 1000) or None,
                     meta={"verified": getattr(entity, "verified", None), "scam": getattr(entity, "scam", None),
                           "fake": getattr(entity, "fake", None)})


@register
class TelegramChannelsSource(Source):
    """cfg: channels (list of public usernames), limit_per_channel, rate_limit_s."""

    type_name = "telegram_channels"
    required_settings = ("telegram_api_id", "telegram_api_hash")
    capabilities = {"terms", "accounts"}

    def hosts(self) -> list[str]:
        return []

    async def _client(self):  # noqa: ANN202
        from telethon import TelegramClient

        s = get_settings()
        client = TelegramClient(session_path(), int(s.telegram_api_id), s.telegram_api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError("Telethon session not authorised; run `socmint telegram-login` once")
        return client

    @staticmethod
    async def _public_channel(client: Any, ch: str) -> Any:
        entity = await client.get_entity(ch)
        if not (getattr(entity, "broadcast", False) and getattr(entity, "username", None)):
            raise ValueError(f"{ch} is not a public broadcast channel; only public channels are read")
        return entity

    def fetch(self, since: datetime | None) -> FetchResult:
        return asyncio.run(self._fetch(since))

    async def _fetch(self, since: datetime | None) -> FetchResult:
        res = FetchResult()
        cutoff = since or (utcnow() - timedelta(days=2))
        client = await self._client()
        try:
            for ch in self.cfg.get("channels", []):
                entity = await self._public_channel(client, ch)
                async for m in client.iter_messages(entity, limit=int(self.cfg.get("limit_per_channel", 200))):
                    if _when(m) and _when(m) < cutoff:
                        break
                    it = message_item(m, ch, str(entity.id), self.name)
                    if it:
                        res.items.append(it)
                res.accounts.append(channel_account(entity))
                time.sleep(float(self.cfg.get("rate_limit_s", 3)))
        finally:
            await client.disconnect()
        return res

    def search_terms(self, terms_by_lang: dict[str, list[str]], since: datetime | None, limit: int) -> FetchResult:
        """Server-side search *within* the listed public channels only (never global search)."""
        return asyncio.run(self._search(flat_terms(terms_by_lang), TermMatcher(terms_by_lang), since, limit))

    async def _search(self, terms: list[str], matcher: TermMatcher, since: datetime | None, limit: int) -> FetchResult:
        res = FetchResult()
        channels = self.cfg.get("channels", [])
        if not channels:
            res.notes.append("no channels listed; Telegram is searched only within listed public channels")
            return res
        client = await self._client()
        try:
            for ch in channels:
                entity = await self._public_channel(client, ch)
                seen: set[int] = set()
                for term in terms:
                    async for m in client.iter_messages(entity, search=term.lstrip("#"), limit=limit):
                        if m.id in seen or (since and _when(m) and _when(m) < since):
                            continue
                        seen.add(m.id)
                        it = message_item(m, ch, str(entity.id), self.name)
                        if it:
                            it.meta["matched_terms"] = matcher.match(it.text, it.hashtags) or [term]
                            res.items.append(it)
                    time.sleep(float(self.cfg.get("rate_limit_s", 3)))
                res.accounts.append(channel_account(entity))
        finally:
            await client.disconnect()
        return res

    def account_profile(self, handle: str) -> AccountIn:
        return asyncio.run(self._profile(handle.lstrip("@")))

    async def _profile(self, ch: str) -> AccountIn:
        from telethon.tl.functions.channels import GetFullChannelRequest

        client = await self._client()
        try:
            entity = await self._public_channel(client, ch)
            return channel_account(entity, await client(GetFullChannelRequest(entity)))
        finally:
            await client.disconnect()

    def account_posts(self, handle: str, since: datetime | None, limit: int) -> FetchResult:
        cfg = {**self.cfg, "channels": [handle.lstrip("@")], "limit_per_channel": limit}
        src = TelegramChannelsSource(cfg, client=self.client)
        res = asyncio.run(src._fetch(since))
        try:
            res.accounts = [self.account_profile(handle)]
        except Exception as e:  # noqa: BLE001  (profile details are optional; posts already collected)
            res.notes.append(f"channel info unavailable: {e}")
        return res
