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
PUSH_MAX_RETRIES = int(os.getenv("PUSH_MAX_RETRIES", "7"))
PUSH_BASE_DELAY = float(os.getenv("PUSH_BASE_DELAY", "1.0"))
PUSH_REQUEST_TIMEOUT = int(os.getenv("PUSH_REQUEST_TIMEOUT", "30"))


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

    Returns:
        True if delivered successfully, False after all retries exhausted.
    """
    payload: dict = {
        "game_id": game_id,
        "day": day,
        "players": players_briefings,
        "is_first_turn": is_first_turn,
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
                    logger.info(
                        f"[PUSH] Delivered day {day} for game {game_id}: "
                        f"{'already_sent' if already else sent_count} players"
                    )
                    return True
                else:
                    error_text = await resp.text()
                    logger.warning(
                        f"[PUSH] Attempt {attempt + 1}/{PUSH_MAX_RETRIES}: "
                        f"HTTP {resp.status} - {error_text}"
                    )
                    last_exception = Exception(f"HTTP {resp.status}: {error_text}")

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(
                f"[PUSH] Attempt {attempt + 1}/{PUSH_MAX_RETRIES}: "
                f"{type(e).__name__}: {e}. Retrying in {jitter:.1f}s..."
            )
            last_exception = e

        # Wait before retry (skip on last attempt)
        if attempt < PUSH_MAX_RETRIES - 1:
            await asyncio.sleep(jitter)

    logger.error(
        f"[PUSH] Failed to deliver day {day} for game {game_id} "
        f"after {PUSH_MAX_RETRIES} attempts: {last_exception}"
    )
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
                    logger.warning(
                        f"[PUSH] {label} attempt {attempt + 1}/{PUSH_MAX_RETRIES}: "
                        f"HTTP {resp.status} - {error_text}"
                    )
                    last_exception = Exception(f"HTTP {resp.status}: {error_text}")

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(
                f"[PUSH] {label} attempt {attempt + 1}/{PUSH_MAX_RETRIES}: "
                f"{type(e).__name__}: {e}. Retrying in {jitter:.1f}s..."
            )
            last_exception = e

        if attempt < PUSH_MAX_RETRIES - 1:
            await asyncio.sleep(jitter)

    logger.error(
        f"[PUSH] {label} failed after {PUSH_MAX_RETRIES} attempts: {last_exception}"
    )
    return False


async def push_player_chosen_action(
    player_id: int,
    day: int,
    chosen_action_url: str,
    game_id: str = "default_game",
    action_text: str = "",
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
    }
    label = f"action player={player_id} day={day}"
    return await _post_with_retry(TELEGRAM_BOT_ACTION_URL, payload, label)


async def push_day_outcome(
    game_id: str,
    day: int,
    outcome_text: str,
    alive_players: list[int],
    outcome_image_url: str | None = None,
    ship_status: str | None = None,
    mission_progress: dict | None = None,
    death_notices: list[dict] | None = None,
    total_crew_count: int | None = None,
    alive_crew_count: int | None = None,
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
        total_crew_count: Total crew members (NPCs + players) at start of turn
        alive_crew_count: Crew members still alive after this turn
    """
    payload: dict = {
        "game_id": game_id,
        "day": day,
        "outcome_text": outcome_text,
        "alive_players": alive_players,
    }
    if outcome_image_url:
        payload["outcome_image_url"] = outcome_image_url
    if ship_status:
        payload["ship_status"] = ship_status
    if mission_progress:
        payload["mission_progress"] = mission_progress
    if death_notices:
        payload["death_notices"] = death_notices
    if total_crew_count is not None:
        payload["total_crew_count"] = total_crew_count
    if alive_crew_count is not None:
        payload["alive_crew_count"] = alive_crew_count

    label = f"outcome day={day} game={game_id}"
    return await _post_with_retry(TELEGRAM_BOT_OUTCOME_URL, payload, label)
