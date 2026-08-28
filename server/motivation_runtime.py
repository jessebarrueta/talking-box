"""Privacy boundary and process-local ownership for entity motivation state."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

try:
    from .deployments import Capability
    from .entity_motivation import ContextSignals, EntityState, select_goals, transition
    from .goal_governance import classify_safety_signals, govern
except ImportError:
    from deployments import Capability
    from entity_motivation import ContextSignals, EntityState, select_goals, transition
    from goal_governance import classify_safety_signals, govern

MAX_PROMPT_GOALS = 3


def declared_capabilities(context: Any) -> frozenset[Capability]:
    """Accept only explicitly declared values from the shared vocabulary."""
    raw = context.get("body_capabilities") if isinstance(context, dict) else None
    if not isinstance(raw, list):
        return frozenset()
    result = set()
    for value in raw:
        try:
            result.add(Capability(str(value).strip().lower()))
        except (TypeError, ValueError):
            pass
    return frozenset(result)


def privacy_neutral_context(context: Any, seconds_since_interaction=None):
    """Reduce already-authorized device input to non-identifying signals."""
    raw = context if isinstance(context, dict) else {}
    speaker = raw.get("speaker") if isinstance(raw.get("speaker"), dict) else {}
    status = str(speaker.get("status") or "").strip().lower()
    sleep = raw.get("last_sleep") if isinstance(raw.get("last_sleep"), dict) else {}
    duration = sleep.get("duration_seconds") if sleep.get("status") == "known" else None
    try:
        duration = max(0.0, float(duration)) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    return ContextSignals(
        familiar_presence=status == "recognized",
        anonymous_presence=status in {"anonymous", "probable", "unknown"},
        seconds_since_interaction=seconds_since_interaction,
        sleep_duration_seconds=duration,
    )


def goal_prompt_context(goals):
    """Bounded JSON containing policy guidance, never identity or source data."""
    view = [{"goal": goal.kind.value, "score": goal.score,
             "reasons": list(goal.reasons)} for goal in goals[:MAX_PROMPT_GOALS]]
    return json.dumps(view, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class MotivationDecision:
    state: EntityState
    goals: tuple
    prompt_context: str
    governance: object


class InMemoryMotivationStore:
    """Per-process, per-entity state. Constructing a new store resets it."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._states = {}
        self._last_interactions = {}
        self._lock = threading.Lock()

    def update(self, entity_id: str, device_context: Any,
               user_text: object = "") -> MotivationDecision:
        now = float(self._clock())
        with self._lock:
            previous = self._states.get(entity_id, EntityState(updated_at=now))
            last = self._last_interactions.get(entity_id)
            elapsed = max(0.0, now - last) if last is not None else None
            neutral = privacy_neutral_context(device_context, elapsed)
            state = transition(previous, neutral, now)
            goals = select_goals(state, declared_capabilities(device_context), now)
            governance = govern(goals, classify_safety_signals(user_text))
            self._states[entity_id] = state
            self._last_interactions[entity_id] = now
            return MotivationDecision(state, governance.allowed_goals,
                                      governance.prompt_json(), governance)

    def inspect(self, entity_id: str):
        with self._lock:
            return self._states.get(entity_id)
