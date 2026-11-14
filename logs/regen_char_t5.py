"""One-off: regenerate one player's turn-5 character image (new per-location pipeline).

Runs INSIDE the game-server container:
    docker compose exec game-server python /app/logs/regen_char_t5.py <player_id> <game_id> <turn>
"""

import asyncio
import os
import sys

import aiohttp

sys.path.insert(0, "/app")

from database import get_all_briefings_for_turn, get_game_language, get_player_profile
from game_server import create_game_server
from image_generator import create_image_generator
from main import _ensure_location_background, _pose_catalogue_for, _pose_filename_for


async def main() -> None:
    pid = int(sys.argv[1])
    game_id = sys.argv[2]
    turn = int(sys.argv[3])
    language = get_game_language(game_id)
    gm = create_game_server(language=language)
    gen = create_image_generator()

    b = next(
        (b for b in (get_all_briefings_for_turn(turn, game_id) or [])
         if str(b.get("player_id")) == str(pid)),
        None,
    )
    if not b:
        print(f"no briefing for player {pid} turn {turn}")
        return
    profile = get_player_profile(pid) or {}
    print(f"player {pid}: role={profile.get('role')}")

    import json
    from database import get_game_turn
    turn_data = get_game_turn(turn, game_id)
    try:
        global_circ = json.loads(turn_data.get("global_circumstances", "{}")) if turn_data else {}
    except (json.JSONDecodeError, TypeError):
        global_circ = {}
    turn_location = global_circ.get("location") or {}
    setting = global_circ.get("setting", "") or (turn_data.get("story", "") if turn_data else "")

    char_action = b.get("image_prompt", "") or f"reacting to the situation in {setting}"
    species_category = profile.get("species_primary_key") or ""
    scene = await gm.generate_scene_instruction(
        action_text=char_action,
        species_desc=profile.get("species_description", "") or profile.get("species", ""),
        language=language,
        scene_description="",
        scene_context=f"Setting: {setting}",
        species_category=species_category,
        game_id=game_id,
        player_id=str(pid),
        turn=turn,
        kind="character_scene",
        pose_catalogue=_pose_catalogue_for(species_category),
        turn_location=turn_location,
    )
    location = scene.get("location") or turn_location
    print("pose:", scene.get("pose_id"), "| location:", location.get("id"))
    pose_filename = await _pose_filename_for(species_category, scene.get("pose_id", ""))

    turn_bg = await _ensure_location_background(game_id, turn, location, setting=setting)
    background_url = turn_bg["image_url"] if turn_bg else None
    print("backdrop:", background_url)

    url = await gen.generate_character_in_scene(
        instruction_prompt=scene["instruction"],
        character_avatar_url=profile.get("avatar_url"),
        background_url=background_url,
        character_description=profile.get("species_description", "") or "",
        filename_prefix=f"{game_id}/regen_char_turn{turn}_p{pid}",
        width=1024,
        height=1024,
        game_id=game_id,
        player_id=str(pid),
        turn=turn,
        kind="character_scene",
        species_category=species_category,
        pose_filename=pose_filename or None,
    )
    print("url:", url)
    if url:
        out = "/app/logs/regen_gm"
        os.makedirs(out, exist_ok=True)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                resp.raise_for_status()
                with open(os.path.join(out, f"char_t{turn}_p{pid}.png"), "wb") as fh:
                    fh.write(await resp.read())
        print(f"saved {out}/char_t{turn}_p{pid}.png")


asyncio.run(main())
