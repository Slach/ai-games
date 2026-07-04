"""HTTP server for receiving push briefings from game-server.

Messages are first persisted to SQLite (push_queue table) then delivered
by a background worker.  Per-player ordering is guaranteed: a failed
message blocks subsequent messages for the same player until it succeeds.
"""

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiohttp import web
from database import (
    get_pending_push_messages,
    insert_push_message,
    mark_push_expired,
    mark_push_failed,
    mark_push_sent,
    reset_failed_for_current_turn,
)
from language import (
    get_actions,
    get_bridge,
    get_current_turn,
    get_notifications,
    get_onboarding,
    get_push_outcome,
)
from retry import call_with_retry

logger = logging.getLogger(__name__)


class _HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


logging.getLogger("aiohttp.access").addFilter(_HealthCheckFilter())


# ── Configuration ──────────────────────────────────────────────────

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

GAME_SERVER_URL = os.getenv("GAME_SERVER_URL", "http://game-server:8000")


# ── Per-player ordering & state ────────────────────────────────────

# {player_id: asyncio.Lock} — ensures serial message delivery per player
_player_locks: dict[int, asyncio.Lock] = {}

# {game_id: current_turn} — updated from batch-fetch at startup and on
# each incoming briefing payload.
_current_turns: dict[str, int] = {}

# Fires when _current_turns is first populated (startup batch fetch completes)
_current_turns_ready = asyncio.Event()

# Background worker task
_sender_task: asyncio.Task[None] | None = None

# Track players already auto-kicked (avoid repeated kick API calls)
_blocked_players: set[int] = set()


async def _auto_kick_blocked_player(player_id: int) -> bool:
    """Call game-server to replace blocked player with NPC. Idempotent."""
    if player_id in _blocked_players:
        return True
    _blocked_players.add(player_id)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{GAME_SERVER_URL}/admin/auto-kick-blocked",
                json={"player_id": player_id, "reason": "bot was blocked"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp,
        ):
            if resp.status == 200:
                result = await resp.json()
                logger.warning(
                    "[BLOCKED] Auto-kicked player %d from game %s (status=%s)",
                    player_id,
                    result.get("game_id", "?"),
                    result.get("status", "?"),
                )
                return True
            else:
                logger.error(
                    "[BLOCKED] Auto-kick API returned %d for player %d",
                    resp.status,
                    player_id,
                )
                return False
    except Exception as e:
        logger.error(
            "[BLOCKED] Failed to auto-kick player %d: %s",
            player_id,
            e,
            exc_info=True,
        )
        return False


def _get_player_lock(player_id: int) -> asyncio.Lock:
    """Get or create the per-player asyncio.Lock."""
    if player_id not in _player_locks:
        _player_locks[player_id] = asyncio.Lock()
    return _player_locks[player_id]


async def _fetch_current_turns() -> dict[str, int]:
    """Batch-fetch {game_id: current_turn} from game-server."""
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                f"{GAME_SERVER_URL}/games/current-turns",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                logger.info(
                    "[PUSH] Fetched current turns for %d game(s): %s",
                    len(data),
                    data,
                )
                return {str(k): int(v) for k, v in data.items()}
            else:
                logger.warning("[PUSH] Failed to fetch current turns: HTTP %s", resp.status)
    except Exception as e:
        logger.warning("[PUSH] Failed to fetch current turns: %s", e)
    return {}


# ── Pending action image coordination ──────────────────────────────

# Track pending action image deliveries so /push/outcome can wait for
# action images to be sent to Telegram BEFORE the outcome message.
# Key: (player_id, turn) -> asyncio.Event()
_pending_action_events: dict[tuple[int, int], asyncio.Event] = {}


# ── Helpers ────────────────────────────────────────────────────────


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown special characters."""
    return re.sub(r"([_*`\[])", r"\\\1", text)


def _build_crew_dialogues_text(
    crew_dialogues: list[dict[str, str]],
    language: str,
) -> str:
    """Build a separate message with NPC dialogues."""
    if not crew_dialogues:
        return ""
    sep = "\n---\n"
    lines = [f"*{d.get('npc', 'NPC')}*: {d.get('dialogue', '')}" for d in crew_dialogues]
    outcome_msgs = get_push_outcome(language)
    return f"*{outcome_msgs['crew_behavior_header']}*:\n{sep.join(lines)}"


def _build_briefing_text(
    turn_num: int,
    briefing: str,
    choices: list[dict[str, Any]],
    language: str,
    personal_title: str = "",
) -> str:
    """Build the briefing message text for a player (without NPC dialogues)."""
    current = get_current_turn(language)
    acts = "\n\n".join(f"{i + 1} - {_escape_md(a.get('text', a.get('description', '')))}" for i, a in enumerate(choices))

    if personal_title:
        title_line = f"🎯 *{personal_title}*"
    else:
        title_line = current.get("title", "Turn {turn}").format(turn=turn_num)

    return title_line + "\n\n" + current.get("briefing_header", "{briefing}").format(briefing=briefing) + "\n\n" + current.get("actions", "{actions}").format(actions=acts) + "\n\n" + current.get("select_action", "")


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


# ── Message delivery functions (called by background worker) ──────

_EXPIRED_NOTICE_RU = "К сожалению, мы не смогли доставить вам это сообщение вовремя — ход состоялся без вашего участия."
_EXPIRED_NOTICE_EN = "Unfortunately we were unable to deliver this message in time — the turn took place without your participation."

_EXPIRED_NOTICE: dict[str, str] = {
    "ru": _EXPIRED_NOTICE_RU,
    "en": _EXPIRED_NOTICE_EN,
}


def _is_stale(turn: int | None, game_id: str | None) -> bool:
    """Check whether a message for *turn* of *game_id* is stale."""
    if turn is None or game_id is None:
        return False
    current = _current_turns.get(game_id)
    return current is not None and turn < current


async def _deliver_briefing(
    payload: dict[str, Any],
    bot: Bot,
    language: str,
    create_keyboard_fn: Callable,
    mark_sent_fn: Callable,
    last_sent: dict[int, int],
) -> bool:
    """Deliver a /push/briefings message to all players in the payload."""
    turn = payload.get("turn")
    players = payload.get("players", [])
    bridge_url = payload.get("bridge_image_url")
    mission = payload.get("mission")
    crew_dialogues = payload.get("crew_dialogues", [])
    is_first_turn = payload.get("is_first_turn", False)
    force_resend = payload.get("force_resend", False)
    global_narrative = payload.get("global_narrative", "")
    was_restarted = payload.get("was_restarted", False)
    language = payload.get("language", language)
    game_id = payload.get("game_id", "default_game")

    if not turn or not players:
        return True  # nothing to send → success

    stale = _is_stale(turn, game_id)

    for player_data in players:
        player_id = player_data.get("player_id")
        if not player_id:
            continue

        # Dedup: skip if already sent for this turn
        if not force_resend and last_sent.get(player_id) == turn:
            continue

        try:
            # Game restarted notification
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

            # Bridge image + mission (first turn only)
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

                if mission:
                    desc = mission.get("description", "")
                    if desc:
                        await call_with_retry(
                            lambda: bot.send_message(
                                chat_id=player_id,
                                text=bridge_msgs.get("mission_desc", "{description}").format(description=desc),
                                parse_mode="Markdown",
                            )
                        )

            # Scene image
            scene_url = player_data.get("scene_url")
            if scene_url:
                img_data = await _download_image(scene_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="scene.png")
                    current_msgs = get_current_turn(language)
                    intro_title = current_msgs.get("global_intro_title", "Turn {turn}").format(turn=turn)
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

            # Global narrative as separate text (if too long)
            if global_narrative and len(global_narrative) > 900:
                current_msgs = get_current_turn(language)
                intro_title = current_msgs.get("global_intro_title", "Turn {turn}").format(turn=turn)
                await call_with_retry(
                    lambda: bot.send_message(
                        chat_id=player_id,
                        text=f"*{intro_title}*\n\n{global_narrative}",
                        parse_mode="Markdown",
                    )
                )

            # Character image
            character_image_url = player_data.get("character_image_url")
            personal_title = player_data.get("personal_title", "")
            if character_image_url:
                img_data = await _download_image(character_image_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="character.png")
                    outcome_msgs = get_push_outcome(language)
                    caption_text = personal_title or outcome_msgs["character_caption"].format(turn=turn, role=player_data.get("role", ""))
                    await call_with_retry(
                        lambda: bot.send_photo(
                            chat_id=player_id,
                            photo=photo,
                            caption=f"*{caption_text}*",
                            parse_mode="Markdown",
                        )
                    )

            # Previous action image
            chosen_action_url = player_data.get("chosen_action_url")
            if chosen_action_url:
                img_data = await _download_image(chosen_action_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="action.png")
                    await call_with_retry(lambda: bot.send_photo(chat_id=player_id, photo=photo))

            # Crew dialogues as a separate message (before briefing to avoid 4096 limit)
            crew_text = _build_crew_dialogues_text(crew_dialogues, language)
            if crew_text:
                await call_with_retry(
                    lambda: bot.send_message(
                        chat_id=player_id,
                        text=crew_text,
                        parse_mode="Markdown",
                    )
                )

            # Briefing text + action choices (or expired notice)
            briefing = player_data.get("briefing", "")
            choices = player_data.get("choices", [])
            if briefing and choices:
                if stale:
                    # Send without keyboard, with apology notice
                    text = _build_briefing_text(
                        turn,
                        briefing,
                        choices,
                        language,
                        personal_title=personal_title,
                    )
                    expired_text = _EXPIRED_NOTICE.get(language, _EXPIRED_NOTICE["en"])
                    text += f"\n\n_{_escape_md(expired_text)}_"
                    await call_with_retry(
                        lambda: bot.send_message(
                            chat_id=player_id,
                            text=text,
                            parse_mode="Markdown",
                        )
                    )
                else:
                    text = _build_briefing_text(
                        turn,
                        briefing,
                        choices,
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

            # Mark as sent (track dedup across bot restarts)
            mark_sent_fn(player_id, turn)

        except TelegramForbiddenError:
            logger.warning(
                "[PUSH_BRIEFING] Player %d blocked the bot (forbidden), auto-kicking",
                player_id,
            )
            asyncio.create_task(_auto_kick_blocked_player(player_id))
            continue
        except TelegramBadRequest as e:
            if "USER_IS_BLOCKED" in str(e):
                logger.warning(
                    "[PUSH_BRIEFING] Player %d blocked the bot, auto-kicking",
                    player_id,
                )
                asyncio.create_task(_auto_kick_blocked_player(player_id))
                continue
            logger.error(
                "[PUSH_BRIEFING] TelegramBadRequest for player %d turn %s: %s",
                player_id,
                turn,
                e,
                exc_info=True,
            )
            return False
        except Exception:
            logger.error(
                "[PUSH_BRIEFING] Unexpected error for player %d turn %s",
                player_id,
                turn,
                exc_info=True,
            )
            return False  # One player failed → stop

    return True


async def _deliver_player_action(
    payload: dict[str, Any],
    bot: Bot,
    language: str,
) -> bool:
    """Deliver a /push/player-action message."""
    player_id = payload.get("player_id")
    turn = payload.get("turn")
    chosen_action_url = payload.get("chosen_action_url")
    action_text = payload.get("action_text", "")
    language = payload.get("language", language)

    if not player_id or not turn or not chosen_action_url:
        return True

    event_key = (player_id, turn)
    _pending_action_events.setdefault(event_key, asyncio.Event())

    try:
        img_data = await _download_image(chosen_action_url)
        if img_data:
            photo = BufferedInputFile(img_data, filename="action_image.png")
            msgs = get_actions(language)
            caption = action_text or msgs.get("action_caption", "")
            await call_with_retry(
                lambda: bot.send_photo(
                    chat_id=player_id,
                    photo=photo,
                    caption=caption,
                )
            )
        return True
    except TelegramBadRequest as e:
        if "USER_IS_BLOCKED" in str(e):
            logger.warning(
                "[PUSH_ACTION] Player %d blocked the bot, auto-kicking",
                player_id,
            )
            asyncio.create_task(_auto_kick_blocked_player(player_id))
            return True
        logger.error(
            "[PUSH_ACTION] TelegramBadRequest for player %d turn %s: %s",
            player_id,
            turn,
            e,
            exc_info=True,
        )
        return False
    except Exception:
        logger.error(
            "[PUSH_ACTION] Unexpected error for player %d turn %s",
            player_id,
            turn,
            exc_info=True,
        )
        return False
    finally:
        if event_key in _pending_action_events:
            _pending_action_events[event_key].set()


async def _deliver_outcome(
    payload: dict[str, Any],
    bot: Bot,
    language: str,
    last_sent_per_player: dict[int, int],
    current_player_id: int = 0,
    mark_outcome_sent_fn: Callable[[int, int], None] | None = None,
) -> bool:
    """Deliver a /push/outcome message to all alive players.

    Returns True if *current_player_id* (the player whose push_queue entry
    triggered this call) received the outcome. Errors for other players are
    logged and skipped — one failing player never blocks the rest.
    """
    turn = payload.get("turn")
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
    language = payload.get("language", language)

    if not turn or not alive_players:
        return True

    # Wait for pending action image deliveries
    wait_events = []
    for pid in alive_players:
        event_key = (pid, turn)
        ev = _pending_action_events.get(event_key)
        if ev is not None:
            wait_events.append(ev)
        else:
            _pending_action_events.setdefault(event_key, asyncio.Event())
            ev = _pending_action_events[event_key]
            wait_events.append(ev)

    if wait_events:
        logger.info(
            "[PUSH_OUTCOME] Waiting for %d action image delivery(es) before sending outcome for turn %d",
            len(wait_events),
            turn,
        )
        ACTION_WAIT_TIMEOUT = 30.0
        results = await asyncio.gather(
            *[asyncio.wait_for(ev.wait(), timeout=ACTION_WAIT_TIMEOUT) for ev in wait_events],
            return_exceptions=True,
        )
        timed_out = sum(1 for r in results if isinstance(r, asyncio.TimeoutError))
        if timed_out:
            logger.warning(
                "[PUSH_OUTCOME] %d/%d action image delivery(es) timed out",
                timed_out,
                len(wait_events),
            )

    # Pre-download action images for album
    _prefetched_action_photos: dict[int | str, BufferedInputFile | None] = {}
    if action_images:
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

    # Build outcome message text
    current_msgs = get_current_turn(language)
    outcome_title = current_msgs.get("outcome_title", "Turn {turn} - Outcome").format(turn=turn)
    parts = [outcome_title, "", outcome_text]

    outcome_msgs = get_push_outcome(language)

    if ship_status:
        status_text = outcome_msgs["ship_alive"] if ship_status == "alive" else outcome_msgs["ship_destroyed"]
        parts.append("")
        parts.append(status_text)

    if ship_hull_integrity is not None or ship_shields is not None:
        parts.append("")
        hull_str = outcome_msgs["hull"].format(value=ship_hull_integrity)
        shield_str = outcome_msgs["shields"].format(value=ship_shields)
        parts.append(f"{hull_str}  |  {shield_str}")

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
            if points > 0:
                direction = "🟢 +"
            elif points < 0:
                direction = "🔴 "
            else:
                direction = "⚪ "
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
                parts.append("")

    outcome_message = "\n".join(parts)

    # Helper: send album of other players'/NPCs' actions
    async def _send_others_album(target_player_id: int):
        if not action_images:
            return
        media_items = []
        for img_entry in action_images:
            pid = img_entry.get("player_id")
            caption = img_entry.get("caption", "")
            img_url = img_entry.get("image_url")
            if not img_url:
                continue
            if pid == target_player_id:
                continue
            key: int | str = pid if pid is not None else (img_entry.get("npc_key") or f"npc_{hash(img_url)}")
            photo = _prefetched_action_photos.get(key)
            if photo is None:
                continue
            if not caption:
                caption = outcome_msgs["turn_prefix"].format(turn=turn)
            media_items.append(InputMediaPhoto(media=photo, caption=caption))

        if not media_items:
            return

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
            except TelegramBadRequest as album_err:
                err_str = str(album_err)
                if "USER_IS_BLOCKED" in err_str:
                    asyncio.create_task(_auto_kick_blocked_player(target_player_id))
                logger.warning(
                    "[PUSH_OUTCOME] Failed to send actions album to player %d: %s",
                    target_player_id,
                    album_err,
                )
            except Exception as album_err:
                logger.warning(
                    "[PUSH_OUTCOME] Actions album error for player %d: %s",
                    target_player_id,
                    album_err,
                )

    current_player_delivered = False
    for player_id in alive_players:
        if last_sent_per_player.get(player_id) == turn:
            if player_id == current_player_id:
                current_player_delivered = True
            continue
        try:
            await _send_others_album(player_id)

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

            await call_with_retry(
                lambda: bot.send_message(
                    chat_id=player_id,
                    text=outcome_message,
                )
            )

            last_sent_per_player[player_id] = turn
            if mark_outcome_sent_fn is not None:
                mark_outcome_sent_fn(player_id, turn)
            if player_id == current_player_id:
                current_player_delivered = True
        except TelegramForbiddenError:
            logger.warning(
                "[PUSH_OUTCOME] Player %d blocked the bot, auto-kicking",
                player_id,
            )
            asyncio.create_task(_auto_kick_blocked_player(player_id))
            continue
        except TelegramBadRequest as e:
            if "USER_IS_BLOCKED" in str(e):
                logger.warning(
                    "[PUSH_OUTCOME] Player %d blocked the bot, auto-kicking",
                    player_id,
                )
                asyncio.create_task(_auto_kick_blocked_player(player_id))
                continue
            logger.error(
                "[PUSH_OUTCOME] TelegramBadRequest for player %d (turn %d): %s",
                player_id,
                turn,
                e,
                exc_info=True,
            )
            continue
        except Exception as e:
            logger.error(
                "[PUSH_OUTCOME] Failed for player %d (turn %d): %s",
                player_id,
                turn,
                e,
                exc_info=True,
            )
            continue

    return current_player_delivered


async def _deliver_gm_notification(
    payload: dict[str, Any],
    bot: Bot,
) -> bool:
    """Deliver a /push/gm-notification message."""
    game_id = payload.get("game_id", "")
    turn = payload.get("turn", 0)
    status = payload.get("status", "")
    error = payload.get("error", "")
    players = payload.get("players", 0)
    npcs = payload.get("npcs", 0)
    language = payload.get("language", "ru")

    safe_error = _escape_md(error)
    if status == "success":
        if language == "ru":
            msg = f"✅ **Ход {turn} игры `{game_id}` сгенерирован!**\n\n🎯 Ход: {turn}\n👤 Игроков: {players}\n🤖 NPC: {npcs}\n\nБрифинги разосланы участникам."
        else:
            msg = f"✅ **Turn {turn} for game `{game_id}` generated!**\n\n🎯 Turn: {turn}\n👤 Players: {players}\n🤖 NPCs: {npcs}\n\nBriefings sent to participants."
    else:
        if language == "ru":
            msg = f"❌ **Ошибка генерации хода {turn} игры `{game_id}`**\n\n{safe_error}"
        else:
            msg = f"❌ **Error generating turn {turn} for game `{game_id}`**\n\n{safe_error}"

    if bot and GAME_MASTER_ID > 0:
        try:
            await call_with_retry(
                lambda: bot.send_message(
                    chat_id=GAME_MASTER_ID,
                    text=msg,
                    parse_mode="Markdown",
                )
            )
        except Exception as e:
            logger.error("[PUSH_GM] Failed to send GM notification: %s", e, exc_info=True)
            return False
    return True


async def _deliver_game_over(
    payload: dict[str, Any],
    bot: Bot,
    current_player_id: int = 0,
    last_sent: dict[int, str] | None = None,
) -> bool:
    """Deliver a /push/game-over message to all alive players.

    Returns True if *current_player_id* (the player whose push_queue entry
    triggered this call) received the finale. Errors for other players are
    logged and skipped — one failing player never blocks the rest.

    Uses last_sent dict (player_id → game_id) to avoid sending the same
    game-over to a player multiple times.
    """
    game_id = payload.get("game_id", "")
    if last_sent is None:
        last_sent = {}
    language = payload.get("language", "ru")
    finale_narrative = payload.get("finale_narrative", "")
    finale_image_url = payload.get("finale_image_url")
    outcome_type = payload.get("outcome_type", "defeat")
    alive_players = payload.get("alive_players", [])
    available_games = payload.get("available_games", [])

    if not alive_players:
        return True

    onboarding_msgs = get_onboarding(language)
    if outcome_type == "victory":
        title = onboarding_msgs.get("game_over_victory_title", "GAME OVER")
    else:
        title = onboarding_msgs.get("game_over_defeat_title", "GAME OVER")

    keyboard_buttons = []
    for game in available_games:
        gid = game.get("game_id", "")
        if not gid:
            continue
        name = game.get("name", gid)
        player_count = game.get("player_count", 0)
        lang_flag = "🇷🇺" if game.get("language") == "ru" else "🇬🇧"
        btn_text = f"{lang_flag} {name} ({player_count})"
        keyboard_buttons.append(
            [
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"select_game:{gid}",
                )
            ]
        )

    keyboard_buttons.append(
        [
            InlineKeyboardButton(
                text=onboarding_msgs.get("new_game", "🆕 New Game"),
                callback_data="select_game:new",
            )
        ]
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    continue_text = onboarding_msgs.get("game_over_continue", "")

    current_player_delivered = False
    for player_id in alive_players:
        # Dedup: skip if this game-over was already sent to this player
        if last_sent.get(player_id) == game_id:
            if player_id == current_player_id:
                current_player_delivered = True
            continue
        try:
            if finale_image_url:
                img_data = await _download_image(finale_image_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="finale.png")
                    await call_with_retry(
                        lambda: bot.send_photo(
                            chat_id=player_id,
                            photo=photo,
                            caption=f"*{title}*",
                            parse_mode="Markdown",
                        )
                    )

            full_text = f"*{title}*\n\n{finale_narrative}"
            await call_with_retry(
                lambda: bot.send_message(
                    chat_id=player_id,
                    text=full_text,
                    parse_mode="Markdown",
                )
            )

            if continue_text:
                await call_with_retry(
                    lambda: bot.send_message(
                        chat_id=player_id,
                        text=continue_text,
                        reply_markup=keyboard,
                    )
                )

            if player_id == current_player_id:
                current_player_delivered = True
            last_sent[player_id] = game_id
        except TelegramForbiddenError:
            logger.warning(
                "[PUSH_GAME_OVER] Player %d blocked the bot, auto-kicking",
                player_id,
            )
            asyncio.create_task(_auto_kick_blocked_player(player_id))
            continue
        except TelegramBadRequest as e:
            if "USER_IS_BLOCKED" in str(e):
                logger.warning(
                    "[PUSH_GAME_OVER] Player %d blocked the bot, auto-kicking",
                    player_id,
                )
                asyncio.create_task(_auto_kick_blocked_player(player_id))
                continue
            logger.error(
                "[PUSH_GAME_OVER] TelegramBadRequest for player %d: %s",
                player_id,
                e,
                exc_info=True,
            )
            continue
        except Exception as e:
            logger.error(
                "[PUSH_GAME_OVER] Failed for player %d: %s",
                player_id,
                e,
                exc_info=True,
            )
            continue

    return current_player_delivered


async def _deliver_onboarding_ready(
    payload: dict[str, Any],
    bot: Bot,
) -> bool:
    """Deliver a /push/onboarding-ready message."""
    player_id = payload.get("player_id")
    game_id = payload.get("game_id", "default_game")
    session_id = payload.get("session_id", "")
    language = payload.get("language", "ru")
    question = payload.get("question")
    game_title = payload.get("game_title", "")
    welcome_message = payload.get("welcome_message", "")

    if not player_id:
        return True

    try:
        welcome_text = welcome_message or ""
        if game_title:
            welcome_text = f"**{_escape_md(game_title)}**\n\n{welcome_text}" if welcome_text else f"**{_escape_md(game_title)}**"

        # Send splash image
        splash_sent = False
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    f"{GAME_SERVER_URL}/content/splash-image",
                    params={"game_id": game_id},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp,
            ):
                if resp.status == 200:
                    splash_data = await resp.json()
                    splash_url = splash_data.get("image_url")
                    if splash_url:
                        async with session.get(splash_url, timeout=aiohttp.ClientTimeout(total=30)) as img_resp:
                            if img_resp.status == 200:
                                photo_data = await img_resp.read()
                                photo = BufferedInputFile(photo_data, filename="splash.png")
                                await bot.send_photo(
                                    chat_id=player_id,
                                    photo=photo,
                                    caption=welcome_text,
                                    parse_mode="Markdown",
                                )
                                splash_sent = True
        except Exception as e:
            logger.warning(
                "[PUSH_ONBOARDING] Failed to send splash image for player %d: %s",
                player_id,
                e,
            )

        if not splash_sent and welcome_text:
            await bot.send_message(
                chat_id=player_id,
                text=welcome_text,
                parse_mode="Markdown",
            )

        # Send first question with images
        if question:
            from player_store import update_player_state

            update_player_state(
                player_id,
                onboarding_session_id=session_id,
                game_id=game_id,
                current_question_id=question.get("id", 1),
                current_options=question.get("options", []),
                current_question_text=question.get("text", ""),
                current_question_image_url=question.get("image_url"),
                language=language,
            )

            options = question.get("options", [])
            buttons = []
            for i in range(len(options)):
                buttons.append(
                    InlineKeyboardButton(
                        text=str(i + 1),
                        callback_data=f"onb_ans:{question['id']}:{i}",
                    )
                )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])

            image_url = question.get("image_url")
            question_text = question.get("text", "")
            options_text = "\n\n".join(f"{i + 1}. {_escape_md(o.get('label', o['value']))}" for i, o in enumerate(options))
            full_text = get_onboarding(language)["question_prefix"].format(
                id=question["id"],
                text=_escape_md(question_text),
            )
            if options_text:
                full_text += f"\n\n---\n\n{options_text}"

            if image_url:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=30)) as img_resp:
                            if img_resp.status == 200:
                                photo_data = await img_resp.read()
                                photo = BufferedInputFile(photo_data, filename=f"q_{question['id']}.png")
                                await bot.send_photo(
                                    chat_id=player_id,
                                    photo=photo,
                                    caption=full_text,
                                    parse_mode="Markdown",
                                    reply_markup=keyboard,
                                )
                            else:
                                await bot.send_message(
                                    chat_id=player_id,
                                    text=full_text,
                                    parse_mode="Markdown",
                                    reply_markup=keyboard,
                                )
                except Exception as e:
                    logger.warning(
                        "[PUSH_ONBOARDING] Failed to send question image for player %d: %s",
                        player_id,
                        e,
                    )
                    await bot.send_message(
                        chat_id=player_id,
                        text=full_text,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
            else:
                await bot.send_message(
                    chat_id=player_id,
                    text=full_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )

        return True
    except TelegramBadRequest as e:
        if "USER_IS_BLOCKED" in str(e):
            logger.warning(
                "[PUSH_ONBOARDING] Player %d blocked the bot during onboarding, auto-kicking",
                player_id,
            )
            asyncio.create_task(_auto_kick_blocked_player(player_id))
            return True
        logger.error("[PUSH_ONBOARDING] Telegram error for player %d: %s", player_id, e)
        return False
    except Exception as e:
        logger.error(
            "[PUSH_ONBOARDING] Unexpected error for player %d: %s",
            player_id,
            e,
            exc_info=True,
        )
        return False


# ── Unified message dispatcher ─────────────────────────────────────

_DELIVER_FNS = {
    "briefing": _deliver_briefing,
    "action": _deliver_player_action,
    "outcome": _deliver_outcome,
    "gm_notification": _deliver_gm_notification,
    "game_over": _deliver_game_over,
    "onboarding": _deliver_onboarding_ready,
}


async def _dispatch_one(
    row: dict[str, Any],
    bot: Bot,
    language: str,
    create_keyboard_fn: Callable,
    mark_sent_fn: Callable,
    last_sent_briefing: dict[int, int],
    last_sent_outcome: dict[int, int],
    last_sent_game_over: dict[int, str],
    mark_outcome_sent_fn: Callable[[int, int], None] | None = None,
) -> bool:
    """Try to deliver a single push_queue row. Returns True on success."""
    push_type = row["push_type"]
    push_id = row["id"]
    player_id = row["player_id"]

    try:
        payload = json.loads(row["payload"])
    except (ValueError, KeyError):
        logger.error("[PUSH] Corrupt payload in push_queue #%d, marking failed", push_id)
        mark_push_failed(push_id, "Corrupt JSON payload")
        return True  # Don't block — this message will never succeed

    deliver_fn = _DELIVER_FNS.get(push_type)
    if deliver_fn is None:
        logger.error("[PUSH] Unknown push_type '%s' for #%d", push_type, push_id)
        mark_push_failed(push_id, f"Unknown push_type: {push_type}")
        return True

    try:
        if push_type == "briefing":
            success = await deliver_fn(
                payload,
                bot,
                language,
                create_keyboard_fn,
                mark_sent_fn,
                last_sent_briefing,
            )
        elif push_type == "outcome":
            success = await deliver_fn(payload, bot, language, last_sent_outcome, player_id, mark_outcome_sent_fn)
        elif push_type == "action":
            success = await deliver_fn(payload, bot, language)
        elif push_type == "game_over":
            success = await deliver_fn(payload, bot, player_id, last_sent_game_over)
        else:
            success = await deliver_fn(payload, bot)

        if success:
            # Staleness check: mark as expired if turn is behind
            turn = row.get("turn")
            game_id = row.get("game_id")
            if _is_stale(turn, game_id) and push_type == "briefing":
                mark_push_expired(push_id)
            else:
                mark_push_sent(push_id)
            logger.info(
                "[PUSH] Delivered #%d (type=%s, player=%s, turn=%s)",
                push_id,
                push_type,
                player_id,
                turn,
            )
            return True
        else:
            mark_push_failed(push_id, f"Delivery failed for {push_type} to player {player_id}")
            logger.warning("[PUSH] Failed to deliver #%d", push_id)
            return False
    except TelegramForbiddenError:
        logger.warning(
            "[PUSH] Player %d blocked the bot (forbidden, caught in _dispatch_one), auto-kicking",
            player_id,
        )
        asyncio.create_task(_auto_kick_blocked_player(player_id))
        mark_push_sent(push_id)  # Don't retry
        return True
    except TelegramBadRequest as e:
        err_str = str(e)
        if "USER_IS_BLOCKED" in err_str:
            logger.warning(
                "[PUSH] Player %d blocked the bot (caught in _dispatch_one), auto-kicking",
                player_id,
            )
            asyncio.create_task(_auto_kick_blocked_player(player_id))
            mark_push_sent(push_id)  # Don't retry
            return True
        logger.error("[PUSH] TelegramBadRequest delivering #%d: %s", push_id, e, exc_info=True)
        mark_push_failed(push_id, str(e))
        return False
    except Exception as e:
        logger.error("[PUSH] Exception delivering #%d: %s", push_id, e, exc_info=True)
        mark_push_failed(push_id, str(e))
        return False


# ── Background sender loop ─────────────────────────────────────────


async def _sender_loop(
    bot: Bot,
    language: str,
    create_keyboard_fn: Callable,
    mark_sent_fn: Callable,
    last_sent_briefing: dict[int, int],
    last_sent_outcome: dict[int, int],
    last_sent_game_over: dict[int, str],
    mark_outcome_sent_fn: Callable[[int, int], None] | None = None,
    poll_interval: float = 1.0,
) -> None:
    """Background task that drains push_queue in per-player order."""
    logger.info("[PUSH_SENDER] Background sender started")
    while True:
        try:
            pending = get_pending_push_messages()
            if not pending:
                await asyncio.sleep(poll_interval)
                continue

            # Group by player_id, preserving insertion order
            by_player: dict[int, list[dict]] = {}
            for msg in pending:
                by_player.setdefault(msg["player_id"], []).append(msg)

            for player_id, messages in by_player.items():
                lock = _get_player_lock(player_id)
                async with lock:
                    for msg in messages:
                        # Re-check status — may have been processed
                        # by another concurrent delivery.
                        if msg["status"] != "pending":
                            continue

                        ok = await _dispatch_one(
                            msg,
                            bot,
                            language,
                            create_keyboard_fn,
                            mark_sent_fn,
                            last_sent_briefing,
                            last_sent_outcome,
                            last_sent_game_over,
                            mark_outcome_sent_fn,
                        )
                        if not ok:
                            # Stop processing this player — order preserved
                            break

        except Exception:
            logger.error(
                "[PUSH_SENDER] Unexpected error in sender loop",
                exc_info=True,
            )

        await asyncio.sleep(poll_interval)


# ── Startup flush (before HTTP server starts) ──────────────────────


async def _startup_flush(
    bot: Bot,
    language: str,
    create_keyboard_fn: Callable,
    mark_sent_fn: Callable,
    last_sent_briefing: dict[int, int],
    last_sent_outcome: dict[int, int],
    last_sent_game_over: dict[int, str],
    mark_outcome_sent_fn: Callable[[int, int], None] | None = None,
) -> None:
    """Synchronously drain all pending messages before starting HTTP."""
    logger.info("[PUSH_STARTUP] Fetching current turns from game-server...")
    turns = await _fetch_current_turns()
    _current_turns.update(turns)
    _current_turns_ready.set()

    # Retry failed messages whose turn is still current
    retried_total = 0
    for game_id, turn in turns.items():
        retried = reset_failed_for_current_turn(game_id, turn)
        if retried:
            retried_total += retried
            logger.info(
                "[PUSH_STARTUP] Reset %d failed message(s) to pending for game %s turn %d",
                retried,
                game_id,
                turn,
            )
    if retried_total:
        logger.info("[PUSH_STARTUP] Total %d failed message(s) reset for retry", retried_total)

    pending = get_pending_push_messages()
    if not pending:
        logger.info("[PUSH_STARTUP] No pending messages to flush")
        return

    logger.info("[PUSH_STARTUP] Flushing %d pending message(s)...", len(pending))

    by_player: dict[int, list[dict]] = {}
    for msg in pending:
        by_player.setdefault(msg["player_id"], []).append(msg)

    for player_id, messages in by_player.items():
        for msg in messages:
            ok = await _dispatch_one(
                msg,
                bot,
                language,
                create_keyboard_fn,
                mark_sent_fn,
                last_sent_briefing,
                last_sent_outcome,
                last_sent_game_over,
                mark_outcome_sent_fn,
            )
            if not ok:
                logger.warning(
                    "[PUSH_STARTUP] Stopped processing player %d after failed message #%d — %d message(s) remain pending",
                    player_id,
                    msg["id"],
                    len(messages),
                )
                break

    logger.info("[PUSH_STARTUP] Startup flush complete")


# ── HTTP Handlers (save to DB, return immediately) ─────────────────


async def handle_push_briefings(request: web.Request) -> web.Response:
    """Handle POST /push/briefings — save to push_queue, return immediately."""
    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)

    turn = payload.get("turn")
    players = payload.get("players", [])
    game_id = payload.get("game_id", "default_game")

    if not turn or not players:
        return web.json_response({"status": "error", "message": "Missing turn or players"}, status=400)

    # Update current_turns cache
    _current_turns[game_id] = turn

    inserted = 0
    for player_data in players:
        player_id = player_data.get("player_id")
        if not player_id:
            continue

        # Build a per-player payload so the sender can process
        # individual players without iterating over the full list.
        per_player_payload = {
            **payload,
            "players": [player_data],
        }
        insert_push_message(
            player_id=player_id,
            push_type="briefing",
            payload=json.dumps(per_player_payload, ensure_ascii=False),
            turn=turn,
            game_id=game_id,
        )
        inserted += 1

    logger.info(
        "[PUSH] Queued %d briefing(s) for turn %d, game %s",
        inserted,
        turn,
        game_id,
    )
    return web.json_response({"status": "ok", "queued": inserted})


async def handle_push_player_chosen_action(request: web.Request) -> web.Response:
    """Handle POST /push/player-action — save to push_queue, return immediately."""
    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)

    player_id = payload.get("player_id")
    turn = payload.get("turn")
    game_id = payload.get("game_id", "default_game")
    chosen_action_url = payload.get("chosen_action_url")

    if not player_id or not turn or not chosen_action_url:
        return web.json_response(
            {"status": "error", "message": "Missing player_id, turn or chosen_action_url"},
            status=400,
        )

    insert_push_message(
        player_id=player_id,
        push_type="action",
        payload=json.dumps(payload, ensure_ascii=False),
        turn=turn,
        game_id=game_id,
    )

    logger.info(
        "[PUSH_ACTION] Queued action image for player %d, turn %d",
        player_id,
        turn,
    )
    return web.json_response({"status": "ok", "queued": 1})


async def handle_push_outcome(request: web.Request) -> web.Response:
    """Handle POST /push/outcome — save to push_queue, return immediately."""
    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)

    turn = payload.get("turn")
    alive_players = payload.get("alive_players", [])
    game_id = payload.get("game_id", "default_game")

    if not turn or not alive_players:
        return web.json_response(
            {"status": "error", "message": "Missing turn or alive_players"},
            status=400,
        )

    inserted = 0
    for player_id in alive_players:
        insert_push_message(
            player_id=player_id,
            push_type="outcome",
            payload=json.dumps(payload, ensure_ascii=False),
            turn=turn,
            game_id=game_id,
        )
        inserted += 1

    logger.info(
        "[PUSH_OUTCOME] Queued %d outcome(s) for turn %d, game %s",
        inserted,
        turn,
        game_id,
    )
    return web.json_response({"status": "ok", "queued": inserted})


async def handle_gm_notification(request: web.Request) -> web.Response:
    """Handle POST /push/gm-notification — save to push_queue, return immediately."""
    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)

    game_id = payload.get("game_id", "")
    turn = payload.get("turn", 0)

    insert_push_message(
        player_id=GAME_MASTER_ID,
        push_type="gm_notification",
        payload=json.dumps(payload, ensure_ascii=False),
        turn=turn,
        game_id=game_id,
    )

    logger.info(
        "[PUSH_GM] Queued GM notification for game %s, turn %s",
        game_id,
        turn,
    )
    return web.json_response({"status": "ok", "queued": 1})


async def handle_push_game_over(request: web.Request) -> web.Response:
    """Handle POST /push/game-over — save to push_queue, return immediately."""
    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)

    alive_players = payload.get("alive_players", [])
    game_id = payload.get("game_id", "default_game")

    if not alive_players:
        return web.json_response({"status": "error", "message": "Missing alive_players"}, status=400)

    inserted = 0
    for player_id in alive_players:
        insert_push_message(
            player_id=player_id,
            push_type="game_over",
            payload=json.dumps(payload, ensure_ascii=False),
            game_id=game_id,
        )
        inserted += 1

    logger.info(
        "[PUSH_GAME_OVER] Queued %d game-over message(s) for game %s",
        inserted,
        game_id,
    )
    return web.json_response({"status": "ok", "queued": inserted})


async def handle_push_onboarding_ready(request: web.Request) -> web.Response:
    """Handle POST /push/onboarding-ready — save to push_queue, return immediately."""
    try:
        payload = await request.json()
    except Exception:
        logger.warning("[PUSH_ONBOARDING] Invalid JSON in request", exc_info=True)
        return web.json_response({"error": "Invalid JSON"}, status=400)

    player_id = payload.get("player_id")
    game_id = payload.get("game_id", "default_game")

    if not player_id:
        return web.json_response({"error": "Missing player_id"}, status=400)

    insert_push_message(
        player_id=player_id,
        push_type="onboarding",
        payload=json.dumps(payload, ensure_ascii=False),
        game_id=game_id,
    )

    logger.info(
        "[PUSH_ONBOARDING] Queued onboarding-ready for player %d, game %s",
        player_id,
        game_id,
    )
    return web.json_response({"status": "ok", "player_id": player_id})


async def handle_health(request: web.Request) -> web.Response:
    """Handle GET /health for health checks."""
    return web.json_response({"status": "ok"})


# ── Server entry point ─────────────────────────────────────────────


async def start_push_server(
    bot: Bot,
    language: str = "ru",
    last_sent_briefing_turn: dict[int, int] | None = None,
    mark_sent_fn: Callable[[int, int], None] | None = None,
    last_sent_outcome_turn: dict[int, int] | None = None,
    mark_outcome_sent_fn: Callable[[int, int], None] | None = None,
    create_keyboard_fn: (Callable[[list[dict[str, Any]]], InlineKeyboardMarkup] | None) = None,
) -> web.AppRunner:
    """Start the push HTTP server with persistent delivery queue.

    1. Startup flush: fetch current turns from game-server, then
       synchronously deliver all pending messages.
    2. Start background sender worker.
    3. Start HTTP server.

    Args:
        bot: aiogram Bot instance
        language: Default bot language
        last_sent_briefing_turn: Shared dict for briefing dedup
        mark_sent_fn: Function to mark briefing as sent
        last_sent_outcome_turn: Shared dict for outcome dedup (loaded from DB)
        mark_outcome_sent_fn: Function to mark outcome as sent
        create_keyboard_fn: Function to create action keyboard

    Returns:
        web.AppRunner for graceful shutdown
    """
    if last_sent_briefing_turn is None:
        last_sent_briefing_turn = {}
    if mark_sent_fn is None:

        def _noop_mark_sent(pid: int, turn: int) -> None:
            pass

        mark_sent_fn = _noop_mark_sent
    if create_keyboard_fn is None:

        def _noop_keyboard(choices: list) -> InlineKeyboardMarkup:
            return InlineKeyboardMarkup(inline_keyboard=[[]])

        create_keyboard_fn = _noop_keyboard

    # Per-player outcome dedup dict (pre-populated from DB)
    last_sent_outcome: dict[int, int] = last_sent_outcome_turn.copy() if last_sent_outcome_turn else {}
    # Per-player game-over dedup dict (player_id → game_id)
    last_sent_game_over: dict[int, str] = {}

    # Startup flush — deliver any messages that were pending from a
    # previous run before we start accepting new ones.
    await _startup_flush(
        bot,
        language,
        create_keyboard_fn,
        mark_sent_fn,
        last_sent_briefing_turn,
        last_sent_outcome,
        last_sent_game_over,
        mark_outcome_sent_fn,
    )

    # Start background sender
    global _sender_task
    _sender_task = asyncio.create_task(
        _sender_loop(
            bot,
            language,
            create_keyboard_fn,
            mark_sent_fn,
            last_sent_briefing_turn,
            last_sent_outcome,
            last_sent_game_over,
            mark_outcome_sent_fn,
        )
    )

    # Build aiohttp app
    app = web.Application()
    app["bot"] = bot
    app["language"] = language
    app["last_sent_briefing_turn"] = last_sent_briefing_turn
    app["mark_sent_fn"] = mark_sent_fn
    app["create_keyboard_fn"] = create_keyboard_fn

    app.router.add_post("/push/briefings", handle_push_briefings)
    app.router.add_post("/push/player-action", handle_push_player_chosen_action)
    app.router.add_post("/push/outcome", handle_push_outcome)
    app.router.add_post("/push/game-over", handle_push_game_over)
    app.router.add_post("/push/gm-notification", handle_gm_notification)
    app.router.add_post("/push/onboarding-ready", handle_push_onboarding_ready)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PUSH_SERVER_PORT)
    await site.start()

    logger.info("[PUSH_SERVER] Started on port %d", PUSH_SERVER_PORT)
    return runner
