# Identity, relationship, and disclosure helpers for Talking Box V7.3.
#
# Deliberately separates biometric verification, tentative biometric
# hypotheses, anonymous voice continuity, conversational self-claims,
# relationships, and disclosure policy.

from __future__ import annotations

import re
from typing import Any

KNOWN_SPEAKER_STATUSES = {"recognized"}
PROBABLE_SPEAKER_STATUSES = {"probable"}
ANONYMOUS_SPEAKER_STATUSES = {"anonymous"}
UNVERIFIED_SPEAKER_STATUSES = {
    "probable",
    "anonymous",
    "unknown",
    "insufficient_audio",
    "unavailable",
    "error",
}

FAMILY_RELATIONSHIPS = {
    "parent",
    "child",
    "sibling",
    "spouse",
    "partner",
    "guardian",
    "household",
}
GUARDIAN_RELATIONSHIPS = {"guardian"}

MEMORY_VISIBILITIES = {
    "public",
    "subject",
    "private",
    "household",
    "guardian",
    "participants",
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
    candidate_id = str(raw.get("candidate_id") or "").strip() or speaker_id
    candidate_display_name = (
        str(raw.get("candidate_display_name") or "").strip()
        or display_name
        or candidate_id
    )
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
        "candidate_id": candidate_id,
        "candidate_display_name": candidate_display_name,
        "voice_session_id": voice_session_id,
        "anonymous_id": anonymous_id,
        "anonymous_key": anonymous_key,
    }

    for key in (
        "is_new",
        "session_only",
        "needs_confirmation",
        "identity_verified",
    ):
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
        "best_sample_similarity",
        "cluster_similarity",
        "cluster_margin",
        "cluster_threshold",
        "known_best_similarity",
        "known_threshold",
        "recent_verified_age_seconds",
    ):
        try:
            if raw.get(key) is not None:
                result[key] = round(float(raw[key]), 4)
        except (TypeError, ValueError):
            pass

    if raw.get("probable_basis"):
        result["probable_basis"] = str(raw["probable_basis"])

    return result


def speaker_key(speaker: dict[str, Any] | None) -> str | None:
    if not speaker:
        return None

    if speaker.get("status") == "recognized" and speaker.get("id"):
        return f"speaker:{speaker['id']}"

    if speaker.get("status") == "probable" and speaker.get("candidate_id"):
        return f"probable:{speaker['candidate_id']}"

    if speaker.get("status") == "anonymous":
        anonymous_key = speaker.get("anonymous_key")
        if anonymous_key:
            return f"anonymous:{anonymous_key}"

        anonymous_id = speaker.get("anonymous_id")
        if anonymous_id:
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

    if status == "probable" and speaker.get("candidate_id"):
        name = (
            speaker.get("candidate_display_name")
            or speaker.get("candidate_id")
        )
        return {
            "speaker_status": status,
            "identity_verified": False,
            "identity_basis": (
                speaker.get("probable_basis")
                or "weak_enrolled_voice_match"
            ),
            "candidate_speaker_id": speaker.get("candidate_id"),
            "candidate_display_name": name,
            "speaker_key": speaker_key(speaker),
            "similarity": speaker.get("similarity"),
            "margin": speaker.get("margin"),
            "recent_verified_age_seconds": speaker.get(
                "recent_verified_age_seconds"
            ),
            "allowed_identity_claim": (
                f"The voice may be {name}, but this is not verified. "
                f"You may conversationally ask something like "
                f"'I think that's {name}—is that you?' Never describe the "
                f"identity as verified and do not unlock private data."
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


def relationships_from_context(context: Any) -> list[dict[str, str]]:
    if not isinstance(context, dict):
        return []

    raw = context.get("relationships")
    if not isinstance(raw, list):
        return []

    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        source = str(item.get("from") or "").strip()
        target = str(item.get("to") or "").strip()
        relation = str(item.get("type") or "").strip().lower()
        if source and target and relation:
            result.append(
                {"from": source, "to": target, "type": relation}
            )
    return result


def relationship_types(
    viewer_id: str | None,
    subject_id: str | None,
    context: Any,
) -> set[str]:
    if not viewer_id or not subject_id:
        return set()

    result = set()
    for rel in relationships_from_context(context):
        if rel["from"] == viewer_id and rel["to"] == subject_id:
            result.add(rel["type"])
        elif (
            rel["type"] in FAMILY_RELATIONSHIPS
            and rel["from"] == subject_id
            and rel["to"] == viewer_id
        ):
            result.add(rel["type"])
    return result


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def resolve_known_speaker(
    value: str,
    context: Any,
) -> dict[str, str] | None:
    # Deliberately exact: relationship language does not establish identity.
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

    if status == "probable":
        label = (
            speaker.get("candidate_display_name")
            or speaker.get("candidate_id")
            or "unknown candidate"
        )
        return f"[Probable speaker: {label}; NOT verified] {text}"

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


def history_visible_to_speaker(
    item: dict[str, Any],
    current_speaker: dict[str, Any] | None,
) -> bool:
    # Raw conversation history is private by default.
    source = normalize_speaker_context(item.get("context"))

    if source is None:
        return bool(
            current_speaker
            and current_speaker.get("status") == "recognized"
        )

    if not current_speaker:
        return False

    source_status = source.get("status")
    current_status = current_speaker.get("status")

    if source_status == "recognized":
        return bool(
            current_status == "recognized"
            and source.get("id")
            and source.get("id") == current_speaker.get("id")
        )

    if source_status == "probable":
        source_id = source.get("candidate_id")
        if not source_id:
            return False
        if current_status == "recognized":
            return current_speaker.get("id") == source_id
        if current_status == "probable":
            return current_speaker.get("candidate_id") == source_id
        return False

    if source_status == "anonymous":
        return bool(
            current_status == "anonymous"
            and source.get("anonymous_key")
            and source.get("anonymous_key")
            == current_speaker.get("anonymous_key")
        )

    return False


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

    source_speaker = metadata.get("speaker")
    if isinstance(source_speaker, dict):
        status = str(source_speaker.get("status") or "").strip().lower()
        source_id = str(source_speaker.get("id") or "").strip() or None

        if status == "recognized" and source_id:
            return "speaker", source_id

        if status in UNVERIFIED_SPEAKER_STATUSES:
            return "private-unverified", None

    return "legacy-unscoped", None


def memory_visibility(memory: dict[str, Any]) -> str:
    metadata = memory.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    explicit = str(metadata.get("visibility") or "").strip().lower()
    if explicit in MEMORY_VISIBILITIES:
        return explicit

    scope, _ = memory_scope(memory)
    if scope == "entity":
        return "public"
    if scope == "speaker":
        return "subject"
    if scope == "legacy-unscoped":
        return "legacy"
    return "private"


def memory_visible_to_speaker(
    memory: dict[str, Any],
    current_speaker: dict[str, Any] | None,
    access_context: Any = None,
) -> bool:
    scope, subject_speaker_id = memory_scope(memory)
    visibility = memory_visibility(memory)

    if scope == "private-unverified":
        return False

    if visibility == "public":
        return True

    if visibility == "legacy":
        return bool(
            current_speaker
            and current_speaker.get("status") == "recognized"
        )

    if not (
        current_speaker
        and current_speaker.get("status") == "recognized"
        and current_speaker.get("id")
    ):
        return False

    viewer_id = current_speaker["id"]

    if subject_speaker_id and viewer_id == subject_speaker_id:
        return True

    if visibility in {"subject", "private"}:
        return False

    if visibility == "household":
        rels = relationship_types(
            viewer_id,
            subject_speaker_id,
            access_context,
        )
        return bool(rels & FAMILY_RELATIONSHIPS)

    if visibility == "guardian":
        for rel in relationships_from_context(access_context):
            if (
                rel["from"] == viewer_id
                and rel["to"] == subject_speaker_id
                and rel["type"] in GUARDIAN_RELATIONSHIPS
            ):
                return True
        return False

    if visibility == "participants":
        metadata = memory.get("metadata")
        participants = (
            metadata.get("participant_speaker_ids")
            if isinstance(metadata, dict)
            else None
        )
        return bool(
            isinstance(participants, list)
            and viewer_id in {str(v) for v in participants}
        )

    return False


def memory_view_metadata(memory: dict[str, Any]) -> dict[str, Any]:
    scope, subject_speaker_id = memory_scope(memory)
    result: dict[str, Any] = {
        "scope": scope,
        "visibility": memory_visibility(memory),
    }

    if subject_speaker_id:
        result["subject_speaker_id"] = subject_speaker_id

    metadata = memory.get("metadata")
    if isinstance(metadata, dict):
        speaker = metadata.get("speaker")
        if isinstance(speaker, dict):
            result["speaker"] = speaker

    return result


def sender_descriptor(speaker: dict[str, Any] | None) -> dict[str, Any]:
    if (
        speaker
        and speaker.get("status") == "recognized"
        and speaker.get("id")
    ):
        return {
            "status": "recognized",
            "speaker_id": speaker.get("id"),
            "display_name": (
                speaker.get("display_name")
                or speaker.get("id")
            ),
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

    # Probable identity remains an unidentified sender.
    return {
        "status": (speaker or {}).get("status") or "unknown",
        "speaker_id": None,
        "display_name": None,
        "voice_session_id": (speaker or {}).get("voice_session_id"),
        "anonymous_id": None,
        "anonymous_key": None,
    }
