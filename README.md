<div align="center">

# 🌙 Aura

**Your personal AI agent — like _Her_, in your pocket.**

Chat with it on Telegram. It runs on a **local** model, so your data never leaves
your machine — no API keys, no per-token cost. It remembers your conversations and
is built to grow into your day: email, calendar, GitHub, your daily standup.

[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-black.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-black.svg)](https://fastapi.tiangolo.com/)
[![Local-first](https://img.shields.io/badge/local--first-Ollama-black.svg)](https://ollama.com/)

</div>

---

## Why Aura

Most "AI assistants" ship your messages to someone else's cloud and bill you per
token. Aura flips that: it's **local-first and private by default**. The brain is
a small model running on your own machine via [Ollama](https://ollama.com), the
memory is a single SQLite file, and the interface is a Telegram chat you already
have open all day.

- 🔒 **Private** — your messages and email stay on your machine. No key required.
- 💸 **Free to run** — local model, zero per-token cost.
- 🪶 **Light infra** — SQLite + Ollama. No Docker, no Postgres, no Redis to start.
- 🔌 **Pluggable brain** — one env var swaps Ollama for Claude or GPT.
- 🧠 **It remembers** — every chat is stored and replayed as context.
- 🛠️ **Hackable** — a small, readable FastAPI codebase made to fork.

## What it does today

- 💬 **Chat over Telegram** — talk to Aura from your phone; it keeps context.
- 📬 **Gmail** — read your latest emails and send replies, grounded in real inbox
  data (IMAP read + SMTP send via a Gmail App Password — no Google Cloud setup).
- 🧠 **Memory** — conversations persist in SQLite so Aura picks up where you left off.

## Roadmap

- 📅 **Calendar** — your day at a glance, reminders, focus blocks.
- 🐙 **GitHub** — assigned PRs/issues, pending reviews, what's blocked.
- ✅ **Tasks + standup** — a daily standup (yesterday / today / blockers).
- 🔊 Voice notes, more channels (WhatsApp/Slack).

## Quickstart

```bash
# 1. Clone + install
git clone https://github.com/BraianTroncoso/aura-agent.git
cd aura-agent
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 2. Get a local model (private, no key, no cost)
#    Install Ollama from https://ollama.com, then:
ollama pull qwen2.5:3b-instruct

# 3. Configure
cp .env.example .env        # set TELEGRAM_BOT_TOKEN (from @BotFather)

# 4. Run
./run.sh                    # http://localhost:8060/health
```

Message your bot on Telegram and Aura replies. Want email too? Add a
[Gmail App Password](https://myaccount.google.com/apppasswords) to `.env`
(`GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD`) and ask it to read or send mail.

## Swap the brain

Aura defaults to a local Ollama model. To use a hosted one, edit `.env`:

```bash
# Anthropic Claude
AURA_API_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
AURA_API_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

No call sites change — only the env var.

## Architecture

```
Telegram ──▶ FastAPI (app/main.py)
                │
                ▼
        aura_agent.py ──▶ LLM gateway (Ollama / Claude / GPT)
                │              (aura_runtime/core/claude_agent.py)
                ├──▶ Gmail (IMAP read / SMTP send)
                └──▶ SQLite memory (conversations + messages)
```

| Path | What it is |
|------|------------|
| `app/main.py` | FastAPI app; boots tables and Telegram polling |
| `app/services/aura_agent.py` | The agent: routes a message to chat / read-email / send-email |
| `app/services/channel_telegram.py` | Telegram send + long-poll receive |
| `app/services/gmail_imap.py` | Gmail read (IMAP) and send (SMTP) |
| `app/services/llm_service.py` | Swappable text-completion helper |
| `aura_runtime/core/claude_agent.py` | Multi-provider LLM gateway (Ollama/Claude/GPT/Gemini) |
| `app/models/conversation.py` | The memory schema |

Stack: **SQLite** (memory) · **Ollama** (brain) · **Telegram** (channel) ·
**FastAPI** (runtime). Scales 1:1 — point `DATABASE_URL` at Postgres when you outgrow SQLite.

## Built with MyJarbis

Aura was designed and built with **[MyJarbis](https://github.com/braiantroncoso/myjarbis)** —
a per-project memory layer for AI coding agents. MyJarbis gives the agent a
durable map of the project (modules, decisions, context, sessions) so work
continues across sessions instead of starting from a blank page each time. This
repo's clean, modular structure is a direct result of building with it.

## Contributing

PRs and forks welcome — Aura is meant to be hacked on. Open an issue to discuss a
capability you'd like to add (calendar, GitHub, a new channel), or fork it and
make it yours.

## License

[MIT](LICENSE) © Braian Troncoso
