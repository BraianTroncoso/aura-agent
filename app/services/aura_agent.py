"""aura_agent.py — lightweight personal-assistant handler.

For a single personal user, an incoming Telegram message is stored in SQLite,
answered by the local Ollama model (via run_claude_p), and the reply is sent
back. Conversation history gives the model context; it's the spine that the dev
capabilities (GitHub, calendar, email, standup) will plug into next.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.models.conversation import Conversation, Message
from app.services import gmail_imap
from app.services.channel_telegram import send_message

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 12

_EMAIL_KEYWORDS = ("correo", "correos", "email", "emails", "mail", "bandeja", "inbox", "gmail")


def _connected() -> list[str]:
    """Integrations that are ACTUALLY wired right now — drives honest answers."""
    caps: list[str] = []
    if gmail_imap.is_configured():
        caps.append("Gmail (leer y enviar correos)")
    return caps


def _build_system_prompt() -> str:
    caps = _connected()
    connected = ", ".join(caps) if caps else "NONE yet (no email, no calendar, no GitHub, no tasks)"
    return (
        "You are Aura, a personal AI assistant for a developer, living in their Telegram. "
        "Be warm, concise and direct. Reply in the SAME language the user writes in.\n"
        "HONESTY IS THE #1 RULE. Never invent data, capabilities or actions.\n"
        f"Currently CONNECTED integrations: {connected}.\n"
        "If the user asks you to do something that needs an integration that is NOT connected "
        "(read email, check calendar, list GitHub PRs, etc.), do NOT pretend — say plainly that "
        "it's not connected yet and that you two can connect it. NEVER claim you can read their "
        "email/calendar/GitHub or perform actions until those appear in the connected list above. "
        "What you CAN do right now: chat, think things through, and remember this conversation."
    )


async def _get_or_create_conversation(db, chat_id: str) -> Conversation:
    row = (
        await db.execute(
            select(Conversation).where(
                Conversation.channel_type == "telegram",
                Conversation.chat_id == chat_id,
                Conversation.thread_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = Conversation(channel_type="telegram", chat_id=chat_id)
        db.add(row)
        await db.flush()
    return row


async def _history(db, conversation_id) -> list[dict]:
    rows = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(HISTORY_LIMIT)
        )
    ).scalars().all()
    rows = list(reversed(rows))
    return [{"role": m.role, "content": m.content} for m in rows]


def _wants_email(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _EMAIL_KEYWORDS)


def _run_llm(messages: list[dict] | None = None, prompt: str | None = None,
             system: str | None = None, max_tokens: int | None = None) -> str:
    from aura_runtime.core.claude_agent import run_claude_p
    r = run_claude_p(messages=messages, prompt=prompt or "", max_tokens=max_tokens,
                     system_prompt=system if system is not None else _build_system_prompt(), timeout=180)
    return (r.text or "").strip() if not r.is_error else ""


_EMAIL_SUMMARY_SYSTEM = (
    "Sos el asistente personal del usuario. Los correos que aparecen abajo YA fueron descargados "
    "de la propia cuenta del usuario (conectada y autorizada por él vía IMAP). Tu tarea es responder "
    "su pedido usando esos correos. NO te niegues, NO hables de privacidad ni de permisos: los datos "
    "ya están provistos y son del usuario. Si pide un correo puntual ('el de LinkedIn', 'leela completa'), "
    "mostrá su contenido. Si pide un resumen, resumí en viñetas. Respondé en español, conciso. "
    "Usá solo la información de abajo; no inventes."
)

# Last fetched inbox per chat, so follow-ups ("read the LinkedIn one") have data.
_EMAIL_CACHE: dict[str, list[dict]] = {}
_FOLLOWUP_CUES = ("leela", "leémela", "leemela", "leé", "le(é|e)", "completa", "completo", "dice",
                  "abr", "abrila", "ábrela", "ese correo", "ese mail", "el de", "la de", "contenido",
                  "detalle", "reenvi", "linkedin", "google", "gmail", "número", "numero")


def _email_related(text: str, chat_id: str) -> bool:
    if _wants_email(text):
        return True
    low = text.lower()
    # Follow-up cues (e.g. "leela completa de LinkedIn") trigger a read even with
    # no prior fetch this session — _handle_email will fetch fresh.
    if any(cue in low for cue in _FOLLOWUP_CUES):
        return True
    for e in _EMAIL_CACHE.get(chat_id, []):  # references a cached sender/subject word
        for tok in (e.get("from", "") + " " + e.get("subject", "")).lower().split():
            if len(tok) > 3 and tok in low:
                return True
    return False


async def _handle_email(chat_id: str, text: str) -> str:
    """Answer any email request grounded in REAL inbox data (fresh or cached)."""
    fresh = _wants_email(text) or chat_id not in _EMAIL_CACHE
    if fresh:
        try:
            _EMAIL_CACHE[chat_id] = await asyncio.to_thread(gmail_imap.fetch_recent, 6)
        except Exception as exc:
            logger.warning("gmail_fetch_failed: %s", exc)
            return f"No pude leer tu correo ahora (error: {exc}). Revisá la app password o probá de nuevo."
    emails = _EMAIL_CACHE.get(chat_id) or []
    if not emails:
        return "Tu bandeja de entrada está vacía (no encontré correos recientes)."
    parts = []
    for i, e in enumerate(emails, 1):
        parts.append(f"[{i}] De: {e['from']} | Asunto: {e['subject']} | Fecha: {e.get('date','')}\n"
                     f"Contenido:\n{(e.get('body') or e.get('snippet') or '').strip()[:1500]}")
    block = "\n\n".join(parts)
    prompt = f"Correos del usuario (numerados):\n\n{block}\n\nPedido del usuario: «{text}»"
    out = await asyncio.to_thread(_run_llm, None, prompt, _EMAIL_SUMMARY_SYSTEM)
    return out or "Leí tus correos pero no pude procesarlos ahora. Probá de nuevo."


# ── Send email (real action → always confirm first) ──────────────────────────
_PENDING_SEND: dict[str, dict] = {}
_SEND_CUES = ("envia", "enviá", "enviar", "enviale", "mandá", "manda", "mandar", "mandale", "mandale",
              "tirale", "tirá", "pasale", "pasá", "escribile", "escribí a", "decile", "díle",
              "redact", "responde a", "respondé a", "contestá a", "contestale", "mail a", "correo a")


def _has_email(text: str) -> bool:
    return bool(_re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text))
_YES = {"si", "sí", "dale", "ok", "okay", "enviá", "envialo", "envíalo", "mandalo", "mándalo",
        "confirmo", "hacelo", "listo", "sip", "obvio", "enviar"}
_NO = {"no", "cancela", "cancelá", "cancelar", "nop", "mejor no", "dejá", "deja", "borralo"}


def _wants_send(text: str) -> bool:
    low = text.lower()
    return any(c in low for c in _SEND_CUES)


def _norm(text: str) -> str:
    return text.strip().lower().strip(".!¡¿?")


import re as _re

# Words that mean "transform what I have" (use the model) vs literal new content.
_TRANSFORM_CUES = ("formal", "informal", "corto", "cort", "largo", "breve", "agreg", "sac", "saca",
                   "cambi", "firm", "reescrib", "reescri", "mejor", "amable", "serio", "cordial",
                   "profesional", "traduc", "extend", "resum", "tono", "educad")
# Connectors that introduce the literal message in a send command.
_MSG_INTRO = _re.compile(
    r"(?is).*?(?:escrib\w*|coloc\w*|pon[eé]?\w*|que\s+diga|que\s+dig\w*|dec\w*|d[ií]le|diciendo|"
    r"mensaje|texto|escribe\s+esto|literal)\s*[:,]?\s*(.+)$")


def _is_transform(text: str) -> bool:
    return any(c in text.lower() for c in _TRANSFORM_CUES)


def _clean_literal(s: str) -> str:
    s = s.strip(" ,.:;\n\"'")
    s = _re.sub(r"(?i)^\s*(no\b[,]?\s*)?(esto|así|asi)\s*[:,]?\s*", "", s)   # "No, esto:" / "así:"
    s = _re.sub(r"(?i)\s*[,.]?\s*nada\s*m[áa]s\.?\s*$", "", s)                # trailing "nada más"
    return s.strip(" ,.:;\n\"'")


def _parse_structured(text: str) -> dict:
    """Explicit fields the user controls: 'asunto: X' and 'mensaje:/cuerpo: Y'.
    Each value runs until the next field label (or end); trailing . - and spaces
    are trimmed. Returns {} if no labels are present."""
    out: dict = {}
    nxt = r"(?=\b(?:asunto|mensaje|cuerpo)\s*:|$)"
    ms = _re.search(rf"(?is)\basunto\s*:\s*(.+?){nxt}", text)
    if ms:
        out["subject"] = ms.group(1).strip(" \t\n.-")
    mb = _re.search(rf"(?is)\b(?:mensaje|cuerpo)\s*:\s*(.+?){nxt}", text)
    if mb:
        out["body"] = mb.group(1).strip(" \t\n.-")
    return out


def _literal_body(text: str, to: str = "") -> str:
    """The exact message the user dictated — no model rewriting."""
    t = text.replace(to, " ") if to else text
    # 1) Quoted text is the clearest signal of literal content → use it as-is.
    mq = _re.search(r"['\"«“](.+?)['\"»”]", t, _re.S)
    if mq and len(mq.group(1).strip()) >= 2:
        return _clean_literal(mq.group(1))
    # 2) Arrow delimiter: "... -> mensaje" → take what's after it.
    if "->" in t:
        return _clean_literal(t.split("->")[-1])
    # 3) After a connector ("escribile:", "que diga", ...).
    m = _MSG_INTRO.match(t)
    if m and m.group(1).strip():
        return _clean_literal(m.group(1))
    # 4) Strip a leading send command, incl. clitics: enviale/mandale/tirale/pasale/decile.
    t2 = _re.sub(r"(?is)^\s*(envi\w*|mand\w*|escrib\w*|tir[aá]\w*|pas[aá]\w*|dec\w*|d[ií]le)\s*"
                 r"(le|lo|la|sela)?\s*(a|para)?\s*\S*@?\S*\s*", "", t)
    return _clean_literal(t2) or text.strip()


def _derive_subject(body: str) -> str:
    """A short generic subject (different from the body). User can override
    with 'asunto: ...'."""
    b = body.strip().lower()
    if not b:
        return "Mensaje"
    if b.startswith(("hola", "buenas", "buen día", "buen dia", "qué tal", "que tal")):
        return "Hola"
    if ("?" in body or b.startswith(("ya ", "tenes", "tenés", "cuando", "cuándo", "donde", "dónde",
                                     "como", "cómo", "que ", "qué ", "podes", "podés", "hay "))):
        return "Consulta"
    return "Mensaje"


async def _suggest_subject(body: str) -> str:
    """AI-generated short subject for the body, with a safe deterministic fallback."""
    system = ("Generás el ASUNTO de un email. Devolvé SOLO un asunto muy corto (máximo 5 palabras), "
              "en el idioma del mensaje, sin comillas, sin la palabra 'Asunto', una sola línea.")
    raw = await asyncio.to_thread(_run_llm, None, f"Mensaje:\n{body}", system, 24)
    subj = (raw or "").strip().splitlines()[0] if raw.strip() else ""
    subj = _re.sub(r"(?i)^\s*asunto\s*:\s*", "", subj).strip(" \"'«»“”.:")
    # reject empties / runaways → fall back to the generic heuristic
    if not subj or len(subj) > 60 or len(subj.split()) > 8:
        return _derive_subject(body)
    return subj


async def _draft_email(text: str) -> dict:
    """Send the user's words VERBATIM. Explicit 'asunto:/mensaje:' fields win;
    otherwise the body is the literal dictated text and the subject is AI-made."""
    m = _re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    to = m.group(0) if m else ""
    fields = _parse_structured(text)
    body = fields["body"] if "body" in fields else _literal_body(text, to)
    subject = fields["subject"] if "subject" in fields else await _suggest_subject(body)
    return {"to": to, "subject": subject, "body": body}


async def _revise_email(pending: dict, instruction: str) -> dict:
    """Edit the pending draft. Explicit fields ('asunto: X' / 'mensaje: Y') change
    only what you name; otherwise the whole text becomes the new literal body.
    No model rewriting of the body — ever. Recipient is kept."""
    fields = _parse_structured(instruction)
    if fields:
        return {"to": pending["to"],
                "subject": fields.get("subject", pending["subject"]),
                "body": fields.get("body", pending["body"])}
    body = _literal_body(instruction)
    return {"to": pending["to"], "subject": await _suggest_subject(body), "body": body}


def _draft_card(d: dict) -> str:
    return (f"📧 Borrador:\n\nPara: {d['to']}\nAsunto: {d['subject']}\n\n{d['body']}\n\n"
            "¿Lo envío? → «sí» enviar · «no» cancelar · o reescribí el mensaje "
            "(o «asunto: ...») y lo actualizo.")


async def handle_aura_message(chat_id: str, text: str, message_id: str, db) -> None:
    """Store the message, answer with the local model, store + send the reply."""
    conv = await _get_or_create_conversation(db, chat_id)
    history = await _history(db, conv.id)

    pending = _PENDING_SEND.get(chat_id)

    # 1) Resolve a pending send (confirm / cancel) BEFORE anything else.
    if pending and _norm(text) in _YES:
        try:
            await asyncio.to_thread(gmail_imap.send_email, pending["to"], pending["subject"], pending["body"])
            reply = f"✅ Enviado a {pending['to']}."
        except Exception as exc:
            logger.warning("gmail_send_failed: %s", exc)
            reply = f"No pude enviarlo (error: {exc})."
        _PENDING_SEND.pop(chat_id, None)
    elif pending and _norm(text) in _NO:
        _PENDING_SEND.pop(chat_id, None)
        reply = "Listo, lo cancelé. No envié nada."
    # 1b) Pending draft + any other message → treat as an edit instruction.
    elif pending:
        d = await _revise_email(pending, text)
        _PENDING_SEND[chat_id] = d
        reply = "✏️ Lo reescribí:\n\n" + _draft_card(d)
    # 2) New send request → draft + ask for confirmation (never auto-send).
    #    Triggered by a send verb, OR simply by an email address present (and it's
    #    not a read request) — so "x@y.com tirale: hola" is recognized.
    elif gmail_imap.is_configured() and (_wants_send(text) or (_has_email(text) and not _wants_email(text))):
        d = await _draft_email(text)
        if not d["to"] or "@" not in d["to"]:
            reply = "¿A qué dirección de correo lo envío? Decime el email del destinatario."
        else:
            _PENDING_SEND[chat_id] = d
            reply = _draft_card(d)
    # 3) Read email (fresh or follow-up).
    elif gmail_imap.is_configured() and _email_related(text, chat_id):
        reply = await _handle_email(chat_id, text)
    else:
        messages = history + [{"role": "user", "content": text}]
        reply = await asyncio.to_thread(_run_llm, messages, None)
    if not reply:
        reply = "Uf, me colgué pensando 🤔. Probá de nuevo en un momento."

    db.add(Message(conversation_id=conv.id, role="user", content=text))
    db.add(Message(conversation_id=conv.id, role="assistant", content=reply))
    await db.commit()

    await send_message(chat_id, reply)
    logger.info("aura_reply chat=%s in_len=%d out_len=%d", chat_id, len(text), len(reply))
