"""One-off: repair althef turn-6 data + regenerate Slach's avatar (neutral pose).

Runs INSIDE the game-server container:
    docker compose exec game-server python /app/logs/repair_turn6.py

1. gc aboard_ship for the shaft location was wrongly True ("корабль в шахту")
   → set False in game_turns.global_circumstances.
2. Delete the bridge-contaminated shaft backdrop row and regenerate it
   (now a proper ancient-shaft txt2img).
3. Slach's avatar_description demanded a sitting/knee-hugging pose — the crooked
   base pose that leaked into every image. Regenerate the avatar with a neutral
   standing-pose prompt, update profile avatar_url + avatar_description.
"""

import asyncio
import json
import sqlite3
import sys

sys.path.insert(0, "/app")

import aiohttp

from database import get_player_profile
from image_generator import create_image_generator
from main import _ensure_location_background

GAME = "althef"
TURN = 6
DB = "/app/game_server.db"


def fix_gc_aboard() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT global_circumstances FROM game_turns WHERE game_id=? AND turn=?", (GAME, TURN)
    ).fetchone()
    gc = json.loads(row["global_circumstances"])
    changed = False
    if gc.get("location", {}).get("aboard_ship"):
        # The shaft under the ruins is NOT a ship interior.
        gc["location"]["aboard_ship"] = False
        changed = True
    conn.execute(
        "UPDATE game_turns SET global_circumstances=? WHERE game_id=? AND turn=?",
        (json.dumps(gc, ensure_ascii=False), GAME, TURN),
    )
    conn.commit()
    conn.close()
    print(f"gc aboard_ship fixed: {changed}")


def delete_bad_shaft_backdrop() -> None:
    conn = sqlite3.connect(DB)
    cur = conn.execute(
        "DELETE FROM game_images WHERE game_id=? AND type='turn_background' AND location_id='ashfall_ruins_shaft_deadend'",
        (GAME,),
    )
    conn.commit()
    conn.close()
    print(f"deleted bad shaft backdrop rows: {cur.rowcount}")


async def regen_shaft_backdrop() -> None:
    bg = await _ensure_location_background(
        GAME, TURN,
        {"id": "ashfall_ruins_shaft_deadend", "name": "Заброшенная шахта древней цивилизации",
         "description": "Тупиковый канал под руинами. Пепел висит неподвижно, воздух застывший. Стены древние, но искусственные. Полная тишина — город здесь не говорит.",
         "aboard_ship": False},
        setting="Мёртвый город под пеплом",
    )
    print("shaft backdrop:", bg["image_url"] if bg else None)


async def regen_slach_avatar() -> None:
    profile = get_player_profile(281412419)
    # Keep only the appearance lines: drop the sitting pose + environment
    # sentences (everything from the first pose verb onwards).
    desc = (profile.get("avatar_description") or "").strip()
    appearance = desc.split("Сидит на")[0].strip().rstrip(". ,")
    prompt = (
        f"{appearance}. "
        "Стоит в нейтральной позе в полный рост, руки расслаблены вдоль тела, "
        "спокойное нейтральное выражение лица, взгляд прямо перед собой, "
        "на нейтральном однотонном фоне. Без действия, без стульев и кресел."
    )
    print("avatar prompt:", prompt)
    gen = create_image_generator()
    url = await gen.generate_avatar_image(
        prompt=prompt,
        filename_prefix=f"{GAME}/avatar_281412419",
        width=768,
        height=1024,
        game_id=GAME,
        player_id="281412419",
        turn=None,
        kind="avatar",
    )
    print("avatar:", url)
    if not url:
        return
    conn = sqlite3.connect(DB)
    conn.execute(
        "UPDATE player_profiles SET avatar_url=?, avatar_description=? WHERE player_id=? AND game_id=?",
        (url, prompt, 281412419, GAME),
    )
    conn.commit()
    conn.close()
    print("profile updated")


async def main_async() -> None:
    fix_gc_aboard()
    delete_bad_shaft_backdrop()
    await regen_shaft_backdrop()
    await regen_slach_avatar()


asyncio.run(main_async())
