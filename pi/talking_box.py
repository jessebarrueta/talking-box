#!/usr/bin/env python3
import base64
import json
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from gpiozero import Button

API_BASE = "https://api.enormousbrain.com"
ENTITY_ID = "voice-box-001"
DEVICE_ID = "aiy-voice-pi4-001"

BUTTON_GPIO = 23
ALSA_DEVICE = "talkingbox"
CAPTURE_DEVICE = "plughw:CARD=sndrpigooglevoi"

VOLUME_MIXER_DEVICE = "talkingbox"
VOLUME_CONTROL = "TalkingBoxVolume"

VOLUME_QUIET = 20
VOLUME_DEFAULT = 55
VOLUME_LOUD = 75
VOLUME_EXTREME = 100

VOLUME_LEVELS = [
    VOLUME_QUIET,
    VOLUME_DEFAULT,
    VOLUME_LOUD,
    VOLUME_EXTREME,
]
RECORD_RATE = 16000
MAX_RECORD_SECONDS = 45

HTTP_TIMEOUT = 75
SPEECH_CONNECT_TIMEOUT = 6
SPEECH_READ_TIMEOUT = 20

PIPER = str(
    Path.home()
    / "piper-venv"
    / "bin"
    / "piper"
)

PIPER_MODEL = str(
    Path.home()
    / "piper-voices"
    / "en_US-lessac-medium.onnx"
)

STATE_FILE = (
    Path.home()
    / ".talking_box_state.json"
)

STARTUP_API_RETRIES = 12
STARTUP_API_RETRY_SECONDS = 5

SHUTDOWN_COMMAND = [
    "sudo",
    "/usr/sbin/shutdown",
    "-h",
    "now",
]

button = Button(
    BUTTON_GPIO,
    pull_up=True,
    bounce_time=0.03,
)

last_spoken_text = None


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def parse_dt(value):
    try:
        if not value:
            return None

        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )

    except ValueError:
        return None


def load_state():
    try:
        if STATE_FILE.exists():
            return json.loads(
                STATE_FILE.read_text()
            )

        return {}

    except Exception as exc:
        print(
            f"Could not read local state: {exc}"
        )

        return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(
        ".tmp"
    )

    tmp.write_text(
        json.dumps(
            state,
            indent=2,
        )
    )

    tmp.replace(
        STATE_FILE
    )


def begin_boot_session():
    state = load_state()
    booted_at = utc_now()

    boot_count = (
        int(
            state.get(
                "boot_count",
                0,
            )
        )
        + 1
    )

    last_shutdown_at = state.get(
        "last_shutdown_at"
    )

    offline = None

    a = parse_dt(
        last_shutdown_at
    )

    b = parse_dt(
        booted_at
    )

    if a and b:
        offline = max(
            0.0,
            (b - a).total_seconds(),
        )

    state.update(
        {
            "boot_count": boot_count,
            "last_boot_at": booted_at,
        }
    )

    save_state(
        state
    )

    return {
        "boot_count": boot_count,
        "booted_at": booted_at,
        "last_shutdown_at": (
            last_shutdown_at
        ),
        "offline_seconds": offline,
    }


def remember_shutdown():
    state = load_state()

    state["last_shutdown_at"] = utc_now()

    save_state(
        state
    )


def normalize_command(text):
    return re.sub(
        r"\s+",
        " ",
        re.sub(
            r"[^a-z0-9 ]+",
            "",
            text.lower(),
        ),
    ).strip()


def is_shutdown_request(text):
    return normalize_command(text) in {
        "shutdown",
        "shut down",
        "please shut down",
        "please shutdown",
        "shut yourself down",
        "please shut yourself down",
        "power down",
        "please power down",
        "power yourself down",
        "please power yourself down",
        "go to sleep",
        "please go to sleep",
        "turn yourself off",
        "please turn yourself off",
    }


def run_amixer(*args, capture=False):
    return subprocess.run(
        ["amixer", "-D", VOLUME_MIXER_DEVICE, *args],
        check=True,
        text=True,
        capture_output=capture,
    )


def set_volume(percent):
    percent = max(0, min(100, int(percent)))
    run_amixer("sset", VOLUME_CONTROL, f"{percent}%")
    print(f"Volume set to {percent}%.")
    return percent


def get_volume():
    try:
        result = run_amixer("sget", VOLUME_CONTROL, capture=True)
        matches = re.findall(r"\[(\d+)%\]", result.stdout)
        if matches:
            return int(matches[-1])
    except Exception as exc:
        print(f"Could not read volume: {type(exc).__name__}: {exc}")
    return VOLUME_DEFAULT


def step_volume(direction):
    current = get_volume()

    if direction > 0:
        for level in VOLUME_LEVELS:
            if level > current + 1:
                return set_volume(level)
        return set_volume(VOLUME_EXTREME)

    for level in reversed(VOLUME_LEVELS):
        if level < current - 1:
            return set_volume(level)
    return set_volume(VOLUME_QUIET)


def classify_local_audio_command(text):
    command = normalize_command(text)

    if command in {
        "can you repeat that", "repeat that", "say that again",
        "say it again", "can you say that again",
    }:
        return ("repeat", None)

    if command in {
        "i cant hear you", "i cannot hear you", "cant hear you",
        "what", "whaat", "whaaat", "what did you say",
        "i didnt hear you", "i did not hear you",
    }:
        return ("louder_repeat", None)

    if command in {
        "too loud", "thats too loud", "that is too loud",
        "please be quiet", "be quiet",
    }:
        return ("set", VOLUME_QUIET)

    if command in {
        "turn your volume down", "turn the volume down",
        "turn yourself down", "volume down", "shh", "shhh", "shhhh",
    }:
        return ("quieter", None)

    if command in {
        "turn yourself up", "turn your volume up", "turn up the volume",
        "turn the volume up", "volume up", "speak up",
    }:
        return ("louder", None)

    if command in {
        "maximum volume", "max volume", "full volume",
        "turn it all the way up", "turn yourself all the way up",
    }:
        return ("set", VOLUME_EXTREME)

    if command in {
        "normal volume", "comfortable volume",
        "set normal volume", "set comfortable volume",
    }:
        return ("set", VOLUME_DEFAULT)

    match = re.fullmatch(
        r"(?:set )?(?:your )?volume(?: to)? (\d{1,3})(?: percent)?",
        command,
    )
    if match:
        return ("set", int(match.group(1)))

    return (None, None)


def record_until_release(path):
    proc = subprocess.Popen(
        [
            "arecord",
            "-q",
            "-D",
            CAPTURE_DEVICE,
            "-f",
            "S16_LE",
            "-r",
            str(RECORD_RATE),
            "-c",
            "1",
            "-t",
            "wav",
            path,
        ]
    )

    started = time.monotonic()

    try:
        while button.is_pressed:
            if (
                time.monotonic() - started
                >= MAX_RECORD_SECONDS
            ):
                break

            time.sleep(
                0.03
            )

    finally:
        proc.terminate()

        try:
            proc.wait(
                timeout=2
            )

        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def transcribe(path):
    data = base64.b64encode(
        Path(path).read_bytes()
    ).decode(
        "ascii"
    )

    r = requests.post(
        f"{API_BASE}/v1/transcribe",
        json={
            "audio_base64": data,
            "format": "wav",
            "language": "en",
        },
        timeout=HTTP_TIMEOUT,
    )

    r.raise_for_status()

    return r.json()["text"].strip()


def device_context():
    return {
        "embodiment": (
            "Google AIY Voice Kit "
            "on Raspberry Pi 4"
        ),
        "input": (
            "push-to-talk yellow button"
        ),
        "microphone": (
            "AIY Voice HAT microphone"
        ),
        "speaker": (
            "AIY Voice HAT speaker"
        ),
        "vision": False,
        "mobility": False,
    }


def interact(text):
    r = requests.post(
        (
            f"{API_BASE}/v1/entities/"
            f"{ENTITY_ID}/interact"
        ),
        json={
            "text": text,
            "device_id": DEVICE_ID,
            "context": device_context(),
        },
        timeout=HTTP_TIMEOUT,
    )

    r.raise_for_status()

    return r.json()["text"].strip()


def wake_greeting(info):
    r = requests.post(
        (
            f"{API_BASE}/v1/entities/"
            f"{ENTITY_ID}/wake"
        ),
        json={
            "device_id": DEVICE_ID,
            **info,
            "context": device_context(),
        },
        timeout=HTTP_TIMEOUT,
    )

    r.raise_for_status()

    return r.json()["text"].strip()


def cloud_speak(text):
    with requests.post(
        f"{API_BASE}/v1/speech",
        json={
            "text": text,
        },
        stream=True,
        timeout=(
            SPEECH_CONNECT_TIMEOUT,
            SPEECH_READ_TIMEOUT,
        ),
    ) as r:
        r.raise_for_status()

        provider = r.headers.get(
            "X-TTS-Provider",
            "unknown",
        )

        transport = r.headers.get(
            "X-TTS-Transport",
            "unknown",
        )

        model = r.headers.get(
            "X-TTS-Model",
            "unknown",
        )

        print(
            "Cloud TTS: "
            f"{provider} / {model} "
            f"via {transport}"
        )

        player = subprocess.Popen(
            [
                "mpg123",
                "-q",
                "-o",
                "alsa",
                "-a",
                ALSA_DEVICE,
                "-",
            ],
            stdin=subprocess.PIPE,
        )

        started_audio = False

        try:
            for chunk in r.iter_content(
                chunk_size=4096
            ):
                if not chunk:
                    continue

                started_audio = True

                player.stdin.write(
                    chunk
                )

                player.stdin.flush()

            player.stdin.close()

            # Do not kill a valid spoken sentence after an arbitrary
            # five seconds. Once the stream is complete, allow mpg123
            # to finish playing the buffered audio naturally.
            return_code = player.wait()

            if return_code != 0:
                raise RuntimeError(
                    "mpg123 exited with "
                    f"status {return_code}"
                )

            if not started_audio:
                raise RuntimeError(
                    "cloud TTS returned no audio"
                )

        except Exception:
            if player.stdin:
                try:
                    player.stdin.close()
                except Exception:
                    pass

            if player.poll() is None:
                player.terminate()

                try:
                    player.wait(
                        timeout=2
                    )

                except subprocess.TimeoutExpired:
                    player.kill()
                    player.wait()

            raise


def piper_speak(text):
    with tempfile.NamedTemporaryFile(
        suffix=".wav",
        delete=False,
    ) as tmp:
        path = tmp.name

    try:
        subprocess.run(
            [
                PIPER,
                "--model",
                PIPER_MODEL,
                "--output_file",
                path,
            ],
            input=text.encode(),
            check=True,
        )

        subprocess.run(
            [
                "aplay",
                "-q",
                "-D",
                ALSA_DEVICE,
                path,
            ],
            check=True,
        )

    finally:
        Path(path).unlink(
            missing_ok=True
        )


def speak(text, remember=True):
    global last_spoken_text

    if remember:
        last_spoken_text = text

    try:
        cloud_speak(text)

    except Exception as exc:
        print(
            "Cloud TTS failed "
            f"({type(exc).__name__}: {exc}); "
            "using Piper fallback."
        )
        piper_speak(text)


def repeat_last_spoken():
    if not last_spoken_text:
        speak("I don't have anything to repeat yet.", remember=False)
        return

    print("Repeating previous response.")
    speak(last_spoken_text, remember=False)


def handle_local_audio_command(text):
    action, value = classify_local_audio_command(text)

    if not action:
        return False

    print(
        "Local audio command: "
        f"{action}"
        + (f" ({value}%)" if value is not None else "")
    )

    if action == "repeat":
        repeat_last_spoken()
    elif action == "louder_repeat":
        step_volume(1)
        repeat_last_spoken()
    elif action == "quieter":
        step_volume(-1)
    elif action == "louder":
        step_volume(1)
    elif action == "set":
        set_volume(value)

    return True


def initialize_volume():
    try:
        set_volume(VOLUME_DEFAULT)
    except Exception as exc:
        print(
            "Could not set default volume: "
            f"{type(exc).__name__}: {exc}"
        )


def wait_for_api():
    for attempt in range(
        1,
        STARTUP_API_RETRIES + 1,
    ):
        try:
            r = requests.get(
                f"{API_BASE}/health",
                timeout=10,
            )

            r.raise_for_status()

            print(
                "Enormous Brain API is online."
            )

            return True

        except requests.RequestException as exc:
            print(
                "Waiting for Enormous Brain API "
                f"({attempt}/"
                f"{STARTUP_API_RETRIES}): "
                f"{type(exc).__name__}"
            )

            if (
                attempt
                < STARTUP_API_RETRIES
            ):
                time.sleep(
                    STARTUP_API_RETRY_SECONDS
                )

    return False


def run_wake_sequence():
    info = begin_boot_session()

    print(
        f"Boot session: {info}"
    )

    if not wait_for_api():
        try:
            piper_speak(
                "I'm awake, but the rest "
                "of my brain seems to be "
                "somewhere else."
            )

        except Exception as exc:
            print(
                "Local wake fallback failed: "
                f"{exc}"
            )

        return

    try:
        greeting = wake_greeting(
            info
        )

        print(
            f"Wake greeting: {greeting}"
        )

        speak(
            greeting
        )

    except Exception as exc:
        print(
            f"Wake sequence failed: {exc}"
        )

        try:
            speak(
                "Oh. I'm back."
            )

        except Exception as fallback:
            print(
                "Wake fallback failed: "
                f"{fallback}"
            )


def shutdown_box():
    print(
        "Shutdown requested."
    )

    try:
        speak(
            "All right. Going to sleep."
        )

    except Exception as exc:
        print(
            "Could not speak "
            f"shutdown message: {exc}"
        )

    remember_shutdown()

    time.sleep(
        0.5
    )

    subprocess.run(
        SHUTDOWN_COMMAND,
        check=True,
    )


def main():
    print(
        "Talking Box V5.2 starting."
    )

    initialize_volume()

    run_wake_sequence()

    print(
        "Talking Box V5.2 ready."
    )

    print(
        "Hold the yellow button to talk. "
        "Release when finished."
    )

    while True:
        button.wait_for_press()

        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False,
        ) as tmp:
            input_path = tmp.name

        print(
            "Listening..."
        )

        try:
            record_until_release(
                input_path
            )

            if (
                Path(input_path).stat().st_size
                < 1000
            ):
                print(
                    "Recording too short; ignored."
                )

                continue

            print(
                "Transcribing..."
            )

            transcript = transcribe(
                input_path
            )

            print(
                f"You: {transcript}"
            )

            if not transcript:
                continue

            if is_shutdown_request(
                transcript
            ):
                shutdown_box()
                return

            if handle_local_audio_command(
                transcript
            ):
                continue

            print(
                "Thinking..."
            )

            reply = interact(
                transcript
            )

            print(
                f"{ENTITY_ID}: {reply}"
            )

            print(
                "Speaking..."
            )

            speak(
                reply
            )

        except requests.HTTPError as exc:
            print(
                "API error: "
                f"{exc} "
                f"{getattr(exc.response, 'text', '')}"
            )

        except Exception as exc:
            print(
                "Error: "
                f"{type(exc).__name__}: {exc}"
            )

        finally:
            Path(input_path).unlink(
                missing_ok=True
            )

        time.sleep(
            0.15
        )


if __name__ == "__main__":
    main()
