# V7 local speaker identity — first slice

This slice adds text-independent household speaker recognition to Jerry.

It intentionally does not implement anonymous-speaker clustering or conversational naming yet. First we establish reliable recognition of explicitly enrolled voices and propagate that identity into server-side interaction/memory context.

## Architecture

```text
push-to-talk WAV
    ↓
local WeSpeaker embedding (sherpa-onnx)
    ↓
cosine comparison against local enrolled profiles
    ↓
recognized / unknown / insufficient_audio
    ↓
interaction context sent to Enormous Brain
    ↓
speaker-aware memory attribution
```

Voice embeddings and enrollment profiles stay on the Raspberry Pi in:

```text
~/.talking_box_speakers.json
```

Enrollment WAV files are temporary and deleted after enrollment.

## Install on the Pi

```bash
cd ~/talking-box
./pi/setup-speaker-id.sh
```

## Enroll a voice

```bash
cd ~/talking-box
./pi/enroll-speaker.sh jesse "Jesse"
```

Repeat for other household members only with appropriate consent.

## Test directly

```bash
arecord   -D plughw:CARD=sndrpigooglevoi   -f S16_LE   -r 16000   -c 1   -t wav   -d 4   /tmp/who.wav

~/piper-venv/bin/python   pi/speaker_identity.py   identify   /tmp/who.wav
```

Expected shape:

```json
{
  "status": "recognized",
  "id": "jesse",
  "display_name": "Jesse",
  "similarity": 0.82,
  "margin": 0.19,
  "threshold": 0.65
}
```

`similarity` is cosine similarity, not a calibrated probability.

## Runtime behavior

`talking_box.py` attempts local speaker recognition for each usable push-to-talk recording.

If the model/dependency is absent, Jerry continues to work normally with speaker recognition disabled.

When recognized, the server receives speaker metadata inside `context.speaker` and uses it to label turns and preserve speaker attribution in memory metadata.

If the voice is unknown, the server is instructed not to create person-specific durable memories.

## Tuning

Defaults:

```text
TALKING_BOX_SPEAKER_THRESHOLD=0.65
TALKING_BOX_SPEAKER_MARGIN=0.08
TALKING_BOX_SPEAKER_MIN_SECONDS=0.8
```

These are starting values, not final truth. Tune them using the actual Voice HAT mic across different distances, background noise, moods, and household speakers.

## Next slice

Once recognition is stable:

1. temporary anonymous voice clusters (`speaker-a`, `speaker-b`);
2. notice a recurring unknown voice;
3. ask for a name naturally;
4. ask permission to remember the voice;
5. bind the anonymous cluster to the named speaker;
6. retroactively attribute same-session memories.

That is the slice that gets us to: “Oh, I’ve heard you before. Were you the one asking about the cat?”
