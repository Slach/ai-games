"""HTTP server for receiving push briefings from game-server-api."""

import asyncio
import logging
import os
import re
from collections.abc import Callable
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from aiohttp import web
from language import get_actions, get_bridge, get_current_day, get_notifications

logger = logging.getLogger(__name__)

PUSH_SERVER_PORT = int(os.getenv("PUSH_SERVER_PORT", "9090"))

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
        crew_txt = f"\n\n*{'Поведение экипажа' if language == 'ru' else 'Crew behavior'}*:\n{sep.join(lines)}"

    acts = "\n\n".join(f"{i + 1} - {_escape_md(a.get('text', a.get('description', '')))}" for i, a in enumerate(choices))

    # Use personal_title when available (LLM-generated with name + role + greeting)
    # Fall back to standard title format
    if personal_title:
        title_line = f"🎯 *{personal_title}*"
    else:
        title_line = current.get("title", "Day {day}").format(day=day_num)

    return title_line + "\n\n" + current.get("briefing_header", "{briefing}").format(briefing=briefing) + crew_txt + "\n\n" + current.get("actions", "{actions}").format(actions=acts) + "\n\n" + current.get("select_action", "")


async def _call_with_retry(
    fn: Callable[[], Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
) -> Any:
    """Call an async function with exponential backoff on network errors.

    Retries on aiohttp.ClientError, TimeoutError, OSError (covers proxy
    timeouts, DNS failures, connection resets). All other errors (e.g.
    Telegram API rejecting a message) are re-raised immediately since
    they won't succeed on retry.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except (aiohttp.ClientError, TimeoutError, OSError, asyncio.TimeoutError) as e:
            last_exc = e
            if attempt < max_retries:
                delay = min(base_delay * (2**attempt), max_delay)
                logger.warning(f"[RETRY] Attempt {attempt + 1}/{max_retries + 1} failed: {e}. Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
        except Exception:
            raise  # Non-retryable — re-raise immediately
    logger.error(f"[RETRY] All {max_retries + 1} attempts failed: {last_exc}")
    raise last_exc  # type: ignore[misc]


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
        return await _call_with_retry(_do_download, max_retries=2, base_delay=0.5)
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
                    await _call_with_retry(
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
                    await _call_with_retry(
                        lambda: bot.send_photo(
                            chat_id=player_id,
                            photo=photo,
                            caption=caption,
                            parse_mode="Markdown",
                        )
                    )
                else:
                    await _call_with_retry(
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
                        await _call_with_retry(
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
                    await _call_with_retry(
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
                await _call_with_retry(
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
                    caption_text = personal_title or (("🎯 Ход {day} — {role}" if language == "ru" else "🎯 Turn {day} — {role}").format(day=day, role=player_data.get("role", "")))
                    await _call_with_retry(
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
                    await _call_with_retry(lambda: bot.send_photo(chat_id=player_id, photo=photo))

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
                await _call_with_retry(
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
            await _call_with_retry(
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

    # Build outcome message text
    current_msgs = get_current_day(language)
    outcome_title = current_msgs.get("outcome_title", "Day {day} - Outcome").format(day=day)
    parts = [outcome_title, "", outcome_text]

    if ship_status:
        if language == "ru":
            status_text = "\U0001f6a2 Корабль цел" if ship_status == "alive" else "\U0001f4a5 Корабль уничтожен!"
        else:
            status_text = "\U0001f6a2 Ship is intact" if ship_status == "alive" else "\U0001f4a5 Ship destroyed!"
        parts.append("")
        parts.append(status_text)

    # Crew count: "9 из 10 членов экипажа живы" or "10 / 10 crew alive"
    if total_crew_count is not None and alive_crew_count is not None:
        parts.append("")
        if language == "ru":
            parts.append(f"\U0001f465 {alive_crew_count} из {total_crew_count} {'членов экипажа живы' if alive_crew_count > 1 else 'член экипажа жив'}")
        else:
            parts.append(f"\U0001f465 {alive_crew_count} / {total_crew_count} crew alive")

    if death_notices:
        if language == "ru":
            parts.append("")
            parts.append("\u2620 \u041f\u043e\u0442\u0435\u0440\u0438 \u044d\u043a\u0438\u043f\u0430\u0436\u0430:")
        else:
            parts.append("")
            parts.append("\u2620 Crew losses:")
        for notice in death_notices:
            role = notice.get("role", "")
            name = notice.get("name", "")
            parts.append(f"\u2022 {role} — {name}")

    if mission_progress:
        parts.append("")
        if language == "ru":
            parts.append("\U0001f3c6 \u041f\u0440\u043e\u0433\u0440\u0435\u0441\u0441 \u043c\u0438\u0441\u0441\u0438\u0438:")
        else:
            parts.append("\U0001f3c6 Mission progress:")
        for entry in mission_progress:
            stage = entry.get("stage", "?")
            points = entry.get("points", 0)
            direction = "\U0001f7e2 +" if points > 0 else ("\U0001f534 " if points < 0 else "\u26aa ")
            parts.append(f"  {direction}{points} \u044d\u0442\u0430\u043f {stage}" if language == "ru" else f"  {direction}{points} stage {stage}")

    if ship_systems_offline:
        parts.append("")
        offline_list = ", ".join(ship_systems_offline)
        if language == "ru":
            parts.append(f"\U0001f6a7 \u0421\u0438\u0441\u0442\u0435\u043c\u044b \u043e\u0442\u043a\u043b\u044e\u0447\u0435\u043d\u044b: {offline_list}")
        else:
            parts.append(f"\U0001f6a7 Systems offline: {offline_list}")

    if injury_notices:
        parts.append("")
        if language == "ru":
            parts.append("\U0001f915 \u0420\u0430\u043d\u0435\u043d\u044b\u0435:")
        else:
            parts.append("\U0001f915 Injured:")
        for notice in injury_notices:
            name = notice.get("name", "")
            role = notice.get("role", "")
            severity = notice.get("severity", "")
            severity_label = {
                "critical": "\U0001f534 \u043a\u0440\u0438\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0435" if language == "ru" else "critical",
                "moderate": "\U0001f7e1 \u0441\u0440\u0435\u0434\u043d\u0435\u0435" if language == "ru" else "moderate",
                "minor": "\U0001f7e2 \u043b\u0451\u0433\u043a\u043e\u0435" if language == "ru" else "minor",
            }.get(severity, severity)
            parts.append(f"\u2022 {role} — {name} ({severity_label})")

    if personal_outcomes:
        parts.append("")
        if language == "ru":
            parts.append("\U0001f3ac \u041f\u043e\u0441\u043b\u0435\u0434\u0441\u0442\u0432\u0438\u044f \u0445\u043e\u0434\u0430:")
        else:
            parts.append("\U0001f3ac Turn consequences:")
        for po in personal_outcomes:
            char_name = po.get("character_name", "")
            char_role = po.get("role", "")
            outcome_txt = po.get("outcome_text", "")
            if char_name and outcome_txt:
                parts.append(f"\u2022 {char_name} ({char_role}): {outcome_txt}")

    outcome_message = "\n".join(parts)

    sent_player_ids: list[int] = []

    for player_id in alive_players:
        # Per-player dedup: skip if this player already got outcome for this day
        if last_sent_per_player.get(player_id) == day:
            continue

        try:
            # Send outcome image first if available
            if outcome_image_url:
                img_data = await _download_image(outcome_image_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="outcome_image.png")
                    await _call_with_retry(
                        lambda: bot.send_photo(
                            chat_id=player_id,
                            photo=photo,
                        )
                    )

            # Send outcome narrative
            await _call_with_retry(
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


async def handle_health(request: web.Request) -> web.Response:
    """Handle GET /health for health checks."""
    return web.json_response({"status": "ok"})


async def start_push_server(
    bot: Bot,
    language: str = "ru",
    last_sent_briefing_day: dict[int, int | None] | None = None,
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
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PUSH_SERVER_PORT)
    await site.start()

    logger.info(f"[PUSH_SERVER] Started on port {PUSH_SERVER_PORT}")
    return runner
