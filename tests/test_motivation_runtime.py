import json
import unittest

from server.deployments import Capability
from server.entity_motivation import GoalKind
from server.motivation_runtime import (
    InMemoryMotivationStore,
    declared_capabilities,
    goal_prompt_context,
    privacy_neutral_context,
)


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class MotivationRuntimeTests(unittest.TestCase):
    def test_state_is_isolated_per_entity(self):
        clock = Clock()
        store = InMemoryMotivationStore(clock)
        store.update("jerry", {"speaker": {"status": "anonymous"}})
        self.assertIsNotNone(store.inspect("jerry"))
        self.assertIsNone(store.inspect("ferret"))
        store.update("ferret", {"speaker": {"status": "recognized"}})
        self.assertNotEqual(store.inspect("jerry"), store.inspect("ferret"))

    def test_new_store_models_process_restart_and_resets_state(self):
        clock = Clock()
        first = InMemoryMotivationStore(clock)
        first.update("jerry", {"speaker": {"status": "anonymous"}})
        restarted = InMemoryMotivationStore(clock)
        self.assertIsNone(restarted.inspect("jerry"))

    def test_capabilities_are_explicit_and_unknown_values_are_filtered(self):
        caps = declared_capabilities({
            "body_capabilities": ["speaker", "motion", "telepathy", 42]
        })
        self.assertEqual(caps, {Capability.SPEAKER, Capability.MOTION})
        self.assertEqual(declared_capabilities({"speaker": True}), frozenset())

    def test_privacy_projection_redacts_candidate_and_private_fields(self):
        source = {
            "speaker": {
                "status": "probable",
                "candidate_id": "secret-person",
                "candidate_display_name": "Private Name",
                "embedding": [1, 2, 3],
            },
            "known_speakers": [{"id": "secret-person"}],
            "raw_memories": ["private fact"],
            "last_sleep": {"status": "known", "duration_seconds": 123},
        }
        neutral = privacy_neutral_context(source, 7)
        rendered = repr(neutral)
        self.assertTrue(neutral.anonymous_presence)
        self.assertFalse(neutral.familiar_presence)
        self.assertEqual(neutral.sleep_duration_seconds, 123)
        for secret in ("secret-person", "Private Name", "embedding", "private fact"):
            self.assertNotIn(secret, rendered)

    def test_goal_output_is_bounded_predictable_and_privacy_neutral(self):
        clock = Clock()
        store = InMemoryMotivationStore(clock)
        context = {
            "body_capabilities": ["speaker", "microphone"],
            "speaker": {"status": "probable", "candidate_display_name": "Hidden"},
        }
        first = store.update("jerry", context)
        restarted = InMemoryMotivationStore(clock).update("jerry", context)
        self.assertEqual(first.prompt_context, restarted.prompt_context)
        view = json.loads(first.prompt_context)
        self.assertLessEqual(len(view), 3)
        self.assertNotIn("Hidden", first.prompt_context)
        self.assertTrue(all(set(item) == {"goal", "score", "reasons"} for item in view))
        kinds = {goal.kind for goal in first.goals}
        self.assertIn(GoalKind.ASK_FOLLOW_UP, kinds)


if __name__ == "__main__":
    unittest.main()
