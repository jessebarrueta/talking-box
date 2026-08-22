# Talking Box V7.1 — Unknown Speaker Discovery

V7.1 adds temporary same-session identity for voices Jerry does not yet know.

## Flow

```text
voice embedding
    ↓
known speaker registry
    ├── Jesse / Greyson / ...
    └── no confident match
            ↓
      anonymous session clusters
            ├── anon-1
            ├── anon-2
            └── ...
```

Durable enrolled profiles remain local in:

```text
~/.talking_box_speakers.json
```

Anonymous clusters are intentionally ephemeral. They live only in Jerry's Python process and disappear when `talking-box.service` restarts. No anonymous embedding is written to disk.

## What Jerry can now infer

The API receives speaker context like:

```json
{
  "status": "anonymous",
  "anonymous_id": "anon-1",
  "is_new": false,
  "seen_count": 3,
  "session_only": true
}
```

That lets the model reason naturally from recent turns carrying the same temporary ID: "I've heard you before," "Were you the one asking about the cat?", or "What's your name?"

A name learned this way is still session conversational context only. V7.1 does **not** silently convert an unknown person into a durable biometric profile. Conversational consent and promotion to the enrolled registry is the next slice.

## Starting thresholds

Known speakers:

```text
TALKING_BOX_SPEAKER_THRESHOLD=0.60
TALKING_BOX_SPEAKER_MARGIN=0.08
```

Anonymous same-session clustering:

```text
TALKING_BOX_ANON_THRESHOLD=0.58
TALKING_BOX_ANON_MARGIN=0.06
```

These are tunable. Test with real voices before loosening them.

## Enrollment fix

Enrollment now validates each capture immediately. A too-quiet sample causes only that sample to be redone instead of failing after all five recordings.

## Apply

From the repository root:

```bash
python3 /path/to/apply-v7.1.py
python3 -m py_compile \
  pi/speaker_identity.py \
  pi/anonymous_speaker_session.py \
  pi/talking_box.py \
  server/main.py

git diff
```

Then commit and push.

On the Pi after pulling:

```bash
sudo systemctl restart talking-box.service
journalctl -u talking-box.service -f
```

The GoDaddy deployment also needs the updated `server/main.py` and a Passenger restart for the natural-language unknown-speaker behavior to take effect.

## Expected logs

Known voice:

```text
Speaker identity: {"status":"recognized","id":"jesse",...}
```

First utterance from a new voice:

```text
Speaker identity: {"status":"anonymous","anonymous_id":"anon-1","is_new":true,"seen_count":1,...}
```

Same unknown voice later:

```text
Speaker identity: {"status":"anonymous","anonymous_id":"anon-1","is_new":false,"seen_count":2,...}
```

If one person repeatedly becomes `anon-2`, `anon-3`, etc., collect their actual scores and tune the anonymous threshold from evidence rather than guessing.
