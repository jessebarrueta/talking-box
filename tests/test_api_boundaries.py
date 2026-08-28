import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from server import main


DEVICE_HEADERS = {"Authorization": "Bearer test-device-token"}


class StubBackend:
    def __init__(self, *, model_content=None, mailbox=None):
        self.model_content = model_content or json.dumps(
            {
                "reply": "Hello.",
                "state_delta": {},
                "memory": {"remember": False},
                "social_message": {"create": False},
                "delivered_message_ids": [],
            }
        )
        self.mailbox = mailbox or []
        self.chat_payloads = []
        self.mailbox_lookups = []
        self.social_writes = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    @staticmethod
    def _response(method, url, status_code=200, json_data=None, text=None):
        request = httpx.Request(method, url)
        if text is not None:
            return httpx.Response(status_code, text=text, request=request)
        return httpx.Response(status_code, json=json_data, request=request)

    async def get(self, url, *, params=None, headers=None):
        if url.endswith("/entities"):
            return self._response(
                "GET",
                url,
                json_data=[
                    {
                        "id": "jerry",
                        "name": "Jerry",
                        "description": "A talking box.",
                        "current_state": {},
                    }
                ],
            )
        if url.endswith("/interactions"):
            return self._response("GET", url, json_data=[])
        if url.endswith("/memories"):
            return self._response(
                "GET",
                url,
                json_data=[
                    {
                        "id": 1,
                        "memory_type": "preference",
                        "summary": "Jesse's private launch code is marigold",
                        "importance": 1.0,
                        "metadata": {
                            "scope": "speaker",
                            "subject_speaker_id": "jesse",
                            "visibility": "private",
                        },
                    },
                    {
                        "id": 2,
                        "memory_type": "fact",
                        "summary": "The shared workshop has a red door",
                        "importance": 0.6,
                        "metadata": {
                            "scope": "entity",
                            "visibility": "public",
                        },
                    },
                ],
            )
        if url.endswith("/social_messages"):
            self.mailbox_lookups.append(params)
            return self._response("GET", url, json_data=self.mailbox)
        raise AssertionError(f"Unexpected GET {url}")

    async def post(self, url, *, params=None, headers=None, json=None):
        if url == "https://openrouter.ai/api/v1/chat/completions":
            self.chat_payloads.append(json)
            return self._response(
                "POST",
                url,
                json_data={
                    "choices": [{"message": {"content": self.model_content}}]
                },
            )
        if url.endswith("/interactions"):
            return self._response("POST", url, status_code=201, json_data=[])
        if url.endswith("/social_messages"):
            self.social_writes.append(json)
            row = {"id": 77, **json}
            return self._response("POST", url, status_code=201, json_data=[row])
        if url.endswith("/memories"):
            return self._response("POST", url, status_code=201, json_data=[json])
        raise AssertionError(f"Unexpected POST {url}")

    async def patch(self, url, *, params=None, headers=None, json=None):
        return self._response("PATCH", url, status_code=204, text="")


class InteractionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.config = patch.multiple(
            main,
            TALKING_BOX_DEVICE_TOKEN="test-device-token",
            SUPABASE_URL="https://stub.invalid",
            SUPABASE_SERVICE_ROLE_KEY="test-service-role",
            OPENROUTER_API_KEY="test-model-key",
        )
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.client.close()

    def interact(self, backend, speaker, text="What should I remember?"):
        context = {
            "known_speakers": [
                {"id": "jesse", "display_name": "Jesse"},
                {"id": "greyson", "display_name": "Greyson"},
            ],
            "speaker": speaker,
        }
        with patch.object(main.httpx, "AsyncClient", return_value=backend):
            return self.client.post(
                "/v1/entities/jerry/interact",
                headers=DEVICE_HEADERS,
                json={"text": text, "context": context},
            )

    def assert_private_memory_hidden(self, speaker):
        backend = StubBackend()
        response = self.interact(backend, speaker)

        self.assertEqual(response.status_code, 200, response.text)
        summaries = [item["summary"] for item in response.json()["memories_used"]]
        self.assertNotIn("Jesse's private launch code is marigold", summaries)
        prompt = json.dumps(backend.chat_payloads[0]["messages"])
        self.assertNotIn("marigold", prompt)

    def test_private_memory_is_hidden_from_other_probable_and_anonymous_speakers(self):
        speakers = [
            {"status": "recognized", "id": "greyson", "display_name": "Greyson"},
            {"status": "probable", "candidate_id": "jesse", "candidate_display_name": "Jesse"},
            {"status": "anonymous", "anonymous_key": "session:anon-1"},
        ]
        for speaker in speakers:
            with self.subTest(status=speaker["status"]):
                self.assert_private_memory_hidden(speaker)

    def test_probable_identity_cannot_read_private_memory_or_mailbox(self):
        backend = StubBackend(
            mailbox=[{"id": 9, "recipient_speaker_id": "jesse", "message_text": "Private note"}]
        )
        response = self.interact(
            backend,
            {"status": "probable", "candidate_id": "jesse", "candidate_display_name": "Jesse"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(backend.mailbox_lookups, [])
        self.assertNotIn("marigold", json.dumps(backend.chat_payloads[0]))
        self.assertNotIn("Private note", json.dumps(backend.chat_payloads[0]))

    def test_malformed_model_output_is_spoken_but_cannot_write_memory_or_mark_mail(self):
        backend = StubBackend(model_content="plain fallback response")
        response = self.interact(
            backend,
            {"status": "recognized", "id": "jesse", "display_name": "Jesse"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["text"], "plain fallback response")
        self.assertIsNone(body["memory_created"])
        self.assertEqual(body["messages_delivered"], [])

    def test_prompt_receives_only_predictable_privacy_neutral_goal_guidance(self):
        backend = StubBackend()
        context = {
            "body_capabilities": ["speaker", "microphone", "telepathy"],
            "speaker": {
                "status": "probable",
                "candidate_id": "hidden-id",
                "candidate_display_name": "Hidden Candidate",
                "embedding": [0.1, 0.2],
            },
        }
        deterministic_store = main.InMemoryMotivationStore(lambda: 100)
        with patch.object(main.httpx, "AsyncClient", return_value=backend), \
                patch.object(main, "motivation_store", deterministic_store):
            response = self.client.post(
                "/v1/entities/jerry/interact",
                headers=DEVICE_HEADERS,
                json={"text": "Hello", "context": context},
            )

        self.assertEqual(response.status_code, 200, response.text)
        instruction = backend.chat_payloads[0]["messages"][-1]["content"]
        guidance = instruction.split(
            "Selected conversational guidance goals (deterministic, privacy-neutral):\n",
            1,
        )[1].split("\n\nRaw device/context metadata:", 1)[0]
        parsed = json.loads(guidance)
        self.assertLessEqual(len(parsed), 3)
        self.assertNotIn("hidden-id", guidance)
        self.assertNotIn("Hidden Candidate", guidance)
        self.assertNotIn("embedding", guidance)
        self.assertIn("Never claim you", instruction)

    def test_human_emotion_claim_is_replaced_before_user_visible_reply(self):
        backend = StubBackend(model_content=json.dumps({
            "reply": "I feel happy and excited to see you.",
            "state_delta": {},
            "memory": {"remember": False},
            "social_message": {"create": False},
            "delivered_message_ids": [],
        }))
        response = self.interact(
            backend,
            {"status": "recognized", "id": "jesse", "display_name": "Jesse"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["text"],
            "I use internal interaction signals to guide my replies, but I "
            "don't have human emotions or subjective experience.",
        )

    def test_explicit_mailbox_routing_uses_exact_enrolled_recipient(self):
        backend = StubBackend(model_content="not valid JSON")
        response = self.interact(
            backend,
            {"status": "anonymous", "anonymous_key": "session:anon-1"},
            text="Tell Jesse to feed Cora",
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["social_message_created"]["recipient_speaker_id"], "jesse")
        self.assertEqual(backend.social_writes[0]["sender_status"], "anonymous")
        self.assertEqual(backend.social_writes[0]["message_text"], "to feed Cora")
        self.assertIn("when I recognize them", body["text"])


if __name__ == "__main__":
    unittest.main()
