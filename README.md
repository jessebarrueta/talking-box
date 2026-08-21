# Enormous Brain Talking Box V5.1

V5.1 replaces the ElevenLabs WebSocket transport with ElevenLabs' **HTTP streaming Text-to-Dialogue endpoint**.

## Why

The full reply text is already available before TTS starts, so a WebSocket is unnecessary. ElevenLabs explicitly supports HTTP streaming dialogue for this use case.

The speech path is now:

```text
entity reply text
    ↓
POST ElevenLabs /v1/text-to-dialogue/stream
    ↓
streaming MP3 response
    ↓
Enormous Brain StreamingResponse
    ↓
Pi streams chunks into mpg123
    ↓
AIY speaker
```

## V5.1 changes

- ElevenLabs HTTP streaming instead of WebSocket
- `POST https://api.elevenlabs.io/v1/text-to-dialogue/stream`
- server streams ElevenLabs audio directly to the Pi
- OpenRouter/Kokoro remains a short-timeout cloud fallback
- Piper remains the final local fallback
- removed `websockets` Python dependency
- removed the Pi's incorrect 5-second `mpg123` playback timeout
- provider/transport are printed in Pi logs
- server version `0.5.1`

## ElevenLabs environment

Required:

```text
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

Recommended:

```text
ELEVENLABS_MODEL_ID=eleven_v3
ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
ELEVENLABS_FIRST_AUDIO_TIMEOUT=8
```

The HTTP Text-to-Dialogue API defaults to `eleven_v3`. If your ElevenLabs workspace specifically supports another v3 model ID, it can still be supplied with `ELEVENLABS_MODEL_ID`.

## Server deployment

Replace:

```text
server/main.py
server/requirements.txt
```

in the GoDaddy Python application.

Then update dependencies:

```bash
pip install -r requirements.txt
```

Restart the cPanel Python application.

Verify:

```bash
curl https://api.enormousbrain.com/health
```

Expected:

```json
{
  "version": "0.5.1",
  "tts_primary": "elevenlabs-http-stream"
}
```

## Test the API from your Mac

```bash
curl -sS -N -D /tmp/talking-box-headers.txt \
  -X POST https://api.enormousbrain.com/v1/speech \
  -H 'Content-Type: application/json' \
  -d '{"text":"Oh. HTTP streaming. Much less dramatic."}' \
  -o /tmp/talking-box-test.mp3
```

Inspect:

```bash
cat /tmp/talking-box-headers.txt
file /tmp/talking-box-test.mp3
afplay /tmp/talking-box-test.mp3
```

A successful ElevenLabs response should include:

```text
X-TTS-Provider: elevenlabs
X-TTS-Transport: http-stream
X-TTS-Model: eleven_v3
```

## Pi deployment

After pushing the Pi file:

```bash
cd /home/jesse/talking-box
./pi/update.sh
```

Then:

```bash
journalctl -u talking-box.service -f
```

A healthy response should look like:

```text
Cloud TTS: elevenlabs / eleven_v3 via http-stream
```

## Playback timeout fix

V5 had:

```python
player.wait(timeout=5)
```

That could incorrectly kill `mpg123` simply because a sentence took more than five seconds to finish playing.

V5.1 lets `mpg123` naturally finish the already-received audio:

```python
player.wait()
```

The network itself still has explicit connection/read timeouts, so this does not reintroduce the old minute-long cloud stall.
