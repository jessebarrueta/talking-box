#!/usr/bin/env python3
from pathlib import Path

path = Path("pi/talking_box.py")
text = path.read_text()

text = text.replace(
    'ALSA_DEVICE = "plughw:CARD=sndrpigooglevoi"\n',
    '''ALSA_DEVICE = "talkingbox"\nCAPTURE_DEVICE = "plughw:CARD=sndrpigooglevoi"\nVOLUME_MIXER_DEVICE = "talkingbox"\nVOLUME_CONTROL = "TalkingBoxVolume"\n\nVOLUME_QUIET = 20\nVOLUME_DEFAULT = 55\nVOLUME_LOUD = 75\nVOLUME_EXTREME = 100\nVOLUME_LEVELS = [VOLUME_QUIET, VOLUME_DEFAULT, VOLUME_LOUD, VOLUME_EXTREME]\n'''
)

text = text.replace(
    'button = Button(\n    BUTTON_GPIO,\n    pull_up=True,\n    bounce_time=0.03,\n)\n',
    'button = Button(\n    BUTTON_GPIO,\n    pull_up=True,\n    bounce_time=0.03,\n)\n\nlast_spoken_text = None\n'
)

marker = '''def record_until_release(path):\n'''
volume_code = r'''def run_amixer(*args, capture=False):
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


'''

if marker not in text:
    raise SystemExit("Could not find record_until_release() insertion point")
text = text.replace(marker, volume_code + marker, 1)

text = text.replace(
    '            ALSA_DEVICE,\n            "-f",',
    '            CAPTURE_DEVICE,\n            "-f",',
    1,
)

old_speak = '''def speak(text):\n    try:\n        cloud_speak(\n            text\n        )\n\n    except Exception as exc:\n        print(\n            "Cloud TTS failed "\n            f"({type(exc).__name__}: {exc}); "\n            "using Piper fallback."\n        )\n\n        piper_speak(\n            text\n        )\n\n\n'''

new_speak = '''def speak(text, remember=True):\n    global last_spoken_text\n\n    if remember:\n        last_spoken_text = text\n\n    try:\n        cloud_speak(text)\n\n    except Exception as exc:\n        print(\n            "Cloud TTS failed "\n            f"({type(exc).__name__}: {exc}); "\n            "using Piper fallback."\n        )\n        piper_speak(text)\n\n\ndef repeat_last_spoken():\n    if not last_spoken_text:\n        speak("I don't have anything to repeat yet.", remember=False)\n        return\n\n    print("Repeating previous response.")\n    speak(last_spoken_text, remember=False)\n\n\ndef handle_local_audio_command(text):\n    action, value = classify_local_audio_command(text)\n\n    if not action:\n        return False\n\n    print(\n        "Local audio command: "\n        f"{action}"\n        + (f" ({value}%)" if value is not None else "")\n    )\n\n    if action == "repeat":\n        repeat_last_spoken()\n    elif action == "louder_repeat":\n        step_volume(1)\n        repeat_last_spoken()\n    elif action == "quieter":\n        step_volume(-1)\n    elif action == "louder":\n        step_volume(1)\n    elif action == "set":\n        set_volume(value)\n\n    return True\n\n\n'''

if old_speak not in text:
    raise SystemExit("Could not find speak() block")
text = text.replace(old_speak, new_speak, 1)

text = text.replace(
    'def wait_for_api():\n',
    '''def initialize_volume():\n    try:\n        set_volume(VOLUME_DEFAULT)\n    except Exception as exc:\n        print(\n            "Could not set default volume: "\n            f"{type(exc).__name__}: {exc}"\n        )\n\n\ndef wait_for_api():\n''',
    1,
)

text = text.replace(
    '"Talking Box V5.1 starting."',
    '"Talking Box V5.2 starting."',
)
text = text.replace(
    '"Talking Box V5.1 ready."',
    '"Talking Box V5.2 ready."',
)
text = text.replace(
    '    run_wake_sequence()\n',
    '    initialize_volume()\n\n    run_wake_sequence()\n',
    1,
)

needle = '''            if is_shutdown_request(\n                transcript\n            ):\n                shutdown_box()\n                return\n\n            print(\n                "Thinking..."\n            )\n'''
replacement = '''            if is_shutdown_request(\n                transcript\n            ):\n                shutdown_box()\n                return\n\n            if handle_local_audio_command(\n                transcript\n            ):\n                continue\n\n            print(\n                "Thinking..."\n            )\n'''

if needle not in text:
    raise SystemExit("Could not find main-loop insertion point")
text = text.replace(needle, replacement, 1)

path.write_text(text)
print("Updated pi/talking_box.py to V5.2 volume controls")
