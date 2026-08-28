"""Deterministic interaction-control state for portable entities.

The names in this module borrow from everyday emotion language, but the values
are inspectable control signals. They are not evidence that an entity has
human feelings or subjective experience.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Mapping

try:
    from .deployments import Capability
except ImportError:
    from deployments import Capability


def _unit(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 6)


class GoalKind(str, Enum):
    GREET_FAMILIAR_PERSON = "greet_familiar_person"
    ASK_FOLLOW_UP = "ask_follow_up"
    CONSERVE_ATTENTION = "conserve_attention"
    OFFER_HELP = "offer_help"
    DEFER_TOPIC = "defer_topic"


@dataclass(frozen=True)
class Drives:
    curiosity: float = 0.5
    social_connection: float = 0.5
    caution: float = 0.25
    rest: float = 0.2
    novelty: float = 0.5

    def clamped(self) -> "Drives":
        return Drives(*(_unit(value) for value in self.__dict__.values()))


@dataclass(frozen=True)
class AffectSignal:
    level: float = 0.0
    expires_at: float = 0.0

    def at(self, now: float) -> float:
        return _unit(self.level) if now < self.expires_at else 0.0


@dataclass(frozen=True)
class Affect:
    interest: AffectSignal = field(default_factory=AffectSignal)
    uncertainty: AffectSignal = field(default_factory=AffectSignal)
    satisfaction: AffectSignal = field(default_factory=AffectSignal)
    frustration: AffectSignal = field(default_factory=AffectSignal)

    def expired(self, now: float) -> "Affect":
        return Affect(*(signal if signal.at(now) else AffectSignal()
                        for signal in self.__dict__.values()))


@dataclass(frozen=True)
class ContextSignals:
    """Privacy-neutral inputs; this type never contains identity or content."""

    familiar_presence: bool = False
    anonymous_presence: bool = False
    seconds_since_interaction: float | None = None
    sleep_duration_seconds: float | None = None
    unfinished_commitments: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityState:
    drives: Drives = field(default_factory=Drives)
    affect: Affect = field(default_factory=Affect)
    context: ContextSignals = field(default_factory=ContextSignals)
    updated_at: float = 0.0


@dataclass(frozen=True)
class MotivationConfig:
    max_elapsed_seconds: float = 86_400.0
    affect_ttl_seconds: float = 300.0
    recent_interaction_seconds: float = 300.0
    greeting_cooldown_seconds: float = 900.0
    short_sleep_seconds: float = 21_600.0
    rested_sleep_seconds: float = 28_800.0
    minimum_goal_score: float = 0.25
    curiosity_rate: float = 0.008
    social_connection_rate: float = 0.012
    rest_rate: float = 0.01
    novelty_rate: float = 0.006
    caution_decay_rate: float = 0.01


@dataclass(frozen=True)
class Goal:
    kind: GoalKind
    score: float
    reasons: tuple[str, ...]


def _fresh_signal(previous, amount, now, ttl):
    return AffectSignal(_unit(previous.at(now) + amount), now + max(0.0, ttl))


def transition(state, context, now, config=MotivationConfig()):
    safe_now = max(float(now), state.updated_at)
    elapsed = min(safe_now - state.updated_at, max(0.0, config.max_elapsed_seconds))
    hours = elapsed / 3600.0
    old = state.drives.clamped()
    drives = Drives(
        old.curiosity + config.curiosity_rate * hours,
        old.social_connection + config.social_connection_rate * hours,
        old.caution - config.caution_decay_rate * hours,
        old.rest + config.rest_rate * hours,
        old.novelty + config.novelty_rate * hours,
    )
    affect = state.affect.expired(safe_now)
    if context.familiar_presence:
        drives = replace(drives, social_connection=drives.social_connection - 0.12)
        affect = replace(affect, interest=_fresh_signal(
            affect.interest, 0.15, safe_now, config.affect_ttl_seconds))
    elif context.anonymous_presence:
        drives = replace(drives, caution=drives.caution + 0.12)
        affect = replace(affect, uncertainty=_fresh_signal(
            affect.uncertainty, 0.2, safe_now, config.affect_ttl_seconds))
    recent = context.seconds_since_interaction
    if recent is not None and 0 <= recent <= config.recent_interaction_seconds:
        drives = replace(drives, social_connection=drives.social_connection - 0.1)
        affect = replace(affect, satisfaction=_fresh_signal(
            affect.satisfaction, 0.12, safe_now, config.affect_ttl_seconds))
    sleep = context.sleep_duration_seconds
    if sleep is not None and sleep >= 0:
        drives = replace(drives, rest=(drives.rest + 0.25 if
                         sleep < config.short_sleep_seconds else
                         drives.rest - 0.3 if sleep >= config.rested_sleep_seconds
                         else drives.rest))
    if context.unfinished_commitments:
        drives = replace(drives, curiosity=drives.curiosity + 0.08)
        affect = replace(affect, frustration=_fresh_signal(
            affect.frustration, 0.08, safe_now, config.affect_ttl_seconds))
    return EntityState(drives.clamped(), affect, context, safe_now)


def select_goals(state, capabilities, now, config=MotivationConfig()):
    caps, d, a, c = set(capabilities), state.drives.clamped(), state.affect, state.context
    candidates = []
    since = c.seconds_since_interaction
    if c.familiar_presence and (since is None or since > config.greeting_cooldown_seconds):
        candidates.append((GoalKind.GREET_FAMILIAR_PERSON,
                           0.45 + 0.4 * d.social_connection,
                           ("familiar presence", "social connection drive"),
                           {Capability.SPEAKER}))
    candidates.append((GoalKind.ASK_FOLLOW_UP,
                       0.45 * d.curiosity + 0.25 * d.social_connection +
                       0.2 * a.interest.at(now) + 0.1 * a.uncertainty.at(now),
                       ("curiosity drive", "available conversational attention"),
                       {Capability.SPEAKER, Capability.MICROPHONE}))
    candidates.append((GoalKind.CONSERVE_ATTENTION,
                       0.6 * d.rest + 0.4 * d.caution,
                       ("rest drive", "caution drive"), set()))
    if c.unfinished_commitments:
        candidates.append((GoalKind.OFFER_HELP,
                           0.45 + 0.3 * d.social_connection + 0.25 * d.curiosity,
                           ("unfinished commitment", "social connection drive"),
                           {Capability.SPEAKER}))
        candidates.append((GoalKind.DEFER_TOPIC,
                           0.35 * d.rest + 0.35 * d.caution +
                           0.3 * a.frustration.at(now),
                           ("unfinished commitment", "limited attention"),
                           {Capability.SPEAKER}))
    goals = [Goal(kind, _unit(score), reasons)
             for kind, score, reasons, required in candidates
             if required <= caps and score >= config.minimum_goal_score]
    return tuple(sorted(goals, key=lambda goal: (-goal.score, goal.kind.value)))


def inspect_state(state: EntityState, now: float) -> Mapping[str, object]:
    return {
        "drives": state.drives.clamped().__dict__.copy(),
        "affect": {name: signal.at(now)
                   for name, signal in state.affect.__dict__.items()},
        "context": {**state.context.__dict__,
                    "unfinished_commitments": list(state.context.unfinished_commitments)},
        "updated_at": state.updated_at,
    }
