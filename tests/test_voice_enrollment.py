import unittest

import numpy as np

from pi.voice_enrollment import (
    FAMILIAR_NAME_PROMPT,
    UNKNOWN_NAME_PROMPT,
    VoiceEnrollmentSession,
    consent_answer,
)


class VoiceEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.session = VoiceEnrollmentSession(clock=lambda: self.now)
        self.embedding = np.array([1.0, 0.0], dtype=np.float32)
        self.promotions = []

    def handle(self, text, existing=(), familiar=False):
        def promote(name, embeddings):
            self.promotions.append((name, embeddings))
            return {"id": name.lower(), "display_name": name}
        return self.session.handle(
            "anon-1", self.embedding, text, existing, promote, familiar=familiar
        )

    def reach_name(self, familiar=False):
        self.assertIsNone(self.handle("first useful turn", familiar=familiar))
        self.assertIsNone(self.handle("second useful turn", familiar=familiar))
        return self.handle("third useful turn", familiar=familiar)

    def test_unknown_voice_uses_ordinary_name_prompt(self):
        self.assertEqual(self.reach_name(), UNKNOWN_NAME_PROMPT)

    def test_familiar_voice_prompt_discloses_no_candidate_identity(self):
        reply = self.reach_name(familiar=True)
        self.assertEqual(reply, FAMILIAR_NAME_PROMPT)
        self.assertEqual(
            reply,
            "You sound familiar, but I can’t confirm who you are—what name should I use?",
        )
        context = self.session.context("anon-1")
        self.assertNotIn("candidate", context)
        self.assertNotIn("name", context)

    def test_familiar_signal_is_sticky_without_storing_candidate(self):
        self.assertIsNone(self.handle("first", familiar=True))
        self.assertIsNone(self.handle("second"))
        self.assertEqual(self.handle("third"), FAMILIAR_NAME_PROMPT)

    def test_only_exact_unambiguous_consent_promotes(self):
        self.reach_name()
        self.assertIn("okay", self.handle("My name is Ada").lower())
        self.assertIn("clear yes or no", self.handle("maybe later").lower())
        self.assertEqual(self.promotions, [])
        self.assertIn("remember you", self.handle("yes please").lower())
        self.assertEqual(self.promotions[0][0], "Ada")
        self.assertNotIn("anon-1", self.session.pending)

    def test_decline_deletes_name_and_samples(self):
        self.reach_name()
        self.handle("Call me Ada")
        self.assertIn("won't save", self.handle("no thanks").lower())
        self.assertEqual(self.promotions, [])
        self.assertEqual(self.session.pending, {})

    def test_ambiguous_consent_eventually_fails_closed(self):
        self.reach_name()
        self.handle("Ada")
        self.handle("perhaps")
        self.assertIn("won't save", self.handle("why do you ask").lower())
        self.assertEqual(self.session.pending, {})

    def test_existing_name_collision_never_promotes(self):
        self.reach_name()
        reply = self.handle("Mary Jane", [{"id": "mary-jane", "display_name": "M. Jane"}])
        self.assertIn("won't merge", reply.lower())
        self.assertEqual(self.promotions, [])

    def test_failed_promotion_discards_buffer_and_reports_safely(self):
        self.reach_name()
        self.handle("Ada")
        reply = self.session.handle(
            "anon-1", self.embedding, "yes", [],
            lambda *_: (_ for _ in ()).throw(OSError("disk full")),
        )
        self.assertIn("discarded", reply.lower())
        self.assertNotIn("disk full", reply.lower())
        self.assertEqual(self.session.pending, {})

    def test_timeout_removes_claim_and_embeddings(self):
        self.reach_name()
        self.handle("Ada")
        self.now += 181
        self.assertEqual(self.session.expire(), 1)
        self.assertEqual(self.session.pending, {})

    def test_consent_classifier_rejects_embedded_or_conflicted_yes(self):
        self.assertTrue(consent_answer("yes"))
        self.assertFalse(consent_answer("no"))
        self.assertIsNone(consent_answer("yes, but actually no"))
        self.assertIsNone(consent_answer("Jerry said yes"))


class AtomicEmbeddingEnrollmentTests(unittest.TestCase):
    def test_failed_save_rolls_back_in_memory_profile(self):
        from pi.speaker_identity import SpeakerIdentity
        identity = object.__new__(SpeakerIdentity)
        identity.embedding_dim = 2
        identity.data = {"speakers": {}}
        identity._save_profiles = lambda: (_ for _ in ()).throw(OSError("disk full"))
        with self.assertRaises(OSError):
            identity.enroll_embeddings("ada", "Ada", [[1.0, 0.0]], True)
        self.assertEqual(identity.data["speakers"], {})

    def test_existing_profile_is_never_overwritten(self):
        from pi.speaker_identity import SpeakerIdentity
        identity = object.__new__(SpeakerIdentity)
        identity.embedding_dim = 2
        identity.data = {"speakers": {"ada": {"display_name": "Ada"}}}
        with self.assertRaises(ValueError):
            identity.enroll_embeddings("ada", "Other Ada", [[1.0, 0.0]], True)


if __name__ == "__main__":
    unittest.main()
