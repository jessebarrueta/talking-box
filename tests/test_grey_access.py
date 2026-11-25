import os
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient

from server import grey, grey_access, grey_identity, main

ENV = {
    "GREY_CLERK_SECRET_KEY": "sk_test_example", "GREY_CLERK_ISSUER": "https://family.clerk.accounts.dev",
    "GREY_OWNER_USER_ID": "user_owner", "GREY_AUTHORIZED_PARTIES": "https://singing.example.com",
    "GREY_DATABASE_URL": "postgresql://unused", "GREY_REALTIME_API_KEY": "test-only",
}
FAMILY = grey_identity.Identity("user_family", ("parent@example.com",), "parent@example.com", False)
OWNER = grey_identity.Identity("user_owner", ("owner@example.com",), "owner@example.com", True)


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.keys = patch.object(grey_identity, "signing_key", AsyncMock(return_value=self.key.public_key()))
        self.keys.start()
        self.addCleanup(self.keys.stop)
        self.provider = patch.object(grey_identity, "clerk_request", AsyncMock(side_effect=self.provider_response))
        self.mock_provider = self.provider.start()
        self.addCleanup(self.provider.stop)
        self.session_status = "active"
        self.user = {"id": "user_family", "primary_email_address_id": "email_primary", "email_addresses": [
            {"id": "email_primary", "email_address": "Parent@Example.com", "verification": {"status": "verified"}},
            {"id": "email_other", "email_address": "approved@example.com", "verification": {"status": "unverified"}},
        ]}

    async def provider_response(self, method, path, **kwargs):
        return httpx.Response(200, json={"status": self.session_status, "user_id": "user_family"}
            if path.startswith("sessions/") else self.user)

    def token(self, **updates):
        now = int(time.time())
        claims = {"iss": ENV["GREY_CLERK_ISSUER"], "sub": "user_family", "sid": "sess_family",
            "azp": "https://singing.example.com", "exp": now + 60, "nbf": now - 1, "iat": now - 1}
        claims.update(updates)
        return "Bearer " + jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test"})

    async def test_resolves_verified_email_and_excludes_unverified_email(self):
        identity = await grey_identity.require_identity(self.token())
        self.assertEqual(identity, FAMILY)

    async def test_rejects_invalid_claims_before_provider_lookup(self):
        for changes in [{"exp": 1}, {"nbf": int(time.time()) + 600}, {"iss": "https://other.example.com"},
                {"azp": "https://animation.example.com"}, {"azp": "https://singing.example.com.evil.test"},
                {"sid": ""}, {"sub": "user_family/other"}, {"sts": "pending"}]:
            with self.subTest(changes=changes), self.assertRaises(HTTPException) as caught:
                await grey_identity.require_identity(self.token(**changes))
            self.assertEqual(caught.exception.status_code, 401)
        self.mock_provider.assert_not_called()

    async def test_rejects_bad_signature(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with patch.object(self, "key", other):
            token = self.token()
        with self.assertRaises(HTTPException) as caught:
            await grey_identity.require_identity(token)
        self.assertEqual(caught.exception.status_code, 401)
        self.mock_provider.assert_not_called()

    async def test_rejects_missing_or_wrong_authorization(self):
        for token in ["", "Basic nope", "Bearer old-family-invite", "Bearer " + "x" * 17000]:
            with self.subTest(token=token[:20]), self.assertRaises(HTTPException) as caught:
                await grey_identity.require_identity(token)
            self.assertEqual(caught.exception.status_code, 401)

    async def test_revoked_session_is_rejected_even_with_unexpired_token(self):
        self.session_status = "revoked"
        with self.assertRaises(HTTPException) as caught:
            await grey_identity.require_identity(self.token())
        self.assertEqual(caught.exception.status_code, 401)

    async def test_unverified_email_cannot_request_access(self):
        self.user["email_addresses"][0]["verification"]["status"] = "unverified"
        with self.assertRaises(HTTPException) as caught:
            await grey_identity.require_identity(self.token())
        self.assertEqual(caught.exception.status_code, 403)

    async def test_banned_account_cannot_sign_in(self):
        self.user["banned"] = True
        with self.assertRaises(HTTPException) as caught:
            await grey_identity.require_identity(self.token())
        self.assertEqual(caught.exception.status_code, 401)

    async def test_owner_role_is_not_claimed_by_email(self):
        self.user["email_addresses"][0]["email_address"] = "owner@example.com"
        identity = await grey_identity.require_identity(self.token(is_admin=True, role="admin"))
        self.assertFalse(identity.is_admin)

    async def test_non_owner_cannot_manage_families(self):
        with self.assertRaises(HTTPException) as caught:
            await grey_identity.require_admin(self.token())
        self.assertEqual(caught.exception.status_code, 403)


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_missing_and_revoked_accounts_are_denied(self):
        for row in [None, {"status": "pending"}, {"status": "revoked"}]:
            with patch.object(grey_access, "require_identity", AsyncMock(return_value=FAMILY)), \
                    patch.object(grey_access, "access_record", AsyncMock(return_value=row)), \
                    self.assertRaises(HTTPException) as caught:
                await grey_access.require_family("Bearer valid")
            self.assertEqual(caught.exception.status_code, 403)

    async def test_approved_verified_family_can_call(self):
        with patch.object(grey_access, "require_identity", AsyncMock(return_value=FAMILY)), \
                patch.object(grey_access, "access_record", AsyncMock(return_value={"status": "approved"})):
            self.assertEqual(await grey_access.require_family("Bearer valid"), FAMILY)

    async def test_revocation_is_checked_on_every_call(self):
        with patch.object(grey_access, "require_identity", AsyncMock(return_value=FAMILY)), \
                patch.object(grey_access, "access_record", AsyncMock(side_effect=[{"status": "approved"}, {"status": "revoked"}])):
            await grey_access.require_family("Bearer valid")
            with self.assertRaises(HTTPException):
                await grey_access.require_family("Bearer valid")

    async def test_owner_has_access_without_approving_own_email(self):
        with patch.object(grey_access, "require_identity", AsyncMock(return_value=OWNER)), \
                patch.object(grey_access, "access_record", AsyncMock()) as lookup:
            self.assertEqual(await grey_access.require_family("Bearer valid"), OWNER)
            lookup.assert_not_called()

    async def test_account_response_has_only_own_details_and_no_cache(self):
        with patch.object(grey_access, "require_identity", AsyncMock(return_value=FAMILY)), \
                patch.object(grey_access, "access_record", AsyncMock(return_value={"status": "pending"})):
            response = Response()
            result = await grey_access.access(response, "Bearer valid")
        self.assertEqual(result["email"], FAMILY.primary_email)
        self.assertFalse(result["approved"])
        self.assertFalse(result["is_admin"])
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    async def test_parent_confirmation_required_before_request(self):
        with patch.object(grey_access, "require_identity", AsyncMock(return_value=FAMILY)), self.assertRaises(HTTPException) as caught:
            await grey_access.request_access(grey_access.AccessRequest(grownup_confirmed=False), Response(), "Bearer valid")
        self.assertEqual(caught.exception.status_code, 400)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_call_requires_account_before_contacting_provider(self):
        with patch.object(grey, "require_family", AsyncMock(side_effect=HTTPException(403, "Awaiting approval"))), \
                patch.object(grey.httpx, "AsyncClient") as upstream:
            response = self.client.post("/companions/grey/calls", json={"sdp": "v=0\r\nm=audio 9 TEST"}, headers={"Authorization": "Bearer old-invite"})
        self.assertEqual(response.status_code, 403)
        upstream.assert_not_called()

    def test_every_admin_route_requires_owner(self):
        with patch.object(grey_access, "require_admin", AsyncMock(side_effect=HTTPException(403, "Owner only"))), \
                patch.object(grey_access, "query") as db:
            responses = [self.client.get("/companions/grey/admin/families"),
                self.client.post("/companions/grey/admin/families", json={"email": "parent@example.com", "status": "approved"}),
                self.client.post("/companions/grey/admin/families/signup-link", json={"email": "parent@example.com"})]
        self.assertTrue(all(r.status_code == 403 for r in responses))
        db.assert_not_called()

    def test_anonymous_account_request_is_denied(self):
        self.assertEqual(self.client.get("/companions/grey/access").status_code, 401)
        self.assertEqual(self.client.post("/companions/grey/access/request", json={"grownup_confirmed": True}).status_code, 401)

    def test_missing_config_fails_closed(self):
        with patch.dict(os.environ, {"GREY_OWNER_USER_ID": ""}):
            self.assertFalse(self.client.get("/companions/grey").json()["realtime"])
            self.assertEqual(self.client.get("/companions/grey/access").status_code, 503)

    def test_email_is_normalized_and_malformed_addresses_rejected(self):
        self.assertEqual(grey_access.FamilyEmail(email="  Parent@Example.COM ").email, "parent@example.com")
        for email in ["invalid", "parent@example.com\r\nX-Header: nope", "one@example.com,two@example.com"]:
            with self.subTest(email=email), self.assertRaises(ValueError):
                grey_access.FamilyEmail(email=email)


if __name__ == "__main__":
    unittest.main()
