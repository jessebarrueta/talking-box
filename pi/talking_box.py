#!/usr/bin/env python3
import base64
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from gpiozero import Button

from lifecycle import calculate_sleep_duration, sleep_context_from_state

try:
    from speaker_identity import SpeakerIdentity
    from anonymous_speaker_session import AnonymousSpeakerSession
except Exception as speaker_import_error:
    SpeakerIdentity = None
    AnonymousSpeakerSession = None
else:
    speaker_import_error = None

API_BASE = "https://api.enormousbrain.com"
ENTITY_ID = "voice-box-001"
DEVICE_ID = "aiy-voice-pi4-001"
VOICE_SESSION_ID = uuid.uuid4().hex[:12]
TALKING_BOX_DEVICE_TOKEN = os.getenv(
    "TALKING_BOX_DEVICE_TOKEN",
    "",
).strip()

BUTTON_GPIO = 23
ALSA_DEVICE = "talkingbox"
CAPTURE_DEVICE = "plughw:CARD=sndrpigooglevoi"

VOLUME_MIXER_DEVICE = "talkingbox"
VOLUME_CONTROL = "TalkingBoxVolume"

VOLUME_QUIET = 20
VOLUME_DEFAULT = 75
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

SPEAKER_ID_ENABLED = os.getenv(
    "TALKING_BOX_SPEAKER_ID",
    "1",
).strip().lower() not in {
    "0", "false", "no", "off",
}

PROBABLE_SPEAKER_THRESHOLD = float(
    os.getenv("TALKING_BOX_SPEAKER_PROBABLE_THRESHOLD", "0.50")
)
PROBABLE_SPEAKER_MIN_MARGIN = float(
    os.getenv("TALKING_BOX_SPEAKER_PROBABLE_MARGIN", "0.10")
)
PROBABLE_RECENT_VERIFIED_SECONDS = float(
    os.getenv("TALKING_BOX_SPEAKER_PROBABLE_RECENT_SECONDS", "180")
)

RELATIONSHIPS_FILE = Path.home() / ".talking_box_relationships.json"

ALSA_CONFIG_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "asoundrc"
)
ALSA_CONFIG_TARGET = Path.home() / ".asoundrc"

SPEAKER_MODEL = (
    Path(__file__).resolve().parent.parent
    / "models"
    / "speaker"
    / "wespeaker_en_voxceleb_resnet34.onnx"
)

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
speaker_identity = None
anonymous_speakers = None
last_verified_speaker = None


def api_auth_headers():
    if not TALKING_BOX_DEVICE_TOKEN:
        raise RuntimeError(
            "TALKING_BOX_DEVICE_TOKEN is not configured"
        )

    return {
        "Authorization": (
            f"Bearer {TALKING_BOX_DEVICE_TOKEN}"
        )
    }


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


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

    offline = calculate_sleep_duration(
        last_shutdown_at,
        booted_at,
    )

    state.update(
        {
            "boot_count": boot_count,
            "last_boot_at": booted_at,
            # Store None explicitly when the interval is unknowable so stale
            # duration data is never presented as fact.
            "last_sleep_seconds": offline,
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
    command = normalize_command(text)

    # Politeness should not alter machine semantics.
    words = command.split()

    while words and words[0] == "please":
        words.pop(0)

    while words and words[-1] == "please":
        words.pop()

    command = " ".join(words)

    return command in {
        "shutdown",
        "shut down",
        "shut yourself down",
        "power down",
        "power yourself down",
        "go to sleep",
        "turn yourself off",
    }


def ensure_alsa_config(force=False):
    try:
        if not ALSA_CONFIG_SOURCE.is_file():
            print(
                "Canonical ALSA config is missing: "
                f"{ALSA_CONFIG_SOURCE}"
            )
            return False

        desired = ALSA_CONFIG_SOURCE.read_bytes()
        current = None

        if ALSA_CONFIG_TARGET.exists():
            try:
                current = ALSA_CONFIG_TARGET.read_bytes()
            except OSError:
                current = None

        if force or current != desired:
            tmp = ALSA_CONFIG_TARGET.with_name(".asoundrc.tmp")
            tmp.write_bytes(desired)
            os.chmod(tmp, 0o644)
            tmp.replace(ALSA_CONFIG_TARGET)
            print("Restored ALSA config from tracked canonical copy.")

        return True

    except Exception as exc:
        print(
            "Could not restore ALSA config: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def run_amixer(*args, capture=False):
    ensure_alsa_config()
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
        headers=api_auth_headers(),
        json={
            "audio_base64": data,
            "format": "wav",
            "language": "en",
        },
        timeout=HTTP_TIMEOUT,
    )

    r.raise_for_status()

    return r.json()["text"].strip()


def initialize_speaker_identity():
    global speaker_identity
    global anonymous_speakers

    if not SPEAKER_ID_ENABLED:
        print("Speaker identity disabled by configuration.")
        return

    if SpeakerIdentity is None or AnonymousSpeakerSession is None:
        print(
            "Speaker identity unavailable: "
            f"{type(speaker_import_error).__name__}: "
            f"{speaker_import_error}"
        )
        return

    if not SPEAKER_MODEL.is_file():
        print(
            "Speaker identity model not installed; "
            "run ./pi/setup-speaker-id.sh"
        )
        return

    try:
        speaker_identity = SpeakerIdentity(model_path=SPEAKER_MODEL)
        anonymous_speakers = AnonymousSpeakerSession()
        enrolled = speaker_identity.list_speakers()
        print(
            "Speaker identity ready: "
            f"{len(enrolled)} enrolled speaker(s)."
        )
        print(
            "Anonymous speaker discovery ready: "
            "session-only clustering."
        )
    except Exception as exc:
        speaker_identity = None
        anonymous_speakers = None
        print(
            "Speaker identity initialization failed: "
            f"{type(exc).__name__}: {exc}"
        )


def _known_speakers_context():
    if speaker_identity is None:
        return []

    try:
        return [
            {
                "id": row.get("id"),
                "display_name": (
                    row.get("display_name")
                    or row.get("id")
                ),
            }
            for row in speaker_identity.list_speakers()
            if row.get("id")
        ]
    except Exception as exc:
        print(
            "Could not build known-speaker context: "
            f"{type(exc).__name__}: {exc}"
        )
        return []


def _speaker_with_session_metadata(result):
    decorated = dict(result or {})
    decorated["voice_session_id"] = VOICE_SESSION_ID

    if decorated.get("status") == "anonymous":
        anonymous_id = decorated.get("anonymous_id")
        if anonymous_id:
            decorated["anonymous_key"] = (
                f"{VOICE_SESSION_ID}:{anonymous_id}"
            )

    return decorated


def identify_speaker(path):
    global last_verified_speaker

    if speaker_identity is None:
        return _speaker_with_session_metadata(
            {
                "status": "unavailable",
                "id": None,
                "display_name": None,
            }
        )

    try:
        embedding = speaker_identity.embedding_from_wav(path)
        result = speaker_identity.identify_embedding(embedding)

        if (
            result.get("status") == "recognized"
            and result.get("id")
        ):
            last_verified_speaker = {
                "id": result.get("id"),
                "display_name": (
                    result.get("display_name")
                    or result.get("id")
                ),
                "at": time.monotonic(),
            }

        elif result.get("status") == "unknown":
            candidate_id = result.get("candidate_id")
            candidate_name = (
                result.get("candidate_display_name")
                or candidate_id
            )
            similarity = float(result.get("similarity") or 0.0)
            margin = result.get("margin")
            margin_ok = (
                margin is None
                or float(margin) >= PROBABLE_SPEAKER_MIN_MARGIN
            )

            recent_match = False
            recent_age = None
            if (
                last_verified_speaker
                and candidate_id
                and last_verified_speaker.get("id") == candidate_id
            ):
                recent_age = max(
                    0.0,
                    time.monotonic()
                    - float(last_verified_speaker.get("at") or 0.0),
                )
                recent_match = (
                    recent_age <= PROBABLE_RECENT_VERIFIED_SECONDS
                )

            if (
                candidate_id
                and similarity >= PROBABLE_SPEAKER_THRESHOLD
                and margin_ok
            ):
                result = {
                    "status": "probable",
                    "id": None,
                    "display_name": None,
                    "candidate_id": candidate_id,
                    "candidate_display_name": candidate_name,
                    "similarity": similarity,
                    "best_sample_similarity": result.get(
                        "best_sample_similarity"
                    ),
                    "margin": margin,
                    "threshold": speaker_identity.threshold,
                    "identity_verified": False,
                    "needs_confirmation": True,
                    "probable_basis": (
                        "recent_verified_same_candidate"
                        if recent_match
                        else "weak_enrolled_voice_match"
                    ),
                    "recent_verified_age_seconds": recent_age,
                }

            elif anonymous_speakers is not None:
                anonymous = anonymous_speakers.observe(embedding)
                anonymous["known_best_similarity"] = result.get(
                    "similarity"
                )
                anonymous["known_threshold"] = speaker_identity.threshold
                anonymous["known_candidate_id"] = candidate_id
                anonymous["known_candidate_display_name"] = candidate_name
                anonymous["known_best_sample_similarity"] = result.get(
                    "best_sample_similarity"
                )
                result = anonymous

        result = _speaker_with_session_metadata(result)

        print(
            "Speaker identity: "
            + json.dumps(result, sort_keys=True)
        )
        return result

    except ValueError as exc:
        result = _speaker_with_session_metadata(
            {
                "status": "insufficient_audio",
                "id": None,
                "display_name": None,
                "reason": str(exc),
            }
        )
        print(
            "Speaker identity: "
            + json.dumps(result, sort_keys=True)
        )
        return result

    except Exception as exc:
        print(
            "Speaker identity failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return _speaker_with_session_metadata(
            {
                "status": "error",
                "id": None,
                "display_name": None,
            }
        )


def _relationship_context():
    try:
        if not RELATIONSHIPS_FILE.exists():
            return []

        payload = json.loads(RELATIONSHIPS_FILE.read_text())
        raw = (
            payload.get("relationships")
            if isinstance(payload, dict)
            else payload
        )
        if not isinstance(raw, list):
            return []

        result = []
        for item in raw:
            if not isinstance(item, dict):
                continue

            source = str(item.get("from") or "").strip()
            target = str(item.get("to") or "").strip()
            relation = str(item.get("type") or "").strip().lower()

            if source and target and relation:
                result.append(
                    {
                        "from": source,
                        "to": target,
                        "type": relation,
                    }
                )

        return result

    except Exception as exc:
        print(
            "Could not read relationship context: "
            f"{type(exc).__name__}: {exc}"
        )
        return []


def device_context():
    state = load_state()

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
        "voice_session_id": VOICE_SESSION_ID,
        "last_sleep": sleep_context_from_state(state),
        "known_speakers": _known_speakers_context(),
        "relationships": _relationship_context(),
    }


def interact(text, speaker=None):
    context = device_context()
    if speaker:
        context["speaker"] = speaker

    r = requests.post(
        (
            f"{API_BASE}/v1/entities/"
            f"{ENTITY_ID}/interact"
        ),
        headers=api_auth_headers(),
        json={
            "text": text,
            "device_id": DEVICE_ID,
            "context": context,
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
        headers=api_auth_headers(),
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
    ensure_alsa_config()

    with requests.post(
        f"{API_BASE}/v1/speech",
        headers=api_auth_headers(),
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
    ensure_alsa_config()

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
        "Talking Box V7.3 starting."
    )

    initialize_volume()
    initialize_speaker_identity()

    run_wake_sequence()

    print(
        "Talking Box V7.3 ready."
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

            speaker = identify_speaker(
                input_path
            )

            print(
                "Thinking..."
            )

            reply = interact(
                transcript,
                speaker,
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
