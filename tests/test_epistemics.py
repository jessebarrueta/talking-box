import unittest

from server.epistemics import (
    history_user_content,
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

    def test_anonymous_key_includes_voice_session(self):
        context = {
            **self.context,
            "speaker": {
                "status": "anonymous",
                "anonymous_id": "anon-1",
                "seen_count": 2,
            },
        }
        speaker = normalize_speaker_context(context)
        self.assertEqual(speaker["anonymous_key"], "session-a:anon-1")
        self.assertEqual(
            speaker_key(speaker),
            "anonymous:session-a:anon-1",
        )
        self.assertFalse(identity_ledger(context)["identity_verified"])

    def test_relationship_language_does_not_resolve_a_person(self):
        self.assertIsNone(resolve_known_speaker("my husband", self.context))
        self.assertIsNone(resolve_known_speaker("my wife", self.context))
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
        anonymous = {"status": "anonymous", "anonymous_key": "x:a"}

        self.assertTrue(memory_visible_to_speaker(memory, jesse))
        self.assertFalse(memory_visible_to_speaker(memory, greyson))
        self.assertFalse(memory_visible_to_speaker(memory, anonymous))

    def test_history_labels_anonymous_session_key(self):
        item = {
            "user_text": "hello",
            "context": {
                "voice_session_id": "boot-7",
                "speaker": {
                    "status": "anonymous",
                    "anonymous_id": "anon-1",
                },
            },
        }
        rendered = history_user_content(item)
        self.assertIn("boot-7:anon-1", rendered)
        self.assertIn("hello", rendered)


if __name__ == "__main__":
    unittest.main()
