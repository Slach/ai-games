"""One-off: regenerate althef turn 3 player character image (new pipeline).

Runs INSIDE the game-server container:
    docker compose exec game-server python /app/logs/gm_review_20261111/regen_char3.py

Downloads the result to logs/regen_gm/char3.png. Nothing is written to the DB.
"""

import asyncio
import os
import sys

import aiohttp

sys.path.insert(0, "/app")

from database import get_all_briefings_for_turn, get_game_language, get_player_profile
from game_server import create_game_server
from image_generator import create_image_generator
from main import _ensure_turn_background, _pose_catalogue_for, _pose_filename_for

GAME = "althef"
TURN = 3
OUT = "/app/logs/regen_gm"


async def main() -> None:
    language = get_game_language(GAME)
    gm = create_game_server(language=language)
    gen = create_image_generator()

    briefs = [
        b for b in (get_all_briefings_for_turn(TURN, GAME) or [])
        if b.get("player_id") and not b.get("is_npc")
    ]
    b = briefs[0]
    pid = b["player_id"]
    profile = get_player_profile(pid) or {}
    print(f"player {pid}: role={profile.get('role')}")

    turn_bg = await _ensure_turn_background(GAME, TURN)
    scene = await gm.generate_scene_instruction(
        action_text=b.get("image_prompt", "") or "reacting to the situation",
        species_desc=profile.get("species_description", "") or profile.get("species", ""),
        language=language,
        scene_description=turn_bg["prompt"] if turn_bg else "",
        scene_context=f"Setting: {profile.get('role')}",
        species_category=profile.get("species_primary_key") or "",
        game_id=GAME,
        player_id=str(pid),
        turn=TURN,
        kind="character_scene",
        pose_catalogue=_pose_catalogue_for(profile.get("species_primary_key") or ""),
    )
    print("pose:", scene.get("pose_id"), "| instruction:", scene["instruction"][:160], "...")
    pose_filename = await _pose_filename_for(profile.get("species_primary_key") or "", scene.get("pose_id", ""))

    url = await gen.generate_character_in_scene(
        instruction_prompt=scene["instruction"],
        character_avatar_url=profile.get("avatar_url"),
        background_url=turn_bg["image_url"] if turn_bg else None,
        character_description=profile.get("species_description", "") or "",
        filename_prefix=f"{GAME}/regen_char_turn{TURN}_p{pid}",
        width=1024,
        height=1024,
        game_id=GAME,
        player_id=str(pid),
        turn=TURN,
        kind="character_scene",
        species_category=profile.get("species_primary_key") or "",
        pose_filename=pose_filename or None,
    )
    print("url:", url)
    if not url:
        return
    os.makedirs(OUT, exist_ok=True)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            resp.raise_for_status()
            with open(os.path.join(OUT, "char3.png"), "wb") as fh:
                fh.write(await resp.read())
    print(f"saved {OUT}/char3.png")


asyncio.run(main())
