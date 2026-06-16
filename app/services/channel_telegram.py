"""Telegram channel: sender + long-poll receiver.

Sender:   send_message / send_typing via Bot API.
Receiver: start_polling() — runs as an asyncio task in the FastAPI lifespan.
          Handles /start {token} for invitation pairing and regular messages
          for Claude orchestration.
"""
import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


def _url(method: str) -> str:
    # Base host is configurable so the Bot API can be reached via a proxy on hosts
    # that block outbound api.telegram.org (e.g. Hugging Face Spaces). Set
    # TELEGRAM_API_BASE to a proxy URL (e.g. a Cloudflare Worker) that forwards to
    # https://api.telegram.org. Defaults to Telegram directly.
    base = (settings.telegram_api_base or "https://api.telegram.org").rstrip("/")
    return f"{base}/bot{settings.telegram_bot_token}/{method}"


# ── Sender ────────────────────────────────────────────────────────────────────

async def send_message(channel_id: str, text: str, parse_mode: str | None = None) -> bool:
    """Send a text message to a Telegram chat.

    channel_id is the string representation of the Telegram chat_id.
    parse_mode: "HTML", "MarkdownV2", or None (plain text).
    Returns True on success, False after exhausting retries.
    """
    payload: dict = {"chat_id": int(channel_id), "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(_url("sendMessage"), json=payload)
                resp.raise_for_status()
                return True
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning("telegram_send_retry channel_id=%s attempt=%d delay=%.1f error=%s", channel_id, attempt, delay, e)
                await asyncio.sleep(delay)
    logger.error("telegram_send_failed channel_id=%s attempts=%d error=%s", channel_id, MAX_RETRIES, last_error)
    return False


async def send_document(
    channel_id: str,
    document: str | bytes,
    filename: str = "document",
    caption: str | None = None,
    parse_mode: str | None = None,
) -> bool:
    """Send a file to a Telegram chat.

    document can be:
    - a public URL string  → Telegram downloads it directly
    - a file_id string     → reuse a previously uploaded file
    - bytes                → uploaded via multipart/form-data (max 50 MB)

    caption supports the same parse_mode values as send_message.
    Returns True on success, False on error.
    """
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            if isinstance(document, bytes):
                data: dict = {"chat_id": str(channel_id)}
                if caption:
                    data["caption"] = caption
                if parse_mode:
                    data["parse_mode"] = parse_mode
                resp = await client.post(
                    _url("sendDocument"),
                    data=data,
                    files={"document": (filename, document)},
                )
            else:
                payload: dict = {"chat_id": int(channel_id), "document": document}
                if caption:
                    payload["caption"] = caption
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                resp = await client.post(_url("sendDocument"), json=payload)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error("telegram_send_document_failed channel_id=%s error=%s", channel_id, e)
        return False


async def send_photo(
    channel_id: str,
    photo: str | bytes,
    filename: str = "photo",
    caption: str | None = None,
    parse_mode: str | None = None,
) -> bool:
    """Send an image inline to a Telegram chat (preview, not a file attachment).

    photo can be:
    - a public URL string  → Telegram downloads it directly
    - a file_id string     → reuse a previously uploaded photo
    - bytes                → uploaded via multipart/form-data

    Unlike send_document, this uses the sendPhoto method so the image renders
    inline. Telegram rejects photos over ~10 MB or with extreme aspect ratios;
    callers should fall back to send_document on failure.

    caption supports the same parse_mode values as send_message.
    Returns True on success, False on error.
    """
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            if isinstance(photo, bytes):
                data: dict = {"chat_id": str(channel_id)}
                if caption:
                    data["caption"] = caption
                if parse_mode:
                    data["parse_mode"] = parse_mode
                resp = await client.post(
                    _url("sendPhoto"),
                    data=data,
                    files={"photo": (filename, photo)},
                )
            else:
                payload: dict = {"chat_id": int(channel_id), "photo": photo}
                if caption:
                    payload["caption"] = caption
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                resp = await client.post(_url("sendPhoto"), json=payload)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error("telegram_send_photo_failed channel_id=%s error=%s", channel_id, e)
        return False


async def send_typing(channel_id: str, composing: bool = True) -> None:
    """Send a typing indicator to a Telegram chat. Best-effort, never raises."""
    if not composing:
        return  # Telegram has no "stop typing" action
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                _url("sendChatAction"),
                json={"chat_id": int(channel_id), "action": "typing"},
            )
    except Exception:
        pass


# ── Poller ───────────────────────────────────────────────────────────────────

async def _get_updates(offset: int, timeout: int = 30) -> list[dict]:
    """Long-poll getUpdates. Returns a list of update objects."""
    try:
        async with httpx.AsyncClient(timeout=timeout + 5.0) as client:
            resp = await client.get(
                _url("getUpdates"),
                params={"offset": offset, "timeout": timeout, "allowed_updates": '["message"]'},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", [])
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("telegram_get_updates_error error=%s", e)
        return []


def _allowed(chat_id: str) -> bool:
    """True if this chat is allowed to use the bot.

    ALLOWED_TELEGRAM_IDS empty → everyone (local use). Set it on any public/hosted
    bot so strangers can't reach the owner's Gmail/agent.
    """
    raw = (settings.allowed_telegram_ids or "").strip()
    if not raw:
        return True
    allowed = {i.strip() for i in raw.split(",") if i.strip()}
    return chat_id in allowed


async def _handle_update(update: dict) -> None:
    """Dispatch a single Telegram update to the orchestrator or pairing flow."""
    # Import here to avoid circular imports at module load time
    from app.db.async_session import AsyncSessionLocal
    from app.services.aura_agent import handle_aura_message

    message = update.get("message")
    if not message:
        return

    chat_id = str(message["chat"]["id"])
    text = message.get("text", "").strip()
    message_id = str(message["message_id"])

    if not _allowed(chat_id):
        # Stranger — ignore silently (no reply, no Gmail access, no quota spent).
        logger.warning("telegram_unauthorized_chat chat_id=%s", chat_id)
        return

    if not text:
        # Ignore non-text / whitespace-only messages
        return

    # /start — greet (no invitation/pairing needed for the personal assistant)
    if text.startswith("/start"):
        await send_message(
            chat_id,
            "Hola 👋 Soy Aura, tu asistente personal. Escribime lo que necesites. "
            "Pronto voy a poder ver tu agenda, correo y GitHub.",
        )
        return

    # Regular message → Aura (local Ollama + SQLite memory)
    async with AsyncSessionLocal() as db:
        await handle_aura_message(chat_id, text, message_id, db)


async def start_polling() -> None:
    """Long-poll loop. Run as an asyncio.Task in the FastAPI lifespan.

    Exits cleanly on CancelledError (lifespan shutdown).
    """
    if not settings.telegram_bot_token:
        logger.info("telegram_polling_disabled no token configured")
        return

    logger.info("telegram_polling_start")
    offset = 0
    while True:
        try:
            updates = await _get_updates(offset)
            for update in updates:
                update_id = update.get("update_id", 0)
                offset = max(offset, update_id + 1)
                try:
                    await _handle_update(update)
                except Exception:
                    logger.exception("telegram_update_error update_id=%d", update_id)
        except asyncio.CancelledError:
            logger.info("telegram_polling_stopped")
            raise
        except Exception:
            logger.exception("telegram_polling_unexpected_error")
            await asyncio.sleep(5)  # back off before retrying
