<div align="center">

# 🌙 Aura

**Your personal AI agent — like _Her_, in your pocket.**

Chat with it on Telegram. Run it **100% locally** (private, your data never leaves your
machine) **or host it 24/7 for free** — no server of your own, **no credit card**. It
remembers your conversations and grows into your day: email, calendar, GitHub, standup.

[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-black.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-black.svg)](https://fastapi.tiangolo.com/)
[![Deploy: free 24/7](https://img.shields.io/badge/deploy-free%2024%2F7%20·%20no%20card-brightgreen.svg)](#-host-it-247--free-no-credit-card)

</div>

---

## Why Aura

Most "AI assistants" ship your messages to someone else's cloud and bill you per token.
Aura flips that — and gives you two honest ways to run it:

- 🏠 **Local & private** — the brain is a small model on your own machine via
  [Ollama](https://ollama.com); memory is a single SQLite file. No keys, no cost, nothing leaves your box.
- ☁️ **Free 24/7 in the cloud** — a fully **free-tier, no-credit-card** stack (Hugging Face +
  Gemini + Neon + Cloudflare) so Aura lives online without your PC ever being on. See
  [Host it 24/7](#-host-it-247--free-no-credit-card).

Either way:

- 🔌 **Pluggable brain** — one env var swaps Ollama ↔ Gemini ↔ Claude ↔ GPT.
- 🧠 **It remembers** — every chat is stored and replayed as context (SQLite or Postgres).
- 🪶 **Light infra** — starts on SQLite, no Docker/Redis required.
- 🛠️ **Hackable** — a small, readable FastAPI codebase made to fork.

## What it does today

- 💬 **Chat over Telegram** — talk to Aura from your phone; it keeps context.
- 📬 **Gmail** — read your latest emails and send replies, grounded in real inbox data
  (IMAP read + SMTP send via a Gmail App Password — no Google Cloud setup).
- 🧠 **Memory** — conversations persist (SQLite locally, Postgres in the cloud).

## Roadmap

- 📅 **Calendar** · 🐙 **GitHub** (assigned PRs/issues) · ✅ **Tasks + daily standup** ·
  🔊 voice notes · more channels (WhatsApp/Slack).

---

## Quickstart (local)

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

Aura's LLM gateway is provider-agnostic — change one env var, no code changes:

```bash
# Google Gemini (free tier, no credit card) — recommended for cloud hosting
AURA_API_PROVIDER=gemini   ·  GEMINI_API_KEY=...        ·  AURA_API_MODEL=gemini-3.5-flash
# Anthropic Claude
AURA_API_PROVIDER=anthropic ·  ANTHROPIC_API_KEY=sk-ant-...
# OpenAI
AURA_API_PROVIDER=openai    ·  OPENAI_API_KEY=sk-...
# Local Ollama (default)
AURA_API_PROVIDER=ollama    ·  OLLAMA_BASE_URL=http://localhost:11434
```

---

## 🚀 Host it 24/7 — free, no credit card

You don't need a server or a credit card. Aura runs online for **$0/month** on free tiers,
so it answers even when your computer is off:

| Layer | Role | Service | Cost |
|------|------|---------|------|
| 🖥️ **Server** | runs Aura 24/7 | [Hugging Face Spaces](https://huggingface.co/spaces) (Docker) | free · no card |
| 🧠 **Brain** | the LLM | [Google Gemini](https://aistudio.google.com/apikey) | free · no card |
| 💾 **Memory** | the database | [Neon](https://neon.tech) (serverless Postgres) | free · no card |
| 📨 **Relay** | reaches Telegram | [Cloudflare Workers](https://workers.cloudflare.com) | free · no card |
| 💬 **Face** | where you chat | Telegram bot ([@BotFather](https://t.me/BotFather)) | free |

```
   You (Telegram)
        │  message
        ▼
  Hugging Face Space  ──►  Gemini   (brain: writes the reply)
   (runs Aura 24/7)   ──►  Neon     (memory: stores the chat)
        │  reply
        ▼
  Cloudflare Worker  ──►  Telegram  ──►  You
   (outbound relay)
```

### Steps

1. **Keys** — grab a [Gemini API key](https://aistudio.google.com/apikey), a
   [Neon](https://neon.tech) Postgres connection string, and a Telegram bot token from
   [@BotFather](https://t.me/BotFather). All free, no card.
2. **Deploy** — create a Hugging Face **Space** (SDK: *Docker*) and push this repo to it
   (it builds from the included `Dockerfile`, `app_port: 8000`).
3. **Config** — in the Space → *Settings → Variables and secrets*, add as **secrets**:
   `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `GMAIL_APP_PASSWORD`, `DATABASE_URL` (the Neon URL);
   and as **variables**: `GMAIL_ADDRESS`, `AURA_LLM_MODE=api`, `AURA_API_PROVIDER=gemini`,
   `AURA_API_MODEL=gemini-3.5-flash`, `TELEGRAM_POLLING_ENABLED=false`.
   > ⚠️ Put credentials under **Secrets** (encrypted), never **Variables** (public on a public Space).
4. **Webhook** — point Telegram at your Space (it *pushes* messages instead of Aura polling):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
        --data-urlencode "url=https://<your-space>.hf.space/telegram/webhook"
   ```

### ⚠️ The Hugging Face + Telegram gotcha (and the fix)

Hugging Face Spaces **block outbound connections to `api.telegram.org`** (they reach Google,
Neon, etc. fine — just not Telegram). So:

- **Receiving** works via **webhook** (Telegram → your Space is *inbound*).
- **Sending** replies fails (your Space → Telegram is *outbound*, blocked).

The fix is a tiny **Cloudflare Worker** that relays outbound Telegram calls. Create a Worker
with this code, deploy it, then set the Space variable `TELEGRAM_API_BASE` to your Worker URL:

```js
export default {
  async fetch(request) {
    const url = new URL(request.url);
    return fetch("https://api.telegram.org" + url.pathname + url.search, request);
  }
}
```

Aura builds its Bot API URLs from `TELEGRAM_API_BASE` (default `https://api.telegram.org`), so
this is config-only — no code change. On hosts that *don't* block Telegram, skip the Worker entirely.

> 💡 Free tiers sleep when idle — a free pinger (e.g. [cron-job.org](https://cron-job.org))
> hitting `https://<your-space>.hf.space/health` every ~10 min keeps the Space awake.

---

## Architecture

```
Telegram ──▶ FastAPI (app/main.py)            polling  (local)  ·  webhook (hosted)
                │
                ▼
        aura_agent.py ──▶ LLM gateway (Ollama / Gemini / Claude / GPT)
                │              (aura_runtime/core/claude_agent.py)
                ├──▶ Gmail (IMAP read / SMTP send)
                └──▶ memory (SQLite local · Postgres in the cloud)
```

| Path | What it is |
|------|------------|
| `app/main.py` | FastAPI app; boots tables, Telegram polling **and** the `/telegram/webhook` route |
| `app/services/aura_agent.py` | The agent: routes a message to chat / read-email / send-email |
| `app/services/channel_telegram.py` | Telegram send + receive; Bot API base is configurable (`TELEGRAM_API_BASE`) |
| `app/services/gmail_imap.py` | Gmail read (IMAP) and send (SMTP) |
| `aura_runtime/core/claude_agent.py` | Multi-provider LLM gateway (Ollama/Gemini/Claude/GPT) |
| `app/models/conversation.py` | The memory schema |

Scales 1:1 — point `DATABASE_URL` at Postgres when you outgrow SQLite.

## Built with MyJarbis

Aura was designed and built with **[MyJarbis](https://github.com/braiantroncoso/myjarbis)** —
a per-project memory layer for AI coding agents. MyJarbis gives the agent a durable map of the
project (modules, decisions, context, sessions) so work continues across sessions instead of
starting from a blank page each time. This repo's clean, modular structure is a direct result.

## Contributing

PRs and forks welcome — Aura is meant to be hacked on. Open an issue to discuss a capability
you'd like to add (calendar, GitHub, a new channel), or fork it and make it yours.

## License

[MIT](LICENSE) © Braian Troncoso
