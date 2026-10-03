"""Telegram tipline bot (python-telegram-bot v21). Requires TELEGRAM_BOT_TOKEN.

Handler logic lives in plain functions (`reply_for_*`) so it is testable without Telegram.
The chat id is only ever passed to `sender_hash()`; it is never stored or logged.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from socmint.core.logging import get_logger
from socmint.core.settings import get_settings
from socmint.tipline import replies
from socmint.tipline.pipeline import check, delete_sender, set_language

log = get_logger(__name__)


def _pref_lang(chat_id: int) -> str:
    from socmint.core.db import session_scope
    from socmint.core.models import Preference
    from socmint.core.privacy import sender_hash

    with session_scope() as s:
        p = s.get(Preference, sender_hash(chat_id))
        return p.lang if p else "en"


def reply_for_start(chat_id: int) -> str:
    lang = _pref_lang(chat_id)
    days = get_settings().retention_days
    # consent notice in all three languages on /start
    return "\n\n———\n\n".join(replies.START[lg].format(days=days) for lg in [lang] + [x for x in ("en", "hi", "mr") if x != lang])


def reply_for_help(chat_id: int) -> str:
    return replies.HELP[_pref_lang(chat_id)]


def reply_for_lang(chat_id: int, arg: str | None) -> str:
    if arg not in ("en", "hi", "mr"):
        return "Usage: /lang en | hi | mr"
    return replies.LANG_SET[set_language(chat_id, arg)]


def reply_for_delete(chat_id: int) -> str:
    lang = _pref_lang(chat_id)
    n = delete_sender(chat_id)
    return replies.DELETED[lang].format(n=n)


def reply_for_message(chat_id: int, text: str | None, file_path: Path | None = None) -> str:
    res = check(text=text, file_path=file_path, sender_id=chat_id, channel="telegram")
    log.info("tipline_reply", extra={"verdict": res.verdict.value, "ms": res.timings_ms.get("total_ms")})
    return res.reply_text


def run_bot() -> None:
    """Start long-polling. Blocks until interrupted."""
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

    token = get_settings().telegram_bot_token
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in .env (get one from @BotFather).")

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(reply_for_start(update.effective_chat.id))

    async def help_(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(reply_for_help(update.effective_chat.id))

    async def lang(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(reply_for_lang(update.effective_chat.id, (ctx.args or [None])[0]))

    async def delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(reply_for_delete(update.effective_chat.id))

    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        out = await asyncio.to_thread(reply_for_message, update.effective_chat.id, msg.text or msg.caption)
        await msg.reply_text(out, disable_web_page_preview=True)

    async def on_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat_id = update.effective_chat.id
        await msg.reply_text(replies.CHECKING.get(_pref_lang(chat_id), replies.CHECKING["en"]))
        tg_file, suffix = None, ""
        if msg.photo:
            tg_file, suffix = await msg.photo[-1].get_file(), ".jpg"
        elif msg.voice:
            tg_file, suffix = await msg.voice.get_file(), ".ogg"
        elif msg.audio:
            tg_file, suffix = await msg.audio.get_file(), Path(msg.audio.file_name or "a.mp3").suffix
        elif msg.video:
            tg_file, suffix = await msg.video.get_file(), ".mp4"
        elif msg.video_note:
            tg_file, suffix = await msg.video_note.get_file(), ".mp4"
        elif msg.document:
            tg_file, suffix = await msg.document.get_file(), Path(msg.document.file_name or "f.bin").suffix
        if tg_file is None:
            return
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"upload{suffix.lower()}"
            await tg_file.download_to_drive(path)
            out = await asyncio.to_thread(reply_for_message, chat_id, msg.caption, path)
        await msg.reply_text(out, disable_web_page_preview=True)

    app = Application.builder().token(token).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_))
    app.add_handler(CommandHandler("lang", lang))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE
                                   | filters.Document.ALL, on_media))
    log.info("bot_starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
