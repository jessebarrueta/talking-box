#!/usr/bin/env python3
"""Apply Talking Box V7.2 — epistemic identity + grounded social mailbox.

Run from a clean talking-box checkout:
    python3 /path/to/apply-v7.2.py .

The patcher refuses to run on a dirty worktree and does not create in-repo
backup files. Git is the rollback mechanism.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


PI_IDENTITY_BLOCK = r'''def _known_speakers_context():
    if speaker_identity is None:
        return []

    try:
        return [
            {
                "id": row.get("id"),
                "display_name": (
                    row.get("display_name")
                    or row.get("id")
                ),
            }
            for row in speaker_identity.list_speakers()
            if row.get("id")
        ]
    except Exception as exc:
        print(
            "Could not build known-speaker context: "
            f"{type(exc).__name__}: {exc}"
        )
        return []


def _speaker_with_session_metadata(result):
    decorated = dict(result or {})
    decorated["voice_session_id"] = VOICE_SESSION_ID

    if decorated.get("status") == "anonymous":
        anonymous_id = decorated.get("anonymous_id")
        if anonymous_id:
            decorated["anonymous_key"] = (
                f"{VOICE_SESSION_ID}:{anonymous_id}"
            )

    return decorated


def identify_speaker(path):
    if speaker_identity is None:
        return _speaker_with_session_metadata(
            {
                "status": "unavailable",
                "id": None,
                "display_name": None,
            }
        )

    try:
        embedding = speaker_identity.embedding_from_wav(path)
        result = speaker_identity.identify_embedding(embedding)

        if (
            result.get("status") == "unknown"
            and anonymous_speakers is not None
        ):
            anonymous = anonymous_speakers.observe(embedding)
            anonymous["known_best_similarity"] = result.get("similarity")
            anonymous["known_threshold"] = speaker_identity.threshold
            result = anonymous

        result = _speaker_with_session_metadata(result)

        print(
            "Speaker identity: "
            + json.dumps(result, sort_keys=True)
        )
        return result

    except ValueError as exc:
        result = _speaker_with_session_metadata(
            {
                "status": "insufficient_audio",
                "id": None,
                "display_name": None,
                "reason": str(exc),
            }
        )
        print(
            "Speaker identity: "
            + json.dumps(result, sort_keys=True)
        )
        return result

    except Exception as exc:
        print(
            "Speaker identity failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return _speaker_with_session_metadata(
            {
                "status": "error",
                "id": None,
                "display_name": None,
            }
        )


def device_context():
    return {
        "embodiment": (
            "Google AIY Voice Kit "
            "on Raspberry Pi 4"
        ),
        "input": (
            "push-to-talk yellow button"
        ),
        "microphone": (
            "AIY Voice HAT microphone"
        ),
        "speaker": (
            "AIY Voice HAT speaker"
        ),
        "vision": False,
        "mobility": False,
        "voice_session_id": VOICE_SESSION_ID,
        "known_speakers": _known_speakers_context(),
    }


'''

SERVER_IMPORTS = r'''try:
    from .epistemics import (
        history_user_content as epistemic_history_user_content,
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
        identity_ledger,
        known_speakers_from_context,
        memory_scope,
        memory_view_metadata,
        memory_visible_to_speaker,
        normalize_speaker_context,
        resolve_known_speaker,
        sender_descriptor,
    )
'''

SERVER_SPEAKER_WRAPPER = r'''def _speaker_from_context(context):
    return normalize_speaker_context(context)


'''

SERVER_MEMORY_VIEW = r'''def _memory_view(memory):
    view = {
        "type": memory.get("memory_type") or "fact",
        "summary": memory.get("summary") or "",
        "importance": float(memory.get("importance") or 0.5),
    }
    view.update(memory_view_metadata(memory))
    return view


'''

SERVER_HISTORY_WRAPPER = r'''def _history_user_content(item):
    return epistemic_history_user_content(item)


'''

SERVER_RELEVANT_MEMORIES = r'''async def _relevant_memories(
    client,
    entity_id,
    query,
    limit=6,
    speaker=None,
):
    memories = await _recent_memories(client, entity_id, 40)
    memories = [
        memory
        for memory in memories
        if memory_visible_to_speaker(memory, speaker)
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


'''

SERVER_SAVE_MEMORY_AND_SOCIAL = r'''async def _save_memory(
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

    if speaker and speaker.get("status") == "recognized" and speaker.get("id"):
        if requested_scope == "entity":
            scope = "entity"
            subject_speaker_id = None
        else:
            scope = "speaker"
            subject_speaker_id = speaker.get("id")
    else:
        # Unknown/anonymous speakers cannot create durable person-specific
        # memory. Entity facts are allowed only when the model explicitly marks
        # them as entity scope.
        if requested_scope != "entity":
            return None
        scope = "entity"
        subject_speaker_id = None

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

    if _mailbox_missing(r):
        print(
            "Social mailbox table is not installed; "
            "run supabase/v7_2_social_messages.sql",
            flush=True,
        )
        return []

    r.raise_for_status()
    return r.json()


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


'''

SERVER_INTERACTION_INSTRUCTION = r'''def _interaction_instruction(
    user_text,
    context,
    pending_messages=None,
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

Pending social messages addressed to THIS verified speaker only:
{json.dumps(pending_view, indent=2)}

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

IDENTITY / EPISTEMIC RULES:
- The identity evidence ledger is authoritative. Do not override it with conversational inference.
- "recognized" means the current voice matched an enrolled local profile. Only then may you state the current speaker's real identity as verified.
- "anonymous" means only that the voice matches the same temporary anonymous_key during this voice session. anonymous_key is not a real-world identity and expires with the device process.
- Relationship words, symmetry, topic, writing style, age, gender, and conversational context NEVER establish speaker identity.
- In particular: one unknown person saying "tell my husband" and another unknown person later saying "did my wife leave a message" does NOT establish that they are spouses or identify either person.
- If an anonymous person tells you a name, you may treat it as a self-reported conversational claim, but not as verified voice identity. Do not unlock speaker-scoped memory because of a claimed name.
- Never apply a recognized person's private memory to another recognized person or to an anonymous speaker.

MEMORY RULES:
- memory should be conservative. Save durable preferences, people/relationships, ongoing projects, important events, promises, stable facts, or observations likely to matter later.
- scope="speaker" means the memory is private to the CURRENT VERIFIED speaker. It is the default for human-specific facts/preferences when identity is recognized.
- scope="entity" is only for durable facts about Jerry/the device/shared world that are genuinely not person-private.
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
"""


'''

SERVER_PARSE = r'''def _parse_interaction_payload(raw):
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


'''

SERVER_INTERACT = r'''@app.post(
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

        history = await _recent_interactions(
            client,
            entity_id,
        )
        memories = await _relevant_memories(
            client,
            entity_id,
            request.text,
            6,
            speaker=current_speaker,
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
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


def replace_between(
    text: str,
    start_marker: str,
    end_marker: str,
    new: str,
    label: str,
) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:start] + new + text[end:]


def check_clean_git(root: Path) -> None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        raise RuntimeError(
            "Could not verify git worktree state. Run from the repository root."
        ) from exc

    if result.stdout.strip():
        raise RuntimeError(
            "Worktree is not clean. Commit/stash current changes before V7.2."
        )


def patch_pi(text: str) -> str:
    text = replace_once(
        text,
        "import time\nfrom datetime import datetime, timezone\n",
        "import time\nimport uuid\nfrom datetime import datetime, timezone\n",
        "Pi uuid import",
    )

    text = replace_once(
        text,
        'DEVICE_ID = "aiy-voice-pi4-001"\n',
        'DEVICE_ID = "aiy-voice-pi4-001"\nVOICE_SESSION_ID = uuid.uuid4().hex[:12]\n',
        "Pi voice session id",
    )

    text = replace_between(
        text,
        "def identify_speaker(path):",
        "def interact(text, speaker=None):",
        PI_IDENTITY_BLOCK,
        "Pi identity/context block",
    )

    count = text.count("Talking Box V7.1")
    if count != 2:
        raise RuntimeError(
            f"Pi version marker: expected 2 V7.1 strings, found {count}"
        )
    text = text.replace("Talking Box V7.1", "Talking Box V7.2")
    return text


def patch_server(text: str) -> str:
    text = replace_once(
        text,
        "from pydantic import BaseModel, Field\n",
        "from pydantic import BaseModel, Field\n\n" + SERVER_IMPORTS,
        "server epistemics import",
    )

    text = replace_once(
        text,
        'version="0.7.1",',
        'version="0.7.2",',
        "FastAPI version",
    )
    text = replace_once(
        text,
        '"version": "0.7.1",',
        '"version": "0.7.2",',
        "health version",
    )

    text = replace_once(
        text,
        "    memory_created: dict[str, Any] | None = None\n",
        "    memory_created: dict[str, Any] | None = None\n"
        "    social_message_created: dict[str, Any] | None = None\n"
        "    messages_delivered: list[int] = Field(default_factory=list)\n",
        "interaction response social fields",
    )

    text = replace_between(
        text,
        "def _speaker_from_context(context):",
        "def _memory_view(memory):",
        SERVER_SPEAKER_WRAPPER,
        "speaker wrapper",
    )

    text = replace_between(
        text,
        "def _memory_view(memory):",
        "def _history_user_content(item):",
        SERVER_MEMORY_VIEW,
        "memory view",
    )

    text = replace_between(
        text,
        "def _history_user_content(item):",
        "def _tokens(text):",
        SERVER_HISTORY_WRAPPER,
        "history wrapper",
    )

    text = replace_between(
        text,
        "async def _relevant_memories(client, entity_id, query, limit=6):",
        "async def _save_interaction(",
        SERVER_RELEVANT_MEMORIES,
        "relevant memories",
    )

    text = replace_between(
        text,
        "async def _save_memory(\n",
        "def _system_prompt(entity, memories=None):",
        SERVER_SAVE_MEMORY_AND_SOCIAL,
        "memory + social persistence",
    )

    text = replace_once(
        text,
        "Persistent memories may include speaker attribution. Never apply a memory attributed to one recognized person to a different recognized or anonymous speaker.\n",
        "Persistent memories may include speaker attribution and scope. The server has already filtered speaker-scoped memories so they are visible only to the verified matching speaker. Never infer or reconstruct another person's private memory from conversational clues.\n",
        "system prompt memory privacy",
    )

    text = replace_between(
        text,
        "def _interaction_instruction(user_text, context):",
        "def _parse_interaction_payload(raw):",
        SERVER_INTERACTION_INSTRUCTION,
        "interaction instruction",
    )

    text = replace_between(
        text,
        "def _parse_interaction_payload(raw):",
        "def _offline_text(seconds):",
        SERVER_PARSE,
        "interaction parser",
    )

    interact_start = text.find(
        '@app.post(\n    "/v1/entities/{entity_id}/interact",'
    )
    if interact_start < 0:
        raise RuntimeError("interact endpoint marker not found")
    text = text[:interact_start] + SERVER_INTERACT + "\n"

    text = replace_once(
        text,
        '        "state": "persistent-v1",\n',
        '        "state": "persistent-v1",\n'
        '        "identity_grounding": "epistemic-v1",\n'
        '        "social_mailbox": "grounded-v1",\n',
        "health features",
    )

    return text


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: apply-v7.2.py /path/to/talking-box", file=sys.stderr)
        return 2

    root = Path(sys.argv[1]).expanduser().resolve()
    pi_path = root / "pi" / "talking_box.py"
    server_path = root / "server" / "main.py"

    required = [pi_path, server_path]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        print("Missing expected repository files:", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 2

    try:
        check_clean_git(root)

        new_paths = {
            root / "server" / "epistemics.py": Path(__file__).parent / "server" / "epistemics.py",
            root / "supabase" / "v7_2_social_messages.sql": Path(__file__).parent / "supabase" / "v7_2_social_messages.sql",
            root / "tests" / "test_epistemics.py": Path(__file__).parent / "tests" / "test_epistemics.py",
            root / "README-V7.2.md": Path(__file__).parent / "README-V7.2.md",
        }

        already = [str(path) for path in new_paths if path.exists()]
        if already:
            raise RuntimeError(
                "V7.2 target files already exist; refusing to apply twice: "
                + ", ".join(already)
            )

        current_pi = pi_path.read_text()
        current_server = server_path.read_text()

        patched_pi = patch_pi(current_pi)
        patched_server = patch_server(current_server)

        # All transformations succeeded; now write atomically enough for a git
        # worktree (no .bak files are created inside the repo).
        pi_path.write_text(patched_pi)
        server_path.write_text(patched_server)

        for dest, source in new_paths.items():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)

    except Exception as exc:
        print(f"V7.2 patch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("If any files changed, use git restore before retrying.", file=sys.stderr)
        return 1

    print("Applied Talking Box V7.2 epistemic social layer.")
    print()
    print("Validate:")
    print(
        "  python3 -m py_compile "
        "pi/talking_box.py server/main.py server/epistemics.py"
    )
    print("  python3 -m unittest tests/test_epistemics.py")
    print("  git diff --check")
    print("  git diff")
    print()
    print("Before deploying server/main.py, run:")
    print("  supabase/v7_2_social_messages.sql")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
