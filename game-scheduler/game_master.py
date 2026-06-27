"""
Game Master Agent - Scheduler that calls game-server-api

This service runs on a schedule and triggers the game-server-api to:
- Generate daily episodes with game state validation
- Generate personalized comics for each player
- Process player actions and auto-select if needed
- Track team assembly over 3 days

It does NOT do the actual AI generation - that's handled by game-server-api.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any

import aiohttp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Get configuration from environment
GAME_MASTER_API_URL = os.getenv("GAME_MASTER_API_URL", "http://game-server-api:8000")
GAME_SCHEDULE_RAW = os.getenv("GAME_SCHEDULE", os.getenv("GAME_SCHEDULE_TIME", "8h"))
try:
    AUTO_ACTION_TIMEOUT_HOURS = int(os.getenv("AUTO_ACTION_TIMEOUT_HOURS", "24"))  # Hours before auto-selection
except (ValueError, TypeError):
    logger.warning("Invalid AUTO_ACTION_TIMEOUT_HOURS, using default 24")
    AUTO_ACTION_TIMEOUT_HOURS = 24
GAME_ID = os.getenv("GAME_ID", "default_game")


def parse_schedule(schedule: str) -> tuple[str, int | str]:
    """Parse schedule string into (type, value).

    Supported formats:
    - HH:MM (e.g., "08:00") — daily at that time
    - Nh (e.g., "6h") — every N hours
    - Nm (e.g., "30m") — every N minutes
    - Ns (e.g., "30s") — every N seconds (testing)

    Returns:
        ("daily", "HH:MM") or ("interval", seconds)
    """
    s = schedule.strip().lower()

    # HH:MM format — daily at specific time
    if re.match(r"^\d{1,2}:\d{2}$", s):
        return ("daily", s)

    # Interval format: Nh, Nm, Ns
    m = re.match(r"^(\d+)([hms])$", s)
    if m:
        try:
            value = int(m.group(1))
        except (ValueError, TypeError):
            raise ValueError(f"Invalid schedule format: {schedule}") from None
        unit = m.group(2)
        if unit == "h":
            return ("interval", value * 3600)
        elif unit == "m":
            return ("interval", value * 60)
        else:  # seconds
            return ("interval", value)

    raise ValueError(f"Invalid schedule format: {schedule}")


GAME_SCHEDULE = parse_schedule(GAME_SCHEDULE_RAW)


class GameMasterScheduler:
    """
    Scheduler that calls game-server-api to generate daily content with full game loop support.
    """

    def __init__(self):
        self.api_url = GAME_MASTER_API_URL
        self.game_id = GAME_ID
        self.last_generation = None
        self.team_assembly_start = None  # Track when first crew member joined

    async def check_game_state(self) -> dict[str, Any]:
        """Check current game state and verify ship/crew are alive"""
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(f"{self.api_url}/game/state", params={"game_id": self.game_id}) as resp,
            ):
                if resp.status != 200:
                    raise Exception(f"API error: {resp.status}")
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to get game state: {e}")
            raise

    async def validate_game_active(self) -> bool:
        """Validate that game is still active (ship and crew alive)"""
        try:
            state = await self.check_game_state()
            return state.get("status") == "active" and state.get("ship_alive", True) and state.get("crew_health", 0) > 0
        except Exception as e:
            logger.error(f"Failed to validate game active: {e}")
            return False

    async def is_game_started(self, game_id: str = "default_game") -> bool:
        """Check if game has officially started (>= 3 players joined)"""
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(f"{self.api_url}/game/started", params={"game_id": game_id}) as resp,
            ):
                if resp.status != 200:
                    return False
                data = await resp.json()
                return data.get("started", False)
        except Exception as e:
            logger.error(f"Failed to check game started status: {e}")
            return False

    async def get_previous_turn_actions(self, turn: int, game_id: str = "default_game") -> list[dict[str, Any]]:
        """Get all player actions from previous turn with consequences"""
        try:
            async with aiohttp.ClientSession() as session:
                # Get game state to find current day
                state = await self.check_game_state()
                current_turn = state.get("turn", 1)

                # Get previous day (current - 1)
                prev_turn = current_turn - 1

                if prev_turn <= 0:
                    return []

                # Fetch previous day data from API
                async with session.get(f"{self.api_url}/game/turn/{prev_turn}", params={"game_id": game_id}) as resp:
                    if resp.status != 200:
                        logger.warning(f"Could not fetch previous turn {prev_turn}")
                        return []

                    day_data = await resp.json()
                    return day_data.get("player_actions", [])
        except Exception as e:
            logger.error(f"Failed to get previous turn actions: {e}")
            return []

    async def get_players_in_game(self, game_id: str = "default_game") -> list[int]:
        """Get list of player IDs in the current game"""
        try:
            async with aiohttp.ClientSession() as session:
                # The /players endpoint returns a list of {player_id, game_id} dicts
                # Older endpoint paths may return 404 — we skip them silently.
                endpoints = [
                    f"{self.api_url}/players/{game_id}/players",
                    f"{self.api_url}/players/{game_id}/list",
                    f"{self.api_url}/players",
                ]

                for endpoint in endpoints:
                    async with session.get(endpoint) as resp:
                        if resp.status != 200:
                            logger.debug(f"get_players_in_game: {endpoint} returned {resp.status}")
                            continue

                        result = await resp.json()
                        logger.debug(f"get_players_in_game: {endpoint} returned type={type(result).__name__}, value={result!r}")

                        # Handle list response — this is the primary format
                        if isinstance(result, list):
                            player_ids = []
                            for item in result:
                                if isinstance(item, dict):
                                    pid = item.get("player_id")
                                    if pid is not None:
                                        player_ids.append(int(pid))
                                elif isinstance(item, (int, str)):
                                    player_ids.append(int(item))
                            if player_ids:
                                return player_ids
                            continue  # empty list, try next endpoint

                        # Handle dict response (legacy format)
                        if isinstance(result, dict):
                            player_ids = result.get("player_ids", []) or result.get("players", []) or []
                            if player_ids:
                                return player_ids
                            continue

                        # Unexpected format — log and skip
                        logger.warning(f"get_players_in_game: unexpected response type {type(result).__name__} from {endpoint}: {result!r}")

                logger.warning(f"No players found for game {game_id}")
                return []
        except Exception as e:
            logger.error(f"Failed to get players in game: {e}")
            return []

    async def generate_personalized_comics(self, day_data: dict[str, Any], game_id: str = "default_game") -> list[dict[str, Any]]:
        """Generate personalized comics for all players in the game with unified intro story"""
        player_ids = await self.get_players_in_game(game_id)
        comics_generated = []

        # Get day data for common intro story
        turn_num = day_data.get("turn", 1)

        for player_id in player_ids:
            try:
                logger.info(f"Generating personalized comic for player {player_id}")

                async with aiohttp.ClientSession() as session:
                    params: dict[str, Any] = {"game_id": game_id}
                    if turn_num:
                        params["turn"] = turn_num

                    async with session.post(
                        f"{self.api_url}/admin/generate-comic/{player_id}",
                        params=params,
                    ) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            comics_generated.append(
                                {
                                    "player_id": player_id,
                                    "comic_url": result.get("comic_url"),
                                    "role": result.get("role"),
                                }
                            )
                            logger.info(f"Comic generated for player {player_id}: {result.get('comic_url')}")
                        else:
                            error_text = await resp.text()
                            logger.error(f"Failed to generate comic for player {player_id}: {resp.status} - {error_text}")
            except Exception as e:
                logger.error(f"Error generating comic for player {player_id}: {e}")

        return comics_generated

    async def select_auto_action(self, player_id: int, turn: int) -> dict[str, Any] | None:
        """Select default action for player who hasn't chosen within timeout.

        Uses LLM endpoint on game-server-api which considers:
        - Global circumstances (setting, conflict, narrative)
        - Player's personal briefing for this turn
        - Player profile (role, traits, species)
        - Available actions (without hidden consequences)
        """
        try:
            logger.info(f"[AUTO_ACTION] Calling LLM auto-action for player {player_id} on turn {turn}")

            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    f"{self.api_url}/game/auto-action/{player_id}/{turn}",
                    params={
                        "language": "en",
                        "game_id": self.game_id,
                    },
                ) as resp,
            ):
                if resp.status == 200:
                    result = await resp.json()
                    logger.info(f"[AUTO_ACTION] LLM selected '{result.get('action_id', '?')}' for player {player_id}: {result.get('action_text', '')[:60]}...")
                    return result
                else:
                    error_text = await resp.text()
                    logger.error(f"[AUTO_ACTION] LLM auto-action failed for player {player_id}: {resp.status} - {error_text}")
                    return None

        except Exception as e:
            logger.error(f"[AUTO_ACTION] Failed to select auto action for player {player_id}: {e}")
            return None

    async def check_and_auto_select_actions(self, turn: int):
        """Check for players who haven't selected actions within timeout and auto-select for them"""
        try:
            # Get all players in game
            player_ids = await self.get_players_in_game(self.game_id)

            if not player_ids:
                logger.info("No players found in game")
                return

            logger.info(f"Checking {len(player_ids)} players for action selection on turn {turn}")

            for player_id in player_ids:
                # Check if player has already selected action via the briefing endpoint
                async with (
                    aiohttp.ClientSession() as session,
                    session.get(f"{self.api_url}/game/briefing/{player_id}/{turn}") as resp,
                ):
                    if resp.status == 200:
                        briefing = await resp.json()
                        if briefing.get("selected_action_id"):
                            continue  # Player already selected

                # No action found, auto-select
                logger.info(f"Player {player_id} has not selected action, auto-selecting")
                await self.select_auto_action(player_id, turn)
        except Exception as e:
            logger.error(f"Failed to check and auto-select actions: {e}")

    async def get_team_assembly_status(self, game_id: str = "default_game") -> dict[str, Any]:
        """Track team assembly over 3 days from first crew member"""
        try:
            # Get current day to determine assembly status
            state = await self.check_game_state()
            current_turn = state.get("turn", 1)

            # Check if team assembly is complete (3 days since first player joined)
            team_assembly_complete = current_turn >= 3

            return {
                "turns_since_first": current_turn,
                "team_assembled": team_assembly_complete,
                "bot_npcs_needed": [] if team_assembly_complete else ["engineer", "pilot"],  # Example bot NPCs
            }
        except Exception as e:
            logger.error(f"Failed to get team assembly status: {e}")
            return {
                "turns_since_first": 0,
                "team_assembled": False,
                "bot_npcs_needed": ["engineer", "pilot"],
            }

    async def generate_scheduled_turn(self) -> dict:
        """Generate the next scheduled turn.

        1. Auto-selects actions for unresponsive players on the PREVIOUS turn.
        2. Triggers the next turn via /admin/continue-game, which handles:
           global circumstances, per-player briefings, NPC decisions,
           _analyze_turn_outcome for the previous turn, and push to players.
        """
        logger.info("=== SCHEDULED TURN STARTED ===")

        # Step 0: Check if game has started (>= 3 players)
        game_started = await self.is_game_started(self.game_id)
        if not game_started:
            logger.info("Game not started yet - waiting for more players (need at least 3)")
            return {
                "status": "game_not_started",
                "message": "Game has not started yet, waiting for more players",
            }

        # Step 1: Validate game state before generation
        is_active = await self.validate_game_active()
        if not is_active:
            logger.warning("Game ended - stopping generation")
            return {
                "status": "game_ended",
                "message": "Game has ended, no new episode generated",
            }

        # Step 2: Get current game state
        state = await self.check_game_state()
        current_turn = state.get("turn", 1)

        logger.info(f"Scheduled turn for Turn {current_turn}")

        # Step 3: Auto-select actions for unresponsive players from the PREVIOUS turn.
        # /admin/continue-game runs _analyze_turn_outcome for day-1, which needs
        # all decisions in. So we must fill in any missing choices first.
        if current_turn > 1:
            prev_turn = current_turn - 1
            logger.info(f"Checking for players who need auto-selection on turn {prev_turn}")
            await self.check_and_auto_select_actions(prev_turn)

        # Step 4: Trigger the next turn via /admin/continue-game.
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/admin/continue-game",
                    params={
                        "game_id": self.game_id,
                        "language": "en",
                    },
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"API error: {resp.status} - {error_text}")
                        raise Exception(f"API error: {resp.status}")

                    result = await resp.json()
                    self.last_generation = datetime.now()

                    logger.info("=== SCHEDULED TURN COMPLETED ===")
                    logger.info(f"Turn {current_turn} generation submitted (background): {result.get('status')}")

                    return result

        except Exception as e:
            logger.error(f"Failed to generate scheduled turn: {e}")
            raise

    async def generate_comics_for_all_players(self, day_result: dict) -> None:
        """Generate personalized comics for all players in the game"""
        try:
            # Get all players in current game
            players = await self.get_players_in_game(self.game_id)

            if not players:
                logger.info("No players found in game")
                return

            logger.info(f"Generating comics for {len(players)} players")

            # Generate comic for each player
            for player_id in players:
                try:
                    await self.generate_comic_for_player(player_id, day_result.get("turn"))
                except Exception as e:
                    logger.error(f"Failed to generate comic for player {player_id}: {e}")

        except Exception as e:
            logger.error(f"Failed to generate comics batch: {e}")

    async def generate_comic_for_player(self, player_id: int, turn: int | None = None) -> dict:
        """Call game-server-api to generate a personalized comic for a player"""
        logger.info(f"Calling API to generate comic for player {player_id}")

        try:
            async with aiohttp.ClientSession() as session:
                params: dict[str, Any] = {"game_id": self.game_id}
                if turn:
                    params["turn"] = turn

                async with session.post(f"{self.api_url}/admin/generate-comic/{player_id}", params=params) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"API error: {resp.status} - {error_text}")
                        raise Exception(f"API error: {resp.status}")

                    result = await resp.json()
                    logger.info(f"Comic generated for player {player_id}: {result.get('comic_url')}")
                    return result

        except Exception as e:
            logger.error(f"Failed to generate comic for player {player_id}: {e}")
            raise

    async def check_pending_actions(self) -> list:
        """Check for players who haven't selected actions yet"""
        try:
            state = await self.check_game_state()
            current_turn = state.get("turn", 1)

            # Get all players in game
            players = await self.get_players_in_game(self.game_id)

            pending = []
            for player_id in players:
                # Check if player has action for current day
                async with (
                    aiohttp.ClientSession() as session,
                    session.get(f"{self.api_url}/game/actions/{player_id}/{current_turn}") as resp,
                ):
                    if resp.status == 200:
                        result = await resp.json()
                        if not result.get("has_action"):
                            pending.append(player_id)
            return pending

        except Exception as e:
            logger.error(f"Failed to check pending actions: {e}")
            return []

    async def auto_select_actions(self, player_ids: list) -> None:
        """Auto-select actions for players who haven't chosen"""
        try:
            state = await self.check_game_state()
            current_turn = state.get("turn", 1)

            for player_id in player_ids:
                logger.info(f"Auto-selecting action for player {player_id}")

                # Call API to auto-select action based on player profile
                async with (
                    aiohttp.ClientSession() as session,
                    session.post(
                        f"{self.api_url}/game/auto-action/{player_id}/{current_turn}",
                        json={"timeout_hours": AUTO_ACTION_TIMEOUT_HOURS},
                    ) as resp,
                ):
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info(f"Auto-selected action for player {player_id}: {result.get('action_id')}")
                    else:
                        error_text = await resp.text()
                        logger.error(f"Auto-action failed for player {player_id}: {resp.status} - {error_text}")

        except Exception as e:
            logger.error(f"Failed to auto-select actions: {e}")

    async def check_and_auto_select(self) -> None:
        """Check for pending actions and auto-select if timeout reached"""
        try:
            state = await self.check_game_state()
            current_turn = state.get("turn", 1)

            # Get last update time for the turn
            async with (
                aiohttp.ClientSession() as session,
                session.get(f"{self.api_url}/game/turn/{current_turn}") as resp,
            ):
                if resp.status == 200:
                    day_data = await resp.json()
                    created_at = day_data.get("created_at", "")

                    # Calculate time since day creation
                    if created_at:
                        day_created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        hours_since = (datetime.now(day_created.tzinfo) - day_created).total_seconds() / 3600

                        if hours_since >= AUTO_ACTION_TIMEOUT_HOURS:
                            pending_players = await self.check_pending_actions()
                            if pending_players:
                                logger.info(f"Auto-action timeout reached for {len(pending_players)} players")
                                await self.auto_select_actions(pending_players)
        except Exception as e:
            logger.error(f"Failed to check auto-action timeout: {e}")

    async def notify_player_auto_action(self, player_id: int, turn: int, action_text: str) -> bool:
        """
        Notify player that AI selected an action on their behalf.
        Returns True if notification sent successfully.
        """
        logger.info(f"Sending auto-action notification to player {player_id}")

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    f"{self.api_url}/admin/notify-player",
                    json={
                        "player_id": player_id,
                        "turn": turn,
                        "message_type": "auto_action",
                        "action_text": action_text,
                    },
                ) as resp,
            ):
                if resp.status != 200:
                    logger.warning(f"Failed to send notification: {resp.status}")
                    return False

                await resp.json()
                logger.info(f"Notification sent to player {player_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to notify player: {e}")
            return False

    async def get_game_state(self) -> dict:
        """Get current game state from API (delegates to check_game_state)"""
        return await self.check_game_state()

    def get_next_run_delay(self) -> float:
        """Calculate delay in seconds until next run."""
        schedule_type, schedule_value = GAME_SCHEDULE

        if schedule_type == "interval":
            try:
                return float(schedule_value)
            except (ValueError, TypeError):
                return 3600.0  # fallback to 1 hour

        # Daily mode — calculate seconds until next occurrence of HH:MM
        now = datetime.now()
        schedule_hour, schedule_minute = map(int, str(schedule_value).split(":"))
        next_run = now.replace(hour=schedule_hour, minute=schedule_minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        return (next_run - now).total_seconds()

    async def run_scheduled_loop(self):
        """Run the generation on a schedule with full game loop"""
        schedule_type, schedule_value = GAME_SCHEDULE
        schedule_desc = f"every {schedule_value}s" if schedule_type == "interval" else f"daily at {schedule_value}"
        logger.info(f"Starting scheduled loop: {schedule_desc}")

        while True:
            try:
                # Calculate delay until next run
                delay = self.get_next_run_delay()

                if schedule_type == "interval":
                    logger.info(f"Next generation in {delay / 3600:.2f} hours ({delay:.0f}s)")
                else:
                    logger.info(f"Next generation in {delay / 3600:.1f} hours ({delay:.0f}s)")

                # Wait until next run
                await asyncio.sleep(delay)

                # Generate scheduled turn with auto-selection + continue-game
                result = await self.generate_scheduled_turn()

                if result.get("status") == "game_ended":
                    logger.info("Game has ended, stopping scheduled generation")
                    break

                logger.info(f"Generation completed: Turn {result.get('turn')}")

            except Exception as e:
                logger.error(f"Error in scheduled loop: {e}")
                # Wait 1 hour before retrying on error
                await asyncio.sleep(3600)

    async def run_single_generation(self):
        """Run a single generation cycle (for testing)"""
        logger.info("Running single generation cycle")
        result = await self.generate_scheduled_turn()
        logger.info(f"Result: {result}")
        return result


async def main():
    """Main entry point"""
    logger.info("Starting Game Master Scheduler")
    logger.info(f"GAME_MASTER_API_URL: {GAME_MASTER_API_URL}")
    schedule_type, schedule_val = GAME_SCHEDULE
    desc = f"daily at {schedule_val}" if schedule_type == "daily" else f"every {schedule_val}s"
    logger.info(f"GAME_SCHEDULE: {desc}")

    scheduler = GameMasterScheduler()

    # Check if we should run in single mode or scheduled mode
    mode = os.getenv("GAME_MASTER_MODE", "scheduled").lower()

    if mode == "single":
        logger.info("Running in single mode (one generation)")
        await scheduler.run_single_generation()
    else:
        logger.info("Running in scheduled mode")
        await scheduler.run_scheduled_loop()


if __name__ == "__main__":
    asyncio.run(main())
