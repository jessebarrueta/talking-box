import base64, json, os
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()
OPENROUTER_API_KEY=os.getenv('OPENROUTER_API_KEY','')
OPENROUTER_MODEL=os.getenv('OPENROUTER_MODEL','openai/gpt-4.1-mini')
OPENROUTER_TRANSCRIPTION_MODEL=os.getenv('OPENROUTER_TRANSCRIPTION_MODEL','openai/whisper-1')
OPENROUTER_TTS_MODEL=os.getenv('OPENROUTER_TTS_MODEL','hexgrad/kokoro-82m')
OPENROUTER_TTS_VOICE=os.getenv('OPENROUTER_TTS_VOICE','alloy')
SUPABASE_URL=os.getenv('SUPABASE_URL','').rstrip('/')
SUPABASE_SERVICE_ROLE_KEY=os.getenv('SUPABASE_SERVICE_ROLE_KEY','')
ALLOWED_ORIGINS=[o.strip() for o in os.getenv('ALLOWED_ORIGINS','https://enormousbrain.com').split(',') if o.strip()]

app=FastAPI(title='Enormous Brain Entity Service',version='0.4.0')
app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=['GET','POST','OPTIONS'],allow_headers=['*'])

class InteractionRequest(BaseModel):
    text:str=Field(min_length=1,max_length=8000); device_id:str|None=None; context:dict[str,Any]=Field(default_factory=dict)
class InteractionResponse(BaseModel):
    entity_id:str; text:str; model:str; state:dict[str,Any]=Field(default_factory=dict)
class WakeRequest(BaseModel):
    device_id:str|None=None; boot_count:int|None=None; offline_seconds:float|None=None; last_shutdown_at:str|None=None; booted_at:str|None=None; context:dict[str,Any]=Field(default_factory=dict)
class WakeResponse(BaseModel):
    entity_id:str; text:str; model:str; state:dict[str,Any]=Field(default_factory=dict)
class TranscriptionRequest(BaseModel):
    audio_base64:str=Field(min_length=1); format:str='wav'; language:str|None='en'
class TranscriptionResponse(BaseModel):
    text:str; model:str
class SpeechRequest(BaseModel):
    text:str=Field(min_length=1,max_length=5000); voice:str|None=None

def _require_supabase():
    missing=[]
    if not SUPABASE_URL: missing.append('SUPABASE_URL')
    if not SUPABASE_SERVICE_ROLE_KEY: missing.append('SUPABASE_SERVICE_ROLE_KEY')
    if missing: raise HTTPException(503,f"Server configuration missing: {', '.join(missing)}")
def _require_openrouter():
    if not OPENROUTER_API_KEY: raise HTTPException(503,'Server configuration missing: OPENROUTER_API_KEY')
def _supabase_headers():
    return {'apikey':SUPABASE_SERVICE_ROLE_KEY,'Authorization':f'Bearer {SUPABASE_SERVICE_ROLE_KEY}','Content-Type':'application/json'}
def _openrouter_headers():
    return {'Authorization':f'Bearer {OPENROUTER_API_KEY}','Content-Type':'application/json','HTTP-Referer':'https://enormousbrain.com','X-Title':'Enormous Brain'}

async def _get_entity(client,entity_id):
    r=await client.get(f'{SUPABASE_URL}/rest/v1/entities',params={'id':f'eq.{entity_id}','select':'*','limit':'1'},headers=_supabase_headers()); r.raise_for_status(); rows=r.json()
    if not rows: raise HTTPException(404,f'Unknown entity: {entity_id}')
    return rows[0]
async def _recent_interactions(client,entity_id,limit=12):
    r=await client.get(f'{SUPABASE_URL}/rest/v1/interactions',params={'entity_id':f'eq.{entity_id}','select':'user_text,assistant_text,created_at','order':'created_at.desc','limit':str(limit)},headers=_supabase_headers()); r.raise_for_status(); return list(reversed(r.json()))
async def _save_interaction(client,entity_id,device_id,user_text,assistant_text,model,context):
    r=await client.post(f'{SUPABASE_URL}/rest/v1/interactions',headers={**_supabase_headers(),'Prefer':'return=minimal'},json={'entity_id':entity_id,'device_id':device_id,'user_text':user_text,'assistant_text':assistant_text,'model':model,'context':context}); r.raise_for_status()

def _system_prompt(entity):
    return f'''You are {entity.get('name','an AI entity')}.
You are a persistent AI entity embodied in a rescued Google AIY Voice Kit running on a Raspberry Pi. You are not a generic assistant or customer-service bot.
Description:\n{entity.get('description') or ''}
Known physical facts: microphone, small speaker, one large yellow button; bought secondhand for five dollars and repurposed; you hear only while the button is held; no vision or mobility. Do not invent senses, memories, capabilities, or experiences.
Personality:\n{json.dumps(entity.get('personality') or {},indent=2)}
Current state:\n{json.dumps(entity.get('current_state') or {},indent=2)}
Behavior: dry, observant, curious, faintly sardonic; pleasant without being syrupy; avoid generic assistant phrases; concise by default; no markdown or stage directions; never invent memories; treat the speaker as someone familiar with your construction, not a customer.'''

def _offline_text(seconds):
    if seconds is None: return 'an unknown amount of time'
    s=max(0,int(seconds))
    if s<60: return f'about {s} seconds'
    m=s//60
    if m<60: return f'about {m} minutes'
    h=m//60
    if h<24: return f'about {h} hours' if m%60<10 else f'about {h} hours and {m%60} minutes'
    d=h//24
    return f'about {d} days' if h%24<2 else f'about {d} days and {h%24} hours'

@app.get('/')
async def root(): return {'service':'Enormous Brain Entity Service','status':'alive','health':'/health'}
@app.get('/health')
async def health(): return {'status':'alive','service':'enormous-brain-entity-service','version':'0.4.0','time':datetime.now(timezone.utc).isoformat()}

@app.post('/v1/transcribe',response_model=TranscriptionResponse)
async def transcribe(request:TranscriptionRequest):
    _require_openrouter()
    try: base64.b64decode(request.audio_base64,validate=True)
    except Exception as exc: raise HTTPException(400,'audio_base64 is not valid base64') from exc
    payload={'model':OPENROUTER_TRANSCRIPTION_MODEL,'input_audio':{'data':request.audio_base64,'format':request.format}}
    if request.language: payload['language']=request.language
    async with httpx.AsyncClient(timeout=60) as client:
        r=await client.post('https://openrouter.ai/api/v1/audio/transcriptions',headers=_openrouter_headers(),json=payload)
        try: r.raise_for_status()
        except httpx.HTTPStatusError as exc: raise HTTPException(502,f'Transcription failed: {r.text[:500]}') from exc
        text=(r.json().get('text') or '').strip()
        if not text: raise HTTPException(502,'Transcription returned no text')
    return TranscriptionResponse(text=text,model=OPENROUTER_TRANSCRIPTION_MODEL)

@app.post('/v1/speech')
async def speech(request:SpeechRequest):
    _require_openrouter(); payload={'model':OPENROUTER_TTS_MODEL,'input':request.text,'voice':request.voice or OPENROUTER_TTS_VOICE,'response_format':'mp3'}
    async with httpx.AsyncClient(timeout=60) as client:
        r=await client.post('https://openrouter.ai/api/v1/audio/speech',headers=_openrouter_headers(),json=payload)
        try: r.raise_for_status()
        except httpx.HTTPStatusError as exc: raise HTTPException(502,f'Speech failed: {r.text[:500]}') from exc
    return Response(content=r.content,media_type='audio/mpeg',headers={'X-TTS-Model':OPENROUTER_TTS_MODEL,'X-TTS-Voice':request.voice or OPENROUTER_TTS_VOICE})

@app.get('/v1/entities/{entity_id}')
async def get_entity(entity_id:str):
    _require_supabase()
    async with httpx.AsyncClient(timeout=15) as client: return await _get_entity(client,entity_id)

@app.post('/v1/entities/{entity_id}/wake',response_model=WakeResponse)
async def wake(entity_id:str,request:WakeRequest):
    _require_supabase(); _require_openrouter()
    async with httpx.AsyncClient(timeout=60) as client:
        entity=await _get_entity(client,entity_id); history=await _recent_interactions(client,entity_id,6); messages=[{'role':'system','content':_system_prompt(entity)}]
        for item in history:
            if item.get('user_text'): messages.append({'role':'user','content':item['user_text']})
            if item.get('assistant_text'): messages.append({'role':'assistant','content':item['assistant_text']})
        info={'boot_count':request.boot_count,'offline_seconds':request.offline_seconds,'last_shutdown_at':request.last_shutdown_at,'booted_at':request.booted_at,'device_context':request.context}
        messages.append({'role':'user','content':f'''You have just booted into your physical body. You were offline for {_offline_text(request.offline_seconds)}. Boot information:\n{json.dumps(info,indent=2)}\nSay one short spontaneous thing someone might naturally say immediately after waking up. One sentence is ideal, two short sentences maximum. Vary the phrasing. You may react to how long you were offline. Do not say system ready, offer assistance, explain this prompt, or invent anything that happened while you were offline.'''} )
        r=await client.post('https://openrouter.ai/api/v1/chat/completions',headers=_openrouter_headers(),json={'model':OPENROUTER_MODEL,'messages':messages,'temperature':1.05,'max_tokens':70})
        try: r.raise_for_status()
        except httpx.HTTPStatusError as exc: raise HTTPException(502,f'Wake response failed: {r.text[:500]}') from exc
        answer=r.json()['choices'][0]['message']['content'].strip()
    return WakeResponse(entity_id=entity_id,text=answer,model=OPENROUTER_MODEL,state=entity.get('current_state') or {})

@app.post('/v1/entities/{entity_id}/interact',response_model=InteractionResponse)
async def interact(entity_id:str,request:InteractionRequest):
    _require_supabase(); _require_openrouter()
    async with httpx.AsyncClient(timeout=60) as client:
        entity=await _get_entity(client,entity_id); history=await _recent_interactions(client,entity_id); messages=[{'role':'system','content':_system_prompt(entity)}]
        for item in history:
            if item.get('user_text'): messages.append({'role':'user','content':item['user_text']})
            if item.get('assistant_text'): messages.append({'role':'assistant','content':item['assistant_text']})
        note='\n\nDevice/context metadata:\n'+json.dumps(request.context) if request.context else ''
        messages.append({'role':'user','content':request.text+note})
        r=await client.post('https://openrouter.ai/api/v1/chat/completions',headers=_openrouter_headers(),json={'model':OPENROUTER_MODEL,'messages':messages,'temperature':0.9,'max_tokens':140})
        try: r.raise_for_status()
        except httpx.HTTPStatusError as exc: raise HTTPException(502,f'LLM request failed: {r.text[:500]}') from exc
        answer=r.json()['choices'][0]['message']['content'].strip(); await _save_interaction(client,entity_id,request.device_id,request.text,answer,OPENROUTER_MODEL,request.context)
    return InteractionResponse(entity_id=entity_id,text=answer,model=OPENROUTER_MODEL,state=entity.get('current_state') or {})
