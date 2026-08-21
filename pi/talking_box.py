#!/usr/bin/env python3
import base64, json, re, subprocess, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
import requests
from gpiozero import Button

API_BASE='https://api.enormousbrain.com'; ENTITY_ID='voice-box-001'; DEVICE_ID='aiy-voice-pi4-001'
BUTTON_GPIO=23; ALSA_DEVICE='plughw:CARD=sndrpigooglevoi'; RECORD_RATE=16000; MAX_RECORD_SECONDS=45; HTTP_TIMEOUT=75
PIPER=str(Path.home()/'piper-venv'/'bin'/'piper'); PIPER_MODEL=str(Path.home()/'piper-voices'/'en_US-lessac-medium.onnx')
STATE_FILE=Path.home()/'.talking_box_state.json'; STARTUP_API_RETRIES=12; STARTUP_API_RETRY_SECONDS=5
SHUTDOWN_COMMAND=['sudo','/usr/sbin/shutdown','-h','now']
button=Button(BUTTON_GPIO,pull_up=True,bounce_time=0.03)

def utc_now(): return datetime.now(timezone.utc).isoformat()
def parse_dt(v):
    try: return datetime.fromisoformat(v.replace('Z','+00:00')) if v else None
    except ValueError: return None
def load_state():
    try: return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except Exception as exc: print(f'Could not read local state: {exc}'); return {}
def save_state(state):
    tmp=STATE_FILE.with_suffix('.tmp'); tmp.write_text(json.dumps(state,indent=2)); tmp.replace(STATE_FILE)
def begin_boot_session():
    state=load_state(); booted_at=utc_now(); boot_count=int(state.get('boot_count',0))+1; last_shutdown_at=state.get('last_shutdown_at'); offline=None
    a,b=parse_dt(last_shutdown_at),parse_dt(booted_at)
    if a and b: offline=max(0.0,(b-a).total_seconds())
    state.update({'boot_count':boot_count,'last_boot_at':booted_at}); save_state(state)
    return {'boot_count':boot_count,'booted_at':booted_at,'last_shutdown_at':last_shutdown_at,'offline_seconds':offline}
def remember_shutdown():
    state=load_state(); state['last_shutdown_at']=utc_now(); save_state(state)
def normalize_command(text): return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 ]+','',text.lower())).strip()
def is_shutdown_request(text):
    return normalize_command(text) in {'shutdown','shut down','please shut down','please shutdown','shut yourself down','please shut yourself down','power down','please power down','power yourself down','please power yourself down','go to sleep','please go to sleep','turn yourself off','please turn yourself off'}

def record_until_release(path):
    proc=subprocess.Popen(['arecord','-q','-D',ALSA_DEVICE,'-f','S16_LE','-r',str(RECORD_RATE),'-c','1','-t','wav',path]); started=time.monotonic()
    try:
        while button.is_pressed:
            if time.monotonic()-started>=MAX_RECORD_SECONDS: break
            time.sleep(.03)
    finally:
        proc.terminate()
        try: proc.wait(timeout=2)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait()
def transcribe(path):
    data=base64.b64encode(Path(path).read_bytes()).decode('ascii'); r=requests.post(f'{API_BASE}/v1/transcribe',json={'audio_base64':data,'format':'wav','language':'en'},timeout=HTTP_TIMEOUT); r.raise_for_status(); return r.json()['text'].strip()
def device_context(): return {'embodiment':'Google AIY Voice Kit on Raspberry Pi 4','input':'push-to-talk yellow button','microphone':'AIY Voice HAT microphone','speaker':'AIY Voice HAT speaker','vision':False,'mobility':False}
def interact(text):
    r=requests.post(f'{API_BASE}/v1/entities/{ENTITY_ID}/interact',json={'text':text,'device_id':DEVICE_ID,'context':device_context()},timeout=HTTP_TIMEOUT); r.raise_for_status(); return r.json()['text'].strip()
def wake_greeting(info):
    r=requests.post(f'{API_BASE}/v1/entities/{ENTITY_ID}/wake',json={'device_id':DEVICE_ID,**info,'context':device_context()},timeout=HTTP_TIMEOUT); r.raise_for_status(); return r.json()['text'].strip()
def cloud_speak(text):
    r=requests.post(f'{API_BASE}/v1/speech',json={'text':text},timeout=HTTP_TIMEOUT); r.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix='.mp3',delete=False) as tmp: path=tmp.name; tmp.write(r.content)
    try: subprocess.run(['mpg123','-q','-o','alsa','-a',ALSA_DEVICE,path],check=True)
    finally: Path(path).unlink(missing_ok=True)
def piper_speak(text):
    with tempfile.NamedTemporaryFile(suffix='.wav',delete=False) as tmp: path=tmp.name
    try:
        subprocess.run([PIPER,'--model',PIPER_MODEL,'--output_file',path],input=text.encode(),check=True)
        subprocess.run(['aplay','-q','-D',ALSA_DEVICE,path],check=True)
    finally: Path(path).unlink(missing_ok=True)
def speak(text):
    try: cloud_speak(text)
    except Exception as exc: print(f'Cloud TTS failed ({exc}); using Piper fallback.'); piper_speak(text)

def wait_for_api():
    for attempt in range(1,STARTUP_API_RETRIES+1):
        try:
            r=requests.get(f'{API_BASE}/health',timeout=10); r.raise_for_status(); print('Enormous Brain API is online.'); return True
        except requests.RequestException as exc:
            print(f'Waiting for Enormous Brain API ({attempt}/{STARTUP_API_RETRIES}): {type(exc).__name__}')
            if attempt<STARTUP_API_RETRIES: time.sleep(STARTUP_API_RETRY_SECONDS)
    return False

def run_wake_sequence():
    info=begin_boot_session(); print(f'Boot session: {info}')
    if not wait_for_api():
        try: piper_speak("I'm awake, but the rest of my brain seems to be somewhere else.")
        except Exception as exc: print(f'Local wake fallback failed: {exc}')
        return
    try:
        greeting=wake_greeting(info); print(f'Wake greeting: {greeting}'); speak(greeting)
    except Exception as exc:
        print(f'Wake sequence failed: {exc}')
        try: speak("Oh. I'm back.")
        except Exception as fallback: print(f'Wake fallback failed: {fallback}')

def shutdown_box():
    print('Shutdown requested.')
    try: speak('All right. Going to sleep.')
    except Exception as exc: print(f'Could not speak shutdown message: {exc}')
    remember_shutdown(); time.sleep(.5); subprocess.run(SHUTDOWN_COMMAND,check=True)

def main():
    print('Talking Box V4 starting.'); run_wake_sequence(); print('Talking Box V4 ready.'); print('Hold the yellow button to talk. Release when finished.')
    while True:
        button.wait_for_press()
        with tempfile.NamedTemporaryFile(suffix='.wav',delete=False) as tmp: input_path=tmp.name
        print('Listening...')
        try:
            record_until_release(input_path)
            if Path(input_path).stat().st_size<1000: print('Recording too short; ignored.'); continue
            print('Transcribing...'); transcript=transcribe(input_path); print(f'You: {transcript}')
            if not transcript: continue
            if is_shutdown_request(transcript): shutdown_box(); return
            print('Thinking...'); reply=interact(transcript); print(f'{ENTITY_ID}: {reply}'); print('Speaking...'); speak(reply)
        except requests.HTTPError as exc: print(f"API error: {exc} {getattr(exc.response,'text','')}")
        except Exception as exc: print(f'Error: {type(exc).__name__}: {exc}')
        finally: Path(input_path).unlink(missing_ok=True)
        time.sleep(.15)

if __name__=='__main__': main()
