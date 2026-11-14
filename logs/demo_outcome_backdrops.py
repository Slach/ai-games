"""One-off: regenerate the althef turn-5 outcome with per-location backdrops.

Runs INSIDE the game-server container:
    docker compose exec game-server python /app/logs/demo_outcome_backdrops.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")

from database import get_game_language, get_game_state, get_game_turn
from game_server import create_game_server
from image_generator import create_image_generator
from main import (
    _collect_crew_avatar_refs,
    _ensure_location_background,
)

GAME = "althef"


async def main_async() -> None:
    state = get_game_state(GAME)
    turn = state["turn"] - 1  # latest completed turn
    language = get_game_language(GAME)
    gm = create_game_server(language=language)
    gen = create_image_generator()

    turn_data = get_game_turn(turn, GAME)
    try:
        gc = json.loads(turn_data.get("global_circumstances", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        gc = {}
    try:
        oc = json.loads(turn_data.get("combined_outcome", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        oc = {}

    crew_refs = _collect_crew_avatar_refs(GAME)
    crew_positions = gc.get("crew_positions") or []
    setting = gc.get("setting", "") or turn_data.get("story", "")
    conflict = gc.get("conflict", "")
    aboard = bool((gc.get("location") or {}).get("aboard_ship"))
    turn_loc_id = ((gc.get("location") or {}).get("id") or "").strip()

    # Locations the crew actually occupies (recorded by this process's action
    # generation) + the turn location as the first anchor.
    loc_ids = [turn_loc_id] if turn_loc_id else []
    # Demo stand-in for the live in-memory map (the live run derived these):
    for loc in ("ashfall_ruins_shaft_deadend", "medbay_emergency"):
        if loc not in loc_ids:
            loc_ids.append(loc)

    backdrops = []
    backdrop_urls = []
    for i, loc_id in enumerate(loc_ids[:3], start=1):
        bg = await _ensure_location_background(
            GAME, turn,
            {"id": loc_id, "name": loc_id, "description": "", "aboard_ship": aboard},
            setting=setting, conflict=conflict,
        )
        if bg:
            backdrop_urls.append(bg["image_url"])
            name = (gc.get("location") or {}).get("name") if loc_id == turn_loc_id else loc_id
            backdrops.append({"picture": i, "name": name or loc_id, "description": (bg["prompt"] or "")[:220]})
    crew_refs = crew_refs[:10 - len(backdrop_urls)]
    print(f"backdrops: {[b['name'] for b in backdrops]}")
    print(f"crew: {[r['name'] for r in crew_refs]}")

    prompt_result = await gm.generate_outcome_image_prompt(
        oc.get("outcome_narrative", ""),
        oc.get("ship_status_change", ""),
        oc.get("crew_morale_change", ""),
        crew_refs,
        game_id=GAME,
        player_id=None,
        turn=turn,
        kind="outcome_image_prompt",
        max_characters=len(crew_refs),
        crew_positions=crew_positions,
        backdrops=backdrops or None,
    )
    image_prompt = prompt_result.get("image_prompt", "")
    print("--- IMAGE PROMPT (first 600 chars) ---")
    print(image_prompt[:600])

    url = await gen.generate_multi_reference_image(
        prompt=image_prompt,
        avatar_urls=[r["avatar_url"] for r in crew_refs],
        filename_prefix=f"{GAME}/regen_outcome_turn{turn}",
        width=1024,
        height=1024,
        game_id=GAME,
        kind="outcome",
        turn=turn,
        backdrop_urls=backdrop_urls or None,
    )
    print("OUTCOME:", url)


asyncio.run(main_async())
