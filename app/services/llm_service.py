"""llm_service.py — swappable LLM text-completion service.

Single entry point (`complete`) for every direct LLM call in the backend.
The provider is chosen by the `LLM_PROVIDER` env var so you can run the whole
app locally with zero API keys and zero cost, and switch to a real model later
without touching call sites.

Providers
---------
- ``stub``      (default) — no dependencies, no network, no cost. Returns a
                 deterministic, plausible response. Ideal for local testing /
                 demos: every AI feature responds instead of erroring.
- ``ollama``    — local models via Ollama (http://localhost:11434). No API key,
                 low compute with a small model (e.g. ``qwen2.5:0.5b``).
- ``anthropic`` — Claude via the Anthropic API (needs ``ANTHROPIC_API_KEY``).
- ``openai``    — GPT via the OpenAI API (needs ``OPENAI_API_KEY``).

Switch provider:  set ``LLM_PROVIDER=ollama`` (or anthropic/openai) in ``.env``.
"""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def complete(
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 2048,
    timeout: int = 120,
) -> str:
    """Return the model's text completion for ``prompt``.

    Never raises for the local providers — on any error it falls back to the
    stub so the calling feature degrades gracefully instead of crashing.
    """
    provider = (settings.llm_provider or "stub").lower()
    try:
        if provider == "ollama":
            return _ollama(prompt, system_prompt, max_tokens, timeout)
        if provider == "anthropic":
            return _anthropic(prompt, system_prompt, max_tokens)
        if provider == "openai":
            return _openai(prompt, system_prompt, max_tokens)
        return _stub(prompt, system_prompt)
    except Exception:
        logger.exception("llm_service provider=%s failed — falling back to stub", provider)
        return _stub(prompt, system_prompt)


# ── stub (default, no deps) ────────────────────────────────────────────────────

def _wants_json(prompt: str, system_prompt: str | None) -> bool:
    blob = f"{system_prompt or ''}\n{prompt}".lower()
    return "json" in blob


def _stub(prompt: str, system_prompt: str | None) -> str:
    """Deterministic offline response.

    If the caller asked for JSON (KG extraction, entity linker, etc.) we return
    an empty JSON object so their tolerant parsers degrade to "nothing found".
    Otherwise we return a short, readable analyst-style answer so chat-like
    features show a sensible reply.
    """
    if _wants_json(prompt, system_prompt):
        return "{}"
    snippet = " ".join(prompt.split())[:280]
    return (
        "[Respuesta generada en modo local de prueba (LLM_PROVIDER=stub) — sin "
        "costo ni API key]. Para respuestas reales, configurá LLM_PROVIDER=ollama "
        "(local) o anthropic/openai con su API key.\n\n"
        f"Resumen de tu consulta: {snippet}"
    )


# ── ollama (local, no key) ─────────────────────────────────────────────────────

def _ollama(prompt: str, system_prompt: str | None, max_tokens: int, timeout: int) -> str:
    import httpx

    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if system_prompt:
        payload["system"] = system_prompt
    url = settings.ollama_base_url.rstrip("/") + "/api/generate"
    resp = httpx.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("response", "")


# ── anthropic (Claude API) ─────────────────────────────────────────────────────

def _anthropic(prompt: str, system_prompt: str | None, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=system_prompt or "",
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")


# ── openai (GPT API) ───────────────────────────────────────────────────────────

def _openai(prompt: str, system_prompt: str | None, max_tokens: int) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=max_tokens,
        messages=messages,
    )
    return resp.choices[0].message.content or ""
