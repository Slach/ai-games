"""HTTP server for receiving push briefings from game-server-api."""

import logging
import os
import re
from collections.abc import Callable
from typing import Any

import aiohttp
from aiohttp import web
from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from language import get_bridge, get_current_day

logger = logging.getLogger(__name__)

PUSH_SERVER_PORT = int(os.getenv("PUSH_SERVER_PORT", "9090"))


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown special characters."""
    return re.sub(r"([_*`\[])", r"\\\1", text)


def _build_briefing_text(
    day_num: int,
    briefing: str,
    choices: list[dict[str, Any]],
    crew_dialogues: list[dict[str, str]],
    language: str,
) -> str:
    """Build the full briefing message text for a player."""
    current = get_current_day(language)
    crew_txt = ""
    if crew_dialogues:
        sep = "\n---\n"
        lines = [
            f"*{d.get('npc', 'NPC')}*: {d.get('dialogue', '')}"
            for d in crew_dialogues
        ]
        crew_txt = f"\n\n*{'Поведение экипажа' if language == 'ru' else 'Crew behavior'}*:\n{sep.join(lines)}"

    acts = "\n\n".join(
        f"{i + 1} - {_escape_md(a.get('text', a.get('description', '')))}"
        for i, a in enumerate(choices)
    )
    return (
        current.get("title", "Day {day}").format(day=day_num)
        + "\n\n"
        + current.get("briefing_header", "{briefing}").format(briefing=briefing)
        + crew_txt
        + "\n\n"
        + current.get("actions", "{actions}").format(actions=acts)
        + "\n\n"
        + current.get("select_action", "")
    )


async def _download_image(url: str, timeout: int = 30) -> bytes | None:
    """Download an image from URL and return raw bytes."""
    try:
        async with aiohttp.ClientSession() as session, session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
                if resp.status == 200:
                    return await resp.read()
                logger.warning(f"[PUSH] Failed to download image: HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"[PUSH] Failed to download image: {e}")
    return None


async def handle_push_briefings(request: web.Request) -> web.Response:
    """Handle POST /push/briefings from game-server-api."""
    bot: Bot = request.app["bot"]
    language: str = request.app.get("language", "ru")
    last_sent: dict[int, int | None] = request.app["last_sent_briefing_day"]
    mark_sent_fn: Callable[[int, int], None] = request.app["mark_sent_fn"]
    create_keyboard_fn: Callable[
        [list[dict[str, Any]]], InlineKeyboardMarkup
    ] = request.app["create_keyboard_fn"]

    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response(
            {"status": "error", "message": f"Invalid JSON: {e}"}, status=400
        )

    day = payload.get("day")
    players = payload.get("players", [])
    bridge_url = payload.get("bridge_image_url")
    mission = payload.get("mission")
    crew_dialogues = payload.get("crew_dialogues", [])
    is_first_turn = payload.get("is_first_turn", False)

    if not day or not players:
        return web.json_response(
            {"status": "error", "message": "Missing day or players"}, status=400
        )

    sent_player_ids: list[int] = []
    already_sent = False

    for player_data in players:
        player_id = player_data.get("player_id")
        if not player_id:
            continue

        # Dedup: skip if already sent for this day
        if last_sent.get(player_id) == day:
            already_sent = True
            continue

        try:
            # 1. Send bridge image + mission (first turn only)
            if is_first_turn and bridge_url:
                bridge_msgs = get_bridge(language)
                caption = bridge_msgs.get("title", "")
                mission_name = (mission or {}).get("name", "")
                if mission_name:
                    caption += "\n\n" + bridge_msgs.get(
                        "mission_header", "Mission: {name}"
                    ).format(name=mission_name)
                img_data = await _download_image(bridge_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="bridge.png")
                    await bot.send_photo(
                        chat_id=player_id,
                        photo=photo,
                        caption=caption,
                        parse_mode="Markdown",
                    )
                else:
                    await bot.send_message(
                        chat_id=player_id,
                        text=caption,
                        parse_mode="Markdown",
                    )

                # Send mission description as separate message
                if mission:
                    desc = mission.get("description", "")
                    if desc:
                        bridge_msgs = get_bridge(language)
                        await bot.send_message(
                            chat_id=player_id,
                            text=bridge_msgs.get(
                                "mission_desc", "{description}"
                            ).format(description=desc),
                            parse_mode="Markdown",
                        )

            # 2. Send comic / scene image
            comic_url = player_data.get("comic_url")
            if comic_url:
                img_data = await _download_image(comic_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="comic.png")
                    await bot.send_photo(chat_id=player_id, photo=photo)

            scene_url = player_data.get("scene_url")
            if scene_url and scene_url != comic_url:
                img_data = await _download_image(scene_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="scene.png")
                    await bot.send_photo(chat_id=player_id, photo=photo)

            # 3. Send briefing text + action choices
            briefing = player_data.get("briefing", "")
            choices = player_data.get("choices", [])
            if briefing and choices:
                text = _build_briefing_text(
                    day, briefing, choices, crew_dialogues, language
                )
                keyboard = create_keyboard_fn(choices)
                await bot.send_message(
                    chat_id=player_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )

            # Mark as sent
            mark_sent_fn(player_id, day)
            sent_player_ids.append(player_id)
            logger.info(f"[PUSH] Sent day {day} briefing to player {player_id}")

        except Exception as e:
            logger.error(f"[PUSH] Failed to send to player {player_id}: {e}")

    status = "already_sent" if already_sent and not sent_player_ids else "ok"
    return web.json_response(
        {
            "status": status,
            "sent": sent_player_ids,
            "already_sent": already_sent,
        }
    )


async def start_push_server(
    bot: Bot,
    language: str = "ru",
    last_sent_briefing_day: dict[int, int | None] | None = None,
    mark_sent_fn: Callable[[int, int], None] | None = None,
    create_keyboard_fn: (
        Callable[[list[dict[str, Any]]], InlineKeyboardMarkup] | None
    ) = None,
) -> web.AppRunner:
    """Start the push HTTP server.

    Args:
        bot: aiogram Bot instance
        language: Bot language code
        last_sent_briefing_day: Shared dict for dedup (from bot.py)
        mark_sent_fn: Function to mark briefing as sent (from bot.py)
        create_keyboard_fn: Function to create action keyboard (from bot.py)

    Returns:
        web.AppRunner for graceful shutdown
    """
    if last_sent_briefing_day is None:
        last_sent_briefing_day = {}
    if mark_sent_fn is None:
        def _noop_mark_sent(pid: int, day: int) -> None:
            pass
        mark_sent_fn = _noop_mark_sent
    if create_keyboard_fn is None:
        def _noop_keyboard(choices: list) -> InlineKeyboardMarkup:
            return InlineKeyboardMarkup(inline_keyboard=[[]])
        create_keyboard_fn = _noop_keyboard

    app = web.Application()
    app["bot"] = bot
    app["language"] = language
    app["last_sent_briefing_day"] = last_sent_briefing_day
    app["mark_sent_fn"] = mark_sent_fn
    app["create_keyboard_fn"] = create_keyboard_fn

    app.router.add_post("/push/briefings", handle_push_briefings)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PUSH_SERVER_PORT)
    await site.start()

    logger.info(f"[PUSH_SERVER] Started on port {PUSH_SERVER_PORT}")
    return runner
