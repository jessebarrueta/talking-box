# Talking Box V6 — Persistent Memory + Internal State

V6 moves the project from a talking device toward a persistent entity.

## What changed

The entity now has six persistent state dimensions:

- energy
- curiosity
- confidence
- trust
- irritation
- anticipation

State changes are gradual and server-clamped. The model can suggest small deltas after each interaction, but cannot swing a state value by more than 0.12 in one turn.

The entity also retrieves persistent memories before answering. Memory creation is conservative and happens as part of the same model call that produces the spoken reply, so we avoid a second LLM round-trip just for reflection.

Memory types:

- preference
- person
- project
- event
- fact
- promise
- observation

The model returns a structured private reflection:

```json
{
  "reply": "spoken reply",
  "state_delta": {
    "energy": 0.0,
    "curiosity": 0.0,
    "confidence": 0.0,
    "trust": 0.0,
    "irritation": 0.0,
    "anticipation": 0.0
  },
  "memory": {
    "remember": false,
    "type": "fact",
    "summary": "",
    "importance": 0.0
  }
}
```

Only `reply` is spoken.

## Wake behavior

Wake now:

1. loads persistent state
2. gently settles a few dimensions based only on offline duration
3. retrieves a handful of high-importance memories
4. generates the wake remark using state + recent interaction history + persistent memory

No invented events are added while offline.

## New API

```text
GET /v1/entities/{entity_id}/memories?limit=20
```

Interaction responses now also include:

```json
{
  "state": {},
  "memories_used": [],
  "memory_created": null
}
```

The Pi can ignore these fields for now; they are there for observability.

## Install

### 1. Supabase

Run:

```text
supabase/v6_memory_state.sql
```

in the Supabase SQL editor.

### 2. Server

Replace the deployed `server/main.py` with the V6 file and restart the cPanel Python app.

Health should then report:

```json
{
  "version": "0.6.0",
  "memory": "persistent-v1",
  "state": "persistent-v1"
}
```

### 3. Test

Talk to the box a few times, then inspect:

```bash
curl -s \
  -H "Authorization: Bearer $TALKING_BOX_DEVICE_TOKEN" \
  https://api.enormousbrain.com/v1/entities/voice-box-001
```

and:

```bash
curl -s \
  -H "Authorization: Bearer $TALKING_BOX_DEVICE_TOKEN" \
  'https://api.enormousbrain.com/v1/entities/voice-box-001/memories?limit=20'
```

You should see state drift over time and durable memories appear only when the interaction is worth keeping.

## Important note

This V6 bundle intentionally does not change the Pi code. The current V5.2 Pi client already accepts the normal `/interact` response and only reads the `text` field, so the richer response is backward-compatible.

Sleep consolidation is the next increment after we confirm state + memory behavior in live use.
