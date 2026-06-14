"""
LLM gateway para pipelines de Aura.

Soporta dos backends según ``AURA_LLM_MODE``:

  - ``claude-p`` (default): subprocess del CLI de Claude Code — usa la
    suscripción, sin coste por token.
  - ``api``: SDK multi-proveedor — anthropic, openai o gemini,
    facturado por token, sin dependencia del CLI.

Env vars relevantes:
  AURA_LLM_MODE       "claude-p" | "api"  (default: "claude-p")
  AURA_API_PROVIDER   "anthropic" | "openai" | "gemini"  (default: "anthropic")
                        Solo aplica en modo api.
  AURA_API_MODEL      Modelo a usar en modo api. Default varía por provider
                        (claude-sonnet-4-6 / gpt-4o / gemini-2.0-flash).
  AURA_CLAUDE_MODEL   Modelo a usar en modo claude-p. Se pasa al CLI vía
                        --model. Solo acepta modelos de Anthropic.
  AURA_MAX_TOKENS     Max tokens de respuesta por defecto (default: 4096).
                        Override por llamada con el arg ``max_tokens``. Solo
                        aplica en modo api (el CLI no expone un límite).
  AURA_LLM_TIMEOUT    Timeout por defecto en segundos (default: 300). Override
                        por llamada con el arg ``timeout``. Aplica en ambos modos.
  ANTHROPIC_API_KEY     Requerido cuando provider=anthropic
  OPENAI_API_KEY        Requerido cuando provider=openai
  GEMINI_API_KEY        Requerido cuando provider=gemini

Nota: ``max_tokens`` y ``temperature`` solo tienen efecto en modo api; el CLI
de claude-p no expone esos parámetros y los ignora.

Uso:
    from aura_runtime.core.claude_agent import run_claude_p

    result = run_claude_p(prompt="Clasifica este email...", system_prompt="Eres un ...")
    text = result.text
"""
from __future__ import annotations

import base64 as _b64
import fcntl
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

AURA_LLM_MODE = os.environ.get('AURA_LLM_MODE', 'claude-p')
AURA_API_PROVIDER = os.environ.get('AURA_API_PROVIDER', 'anthropic').lower()
DEFAULT_TIMEOUT = int(os.environ.get('AURA_LLM_TIMEOUT', '300'))

_DEFAULT_MODELS = {
    'anthropic': 'claude-sonnet-4-6',
    'openai': 'gpt-4o',
    'gemini': 'gemini-2.0-flash',
    'ollama': 'qwen2.5:3b-instruct',
}

# Local Ollama endpoint. From inside Docker the host runs Ollama, so default to
# host.docker.internal; override with AURA_OLLAMA_URL / OLLAMA_BASE_URL.
AURA_OLLAMA_URL = (
    os.environ.get('AURA_OLLAMA_URL')
    or os.environ.get('OLLAMA_BASE_URL')
    or 'http://host.docker.internal:11434'
)


def _resolve_model() -> str:
    explicit = os.environ.get('AURA_API_MODEL', '').strip()
    if explicit:
        return explicit
    return _DEFAULT_MODELS.get(AURA_API_PROVIDER, 'claude-sonnet-4-6')


def _resolve_max_tokens(explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    return int(os.environ.get('AURA_MAX_TOKENS', '4096'))

MAX_CONCURRENT_SESSIONS = 4
_thread_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_SESSIONS)
_LOCK_DIR = Path(tempfile.gettempdir()) / 'aura_claude_locks'

_SENSITIVE_KEYS = frozenset({
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "DATABASE_URL",
    "JWT_SECRET_KEY",
    "SESSION_SECRET",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_CLIENT_ID",
    "GMAIL_APP_PASSWORD",
})


@dataclass
class ClaudeResult:
    text: str
    cost_usd: float | None = None
    duration_ms: int | None = None
    is_error: bool = False


def _build_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _SENSITIVE_KEYS}
    env.setdefault("HOME", str(Path.home()))
    return env


class _SessionSlot:
    """Cross-process concurrency limiter using file locks."""

    def __init__(self, timeout: float):
        self._timeout = timeout
        self._fd: int | None = None
        self._path: Path | None = None

    def __enter__(self):
        _thread_semaphore.acquire(timeout=self._timeout)
        try:
            _LOCK_DIR.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + self._timeout
            while time.monotonic() < deadline:
                for i in range(MAX_CONCURRENT_SESSIONS):
                    path = _LOCK_DIR / f'slot_{i}.lock'
                    try:
                        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY)
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        self._fd = fd
                        self._path = path
                        return self
                    except (OSError, IOError):
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                time.sleep(0.5)
            log.warning("_SessionSlot: no slot available after %.0fs, proceeding anyway", self._timeout)
            return self
        except Exception:
            _thread_semaphore.release()
            raise

    def __exit__(self, *exc):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
        _thread_semaphore.release()


def _flatten_messages(messages: list[dict]) -> str:
    """Aplana una lista de mensajes multi-turno a un string para claude -p."""
    parts: list[str] = []
    for msg in messages:
        role = msg.get('role', 'user').capitalize()
        content = msg.get('content', '')
        parts.append(f"{role}: {content}")
    return '\n\n'.join(parts)


def run_claude_p(
    *,
    prompt: str = '',
    messages: list[dict] | None = None,
    system_prompt: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> ClaudeResult:
    """Ejecuta una llamada LLM según ``AURA_LLM_MODE``.

    En modo ``claude-p``: lanza ``claude -p`` como subprocess síncrono.
    En modo ``api``: llama al SDK del proveedor configurado.

    Args:
        prompt: Texto del prompt (single-turn).
        messages: Lista de mensajes multi-turno (opcional, tiene prioridad
            sobre prompt). Formato: [{"role": "user"|"assistant", "content": str}].
        system_prompt: System prompt opcional.
        timeout: Segundos máximos de espera (default: ``AURA_LLM_TIMEOUT``).
        max_tokens: Límite de tokens de respuesta. Solo modo api (default:
            ``AURA_MAX_TOKENS``); en claude-p se ignora.
        temperature: Temperatura de sampling. Solo modo api; en claude-p se
            ignora. ``None`` usa el default del proveedor.

    Returns:
        ClaudeResult con el texto de respuesta y métricas opcionales.
    """
    if messages:
        effective_prompt = _flatten_messages(messages)
    else:
        effective_prompt = prompt

    if not effective_prompt.strip():
        return ClaudeResult(text="", is_error=True)

    if AURA_LLM_MODE == 'api':
        return _run_api_text(
            prompt=effective_prompt, messages=messages,
            system_prompt=system_prompt, timeout=timeout,
            max_tokens=max_tokens, temperature=temperature,
        )

    cmd = [
        "claude",
        "--dangerously-skip-permissions",
        "--output-format", "json",
    ]

    _model_override = os.environ.get('AURA_CLAUDE_MODEL', '').strip()
    if _model_override:
        cmd += ["--model", _model_override]

    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]

    cmd += ["-p", effective_prompt]

    env = _build_env()

    log.info("run_claude_p prompt_len=%d system_len=%d timeout=%d",
             len(effective_prompt), len(system_prompt or ""), timeout)

    with _SessionSlot(timeout=timeout):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=str(Path.home()),
            )
        except subprocess.TimeoutExpired:
            log.error("run_claude_p timeout after %ds", timeout)
            return ClaudeResult(text="", is_error=True)

    if result.returncode != 0:
        log.error("run_claude_p failed rc=%d stderr=%s",
                  result.returncode, result.stderr[:500])
        return ClaudeResult(text="", is_error=True)

    stdout = result.stdout.strip()
    try:
        data = json.loads(stdout)
        cost_raw = data.get("cost_usd")
        return ClaudeResult(
            text=data.get("result", stdout),
            cost_usd=float(cost_raw) if cost_raw is not None else None,
            duration_ms=int(data.get("duration_ms", 0)) or None,
        )
    except (json.JSONDecodeError, ValueError):
        return ClaudeResult(text=stdout)


def run_claude_p_with_file(
    *,
    file_bytes: bytes,
    file_ext: str,
    prompt: str,
    system_prompt: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> ClaudeResult:
    """Ejecuta una llamada LLM con un fichero adjunto.

    En modo ``claude-p``: escribe a temporal y referencia desde el prompt.
    En modo ``api``: envía el fichero como content block base64 (imagen/PDF)
    o como texto inline (otros formatos).

    Args:
        file_bytes: Contenido binario del fichero.
        file_ext: Extensión con punto (ej. ``.pdf``, ``.png``).
        prompt: Instrucciones para Claude sobre qué hacer con el fichero.
        system_prompt: System prompt opcional.
        timeout: Segundos máximos de espera.

    Returns:
        ClaudeResult con el texto extraído.
    """
    if AURA_LLM_MODE == 'api':
        return _run_api_vision(
            file_bytes=file_bytes, file_ext=file_ext,
            prompt=prompt, system_prompt=system_prompt, timeout=timeout,
        )

    with tempfile.NamedTemporaryFile(
        suffix=file_ext, prefix="aura-vision-", delete=False,
    ) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        full_prompt = f"Lee el fichero {tmp_path} y luego:\n\n{prompt}"
        return run_claude_p(
            prompt=full_prompt,
            system_prompt=system_prompt,
            timeout=timeout,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Multi-provider API backend ────────────────────────────────────────

_IMAGE_EXTS = frozenset({'png', 'jpg', 'jpeg', 'gif', 'webp'})
_MEDIA_TYPES = {
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'gif': 'image/gif', 'webp': 'image/webp', 'pdf': 'application/pdf',
}


def _run_api_text(*, prompt: str, messages: list[dict] | None = None,
                  system_prompt: str | None = None, timeout: int = DEFAULT_TIMEOUT,
                  max_tokens: int | None = None,
                  temperature: float | None = None) -> ClaudeResult:
    provider = AURA_API_PROVIDER
    if provider == 'ollama':
        return _run_ollama_text(prompt=prompt, messages=messages,
                                system_prompt=system_prompt, timeout=timeout,
                                max_tokens=max_tokens, temperature=temperature)
    if provider == 'openai':
        return _run_openai_text(prompt=prompt, messages=messages,
                                system_prompt=system_prompt, timeout=timeout,
                                max_tokens=max_tokens, temperature=temperature)
    if provider == 'gemini':
        return _run_gemini_text(prompt=prompt, messages=messages,
                                system_prompt=system_prompt, timeout=timeout,
                                max_tokens=max_tokens, temperature=temperature)
    return _run_anthropic_text(prompt=prompt, messages=messages,
                               system_prompt=system_prompt, timeout=timeout,
                               max_tokens=max_tokens, temperature=temperature)


def _run_ollama_text(*, prompt: str, messages: list[dict] | None = None,
                     system_prompt: str | None = None, timeout: int = DEFAULT_TIMEOUT,
                     max_tokens: int | None = None,
                     temperature: float | None = None) -> ClaudeResult:
    """Local Ollama backend (no API key, no cost). Uses the /api/chat endpoint."""
    import httpx

    model = _resolve_model()
    api_messages: list[dict] = []
    if system_prompt:
        api_messages.append({'role': 'system', 'content': system_prompt})
    if messages:
        api_messages.extend(messages)
    else:
        api_messages.append({'role': 'user', 'content': prompt})

    # Cap reply length so chat stays snappy on CPU; callers can override.
    options: dict = {'num_predict': max_tokens or int(os.environ.get('AURA_NUM_PREDICT', '256'))}
    if temperature is not None:
        options['temperature'] = temperature

    # keep_alive keeps the model resident in RAM so there's no cold reload
    # between messages (key for a full-time personal assistant).
    keep_alive = os.environ.get('AURA_OLLAMA_KEEP_ALIVE', '30m')
    payload = {'model': model, 'messages': api_messages, 'stream': False,
               'options': options, 'keep_alive': keep_alive}
    log.info("_run_ollama_text model=%s url=%s prompt_len=%d", model, AURA_OLLAMA_URL, len(prompt))
    try:
        t0 = time.monotonic()
        resp = httpx.post(f"{AURA_OLLAMA_URL.rstrip('/')}/api/chat", json=payload, timeout=float(timeout))
        resp.raise_for_status()
        duration_ms = int((time.monotonic() - t0) * 1000)
        text = resp.json().get('message', {}).get('content', '')
        return ClaudeResult(text=text, duration_ms=duration_ms)
    except Exception as exc:
        log.error("_run_ollama_text failed: %s", exc)
        return ClaudeResult(text="", is_error=True)


def _run_api_vision(*, file_bytes: bytes, file_ext: str, prompt: str,
                    system_prompt: str | None = None,
                    timeout: int = 300) -> ClaudeResult:
    provider = AURA_API_PROVIDER
    if provider == 'openai':
        return _run_openai_vision(file_bytes=file_bytes, file_ext=file_ext,
                                  prompt=prompt, system_prompt=system_prompt,
                                  timeout=timeout)
    if provider == 'gemini':
        log.error("_run_api_vision: gemini vision not implemented")
        return ClaudeResult(text='', is_error=True)
    return _run_anthropic_vision(file_bytes=file_bytes, file_ext=file_ext,
                                 prompt=prompt, system_prompt=system_prompt,
                                 timeout=timeout)


# ── Anthropic ────────────────────────────────────────────────────────

def _run_anthropic_text(*, prompt: str, messages: list[dict] | None = None,
                        system_prompt: str | None = None, timeout: int = DEFAULT_TIMEOUT,
                        max_tokens: int | None = None,
                        temperature: float | None = None) -> ClaudeResult:
    import anthropic

    model = _resolve_model()
    max_tokens = _resolve_max_tokens(max_tokens)

    client = anthropic.Anthropic(
        api_key=os.environ.get('ANTHROPIC_API_KEY', ''),
        timeout=float(timeout),
    )
    api_messages = messages if messages else [{'role': 'user', 'content': prompt}]
    kwargs: dict = {
        'model': model,
        'max_tokens': max_tokens,
        'messages': api_messages,
    }
    if system_prompt:
        kwargs['system'] = system_prompt
    if temperature is not None:
        kwargs['temperature'] = temperature

    log.info("_run_anthropic_text model=%s prompt_len=%d system_len=%d",
             model, len(prompt), len(system_prompt or ""))

    try:
        t0 = time.monotonic()
        resp = client.messages.create(**kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)
        text = resp.content[0].text if resp.content else ''
        return ClaudeResult(text=text, duration_ms=duration_ms)
    except Exception as exc:
        log.error("_run_anthropic_text failed: %s", exc)
        return ClaudeResult(text='', is_error=True)


def _run_anthropic_vision(*, file_bytes: bytes, file_ext: str, prompt: str,
                          system_prompt: str | None = None,
                          timeout: int = 300) -> ClaudeResult:
    import anthropic

    model = _resolve_model()
    max_tokens = int(os.environ.get('AURA_MAX_TOKENS', '4096'))

    ext = file_ext.lower().lstrip('.')
    b64_data = _b64.standard_b64encode(file_bytes).decode('ascii')

    content_blocks: list[dict] = []
    if ext in _IMAGE_EXTS:
        content_blocks.append({
            'type': 'image',
            'source': {'type': 'base64', 'media_type': _MEDIA_TYPES.get(ext, f'image/{ext}'), 'data': b64_data},
        })
    elif ext == 'pdf':
        content_blocks.append({
            'type': 'document',
            'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': b64_data},
        })
    else:
        try:
            text_content = file_bytes.decode('utf-8', errors='replace')
            prompt = f'Contenido del fichero ({file_ext}):\n\n{text_content[:8000]}\n\n{prompt}'
        except Exception:
            prompt = f'[fichero binario {file_ext}]\n\n{prompt}'
    content_blocks.append({'type': 'text', 'text': prompt})

    client = anthropic.Anthropic(
        api_key=os.environ.get('ANTHROPIC_API_KEY', ''),
        timeout=float(timeout),
    )
    kwargs: dict = {
        'model': model,
        'max_tokens': max_tokens,
        'messages': [{'role': 'user', 'content': content_blocks}],
    }
    if system_prompt:
        kwargs['system'] = system_prompt

    log.info("_run_anthropic_vision model=%s ext=%s size=%d", model, ext, len(file_bytes))

    try:
        t0 = time.monotonic()
        resp = client.messages.create(**kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)
        text = '\n'.join(b.text for b in resp.content if getattr(b, 'type', None) == 'text').strip()
        return ClaudeResult(text=text, duration_ms=duration_ms)
    except Exception as exc:
        log.error("_run_anthropic_vision failed: %s", exc)
        return ClaudeResult(text='', is_error=True)


# ── OpenAI ───────────────────────────────────────────────────────────

def _run_openai_text(*, prompt: str, messages: list[dict] | None = None,
                     system_prompt: str | None = None, timeout: int = DEFAULT_TIMEOUT,
                     max_tokens: int | None = None,
                     temperature: float | None = None) -> ClaudeResult:
    import openai

    model = _resolve_model()
    max_tokens = _resolve_max_tokens(max_tokens)

    client = openai.OpenAI(
        api_key=os.environ.get('OPENAI_API_KEY', ''),
        timeout=float(timeout),
    )
    oai_messages: list[dict] = []
    if system_prompt:
        oai_messages.append({'role': 'system', 'content': system_prompt})
    if messages:
        oai_messages.extend(messages)
    else:
        oai_messages.append({'role': 'user', 'content': prompt})

    log.info("_run_openai_text model=%s prompt_len=%d system_len=%d",
             model, len(prompt), len(system_prompt or ""))

    kwargs: dict = {'model': model, 'max_tokens': max_tokens, 'messages': oai_messages}
    if temperature is not None:
        kwargs['temperature'] = temperature

    try:
        t0 = time.monotonic()
        resp = client.chat.completions.create(**kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)
        text = resp.choices[0].message.content or ''
        return ClaudeResult(text=text, duration_ms=duration_ms)
    except Exception as exc:
        log.error("_run_openai_text failed: %s", exc)
        return ClaudeResult(text='', is_error=True)


def _run_openai_vision(*, file_bytes: bytes, file_ext: str, prompt: str,
                       system_prompt: str | None = None,
                       timeout: int = 300) -> ClaudeResult:
    import openai

    model = _resolve_model()
    max_tokens = int(os.environ.get('AURA_MAX_TOKENS', '4096'))

    ext = file_ext.lower().lstrip('.')

    client = openai.OpenAI(
        api_key=os.environ.get('OPENAI_API_KEY', ''),
        timeout=float(timeout),
    )
    oai_messages: list[dict] = []
    if system_prompt:
        oai_messages.append({'role': 'system', 'content': system_prompt})

    if ext in _IMAGE_EXTS:
        b64_data = _b64.standard_b64encode(file_bytes).decode('ascii')
        media_type = _MEDIA_TYPES.get(ext, f'image/{ext}')
        oai_messages.append({
            'role': 'user',
            'content': [
                {'type': 'image_url', 'image_url': {'url': f'data:{media_type};base64,{b64_data}'}},
                {'type': 'text', 'text': prompt},
            ],
        })
    elif ext == 'pdf':
        b64_data = _b64.standard_b64encode(file_bytes).decode('ascii')
        oai_messages.append({
            'role': 'user',
            'content': [
                {'type': 'file', 'file': {'filename': 'document.pdf', 'file_data': f'data:application/pdf;base64,{b64_data}'}},
                {'type': 'text', 'text': prompt},
            ],
        })
    else:
        try:
            text_content = file_bytes.decode('utf-8', errors='replace')
            oai_messages.append({'role': 'user', 'content': f'Contenido del fichero ({file_ext}):\n\n{text_content[:8000]}\n\n{prompt}'})
        except Exception:
            oai_messages.append({'role': 'user', 'content': f'[fichero binario {file_ext}]\n\n{prompt}'})

    log.info("_run_openai_vision model=%s ext=%s size=%d", model, ext, len(file_bytes))

    try:
        t0 = time.monotonic()
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens, messages=oai_messages,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)
        text = resp.choices[0].message.content or ''
        return ClaudeResult(text=text, duration_ms=duration_ms)
    except Exception as exc:
        log.error("_run_openai_vision failed: %s", exc)
        return ClaudeResult(text='', is_error=True)


# ── Gemini ───────────────────────────────────────────────────────────

def _run_gemini_text(*, prompt: str, messages: list[dict] | None = None,
                     system_prompt: str | None = None, timeout: int = DEFAULT_TIMEOUT,
                     max_tokens: int | None = None,
                     temperature: float | None = None) -> ClaudeResult:
    import google.generativeai as genai

    model = _resolve_model()
    max_tokens = _resolve_max_tokens(max_tokens)

    genai.configure(api_key=os.environ.get('GEMINI_API_KEY', ''))

    parts: list[str] = []
    if system_prompt:
        parts.append(system_prompt)
    if messages:
        for m in messages:
            role = m.get('role', 'user').capitalize()
            parts.append(f"{role}: {m.get('content', '')}")
    else:
        parts.append(prompt)
    full_prompt = '\n\n'.join(parts)

    log.info("_run_gemini_text model=%s prompt_len=%d system_len=%d",
             model, len(prompt), len(system_prompt or ""))

    gen_config_kwargs: dict = {'max_output_tokens': max_tokens}
    if temperature is not None:
        gen_config_kwargs['temperature'] = temperature

    try:
        t0 = time.monotonic()
        gemini = genai.GenerativeModel(
            model_name=model,
            generation_config=genai.types.GenerationConfig(**gen_config_kwargs),
        )
        resp = gemini.generate_content(full_prompt)
        duration_ms = int((time.monotonic() - t0) * 1000)
        return ClaudeResult(text=resp.text, duration_ms=duration_ms)
    except Exception as exc:
        log.error("_run_gemini_text failed: %s", exc)
        return ClaudeResult(text='', is_error=True)
