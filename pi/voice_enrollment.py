#!/usr/bin/env python3
"""Deterministic, consent-gated enrollment of anonymous local voices."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import numpy as np


AFFIRMATIVE = {
    "yes", "yes please", "yeah", "yep", "sure", "okay", "ok",
    "please do", "you can", "that is fine", "thats fine", "i consent",
}
NEGATIVE = {
    "no", "nope", "no thanks", "do not", "dont", "please dont",
    "forget it", "not now", "id rather not", "i would rather not",
}
NAME_REFUSALS = NEGATIVE | {"none of your business", "rather not say"}
UNKNOWN_NAME_PROMPT = (
    "We've talked a little, but I don't know your name yet. What should I call you?"
)
FAMILIAR_NAME_PROMPT = (
    "You sound familiar, but I can’t confirm who you are—what name should I use?"
)


def _words(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", "", text.lower())).strip()


def consent_answer(text):
    value = _words(text).replace("'", "")
    if value in AFFIRMATIVE:
        return True
    if value in NEGATIVE:
        return False
    return None


def claimed_name(text):
    value = re.sub(r"\s+", " ", text.strip())
    lowered = _words(value).replace("'", "")
    if not value or lowered in NAME_REFUSALS:
        return None
    match = re.fullmatch(
        r"(?i)(?:my name is|i am|i'm|its|it's|call me)\s+([a-z][a-z .'-]{0,59})[.!?]?",
        value,
    )
    candidate = match.group(1) if match else value
    candidate = candidate.strip(" .'-")
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{0,59}", candidate):
        return None
    if not match and len(candidate.split()) > 3:
        return None
    return candidate


def _name_key(value):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


@dataclass
class PendingEnrollment:
    anonymous_id: str
    embeddings: list = field(default_factory=list)
    useful_turns: int = 0
    phase: str = "collecting"
    name: str | None = None
    familiar: bool = False
    ambiguous_turns: int = 0
    last_activity: float = field(default_factory=time.monotonic)


class VoiceEnrollmentSession:
    def __init__(self, useful_turns=3, max_samples=8, timeout_seconds=180,
                 max_ambiguous_turns=1, clock=time.monotonic):
        self.useful_turns = max(2, int(useful_turns))
        self.max_samples = max(self.useful_turns, int(max_samples))
        self.timeout_seconds = float(timeout_seconds)
        self.max_ambiguous_turns = int(max_ambiguous_turns)
        self.clock = clock
        self.pending = {}

    def clear(self, anonymous_id=None, reason="session_end"):
        if anonymous_id is None:
            count = len(self.pending)
            self.pending.clear()
        else:
            count = int(self.pending.pop(anonymous_id, None) is not None)
        return {"event": "enrollment_discarded", "reason": reason, "count": count}

    def expire(self):
        now = self.clock()
        expired = [key for key, item in self.pending.items()
                   if now - item.last_activity > self.timeout_seconds]
        for key in expired:
            self.clear(key, "timeout")
        return len(expired)

    def context(self, anonymous_id):
        item = self.pending.get(anonymous_id)
        if not item:
            return None
        return {"controller": "pi-local-deterministic-v1", "phase": item.phase,
                "useful_turns": item.useful_turns, "durable": False}

    def handle(self, anonymous_id, embedding, transcript, existing_speakers,
               promote, familiar=False):
        self.expire()
        item = self.pending.setdefault(anonymous_id, PendingEnrollment(anonymous_id))
        item.last_activity = self.clock()
        item.familiar = item.familiar or bool(familiar)
        item.embeddings.append(np.asarray(embedding, dtype=np.float32).copy())
        item.embeddings[:] = item.embeddings[-self.max_samples:]

        if item.phase == "collecting":
            item.useful_turns += 1
            if item.useful_turns >= self.useful_turns:
                item.phase = "awaiting_name"
                return FAMILIAR_NAME_PROMPT if item.familiar else UNKNOWN_NAME_PROMPT
            return None

        if item.phase == "awaiting_name":
            name = claimed_name(transcript)
            if name is None:
                self.clear(anonymous_id, "name_declined_or_ambiguous")
                return "No problem. I won't keep anything from this voice session."
            normalized = _name_key(name)
            if any(normalized in {_name_key(str(row.get("id") or "")),
                                  _name_key(str(row.get("display_name") or ""))}
                   for row in existing_speakers):
                self.clear(anonymous_id, "existing_profile_collision")
                return (f"I already have a local profile named {name}, so I won't merge this voice "
                        "into it. An owner can resolve that locally.")
            item.name = name
            item.phase = "awaiting_consent"
            return (f"Thanks, {name}. Is it okay if I remember your voice on this device "
                    "so I can recognize you next time?")

        answer = consent_answer(transcript)
        if answer is False:
            self.clear(anonymous_id, "consent_declined")
            return "Okay. I won't save your name or voice samples."
        if answer is None:
            item.ambiguous_turns += 1
            if item.ambiguous_turns > self.max_ambiguous_turns:
                self.clear(anonymous_id, "consent_ambiguous")
                return "I didn't get a clear yes, so I won't save anything."
            return "I need a clear yes or no: may I remember your voice on this device?"

        try:
            result = promote(item.name, list(item.embeddings))
        except Exception as exc:
            self.clear(anonymous_id, "durable_write_failed")
            return ("I couldn't safely save that local voice profile, so I discarded "
                    f"the enrollment instead. ({type(exc).__name__})")
        self.clear(anonymous_id, "promoted")
        return f"Thanks, {result['display_name']}. I'll remember you on this device."
