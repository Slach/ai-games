"""One-off: backfill games.ship_style for existing games (migration 29).

Runs INSIDE the game-server container:
    docker compose exec game-server python /app/logs/backfill_ship_style.py <game_id>

Asks the bridge-prompt LLM for the ship's design language and saves ONLY
ship_style — no image is generated, existing bridge/backdrop rows stay put.
"""

import asyncio
import sys

sys.path.insert(0, "/app")

from database import get_all_briefings_for_turn, get_game_language, get_players_in_game, save_ship_style
from game_server import create_game_server


async def main() -> None:
    game_id = sys.argv[1]
    language = get_game_language(game_id)
    gm = create_game_server(language=language)

    state_turn = 1
    participants: list[dict] = []
    for pid in (get_players_in_game(game_id) or []):
        participants.append({"type": "player", "player_id": pid, "role": "crew"})
    mission = {}
    briefings = get_all_briefings_for_turn(state_turn, game_id) or []

    result = await gm.generate_bridge_image_prompt(
        mission, participants, crew_refs=None,
        game_id=game_id, player_id=None, turn=None, kind="ship_style_backfill",
    )
    style = (result.get("ship_style") or "").strip()
    if not style:
        print("ERROR: empty ship_style returned")
        return
    save_ship_style(game_id, style)
    print(f"ship_style saved for {game_id} ({len(style)} chars):\n{style}")
    print(f"(briefings on turn 1 inspected: {len(briefings)})")


asyncio.run(main())
