# Enormous Brain Talking Box V3

Complete current files for the Google AIY Talking Box.

## V3 additions
- starts automatically at boot with systemd
- local voice shutdown intent
- shutdown says "All right. Going to sleep." and powers off safely
- cloud TTS uses OpenRouter MP3 + mpg123
- Piper remains a fallback
- server version 0.3.0

## Server deployment
Replace the files in `/home/euxpjcskx500/enormousbrain-api/` with the files under `server/`, then restart the cPanel Python app.

Required environment variables:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `OPENROUTER_API_KEY`

Optional:
- `OPENROUTER_MODEL=openai/gpt-4.1-mini`
- `OPENROUTER_TRANSCRIPTION_MODEL=openai/whisper-1`
- `OPENROUTER_TTS_MODEL=hexgrad/kokoro-82m`
- `OPENROUTER_TTS_VOICE=alloy`

Verify with `curl https://api.enormousbrain.com/health` and expect version `0.3.0`.

## Pi deployment
Copy `pi/talking_box.py` to `/home/jesse/talking_box.py`.

Then from the extracted project directory on the Pi:

```bash
chmod +x pi/install_service.sh
./pi/install_service.sh
```

Check status:

```bash
systemctl status talking-box.service
journalctl -u talking-box.service -f
```

## Voice shutdown
Recognized local phrases include:
- please shut yourself down
- shut yourself down
- please shut down
- power down
- go to sleep
- turn yourself off

The LLM never gets shell access. The local script recognizes only a small fixed set of phrases, and the sudoers rule permits only `/usr/sbin/shutdown -h now`.
