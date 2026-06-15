# Aura — container image for always-on hosting (Koyeb, Fly, a VPS, etc.)
FROM python:3.12-slim

WORKDIR /app

# Core deps + optional drivers used in hosted deploys:
#   asyncpg + psycopg2-binary → Postgres (set DATABASE_URL=postgresql://...)
#   google-generativeai       → Gemini brain (AURA_API_PROVIDER=gemini)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt asyncpg psycopg2-binary google-generativeai

COPY . .

ENV PYTHONUNBUFFERED=1

# Hosts inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
