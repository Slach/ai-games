"""Push client that delivers briefings to telegram-bot with exponential retry."""

import asyncio
import logging
import os
import random

import aiohttp

logger = logging.getLogger(__name__)

# Config from environment
TELEGRAM_BOT_PUSH_URL = os.getenv(
    "TELEGRAM_BOT_PUSH_URL",
    "http://telegram-bot:9090/push/briefings",
)
TELEGRAM_BOT_ACTION_URL = os.getenv(
    "TELEGRAM_BOT_ACTION_URL",
    "http://telegram-bot:9090/push/player-action",
)
TELEGRAM_BOT_OUTCOME_URL = os.getenv(
    "TELEGRAM_BOT_OUTCOME_URL",
    "http://telegram-bot:9090/push/outcome",
)
TELEGRAM_BOT_GM_NOTIFICATION_URL = os.getenv(
    "TELEGRAM_BOT_GM_NOTIFICATION_URL",
    "http://telegram-bot:9090/push/gm-notification",
)
TELEGRAM_BOT_GAME_OVER_URL = os.getenv(
    "TELEGRAM_BOT_GAME_OVER_URL",
    "http://telegram-bot:9090/push/game-over",
)
try:
    PUSH_MAX_RETRIES = int(os.getenv("PUSH_MAX_RETRIES", "7"))
except (ValueError, TypeError):
    logger.warning("Invalid PUSH_MAX_RETRIES, using default 7")
    PUSH_MAX_RETRIES = 7

try:
    PUSH_BASE_DELAY = float(os.getenv("PUSH_BASE_DELAY", "1.0"))
except (ValueError, TypeError):
    logger.warning("Invalid PUSH_BASE_DELAY, using default 1.0")
    PUSH_BASE_DELAY = 1.0

try:
    PUSH_REQUEST_TIMEOUT = int(os.getenv("PUSH_REQUEST_TIMEOUT", "120"))
except (ValueError, TypeError):
    logger.warning("Invalid PUSH_REQUEST_TIMEOUT, using default 120")
    PUSH_REQUEST_TIMEOUT = 120


async def push_briefings(
    game_id: str,
    day: int,
    players_briefings: list[dict],
    bridge_url: str | None = None,
    mission: dict | None = None,
    crew_dialogues: list | None = None,
    is_first_turn: bool = False,
    force_resend: bool = False,
    global_narrative: str = "",
    was_restarted: bool = False,
    language: str = "ru",
) -> bool:
    """Push briefings to telegram-bot with exponential backoff retry.

    Args:
        game_id: Game identifier
        day: Day/turn number
        players_briefings: List of per-player briefing dicts, each containing
            player_id, briefing, choices, etc.
        bridge_url: URL of bridge image (for first turn)
        mission: Mission info dict with name, description
        crew_dialogues: List of NPC dialogue dicts with npc, dialogue
        is_first_turn: If True, bot also sends bridge image + mission info
        force_resend: If True, skip dedup check on telegram-bot side
            (used for regenerate-turn to re-deliver briefings)
        global_narrative: Shared narrative for all players (setting, conflict).
            Sent as a separate common-intro message before personal briefings.
        was_restarted: If True, telegram-bot will send a "game restarted"
            notification to all alive players before their briefings.
        language: Game language code used for UI messages (bridge, titles, etc.).

    Returns:
        True if delivered successfully, False after all retries exhausted.
    """
    payload: dict = {
        "game_id": game_id,
        "day": day,
        "players": players_briefings,
        "is_first_turn": is_first_turn,
        "was_restarted": was_restarted,
        "language": language,
    }
    if force_resend:
        payload["force_resend"] = True
    if global_narrative:
        payload["global_narrative"] = global_narrative
    if bridge_url:
        payload["bridge_image_url"] = bridge_url
    if mission:
        payload["mission"] = mission
    if crew_dialogues:
        payload["crew_dialogues"] = crew_dialogues

    last_exception: Exception | None = None

    for attempt in range(PUSH_MAX_RETRIES):
        delay = PUSH_BASE_DELAY * (2**attempt)
        jitter = random.uniform(0, delay)

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    TELEGRAM_BOT_PUSH_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=PUSH_REQUEST_TIMEOUT),
                ) as resp,
            ):
                if resp.status == 200:
                    body = await resp.json()
                    sent_count = len(body.get("sent", []))
                    already = body.get("already_sent", False)
                    logger.info(f"[PUSH] Delivered day {day} for game {game_id}: {'already_sent' if already else sent_count} players")
                    return True
                else:
                    error_text = await resp.text()
                    logger.warning(f"[PUSH] Attempt {attempt + 1}/{PUSH_MAX_RETRIES}: HTTP {resp.status} - {error_text}")
                    last_exception = Exception(f"HTTP {resp.status}: {error_text}")

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"[PUSH] Attempt {attempt + 1}/{PUSH_MAX_RETRIES}: {type(e).__name__}: {e}. Retrying in {jitter:.1f}s...")
            last_exception = e

        # Wait before retry (skip on last attempt)
        if attempt < PUSH_MAX_RETRIES - 1:
            await asyncio.sleep(jitter)

    logger.error(f"[PUSH] Failed to deliver day {day} for game {game_id} after {PUSH_MAX_RETRIES} attempts: {last_exception}")
    return False


async def _post_with_retry(url: str, payload: dict, label: str) -> bool:
    """Post JSON payload to a push endpoint with exponential backoff."""
    last_exception: Exception | None = None

    for attempt in range(PUSH_MAX_RETRIES):
        delay = PUSH_BASE_DELAY * (2**attempt)
        jitter = random.uniform(0, delay)

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=PUSH_REQUEST_TIMEOUT),
                ) as resp,
            ):
                if resp.status == 200:
                    logger.info(f"[PUSH] {label}: delivered successfully")
                    return True
                else:
                    error_text = await resp.text()
                    logger.warning(f"[PUSH] {label} attempt {attempt + 1}/{PUSH_MAX_RETRIES}: HTTP {resp.status} - {error_text}")
                    last_exception = Exception(f"HTTP {resp.status}: {error_text}")

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"[PUSH] {label} attempt {attempt + 1}/{PUSH_MAX_RETRIES}: {type(e).__name__}: {e}. Retrying in {jitter:.1f}s...")
            last_exception = e

        if attempt < PUSH_MAX_RETRIES - 1:
            await asyncio.sleep(jitter)

    logger.error(f"[PUSH] {label} failed after {PUSH_MAX_RETRIES} attempts: {last_exception}")
    return False


async def push_player_chosen_action(
    player_id: int,
    day: int,
    chosen_action_url: str,
    game_id: str = "default_game",
    action_text: str = "",
    language: str = "ru",
) -> bool:
    """Push a player's chosen action image to the telegram-bot.

    Delivers the action image directly to the player who performed the action.
    """
    payload: dict = {
        "player_id": player_id,
        "day": day,
        "chosen_action_url": chosen_action_url,
        "game_id": game_id,
        "action_text": action_text,
        "language": language,
    }
    label = f"action player={player_id} day={day}"
    return await _post_with_retry(TELEGRAM_BOT_ACTION_URL, payload, label)


async def push_gm_notification(
    game_id: str,
    day: int,
    status: str,
    error: str = "",
    players: int = 0,
    npcs: int = 0,
    language: str = "ru",
) -> bool:
    """Push a notification to the Game Master about turn generation status.

    Called after background turn generation completes (success or failure)
    so the GM gets a Telegram message without waiting for the HTTP response.

    Args:
        game_id: Game identifier
        day: Day/turn number that was being generated
        status: "success" or "error"
        error: Error message (only when status="error")
        players: Number of players (only when status="success")
        npcs: Number of NPCs (only when status="success")
        language: Game language code for the notification message.

    Returns:
        True if delivered successfully, False after all retries exhausted.
    """
    payload: dict = {
        "game_id": game_id,
        "day": day,
        "status": status,
        "error": error,
        "players": players,
        "npcs": npcs,
        "language": language,
    }
    label = f"gm-notification game={game_id} day={day} status={status}"
    return await _post_with_retry(TELEGRAM_BOT_GM_NOTIFICATION_URL, payload, label)


async def push_day_outcome(
    game_id: str,
    day: int,
    outcome_text: str,
    alive_players: list[int],
    outcome_image_url: str | None = None,
    ship_status: str | None = None,
    mission_progress: dict | None = None,
    death_notices: list[dict] | None = None,
    injury_notices: list[dict] | None = None,
    personal_outcomes: list[dict] | None = None,
    action_images: list[dict] | None = None,
    ship_hull_integrity: int | None = None,
    ship_shields: int | None = None,
    ship_systems_offline: list[str] | None = None,
    total_crew_count: int | None = None,
    alive_crew_count: int | None = None,
    language: str = "ru",
) -> bool:
    """Push the combined day outcome to all alive players.

    Args:
        game_id: Game identifier
        day: Day number
        outcome_text: Narrative description of the outcome
        alive_players: List of player IDs still alive
        outcome_image_url: Optional URL to an outcome scene image
        ship_status: Current ship status ("alive" / "destroyed")
        mission_progress: Dict with stage progress info
        death_notices: List of death notice dicts with player_id and role
        injury_notices: List of injury notice dicts with name, role, severity
        personal_outcomes: List of personal outcome dicts with character_name, role, outcome_text
        action_images: List of action image dicts with image_url, caption, player_id/npc_key
            Format: [{"image_url": str, "caption": "Ход X — Имя — Роль — Действие",
                     "player_id": int | None, "npc_key": str | None}]
        ship_hull_integrity: Hull integrity percentage (0-100)
        ship_shields: Shield strength percentage (0-100)
        ship_systems_offline: List of offline/damaged systems
        total_crew_count: Total crew members (NPCs + players) at start of turn
        alive_crew_count: Crew members still alive after this turn
        language: Game language code for UI messages (titles, status labels, etc.).
    """
    payload: dict = {
        "game_id": game_id,
        "day": day,
        "outcome_text": outcome_text,
        "alive_players": alive_players,
        "language": language,
    }
    if outcome_image_url:
        payload["outcome_image_url"] = outcome_image_url
    if ship_status:
        payload["ship_status"] = ship_status
    if mission_progress:
        payload["mission_progress"] = mission_progress
    if death_notices:
        payload["death_notices"] = death_notices
    if injury_notices:
        payload["injury_notices"] = injury_notices
    if personal_outcomes:
        payload["personal_outcomes"] = personal_outcomes
    if action_images:
        payload["action_images"] = action_images
    if ship_hull_integrity is not None:
        payload["ship_hull_integrity"] = ship_hull_integrity
    if ship_shields is not None:
        payload["ship_shields"] = ship_shields
    if ship_systems_offline is not None:
        payload["ship_systems_offline"] = ship_systems_offline
    if total_crew_count is not None:
        payload["total_crew_count"] = total_crew_count
    if alive_crew_count is not None:
        payload["alive_crew_count"] = alive_crew_count

    label = f"outcome day={day} game={game_id}"
    return await _post_with_retry(TELEGRAM_BOT_OUTCOME_URL, payload, label)


async def push_game_over(
    game_id: str,
    finale_narrative: str,
    finale_image_url: str | None,
    outcome_type: str,
    alive_players: list[int],
    available_games: list[dict],
    language: str = "ru",
) -> bool:
    """Push the game-over finale to all alive players.

    Args:
        game_id: Game identifier that just ended
        finale_narrative: The LLM-generated finale narrative text
        finale_image_url: URL to the finale scene image
        outcome_type: "victory" or "defeat"
        alive_players: List of player IDs still alive to receive the message
        available_games: List of other active games for the continuation keyboard
        language: Game language code
    """
    payload: dict = {
        "game_id": game_id,
        "finale_narrative": finale_narrative,
        "outcome_type": outcome_type,
        "alive_players": alive_players,
        "available_games": available_games,
        "language": language,
    }
    if finale_image_url:
        payload["finale_image_url"] = finale_image_url

    label = f"game-over game={game_id} type={outcome_type}"
    return await _post_with_retry(TELEGRAM_BOT_GAME_OVER_URL, payload, label)
