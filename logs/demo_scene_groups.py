"""One-off: live demo of the grouped scene-stage comic for althef turn 5.

Runs INSIDE the game-server container:
    docker compose exec game-server python /app/logs/demo_scene_groups.py

Simulates the in-process state a real turn 6 will have (per-crew derived
locations + fresh per-crew panel urls), then runs the real
_build_scene_stage_url: groups the crew by location, generates ONE wide
group picture for the co-located trio and stitches the page.
"""

import asyncio
import sys

sys.path.insert(0, "/app")

import database as db
from database import (
    get_all_briefings_for_turn,
    get_game_turn,
    update_briefing_chosen_action_url,
)
import main

GAME = "althef"
TURN = 5
BASE = "http://comfyui:8188/view?filename={}&subfolder=althef&type=output"

# Fresh (new-pipeline) panel urls per crew member. Player keys are bare
# str(player_id) — that's how main.py keys _turn_crew_locations.
NEW_PANELS = {
    "281412419": BASE.format("regen_action_turn5_p281412419_00001_.png"),
    "npc_science_officer_althef": BASE.format("regen_action_turn5_science_officer_00001_.png"),
    "130804996": BASE.format("regen_char_turn5_p130804996_00001_.png"),
    "npc_medical_officer_althef": BASE.format("regen_action_turn5_medical_officer_00001_.png"),
}
LOCATIONS = {
    "281412419": "ashfall_ruins_perimeter",
    "npc_science_officer_althef": "ashfall_ruins_perimeter",
    "130804996": "ashfall_ruins_perimeter",
    "npc_medical_officer_althef": "medbay_emergency",
}


async def main_async() -> None:
    # Persist the corrected NPC panel urls (the regen tool only downloaded
    # files; the briefing rows still pointed at the old-bridge images).
    for row in (get_all_briefings_for_turn(TURN, GAME) or []):
        key = row.get("npc_key")
        if key and key in NEW_PANELS and row.get("id"):
            update_briefing_chosen_action_url(row["id"], NEW_PANELS[key])
            print(f"updated chosen_action_url for {key}")

    for key, loc in LOCATIONS.items():
        main._turn_crew_locations[(GAME, TURN, key)] = loc

    briefings = []
    for r in (get_all_briefings_for_turn(TURN, GAME) or []):
        if r.get("is_npc"):
            continue
        b = dict(r)
        pid = str(b.get("player_id"))
        if pid in NEW_PANELS:
            b["character_image_url"] = NEW_PANELS[pid]
        briefings.append(b)

    gc = get_game_turn(TURN, GAME)
    print("turn exists:", bool(gc))
    url = await main._build_scene_stage_url(GAME, TURN, briefings)
    print("SCENE STAGE:", url)
    print("groups seen:", {k: v for (g, t, k), v in main._turn_crew_locations.items() if t == TURN})


asyncio.run(main_async())
