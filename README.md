# Enormous Brain Talking Box V3

Complete current files for the Google AIY Talking Box.

## V3 additions

- starts automatically at boot with systemd
- runs directly from the cloned Git repository on the Pi
- one-command repo update + systemd restart
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

Verify:

```bash
curl https://api.enormousbrain.com/health
```

Expect server version `0.3.0`.

## Pi: first-time install from GitHub

Clone the repository into the path expected by the systemd service:

```bash
cd /home/jesse
git clone https://github.com/jessebarrueta/talking-box.git
cd talking-box
```

The service runs the client directly from:

```text
/home/jesse/talking-box/pi/talking_box.py
```

There is no longer a second copied `/home/jesse/talking_box.py` to drift out of sync.

Install the service:

```bash
chmod +x pi/install_service.sh
./pi/install_service.sh
```

Check status:

```bash
systemctl status talking-box.service
```

Watch logs:

```bash
journalctl -u talking-box.service -f
```

## Updating the Pi from the repo

After pushing changes to GitHub, either run:

```bash
cd /home/jesse/talking-box
git pull --ff-only
sudo systemctl restart talking-box.service
```

or use the included helper:

```bash
cd /home/jesse/talking-box
chmod +x pi/update.sh
./pi/update.sh
```

`pi/update.sh` pulls the latest code, refreshes the installed systemd and sudoers files, reloads systemd, restarts the service, and prints status.

Because systemd points directly at the cloned repo, normal Python changes require no copy step.

## Useful service commands

Restart:

```bash
sudo systemctl restart talking-box.service
```

Stop:

```bash
sudo systemctl stop talking-box.service
```

Start:

```bash
sudo systemctl start talking-box.service
```

Status:

```bash
systemctl status talking-box.service
```

Logs:

```bash
journalctl -u talking-box.service -f
```

Disable automatic startup:

```bash
sudo systemctl disable talking-box.service
```

Re-enable automatic startup:

```bash
sudo systemctl enable talking-box.service
```

## Voice shutdown

Recognized local phrases include:

- please shut yourself down
- shut yourself down
- please shut down
- power down
- go to sleep
- turn yourself off

The LLM never gets shell access. The local script recognizes only a fixed set of phrases, and the sudoers rule permits only:

```text
/usr/sbin/shutdown -h now
```

## Boot behavior

```text
power applied
  ↓
Debian boots
  ↓
network/audio become available
  ↓
talking-box.service starts
  ↓
/home/jesse/talking-box/pi/talking_box.py
  ↓
yellow button waits for speech
```
