"""Aura — personal AI agent. FastAPI app.

Boots on SQLite with no heavy infra. On startup it creates the tables and, if a
Telegram bot token is set, starts long-polling so you can chat with Aura right
away. The brain is a local Ollama model by default (private, no API key, no cost).

Two ways to receive Telegram messages:
  - Polling (default, local): the app long-polls getUpdates.
  - Webhook (hosted): set TELEGRAM_POLLING_ENABLED=false and point Telegram's
    setWebhook at POST /telegram/webhook. Preferred on hosts where outbound
    long-poll connections are unreliable (e.g. Hugging Face Spaces).
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from app.config import settings
from app.db.session import Base, engine

# Register models so SQLAlchemy creates their tables on startup.
import app.models  # noqa: F401,E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    logger.info("tables_ready db=%s", settings.database_url)

    tg_task = None
    if settings.telegram_bot_token and settings.telegram_polling_enabled:
        from app.services.channel_telegram import start_polling
        tg_task = asyncio.create_task(start_polling())
        logger.info("telegram_polling_enabled")
    else:
        logger.info("telegram_disabled (set TELEGRAM_BOT_TOKEN to enable)")

    yield

    if tg_task is not None:
        tg_task.cancel()
        with suppress(asyncio.CancelledError):
            await tg_task


app = FastAPI(title="Aura", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "telegram": bool(settings.telegram_bot_token),
        "llm_provider": settings.llm_provider,
    }


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    """Receive a Telegram update via webhook and process it in the background.

    Returns 200 immediately so Telegram doesn't retry while the model thinks.
    If TELEGRAM_WEBHOOK_SECRET is set, the matching header (configured on
    setWebhook) is required.
    """
    if settings.telegram_webhook_secret and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="bad secret token")
    update = await request.json()
    from app.services.channel_telegram import _handle_update
    background_tasks.add_task(_handle_update, update)
    return {"ok": True}
