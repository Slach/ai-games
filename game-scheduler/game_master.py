"""
Game Scheduler — HTTP API service that triggers game-server-api on a schedule.

Runs a scheduling loop as a background task and exposes an HTTP API
for timer control (reset, pause, resume) and status queries.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from aiohttp import web

from database import init_db, load_scheduler_state, save_scheduler_state

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Get configuration from environment
GAME_SERVER_API_URL = os.getenv("GAME_SERVER_API_URL", "http://game-server-api:8000")
GAME_SCHEDULE_RAW = os.getenv("GAME_SCHEDULE", os.getenv("GAME_SCHEDULE_TIME", "8h"))
try:
    GAME_SCHEDULER_PORT = int(os.getenv("GAME_SCHEDULER_PORT", "8001"))
except (ValueError, TypeError):
    logger.warning("Invalid GAME_SCHEDULER_PORT, using default 8001")
    GAME_SCHEDULER_PORT = 8001
try:
    AUTO_ACTION_TIMEOUT_HOURS = int(os.getenv("AUTO_ACTION_TIMEOUT_HOURS", "24"))
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


def _compute_next_run(schedule: tuple[str, int | str], from_time: datetime | None = None) -> datetime:
    """Compute next run time from a given from_time (default now)."""
    schedule_type, schedule_value = schedule
    now = from_time or datetime.now()

    if schedule_type == "interval":
        try:
            seconds = float(schedule_value)
        except (ValueError, TypeError):
            seconds = 3600.0
        return now + timedelta(seconds=seconds)

    # Daily mode — next occurrence of HH:MM
    schedule_hour, schedule_minute = map(int, str(schedule_value).split(":"))
    next_run = now.replace(hour=schedule_hour, minute=schedule_minute, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return next_run


class GameScheduler:
    """Scheduler that calls game-server-api to generate turns on a schedule."""

    def __init__(self):
        self.api_url = GAME_SERVER_API_URL
        self.game_id = GAME_ID
        self.last_generation: datetime | None = None
        self.mode: str = "scheduled"  # "scheduled" | "paused"
        self.next_run_at: datetime | None = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused initially

        # Load persisted state
        self._load_state()

    def _load_state(self) -> None:
        """Load scheduler state from database, applying env var overrides."""
        init_db()
        state = load_scheduler_state()

        schedule_type_str = str(GAME_SCHEDULE[0])
        schedule_value_str = str(GAME_SCHEDULE[1])

        if state:
            self.mode = state.get("mode", "scheduled")
            if state.get("last_run_at"):
                self.last_generation = datetime.fromisoformat(state["last_run_at"])
            if state.get("next_run_at"):
                self.next_run_at = datetime.fromisoformat(state["next_run_at"])
        else:
            # First run: seed defaults
            save_scheduler_state(
                mode=self.mode,
                last_run_at=None,
                next_run_at=None,
                schedule_type=schedule_type_str,
                schedule_value=schedule_value_str,
                game_id=self.game_id,
            )

        if self.mode == "paused":
            self._pause_event.clear()

        # If no next_run_at set, compute from now
        if self.next_run_at is None:
            self.next_run_at = _compute_next_run(GAME_SCHEDULE)

        logger.info(f"GameScheduler initialized: mode={self.mode}, next_run_at={self.next_run_at.isoformat() if self.next_run_at else 'none'}")

    def _persist(self) -> None:
        """Write current state to database."""
        schedule_type_str = str(GAME_SCHEDULE[0])
        schedule_value_str = str(GAME_SCHEDULE[1])
        save_scheduler_state(
            mode=self.mode,
            last_run_at=self.last_generation.isoformat() if self.last_generation else None,
            next_run_at=self.next_run_at.isoformat() if self.next_run_at else None,
            schedule_type=schedule_type_str,
            schedule_value=schedule_value_str,
            game_id=self.game_id,
        )

    def reset_timer(self) -> datetime:
        """Reset next_run_at to now + interval (or next HH:MM for daily schedule)."""
        self.next_run_at = _compute_next_run(GAME_SCHEDULE)
        self._persist()
        delay_s = (self.next_run_at - datetime.now()).total_seconds()
        logger.info(f"Timer reset: next run at {self.next_run_at.strftime('%Y-%m-%d %H:%M:%S')} (in {delay_s / 3600:.1f}h)")
        return self.next_run_at

    def pause(self) -> None:
        """Pause the scheduling loop."""
        self.mode = "paused"
        self._pause_event.clear()
        self._persist()
        logger.info("Scheduler paused")

    def resume(self) -> datetime:
        """Resume the scheduling loop and reset timer."""
        self.mode = "scheduled"
        self.next_run_at = _compute_next_run(GAME_SCHEDULE)
        self._pause_event.set()
        self._persist()
        delay_s = (self.next_run_at - datetime.now()).total_seconds()
        logger.info(f"Scheduler resumed, next run at {self.next_run_at.strftime('%Y-%m-%d %H:%M:%S')} (in {delay_s / 3600:.1f}h)")
        return self.next_run_at

    def get_status(self) -> dict[str, Any]:
        """Return current scheduler status."""
        schedule_type_str = str(GAME_SCHEDULE[0])
        schedule_value_str = str(GAME_SCHEDULE[1])
        return {
            "schedule_type": schedule_type_str,
            "schedule_value": schedule_value_str,
            "last_run_at": self.last_generation.isoformat() if self.last_generation else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "mode": self.mode,
            "game_id": self.game_id,
        }

    # ── API client methods ──

    async def check_game_state(self) -> dict[str, Any]:
        """Check current game state and verify ship/crew are alive."""
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(f"{self.api_url}/game/state", params={"game_id": self.game_id}) as resp,
            ):
                if resp.status != 200:
                    raise Exception(f"API error: {resp.status}")
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to get game state: {e}", exc_info=True)
            raise

    async def validate_game_active(self) -> bool:
        """Validate that game is still active (ship and crew alive)."""
        try:
            state = await self.check_game_state()
            return state.get("status") == "active" and state.get("ship_alive", True) and state.get("crew_health", 0) > 0
        except Exception as e:
            logger.error(f"Failed to validate game active: {e}", exc_info=True)
            return False

    async def is_game_started(self, game_id: str = "default_game") -> bool:
        """Check if game has officially started (>= 3 players joined)."""
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
            logger.error(f"Failed to check game started status: {e}", exc_info=True)
            return False

    async def get_players_in_game(self, game_id: str = "default_game") -> list[int]:
        """Get list of player IDs in the current game."""
        try:
            async with aiohttp.ClientSession() as session:
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
                            continue

                        if isinstance(result, dict):
                            player_ids = result.get("player_ids", []) or result.get("players", []) or []
                            if player_ids:
                                return player_ids
                            continue

                logger.warning(f"No players found for game {self.game_id}")
                return []
        except Exception as e:
            logger.error(f"Failed to get players in game: {e}", exc_info=True)
            return []

    async def check_and_auto_select_actions(self, turn: int):
        """Check for players who haven't selected actions and auto-select for them."""
        try:
            player_ids = await self.get_players_in_game(self.game_id)

            if not player_ids:
                logger.info("No players found in game")
                return

            logger.info(f"Checking {len(player_ids)} players for action selection on turn {turn}")

            for player_id in player_ids:
                async with (
                    aiohttp.ClientSession() as session,
                    session.get(f"{self.api_url}/game/briefing/{player_id}/{turn}") as resp,
                ):
                    if resp.status == 200:
                        briefing = await resp.json()
                        if briefing.get("selected_action_id"):
                            continue

                logger.info(f"Player {player_id} has not selected action, auto-selecting")
                await self._select_auto_action(player_id, turn)
        except Exception as e:
            logger.error(f"Failed to check and auto-select actions: {e}", exc_info=True)

    async def _select_auto_action(self, player_id: int, turn: int) -> dict[str, Any] | None:
        """Select default action for player who hasn't chosen within timeout."""
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
            logger.error(f"[AUTO_ACTION] Failed to select auto action for player {player_id}: {e}", exc_info=True)
            return None

    async def generate_scheduled_turn(self) -> dict:
        """Generate the next scheduled turn.

        1. Auto-selects actions for unresponsive players on the PREVIOUS turn.
        2. Triggers the next turn via /admin/continue-game.
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
                    self._persist()

                    logger.info("=== SCHEDULED TURN COMPLETED ===")
                    logger.info(f"Turn {current_turn} generation submitted (background): {result.get('status')}")

                    return result

        except Exception as e:
            logger.error(f"Failed to generate scheduled turn: {e}", exc_info=True)
            raise

    async def run_scheduled_loop(self):
        """Run the scheduling loop with pause support."""
        schedule_type, schedule_value = GAME_SCHEDULE
        schedule_desc = f"every {schedule_value}s" if schedule_type == "interval" else f"daily at {schedule_value}"
        logger.info(f"Starting scheduled loop: {schedule_desc}")

        while True:
            try:
                # Wait for pause to be cleared
                await self._pause_event.wait()

                # Compute delay until next run
                now = datetime.now()
                if self.next_run_at is None or self.next_run_at <= now:
                    self.next_run_at = _compute_next_run(GAME_SCHEDULE)
                    self._persist()

                delay = (self.next_run_at - now).total_seconds()
                if delay > 0:
                    logger.info(f"Next turn in {delay / 3600:.1f}h ({delay:.0f}s)")
                    await asyncio.sleep(delay)

                # Re-check pause after sleep
                if self.mode == "paused":
                    continue

                # Generate scheduled turn
                result = await self.generate_scheduled_turn()

                if result.get("status") == "game_ended":
                    logger.info("Game has ended, stopping scheduled generation")
                    break

                logger.info(f"Generation completed: Turn {result.get('turn')}")

            except Exception as e:
                logger.error(f"Error in scheduled loop: {e}", exc_info=True)
                await asyncio.sleep(3600)

    async def run_single_generation(self):
        """Run a single generation cycle (for testing)."""
        logger.info("Running single generation cycle")
        result = await self.generate_scheduled_turn()
        logger.info(f"Result: {result}")
        return result


# ── HTTP API handlers ──


async def handle_status(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    status = scheduler.get_status()
    logger.info(f"GET /scheduler/status — mode={status['mode']}, next_run={status.get('next_run_at', 'none')}, schedule={status['schedule_type']}:{status['schedule_value']}")
    return web.json_response(status)


async def handle_reset(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    logger.info("POST /scheduler/reset — resetting timer")
    next_run = scheduler.reset_timer()
    return web.json_response({"status": "ok", "next_run_at": next_run.isoformat()})


async def handle_pause(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    next_run = scheduler.next_run_at
    logger.info("POST /scheduler/pause — pausing scheduler" + (f", was scheduled at {next_run.strftime('%Y-%m-%d %H:%M:%S')}" if next_run else ""))
    scheduler.pause()
    return web.json_response({"status": "ok", "mode": "paused"})


async def handle_resume(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    logger.info("POST /scheduler/resume — resuming scheduler")
    next_run = scheduler.resume()
    return web.json_response({"status": "ok", "mode": "scheduled", "next_run_at": next_run.isoformat()})


def create_app() -> web.Application:
    """Create aiohttp application with scheduler endpoints."""
    app = web.Application()

    scheduler = GameScheduler()
    app["scheduler"] = scheduler

    app.router.add_get("/scheduler/status", handle_status)
    app.router.add_post("/scheduler/reset", handle_reset)
    app.router.add_post("/scheduler/pause", handle_pause)
    app.router.add_post("/scheduler/resume", handle_resume)

    # Start scheduling loop as background task
    async def start_scheduler(app: web.Application) -> None:
        mode = os.getenv("GAME_SCHEDULER_MODE", "scheduled").lower()
        sched: GameScheduler = app["scheduler"]
        if mode == "single":
            logger.info("Running in single mode (one generation)")
            asyncio.create_task(sched.run_single_generation())
        else:
            logger.info("Running in scheduled mode")
            asyncio.create_task(sched.run_scheduled_loop())

    app.on_startup.append(start_scheduler)

    return app


def main():
    """Main entry point."""
    logger.info("Starting Game Scheduler HTTP API")
    logger.info(f"GAME_SERVER_API_URL: {GAME_SERVER_API_URL}")
    schedule_type, schedule_val = GAME_SCHEDULE
    desc = f"daily at {schedule_val}" if schedule_type == "daily" else f"every {schedule_val}s"
    logger.info(f"GAME_SCHEDULE: {desc}")
    logger.info(f"GAME_SCHEDULER_PORT: {GAME_SCHEDULER_PORT}")

    app = create_app()
    host = os.getenv("GAME_SCHEDULER_HOST", "0.0.0.0")
    web.run_app(app, host=host, port=GAME_SCHEDULER_PORT)


if __name__ == "__main__":
    main()
