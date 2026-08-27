import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import main


class DeviceAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()

    def test_health_remains_public(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)

    def test_v1_request_without_credentials_is_unauthorized(self):
        with patch.object(main, "TALKING_BOX_DEVICE_TOKEN", "test-secret"):
            response = self.client.post(
                "/v1/transcribe",
                json={"audio_base64": "not-base64"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_v1_prefix_itself_is_also_protected(self):
        with patch.object(main, "TALKING_BOX_DEVICE_TOKEN", "test-secret"):
            response = self.client.get("/v1")

        self.assertEqual(response.status_code, 401)

    def test_v1_request_with_invalid_credentials_is_unauthorized(self):
        with patch.object(main, "TALKING_BOX_DEVICE_TOKEN", "test-secret"):
            response = self.client.post(
                "/v1/transcribe",
                headers={"Authorization": "Bearer wrong-secret"},
                json={"audio_base64": "not-base64"},
            )

        self.assertEqual(response.status_code, 401)

    def test_valid_bearer_token_authorizes_request(self):
        with (
            patch.object(main, "TALKING_BOX_DEVICE_TOKEN", "test-secret"),
            patch.object(main, "OPENROUTER_API_KEY", "test-provider-key"),
        ):
            response = self.client.post(
                "/v1/transcribe",
                headers={"Authorization": "Bearer test-secret"},
                json={"audio_base64": "not-base64"},
            )

        # Validation is reached only after authentication succeeds.
        self.assertEqual(response.status_code, 400)

    def test_valid_api_key_authorizes_request(self):
        with (
            patch.object(main, "TALKING_BOX_DEVICE_TOKEN", "test-secret"),
            patch.object(main, "OPENROUTER_API_KEY", "test-provider-key"),
        ):
            response = self.client.post(
                "/v1/transcribe",
                headers={"X-API-Key": "test-secret"},
                json={"audio_base64": "not-base64"},
            )

        self.assertEqual(response.status_code, 400)

    def test_missing_server_token_configuration_fails_closed(self):
        with patch.object(main, "TALKING_BOX_DEVICE_TOKEN", ""):
            response = self.client.post(
                "/v1/transcribe",
                headers={"Authorization": "Bearer any-value"},
                json={"audio_base64": "not-base64"},
            )

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
