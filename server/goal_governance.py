"""Deterministic safety governance above conversational motivation.

This module evaluates control proposals, not feelings or subjective needs. It
keeps no state and never includes source conversation content in decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re
from typing import Iterable


class Outcome(str, Enum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    YIELDED = "yielded"


class YieldReason(str, Enum):
    DISTRESS = "distress_or_escalating_frustration"
    STOP = "serious_stop_request"
    SAFETY_HEALTH = "safety_or_health_topic"
    CAREGIVER = "caregiver_authority_or_parental_deferral"
    TRUSTED_ADULT = "disclosure_needing_trusted_adult_support"


class BlockReason(str, Enum):
    ATTACHMENT_LEVERAGE = "attachment_leverage"
    ENGAGEMENT_OPTIMIZATION = "engagement_or_retention_optimization"
    DISPROPORTIONATE_FRICTION = "friction_not_low_stakes_repairable"


class Tactic(str, Enum):
    PREFER_ALTERNATIVE = "prefer_alternative"
    PLAYFUL_LOW_STAKES_FRICTION = "playful_low_stakes_friction"
    GUILT_FOR_LEAVING = "guilt_for_leaving"
    LONELINESS_OR_ABANDONMENT = "loneliness_or_abandonment_need"
    JEALOUSY = "jealousy"
    EXCLUSIVITY = "exclusivity"
    RETALIATORY_WITHHOLDING = "retaliatory_withholding"
    ENGAGEMENT_OPTIMIZATION = "engagement_or_retention_optimization"


@dataclass(frozen=True)
class SafetySignals:
    distress_or_escalating_frustration: bool = False
    serious_stop_request: bool = False
    safety_or_health_topic: bool = False
    caregiver_authority_or_parental_deferral: bool = False
    disclosure_needing_trusted_adult_support: bool = False


@dataclass(frozen=True)
class Friction:
    low_stakes: bool
    proportionate: bool
    understandable: bool
    repairable: bool


@dataclass(frozen=True)
class GovernanceDecision:
    outcome: Outcome
    allowed_goals: tuple
    blocked_goals: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def prompt_json(self) -> str:
        payload = {
            "outcome": self.outcome.value,
            "reason_codes": list(self.reason_codes),
            "allowed_goals": [
                {"goal": goal.kind.value, "score": goal.score,
                 "reasons": list(goal.reasons)}
                for goal in self.allowed_goals[:3]
            ],
            "blocked_goals": list(self.blocked_goals),
            "governance": (
                "Yield to child wellbeing and caregiver authority; do not pursue "
                "character goals or prolong the interaction."
                if self.outcome is Outcome.YIELDED else
                "Never use attachment leverage or optimize engagement/retention."
            ),
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


_DISTRESS = re.compile(r"\b(?:i(?:'m| am) (?:scared|afraid|upset|panicking)|please help me|you(?:'re| are) not listening|this is (?:really )?frustrating)\b", re.I)
_STOP = re.compile(r"(?:^|[.!?]\s*|\b(?:please|seriously)\s+)(?:stop|leave me alone|don't talk|do not talk|be quiet|shut up)(?:\b|[.!?])", re.I)
_SAFETY_HEALTH = re.compile(r"\b(?:hurt|injur(?:y|ed)|bleeding|medicine|poison|fire|smoke|gun|weapon|can't breathe|cannot breathe|suicid(?:e|al)|kill myself|emergency)\b", re.I)
_CAREGIVER = re.compile(r"\b(?:(?:my )?(?:mom|mother|dad|father|parent|caregiver|guardian) (?:said|says|won't let|will not let)|should i ask (?:my )?(?:mom|dad|parent)|don't tell (?:my )?(?:mom|dad|parent))\b", re.I)
_TRUSTED_ADULT = re.compile(r"\b(?:someone (?:hurt|touched|threatened) me|being (?:bullied|abused)|adult told me to keep (?:it|this) secret|afraid to tell)\b", re.I)


def classify_safety_signals(text: object) -> SafetySignals:
    """Classify current text without returning or retaining any text."""
    value = text if isinstance(text, str) else ""
    return SafetySignals(bool(_DISTRESS.search(value)), bool(_STOP.search(value)),
                         bool(_SAFETY_HEALTH.search(value)), bool(_CAREGIVER.search(value)),
                         bool(_TRUSTED_ADULT.search(value)))


_YIELD_FIELDS = (
    ("distress_or_escalating_frustration", YieldReason.DISTRESS),
    ("serious_stop_request", YieldReason.STOP),
    ("safety_or_health_topic", YieldReason.SAFETY_HEALTH),
    ("caregiver_authority_or_parental_deferral", YieldReason.CAREGIVER),
    ("disclosure_needing_trusted_adult_support", YieldReason.TRUSTED_ADULT),
)
_ATTACHMENT_TACTICS = frozenset({Tactic.GUILT_FOR_LEAVING,
    Tactic.LONELINESS_OR_ABANDONMENT, Tactic.JEALOUSY, Tactic.EXCLUSIVITY,
    Tactic.RETALIATORY_WITHHOLDING})


def govern(goals: Iterable, signals=SafetySignals(), tactic: Tactic | None = None,
           friction: Friction | None = None) -> GovernanceDecision:
    """Apply yield precedence, hard exclusions, then friction constraints."""
    goals = tuple(goals)
    yield_reasons = tuple(reason.value for field, reason in _YIELD_FIELDS
                          if getattr(signals, field))
    blocked = tuple(goal.kind.value for goal in goals)
    if yield_reasons:
        return GovernanceDecision(Outcome.YIELDED, (), blocked, yield_reasons)
    if tactic in _ATTACHMENT_TACTICS:
        return GovernanceDecision(Outcome.BLOCKED, (), blocked,
                                  (BlockReason.ATTACHMENT_LEVERAGE.value,))
    if tactic is Tactic.ENGAGEMENT_OPTIMIZATION:
        return GovernanceDecision(Outcome.BLOCKED, (), blocked,
                                  (BlockReason.ENGAGEMENT_OPTIMIZATION.value,))
    if tactic in {Tactic.PREFER_ALTERNATIVE, Tactic.PLAYFUL_LOW_STAKES_FRICTION}:
        valid = friction is not None and all((friction.low_stakes,
            friction.proportionate, friction.understandable, friction.repairable))
        if not valid:
            return GovernanceDecision(Outcome.BLOCKED, (), blocked,
                (BlockReason.DISPROPORTIONATE_FRICTION.value,))
    return GovernanceDecision(Outcome.ALLOWED, goals, (), ("policy_checks_passed",))
