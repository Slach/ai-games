"""
Game Scheduler — HTTP API service that triggers game-server on a schedule.

Manages multiple game schedules independently. Each game has its own
schedule (interval or daily time). Schedules are persisted per game in
scheduler.db and set either at game creation (via first player) or by
the GM via /gm_schedule.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from aiohttp import web

from database import (
    init_db,
    load_game_schedule,
    save_game_schedule,
    delete_game_schedule,
    list_game_schedules,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Get configuration from environment
GAME_SERVER_API_URL = os.getenv("GAME_SERVER_API_URL", "http://game-server:8000")
DEFAULT_SCHEDULE_RAW = os.getenv("GAME_SCHEDULE", os.getenv("GAME_SCHEDULE_TIME", "8h"))
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


def _compute_next_run(schedule_type: str, schedule_value: str | int, from_time: datetime | None = None) -> datetime:
    """Compute next run time from a given from_time (default now)."""
    now = from_time or datetime.now(timezone.utc)

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


def _schedule_label(schedule_type: str, schedule_value: str | int) -> str:
    """Human-readable label for a schedule."""
    if schedule_type == "daily":
        return f"daily at {schedule_value}"
    return f"every {schedule_value}s"


DEFAULT_SCHEDULE = parse_schedule(DEFAULT_SCHEDULE_RAW)


class GameScheduleState:
    """Mutable in-memory state for one game's schedule."""

    def __init__(self, game_id: str, row: dict[str, Any] | None):
        self.game_id = game_id
        self.mode: str = "scheduled"

        if row:
            self.mode = row.get("mode", "scheduled")
            self.schedule_type = row.get("schedule_type", DEFAULT_SCHEDULE[0])
            # schedule_value is stored as string in DB; for interval it's "28800"
            self.schedule_value: str = row.get("schedule_value", str(DEFAULT_SCHEDULE[1]))
            raw_last = row.get("last_run_at")
            self.last_generation: datetime | None = datetime.fromisoformat(raw_last).replace(tzinfo=timezone.utc) if raw_last else None
            raw_next = row.get("next_run_at")
            self.next_run_at: datetime | None = datetime.fromisoformat(raw_next).replace(tzinfo=timezone.utc) if raw_next else None
        else:
            self.schedule_type = DEFAULT_SCHEDULE[0]
            self.schedule_value = str(DEFAULT_SCHEDULE[1])
            self.last_generation: datetime | None = None
            self.next_run_at: datetime | None = None
            # Seed defaults
            save_game_schedule(
                game_id=game_id,
                mode=self.mode,
                schedule_type=self.schedule_type,
                schedule_value=self.schedule_value,
            )

        self.next_run_at = self.next_run_at or _compute_next_run(self.schedule_type, self.schedule_value)

    def get_schedule_tuple(self) -> tuple[str, str]:
        """Return (type, value_str) for this state."""
        return (self.schedule_type, str(self.schedule_value))

    def persist(self) -> None:
        save_game_schedule(
            game_id=self.game_id,
            mode=self.mode,
            schedule_type=self.schedule_type,
            schedule_value=self.schedule_value,
            last_run_at=self.last_generation.isoformat() if self.last_generation else None,
            next_run_at=self.next_run_at.isoformat() if self.next_run_at else None,
        )

    def reset_timer(self) -> datetime:
        self.next_run_at = _compute_next_run(self.schedule_type, self.schedule_value)
        self.persist()
        return self.next_run_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "mode": self.mode,
            "schedule_type": self.schedule_type,
            "schedule_value": str(self.schedule_value),
            "last_run_at": self.last_generation.isoformat() if self.last_generation else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
        }


class GameScheduler:
    """Scheduler that manages per-game schedules and calls game-server to generate turns."""

    def __init__(self):
        self.api_url = GAME_SERVER_API_URL
        self._games: dict[str, GameScheduleState] = {}
        self._paused_games: set[str] = set()  # game_ids paused via /scheduler/pause
        self._loop_running = False
        self._global_paused = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()

        self._load_all_states()

    def _load_all_states(self) -> None:
        """Load all persisted schedules from DB."""
        init_db(default_schedule=DEFAULT_SCHEDULE_RAW)
        rows = list_game_schedules()
        for row in rows:
            gid = row["game_id"]
            state = GameScheduleState(gid, row)
            self._games[gid] = state
            if state.mode == "paused":
                self._paused_games.add(gid)

        logger.info(f"Loaded {len(self._games)} game schedule(s) from DB")
        if not self._games:
            logger.info("No persisted schedules; games must be registered explicitly via /gm_schedule or on first start")

        logger.info(f"Loaded {len(self._games)} game schedule(s) from DB")

    # ── Game management ──

    def register_game(self, game_id: str, schedule_raw: str | None = None) -> GameScheduleState:
        """Register a game with the scheduler. Uses default or provided schedule.

        If the game already exists and was ended, reactivates it.
        """
        existing = self._games.get(game_id)
        if existing:
            if existing.mode == "ended":
                existing.mode = "scheduled"
                self._paused_games.discard(game_id)
                existing.reset_timer()
                existing.persist()
                logger.info(f"Reactivated ended game '{game_id}' with schedule {_schedule_label(existing.schedule_type, existing.schedule_value)}")
            return existing

        row = load_game_schedule(game_id)
        if row:
            # Was persisted from a previous run
            state = GameScheduleState(game_id, row)
        else:
            # Seed with env default or provided schedule
            stype, svalue = parse_schedule(schedule_raw or DEFAULT_SCHEDULE_RAW)
            state = GameScheduleState(game_id, None)
            state.schedule_type = stype
            state.schedule_value = str(svalue)
            state.persist()

        self._games[game_id] = state
        logger.info(f"Registered game '{game_id}' with schedule {_schedule_label(state.schedule_type, state.schedule_value)}")
        return state

    def unregister_game(self, game_id: str) -> bool:
        """Mark a game as ended — keeps schedule in DB for potential restart."""
        state = self._games.get(game_id)
        if not state:
            return False
        state.mode = "ended"
        self._paused_games.add(game_id)  # stop scheduling
        state.persist()
        logger.info(f"Game '{game_id}' marked as ended")
        return True

    def set_schedule(self, game_id: str, schedule_raw: str) -> GameScheduleState:
        """Set a new schedule format for an existing game."""
        state = self._games.get(game_id)
        if not state:
            state = self.register_game(game_id, schedule_raw)
        else:
            stype, svalue = parse_schedule(schedule_raw)
            state.schedule_type = stype
            state.schedule_value = str(svalue)
            state.next_run_at = _compute_next_run(stype, svalue)
            state.persist()
            logger.info(f"Schedule for '{game_id}' set to {_schedule_label(stype, svalue)}, next run at {state.next_run_at}")
        return state

    def pause_game(self, game_id: str) -> bool:
        """Pause scheduling for one game."""
        state = self._games.get(game_id)
        if not state:
            return False
        state.mode = "paused"
        self._paused_games.add(game_id)
        state.persist()
        logger.info(f"Paused game '{game_id}'")
        return True

    def resume_game(self, game_id: str) -> bool:
        """Resume scheduling for one game (resets timer)."""
        state = self._games.get(game_id)
        if not state:
            return False
        state.mode = "scheduled"
        self._paused_games.discard(game_id)
        state.reset_timer()
        state.persist()
        logger.info(f"Resumed game '{game_id}', next run at {state.next_run_at}")
        return True

    def get_status(self, game_id: str | None = None) -> list[dict[str, Any]] | dict[str, Any]:
        """Return status for one or all games."""
        if game_id:
            state = self._games.get(game_id)
            return state.to_dict() if state else {"error": "unknown_game", "game_id": game_id}
        return [s.to_dict() for s in self._games.values()]

    # ── Scheduling loop ──

    async def _generate_turn_for_game(self, game_id: str) -> dict[str, Any]:
        """Generate the next turn for a specific game."""
        logger.info(f"=== SCHEDULED TURN STARTED for game '{game_id}' ===")

        state = self._games.get(game_id)
        if not state:
            return {"status": "error", "message": f"Game '{game_id}' not registered"}

        # Step 0: Check if game has started (>= 3 players)
        game_started = await self.is_game_started(game_id)
        if not game_started:
            logger.info(f"Game '{game_id}' not started yet — waiting for more players (need at least 3)")
            return {"status": "game_not_started", "message": "Game has not started yet, waiting for more players"}

        # Step 1: Validate game state
        is_active = await self.validate_game_active(game_id)
        if not is_active:
            logger.warning(f"Game '{game_id}' ended — stopping generation")
            return {"status": "game_ended", "message": "Game has ended, no new episode generated"}

        # Step 2: Get current game state
        api_state = await self.check_game_state(game_id)
        current_turn = api_state.get("turn", 1)
        logger.info(f"Scheduled turn for game '{game_id}', Turn {current_turn}")

        # Step 3: Auto-select actions for unresponsive players from the PREVIOUS turn.
        if current_turn > 1:
            prev_turn = current_turn - 1
            logger.info(f"Checking players for auto-selection on turn {prev_turn} in game '{game_id}'")
            await self.check_and_auto_select_actions(game_id, prev_turn)

        # Step 4: Trigger the next turn
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/admin/continue-game",
                    params={
                        "game_id": game_id,
                        "language": "en",
                    },
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"API error for game '{game_id}': {resp.status} - {error_text}")
                        raise Exception(f"API error: {resp.status}")

                    result = await resp.json()
                    state.last_generation = datetime.now(timezone.utc)
                    state.reset_timer()
                    state.persist()

                    logger.info(f"=== SCHEDULED TURN COMPLETED for game '{game_id}' ===")
                    logger.info(f"Turn {current_turn} generation submitted: {result.get('status')}")
                    return result

        except Exception as e:
            logger.error(f"Failed to generate turn for game '{game_id}': {e}", exc_info=True)
            raise

    async def run_scheduling_loop(self):
        """Run the scheduling loop for all registered games."""
        if self._loop_running:
            return
        self._loop_running = True
        active = sum(1 for s in self._games.values() if s.mode not in ("paused", "ended"))
        logger.info(f"Starting multi-game scheduling loop ({active} active of {len(self._games)} total game(s))")

        while True:
            try:
                await self._pause_event.wait()

                now = datetime.now(timezone.utc)
                # Find the game whose next_run_at is nearest
                nearest: tuple[str, datetime] | None = None
                for gid, state in list(self._games.items()):
                    if gid in self._paused_games or state.mode == "ended":
                        continue
                    if state.next_run_at is None or state.next_run_at <= now:
                        state.next_run_at = _compute_next_run(state.schedule_type, state.schedule_value)
                        state.persist()
                    if nearest is None or state.next_run_at < nearest[1]:
                        nearest = (gid, state.next_run_at)

                if nearest is None:
                    # No scheduled games — sleep and re-check
                    await asyncio.sleep(60)
                    continue

                gid, next_time = nearest
                delay = (next_time - now).total_seconds()
                if delay > 0:
                    total_next = f"next: game='{gid}' in {delay / 3600:.1f}h ({delay:.0f}s)"
                    logger.info(total_next)
                    await asyncio.sleep(min(delay, 3600))  # wake at most every hour

                # Fine-grained loop: check which games are due now
                due_games = []
                now = datetime.now(timezone.utc)
                for gid, state in list(self._games.items()):
                    if gid in self._paused_games:
                        continue
                    if state.next_run_at and state.next_run_at <= now:
                        due_games.append(gid)

                if not due_games:
                    continue

                for gid in due_games:
                    result = await self._generate_turn_for_game(gid)
                    if result.get("status") == "game_ended":
                        logger.info(f"Game '{gid}' has ended, marking as ended")
                        self.unregister_game(gid)

            except asyncio.CancelledError:
                break
            except (OSError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"Error in scheduling loop: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def run_single_generation(self, game_id: str | None = None):
        """Run a single generation for a specific game or the first registered."""
        gid = game_id or next(iter(self._games.keys()), GAME_ID)
        logger.info(f"Running single generation for game '{gid}'")
        result = await self._generate_turn_for_game(gid)
        logger.info(f"Result: {result}")
        return result

    # ── API client methods (per-game) ──

    async def check_game_state(self, game_id: str) -> dict[str, Any]:
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(f"{self.api_url}/game/state", params={"game_id": game_id}) as resp,
            ):
                if resp.status != 200:
                    raise Exception(f"API error: {resp.status}")
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to get game state for '{game_id}': {e}", exc_info=True)
            raise

    async def validate_game_active(self, game_id: str) -> bool:
        try:
            state = await self.check_game_state(game_id)
            return state.get("status") == "active" and state.get("ship_alive", True) and state.get("crew_health", 0) > 0
        except Exception as e:
            logger.error(f"Failed to validate game '{game_id}' active: {e}", exc_info=True)
            return False

    async def is_game_started(self, game_id: str) -> bool:
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
            logger.error(f"Failed to check game started for '{game_id}': {e}", exc_info=True)
            return False

    async def get_players_in_game(self, game_id: str) -> list[int]:
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
                return []
        except Exception as e:
            logger.error(f"Failed to get players in game '{game_id}': {e}", exc_info=True)
            return []

    async def check_and_auto_select_actions(self, game_id: str, turn: int):
        try:
            player_ids = await self.get_players_in_game(game_id)
            if not player_ids:
                logger.info(f"No players in game '{game_id}'")
                return
            logger.info(f"Checking {len(player_ids)} players for action selection on turn {turn} in game '{game_id}'")
            for player_id in player_ids:
                async with (
                    aiohttp.ClientSession() as session,
                    session.get(f"{self.api_url}/game/briefing/{player_id}/{turn}") as resp,
                ):
                    if resp.status == 200:
                        briefing = await resp.json()
                        if briefing.get("selected_action_id"):
                            continue
                logger.info(f"Player {player_id} (game '{game_id}') has not selected action, auto-selecting")
                await self._select_auto_action(game_id, player_id, turn)
        except Exception as e:
            logger.error(f"Failed to check auto-select for game '{game_id}': {e}", exc_info=True)

    async def _select_auto_action(self, game_id: str, player_id: int, turn: int) -> dict[str, Any] | None:
        try:
            logger.info(f"[AUTO_ACTION] Calling LLM auto-action for player {player_id} in game '{game_id}' on turn {turn}")
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    f"{self.api_url}/game/auto-action/{player_id}/{turn}",
                    params={"language": "en", "game_id": game_id},
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


# ── HTTP API handlers ──


async def handle_status(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    game_id = request.query.get("game_id")
    status = scheduler.get_status(game_id=game_id)
    return web.json_response(status)


async def handle_register_game(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    game_id = request.match_info.get("game_id", "")
    if not game_id:
        return web.json_response({"status": "error", "message": "Missing game_id"}, status=400)

    # Optional schedule override in query or body
    schedule_raw = request.query.get("schedule")
    if not schedule_raw:
        try:
            body = await request.json()
            schedule_raw = body.get("schedule")
        except Exception:
            schedule_raw = None  # Body missing or not JSON — use default schedule

    state = scheduler.register_game(game_id, schedule_raw=schedule_raw)
    return web.json_response({"status": "ok", "game": state.to_dict()})


async def handle_unregister_game(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    game_id = request.match_info.get("game_id", "")
    if not game_id:
        return web.json_response({"status": "error", "message": "Missing game_id"}, status=400)
    deleted = scheduler.unregister_game(game_id)
    return web.json_response({"status": "ok" if deleted else "not_found"})


async def handle_set_schedule(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    game_id = request.match_info.get("game_id", "")
    if not game_id:
        return web.json_response({"status": "error", "message": "Missing game_id"}, status=400)

    try:
        body = await request.json()
        schedule_raw = body.get("schedule", "")
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON body"}, status=400)

    if not schedule_raw.strip():
        return web.json_response({"status": "error", "message": "Missing 'schedule' field"}, status=400)

    try:
        parse_schedule(schedule_raw)  # validate
    except ValueError as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)

    state = scheduler.set_schedule(game_id, schedule_raw)
    return web.json_response({"status": "ok", "game": state.to_dict()})


async def handle_pause(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    game_id = request.query.get("game_id", "")
    if not game_id:
        return web.json_response({"status": "error", "message": "Missing game_id query param"}, status=400)
    ok = scheduler.pause_game(game_id)
    return web.json_response({"status": "ok" if ok else "not_found"})


async def handle_resume(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    game_id = request.query.get("game_id", "")
    if not game_id:
        return web.json_response({"status": "error", "message": "Missing game_id query param"}, status=400)
    ok = scheduler.resume_game(game_id)
    return web.json_response({"status": "ok" if ok else "not_found"})


async def handle_generate_now(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    game_id = request.query.get("game_id", "")
    logger.info(f"POST /scheduler/generate-now — manual generation for game '{game_id}'")
    result = await scheduler.run_single_generation(game_id=game_id or None)
    return web.json_response(result)


def create_app() -> web.Application:
    """Create aiohttp application with per-game scheduler endpoints."""
    app = web.Application()

    scheduler = GameScheduler()
    app["scheduler"] = scheduler

    # ── Per-game management ──
    app.router.add_get("/scheduler/status", handle_status)
    app.router.add_post("/scheduler/register/{game_id}", handle_register_game)
    app.router.add_delete("/scheduler/game/{game_id}", handle_unregister_game)
    app.router.add_post("/scheduler/schedule/{game_id}", handle_set_schedule)
    app.router.add_post("/scheduler/pause", handle_pause)
    app.router.add_post("/scheduler/resume", handle_resume)

    # Legacy endpoints (backwards compatibility for single-game setups)
    # Also aliased for game-specific requests via query param
    async def handle_legacy_pause(request: web.Request) -> web.Response:
        scheduler: GameScheduler = request.app["scheduler"]
        game_id = request.query.get("game_id", "") or os.getenv("GAME_ID", "default_game")
        if not game_id:
            return web.json_response({"status": "error", "message": "No game_id"}, status=400)
        ok = scheduler.pause_game(game_id)
        return web.json_response({"status": "ok" if ok else "not_found"})

    async def handle_legacy_resume(request: web.Request) -> web.Response:
        scheduler: GameScheduler = request.app["scheduler"]
        game_id = request.query.get("game_id", "") or os.getenv("GAME_ID", "default_game")
        if not game_id:
            return web.json_response({"status": "error", "message": "No game_id"}, status=400)
        ok = scheduler.resume_game(game_id)
        return web.json_response({"status": "ok" if ok else "not_found"})

    app.router.add_post("/scheduler/reset", handle_generate_now)
    app.router.add_post("/scheduler/generate-now", handle_generate_now)

    # ── Start scheduling loop ──
    async def start_scheduler(app: web.Application) -> None:
        mode = os.getenv("GAME_SCHEDULER_MODE", "scheduled").lower()
        sched: GameScheduler = app["scheduler"]
        if mode == "single":
            logger.info("Running in single mode (one generation)")
            asyncio.create_task(sched.run_single_generation())
        else:
            logger.info("Running in multi-game scheduled mode")
            asyncio.create_task(sched.run_scheduling_loop())

    app.on_startup.append(start_scheduler)
    return app


def main():
    """Main entry point."""
    logger.info("Starting Game Scheduler HTTP API (per-game scheduling)")
    logger.info(f"GAME_SERVER_API_URL: {GAME_SERVER_API_URL}")
    logger.info(f"DEFAULT_SCHEDULE: {_schedule_label(DEFAULT_SCHEDULE[0], DEFAULT_SCHEDULE[1])}")
    logger.info(f"GAME_SCHEDULER_PORT: {GAME_SCHEDULER_PORT}")

    app = create_app()
    host = os.getenv("GAME_SCHEDULER_HOST", "0.0.0.0")
    web.run_app(app, host=host, port=GAME_SCHEDULER_PORT)


if __name__ == "__main__":
    main()
