BEGIN;
CREATE SCHEMA IF NOT EXISTS grey;
REVOKE ALL ON SCHEMA grey FROM PUBLIC;
CREATE TABLE IF NOT EXISTS grey.family_access (
    email text PRIMARY KEY CHECK (email = lower(btrim(email)) AND length(email) <= 254),
    status text NOT NULL CHECK (status IN ('pending', 'approved', 'revoked')),
    requested_by text,
    decided_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON grey.family_access FROM PUBLIC;
COMMIT;
