"""One-off: repose Slach's avatar keeping identity (edit from the old avatar).

Runs INSIDE the game-server container:
    docker compose exec game-server python /app/logs/repose_avatar.py

Text-only avatar regeneration loses identity (the previous attempt rendered a
different person). The avatar IMAGE is the identity — so the pose fix is an
EDIT of the old avatar: same face/skin/uniform, neutral standing pose.
"""

import asyncio
import sqlite3
import sys

sys.path.insert(0, "/app")

from image_generator import create_image_generator

GAME = "althef"
PID = "281412419"
OLD_AVATAR = "http://comfyui:8188/view?filename=avatar_281412419_00002_.png&subfolder=althef&type=output"
DB = "/app/game_server.db"

INSTRUCTION = (
    "Repose this exact character into a neutral identity-reference pose: standing "
    "upright, relaxed, arms at the sides, calm neutral expression, facing the camera "
    "in three-quarter view, full body from head to boots, on a plain flat neutral "
    "background. Keep the face, hair, skin tone, build, uniform, insignia and every "
    "identity trait EXACTLY as in the reference picture — this is the same person, "
    "only standing relaxed instead of the crouching action pose. No other people, "
    "no props."
)


async def main_async() -> None:
    gen = create_image_generator()
    url = await gen.generate_character_in_scene(
        instruction_prompt=INSTRUCTION,
        character_avatar_url=OLD_AVATAR,
        background_url=None,
        character_description="Slach, the communications officer (keep identity)",
        filename_prefix=f"{GAME}/avatar_281412419_repose",
        width=768,
        height=1024,
        game_id=GAME,
        player_id=PID,
        turn=None,
        kind="avatar",
    )
    print("reposed avatar:", url)
    if not url:
        return
    conn = sqlite3.connect(DB)
    conn.execute(
        "UPDATE player_profiles SET avatar_url=? WHERE player_id=? AND game_id=?",
        (url, int(PID), GAME),
    )
    conn.commit()
    conn.close()
    print("profile avatar_url updated")


asyncio.run(main_async())
