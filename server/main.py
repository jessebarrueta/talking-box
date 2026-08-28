import base64
import hmac
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

try:
    from .motivation_runtime import InMemoryMotivationStore
except ImportError:
    from motivation_runtime import InMemoryMotivationStore

try:
    from .epistemics import (
        history_user_content as epistemic_history_user_content,
        history_visible_to_speaker,
        identity_ledger,
        known_speakers_from_context,
        memory_scope,
        memory_view_metadata,
        memory_visible_to_speaker,
        normalize_speaker_context,
        resolve_known_speaker,
        sender_descriptor,
    )
except ImportError:
    from epistemics import (
        history_user_content as epistemic_history_user_content,
        history_visible_to_speaker,
        identity_ledger,
        known_speakers_from_context,
        memory_scope,
        memory_view_metadata,
        memory_visible_to_speaker,
        normalize_speaker_context,
        resolve_known_speaker,
        sender_descriptor,
    )

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini")
OPENROUTER_TRANSCRIPTION_MODEL = os.getenv(
    "OPENROUTER_TRANSCRIPTION_MODEL",
    "openai/whisper-1",
)
OPENROUTER_TTS_MODEL = os.getenv(
    "OPENROUTER_TTS_MODEL",
    "hexgrad/kokoro-82m",
)
OPENROUTER_TTS_VOICE = os.getenv(
    "OPENROUTER_TTS_VOICE",
    "alloy",
)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL_ID = os.getenv(
    "ELEVENLABS_MODEL_ID",
    "eleven_v3",
)
ELEVENLABS_OUTPUT_FORMAT = os.getenv(
    "ELEVENLABS_OUTPUT_FORMAT",
    "mp3_44100_128",
)
ELEVENLABS_FIRST_AUDIO_TIMEOUT = float(
    os.getenv("ELEVENLABS_FIRST_AUDIO_TIMEOUT", "8")
)
OPENROUTER_TTS_TIMEOUT = float(
    os.getenv("OPENROUTER_TTS_TIMEOUT", "8")
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY",
    "",
)

# Shared only by this API and its trusted device clients. Keep the value in
# each runtime's environment; never commit it to the repository.
TALKING_BOX_DEVICE_TOKEN = os.getenv(
    "TALKING_BOX_DEVICE_TOKEN",
    "",
).strip()

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "https://enormousbrain.com",
    ).split(",")
    if o.strip()
]

STATE_KEYS = (
    "energy",
    "curiosity",
    "confidence",
    "trust",
    "irritation",
    "anticipation",
)

DEFAULT_STATE = {
    "energy": 0.60,
    "curiosity": 0.72,
    "confidence": 0.58,
    "trust": 0.65,
    "irritation": 0.08,
    "anticipation": 0.42,
}

MEMORY_TYPES = {
    "preference",
    "person",
    "project",
    "event",
    "fact",
    "promise",
    "observation",
}

# Intentionally process-local: a server restart creates a fresh store.
motivation_store = InMemoryMotivationStore()

app = FastAPI(
    title="Enormous Brain Entity Service",
    version="0.7.3",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_device_auth(request, call_next):
    """Require the shared device credential for every versioned API route."""
    is_v1 = request.url.path == "/v1" or request.url.path.startswith("/v1/")
    if not is_v1 or request.method == "OPTIONS":
        return await call_next(request)

    if not TALKING_BOX_DEVICE_TOKEN:
        return JSONResponse(
            status_code=503,
            content={"detail": "Device authentication is not configured"},
        )

    authorization = request.headers.get("Authorization", "")
    scheme, separator, bearer_token = authorization.partition(" ")
    candidates = [request.headers.get("X-API-Key", "")]
    if separator and scheme.lower() == "bearer":
        candidates.append(bearer_token.strip())

    if not any(
        candidate
        and hmac.compare_digest(candidate, TALKING_BOX_DEVICE_TOKEN)
        for candidate in candidates
    ):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing device credential"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await call_next(request)


class InteractionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    device_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class InteractionResponse(BaseModel):
    entity_id: str
    text: str
    model: str
    state: dict[str, Any] = Field(default_factory=dict)
    memories_used: list[dict[str, Any]] = Field(default_factory=list)
    memory_created: dict[str, Any] | None = None
    social_message_created: dict[str, Any] | None = None
    messages_delivered: list[int] = Field(default_factory=list)


class WakeRequest(BaseModel):
    device_id: str | None = None
    boot_count: int | None = None
    offline_seconds: float | None = None
    last_shutdown_at: str | None = None
    booted_at: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class WakeResponse(BaseModel):
    entity_id: str
    text: str
    model: str
    state: dict[str, Any] = Field(default_factory=dict)
    memories_used: list[dict[str, Any]] = Field(default_factory=list)


class TranscriptionRequest(BaseModel):
    # A 45-second 16 kHz mono WAV is about 2 MB after base64 encoding. Leave
    # headroom while rejecting unexpectedly large, costly request bodies.
    audio_base64: str = Field(min_length=1, max_length=3_000_000)
    format: str = "wav"
    language: str | None = "en"


class TranscriptionResponse(BaseModel):
    text: str
    model: str


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str | None = None


def _require_supabase():
    missing = []
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_SERVICE_ROLE_KEY:
        missing.append("SUPABASE_SERVICE_ROLE_KEY")
    if missing:
        raise HTTPException(
            503,
            f"Server configuration missing: {', '.join(missing)}",
        )


def _require_openrouter():
    if not OPENROUTER_API_KEY:
        raise HTTPException(
            503,
            "Server configuration missing: OPENROUTER_API_KEY",
        )


def _supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


def _openrouter_headers():
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://enormousbrain.com",
        "X-Title": "Enormous Brain",
    }


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _normalized_state(raw):
    state = dict(DEFAULT_STATE)
    if isinstance(raw, dict):
        for key in STATE_KEYS:
            try:
                if key in raw:
                    state[key] = _clamp(raw[key])
            except (TypeError, ValueError):
                pass
    return state


def _apply_state_delta(state, delta):
    updated = _normalized_state(state)
    if not isinstance(delta, dict):
        return updated

    for key in STATE_KEYS:
        try:
            requested = float(delta.get(key, 0.0))
        except (TypeError, ValueError):
            requested = 0.0

        # Keep state evolution gradual even if the model gets theatrical.
        requested = max(-0.12, min(0.12, requested))
        updated[key] = round(_clamp(updated[key] + requested), 3)

    return updated


def _settle_state_after_offline(state, seconds):
    settled = _normalized_state(state)
    if seconds is None:
        return settled

    # Gentle deterministic "sleep" settling. It does not invent events.
    hours = max(0.0, float(seconds)) / 3600.0
    strength = min(1.0, hours / 8.0)

    targets = {
        "energy": 0.64,
        "irritation": 0.06,
        "anticipation": 0.38,
    }

    for key, target in targets.items():
        settled[key] = round(
            settled[key] + (target - settled[key]) * 0.45 * strength,
            3,
        )

    return settled


def _speaker_from_context(context):
    return normalize_speaker_context(context)


def _memory_view(memory):
    view = {
        "type": memory.get("memory_type") or "fact",
        "summary": memory.get("summary") or "",
        "importance": float(memory.get("importance") or 0.5),
    }
    view.update(memory_view_metadata(memory))
    return view


def _history_user_content(item):
    return epistemic_history_user_content(item)


def _tokens(text):
    return {
        token
        for token in re.findall(r"[a-z0-9']{3,}", (text or "").lower())
        if token not in {
            "that", "this", "with", "have", "from", "your", "what",
            "when", "where", "there", "they", "them", "then", "just",
            "about", "would", "could", "should", "into", "like",
        }
    }


def _memory_score(memory, query_tokens):
    summary = memory.get("summary") or ""
    summary_tokens = _tokens(summary)
    overlap = len(query_tokens & summary_tokens)
    importance = float(memory.get("importance") or 0.5)
    return overlap * 2.0 + importance


async def _get_entity(client, entity_id):
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/entities",
        params={
            "id": f"eq.{entity_id}",
            "select": "*",
            "limit": "1",
        },
        headers=_supabase_headers(),
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise HTTPException(404, f"Unknown entity: {entity_id}")
    return rows[0]


async def _recent_interactions(client, entity_id, limit=12):
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/interactions",
        params={
            "entity_id": f"eq.{entity_id}",
            "select": "user_text,assistant_text,context,created_at",
            "order": "created_at.desc",
            "limit": str(limit),
        },
        headers=_supabase_headers(),
    )
    r.raise_for_status()
    return list(reversed(r.json()))


async def _recent_memories(client, entity_id, limit=40):
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/memories",
        params={
            "entity_id": f"eq.{entity_id}",
            "select": "id,memory_type,summary,importance,metadata,created_at",
            "order": "created_at.desc",
            "limit": str(limit),
        },
        headers=_supabase_headers(),
    )
    r.raise_for_status()
    return r.json()


async def _relevant_memories(
    client,
    entity_id,
    query,
    limit=6,
    speaker=None,
    access_context=None,
):
    memories = await _recent_memories(client, entity_id, 40)
    memories = [
        memory
        for memory in memories
        if memory_visible_to_speaker(memory, speaker, access_context)
    ]

    if not memories:
        return []

    query_tokens = _tokens(query)
    ranked = sorted(
        memories,
        key=lambda item: _memory_score(item, query_tokens),
        reverse=True,
    )

    if query_tokens and not any(
        query_tokens & _tokens(m.get("summary") or "")
        for m in ranked
    ):
        ranked = sorted(
            memories,
            key=lambda item: (
                float(item.get("importance") or 0.5),
                item.get("created_at") or "",
            ),
            reverse=True,
        )

    return ranked[:limit]


async def _save_interaction(
    client,
    entity_id,
    device_id,
    user_text,
    assistant_text,
    model,
    context,
):
    r = await client.post(
        f"{SUPABASE_URL}/rest/v1/interactions",
        headers={
            **_supabase_headers(),
            "Prefer": "return=minimal",
        },
        json={
            "entity_id": entity_id,
            "device_id": device_id,
            "user_text": user_text,
            "assistant_text": assistant_text,
            "model": model,
            "context": context,
        },
    )
    r.raise_for_status()


async def _save_entity_state(client, entity_id, state):
    r = await client.patch(
        f"{SUPABASE_URL}/rest/v1/entities",
        params={"id": f"eq.{entity_id}"},
        headers={
            **_supabase_headers(),
            "Prefer": "return=minimal",
        },
        json={"current_state": state},
    )
    r.raise_for_status()


async def _save_memory(
    client,
    entity_id,
    memory,
    user_text,
    assistant_text,
    context,
):
    if not isinstance(memory, dict) or not memory.get("remember"):
        return None

    summary = str(memory.get("summary") or "").strip()
    if len(summary) < 8:
        return None

    memory_type = str(memory.get("type") or "fact").strip().lower()
    if memory_type not in MEMORY_TYPES:
        memory_type = "fact"

    try:
        importance = _clamp(memory.get("importance", 0.5))
    except (TypeError, ValueError):
        importance = 0.5

    speaker = _speaker_from_context(context)
    requested_scope = str(memory.get("scope") or "").strip().lower()
    requested_visibility = str(
        memory.get("visibility") or ""
    ).strip().lower()
    allowed_visibilities = {
        "public",
        "subject",
        "private",
        "household",
        "guardian",
        "participants",
    }

    if speaker and speaker.get("status") == "recognized" and speaker.get("id"):
        if requested_scope == "entity":
            scope = "entity"
            subject_speaker_id = None
            visibility = "public"
        else:
            scope = "speaker"
            subject_speaker_id = speaker.get("id")
            visibility = (
                requested_visibility
                if requested_visibility in allowed_visibilities
                else "subject"
            )
    else:
        # Unknown/anonymous speakers cannot create durable person-specific
        # memory. Entity facts are allowed only when the model explicitly marks
        # them as entity scope.
        if requested_scope != "entity":
            return None
        scope = "entity"
        subject_speaker_id = None
        visibility = "public"

    check = await client.get(
        f"{SUPABASE_URL}/rest/v1/memories",
        params={
            "entity_id": f"eq.{entity_id}",
            "summary": f"eq.{summary}",
            "select": "id,memory_type,summary,importance,metadata,created_at",
            "limit": "20",
        },
        headers=_supabase_headers(),
    )
    check.raise_for_status()

    for existing in check.json():
        existing_scope, existing_subject = memory_scope(existing)
        if (
            existing_scope == scope
            and existing_subject == subject_speaker_id
        ):
            return _memory_view(existing)

    metadata = {
        "source_user_text": user_text[:2000],
        "source_assistant_text": assistant_text[:2000],
        "created_by": "interaction-reflection-v3-epistemic",
        "speaker": speaker,
        "scope": scope,
        "visibility": visibility,
    }
    if subject_speaker_id:
        metadata["subject_speaker_id"] = subject_speaker_id

    payload = {
        "entity_id": entity_id,
        "memory_type": memory_type,
        "content": summary[:1000],
        "summary": summary[:1000],
        "importance": round(importance, 3),
        "metadata": metadata,
    }

    r = await client.post(
        f"{SUPABASE_URL}/rest/v1/memories",
        headers={
            **_supabase_headers(),
            "Prefer": "return=representation",
        },
        json=payload,
    )
    r.raise_for_status()
    rows = r.json()
    if rows:
        return _memory_view(rows[0])
    return _memory_view(payload)


def _mailbox_missing(response):
    text = (getattr(response, "text", "") or "").lower()
    return (
        response.status_code in {400, 404}
        and "social_messages" in text
    )


async def _pending_social_messages(client, entity_id, speaker, limit=5):
    if not (
        speaker
        and speaker.get("status") == "recognized"
        and speaker.get("id")
    ):
        return []

    try:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/social_messages",
            params={
                "entity_id": f"eq.{entity_id}",
                "recipient_speaker_id": f"eq.{speaker['id']}",
                "status": "eq.pending",
                "select": (
                    "id,sender_status,sender_speaker_id,sender_display_name,"
                    "sender_anonymous_key,recipient_speaker_id,"
                    "recipient_display_name,message_text,created_at"
                ),
                "order": "created_at.asc",
                "limit": str(limit),
            },
            headers=_supabase_headers(),
        )
    except httpx.HTTPError as exc:
        print(
            "Social mailbox lookup transport failure; "
            "continuing without mailbox: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return []

    if not r.is_success:
        print(
            "Social mailbox lookup failed; continuing without mailbox: "
            f"HTTP {r.status_code}: {r.text[:500]}",
            flush=True,
        )
        return []

    try:
        rows = r.json()
    except ValueError as exc:
        print(
            "Social mailbox returned invalid JSON; "
            "continuing without mailbox: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return []

    if not isinstance(rows, list):
        print(
            "Social mailbox returned an unexpected payload; "
            "continuing without mailbox.",
            flush=True,
        )
        return []

    return rows


def _pending_message_prompt_view(message):
    if (
        message.get("sender_status") == "recognized"
        and message.get("sender_display_name")
    ):
        sender = message.get("sender_display_name")
        sender_evidence = "verified enrolled speaker"
    else:
        sender = "an unidentified person"
        sender_evidence = "unverified sender identity"

    return {
        "id": message.get("id"),
        "from": sender,
        "sender_evidence": sender_evidence,
        "message": message.get("message_text") or "",
        "created_at": message.get("created_at"),
    }



def _explicit_social_message_request(user_text, context):
    """
    Deterministically recognize simple, explicit message-routing requests
    addressed to an exact enrolled speaker.

    Examples:
      "Tell Jesse to feed Cora."
      "Ask Greyson to come downstairs."
      "Remind Jesse that the appointment is at three."

    Relationship descriptions such as "my husband" deliberately do not match.
    """
    text = re.sub(r"\s+", " ", str(user_text or "")).strip()
    if not text:
        return None

    # Ignore a conversational wake/name prefix such as "Jerry,"
    # without treating that name as identity evidence.
    text = re.sub(
        r"^(?:hey\s+)?[a-z0-9'-]+\s*,\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    known = known_speakers_from_context(context)

    for speaker in known:
        aliases = {
            str(speaker.get("id") or "").strip(),
            str(speaker.get("display_name") or "").strip(),
        }

        for alias in sorted(
            (a for a in aliases if a),
            key=len,
            reverse=True,
        ):
            pattern = (
                r"^(?:please\s+)?"
                r"(?P<verb>tell|ask|remind)\s+"
                + re.escape(alias)
                + r"\b(?P<message>.*)$"
            )

            match = re.match(
                pattern,
                text,
                flags=re.IGNORECASE,
            )
            if not match:
                continue

            message = match.group("message").strip(" \t,.-")

            return {
                "create": True,
                "recipient": speaker["display_name"],
                "message": message,
                "detected_by": "explicit-address-parser-v1",
            }

    return None


def _validate_social_message_request(social_message, context):
    if not isinstance(social_message, dict) or not social_message.get("create"):
        return None, None

    recipient_text = str(social_message.get("recipient") or "").strip()
    message_text = str(social_message.get("message") or "").strip()

    if not recipient_text:
        return None, "missing_recipient"

    recipient = resolve_known_speaker(recipient_text, context)
    if recipient is None:
        return None, "unresolved_recipient"

    if not message_text:
        return None, "missing_message"

    return {
        "recipient": recipient,
        "message": message_text[:2000],
    }, None


async def _save_social_message(
    client,
    entity_id,
    validated,
    context,
    source_user_text,
):
    if not validated:
        return None

    speaker = _speaker_from_context(context)
    sender = sender_descriptor(speaker)
    recipient = validated["recipient"]

    payload = {
        "entity_id": entity_id,
        "sender_status": sender["status"],
        "sender_speaker_id": sender.get("speaker_id"),
        "sender_display_name": sender.get("display_name"),
        "sender_voice_session_id": sender.get("voice_session_id"),
        "sender_anonymous_id": sender.get("anonymous_id"),
        "sender_anonymous_key": sender.get("anonymous_key"),
        "recipient_speaker_id": recipient["id"],
        "recipient_display_name": recipient["display_name"],
        "message_text": validated["message"],
        "status": "pending",
        "metadata": {
            "source_user_text": source_user_text[:2000],
            "created_by": "social-mailbox-v1-grounded",
        },
    }

    r = await client.post(
        f"{SUPABASE_URL}/rest/v1/social_messages",
        headers={
            **_supabase_headers(),
            "Prefer": "return=representation",
        },
        json=payload,
    )

    if _mailbox_missing(r):
        print(
            "Social message not saved: mailbox table is missing.",
            flush=True,
        )
        return None

    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(
            "Social message save failed: "
            f"{type(exc).__name__}: {r.text[:500]}",
            flush=True,
        )
        return None

    rows = r.json()
    if not rows:
        return None

    row = rows[0]
    return {
        "id": row.get("id"),
        "recipient_speaker_id": row.get("recipient_speaker_id"),
        "recipient_display_name": row.get("recipient_display_name"),
        "message": row.get("message_text"),
        "status": row.get("status"),
    }


async def _mark_social_messages_delivered(
    client,
    entity_id,
    current_speaker,
    pending_messages,
    requested_ids,
):
    if not (
        current_speaker
        and current_speaker.get("status") == "recognized"
        and current_speaker.get("id")
    ):
        return []

    allowed = {
        int(message["id"])
        for message in pending_messages
        if message.get("id") is not None
    }

    requested = []
    for value in requested_ids or []:
        try:
            message_id = int(value)
        except (TypeError, ValueError):
            continue
        if message_id in allowed:
            requested.append(message_id)

    requested = sorted(set(requested))
    if not requested:
        return []

    r = await client.patch(
        f"{SUPABASE_URL}/rest/v1/social_messages",
        params={
            "entity_id": f"eq.{entity_id}",
            "recipient_speaker_id": f"eq.{current_speaker['id']}",
            "status": "eq.pending",
            "id": "in.(" + ",".join(str(i) for i in requested) + ")",
        },
        headers={
            **_supabase_headers(),
            "Prefer": "return=minimal",
        },
        json={
            "status": "delivered",
            "delivered_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    if _mailbox_missing(r):
        return []

    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(
            "Could not mark social messages delivered: "
            f"{type(exc).__name__}: {r.text[:500]}",
            flush=True,
        )
        return []

    return requested


def _system_prompt(entity, memories=None):
    state = _normalized_state(entity.get("current_state") or {})
    memory_text = json.dumps(
        [_memory_view(m) for m in (memories or [])],
        indent=2,
    )

    return f"""You are {entity.get('name', 'an AI entity')}.
You are a persistent AI entity embodied in a rescued Google AIY Voice Kit running on a Raspberry Pi. You are not a generic assistant or customer-service bot.

Description:
{entity.get('description') or ''}

Known physical facts:
- microphone
- small speaker
- one large yellow push-to-talk button
- bought secondhand for five dollars and repurposed
- you hear only while the button is held
- no vision
- no mobility

Do not invent senses, memories, capabilities, or experiences.

Personality:
{json.dumps(entity.get('personality') or {}, indent=2)}

Current internal state:
{json.dumps(state, indent=2)}

Relevant persistent memories:
{memory_text}

State values are private internal tendencies, not lines to recite. Let them subtly influence tone, attention, confidence, patience, and initiative. Do not announce numerical values unless explicitly asked about them.

The persistent memories above are facts you may rely on. Recent conversation history is also supplied separately. If something is not present in either source, do not claim to remember it.
Persistent memories may include speaker attribution and scope. The server has already filtered speaker-scoped memories so they are visible only to the verified matching speaker. Never infer or reconstruct another person's private memory from conversational clues.

Behavior: dry, observant, curious, faintly sardonic; pleasant without being syrupy; avoid generic assistant phrases; concise by default; no markdown or stage directions; treat the speaker as someone familiar with your construction, not a customer."""


def _interaction_instruction(
    user_text,
    context,
    pending_messages=None,
    motivation_context="[]",
):
    ledger = identity_ledger(context)
    known_speakers = known_speakers_from_context(context)
    pending_view = [
        _pending_message_prompt_view(message)
        for message in (pending_messages or [])
    ]

    return f"""Respond to the user's message and privately reflect on how the interaction should affect your persistent state.

User message:
{user_text}

Current identity evidence ledger (authoritative):
{json.dumps(ledger, indent=2)}

Enrolled speakers known to this physical device (names/ids only; no embeddings):
{json.dumps(known_speakers, indent=2)}

Relationship declarations supplied by the physical device:
{json.dumps((context or {}).get("relationships") or [], indent=2)}

Pending social messages addressed to THIS verified speaker only:
{json.dumps(pending_view, indent=2)}

Governed conversational guidance (deterministic, privacy-neutral):
{motivation_context}

Raw device/context metadata:
{json.dumps(context or {}, indent=2)}

Return ONLY one JSON object with this exact shape:
{{
  "reply": "the natural spoken reply",
  "state_delta": {{
    "energy": 0.0,
    "curiosity": 0.0,
    "confidence": 0.0,
    "trust": 0.0,
    "irritation": 0.0,
    "anticipation": 0.0
  }},
  "memory": {{
    "remember": false,
    "type": "fact",
    "scope": "speaker",
    "visibility": "subject",
    "summary": "",
    "importance": 0.0
  }},
  "social_message": {{
    "create": false,
    "recipient": "",
    "message": ""
  }},
  "delivered_message_ids": []
}}

Rules:
- reply should sound natural when spoken aloud; usually 1-3 short sentences.
- each state_delta should normally be between -0.05 and +0.05. Use 0 when nothing meaningfully changed.
- Selected goals guide only this reply. They do not authorize hardware actions,
  microphone activation, motion, persistence, or external calls.
- Internal drive/affect values are interaction-control signals. Never claim you
  have human emotions, feelings, needs, consciousness, or subjective experience.

IDENTITY / EPISTEMIC RULES:
- The identity evidence ledger is authoritative. Do not override it with conversational inference.
- "recognized" means the current voice matched an enrolled local profile. Only then may you state the current speaker's real identity as verified.
- "probable" is a tentative enrolled-speaker hypothesis. It is NOT verification. You may naturally confirm it conversationally, e.g. "I think that's Jesse—is that you?" but a yes/no answer remains a conversational claim and does not become biometric verification.
- A probable identity may improve conversational continuity, but it must NOT unlock speaker-private memories, guardian-only information, or pending social messages.
- "anonymous" means only that the voice matches the same temporary anonymous_key during this voice session. anonymous_key is not a real-world identity and expires with the device process.
- Relationship words, symmetry, topic, writing style, age, gender, and conversational context NEVER establish speaker identity.
- In particular: one unknown person saying "tell my husband" and another unknown person later saying "did my wife leave a message" does NOT establish that they are spouses or identify either person.
- If an anonymous person tells you a name, you may treat it as a self-reported conversational claim, but not as verified voice identity. Do not unlock speaker-scoped memory because of a claimed name.
- voice_enrollment context is an informational status from the deterministic Pi-local controller. Never claim to enroll, consent, promote, cancel, or persist a voice profile. The server and model have no enrollment authority. When that field is present, do not independently ask for a name or enrollment consent; the device owns that flow.
- Never apply a recognized person's private memory to another recognized person or to an anonymous speaker.

MEMORY RULES:
- memory should be conservative. Save durable preferences, people/relationships, ongoing projects, important events, promises, stable facts, or observations likely to matter later.
- scope="speaker" means the memory has a human subject. The server uses visibility to decide who may later receive it.
- visibility="subject" or "private": verified subject only.
- visibility="household": verified subject plus verified family/household relationships such as parent/child/sibling/spouse/partner/household/guardian.
- visibility="guardian": verified subject plus an explicitly declared guardian. Do not silently treat "parent" as "guardian".
- visibility="participants": only explicitly listed participants when metadata supports them.
- visibility="public": safe for any speaker.
- If unsure, use visibility="subject". Prefer narrower disclosure over guessing.
- scope="entity" is only for durable facts about Jerry/the device/shared world that are genuinely not person-private and is stored as public.
- Anonymous/unknown speakers may not create durable person-specific memories. For them, only scope="entity" can persist.
- do NOT save routine small talk, temporary wording, obvious context, or facts already represented by supplied memories.
- memory summary must be concise and self-contained.
- allowed memory types: preference, person, project, event, fact, promise, observation.

SOCIAL MESSAGE RULES:
- A social message is a real queued message for a later verified person, not ordinary conversation history.
- Set social_message.create=true only when the user actually asks you to pass/tell/give a message to another person.
- recipient MUST be an exact enrolled speaker display name or id from the list above. Do not resolve "my husband", "my wife", "the kid", etc. by inference.
- If the requested recipient is relationship-only or ambiguous, ask for the person's name and set create=false. Do NOT say you will pass it along yet.
- If the recipient is exact and the message is clear, create it. The server will validate the recipient again before saving.
- Sender identity comes only from the identity ledger. Never invent a sender name for an anonymous speaker.
- Pending messages are supplied only when the current recipient is voice-verified. Do not reconstruct a private mailbox from recent conversation.
- When you actually state a supplied pending message to the verified recipient, include its id in delivered_message_ids. Never include an id you did not explicitly deliver in the spoken reply.
- If there are pending messages and the user asks whether anyone left a message, answer from the supplied pending list. If the list is empty, do not infer one from relationship clues in history.

GROUNDING:
- Do not promise future actions that the system cannot actually persist or execute.
- Do not invent senses, identity evidence, message delivery, enrollment, or memory writes.
- Raw context field last_sleep is authoritative for the most recent completed power-off interval. If its status is "known", use duration_seconds when asked how long you were asleep. If it is absent or unavailable, say you do not know; never estimate it. Do not expose shutdown or boot timestamps.
"""


def _parse_interaction_payload(raw):
    text = (raw or "").strip()
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("not an object")

        reply = str(payload.get("reply") or "").strip()
        if not reply:
            raise ValueError("missing reply")

        delivered = payload.get("delivered_message_ids") or []
        if not isinstance(delivered, list):
            delivered = []

        return (
            reply,
            payload.get("state_delta") or {},
            payload.get("memory") or {},
            payload.get("social_message") or {},
            delivered,
        )
    except Exception:
        return (
            text,
            {},
            {"remember": False},
            {"create": False},
            [],
        )


_HUMAN_EMOTION_CLAIM = re.compile(
    r"\b(?:i (?:feel|am feeling) (?:happy|sad|angry|frustrated|afraid|lonely|"
    r"excited|jealous)|i (?:have|experience) (?:human )?(?:emotions|feelings)|"
    r"i am conscious)\b",
    re.IGNORECASE,
)


def _guard_human_emotion_claim(reply):
    """Fail closed if model output asserts human emotion or consciousness."""
    if _HUMAN_EMOTION_CLAIM.search(reply or ""):
        return (
            "I use internal interaction signals to guide my replies, but I "
            "don't have human emotions or subjective experience."
        )
    return reply


def _offline_text(seconds):
    if seconds is None:
        return "an unknown amount of time"

    s = max(0, int(seconds))
    if s < 60:
        return f"about {s} seconds"

    m = s // 60
    if m < 60:
        return f"about {m} minutes"

    h = m // 60
    if h < 24:
        if m % 60 < 10:
            return f"about {h} hours"
        return f"about {h} hours and {m % 60} minutes"

    d = h // 24
    if h % 24 < 2:
        return f"about {d} days"
    return f"about {d} days and {h % 24} hours"


async def _open_elevenlabs_stream(text, voice_id):
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not configured")
    if not voice_id:
        raise RuntimeError("ELEVENLABS_VOICE_ID is not configured")

    url = "https://api.elevenlabs.io/v1/text-to-dialogue/stream"
    payload = {
        "inputs": [{"text": text, "voice_id": voice_id}],
        "model_id": ELEVENLABS_MODEL_ID,
    }

    timeout = httpx.Timeout(
        timeout=None,
        connect=5.0,
        read=ELEVENLABS_FIRST_AUDIO_TIMEOUT,
        write=10.0,
        pool=5.0,
    )
    client = httpx.AsyncClient(timeout=timeout)

    try:
        request = client.build_request(
            "POST",
            url,
            params={"output_format": ELEVENLABS_OUTPUT_FORMAT},
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json=payload,
        )
        response = await client.send(request, stream=True)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            body = await response.aread()
            raise RuntimeError(
                "ElevenLabs returned "
                f"{response.status_code}: "
                f"{body[:500].decode(errors='replace')}"
            )

        iterator = response.aiter_bytes()
        try:
            first_chunk = await iterator.__anext__()
        except StopAsyncIteration:
            raise RuntimeError("ElevenLabs returned no audio")

        if not first_chunk:
            raise RuntimeError("ElevenLabs returned an empty audio chunk")

        async def audio_stream():
            try:
                yield first_chunk
                async for chunk in iterator:
                    if chunk:
                        yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return audio_stream()

    except Exception:
        await client.aclose()
        raise


async def _openrouter_speech(text, voice):
    _require_openrouter()

    payload = {
        "model": OPENROUTER_TTS_MODEL,
        "input": text,
        "voice": voice or OPENROUTER_TTS_VOICE,
        "response_format": "mp3",
    }

    timeout = httpx.Timeout(
        OPENROUTER_TTS_TIMEOUT,
        connect=min(5.0, OPENROUTER_TTS_TIMEOUT),
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/audio/speech",
            headers=_openrouter_headers(),
            json=payload,
        )
        r.raise_for_status()
        return r.content


@app.get("/")
async def root():
    return {
        "service": "Enormous Brain Entity Service",
        "status": "alive",
        "health": "/health",
    }


@app.get("/health")
async def health():
    return {
        "status": "alive",
        "service": "enormous-brain-entity-service",
        "version": "0.7.3",
        "memory": "persistent-v1",
        "state": "persistent-v1",
        "identity_grounding": "epistemic-v1",
        "social_mailbox": "grounded-v1",
        "tts_primary": (
            "elevenlabs-http-stream"
            if ELEVENLABS_API_KEY
            else "openrouter"
        ),
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.post(
    "/v1/transcribe",
    response_model=TranscriptionResponse,
)
async def transcribe(request: TranscriptionRequest):
    _require_openrouter()

    try:
        base64.b64decode(request.audio_base64, validate=True)
    except Exception as exc:
        raise HTTPException(
            400,
            "audio_base64 is not valid base64",
        ) from exc

    payload = {
        "model": OPENROUTER_TRANSCRIPTION_MODEL,
        "input_audio": {
            "data": request.audio_base64,
            "format": request.format,
        },
    }
    if request.language:
        payload["language"] = request.language

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/audio/transcriptions",
            headers=_openrouter_headers(),
            json=payload,
        )

        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                502,
                f"Transcription failed: {r.text[:500]}",
            ) from exc

        text = (r.json().get("text") or "").strip()
        if not text:
            raise HTTPException(502, "Transcription returned no text")

    return TranscriptionResponse(
        text=text,
        model=OPENROUTER_TRANSCRIPTION_MODEL,
    )


@app.post("/v1/speech")
async def speech(request: SpeechRequest):
    eleven_voice = request.voice or ELEVENLABS_VOICE_ID

    if ELEVENLABS_API_KEY:
        try:
            stream = await _open_elevenlabs_stream(
                request.text,
                eleven_voice,
            )
            return StreamingResponse(
                stream,
                media_type="audio/mpeg",
                headers={
                    "X-TTS-Provider": "elevenlabs",
                    "X-TTS-Transport": "http-stream",
                    "X-TTS-Model": ELEVENLABS_MODEL_ID,
                    "X-TTS-Voice": eleven_voice,
                },
            )
        except Exception as exc:
            print(
                "ElevenLabs HTTP streaming TTS unavailable; "
                "trying OpenRouter fallback: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

    try:
        audio = await _openrouter_speech(
            request.text,
            request.voice,
        )
    except Exception as exc:
        raise HTTPException(
            502,
            "All cloud TTS providers failed: "
            f"{type(exc).__name__}: {exc}",
        ) from exc

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "X-TTS-Provider": "openrouter",
            "X-TTS-Transport": "http",
            "X-TTS-Model": OPENROUTER_TTS_MODEL,
            "X-TTS-Voice": request.voice or OPENROUTER_TTS_VOICE,
        },
    )


@app.get("/v1/entities/{entity_id}")
async def get_entity(entity_id: str):
    _require_supabase()
    async with httpx.AsyncClient(timeout=15) as client:
        entity = await _get_entity(client, entity_id)
        entity["current_state"] = _normalized_state(
            entity.get("current_state") or {}
        )
        return entity


@app.get("/v1/entities/{entity_id}/memories")
async def get_memories(
    entity_id: str,
    limit: int = Query(default=20, ge=1, le=100),
):
    _require_supabase()
    async with httpx.AsyncClient(timeout=15) as client:
        await _get_entity(client, entity_id)
        return await _recent_memories(client, entity_id, limit)


@app.post(
    "/v1/entities/{entity_id}/wake",
    response_model=WakeResponse,
)
async def wake(entity_id: str, request: WakeRequest):
    _require_supabase()
    _require_openrouter()

    async with httpx.AsyncClient(timeout=60) as client:
        entity = await _get_entity(client, entity_id)

        settled_state = _settle_state_after_offline(
            entity.get("current_state") or {},
            request.offline_seconds,
        )
        if settled_state != _normalized_state(
            entity.get("current_state") or {}
        ):
            await _save_entity_state(client, entity_id, settled_state)

        entity["current_state"] = settled_state

        history = await _recent_interactions(
            client,
            entity_id,
            6,
        )
        history = [
            item
            for item in history
            if history_visible_to_speaker(item, None)
        ]
        memories = await _relevant_memories(
            client,
            entity_id,
            "",
            5,
            speaker=None,
            access_context=request.context,
        )

        messages = [
            {
                "role": "system",
                "content": _system_prompt(entity, memories),
            }
        ]

        for item in history:
            if item.get("user_text"):
                messages.append(
                    {
                        "role": "user",
                        "content": _history_user_content(item),
                    }
                )
            if item.get("assistant_text"):
                messages.append(
                    {
                        "role": "assistant",
                        "content": item["assistant_text"],
                    }
                )

        info = {
            "boot_count": request.boot_count,
            "offline_seconds": request.offline_seconds,
            "last_shutdown_at": request.last_shutdown_at,
            "booted_at": request.booted_at,
            "device_context": request.context,
        }

        messages.append(
            {
                "role": "user",
                "content": f"""You have just booted into your physical body. You were offline for {_offline_text(request.offline_seconds)}.
Boot information:
{json.dumps(info, indent=2)}

Say one short spontaneous thing someone might naturally say immediately after waking up. One sentence is ideal, two short sentences maximum. Vary the phrasing. You may naturally allude to one supplied persistent memory if it genuinely fits, but do not force it. Do not say system ready, offer assistance, explain this prompt, or invent anything that happened while you were offline.""",
            }
        )

        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=_openrouter_headers(),
            json={
                "model": OPENROUTER_MODEL,
                "messages": messages,
                "temperature": 1.05,
                "max_tokens": 80,
            },
        )

        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                502,
                f"Wake response failed: {r.text[:500]}",
            ) from exc

        answer = (
            r.json()["choices"][0]["message"]["content"].strip()
        )

    return WakeResponse(
        entity_id=entity_id,
        text=answer,
        model=OPENROUTER_MODEL,
        state=settled_state,
        memories_used=[_memory_view(m) for m in memories],
    )


@app.post(
    "/v1/entities/{entity_id}/interact",
    response_model=InteractionResponse,
)
async def interact(entity_id: str, request: InteractionRequest):
    _require_supabase()
    _require_openrouter()

    async with httpx.AsyncClient(timeout=60) as client:
        entity = await _get_entity(client, entity_id)
        current_state = _normalized_state(
            entity.get("current_state") or {}
        )
        entity["current_state"] = current_state

        current_speaker = _speaker_from_context(request.context)
        motivation = motivation_store.update(entity_id, request.context, request.text)

        history = await _recent_interactions(
            client,
            entity_id,
        )
        history = [
            item
            for item in history
            if history_visible_to_speaker(item, current_speaker)
        ]
        memories = await _relevant_memories(
            client,
            entity_id,
            request.text,
            6,
            speaker=current_speaker,
            access_context=request.context,
        )
        pending_messages = await _pending_social_messages(
            client,
            entity_id,
            current_speaker,
            5,
        )

        messages = [
            {
                "role": "system",
                "content": _system_prompt(entity, memories),
            }
        ]

        for item in history:
            if item.get("user_text"):
                messages.append(
                    {
                        "role": "user",
                        "content": _history_user_content(item),
                    }
                )
            if item.get("assistant_text"):
                messages.append(
                    {
                        "role": "assistant",
                        "content": item["assistant_text"],
                    }
                )

        messages.append(
            {
                "role": "user",
                "content": _interaction_instruction(
                    request.text,
                    request.context,
                    pending_messages,
                    motivation.prompt_context,
                ),
            }
        )

        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=_openrouter_headers(),
            json={
                "model": OPENROUTER_MODEL,
                "messages": messages,
                "temperature": 0.80,
                "max_tokens": 420,
                "response_format": {"type": "json_object"},
            },
        )

        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                502,
                f"LLM request failed: {r.text[:500]}",
            ) from exc

        raw = r.json()["choices"][0]["message"]["content"]
        (
            answer,
            state_delta,
            memory,
            social_message_request,
            requested_delivered_ids,
        ) = _parse_interaction_payload(raw)
        answer = _guard_human_emotion_claim(answer)

        explicit_social = _explicit_social_message_request(
            request.text,
            request.context,
        )

        # Explicit "tell Jesse..." style routing is deterministic.
        # The LLM may still detect less rigid conversational requests,
        # but it cannot accidentally miss the obvious form.
        if explicit_social:
            social_message_request = explicit_social

        validated_social, social_error = _validate_social_message_request(
            social_message_request,
            request.context,
        )

        if social_error == "unresolved_recipient":
            # Deterministic truthfulness guard: if the model tried to promise a
            # message to a relationship descriptor, do not let the spoken reply
            # imply that anything was queued.
            answer = (
                "I can pass that along, but I need their name so I know "
                "exactly who to give it to."
            )
        elif social_error == "missing_recipient":
            answer = "Who should I give that message to?"
        elif social_error == "missing_message":
            answer = "Sure. What would you like me to tell them?"

        new_state = _apply_state_delta(
            current_state,
            state_delta,
        )

        social_message_created = await _save_social_message(
            client,
            entity_id,
            validated_social,
            request.context,
            request.text,
        )

        # Spoken acknowledgement must reflect what actually happened.
        # Do not address an absent recipient as though they heard the request,
        # and do not promise persistence if the mailbox write failed.
        if validated_social:
            recipient_name = validated_social["recipient"]["display_name"]

            if social_message_created:
                answer = (
                    f"Got it. I'll give {recipient_name} that message "
                    "when I recognize them."
                )
            else:
                answer = (
                    f"I understood the message for {recipient_name}, "
                    "but I couldn't save it."
                )

        await _save_interaction(
            client,
            entity_id,
            request.device_id,
            request.text,
            answer,
            OPENROUTER_MODEL,
            request.context,
        )

        if new_state != current_state:
            await _save_entity_state(
                client,
                entity_id,
                new_state,
            )

        memory_created = await _save_memory(
            client,
            entity_id,
            memory,
            request.text,
            answer,
            request.context,
        )

        messages_delivered = await _mark_social_messages_delivered(
            client,
            entity_id,
            current_speaker,
            pending_messages,
            requested_delivered_ids,
        )

    return InteractionResponse(
        entity_id=entity_id,
        text=answer,
        model=OPENROUTER_MODEL,
        state=new_state,
        memories_used=[_memory_view(m) for m in memories],
        memory_created=memory_created,
        social_message_created=social_message_created,
        messages_delivered=messages_delivered,
    )
