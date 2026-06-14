"""Application settings — all configuration comes from environment / .env.

Nothing sensitive is hard-coded: tokens, passwords and the database URL are read
from the environment. Copy ``.env.example`` to ``.env`` and fill in what you use.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    # SQLite by default (zero infra). Point at Postgres to scale: it runs the
    # same models unchanged (postgresql+asyncpg://user:pass@host/db).
    database_url: str = "sqlite:///./aura.db"

    # ── Telegram (the chat interface) ─────────────────────────────────────────
    telegram_bot_token: str = ""           # from @BotFather — empty = Telegram off
    telegram_polling_enabled: bool = True  # long-poll; set False to use a webhook

    # ── Gmail over IMAP/SMTP (App Password, no OAuth) ─────────────────────────
    gmail_address: str = ""
    gmail_app_password: str = ""

    # ── LLM provider for the llm_service helper ───────────────────────────────
    # stub (default, offline) | ollama (local) | anthropic | openai
    llm_provider: str = "stub"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b-instruct"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # ── Limits ────────────────────────────────────────────────────────────────
    llm_max_tokens: int = 1024
    claude_timeout: int = 180

    class Config:
        env_file = ".env"
        extra = "ignore"  # tolerate extra env keys


settings = Settings()
