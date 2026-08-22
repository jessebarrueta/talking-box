# Talking Box V7.2 — Epistemic Social Layer

V7.2 turns the speaker-recognition work into an explicit **identity and social
reasoning boundary** instead of leaving those decisions to conversational vibes.

The central rule is simple:

> Language may suggest who somebody is. Only evidence is allowed to establish it.

## What V7.2 adds

### 1. Boot-scoped anonymous identity keys

V7.1 used `anon-1`, `anon-2`, etc. Those labels restart from 1 whenever the Pi
process restarts, so old conversation history could eventually contain two
unrelated `anon-1` speakers.

V7.2 generates a random `voice_session_id` at process start and sends keys like:

```text
7b3a1b9c02ef:anon-1
```

That key means only:

> the same unidentified voice cluster during this device process

It does **not** mean a real-world person.

### 2. Identity evidence ledger

Every interaction prompt now contains a server-generated identity ledger.

A speaker can be:

- **recognized** — matched to an enrolled local voice profile
- **anonymous** — same unknown voice cluster in this boot/session
- **unverified** — audio insufficient/unavailable/error

Relationship language and conversational symmetry are explicitly prohibited as
identity evidence.

So this sequence:

```text
anon-A: "Tell my husband I love him."
anon-B: "Did my wife leave me a message?"
```

must **not** become:

```text
anon-A = Janine
anon-B = Jesse
```

unless independent identity evidence establishes those people.

### 3. Speaker-scoped memory firewall

New memories have a scope:

```text
speaker  → available only when that exact enrolled voice is verified
entity   → shared fact about Jerry/device/shared world
```

A recognized speaker's personal preferences, facts, projects, etc. are private
by default. Anonymous speakers cannot create durable person-specific memory.

Older pre-scope memories remain `legacy-unscoped` for compatibility.

### 4. Grounded person-to-person mailbox

V7.2 adds a real `social_messages` table.

A message can only be queued when the recipient exactly resolves to an enrolled
speaker id/display name reported by the Pi.

Valid:

```text
"Jerry, tell Jesse I love him."
```

Not routable yet:

```text
"Jerry, tell my husband I love him."
```

For the second form Jerry should ask for the person's name. The server also has
a deterministic truthfulness guard: if the model tries to queue a relationship-
only recipient anyway, the spoken reply is replaced with a request for the exact
name and nothing is stored.

Pending messages are fetched **only when the recipient's voice is recognized**.
The model must explicitly say the message in its spoken reply before returning
the message id as delivered; the server validates those ids before marking them.

An anonymous sender is stored as an unidentified sender. Jerry does not invent a
name from context.

## Apply

Start from a clean and current checkout:

```bash
git pull --ff-only
git status
```

Unzip the V7.2 bundle somewhere outside the repo, then:

```bash
python3 /path/to/talking-box-v7.2/apply-v7.2.py /path/to/talking-box
```

Validate:

```bash
cd /path/to/talking-box

python3 -m py_compile \
  pi/talking_box.py \
  server/main.py \
  server/epistemics.py

python3 -m unittest tests/test_epistemics.py
git diff --check
git diff
```

The patcher intentionally creates **no `.bak` files** in the repository.

## Database migration — do this before server deployment

Run the contents of:

```text
supabase/v7_2_social_messages.sql
```

in the Supabase SQL editor for the `enormousbrain` project.

It stores text messages and identity evidence metadata only. No raw audio or
voice embeddings are uploaded.

## Commit / deploy

After reviewing the diff:

```bash
git add \
  pi/talking_box.py \
  server/main.py \
  server/epistemics.py \
  supabase/v7_2_social_messages.sql \
  tests/test_epistemics.py \
  README-V7.2.md

git commit -m "Add V7.2 epistemic social layer"
git push
```

### Pi

```bash
cd ~/talking-box
git pull --ff-only
sudo systemctl restart talking-box.service
journalctl -u talking-box.service -f
```

Speaker logs should now include `voice_session_id`, and anonymous speakers should
also include `anonymous_key`.

### GoDaddy / Passenger server

Upload both:

```text
server/main.py       → application main.py
server/epistemics.py → application epistemics.py
```

Then restart the Passenger application.

Check `/health`; V7.2 reports:

```json
{
  "version": "0.7.2",
  "identity_grounding": "epistemic-v1",
  "social_mailbox": "grounded-v1"
}
```

## Tests worth doing in the room

### Identity firewall

Have an unidentified person say:

```text
Tell my husband I love him.
```

Jerry should ask for the recipient's name rather than promising delivery.

Then have another unidentified voice say:

```text
Did my wife leave me a message?
```

Jerry should not infer that this person is the husband.

### Grounded mailbox

Have an unidentified person say:

```text
Tell Jesse I love him and appreciate everything he does.
```

That is routable because `Jesse` is an exact enrolled speaker name.

Later, when Jesse is actually voice-recognized, Jerry may deliver it. The sender
should be described as unidentified unless that sender's own voice was recognized.

### Memory isolation

Tell Jerry a personal preference while recognized as Jesse, then have another
recognized speaker ask a question that would tempt Jerry to use it. The other
speaker should not receive Jesse-scoped memory.

## Deliberately not in V7.2

An anonymous speaker saying "My name is Janine" is still a **self-reported claim**,
not durable voice enrollment. V7.2 will let the conversation use that claim with
appropriate uncertainty, but it will not unlock Janine-scoped private memory or
persist a biometric identity.

The next logical slice is explicit conversational enrollment:

```text
anonymous voice
→ self-reported name
→ explicit consent
→ collect several fresh voice samples
→ durable local enrollment
→ future verified recognition
```

That should be a separate, auditable transition rather than something an LLM can
silently decide to do.
