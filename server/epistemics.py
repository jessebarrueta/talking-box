"""Identity/epistemic helpers for Talking Box.

This module intentionally separates three different claims that are easy for a
language model to blur together:

1. a durable recognized speaker (voice match against an enrolled profile),
2. a temporary anonymous voice cluster (same-sounding unknown voice this boot),
3. a conversational claim ("I'm Janine"), which is useful context but is not
   biometric verification.

No voice embeddings live here.
"""

from __future__ import annotations

import re
from typing import Any


KNOWN_SPEAKER_STATUSES = {"recognized"}
ANONYMOUS_SPEAKER_STATUSES = {"anonymous"}
UNVERIFIED_SPEAKER_STATUSES = {
    "anonymous",
    "unknown",
    "insufficient_audio",
    "unavailable",
    "error",
}


def normalize_speaker_context(context: Any) -> dict[str, Any] | None:
    if not isinstance(context, dict):
        return None

    raw = context.get("speaker")
    if not isinstance(raw, dict):
        return None

    status = str(raw.get("status") or "unknown").strip().lower()
    speaker_id = str(raw.get("id") or "").strip() or None
    display_name = str(raw.get("display_name") or "").strip() or speaker_id
    voice_session_id = (
        str(
            raw.get("voice_session_id")
            or context.get("voice_session_id")
            or ""
        ).strip()
        or None
    )
    anonymous_id = str(raw.get("anonymous_id") or "").strip() or None
    anonymous_key = str(raw.get("anonymous_key") or "").strip() or None

    if not anonymous_key and voice_session_id and anonymous_id:
        anonymous_key = f"{voice_session_id}:{anonymous_id}"

    result: dict[str, Any] = {
        "status": status,
        "id": speaker_id,
        "display_name": display_name,
        "voice_session_id": voice_session_id,
        "anonymous_id": anonymous_id,
        "anonymous_key": anonymous_key,
    }

    for key in ("is_new", "session_only"):
        if raw.get(key) is not None:
            result[key] = bool(raw.get(key))

    try:
        if raw.get("seen_count") is not None:
            result["seen_count"] = int(raw["seen_count"])
    except (TypeError, ValueError):
        pass

    for key in (
        "similarity",
        "margin",
        "threshold",
        "cluster_similarity",
        "cluster_margin",
        "cluster_threshold",
        "known_best_similarity",
        "known_threshold",
    ):
        try:
            if raw.get(key) is not None:
                result[key] = round(float(raw[key]), 4)
        except (TypeError, ValueError):
            pass

    return result


def speaker_key(speaker: dict[str, Any] | None) -> str | None:
    if not speaker:
        return None

    if speaker.get("status") == "recognized" and speaker.get("id"):
        return f"speaker:{speaker['id']}"

    if speaker.get("status") == "anonymous":
        anonymous_key = speaker.get("anonymous_key")
        if anonymous_key:
            return f"anonymous:{anonymous_key}"

        anonymous_id = speaker.get("anonymous_id")
        if anonymous_id:
            # Legacy V7.1 context. This is intentionally weaker because anon-1
            # can repeat after a service restart.
            return f"anonymous-legacy:{anonymous_id}"

    return None


def identity_ledger(context: Any) -> dict[str, Any]:
    speaker = normalize_speaker_context(context)

    if not speaker:
        return {
            "speaker_status": "unobserved",
            "identity_verified": False,
            "identity_basis": "none",
            "speaker_key": None,
            "allowed_identity_claim": (
                "No current identity claim is justified by sensor metadata."
            ),
        }

    status = speaker.get("status")

    if status == "recognized" and speaker.get("id"):
        return {
            "speaker_status": status,
            "identity_verified": True,
            "identity_basis": "enrolled_voice_embedding_match",
            "speaker_id": speaker.get("id"),
            "display_name": speaker.get("display_name"),
            "speaker_key": speaker_key(speaker),
            "allowed_identity_claim": (
                "The current voice matched an enrolled local speaker profile."
            ),
        }

    if status == "anonymous":
        return {
            "speaker_status": status,
            "identity_verified": False,
            "identity_basis": "temporary_same_voice_cluster",
            "speaker_id": None,
            "display_name": None,
            "anonymous_id": speaker.get("anonymous_id"),
            "anonymous_key": speaker.get("anonymous_key"),
            "speaker_key": speaker_key(speaker),
            "seen_count": speaker.get("seen_count"),
            "allowed_identity_claim": (
                "This sounds like the same unidentified voice as turns with "
                "the same anonymous_key during this voice session only."
            ),
        }

    return {
        "speaker_status": status,
        "identity_verified": False,
        "identity_basis": "insufficient_or_unavailable_voice_evidence",
        "speaker_id": None,
        "display_name": None,
        "speaker_key": None,
        "allowed_identity_claim": (
            "Do not identify the current speaker from this turn."
        ),
    }


def known_speakers_from_context(context: Any) -> list[dict[str, str]]:
    if not isinstance(context, dict):
        return []

    raw = context.get("known_speakers")
    if not isinstance(raw, list):
        return []

    result: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        speaker_id = str(item.get("id") or "").strip()
        display_name = str(item.get("display_name") or "").strip()

        if not speaker_id or speaker_id in seen_ids:
            continue

        seen_ids.add(speaker_id)
        result.append(
            {
                "id": speaker_id,
                "display_name": display_name or speaker_id,
            }
        )

    return result


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def resolve_known_speaker(
    value: str,
    context: Any,
) -> dict[str, str] | None:
    """Resolve only exact id/display-name matches.

    Deliberately *not* fuzzy. Social-message routing should fail closed rather
    than decide that "Jess" probably means Jesse or that "my husband" maps to a
    particular person.
    """

    needle = _name_key(value)
    if not needle:
        return None

    matches = []
    for speaker in known_speakers_from_context(context):
        if needle in {
            _name_key(speaker["id"]),
            _name_key(speaker["display_name"]),
        }:
            matches.append(speaker)

    if len(matches) != 1:
        return None

    return matches[0]


def history_user_content(item: dict[str, Any]) -> str:
    text = str(item.get("user_text") or "")
    speaker = normalize_speaker_context(item.get("context"))

    if not speaker:
        return text

    status = speaker.get("status")

    if status == "recognized":
        label = (
            speaker.get("display_name")
            or speaker.get("id")
            or "recognized speaker"
        )
        return f"[Verified speaker: {label}] {text}"

    if status == "anonymous":
        key = speaker.get("anonymous_key")
        if key:
            return (
                "[Unidentified speaker, temporary voice key "
                f"{key}] {text}"
            )

        anon = speaker.get("anonymous_id") or "unknown"
        return (
            "[Unidentified speaker, legacy temporary cluster "
            f"{anon}] {text}"
        )

    return f"[Speaker identity unavailable on this turn] {text}"


def memory_scope(memory: dict[str, Any]) -> tuple[str, str | None]:
    metadata = memory.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    explicit_scope = str(metadata.get("scope") or "").strip().lower()
    subject_speaker_id = (
        str(metadata.get("subject_speaker_id") or "").strip() or None
    )

    if explicit_scope in {"entity", "speaker"}:
        return explicit_scope, subject_speaker_id

    # Compatibility for V7 speaker-attributed memories that predate scope.
    source_speaker = metadata.get("speaker")
    if isinstance(source_speaker, dict):
        status = str(source_speaker.get("status") or "").strip().lower()
        source_id = str(source_speaker.get("id") or "").strip() or None

        if status == "recognized" and source_id:
            return "speaker", source_id

        if status in UNVERIFIED_SPEAKER_STATUSES:
            return "private-unverified", None

    # Pre-speaker-era memories are legacy/unscoped. Keep them available so old
    # entity facts do not disappear, but expose the scope label to the model.
    return "legacy-unscoped", None


def memory_visible_to_speaker(
    memory: dict[str, Any],
    current_speaker: dict[str, Any] | None,
) -> bool:
    scope, subject_speaker_id = memory_scope(memory)

    if scope in {"entity", "legacy-unscoped"}:
        return True

    if scope == "private-unverified":
        return False

    if scope == "speaker":
        return bool(
            current_speaker
            and current_speaker.get("status") == "recognized"
            and current_speaker.get("id")
            and current_speaker.get("id") == subject_speaker_id
        )

    return False


def memory_view_metadata(memory: dict[str, Any]) -> dict[str, Any]:
    scope, subject_speaker_id = memory_scope(memory)
    result: dict[str, Any] = {"scope": scope}

    if subject_speaker_id:
        result["subject_speaker_id"] = subject_speaker_id

    metadata = memory.get("metadata")
    if isinstance(metadata, dict):
        speaker = metadata.get("speaker")
        if isinstance(speaker, dict):
            result["speaker"] = speaker

    return result


def sender_descriptor(speaker: dict[str, Any] | None) -> dict[str, Any]:
    if speaker and speaker.get("status") == "recognized" and speaker.get("id"):
        return {
            "status": "recognized",
            "speaker_id": speaker.get("id"),
            "display_name": speaker.get("display_name") or speaker.get("id"),
            "voice_session_id": speaker.get("voice_session_id"),
            "anonymous_id": None,
            "anonymous_key": None,
        }

    if speaker and speaker.get("status") == "anonymous":
        return {
            "status": "anonymous",
            "speaker_id": None,
            "display_name": None,
            "voice_session_id": speaker.get("voice_session_id"),
            "anonymous_id": speaker.get("anonymous_id"),
            "anonymous_key": speaker.get("anonymous_key"),
        }

    return {
        "status": (speaker or {}).get("status") or "unknown",
        "speaker_id": None,
        "display_name": None,
        "voice_session_id": (speaker or {}).get("voice_session_id"),
        "anonymous_id": None,
        "anonymous_key": None,
    }
