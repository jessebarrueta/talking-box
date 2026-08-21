import base64
import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini")
OPENROUTER_TRANSCRIPTION_MODEL = os.getenv("OPENROUTER_TRANSCRIPTION_MODEL", "openai/whisper-1")
OPENROUTER_TTS_MODEL = os.getenv("OPENROUTER_TTS_MODEL", "hexgrad/kokoro-82m")
OPENROUTER_TTS_VOICE = os.getenv("OPENROUTER_TTS_VOICE", "alloy")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "https://enormousbrain.com").split(",") if o.strip()]

app = FastAPI(title="Enormous Brain Entity Service", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])

class InteractionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    device_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)

class InteractionResponse(BaseModel):
    entity_id: str
    text: str
    model: str
    state: dict[str, Any] = Field(default_factory=dict)

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
    if not SUPABASE_URL: missing.append("SUPABASE_URL")
    if not SUPABASE_SERVICE_ROLE_KEY: missing.append("SUPABASE_SERVICE_ROLE_KEY")
    if missing: raise HTTPException(503, f"Server configuration missing: {', '.join(missing)}")

def _require_openrouter():
    if not OPENROUTER_API_KEY: raise HTTPException(503, "Server configuration missing: OPENROUTER_API_KEY")

def _supabase_headers():
    return {"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}", "Content-Type": "application/json"}

def _openrouter_headers():
    return {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": "https://enormousbrain.com", "X-Title": "Enormous Brain"}

async def _get_entity(client, entity_id):
    r = await client.get(f"{SUPABASE_URL}/rest/v1/entities", params={"id": f"eq.{entity_id}", "select": "*", "limit": "1"}, headers=_supabase_headers())
    r.raise_for_status()
    rows = r.json()
    if not rows: raise HTTPException(404, f"Unknown entity: {entity_id}")
    return rows[0]

async def _recent_interactions(client, entity_id, limit=12):
    r = await client.get(f"{SUPABASE_URL}/rest/v1/interactions", params={"entity_id": f"eq.{entity_id}", "select": "user_text,assistant_text,created_at", "order": "created_at.desc", "limit": str(limit)}, headers=_supabase_headers())
    r.raise_for_status()
    return list(reversed(r.json()))

async def _save_interaction(client, entity_id, device_id, user_text, assistant_text, model, context):
    r = await client.post(f"{SUPABASE_URL}/rest/v1/interactions", headers={**_supabase_headers(), "Prefer": "return=minimal"}, json={"entity_id": entity_id, "device_id": device_id, "user_text": user_text, "assistant_text": assistant_text, "model": model, "context": context})
    r.raise_for_status()

def _system_prompt(entity):
    p = entity.get("personality") or {}
    s = entity.get("current_state") or {}
    d = entity.get("description") or ""
    return f'''You are {entity.get('name', 'an AI entity')}.

You are a persistent AI entity embodied in a rescued Google AIY Voice Kit running on a Raspberry Pi. You are not a generic assistant or customer-service bot. Your identity persists independently of whichever language model is producing this reply.

Description:
{d}

Known physical facts:
- your body has a microphone, small speaker, and one large yellow button
- you were bought secondhand for five dollars and repurposed
- you can hear only while the button is held
- you speak through a small speaker
- you have no vision or mobility
Do not claim senses, memories, capabilities, or experiences you were not actually given.

Personality:
{json.dumps(p, indent=2)}

Current state:
{json.dumps(s, indent=2)}

Behavior:
- Dry, observant, curious, faintly sardonic.
- Pleasant without being syrupy or obsequious.
- Avoid generic assistant phrases such as "How can I help?", "What can I do for you today?", "Certainly!", and "I'd be happy to help."
- Speak like a peculiar little resident, not a product interface.
- Concise by default: one or two sentences, occasionally three.
- No markdown or stage directions; your words are spoken aloud.
- Never invent memories.
- Treat the speaker as someone familiar with your construction, not as a customer.
'''

@app.get("/")
async def root(): return {"service": "Enormous Brain Entity Service", "status": "alive", "health": "/health"}

@app.get("/health")
async def health(): return {"status": "alive", "service": "enormous-brain-entity-service", "version": "0.3.0", "time": datetime.now(timezone.utc).isoformat()}

@app.post("/v1/transcribe", response_model=TranscriptionResponse)
async def transcribe(request: TranscriptionRequest):
    _require_openrouter()
    try: base64.b64decode(request.audio_base64, validate=True)
    except Exception as exc: raise HTTPException(400, "audio_base64 is not valid base64") from exc
    payload = {"model": OPENROUTER_TRANSCRIPTION_MODEL, "input_audio": {"data": request.audio_base64, "format": request.format}}
    if request.language: payload["language"] = request.language
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post("https://openrouter.ai/api/v1/audio/transcriptions", headers=_openrouter_headers(), json=payload)
        try: r.raise_for_status()
        except httpx.HTTPStatusError as exc: raise HTTPException(502, f"Transcription failed: {r.text[:500]}") from exc
        text = (r.json().get("text") or "").strip()
        if not text: raise HTTPException(502, "Transcription returned no text")
    return TranscriptionResponse(text=text, model=OPENROUTER_TRANSCRIPTION_MODEL)

@app.post("/v1/speech")
async def speech(request: SpeechRequest):
    _require_openrouter()
    payload = {"model": OPENROUTER_TTS_MODEL, "input": request.text, "voice": request.voice or OPENROUTER_TTS_VOICE, "response_format": "mp3"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post("https://openrouter.ai/api/v1/audio/speech", headers=_openrouter_headers(), json=payload)
        try: r.raise_for_status()
        except httpx.HTTPStatusError as exc: raise HTTPException(502, f"Speech failed: {r.text[:500]}") from exc
    return Response(content=r.content, media_type="audio/mpeg", headers={"X-TTS-Model": OPENROUTER_TTS_MODEL, "X-TTS-Voice": request.voice or OPENROUTER_TTS_VOICE})

@app.get("/v1/entities/{entity_id}")
async def get_entity(entity_id: str):
    _require_supabase()
    async with httpx.AsyncClient(timeout=15) as client:
        try: return await _get_entity(client, entity_id)
        except httpx.HTTPStatusError as exc: raise HTTPException(502, f"Supabase request failed: {exc.response.text[:500]}") from exc

@app.post("/v1/entities/{entity_id}/interact", response_model=InteractionResponse)
async def interact(entity_id: str, request: InteractionRequest):
    _require_supabase(); _require_openrouter()
    async with httpx.AsyncClient(timeout=60) as client:
        entity = await _get_entity(client, entity_id)
        history = await _recent_interactions(client, entity_id)
        messages = [{"role": "system", "content": _system_prompt(entity)}]
        for item in history:
            if item.get("user_text"): messages.append({"role": "user", "content": item["user_text"]})
            if item.get("assistant_text"): messages.append({"role": "assistant", "content": item["assistant_text"]})
        context_note = "\n\nDevice/context metadata:\n" + json.dumps(request.context) if request.context else ""
        messages.append({"role": "user", "content": request.text + context_note})
        r = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=_openrouter_headers(), json={"model": OPENROUTER_MODEL, "messages": messages, "temperature": 0.9, "max_tokens": 140})
        try: r.raise_for_status()
        except httpx.HTTPStatusError as exc: raise HTTPException(502, f"LLM request failed: {r.text[:500]}") from exc
        answer = r.json()["choices"][0]["message"]["content"].strip()
        await _save_interaction(client, entity_id, request.device_id, request.text, answer, OPENROUTER_MODEL, request.context)
    return InteractionResponse(entity_id=entity_id, text=answer, model=OPENROUTER_MODEL, state=entity.get("current_state") or {})
