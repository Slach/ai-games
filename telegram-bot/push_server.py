"""HTTP server for receiving push briefings from game-server-api."""

import asyncio
import logging
import os
import re
from collections.abc import Callable
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web
from language import get_actions, get_bridge, get_current_day, get_notifications, get_push_outcome
from retry import call_with_retry

logger = logging.getLogger(__name__)


class _HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


logging.getLogger("aiohttp.access").addFilter(_HealthCheckFilter())


try:
    PUSH_SERVER_PORT = int(os.getenv("PUSH_SERVER_PORT", "9090"))
except (ValueError, TypeError):
    logger.warning("Invalid PUSH_SERVER_PORT env var, using default 9090")
    PUSH_SERVER_PORT = 9090

try:
    GAME_MASTER_ID = int(os.getenv("TELEGRAM_BOT_GAME_MASTER_ID", "0"))
except (ValueError, TypeError):
    logger.warning("Invalid TELEGRAM_BOT_GAME_MASTER_ID env var, using default 0")
    GAME_MASTER_ID = 0

# Track pending action image deliveries so /push/outcome can wait for
# action images to be sent to Telegram BEFORE the outcome message.
# Key: (player_id, day) -> asyncio.Event()
_pending_action_events: dict[tuple[int, int], asyncio.Event] = {}


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown special characters."""
    return re.sub(r"([_*`\[])", r"\\\1", text)


def _build_briefing_text(
    day_num: int,
    briefing: str,
    choices: list[dict[str, Any]],
    crew_dialogues: list[dict[str, str]],
    language: str,
    personal_title: str = "",
) -> str:
    """Build the full briefing message text for a player.

    Uses personal_title (LLM-generated greeting with name+role) as the header
    when available, falling back to the standard "Day {day}" title.
    """
    current = get_current_day(language)
    crew_txt = ""
    if crew_dialogues:
        sep = "\n---\n"
        lines = [f"*{d.get('npc', 'NPC')}*: {d.get('dialogue', '')}" for d in crew_dialogues]
        outcome_msgs = get_push_outcome(language)
        crew_txt = f"\n\n*{outcome_msgs['crew_behavior_header']}*:\n{sep.join(lines)}"

    acts = "\n\n".join(f"{i + 1} - {_escape_md(a.get('text', a.get('description', '')))}" for i, a in enumerate(choices))

    # Use personal_title when available (LLM-generated with name + role + greeting)
    # Fall back to standard title format
    if personal_title:
        title_line = f"🎯 *{personal_title}*"
    else:
        title_line = current.get("title", "Day {day}").format(day=day_num)

    return title_line + "\n\n" + current.get("briefing_header", "{briefing}").format(briefing=briefing) + crew_txt + "\n\n" + current.get("actions", "{actions}").format(actions=acts) + "\n\n" + current.get("select_action", "")


async def _download_image(url: str, timeout: int = 30) -> bytes | None:
    """Download an image from URL and return raw bytes."""

    async def _do_download():
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
        ):
            if resp.status == 200:
                return await resp.read()
            raise aiohttp.ClientResponseError(
                resp.request_info,
                resp.history,
                status=resp.status,
                message=f"HTTP {resp.status}",
            )

    try:
        return await call_with_retry(_do_download, max_retries=2, base_delay=0.5)
    except Exception as e:
        logger.warning(f"[PUSH] Failed to download image: {e}")
    return None


async def handle_push_briefings(request: web.Request) -> web.Response:
    """Handle POST /push/briefings from game-server-api."""
    bot: Bot = request.app["bot"]
    language: str = request.app.get("language", "ru")
    last_sent: dict[int, int | None] = request.app["last_sent_briefing_day"]
    mark_sent_fn: Callable[[int, int], None] = request.app["mark_sent_fn"]
    create_keyboard_fn: Callable[[list[dict[str, Any]]], InlineKeyboardMarkup] = request.app["create_keyboard_fn"]

    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)

    day = payload.get("day")
    players = payload.get("players", [])
    bridge_url = payload.get("bridge_image_url")
    mission = payload.get("mission")
    crew_dialogues = payload.get("crew_dialogues", [])
    is_first_turn = payload.get("is_first_turn", False)
    force_resend = payload.get("force_resend", False)
    global_narrative = payload.get("global_narrative", "")
    was_restarted = payload.get("was_restarted", False)
    language = payload.get("language", language)

    if not day or not players:
        return web.json_response({"status": "error", "message": "Missing day or players"}, status=400)

    sent_player_ids: list[int] = []
    already_sent = False

    for player_data in players:
        player_id = player_data.get("player_id")
        if not player_id:
            continue

        # Dedup: skip if already sent for this day (unless force_resend)
        if not force_resend and last_sent.get(player_id) == day:
            already_sent = True
            continue

        try:
            # 0. Send "game restarted" notification (first turn after restart)
            if was_restarted:
                notif_msgs = get_notifications(language)
                restart_msg = notif_msgs.get("game_restarted", "")
                if restart_msg:
                    await call_with_retry(
                        lambda: bot.send_message(
                            chat_id=player_id,
                            text=restart_msg,
                            parse_mode="Markdown",
                        )
                    )

            # 1. Send bridge image + mission (first turn only)
            if is_first_turn and bridge_url:
                bridge_msgs = get_bridge(language)
                caption = bridge_msgs.get("title", "")
                mission_name = (mission or {}).get("name", "")
                if mission_name:
                    caption += "\n\n" + bridge_msgs.get("mission_header", "Mission: {name}").format(name=mission_name)
                img_data = await _download_image(bridge_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="bridge.png")
                    await call_with_retry(
                        lambda: bot.send_photo(
                            chat_id=player_id,
                            photo=photo,
                            caption=caption,
                            parse_mode="Markdown",
                        )
                    )
                else:
                    await call_with_retry(
                        lambda: bot.send_message(
                            chat_id=player_id,
                            text=caption,
                            parse_mode="Markdown",
                        )
                    )

                # Send mission description as separate message
                if mission:
                    desc = mission.get("description", "")
                    if desc:
                        bridge_msgs = get_bridge(language)
                        await call_with_retry(
                            lambda: bot.send_message(
                                chat_id=player_id,
                                text=bridge_msgs.get("mission_desc", "{description}").format(description=desc),
                                parse_mode="Markdown",
                            )
                        )

            # 2. Send scene image (common for all players) before global narrative
            scene_url = player_data.get("scene_url")
            if scene_url:
                img_data = await _download_image(scene_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="scene.png")
                    current_msgs = get_current_day(language)
                    intro_title = current_msgs.get("global_intro_title", "Turn {day}").format(day=day)
                    # If global_narrative is long enough to need a separate
                    # text message (step 3), only put the title in the caption
                    # to avoid showing the same "Ход N — Общая вводная" twice.
                    caption = f"*{intro_title}*"
                    narrative_too_long = global_narrative and len(global_narrative) > 900
                    if not narrative_too_long and global_narrative:
                        caption += f"\n\n{global_narrative}"
                    await call_with_retry(
                        lambda: bot.send_photo(
                            chat_id=player_id,
                            photo=photo,
                            caption=caption,
                            parse_mode="Markdown",
                        )
                    )

            # 3. Send global narrative as separate text (if too long for caption)
            if global_narrative and len(global_narrative) > 900:
                current_msgs = get_current_day(language)
                intro_title = current_msgs.get("global_intro_title", "Turn {day}").format(day=day)
                await call_with_retry(
                    lambda: bot.send_message(
                        chat_id=player_id,
                        text=f"*{intro_title}*\n\n{global_narrative}",
                        parse_mode="Markdown",
                    )
                )

            # 4. Send character image (per-player personal intro image)
            character_image_url = player_data.get("character_image_url")
            personal_title = player_data.get("personal_title", "")

            if character_image_url:
                img_data = await _download_image(character_image_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="character.png")
                    # Use personal_title as caption, or build fallback
                    outcome_msgs = get_push_outcome(language)
                    caption_text = personal_title or (outcome_msgs["character_caption"]).format(day=day, role=player_data.get("role", ""))
                    await call_with_retry(
                        lambda: bot.send_photo(
                            chat_id=player_id,
                            photo=photo,
                            caption=f"*{caption_text}*",
                            parse_mode="Markdown",
                        )
                    )

            # 5. Send previous action image (if any)
            chosen_action_url = player_data.get("chosen_action_url")
            if chosen_action_url:
                img_data = await _download_image(chosen_action_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="action.png")
                    await call_with_retry(lambda: bot.send_photo(chat_id=player_id, photo=photo))

            # 6. Send briefing text + action choices
            briefing = player_data.get("briefing", "")
            choices = player_data.get("choices", [])
            if briefing and choices:
                text = _build_briefing_text(
                    day,
                    briefing,
                    choices,
                    crew_dialogues,
                    language,
                    personal_title=personal_title,
                )
                keyboard = create_keyboard_fn(choices)
                await call_with_retry(
                    lambda: bot.send_message(
                        chat_id=player_id,
                        text=text,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                )

            # Mark as sent
            mark_sent_fn(player_id, day)
            sent_player_ids.append(player_id)
            logger.info(f"[PUSH] Sent day {day} briefing to player {player_id}")

        except Exception:
            pass  # Logged with full detail inside _call_with_retry

    status = "already_sent" if already_sent and not sent_player_ids else "ok"
    return web.json_response(
        {
            "status": status,
            "sent": sent_player_ids,
            "already_sent": already_sent,
        }
    )


async def handle_push_player_chosen_action(request: web.Request) -> web.Response:
    """Handle POST /push/player-action from game-server-api.

    Delivers a chosen action image to the player who performed the action.
    This is called after fire-and-forget action image generation completes.
    Payload: {"player_id": int, "day": int, "chosen_action_url": str, "game_id": str}
    """
    bot: Bot = request.app["bot"]
    language: str = request.app.get("language", "ru")

    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)

    # Use game's language from payload when available
    language = payload.get("language", language)

    player_id = payload.get("player_id")
    day = payload.get("day")
    chosen_action_url = payload.get("chosen_action_url")

    if not player_id or not day or not chosen_action_url:
        return web.json_response(
            {
                "status": "error",
                "message": "Missing player_id, day or chosen_action_url",
            },
            status=400,
        )

    # Signal that this action image is being processed — outcome handler will wait.
    # Use existing event if outcome handler already created a placeholder (race).
    event_key = (player_id, day)
    _pending_action_events.setdefault(event_key, asyncio.Event())

    try:
        logger.info(f"[PUSH_ACTION] Sending action image to player {player_id} for day {day}")

        # Download and send the action image
        img_data = await _download_image(chosen_action_url)
        if img_data:
            photo = BufferedInputFile(img_data, filename="action_image.png")
            msgs = get_actions(language)
            action_text = payload.get("action_text", "")
            if action_text:
                caption = action_text
            else:
                caption = msgs.get("action_caption", "")
            await call_with_retry(
                lambda: bot.send_photo(
                    chat_id=player_id,
                    photo=photo,
                    caption=caption,
                )
            )
            logger.info(f"[PUSH_ACTION] Action image delivered to player {player_id}")
        else:
            logger.warning(f"[PUSH_ACTION] Failed to download image for player {player_id}")

        return web.json_response({"status": "ok"})

    except Exception as e:
        # Logged with full detail inside _call_with_retry
        return web.json_response({"status": "error", "message": str(e)}, status=500)

    finally:
        # Signal that this action image has been processed (success or failure)
        if event_key in _pending_action_events:
            _pending_action_events[event_key].set()


async def handle_push_outcome(request: web.Request) -> web.Response:
    """Handle POST /push/outcome from game-server-api.

    Delivers the combined day outcome (narrative + status + image) to all alive players.
    This is called after _analyze_day_outcome completes.

    Dedup: tracks (player_id, day) pairs so that retries or duplicate calls
    do not send the same outcome twice to the same player.
    New players (not yet tracked) still receive the outcome on retry.
    """
    bot: Bot = request.app["bot"]
    language: str = request.app.get("language", "ru")
    last_sent_per_player: dict[int, int] = request.app.setdefault("last_sent_outcome_day", {})

    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)

    # Use game's language from payload when available
    language = payload.get("language", language)

    day = payload.get("day")

    outcome_text = payload.get("outcome_text", "")
    alive_players = payload.get("alive_players", [])
    outcome_image_url = payload.get("outcome_image_url")
    ship_status = payload.get("ship_status")
    death_notices = payload.get("death_notices")
    injury_notices = payload.get("injury_notices")
    personal_outcomes = payload.get("personal_outcomes")
    mission_progress = payload.get("mission_progress")
    ship_hull_integrity = payload.get("ship_hull_integrity")
    ship_shields = payload.get("ship_shields")
    ship_systems_offline = payload.get("ship_systems_offline")
    total_crew_count = payload.get("total_crew_count")
    alive_crew_count = payload.get("alive_crew_count")
    action_images = payload.get("action_images", [])

    if not day or not alive_players:
        return web.json_response(
            {"status": "error", "message": "Missing day or alive_players"},
            status=400,
        )

    # ── Wait for pending action image deliveries ──────────────────
    # Ensures action images arrive at Telegram API BEFORE the outcome
    # message, even when aiohttp processes the outcome request
    # concurrently with the action image request.
    wait_events = []
    for pid in alive_players:
        event_key = (pid, day)
        ev = _pending_action_events.get(event_key)
        if ev is not None:
            wait_events.append(ev)
        else:
            # Create a placeholder that may be set later (race: outcome
            # request arrives before the action image handler started).
            # If the action handler already registered an event, use it.
            _pending_action_events.setdefault(event_key, asyncio.Event())
            ev = _pending_action_events[event_key]
            wait_events.append(ev)

    if wait_events:
        logger.info(f"[PUSH_OUTCOME] Waiting for {len(wait_events)} action image delivery(es) before sending outcome for day {day}")
        ACTION_WAIT_TIMEOUT = 30.0  # Max seconds to wait for action image delivery
        results = await asyncio.gather(
            *[asyncio.wait_for(ev.wait(), timeout=ACTION_WAIT_TIMEOUT) for ev in wait_events],
            return_exceptions=True,
        )
        timed_out = 0
        for r in results:
            if isinstance(r, asyncio.TimeoutError):
                timed_out += 1
        if timed_out:
            logger.warning(f"[PUSH_OUTCOME] {timed_out}/{len(wait_events)} action image delivery(es) timed out, proceeding with outcome for day {day}")
        else:
            logger.info(f"[PUSH_OUTCOME] All action images delivered, sending outcome for day {day}")

    # ── Pre-download action images for the album ─────────────────
    # Download all action images once, so we don't re-download for each player.
    # Maps player_id/npc_key -> BufferedInputFile. Used for the group album.
    _prefetched_action_photos: dict[int | str, BufferedInputFile | None] = {}
    if action_images:
        logger.info(f"[PUSH_OUTCOME] Pre-downloading {len(action_images)} action images for album")
        for img_entry in action_images:
            img_url = img_entry.get("image_url")
            if not img_url:
                continue
            pid = img_entry.get("player_id")
            npc_key = img_entry.get("npc_key")
            key = pid if pid is not None else (npc_key or f"npc_{len(_prefetched_action_photos)}")
            if key in _prefetched_action_photos:
                continue
            img_data = await _download_image(img_url)
            if img_data:
                _prefetched_action_photos[key] = BufferedInputFile(img_data, filename=f"action_{key}.png")
            else:
                _prefetched_action_photos[key] = None
        logger.info(f"[PUSH_OUTCOME] Pre-downloaded {sum(1 for v in _prefetched_action_photos.values() if v)}/{len(action_images)} action images")

    # Build outcome message text
    current_msgs = get_current_day(language)
    outcome_title = current_msgs.get("outcome_title", "Day {day} - Outcome").format(day=day)
    parts = [outcome_title, "", outcome_text]

    outcome_msgs = get_push_outcome(language)

    if ship_status:
        status_text = outcome_msgs["ship_alive"] if ship_status == "alive" else outcome_msgs["ship_destroyed"]
        parts.append("")
        parts.append(status_text)

    # Ship hull and shield status
    if ship_hull_integrity is not None or ship_shields is not None:
        parts.append("")
        hull_str = outcome_msgs["hull"].format(value=ship_hull_integrity)
        shield_str = outcome_msgs["shields"].format(value=ship_shields)
        parts.append(f"{hull_str}  |  {shield_str}")

    # Crew count: "9 из 10 членов экипажа живы" or "10 / 10 crew alive"
    if total_crew_count is not None and alive_crew_count is not None:
        parts.append("")
        crew_key = "crew_alive_one" if alive_crew_count == 1 else "crew_alive"
        parts.append(outcome_msgs[crew_key].format(alive=alive_crew_count, total=total_crew_count))

    if death_notices:
        parts.append("")
        parts.append(outcome_msgs["death_notices_header"])
        for notice in death_notices:
            role = notice.get("role", "")
            name = notice.get("name", "")
            parts.append(f"• {role} — {name}")

    if mission_progress:
        parts.append("")
        parts.append(outcome_msgs["mission_progress_header"])
        for entry in mission_progress:
            stage = entry.get("stage", "?")
            points = entry.get("points", 0)
            direction = "🟢 +" if points > 0 else ("🔴 " if points < 0 else "⚪ ")
            parts.append(outcome_msgs["mission_progress_item"].format(direction=direction, points=points, stage=stage))

    if ship_systems_offline:
        parts.append("")
        offline_list = ", ".join(ship_systems_offline)
        parts.append(outcome_msgs["systems_offline"].format(list=offline_list))

    if injury_notices:
        parts.append("")
        parts.append(outcome_msgs["injured_header"])
        for notice in injury_notices:
            name = notice.get("name", "")
            role = notice.get("role", "")
            severity = notice.get("severity", "")
            severity_map = {
                "critical": outcome_msgs["severity_critical"],
                "moderate": outcome_msgs["severity_moderate"],
                "minor": outcome_msgs["severity_minor"],
            }
            severity_label = severity_map.get(severity, severity)
            parts.append(f"• {role} — {name} ({severity_label})")

    if personal_outcomes:
        parts.append("")
        parts.append(outcome_msgs["personal_outcomes_header"])
        for po in personal_outcomes:
            char_name = po.get("character_name", "")
            char_role = po.get("role", "")
            outcome_txt = po.get("outcome_text", "")
            if char_name and outcome_txt:
                parts.append(f"• {char_name} ({char_role}): {outcome_txt}")
                parts.append("")  # blank line between items

    outcome_message = "\n".join(parts)

    sent_player_ids: list[int] = []

    # ── Helper: send album of OTHER players' and NPCs' actions ─────
    async def _send_others_album(target_player_id: int):
        """Send a media group album of actions by other players/NPCs (not this player)."""
        if not action_images:
            return

        # Build InputMediaPhoto list: exclude target player's own actions
        media_items = []
        for img_entry in action_images:
            pid = img_entry.get("player_id")
            caption = img_entry.get("caption", "")
            img_url = img_entry.get("image_url")
            if not img_url:
                continue
            # Skip this player's own action — they already got it via push_player_chosen_action
            if pid == target_player_id:
                continue
            # Get pre-downloaded photo
            key: int | str = pid if pid is not None else (img_entry.get("npc_key") or f"npc_{hash(img_url)}")
            photo = _prefetched_action_photos.get(key)
            if photo is None:
                continue
            if not caption:
                outcome_msgs = get_push_outcome(language)
                caption = outcome_msgs["turn_prefix"].format(day=day)
            media_items.append(InputMediaPhoto(media=photo, caption=caption))

        if not media_items:
            return

        # Telegram limits media groups to 10 items per call; split into chunks
        max_group_size = 10
        for chunk_start in range(0, len(media_items), max_group_size):
            chunk = media_items[chunk_start : chunk_start + max_group_size]
            try:
                await call_with_retry(
                    lambda: bot.send_media_group(
                        chat_id=target_player_id,
                        media=chunk,
                    )
                )
                logger.info(f"[PUSH_OUTCOME] Sent actions album (chunk {chunk_start // max_group_size + 1}) to player {target_player_id}: {len(chunk)} images")
            except TelegramBadRequest as album_err:
                logger.warning(f"[PUSH_OUTCOME] Failed to send actions album to player {target_player_id}: {album_err}")
            except Exception as album_err:
                logger.warning(f"[PUSH_OUTCOME] Actions album error for player {target_player_id}: {album_err}")

    for player_id in alive_players:
        # Per-player dedup: skip if this player already got outcome for this day
        if last_sent_per_player.get(player_id) == day:
            continue

        try:
            # 1. Send album of other players' and NPCs' actions FIRST
            await _send_others_album(player_id)

            # 2. Send outcome image
            if outcome_image_url:
                img_data = await _download_image(outcome_image_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="outcome_image.png")
                    await call_with_retry(
                        lambda: bot.send_photo(
                            chat_id=player_id,
                            photo=photo,
                        )
                    )

            # 3. Send outcome narrative
            await call_with_retry(
                lambda: bot.send_message(
                    chat_id=player_id,
                    text=outcome_message,
                )
            )

            sent_player_ids.append(player_id)
            # Mark this player as having received this day's outcome
            last_sent_per_player[player_id] = day
            logger.info(f"[PUSH_OUTCOME] Outcome for day {day} sent to player {player_id}")

        except Exception as e:
            # _call_with_retry already logged network errors with detail;
            # this catches truly unexpected errors from surrounding code
            logger.error(f"[PUSH_OUTCOME] Failed to send outcome to player {player_id}: {e}")

    return web.json_response(
        {
            "status": "ok",
            "sent": sent_player_ids,
        }
    )


async def handle_gm_notification(request: web.Request) -> web.Response:
    """Handle POST /push/gm-notification from game-server-api.

    Sends a Telegram message to the Game Master about turn generation
    progress, success, or failure.
    """
    bot: Bot = request.app["bot"]

    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)

    game_id = payload.get("game_id", "")
    day = payload.get("day", 0)
    status = payload.get("status", "")  # "success" or "error"
    error = payload.get("error", "")
    players = payload.get("players", 0)
    npcs = payload.get("npcs", 0)
    language = payload.get("language", "ru")

    if status == "success":
        if language == "ru":
            msg = f"✅ **Ход {day} игры `{game_id}` сгенерирован!**\n\n🎯 Ход: {day}\n👤 Игроков: {players}\n🤖 NPC: {npcs}\n\nБрифинги разосланы участникам."
        else:
            msg = f"✅ **Turn {day} for game `{game_id}` generated!**\n\n🎯 Turn: {day}\n👤 Players: {players}\n🤖 NPCs: {npcs}\n\nBriefings sent to participants."
    else:
        if language == "ru":
            msg = f"❌ **Ошибка генерации хода {day} игры `{game_id}`**\n\n{error}"
        else:
            msg = f"❌ **Error generating turn {day} for game `{game_id}`**\n\n{error}"

    # Send to GM if we have a bot and GM ID
    if bot and GAME_MASTER_ID > 0:
        try:
            await call_with_retry(
                lambda: bot.send_message(
                    chat_id=GAME_MASTER_ID,
                    text=msg,
                    parse_mode="Markdown",
                )
            )
            logger.info(f"[PUSH_GM] Notification sent to GM for game {game_id} day {day}: {status}")
        except Exception as e:
            logger.error(f"[PUSH_GM] Failed to send GM notification: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)
    else:
        logger.warning(f"[PUSH_GM] Cannot send GM notification: bot={bool(bot)}, GAME_MASTER_ID={GAME_MASTER_ID}")

    return web.json_response({"status": "ok"})


async def handle_health(request: web.Request) -> web.Response:
    """Handle GET /health for health checks."""
    return web.json_response({"status": "ok"})


async def start_push_server(
    bot: Bot,
    language: str = "ru",
    last_sent_briefing_day: dict[int, int] | None = None,
    mark_sent_fn: Callable[[int, int], None] | None = None,
    create_keyboard_fn: (Callable[[list[dict[str, Any]]], InlineKeyboardMarkup] | None) = None,
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
    app.router.add_post("/push/player-action", handle_push_player_chosen_action)
    app.router.add_post("/push/outcome", handle_push_outcome)
    app.router.add_post("/push/gm-notification", handle_gm_notification)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PUSH_SERVER_PORT)
    await site.start()

    logger.info(f"[PUSH_SERVER] Started on port {PUSH_SERVER_PORT}")
    return runner
