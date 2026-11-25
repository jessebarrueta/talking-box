"""Separate, durable Singing Friend email approvals (no Studio membership grants)."""
import os
import json
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, Field, field_validator

try:
    from .grey_identity import PRIVATE, clerk_request, denied, require_admin, require_identity
except ImportError:
    from grey_identity import PRIVATE, clerk_request, denied, require_admin, require_identity

router = APIRouter(prefix="/companions/grey", tags=["Family access"])


async def query(sql, params=()):
    """Use Neon's HTTPS SQL transport, as Studio does, with bound parameters."""
    try:
        dsn = os.environ["GREY_DATABASE_URL"]
        host = urlparse(dsn).hostname
        if not host or not host.endswith(".neon.tech"):
            raise ValueError()
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post("https://" + host + "/sql", headers={
                "Neon-Connection-String": dsn, "Neon-Raw-Text-Output": "true", "Neon-Array-Mode": "true",
            }, json={"query": sql, "params": list(params)})
            response.raise_for_status()
            result = response.json()
            fields = [field["name"] for field in result["fields"]]
            return [dict(zip(fields, row, strict=True)) for row in result["rows"]]
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        raise denied(503, "Family access is temporarily unavailable. Please try again.") from None


async def access_record(identity):
    rows = await query("""SELECT email, status FROM grey.family_access
        WHERE email IN (SELECT jsonb_array_elements_text($1::jsonb))
        ORDER BY CASE status WHEN 'approved' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
        email LIMIT 1""", (json.dumps(identity.emails),))
    return rows[0] if rows else None


async def require_family(authorization):
    identity = await require_identity(authorization)
    if identity.is_admin:
        return identity
    row = await access_record(identity)
    if not row or row["status"] != "approved":
        raise denied(403, "Your family account is awaiting approval for live calls.")
    return identity


@router.get("/access")
async def access(response: Response, authorization: str = Header(default="")):
    response.headers.update(PRIVATE)
    identity = await require_identity(authorization)
    row = await access_record(identity) if not identity.is_admin else None
    status = "approved" if identity.is_admin else row["status"] if row else "not_requested"
    return {"email": identity.primary_email, "status": status,
        "approved": status == "approved", "is_admin": identity.is_admin,
        "realtime": bool(os.getenv("GREY_REALTIME_API_KEY"))}


class AccessRequest(BaseModel):
    grownup_confirmed: bool


@router.post("/access/request")
async def request_access(body: AccessRequest, response: Response, authorization: str = Header(default="")):
    response.headers.update(PRIVATE)
    identity = await require_identity(authorization)
    if body.grownup_confirmed is not True:
        raise denied(400, "A parent or guardian needs to request family access.")
    # A repeated request cannot undo approval or revocation.
    await query("""INSERT INTO grey.family_access (email, status, requested_by)
        VALUES ($1, 'pending', $2) ON CONFLICT (email) DO NOTHING""",
        (identity.primary_email, identity.user_id))
    return await access(response, authorization)


class FamilyEmail(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        from email_validator import validate_email, EmailNotValidError
        try:
            return validate_email(value.strip(), check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            raise ValueError("Enter a valid email address.") from None


class FamilyDecision(FamilyEmail):
    status: str = Field(pattern="^(approved|revoked)$")


@router.get("/admin/families")
async def families(response: Response, authorization: str = Header(default="")):
    response.headers.update(PRIVATE)
    await require_admin(authorization)
    rows = await query("""SELECT email, status, created_at, updated_at
        FROM grey.family_access ORDER BY CASE status WHEN 'pending' THEN 0
        WHEN 'approved' THEN 1 ELSE 2 END, email LIMIT 1001""")
    return {"families": rows[:1000], "has_more": len(rows) > 1000}


@router.post("/admin/families")
async def decide_family(body: FamilyDecision, response: Response, authorization: str = Header(default="")):
    response.headers.update(PRIVATE)
    admin = await require_admin(authorization)
    await query("""INSERT INTO grey.family_access (email, status, decided_by)
        VALUES ($1, $2, $3) ON CONFLICT (email) DO UPDATE SET
        status = EXCLUDED.status, decided_by = EXCLUDED.decided_by, updated_at = now()""",
        (body.email, body.status, admin.user_id))
    return {"email": body.email, "status": body.status}


def signup_destination():
    origin = os.getenv("GREY_APP_ORIGIN", "https://enormousbrain.com").rstrip("/")
    parsed = urlparse(origin)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise denied(503, "The family signup destination is not configured.")
    return origin + "/singing-friend/?account=signup"


@router.post("/admin/families/signup-link")
async def signup_link(body: FamilyEmail, response: Response, authorization: str = Header(default="")):
    response.headers.update(PRIVATE)
    await require_admin(authorization)
    rows = await query("SELECT status FROM grey.family_access WHERE email = $1", (body.email,))
    row = rows[0] if rows else None
    if not row or row["status"] != "approved":
        raise denied(409, "Approve this email before creating its signup link.")
    destination = signup_destination()
    existing = await clerk_request("GET", "users", params={"email_address": body.email, "limit": 10})
    if existing.status_code != 200:
        raise denied(503, "We could not check this account. Please try again.")
    data = existing.json()
    users = data if isinstance(data, list) else data.get("data", [])
    if any(any(a.get("email_address", "").strip().lower() == body.email
            for a in u.get("email_addresses", [])) for u in users):
        return {"url": destination.replace("account=signup", "account=signin"), "existing_account": True}
    invitation = await clerk_request("POST", "invitations", json={
        "email_address": body.email, "redirect_url": destination, "notify": False,
        "ignore_existing": True, "expires_in_days": 7,
    })
    if invitation.status_code not in (200, 201):
        raise denied(503, "A signup link could not be created. Please try again.")
    link = invitation.json().get("url")
    if not isinstance(link, str) or not link.startswith("https://"):
        raise denied(502, "The signup link was unavailable. Please try again.")
    return {"url": link, "existing_account": False}
