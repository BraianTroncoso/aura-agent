#!/usr/bin/env bash
# Aura — personal assistant, light infra (SQLite + Ollama, no Docker).
set -a; [ -f .env ] && . ./.env; set +a
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8060}" --reload
