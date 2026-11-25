"""Verified Clerk identities for Grey; never accept browser-supplied emails."""
import os
import re
import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import HTTPException

PRIVATE = {"Cache-Control": "private, no-store"}
_jwks = {}


def identity_configured():
    return all(os.getenv(key, "").strip() for key in (
        "GREY_CLERK_SECRET_KEY", "GREY_CLERK_ISSUER", "GREY_OWNER_USER_ID",
        "GREY_AUTHORIZED_PARTIES", "GREY_DATABASE_URL",
    ))


def denied(status, message):
    return HTTPException(status, message, headers=PRIVATE)


async def clerk_request(method, path, **kwargs):
    key = os.getenv("GREY_CLERK_SECRET_KEY", "").strip()
    if not key:
        raise denied(503, "Family sign-in is being set up.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.request(method, "https://api.clerk.com/v1/" + path,
                headers={"Authorization": "Bearer " + key, "User-Agent": "SingingFriend/1.0"}, **kwargs)
    except httpx.HTTPError:
        raise denied(503, "Family sign-in is temporarily unavailable. Please try again.") from None
    return response


async def signing_key(kid):
    issuer = os.getenv("GREY_CLERK_ISSUER", "").rstrip("/")
    cached = _jwks.get(issuer)
    if not cached or cached[0] < time.monotonic():
        response = await clerk_request("GET", "jwks")
        if response.status_code != 200:
            raise denied(503, "We could not verify your sign-in. Please try again.")
        try:
            keys = response.json()["keys"]
            if not isinstance(keys, list):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise denied(503, "We could not verify your sign-in. Please try again.") from None
        cached = (time.monotonic() + 300, keys)
        _jwks[issuer] = cached
    for key in cached[1]:
        if key.get("kid") == kid and key.get("kty") == "RSA":
            try:
                return jwt.PyJWK.from_dict(key, algorithm="RS256").key
            except (ValueError, jwt.PyJWTError):
                break
    raise denied(401, "Please sign in again.")


@dataclass(frozen=True)
class Identity:
    user_id: str
    emails: tuple[str, ...]
    primary_email: str
    is_admin: bool


async def require_identity(authorization):
    if not identity_configured():
        raise denied(503, "Family sign-in is being set up.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 16384:
        raise denied(401, "Sign in with your grown-up account to continue.")
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError()
        key = await signing_key(header["kid"])
        claims = jwt.decode(token, key, algorithms=["RS256"],
            issuer=os.environ["GREY_CLERK_ISSUER"].rstrip("/"), leeway=5,
            options={"require": ["exp", "nbf", "iat", "iss", "sub", "sid", "azp"]})
        parties = {v.strip() for v in os.environ["GREY_AUTHORIZED_PARTIES"].split(",") if v.strip()}
        if claims["azp"] not in parties or claims.get("sts") == "pending":
            raise ValueError()
        uid, sid = claims["sub"], claims["sid"]
        if not re.fullmatch(r"user_[A-Za-z0-9]+", uid) or not re.fullmatch(r"sess_[A-Za-z0-9]+", sid):
            raise ValueError()
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise denied(401, "Your sign-in has expired. Please sign in again.") from None
    # Check the current provider session and user, including revocation/deletion.
    session_response = await clerk_request("GET", "sessions/" + sid)
    if session_response.status_code == 404:
        raise denied(401, "Please sign in again.")
    if session_response.status_code != 200:
        raise denied(503, "We could not verify your sign-in. Please try again.")
    session = session_response.json()
    if session.get("status") != "active" or session.get("user_id") != uid:
        raise denied(401, "Please sign in again.")
    response = await clerk_request("GET", "users/" + uid)
    if response.status_code == 404:
        raise denied(401, "This account is no longer available.")
    if response.status_code != 200:
        raise denied(503, "We could not verify your email. Please try again.")
    user = response.json()
    if user.get("id") != uid or user.get("banned") or user.get("locked"):
        raise denied(401, "This account is unavailable.")
    verified = [a for a in user.get("email_addresses", [])
        if a.get("verification", {}).get("status") == "verified"]
    emails = tuple(dict.fromkeys(a["email_address"].strip().lower() for a in verified))
    primary = next((a["email_address"].strip().lower() for a in verified
        if a.get("id") == user.get("primary_email_address_id")), emails[0] if emails else "")
    if not emails:
        raise denied(403, "Verify your email address before requesting family access.")
    return Identity(uid, emails, primary, uid == os.environ["GREY_OWNER_USER_ID"].strip())


async def require_admin(authorization):
    identity = await require_identity(authorization)
    if not identity.is_admin:
        raise denied(403, "Only the Singing Friend owner can manage family access.")
    return identity
