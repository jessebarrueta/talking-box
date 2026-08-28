# Consent-based conversational voice enrollment

Jerry can offer local voice enrollment after three usable turns from the same
anonymous voice cluster. This is a deterministic Pi-owned state machine, not
an LLM action.

Before consent, embeddings and any claimed name exist only in the running Pi
process. They are never written to disk or sent to the server. An unknown voice
gets the ordinary name prompt. A weak/familiar match remains anonymous and gets
the cautious prompt without revealing the candidate identity. Recognition and
anonymous-clustering thresholds are unchanged.

Only a complete answer from a small affirmative allow-list can call the durable
profile writer. A decline, ambiguity, timeout, shutdown, or process exit clears
the buffer. The server sees only non-identifying enrollment phase/count
metadata and has no enrollment authority.

Durable embeddings remain in `~/.talking_box_speakers.json`, mode 0600, using a
flushed temporary file, atomic replacement, and directory sync. Conversational
enrollment never overwrites or merges an existing profile.

## Real-Pi validation

Before normal household use, validate clustering across representative noise
and distances, both name prompts, yes/no and ambiguous answers, timeout and
restart cleanup, forced write failure, and recognition after a consented test
enrollment. Remove the test profile afterward. No deployment or live Pi config
change is part of this repository change.
