# Singing Friend family accounts

Grey uses the existing Clerk identity provider, with a separate allowlist in the Neon database's `grey.family_access` table. Studio memberships are not consulted or changed. A verified account is necessary but is not itself approval for live calls.

Apply `migrations/grey_family_access.sql` before deployment. It creates only the `grey` schema and family access table, revokes PUBLIC privileges, and leaves existing Studio tables unchanged. Database queries use Neon's HTTPS SQL transport with parameter binding, keeping credentials server-side.

API environment:

| Name | Purpose |
| --- | --- |
| `GREY_CLERK_SECRET_KEY` | Clerk backend key for the existing account instance |
| `GREY_CLERK_ISSUER` | Exact HTTPS issuer for that instance |
| `GREY_OWNER_USER_ID` | Explicit Clerk user ID allowed to administer this list |
| `GREY_AUTHORIZED_PARTIES` | Comma-separated exact frontend origins; no wildcards |
| `GREY_DATABASE_URL` | Neon PostgreSQL connection string |
| `GREY_APP_ORIGIN` | Canonical HTTPS origin for signup links |
| `GREY_REALTIME_API_KEY` | Dedicated voice provider key |

The frontend uses only `VITE_CLERK_PUBLISHABLE_KEY`. Existing `GREY_FAMILY_INVITE_HASHES` values are ignored. The owner identity is configured explicitly, never assigned to the first signup. Existing Studio users still require a Grey approval.

`GET /companions/grey/access` reports only the signed-in account's access. `POST /access/request` records a parent/guardian's request using their server-verified primary email. A duplicate request cannot overwrite approved or revoked access. Owner-only `/admin/families` routes list, approve and revoke emails. `/admin/families/signup-link` creates a seven-day Clerk signup link with `notify: false`, or returns a sign-in link for an existing account. No emails are sent by these routes; email verification in the Clerk signup flow is initiated by the person signing up.

Every live call checks RS256 signature, exact issuer, expiry, not-before, issued-at, session and user IDs, and allowed origin; it then reads the current provider session and verified emails and checks Grey approval. Pending/revoked emails never reach the voice provider. Signed-in owner access is explicit. Authenticated responses use `private, no-store` and do not return the full list to families.

Revocation applies to new calls immediately. Visible browser visits poll access every 30 seconds and close when access is lost. This is not server-side revocation of an already-established third-party WebRTC session. Calls do not persist transcripts or recordings; account access stores parent email, status, actor IDs and timestamps.

Validation: `python3 -m unittest discover -s tests -p 'test_*.py'`. The focused `test_grey_access.py` suite covers session forgery/expiry/origin, revoked provider sessions, verified email handling, independent approval, owner-only access, parent confirmation, and denial before voice-provider requests.
