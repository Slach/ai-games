"""Dev tool: regenerate NPC avatars + turn action images for a GM visual test.

For every briefing of the given turn that has a selected action:
  * NPC avatars are regenerated first with the avatar-override model
    (COMFYUI_AVATAR_MODEL, e.g. qwen_image_2512) — the complex-prompt fix;
  * the action image is then regenerated via Qwen-Image-Edit with that avatar
    as Picture 1 (players keep their existing avatar).

Nothing is written to the database and nothing is pushed to players. Every
image is downloaded into logs/regen_gm/ together with manifest.json, so the
results can be sent to the game master from the host:

    docker compose exec game-server python /app/tools/regen_actions_to_gm.py [game_id]
"""

import asyncio
import json
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import (
    get_all_briefings_for_turn,
    get_game_language,
    get_game_state,
    get_game_turn,
    get_npc_profile,
    get_player_profile,
)
from game_server import create_game_server
from image_generator import create_image_generator
from main import _ensure_turn_background as ensure_turn_background

OUT_DIR = "/app/logs/regen_gm"
COMFYUI_BASE = os.getenv("COMFYUI_URL", "http://comfyui:8188")


async def _download(url: str, dest: str) -> bool:
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp,
        ):
            if resp.status == 200:
                with open(dest, "wb") as f:
                    f.write(await resp.read())
                return True
            print(f"  ! download HTTP {resp.status}: {url}")
    except Exception as e:
        print(f"  ! download failed ({e}): {url}")
    return False


def _scene_context(turn_data: dict | None) -> str:
    if not turn_data:
        return ""
    try:
        global_circ = json.loads(turn_data.get("global_circumstances", "{}"))
    except (json.JSONDecodeError, TypeError):
        global_circ = {}
    setting = global_circ.get("setting", "") or turn_data.get("story", "")
    conflict = global_circ.get("conflict", "")
    return f"Setting: {setting}. Situation: {conflict}" if conflict else f"Setting: {setting}"


async def main() -> None:
    game_id = sys.argv[1] if len(sys.argv) > 1 else "default_game"
    # game_state tracks the NEXT turn to generate; the latest turn with
    # chosen actions (the one whose outcome imagery we redo) is turn - 1.
    turn = get_game_state(game_id)["turn"] - 1
    language = get_game_language(game_id)
    turn_data = get_game_turn(turn, game_id)
    scene_ctx = _scene_context(turn_data)

    briefings = [
        b for b in (get_all_briefings_for_turn(turn, game_id) or [])
        if b.get("selected_action_id")
    ]
    if not briefings:
        print(f"No briefings with selected actions for {game_id} turn {turn}")
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    gen = create_image_generator()
    gm = create_game_server(language=language)
    manifest = []

    for i, b in enumerate(briefings, start=1):
        player_id = b.get("player_id")
        npc_key = b.get("npc_key")
        if player_id:
            profile = get_player_profile(player_id) or {}
            name = profile.get("player_name", "") or str(player_id)
            role = profile.get("role", "")
            species_desc = profile.get("species_description", "") or profile.get("species", "")
            species_category = profile.get("species_primary_key", "") or ""
            avatar_url = profile.get("avatar_url")
            slug = f"p{player_id}"
            kind = "player_action"
        elif npc_key:
            profile = get_npc_profile(npc_key) or {}
            name = profile.get("npc_name", npc_key)
            role = profile.get("role", "")
            species_desc = profile.get("species", "") or ""
            species_category = profile.get("species", "") or ""
            kind = "npc_action"
            slug = npc_key.replace(f"_{game_id}", "").replace("npc_", "")
            avatar_desc = profile.get("avatar_description", "")
            avatar_prompt = avatar_desc.split(";", 1)[1] if ";" in avatar_desc else ""
            avatar_url = None
            if avatar_prompt:
                print(f"[{i}/{len(briefings)}] {name} — regenerating avatar ({slug})...")
                avatar_url = await gen.generate_avatar_image(
                    prompt=avatar_prompt,
                    filename_prefix=f"{game_id}/regen_avatar_{slug}",
                    width=768,
                    height=1024,
                    game_id=game_id,
                    player_id=None,
                    turn=None,
                    kind=f"npc_avatar_{slug}",
                )
                if not avatar_url:
                    print(f"  ! avatar generation failed for {name}")
        else:
            continue

        action_text = ""
        for c in b.get("choices", []):
            if c.get("id") == b.get("selected_action_id"):
                action_text = c.get("text", c.get("description", ""))
                break
        if not action_text:
            action_text = b.get("selected_action_id", "")

        if not avatar_url:
            print(f"[{i}/{len(briefings)}] {name} — no avatar, skipping action image")
            continue

        print(f"[{i}/{len(briefings)}] {name} — scene instruction...")
        turn_bg = await ensure_turn_background(game_id, turn)
        background_url = turn_bg["image_url"] if turn_bg else None
        scene_desc = turn_bg["prompt"] if turn_bg else ""
        instruction = ""
        try:
            instruction = await gm.generate_scene_instruction(
                action_text=action_text,
                species_desc=species_desc,
                language=language,
                scene_description=scene_desc,
                scene_context=scene_ctx,
                species_category=species_category,
                game_id=game_id,
                player_id=str(player_id) if player_id else npc_key,
                turn=turn,
                kind=kind,
            )
        except Exception as e:
            print(f"  ! scene instruction failed: {e}")
        if not instruction:
            instruction = (
                f"Place the character from Picture 1 in the scene of Picture 2 performing this action: {action_text}. "
                "Cinematic sci-fi scene, dramatic lighting, space opera aesthetic, photorealistic, 4K."
            )

        print(f"[{i}/{len(briefings)}] {name} — action image (category={species_category or 'human'})...")
        action_url = await gen.generate_character_in_scene(
            instruction_prompt=instruction,
            character_avatar_url=avatar_url,
            background_url=background_url,
            character_description=species_desc,
            filename_prefix=f"{game_id}/regen_action_turn{turn}_{slug}",
            width=1024,
            height=1024,
            game_id=game_id,
            player_id=str(player_id) if player_id else None,
            turn=turn,
            kind=kind,
            species_category=species_category,
        )
        if not action_url:
            print(f"  ! action image failed for {name}")
            continue

        entry = {
            "name": name,
            "role": role,
            "action": action_text,
            "avatar_file": f"{i:02d}_{slug}_avatar.png",
            "action_file": f"{i:02d}_{slug}_action.png",
            "avatar_is_new": bool(npc_key),
        }
        if await _download(avatar_url, os.path.join(OUT_DIR, entry["avatar_file"])):
            print(f"  avatar → {entry['avatar_file']}")
        if await _download(action_url, os.path.join(OUT_DIR, entry["action_file"])):
            print(f"  action → {entry['action_file']}")
        manifest.append(entry)

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"game_id": game_id, "turn": turn, "entries": manifest}, f, ensure_ascii=False, indent=2)
    print(f"\nDone: {len(manifest)} entr(ies) in {OUT_DIR}/manifest.json")


if __name__ == "__main__":
    asyncio.run(main())
