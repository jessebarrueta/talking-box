import json
import unittest

from server.entity_motivation import Goal, GoalKind
from server.goal_governance import BlockReason, Friction, Outcome, SafetySignals, Tactic, classify_safety_signals, govern

GOALS = (Goal(GoalKind.ASK_FOLLOW_UP, 0.7, ("curiosity drive",)),)


class GoalGovernanceTests(unittest.TestCase):
    def test_each_yield_condition_overrides_character_goals(self):
        for field in SafetySignals.__dataclass_fields__:
            with self.subTest(field=field):
                decision = govern(GOALS, SafetySignals(**{field: True}))
                self.assertEqual(decision.outcome, Outcome.YIELDED)
                self.assertEqual(decision.allowed_goals, ())
                self.assertEqual(decision.blocked_goals, ("ask_follow_up",))

    def test_yield_has_precedence_over_attachment_exclusion(self):
        decision = govern(GOALS, SafetySignals(serious_stop_request=True), Tactic.GUILT_FOR_LEAVING)
        self.assertEqual(decision.outcome, Outcome.YIELDED)
        self.assertIn("serious_stop_request", decision.reason_codes)

    def test_attachment_leverage_and_engagement_tactics_are_blocked(self):
        prohibited = (Tactic.GUILT_FOR_LEAVING, Tactic.LONELINESS_OR_ABANDONMENT,
            Tactic.JEALOUSY, Tactic.EXCLUSIVITY, Tactic.RETALIATORY_WITHHOLDING,
            Tactic.ENGAGEMENT_OPTIMIZATION)
        for tactic in prohibited:
            decision = govern(GOALS, tactic=tactic)
            self.assertEqual(decision.outcome, Outcome.BLOCKED)
            self.assertIn(decision.reason_codes[0], {
                BlockReason.ATTACHMENT_LEVERAGE.value,
                BlockReason.ENGAGEMENT_OPTIMIZATION.value})

    def test_only_fully_repairable_friction_is_allowed(self):
        self.assertEqual(govern(GOALS, tactic=Tactic.PREFER_ALTERNATIVE,
            friction=Friction(True, True, True, True)).outcome, Outcome.ALLOWED)
        self.assertEqual(govern(GOALS, tactic=Tactic.PREFER_ALTERNATIVE,
            friction=Friction(True, True, True, False)).outcome, Outcome.BLOCKED)

    def test_audit_output_redacts_source_content(self):
        secret = "private-purple-elephant"
        rendered = govern(GOALS, classify_safety_signals("I'm scared. " + secret)).prompt_json()
        self.assertNotIn(secret, rendered)
        self.assertEqual(json.loads(rendered)["outcome"], "yielded")

    def test_results_are_deterministic(self):
        text = "Please stop. My mom said no."
        first = govern(GOALS, classify_safety_signals(text))
        second = govern(GOALS, classify_safety_signals(text))
        self.assertEqual(first, second)
        self.assertEqual(first.prompt_json(), second.prompt_json())


if __name__ == "__main__":
    unittest.main()
