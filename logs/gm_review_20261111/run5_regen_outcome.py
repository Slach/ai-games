"""One-off: regenerate ixisyr turn-11 OUTCOME image on the location backdrop.

Mirrors the production outcome path (main.py outcome flow) with the new
background_url wiring, writes the result to logs/gm_review_20261111/run5/,
sends nothing itself (the host sends it to the GM).
"""

import asyncio
import json
import os
import sys

import aiohttp

sys.path.insert(0, "/app")

from database import get_game_language, get_game_turn
from game_server import create_game_server
from image_generator import create_image_generator
from main import _collect_crew_avatar_refs, _ensure_turn_background

GAME_ID = "ixisyr"
TURN = 11
OUT_DIR = "/app/logs/gm_review_20261111/run5"
COMFYUI_BASE = os.getenv("COMFYUI_URL", "http://comfyui:8188")


async def _download(url: str, dest: str) -> bool:
    async with aiohttp.ClientSession() as session, session.get(
        url, timeout=aiohttp.ClientTimeout(total=60)
    ) as resp:
        if resp.status == 200:
            with open(dest, "wb") as f:
                f.write(await resp.read())
            return True
    return False


async def run():
    os.makedirs(OUT_DIR, exist_ok=True)
    turn_data = get_game_turn(TURN, GAME_ID)
    outcome = json.loads(turn_data.get("combined_outcome") or "{}")
    narrative = outcome.get("outcome_narrative", "")
    ship_status = outcome.get("ship_status_change", "")
    crew_morale = outcome.get("crew_morale_change", "")

    language = get_game_language(GAME_ID)
    gm = create_game_server(language=language)
    crew_refs = _collect_crew_avatar_refs(GAME_ID)
    turn_bg = await _ensure_turn_background(GAME_ID, TURN)
    turn_bg_url = turn_bg["image_url"] if turn_bg else None
    turn_bg_prompt = turn_bg["prompt"] if turn_bg else ""
    print(f"crew refs (alive): {len(crew_refs)}, bg: {turn_bg_url}")

    prompt_result = await gm.generate_outcome_image_prompt(
        narrative, ship_status, crew_morale, crew_refs,
        game_id=GAME_ID, player_id=None, turn=TURN,
        kind="outcome_image_prompt", scene_description=turn_bg_prompt,
        max_characters=len(crew_refs),
    )
    image_prompt = prompt_result.get("image_prompt", "")
    print("--- image prompt ---")
    print(image_prompt[:600])

    gen = create_image_generator()
    url = await gen.generate_multi_reference_image(
        prompt=image_prompt,
        avatar_urls=[r["avatar_url"] for r in crew_refs],
        filename_prefix=f"{GAME_ID}/regen_outcome_turn{TURN}",
        game_id=GAME_ID, kind="outcome", width=1024, height=1024,
        turn=TURN, background_url=turn_bg_url,
    )
    print("outcome url:", url)
    if url:
        await _download(url, os.path.join(OUT_DIR, "outcome_new.png"))
    if turn_bg_url:
        await _download(turn_bg_url, os.path.join(OUT_DIR, "turn_bg.png"))
    print("done")


asyncio.run(run())
