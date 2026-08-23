import unittest

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
