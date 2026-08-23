#!/usr/bin/env python3
# Apply Talking Box V7.3: probable identity + relationship-aware disclosure.
# Run from the talking-box repository root.

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path.cwd()

def read(rel):
    p = ROOT / rel
    if not p.exists():
        raise SystemExit(f"Missing expected file: {rel}")
    return p.read_text()

def backup(rel):
    p = ROOT / rel
    b = p.with_name(p.name + ".bak-v7.3")
    if not b.exists():
        shutil.copy2(p, b)

def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit(f"Patch anchor not found: {label}")
    if text.count(old) != 1:
        raise SystemExit(
            f"Patch anchor is not unique ({text.count(old)} matches): {label}"
        )
    return text.replace(old, new, 1)

EPISTEMICS_V73 = r'''# Identity, relationship, and disclosure helpers for Talking Box V7.3.
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
'''

TESTS_V73 = r'''import unittest

from server.epistemics import (
    history_user_content,
    history_visible_to_speaker,
    identity_ledger,
    memory_visible_to_speaker,
    normalize_speaker_context,
    resolve_known_speaker,
    speaker_key,
)


class EpistemicsTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            "voice_session_id": "session-a",
            "known_speakers": [
                {"id": "jesse", "display_name": "Jesse"},
                {"id": "greyson", "display_name": "Greyson"},
            ],
            "relationships": [
                {"from": "jesse", "to": "greyson", "type": "parent"},
            ],
        }

    def test_recognized_speaker_is_verified(self):
        context = {
            **self.context,
            "speaker": {
                "status": "recognized",
                "id": "jesse",
                "display_name": "Jesse",
                "similarity": 0.72,
            },
        }
        speaker = normalize_speaker_context(context)
        self.assertEqual(speaker_key(speaker), "speaker:jesse")
        ledger = identity_ledger(context)
        self.assertTrue(ledger["identity_verified"])
        self.assertEqual(ledger["speaker_id"], "jesse")

    def test_probable_speaker_is_tentative_not_verified(self):
        context = {
            **self.context,
            "speaker": {
                "status": "probable",
                "candidate_id": "jesse",
                "candidate_display_name": "Jesse",
                "similarity": 0.53,
                "margin": 0.2,
                "probable_basis": "weak_enrolled_voice_match",
            },
        }
        speaker = normalize_speaker_context(context)
        self.assertEqual(speaker_key(speaker), "probable:jesse")
        ledger = identity_ledger(context)
        self.assertFalse(ledger["identity_verified"])
        self.assertEqual(ledger["candidate_speaker_id"], "jesse")
        self.assertIn(
            "not verified",
            ledger["allowed_identity_claim"].lower(),
        )

    def test_relationship_language_does_not_resolve_a_person(self):
        self.assertIsNone(
            resolve_known_speaker("my husband", self.context)
        )
        self.assertEqual(
            resolve_known_speaker("Jesse", self.context)["id"],
            "jesse",
        )

    def test_speaker_scoped_memory_is_private_by_default(self):
        memory = {
            "metadata": {
                "scope": "speaker",
                "subject_speaker_id": "jesse",
            }
        }
        jesse = {"status": "recognized", "id": "jesse"}
        greyson = {"status": "recognized", "id": "greyson"}
        probable = {
            "status": "probable",
            "candidate_id": "jesse",
        }

        self.assertTrue(
            memory_visible_to_speaker(memory, jesse, self.context)
        )
        self.assertFalse(
            memory_visible_to_speaker(memory, greyson, self.context)
        )
        self.assertFalse(
            memory_visible_to_speaker(memory, probable, self.context)
        )

    def test_household_memory_can_cross_family_relationship(self):
        memory = {
            "metadata": {
                "scope": "speaker",
                "subject_speaker_id": "greyson",
                "visibility": "household",
            }
        }
        jesse = {"status": "recognized", "id": "jesse"}
        self.assertTrue(
            memory_visible_to_speaker(memory, jesse, self.context)
        )

    def test_parent_is_not_silently_promoted_to_guardian(self):
        memory = {
            "metadata": {
                "scope": "speaker",
                "subject_speaker_id": "greyson",
                "visibility": "guardian",
            }
        }
        jesse = {"status": "recognized", "id": "jesse"}
        self.assertFalse(
            memory_visible_to_speaker(memory, jesse, self.context)
        )

        guardian_context = {
            **self.context,
            "relationships": [
                {
                    "from": "jesse",
                    "to": "greyson",
                    "type": "guardian",
                },
            ],
        }
        self.assertTrue(
            memory_visible_to_speaker(
                memory,
                jesse,
                guardian_context,
            )
        )

    def test_legacy_memory_hidden_from_unverified(self):
        memory = {"metadata": {}}
        anonymous = {
            "status": "anonymous",
            "anonymous_key": "session-a:anon-1",
        }
        probable = {
            "status": "probable",
            "candidate_id": "jesse",
        }
        recognized = {"status": "recognized", "id": "jesse"}

        self.assertFalse(
            memory_visible_to_speaker(
                memory,
                anonymous,
                self.context,
            )
        )
        self.assertFalse(
            memory_visible_to_speaker(
                memory,
                probable,
                self.context,
            )
        )
        self.assertTrue(
            memory_visible_to_speaker(
                memory,
                recognized,
                self.context,
            )
        )

    def test_raw_history_does_not_cross_recognized_people(self):
        item = {
            "user_text": "private thought",
            "context": {
                "speaker": {
                    "status": "recognized",
                    "id": "greyson",
                    "display_name": "Greyson",
                }
            },
        }
        jesse = {"status": "recognized", "id": "jesse"}
        greyson = {"status": "recognized", "id": "greyson"}

        self.assertFalse(
            history_visible_to_speaker(item, jesse)
        )
        self.assertTrue(
            history_visible_to_speaker(item, greyson)
        )

    def test_probable_history_label_is_tentative(self):
        item = {
            "user_text": "hello",
            "context": {
                "speaker": {
                    "status": "probable",
                    "candidate_id": "jesse",
                    "candidate_display_name": "Jesse",
                }
            },
        }
        rendered = history_user_content(item)
        self.assertIn("Probable speaker: Jesse", rendered)
        self.assertIn("NOT verified", rendered)


if __name__ == "__main__":
    unittest.main()
'''

backup("server/epistemics.py")

main = read("server/main.py")
backup("server/main.py")

# Import helper in both relative and fallback import blocks.
needle = (
    "        history_user_content as epistemic_history_user_content,\n"
    "        identity_ledger,\n"
)
replacement = (
    "        history_user_content as epistemic_history_user_content,\n"
    "        history_visible_to_speaker,\n"
    "        identity_ledger,\n"
)
if main.count(needle) != 2:
    raise SystemExit(
        f"Expected two server import anchors; found {main.count(needle)}"
    )
main = main.replace(needle, replacement)

main = replace_once(
    main,
    '    version="0.7.2",\n',
    '    version="0.7.3",\n',
    "server version",
)

main = replace_once(
    main,
    '''async def _relevant_memories(
    client,
    entity_id,
    query,
    limit=6,
    speaker=None,
):
''',
    '''async def _relevant_memories(
    client,
    entity_id,
    query,
    limit=6,
    speaker=None,
    access_context=None,
):
''',
    "relevant memories signature",
)
main = replace_once(
    main,
    "        if memory_visible_to_speaker(memory, speaker)\n",
    "        if memory_visible_to_speaker(memory, speaker, access_context)\n",
    "relevant memories disclosure filter",
)

main = replace_once(
    main,
    '''    speaker = _speaker_from_context(context)
    requested_scope = str(memory.get("scope") or "").strip().lower()

    if speaker and speaker.get("status") == "recognized" and speaker.get("id"):
        if requested_scope == "entity":
            scope = "entity"
            subject_speaker_id = None
        else:
            scope = "speaker"
            subject_speaker_id = speaker.get("id")
    else:
''',
    '''    speaker = _speaker_from_context(context)
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
''',
    "memory visibility selection",
)

main = replace_once(
    main,
    '''        if requested_scope != "entity":
            return None
        scope = "entity"
        subject_speaker_id = None
''',
    '''        if requested_scope != "entity":
            return None
        scope = "entity"
        subject_speaker_id = None
        visibility = "public"
''',
    "unverified entity memory visibility",
)

main = replace_once(
    main,
    '''        "speaker": speaker,
        "scope": scope,
    }
''',
    '''        "speaker": speaker,
        "scope": scope,
        "visibility": visibility,
    }
''',
    "memory metadata visibility",
)

main = replace_once(
    main,
    '''        history = await _recent_interactions(
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
''',
    '''        history = await _recent_interactions(
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
''',
    "wake disclosure filtering",
)

main = replace_once(
    main,
    '''        history = await _recent_interactions(
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
''',
    '''        history = await _recent_interactions(
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
''',
    "interaction disclosure filtering",
)

main = replace_once(
    main,
    '''  "memory": {
    "remember": false,
    "type": "fact",
    "scope": "speaker",
    "summary": "",
    "importance": 0.0
  },
''',
    '''  "memory": {
    "remember": false,
    "type": "fact",
    "scope": "speaker",
    "visibility": "subject",
    "summary": "",
    "importance": 0.0
  },
''',
    "interaction JSON memory visibility",
)

main = replace_once(
    main,
    '''Enrolled speakers known to this physical device (names/ids only; no embeddings):
{json.dumps(known_speakers, indent=2)}

Pending social messages addressed to THIS verified speaker only:
''',
    '''Enrolled speakers known to this physical device (names/ids only; no embeddings):
{json.dumps(known_speakers, indent=2)}

Relationship declarations supplied by the physical device:
{json.dumps((context or {}).get("relationships") or [], indent=2)}

Pending social messages addressed to THIS verified speaker only:
''',
    "relationship prompt context",
)

main = replace_once(
    main,
    '''- "recognized" means the current voice matched an enrolled local profile. Only then may you state the current speaker's real identity as verified.
- "anonymous" means only that the voice matches the same temporary anonymous_key during this voice session. anonymous_key is not a real-world identity and expires with the device process.
''',
    '''- "recognized" means the current voice matched an enrolled local profile. Only then may you state the current speaker's real identity as verified.
- "probable" is a tentative enrolled-speaker hypothesis. It is NOT verification. You may naturally confirm it conversationally, e.g. "I think that's Jesse—is that you?" but a yes/no answer remains a conversational claim and does not become biometric verification.
- A probable identity may improve conversational continuity, but it must NOT unlock speaker-private memories, guardian-only information, or pending social messages.
- "anonymous" means only that the voice matches the same temporary anonymous_key during this voice session. anonymous_key is not a real-world identity and expires with the device process.
''',
    "probable identity prompt rules",
)

main = replace_once(
    main,
    '''- scope="speaker" means the memory is private to the CURRENT VERIFIED speaker. It is the default for human-specific facts/preferences when identity is recognized.
- scope="entity" is only for durable facts about Jerry/the device/shared world that are genuinely not person-private.
''',
    '''- scope="speaker" means the memory has a human subject. The server uses visibility to decide who may later receive it.
- visibility="subject" or "private": verified subject only.
- visibility="household": verified subject plus verified family/household relationships such as parent/child/sibling/spouse/partner/household/guardian.
- visibility="guardian": verified subject plus an explicitly declared guardian. Do not silently treat "parent" as "guardian".
- visibility="participants": only explicitly listed participants when metadata supports them.
- visibility="public": safe for any speaker.
- If unsure, use visibility="subject". Prefer narrower disclosure over guessing.
- scope="entity" is only for durable facts about Jerry/the device/shared world that are genuinely not person-private and is stored as public.
''',
    "memory disclosure prompt rules",
)

sid = read("pi/speaker_identity.py")
backup("pi/speaker_identity.py")

sid = replace_once(
    sid,
    '''        if not recognized:
            return {"status": "unknown", "id": None, "display_name": None,
                    "similarity": round(best_score, 4),
                    "margin": round(separation, 4) if separation is not None else None,
                    "threshold": self.threshold}
        return {"status": "recognized", "id": best_id,
                "display_name": best_profile.get("display_name") or best_id,
                "similarity": round(best_score, 4),
                "margin": round(separation, 4) if separation is not None else None,
                "threshold": self.threshold}
''',
    '''        sample_scores = []
        for value in best_profile.get("embeddings") or []:
            vector = np.asarray(value, dtype=np.float32)
            if vector.size == self.embedding_dim:
                sample_scores.append(
                    float(np.dot(query, _normalize(vector)))
                )
        best_sample_score = max(sample_scores) if sample_scores else None

        if not recognized:
            return {
                "status": "unknown",
                "id": None,
                "display_name": None,
                "candidate_id": best_id,
                "candidate_display_name": (
                    best_profile.get("display_name") or best_id
                ),
                "similarity": round(best_score, 4),
                "best_sample_similarity": (
                    round(best_sample_score, 4)
                    if best_sample_score is not None
                    else None
                ),
                "margin": (
                    round(separation, 4)
                    if separation is not None
                    else None
                ),
                "threshold": self.threshold,
            }
        return {
            "status": "recognized",
            "id": best_id,
            "display_name": best_profile.get("display_name") or best_id,
            "similarity": round(best_score, 4),
            "best_sample_similarity": (
                round(best_sample_score, 4)
                if best_sample_score is not None
                else None
            ),
            "margin": (
                round(separation, 4)
                if separation is not None
                else None
            ),
            "threshold": self.threshold,
        }
''',
    "speaker candidate diagnostics",
)

pi = read("pi/talking_box.py")
backup("pi/talking_box.py")

pi = replace_once(
    pi,
    "VOLUME_DEFAULT = 55\n",
    "VOLUME_DEFAULT = 75\n",
    "default volume 75",
)

pi = replace_once(
    pi,
    '''SPEAKER_ID_ENABLED = os.getenv(
    "TALKING_BOX_SPEAKER_ID",
    "1",
).strip().lower() not in {
    "0", "false", "no", "off",
}
''',
    '''SPEAKER_ID_ENABLED = os.getenv(
    "TALKING_BOX_SPEAKER_ID",
    "1",
).strip().lower() not in {
    "0", "false", "no", "off",
}

PROBABLE_SPEAKER_THRESHOLD = float(
    os.getenv("TALKING_BOX_SPEAKER_PROBABLE_THRESHOLD", "0.50")
)
PROBABLE_SPEAKER_MIN_MARGIN = float(
    os.getenv("TALKING_BOX_SPEAKER_PROBABLE_MARGIN", "0.10")
)
PROBABLE_RECENT_VERIFIED_SECONDS = float(
    os.getenv("TALKING_BOX_SPEAKER_PROBABLE_RECENT_SECONDS", "180")
)

RELATIONSHIPS_FILE = Path.home() / ".talking_box_relationships.json"

ALSA_CONFIG_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "asoundrc"
)
ALSA_CONFIG_TARGET = Path.home() / ".asoundrc"
''',
    "probable speaker constants",
)

pi = replace_once(
    pi,
    '''last_spoken_text = None
speaker_identity = None
anonymous_speakers = None
''',
    '''last_spoken_text = None
speaker_identity = None
anonymous_speakers = None
last_verified_speaker = None
''',
    "recent verified state",
)

pi = replace_once(
    pi,
    """def run_amixer(*args, capture=False):
    return subprocess.run(
        ["amixer", "-D", VOLUME_MIXER_DEVICE, *args],
        check=True,
        text=True,
        capture_output=capture,
    )
""",
    """def ensure_alsa_config(force=False):
    try:
        if not ALSA_CONFIG_SOURCE.is_file():
            print(
                "Canonical ALSA config is missing: "
                f"{ALSA_CONFIG_SOURCE}"
            )
            return False

        desired = ALSA_CONFIG_SOURCE.read_bytes()
        current = None

        if ALSA_CONFIG_TARGET.exists():
            try:
                current = ALSA_CONFIG_TARGET.read_bytes()
            except OSError:
                current = None

        if force or current != desired:
            tmp = ALSA_CONFIG_TARGET.with_name(".asoundrc.tmp")
            tmp.write_bytes(desired)
            os.chmod(tmp, 0o644)
            tmp.replace(ALSA_CONFIG_TARGET)
            print("Restored ALSA config from tracked canonical copy.")

        return True

    except Exception as exc:
        print(
            "Could not restore ALSA config: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def run_amixer(*args, capture=False):
    ensure_alsa_config()
    return subprocess.run(
        ["amixer", "-D", VOLUME_MIXER_DEVICE, *args],
        check=True,
        text=True,
        capture_output=capture,
    )
""",
    "runtime ALSA self-heal before mixer access",
)

pi = replace_once(
    pi,
    """def cloud_speak(text):
    with requests.post(
""",
    """def cloud_speak(text):
    ensure_alsa_config()

    with requests.post(
""",
    "runtime ALSA self-heal before cloud playback",
)

pi = replace_once(
    pi,
    """def piper_speak(text):
    with tempfile.NamedTemporaryFile(
""",
    """def piper_speak(text):
    ensure_alsa_config()

    with tempfile.NamedTemporaryFile(
""",
    "runtime ALSA self-heal before Piper playback",
)

pi = replace_once(
    pi,
    '''def identify_speaker(path):
    if speaker_identity is None:
''',
    '''def identify_speaker(path):
    global last_verified_speaker

    if speaker_identity is None:
''',
    "identify speaker global",
)

pi = replace_once(
    pi,
    '''        embedding = speaker_identity.embedding_from_wav(path)
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
''',
    '''        embedding = speaker_identity.embedding_from_wav(path)
        result = speaker_identity.identify_embedding(embedding)

        if (
            result.get("status") == "recognized"
            and result.get("id")
        ):
            last_verified_speaker = {
                "id": result.get("id"),
                "display_name": (
                    result.get("display_name")
                    or result.get("id")
                ),
                "at": time.monotonic(),
            }

        elif result.get("status") == "unknown":
            candidate_id = result.get("candidate_id")
            candidate_name = (
                result.get("candidate_display_name")
                or candidate_id
            )
            similarity = float(result.get("similarity") or 0.0)
            margin = result.get("margin")
            margin_ok = (
                margin is None
                or float(margin) >= PROBABLE_SPEAKER_MIN_MARGIN
            )

            recent_match = False
            recent_age = None
            if (
                last_verified_speaker
                and candidate_id
                and last_verified_speaker.get("id") == candidate_id
            ):
                recent_age = max(
                    0.0,
                    time.monotonic()
                    - float(last_verified_speaker.get("at") or 0.0),
                )
                recent_match = (
                    recent_age <= PROBABLE_RECENT_VERIFIED_SECONDS
                )

            if (
                candidate_id
                and similarity >= PROBABLE_SPEAKER_THRESHOLD
                and margin_ok
            ):
                result = {
                    "status": "probable",
                    "id": None,
                    "display_name": None,
                    "candidate_id": candidate_id,
                    "candidate_display_name": candidate_name,
                    "similarity": similarity,
                    "best_sample_similarity": result.get(
                        "best_sample_similarity"
                    ),
                    "margin": margin,
                    "threshold": speaker_identity.threshold,
                    "identity_verified": False,
                    "needs_confirmation": True,
                    "probable_basis": (
                        "recent_verified_same_candidate"
                        if recent_match
                        else "weak_enrolled_voice_match"
                    ),
                    "recent_verified_age_seconds": recent_age,
                }

            elif anonymous_speakers is not None:
                anonymous = anonymous_speakers.observe(embedding)
                anonymous["known_best_similarity"] = result.get(
                    "similarity"
                )
                anonymous["known_threshold"] = speaker_identity.threshold
                anonymous["known_candidate_id"] = candidate_id
                anonymous["known_candidate_display_name"] = candidate_name
                anonymous["known_best_sample_similarity"] = result.get(
                    "best_sample_similarity"
                )
                result = anonymous

        result = _speaker_with_session_metadata(result)
''',
    "probable speaker classification",
)

pi = replace_once(
    pi,
    '''def device_context():
    return {
''',
    '''def _relationship_context():
    try:
        if not RELATIONSHIPS_FILE.exists():
            return []

        payload = json.loads(RELATIONSHIPS_FILE.read_text())
        raw = (
            payload.get("relationships")
            if isinstance(payload, dict)
            else payload
        )
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
                    {
                        "from": source,
                        "to": target,
                        "type": relation,
                    }
                )

        return result

    except Exception as exc:
        print(
            "Could not read relationship context: "
            f"{type(exc).__name__}: {exc}"
        )
        return []


def device_context():
    return {
''',
    "relationship context loader",
)

pi = replace_once(
    pi,
    '''        "known_speakers": _known_speakers_context(),
    }
''',
    '''        "known_speakers": _known_speakers_context(),
        "relationships": _relationship_context(),
    }
''',
    "device context relationships",
)

pi = pi.replace(
    "Talking Box V7.2 starting.",
    "Talking Box V7.3 starting.",
)
pi = pi.replace(
    "Talking Box V7.2 ready.",
    "Talking Box V7.3 ready.",
)

backup("tests/test_epistemics.py")

# Write only after all patch anchors succeeded.
(ROOT / "server/epistemics.py").write_text(EPISTEMICS_V73)
(ROOT / "server/main.py").write_text(main)
(ROOT / "pi/speaker_identity.py").write_text(sid)
(ROOT / "pi/talking_box.py").write_text(pi)
(ROOT / "tests/test_epistemics.py").write_text(TESTS_V73)

example = ROOT / "config" / "relationships.example.json"
example.parent.mkdir(parents=True, exist_ok=True)
if not example.exists():
    example.write_text(
        '{\n'
        '  "relationships": [\n'
        '    {"from": "person-a", "to": "person-b", "type": "parent"}\n'
        '  ]\n'
        '}\n'
    )

print("Applied Talking Box V7.3 identity/relationship/disclosure + audio resilience patch.")
print("Backups use the suffix .bak-v7.3")
print()
print("Next:")
print(
    "  python3 -m py_compile server/main.py server/epistemics.py "
    "pi/talking_box.py pi/speaker_identity.py"
)
print("  python3 -m unittest tests.test_epistemics -v")
print("  git diff --check")
