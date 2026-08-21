#!/usr/bin/env python3
import base64
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests
from gpiozero import Button

API_BASE = "https://api.enormousbrain.com"
ENTITY_ID = "voice-box-001"
DEVICE_ID = "aiy-voice-pi4-001"
BUTTON_GPIO = 23
ALSA_DEVICE = "plughw:CARD=sndrpigooglevoi"
PIPER = str(Path.home()/"piper-venv"/"bin"/"piper")
PIPER_MODEL = str(Path.home()/"piper-voices"/"en_US-lessac-medium.onnx")
RECORD_RATE = 16000
MAX_RECORD_SECONDS = 45
HTTP_TIMEOUT = 75
SHUTDOWN_COMMAND = ["sudo", "/usr/sbin/shutdown", "-h", "now"]
button = Button(BUTTON_GPIO, pull_up=True, bounce_time=0.03)

def normalize_command(text):
    text = re.sub(r"[^a-z0-9 ]+", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()

def is_shutdown_request(text):
    return normalize_command(text) in {
        "shutdown", "shut down", "please shut down", "please shutdown",
        "shut yourself down", "please shut yourself down",
        "power down", "please power down", "power yourself down",
        "please power yourself down", "go to sleep", "please go to sleep",
        "turn yourself off", "please turn yourself off",
    }

def record_until_release(wav_path):
    proc = subprocess.Popen(["arecord", "-q", "-D", ALSA_DEVICE, "-f", "S16_LE", "-r", str(RECORD_RATE), "-c", "1", "-t", "wav", wav_path])
    started = time.monotonic()
    try:
        while button.is_pressed:
            if time.monotonic() - started >= MAX_RECORD_SECONDS: break
            time.sleep(0.03)
    finally:
        proc.terminate()
        try: proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()

def transcribe(wav_path):
    audio_b64 = base64.b64encode(Path(wav_path).read_bytes()).decode("ascii")
    r = requests.post(f"{API_BASE}/v1/transcribe", json={"audio_base64": audio_b64, "format": "wav", "language": "en"}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()["text"].strip()

def interact(text):
    r = requests.post(f"{API_BASE}/v1/entities/{ENTITY_ID}/interact", json={"text": text, "device_id": DEVICE_ID, "context": {"embodiment": "Google AIY Voice Kit on Raspberry Pi 4", "input": "push-to-talk yellow button", "microphone": "AIY Voice HAT microphone", "speaker": "AIY Voice HAT speaker", "vision": False, "mobility": False}}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()["text"].strip()

def cloud_speak(text):
    r = requests.post(f"{API_BASE}/v1/speech", json={"text": text}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = tmp.name
        tmp.write(r.content)
    try:
        subprocess.run(["mpg123", "-q", "-o", "alsa", "-a", ALSA_DEVICE, path], check=True)
    finally:
        Path(path).unlink(missing_ok=True)

def piper_speak(text):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = tmp.name
    try:
        subprocess.run([PIPER, "--model", PIPER_MODEL, "--output_file", path], input=text.encode(), check=True)
        subprocess.run(["aplay", "-q", "-D", ALSA_DEVICE, path], check=True)
    finally:
        Path(path).unlink(missing_ok=True)

def speak(text):
    try: cloud_speak(text)
    except Exception as exc:
        print(f"Cloud TTS failed ({type(exc).__name__}: {exc}); using Piper fallback.")
        piper_speak(text)

def shutdown_box():
    print("Shutdown requested.")
    try: speak("All right. Going to sleep.")
    except Exception as exc: print(f"Could not speak shutdown message: {type(exc).__name__}: {exc}")
    time.sleep(0.5)
    subprocess.run(SHUTDOWN_COMMAND, check=True)

def main():
    print("Talking Box V3 ready.")
    print("Hold the yellow button to talk. Release when finished.")
    print("Say 'please shut yourself down' to power off safely.")
    while True:
        button.wait_for_press()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            input_path = tmp.name
        print("Listening...")
        try:
            record_until_release(input_path)
            if Path(input_path).stat().st_size < 1000:
                print("Recording too short; ignored."); continue
            print("Transcribing...")
            transcript = transcribe(input_path)
            print(f"You: {transcript}")
            if not transcript: continue
            if is_shutdown_request(transcript):
                shutdown_box(); return
            print("Thinking...")
            reply = interact(transcript)
            print(f"{ENTITY_ID}: {reply}")
            print("Speaking...")
            speak(reply)
        except requests.HTTPError as exc:
            print(f"API error: {exc} {getattr(exc.response, 'text', '')}")
        except Exception as exc:
            print(f"Error: {type(exc).__name__}: {exc}")
        finally:
            Path(input_path).unlink(missing_ok=True)
        time.sleep(0.15)

if __name__ == "__main__":
    main()
