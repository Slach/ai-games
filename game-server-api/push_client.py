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

    Returns:
        True if delivered successfully, False after all retries exhausted.
    """
    payload: dict = {
        "game_id": game_id,
        "day": day,
        "players": players_briefings,
        "is_first_turn": is_first_turn,
    }
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
            async with aiohttp.ClientSession() as session, session.post(
                TELEGRAM_BOT_PUSH_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=PUSH_REQUEST_TIMEOUT),
            ) as resp:
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
                    last_exception = Exception(
                        f"HTTP {resp.status}: {error_text}"
                    )

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
