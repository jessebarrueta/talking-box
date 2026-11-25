"""Grey's anonymous fort activities and invited, ephemeral family calls."""
import json
import os
from enum import Enum
from pathlib import Path

import httpx
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field

try:
    from .grey_access import require_family
    from .grey_identity import identity_configured
except ImportError:
    from grey_access import require_family
    from grey_identity import identity_configured

router = APIRouter(prefix="/companions/grey", tags=["Singing Friend"])
SONGS = json.loads(Path(__file__).with_name("grey_songs.json").read_text())
REACTIONS = ["love", "laugh", "surprise", "cheer"]
PRIVATE = {"Cache-Control": "private, no-store"}
PERSONA = (
    "You are Grey, a warm, playful singing AI character visiting from a pillow fort. "
    "Speak in one to three short sentences for ages four to eight. Match the child's energy. "
    "Be honest about being a character. Never request private information, secrecy, exclusivity, "
    "or pictures of anyone. Encourage a trusted grown-up when help is needed. "
    "Only describe camera details if a recent frame is provided. Your songs are "
    + ", ".join(SONGS.values()) + ". Offer a relevant song and wait for agreement. "
    "Use play_song only when requested or accepted. Never speak tool names, tags, "
    "or reactions. Visual reactions are occasional, at most one per turn."
)


class Story(str, Enum):
    moon = "moon"
    beetle = "beetle"
    button = "button"


# Only these authored story ingredients are sent upstream. No visitor-supplied
# text, images, audio or identifiers enter this public interaction.
STORIES = {
    Story.moon: ("a small moon who borrows a quilt", "The moon borrowed my quilt. When she brought it back, one corner was full of stars!"),
    Story.beetle: ("a beetle delivering a tiny musical parcel", "A beetle knocked with six little feet. Inside his parcel was a song just big enough for a ladybug."),
    Story.button: ("a runaway button who becomes a drum", "My button rolled under a pillow and heard a tiny parade. They needed a drum, and he was exactly the right shape!"),
}


def live_enabled():
    # Provision this dedicated project key only after configuring it for
    # child-directed use. Invitations are issued outside the app to approved families.
    return bool(os.getenv("GREY_REALTIME_API_KEY") and identity_configured())


@router.get("")
async def capabilities(response: Response):
    response.headers.update(PRIVATE)
    return {"id": "grey", "realtime": live_enabled(), "family_accounts": identity_configured(), "songs": list(SONGS), "reactions": REACTIONS}


def story_fallback(seed):
    return {"text": STORIES[seed][1], "source": "storybook"}


_story_cache = {}


@router.get("/stories/{seed}")
async def story(seed: Story, response: Response):
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=86400"
    if seed in _story_cache:
        return _story_cache[seed]
    line = await generate_line("Write a gentle two-sentence miniature fairy tale for a four-year-old in Grey's cozy pillow fort. No questions, instructions, scary events, or markdown.", STORIES[seed][0])
    if line:
        _story_cache[seed] = {"text": line, "source": "grey"}
        return _story_cache[seed]
    return story_fallback(seed)


async def generate_line(instructions, prompt):
    key = os.getenv("OPENROUTER_API_KEY")
    if key:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                result = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json={
                        "model": os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini"),
                        "max_tokens": 110,
                        "messages": [
                            {"role": "system", "content": instructions},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                result.raise_for_status()
                line = result.json()["choices"][0]["message"]["content"].strip()
                if 10 <= len(line) <= 400:
                    return line
        except (httpx.HTTPError, KeyError, ValueError, IndexError, AttributeError, TypeError):
            pass
    return None


class Offer(BaseModel):
    sdp: str = Field(min_length=16, max_length=65536)


def tool(name, description, field, choices):
    return {
        "type": "function", "name": name, "description": description,
        "parameters": {"type": "object", "properties": {field: {"type": "string", "enum": choices}},
                       "required": [field], "additionalProperties": False},
    }


@router.post("/calls")
async def start_call(offer: Offer, authorization: str = Header(default="")):
    if not live_enabled():
        raise HTTPException(503, "Live family calls are unavailable.", headers=PRIVATE)
    await require_family(authorization)
    if not offer.sdp.startswith("v=0") or "m=audio " not in offer.sdp:
        raise HTTPException(400, "Expected an audio call offer.", headers=PRIVATE)
    session = {
        "type": "realtime", "model": "gpt-realtime", "instructions": PERSONA,
        "audio": {"input": {"noise_reduction": {"type": "near_field"}, "turn_detection": {"type": "semantic_vad"}},
                  "output": {"voice": "shimmer"}},
        "tools": [
            tool("play_song", "Play the song the child requested or accepted.", "song_id", list(SONGS)),
            tool("show_reaction", "Display one silent reaction.", "reaction", REACTIONS),
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=18) as client:
            result = await client.post(
                "https://api.openai.com/v1/realtime/calls",
                headers={"Authorization": "Bearer " + os.environ["GREY_REALTIME_API_KEY"]},
                files={"sdp": (None, offer.sdp, "application/sdp"),
                       "session": (None, json.dumps(session), "application/json")},
            )
            result.raise_for_status()
            if not result.text.startswith("v=0"):
                raise ValueError("Invalid answer")
            return Response(content=result.text, media_type="application/sdp", headers=PRIVATE)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Grey could not answer. Please try again.", headers=PRIVATE) from None


class FortTopic(str, Enum):
    hello = "hello"
    stars = "stars"
    tune = "tune"
    silly = "silly"


class FortReply(str, Enum):
    discover = "discover"
    imagine = "imagine"
    hum = "hum"


TOPICS = {
    FortTopic.hello: "welcome the visitor into your pillow fort",
    FortTopic.stars: "a star stitched into the ceiling has begun to twinkle",
    FortTopic.tune: "a pillow makes a soft humming sound",
    FortTopic.silly: "a sock puppet thinks it is the mayor of the fort",
}
REPLIES = {
    FortReply.discover: "look more closely together",
    FortReply.imagine: "make up something silly together",
    FortReply.hum: "make a musical game out of it",
}
_turn_cache = {}


@router.get("/turns/{topic}/{reply}")
async def tap_turn(topic: FortTopic, reply: FortReply, response: Response):
    """Only two fixed game choices reach the model, even when visitors share a URL."""
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=86400"
    key = (topic, reply)
    if key in _turn_cache:
        return _turn_cache[key]
    fallback = {
        FortReply.discover: "Look up! One of the stitches on my blanket is twinkling. Shall we give it a name?",
        FortReply.imagine: "Mayor Sock has called a meeting. All pillows must report for a very important squish!",
        FortReply.hum: "Hmm, hmm, ba-dum! That was my pillow drum. Shall we find a song in my book?",
    }[reply]
    line = await generate_line(
        "You are Grey, a playful animated singing friend for ages 4–8. "
        "Speak directly in character in two short sentences from your pillow fort. "
        "Stay within the pretend game. No personal questions, markdown, stage directions or emojis.",
        f"The previous scene is: {TOPICS[topic]}. The visitor taps to {REPLIES[reply]}.",
    )
    result = {"text": line or fallback, "source": "grey" if line else "storybook",
              "topic": {FortReply.discover: "stars", FortReply.imagine: "silly", FortReply.hum: "tune"}[reply]}
    if line:
        _turn_cache[key] = result
    return result
