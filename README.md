# Enormous Brain Talking Box V5

V5 makes ElevenLabs Eleven v3 Conversational the primary cloud voice and streams MP3 audio to the Pi while it is generated.

## V5 additions

- ElevenLabs Text-to-Dialogue WebSocket
- `eleven_v3_conversational`
- server-to-Pi streaming audio
- Pi pipes MP3 chunks directly into `mpg123`
- OpenRouter/Kokoro remains a short-timeout cloud fallback
- Piper remains the final local fallback
- server version `0.5.0`

The speech path is:

```text
entity reply
  ↓
ElevenLabs v3 conversational WebSocket
  ↓
MP3 chunks
  ↓
HTTP StreamingResponse
  ↓
Pi → mpg123 → AIY speaker

ElevenLabs first-audio timeout
  ↓
OpenRouter/Kokoro short fallback
  ↓
Piper local fallback
```

## ElevenLabs setup

Add these environment variables to the cPanel Python app:

```text
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

Optional:

```text
ELEVENLABS_MODEL_ID=eleven_v3_conversational
ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
ELEVENLABS_FIRST_AUDIO_TIMEOUT=8
OPENROUTER_TTS_TIMEOUT=8
```

If `ELEVENLABS_VOICE_ID` is omitted, V5 uses the ElevenLabs documentation example voice ID `21m00Tcm4TlvDq8ikWAM`. For the actual entity, set the voice you want explicitly.

The ElevenLabs API key stays on the server. Do not put it on the Pi.

## Server deployment

Update:

```text
server/main.py
server/requirements.txt
```

Install the new dependency in the cPanel Python application's virtualenv:

```bash
pip install -r requirements.txt
```

Then restart the cPanel Python application.

Verify:

```bash
curl https://api.enormousbrain.com/health
```

Expected version:

```text
0.5.0
```

and, when the ElevenLabs key is configured:

```text
"tts_primary":"elevenlabs"
```

## Mac speech test

```bash
curl -sS -N -D /tmp/talking-box-headers.txt \
  -X POST https://api.enormousbrain.com/v1/speech \
  -H 'Content-Type: application/json' \
  -d '{"text":"Oh. This is significantly more responsive."}' \
  -o /tmp/talking-box-test.mp3

cat /tmp/talking-box-headers.txt
file /tmp/talking-box-test.mp3
afplay /tmp/talking-box-test.mp3
```

A successful ElevenLabs response should include:

```text
X-TTS-Provider: elevenlabs
X-TTS-Model: eleven_v3_conversational
```

## Pi deployment

After pushing V5 to GitHub:

```bash
cd /home/jesse/talking-box
./pi/update.sh
```

Then:

```bash
journalctl -u talking-box.service -f
```

A successful spoken response should log:

```text
Cloud TTS: elevenlabs / eleven_v3_conversational
```

The wake-up sequence uses this same speech path, so the startup greeting should also use ElevenLabs when available.

## Notes on ElevenLabs v3 streaming

The server uses ElevenLabs' Text-to-Dialogue WebSocket endpoint:

```text
wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input
```

with one registered voice for `eleven_v3_conversational`.

The request is flushed with `close_socket` after one entity turn, while audio chunks are streamed back as they are generated.

## Fallback behavior

ElevenLabs gets a short first-audio deadline. If it cannot begin speaking quickly, the server tries OpenRouter/Kokoro briefly. If cloud speech still fails, the Pi falls back to Piper.

This prevents a slow provider from freezing the entity for roughly a minute.
