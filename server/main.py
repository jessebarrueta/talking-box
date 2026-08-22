import base64
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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

app = FastAPI(
    title="Enormous Brain Entity Service",
    version="0.6.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


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
    audio_base64: str = Field(min_length=1)
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
    if not isinstance(context, dict):
        return None

    raw = context.get("speaker")
    if not isinstance(raw, dict):
        return None

    status = str(raw.get("status") or "").strip().lower()
    speaker_id = str(raw.get("id") or "").strip() or None
    display_name = (
        str(raw.get("display_name") or "").strip()
        or speaker_id
    )

    speaker = {
        "status": status or "unknown",
        "id": speaker_id,
        "display_name": display_name,
    }

    for key in ("similarity", "margin", "threshold"):
        try:
            if raw.get(key) is not None:
                speaker[key] = round(float(raw[key]), 4)
        except (TypeError, ValueError):
            pass

    return speaker


def _memory_view(memory):
    view = {
        "type": memory.get("memory_type") or "fact",
        "summary": memory.get("summary") or "",
        "importance": float(memory.get("importance") or 0.5),
    }

    metadata = memory.get("metadata")
    if isinstance(metadata, dict):
        speaker = metadata.get("speaker")
        if isinstance(speaker, dict):
            view["speaker"] = speaker

    return view


def _history_user_content(item):
    text = item.get("user_text") or ""
    speaker = _speaker_from_context(item.get("context"))

    if not speaker:
        return text

    if speaker.get("status") == "recognized":
        label = (
            speaker.get("display_name")
            or speaker.get("id")
            or "recognized speaker"
        )
        return f"[Speaker: {label}] {text}"

    if speaker.get("status") == "unknown":
        return f"[Speaker: unknown] {text}"

    return text


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


async def _relevant_memories(client, entity_id, query, limit=6):
    memories = await _recent_memories(client, entity_id, 40)
    if not memories:
        return []

    query_tokens = _tokens(query)
    ranked = sorted(
        memories,
        key=lambda item: _memory_score(item, query_tokens),
        reverse=True,
    )

    # If nothing overlaps, use a few high-importance recent memories rather
    # than dumping an arbitrary long history into the prompt.
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

    # Exact-summary dedupe. A later semantic dedupe pass can replace this.
    check = await client.get(
        f"{SUPABASE_URL}/rest/v1/memories",
        params={
            "entity_id": f"eq.{entity_id}",
            "summary": f"eq.{summary}",
            "select": "id,memory_type,summary,importance,created_at",
            "limit": "1",
        },
        headers=_supabase_headers(),
    )
    check.raise_for_status()
    existing = check.json()
    if existing:
        return _memory_view(existing[0])

    payload = {
        "entity_id": entity_id,
        "memory_type": memory_type,
        "content": summary[:1000],
        "summary": summary[:1000],
        "importance": round(importance, 3),
        "metadata": {
            "source_user_text": user_text[:2000],
            "source_assistant_text": assistant_text[:2000],
            "created_by": "interaction-reflection-v2-speaker-aware",
            "speaker": _speaker_from_context(context),
        },
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

Behavior: dry, observant, curious, faintly sardonic; pleasant without being syrupy; avoid generic assistant phrases; concise by default; no markdown or stage directions; treat the speaker as someone familiar with your construction, not a customer."""


def _interaction_instruction(user_text, context):
    return f"""Respond to the user's message and privately reflect on how the interaction should affect your persistent state.

User message:
{user_text}

Device/context metadata:
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
    "summary": "",
    "importance": 0.0
  }}
}}

Rules:
- reply should sound natural when spoken aloud; usually 1-3 short sentences.
- each state_delta should normally be between -0.05 and +0.05. Use 0 when nothing meaningfully changed.
- memory should be conservative. Save durable preferences, people/relationships, ongoing projects, important events, promises, stable facts, or observations likely to matter later.
- do NOT save routine small talk, temporary wording, obvious context, or facts already represented by the supplied memories.
- memory summary must be concise and self-contained.
- speaker identity comes only from Device/context metadata. Never guess identity from wording, topic, age, gender, or the transcript itself.
- if context.speaker.status is "recognized", use that person's display name in durable person-specific memory summaries instead of the generic word "User".
- if context.speaker.status is "unknown", do not create durable person-specific memories. Entity/device-global facts may still be remembered.
- speaker similarity is a cosine-similarity signal, not certainty or a probability. If identity metadata is uncertain, behave accordingly.
- allowed memory types: preference, person, project, event, fact, promise, observation.
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
        return (
            reply,
            payload.get("state_delta") or {},
            payload.get("memory") or {},
        )
    except Exception:
        # Avoid silencing the entity if a model/provider ignores JSON mode.
        return text, {}, {"remember": False}


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
        "version": "0.6.0",
        "memory": "persistent-v1",
        "state": "persistent-v1",
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
        memories = await _relevant_memories(
            client,
            entity_id,
            "",
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

        history = await _recent_interactions(
            client,
            entity_id,
        )
        memories = await _relevant_memories(
            client,
            entity_id,
            request.text,
            6,
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
                ),
            }
        )

        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=_openrouter_headers(),
            json={
                "model": OPENROUTER_MODEL,
                "messages": messages,
                "temperature": 0.85,
                "max_tokens": 300,
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
        answer, state_delta, memory = _parse_interaction_payload(raw)

        new_state = _apply_state_delta(
            current_state,
            state_delta,
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

    return InteractionResponse(
        entity_id=entity_id,
        text=answer,
        model=OPENROUTER_MODEL,
        state=new_state,
        memories_used=[_memory_view(m) for m in memories],
        memory_created=memory_created,
    )
