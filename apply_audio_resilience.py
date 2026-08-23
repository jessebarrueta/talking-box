#!/usr/bin/env python3
from pathlib import Path
import shutil

path = Path("pi/talking_box.py")
if not path.exists():
    raise SystemExit("Run this from the talking-box repository root.")

text = path.read_text()
backup = path.with_name(path.name + ".bak-audio-resilience")
if not backup.exists():
    shutil.copy2(path, backup)

changed = False

if "VOLUME_DEFAULT = 55\n" in text:
    text = text.replace("VOLUME_DEFAULT = 55\n", "VOLUME_DEFAULT = 75\n", 1)
    changed = True

if "ALSA_CONFIG_SOURCE =" not in text:
    anchor = 'VOLUME_CONTROL = "TalkingBoxVolume"\n'
    addition = '''VOLUME_CONTROL = "TalkingBoxVolume"

ALSA_CONFIG_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "asoundrc"
)
ALSA_CONFIG_TARGET = Path.home() / ".asoundrc"
'''
    if anchor not in text:
        raise SystemExit("Could not find VOLUME_CONTROL anchor.")
    text = text.replace(anchor, addition, 1)
    changed = True

if "def ensure_alsa_config(" not in text:
    old = '''def run_amixer(*args, capture=False):
    return subprocess.run(
        ["amixer", "-D", VOLUME_MIXER_DEVICE, *args],
        check=True,
        text=True,
        capture_output=capture,
    )
'''
    new = '''def ensure_alsa_config(force=False):
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
            tmp.chmod(0o644)
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
'''
    if old not in text:
        raise SystemExit("Could not find run_amixer block.")
    text = text.replace(old, new, 1)
    changed = True

if "def cloud_speak(text):\n    ensure_alsa_config()" not in text:
    old = "def cloud_speak(text):\n    with requests.post(\n"
    new = "def cloud_speak(text):\n    ensure_alsa_config()\n\n    with requests.post(\n"
    if old not in text:
        raise SystemExit("Could not find cloud_speak anchor.")
    text = text.replace(old, new, 1)
    changed = True

if "def piper_speak(text):\n    ensure_alsa_config()" not in text:
    old = "def piper_speak(text):\n    with tempfile.NamedTemporaryFile(\n"
    new = "def piper_speak(text):\n    ensure_alsa_config()\n\n    with tempfile.NamedTemporaryFile(\n"
    if old not in text:
        raise SystemExit("Could not find piper_speak anchor.")
    text = text.replace(old, new, 1)
    changed = True

if changed:
    path.write_text(text)
    print("Applied 75% default volume + runtime ALSA self-healing.")
else:
    print("Audio resilience changes already present.")
