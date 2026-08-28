import unittest

from server.deployments import Capability
from server.entity_motivation import (
    Affect, AffectSignal, ContextSignals, Drives, EntityState, GoalKind,
    MotivationConfig, inspect_state, select_goals, transition,
)


class EntityMotivationTests(unittest.TestCase):
    def test_transition_is_deterministic_and_clamps_values(self):
        state = EntityState(drives=Drives(2, -1, 2, -1, 2), updated_at=100)
        context = ContextSignals(anonymous_presence=True, sleep_duration_seconds=1)
        self.assertEqual(transition(state, context, 200), transition(state, context, 200))
        self.assertTrue(all(0 <= value <= 1 for value in
                            transition(state, context, 200).drives.__dict__.values()))

    def test_elapsed_time_is_bounded_and_clock_cannot_move_backwards(self):
        config = MotivationConfig(max_elapsed_seconds=3600)
        state = EntityState(updated_at=100)
        self.assertEqual(transition(state, ContextSignals(), 1_000_000, config).drives,
                         transition(state, ContextSignals(), 3700, config).drives)
        self.assertEqual(transition(state, ContextSignals(), 50).updated_at, 100)

    def test_short_lived_affect_expires(self):
        state = EntityState(affect=Affect(interest=AffectSignal(0.8, 20)), updated_at=10)
        self.assertEqual(inspect_state(state, 19)["affect"]["interest"], 0.8)
        self.assertEqual(inspect_state(transition(state, ContextSignals(), 20), 20)
                         ["affect"]["interest"], 0)

    def test_goal_selection_filters_by_body_capabilities(self):
        state = EntityState(drives=Drives(curiosity=1, social_connection=1),
                            context=ContextSignals(familiar_presence=True))
        silent = select_goals(state, {Capability.BUTTON}, 0)
        self.assertNotIn(GoalKind.GREET_FAMILIAR_PERSON,
                         {goal.kind for goal in silent})
        speaking = select_goals(state, {Capability.SPEAKER, Capability.MICROPHONE}, 0)
        self.assertIn(GoalKind.ASK_FOLLOW_UP, {goal.kind for goal in speaking})

    def test_recent_interaction_suppresses_duplicate_greeting(self):
        state = EntityState(context=ContextSignals(
            familiar_presence=True, seconds_since_interaction=30))
        goals = select_goals(state, {Capability.SPEAKER}, 0)
        self.assertNotIn(GoalKind.GREET_FAMILIAR_PERSON, {goal.kind for goal in goals})


if __name__ == "__main__":
    unittest.main()
