"""One-off: regenerate althef turn 2 outcome as a comic page (new pipeline).

Runs INSIDE the game-server container:
    docker compose exec game-server python \
      /app/logs/gm_review_20261111/regen_outcome2_comic.py

Uses the shipped code path (comic prompt builder + multi-reference edit on the
location backdrop) and downloads the result to logs/regen_gm/outcome3_comic.png.
Nothing is written to the database.
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

GAME = "althef"
TURN = 3
OUT = "/app/logs/regen_gm"


async def main() -> None:
    language = get_game_language(GAME)
    turn_data = get_game_turn(TURN, GAME)
    outcome = json.loads(turn_data["combined_outcome"])
    crew_refs = _collect_crew_avatar_refs(GAME)
    print(f"crew refs: {len(crew_refs)} -> {[r.get('name') for r in crew_refs]}")

    bg = await _ensure_turn_background(GAME, TURN)
    bg_url = bg["image_url"] if bg else None
    print(f"turn background: {bg_url}")

    gm = create_game_server(language=language)
    prompt_result = await gm.generate_outcome_image_prompt(
        outcome.get("outcome_narrative", ""),
        outcome.get("ship_status_change", ""),
        outcome.get("crew_morale_change", ""),
        crew_refs,
        game_id=GAME,
        player_id=None,
        turn=TURN,
        kind="outcome_image_prompt",
        scene_description=bg["prompt"] if bg else "",
        max_characters=len(crew_refs),
    )
    image_prompt = prompt_result.get("image_prompt", "")
    print("--- IMAGE PROMPT ---")
    print(image_prompt or "(EMPTY — fallback would be used)")
    if not image_prompt:
        return

    gen = create_image_generator()
    url = await gen.generate_multi_reference_image(
        prompt=image_prompt,
        avatar_urls=[r["avatar_url"] for r in crew_refs],
        filename_prefix=f"{GAME}/regen_outcome_turn{TURN}",
        game_id=GAME,
        kind="outcome",
        width=1024,
        height=1024,
        turn=TURN,
        background_url=bg_url,
    )
    print("url:", url)
    if not url:
        return
    os.makedirs(OUT, exist_ok=True)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            resp.raise_for_status()
            with open(os.path.join(OUT, "outcome3_comic.png"), "wb") as fh:
                fh.write(await resp.read())
    print(f"saved {OUT}/outcome3_comic.png")


asyncio.run(main())
