"""Linked game concept pipeline: mission (archetype + seeds) → title + welcome.

The title tagline and welcome are derived from the mission so the game name,
its welcome and the underlying mission stay consistent (instead of the title
being generated blind). Everything is produced in one place and guarded by a
per-game_id async lock plus the ``uq_game_mission`` unique index, so two
concurrent callers (two players finishing onboarding, an admin create racing
onboarding, a restart colliding with an in-flight task) cannot produce
duplicate missions or clobber the title.
"""

import asyncio
import logging

from database import (
    create_mission,
    get_game_title,
    get_game_welcome_text,
    get_mission,
    save_game_title_and_welcome,
    update_game_title,
)
from game_server import create_game_server
from language import get_game_strings

logger = logging.getLogger(__name__)

# Per-game_id async lock guarding the game concept pipeline (mission + title +
# welcome) so two near-simultaneous calls cannot generate a duplicate mission
# or clobber the title.
_game_concept_locks: dict[str, asyncio.Lock] = {}


def get_game_concept_lock(game_id: str) -> asyncio.Lock:
    """Get or create an async lock for game concept generation (per game_id)."""
    if game_id not in _game_concept_locks:
        _game_concept_locks[game_id] = asyncio.Lock()
    return _game_concept_locks[game_id]


async def generate_game_concept(
    game_id: str,
    language: str,
    *,
    max_attempts: int = 3,
    retry_delay: float = 2.0,
) -> dict:
    """Generate and persist the linked game concept: mission (archetype +
    seeds) → title + welcome (tied to that mission).

    Idempotent: a second call reuses the existing mission and title.
    Returns ``{"mission": dict|None, "title": str, "welcome_text": str}``.
    """
    lock = get_game_concept_lock(game_id)
    async with lock:
        gs = get_game_strings(language)
        gm = create_game_server(language=language)

        # ── Mission (archetype + seeds + objectives). Plot-driven, no crew. ──
        mission_data = get_mission(None, game_id=game_id)
        mission_just_created = False
        if not mission_data:
            last_err = None
            for attempt in range(1, max_attempts + 1):
                try:
                    mission_data = gm.generate_mission(game_id=game_id, player_id=None, turn=None, kind="mission")
                    create_mission(mission_data, game_id)
                    logger.info(
                        "[CONCEPT] Generated mission for game %s: %s (archetype=%s)",
                        game_id, mission_data.get("name", ""), mission_data.get("archetype", ""),
                    )
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    if attempt < max_attempts:
                        logger.warning("[CONCEPT] Mission attempt %d/%d failed for game %s, retrying", attempt, max_attempts, game_id, exc_info=True)
                        await asyncio.sleep(retry_delay * attempt)
            if last_err is not None:
                logger.error("[CONCEPT] All %d mission attempts failed for game %s; startup sweep will retry", max_attempts, game_id, exc_info=last_err)
            # A concurrent winner may have inserted the mission between our
            # get_mission check and now; re-read so the title step sees it.
            mission_data = get_mission(None, game_id=game_id)
            mission_just_created = mission_data is not None

        # ── Title + welcome, tied to the mission. ──
        # Generate when missing, or regenerate when the mission was just
        # created so the title tagline reflects the actual mission (instead of
        # a blind placeholder generated earlier).
        title = get_game_title(game_id)
        welcome_text = get_game_welcome_text(game_id)
        if not title or mission_just_created:
            try:
                title_data = gm.generate_game_title(
                    game_id=game_id,
                    player_id=None,
                    turn=None,
                    kind="game_title",
                    mission_context=mission_data,
                )
                if title_data.get("title"):
                    title = title_data["title"]
                    welcome_text = title_data.get("welcome_text", "") or gs["welcome_text_fallback"]
                    save_game_title_and_welcome(game_id, title, welcome_text)
                    logger.info("[CONCEPT] Generated title for game %s: %s", game_id, title)
            except Exception:
                logger.error("[CONCEPT] Title generation failed for game %s", game_id, exc_info=True)
                title = title or gs["game_title_fallback"]
                welcome_text = welcome_text or gs["welcome_text_fallback"]
                update_game_title(game_id, title)

        return {
            "mission": mission_data,
            "title": title or gs["game_title_fallback"],
            "welcome_text": welcome_text or gs["welcome_text_fallback"],
        }
