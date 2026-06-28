# Game Scheduler Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename game-scheduler → game-scheduler, convert it to an HTTP API service with scheduler state persistence, synchronize /gm_* commands with scheduling timer, fix "day"→"turn" terminology everywhere.

**Architecture:** game-scheduler becomes an aiohttp API server (port 8001) with the
scheduling loop as a background task. game-server calls game-scheduler's
`/scheduler/reset` after each turn. Telegram bot calls `/scheduler/status` for
`/gm_status` and `/gm_list`, and `/scheduler/pause|resume` for `/gm_pause`.

**Tech Stack:** Python 3, aiohttp, SQLite, asyncio

## Global Constraints

- All imports at top of file, never inside functions
- Use actual UTF-8 characters, not `\uXXXX` escape sequences
- LLM prompts go in `prompts.py` only
- Every `logger.error(...)` must include `exc_info=True` or `stack_info=True`
- Never use `contextlib.suppress`
- Database schema changes must use `MIGRATIONS` list
- Use `git mv` for renaming files/directories
- `PYTHONDONTWRITEBYTECODE=1` for all Python invocations
- `game-server/game_server.py` and `game-server/game_server.db` NOT renamed

---

### Task 1: Rename game-scheduler → game-scheduler directory and Docker service

**Files:**

- Rename: `game-scheduler/` → `game-scheduler/` (git mv)
- Modify: `docker-compose.yaml`

**Interfaces:**

- Produces: `game-scheduler/` directory with existing files, updated Docker service name

- [ ] **Step 1: Rename directory**

```bash
cd /home/slach/src/github.com/Slach/ai-games
git mv game-scheduler/ game-scheduler/
```

- [ ] **Step 2: Update docker-compose.yaml — service name and image**

Replace `game-scheduler` service block. Current lines ~139-157, replace with:

```yaml
  # Can be run manually for debugging: docker compose run --rm game-scheduler
  game-scheduler:
    image: game-scheduler:spark-full
    build:
      context: game-scheduler/
      dockerfile: Dockerfile.spark
    ports:
      - "${GAME_SCHEDULER_PORT:-8001}:8001"
    depends_on:
      game-server:
        condition: service_healthy
    environment:
      - GAME_SERVER_API_URL=${GAME_SERVER_API_URL:-http://game-server:8000}
      - GAME_SCHEDULE=${GAME_SCHEDULE:-8h}
      - GAME_SCHEDULER_MODE=${GAME_SCHEDULER_MODE:-scheduled}
      - GAME_SCHEDULER_PORT=8001
      - GAME_ID=${GAME_ID:-default_game}
      - AUTO_ACTION_TIMEOUT_HOURS=${AUTO_ACTION_TIMEOUT_HOURS:-24}
      - PYTHONDONTWRITEBYTECODE=1
    volumes:
      - ./game-scheduler/:/app/
    restart: unless-stopped
```

- [ ] **Step 3: Verify git mv result**

```bash
ls game-scheduler/
# Expect: Dockerfile.spark  game_server.py  __init__.py  pyrightconfig.json  requirements.txt  .ruff_cache
```

- [ ] **Step 4: Quick grep for stale references**

```bash
grep -rn 'game-scheduler' docker-compose.yaml telegram-bot/ game-server/ --include='*.py' --include='*.yaml' --include='*.yml' | grep -v game_server.py | grep -v game_server.db | grep -v GAME_MASTER_ID | grep -v GAME_SERVER_URL
# Should have zero results (only the allowed exceptions remain)
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: rename game-scheduler -> game-scheduler directory and Docker service"
```

---

### Task 2: Create game-scheduler/database.py

**Files:**

- Create: `game-scheduler/database.py`

**Interfaces:**

- Produces: `init_db()`, `get_db_connection()`, `load_scheduler_state() -> dict`, `save_scheduler_state(mode, last_run_at, next_run_at)`

```python
"""
SQLite database for Game Scheduler — persistent scheduler state.
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "scheduler.db"

MIGRATIONS: list[tuple[int, str]] = []


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize database: create tables, apply migrations, seed defaults."""
    conn = get_db_connection()
    cursor = conn.cursor()
    conn.execute("PRAGMA journal_mode=WAL")
    cursor.executescript(
        """
    CREATE TABLE IF NOT EXISTS migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS scheduler_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        mode TEXT NOT NULL DEFAULT 'scheduled',
        last_run_at TEXT,
        next_run_at TEXT,
        schedule_type TEXT NOT NULL DEFAULT 'interval',
        schedule_value TEXT NOT NULL DEFAULT '8h',
        game_id TEXT NOT NULL DEFAULT 'default_game',
        updated_at TEXT NOT NULL
    );
    """
    )
    cursor.execute("SELECT MAX(version) FROM migrations")
    current_version = cursor.fetchone()[0] or 0
    for version, sql in MIGRATIONS:
        if version > current_version:
            cursor.executescript(sql)
            cursor.execute(
                "INSERT INTO migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now().isoformat()),
            )
            conn.commit()
    conn.commit()
    conn.close()
    logger.info("Scheduler database initialized")


def load_scheduler_state() -> dict[str, Any]:
    """Load persisted scheduler state. Returns defaults if no row exists."""
    conn = get_db_connection()
    cursor = conn.cursor()
    row = cursor.execute("SELECT * FROM scheduler_state WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return {}
    return dict(row)


def save_scheduler_state(
    mode: str,
    last_run_at: str | None,
    next_run_at: str | None,
    schedule_type: str,
    schedule_value: str,
    game_id: str,
) -> None:
    """Persist scheduler state (upsert singleton row)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO scheduler_state (id, mode, last_run_at, next_run_at, schedule_type, schedule_value, game_id, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            mode = excluded.mode,
            last_run_at = excluded.last_run_at,
            next_run_at = excluded.next_run_at,
            schedule_type = excluded.schedule_type,
            schedule_value = excluded.schedule_value,
            game_id = excluded.game_id,
            updated_at = excluded.updated_at
        """,
        (mode, last_run_at, next_run_at, schedule_type, schedule_value, game_id, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 1: Write the file**

Write `game-scheduler/database.py` with the content above.

- [ ] **Step 2: Verify it imports cleanly**

```bash
cd game-scheduler && PYTHONDONTWRITEBYTECODE=1 python -c "from database import init_db, load_scheduler_state, save_scheduler_state; init_db(); print('OK')"
# Expect: OK
```

- [ ] **Step 3: Verify DB file created**

```bash
ls -la game-scheduler/scheduler.db
# Expect: file exists
```

- [ ] **Step 4: Test save and load round-trip**

```bash
cd game-scheduler && PYTHONDONTWRITEBYTECODE=1 python -c "
from database import init_db, save_scheduler_state, load_scheduler_state
init_db()
save_scheduler_state('scheduled', '2026-06-27T10:00:00', '2026-06-27T18:00:00', 'interval', '8h', 'default_game')
state = load_scheduler_state()
assert state['mode'] == 'scheduled'
assert state['next_run_at'] == '2026-06-27T18:00:00'
print('PASS')
"
```

- [ ] **Step 5: Commit**

```bash
cd /home/slach/src/github.com/Slach/ai-games
git add game-scheduler/database.py
git commit -m "feat: add game-scheduler/database.py with scheduler_state persistence"
```

---

### Task 3: Refactor game-scheduler/game_server.py → GameScheduler HTTP API

**Files:**

- Modify: `game-scheduler/game_server.py` (full rewrite)

**Interfaces:**

- Consumes: `game-scheduler/database.py` — `init_db()`, `load_scheduler_state()`, `save_scheduler_state()`
- Produces: `GameScheduler` class, HTTP endpoints on port 8001
- Endpoints: `GET /scheduler/status`, `POST /scheduler/reset`, `POST /scheduler/pause`, `POST /scheduler/resume`, `POST /scheduler/trigger`

The file needs a full rewrite. Key changes from current:

1. Class rename: `GameMasterScheduler` → `GameScheduler`
2. All "day" terminology → "turn" (variables, comments, docstrings)
3. Env var: `GAME_SERVER_URL` → `GAME_SERVER_API_URL`  
4. Env var: `GAME_MASTER_MODE` → `GAME_SCHEDULER_MODE`
5. Add `self.mode`, `self.next_run_at` attributes, loaded from DB on init
6. Add `self._pause_event = asyncio.Event()` for pause mechanism
7. Scheduling loop uses `next_run_at` and respects pause via `_pause_event.wait()`
8. Add aiohttp web application with scheduler endpoints
9. `main()` starts both the web server and scheduling loop

Here's the full rewritten file:

```python
"""
Game Scheduler — HTTP API service that triggers game-server on a schedule.

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
GAME_SERVER_API_URL = os.getenv("GAME_SERVER_API_URL", "http://game-server:8000")
GAME_SCHEDULE_RAW = os.getenv("GAME_SCHEDULE", os.getenv("GAME_SCHEDULE_TIME", "8h"))
GAME_SCHEDULER_PORT = int(os.getenv("GAME_SCHEDULER_PORT", "8001"))
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
    """Scheduler that calls game-server to generate turns on a schedule."""

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

        logger.info(
            f"GameScheduler initialized: mode={self.mode}, "
            f"next_run_at={self.next_run_at.isoformat() if self.next_run_at else 'none'}"
        )

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
        logger.info(f"Timer reset: next run at {self.next_run_at.isoformat()}")
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
        logger.info(f"Scheduler resumed, next run at {self.next_run_at.isoformat()}")
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

    # ── API client methods (unchanged logic, "day"→"turn" in names/comments) ──

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
                    logger.info(
                        f"[AUTO_ACTION] LLM selected '{result.get('action_id', '?')}' "
                        f"for player {player_id}: {result.get('action_text', '')[:60]}..."
                    )
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
    return web.json_response(scheduler.get_status())


async def handle_reset(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    next_run = scheduler.reset_timer()
    return web.json_response({"status": "ok", "next_run_at": next_run.isoformat()})


async def handle_pause(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    scheduler.pause()
    return web.json_response({"status": "ok", "mode": "paused"})


async def handle_resume(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    next_run = scheduler.resume()
    return web.json_response({"status": "ok", "mode": "scheduled", "next_run_at": next_run.isoformat()})


async def handle_trigger(request: web.Request) -> web.Response:
    scheduler: GameScheduler = request.app["scheduler"]
    try:
        result = await scheduler.generate_scheduled_turn()
        # Reset timer after manual trigger
        scheduler.reset_timer()
        return web.json_response(result)
    except Exception as e:
        logger.error(f"Trigger failed: {e}", exc_info=True)
        return web.json_response({"status": "error", "error": str(e)}, status=500)


def create_app() -> web.Application:
    """Create aiohttp application with scheduler endpoints."""
    app = web.Application()

    scheduler = GameScheduler()
    app["scheduler"] = scheduler

    app.router.add_get("/scheduler/status", handle_status)
    app.router.add_post("/scheduler/reset", handle_reset)
    app.router.add_post("/scheduler/pause", handle_pause)
    app.router.add_post("/scheduler/resume", handle_resume)
    app.router.add_post("/scheduler/trigger", handle_trigger)

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
    web.run_app(app, host="0.0.0.0", port=GAME_SCHEDULER_PORT)


if __name__ == "__main__":
    main()
```

- [ ] **Step 1: Write the file**

Write `game-scheduler/game_server.py` with the content above (full replacement).

- [ ] **Step 2: Verify it imports**

```bash
cd game-scheduler && PYTHONDONTWRITEBYTECODE=1 python -c "from game_master import GameScheduler, create_app; print('Import OK')"
```

- [ ] **Step 3: Start the server briefly and test endpoints**

```bash
cd game-scheduler && PYTHONDONTWRITEBYTECODE=1 timeout 5 python -c "
import asyncio
from game_master import create_app
from aiohttp import web

async def test():
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 18001)
    await site.start()

    import aiohttp
    async with aiohttp.ClientSession() as s:
        # Test status
        async with s.get('http://localhost:18001/scheduler/status') as r:
            data = await r.json()
            print('Status:', data)

        # Test reset
        async with s.post('http://localhost:18001/scheduler/reset') as r:
            data = await r.json()
            print('Reset:', data)

        # Test pause
        async with s.post('http://localhost:18001/scheduler/pause') as r:
            data = await r.json()
            print('Pause:', data)

        # Test resume
        async with s.post('http://localhost:18001/scheduler/resume') as r:
            data = await r.json()
            print('Resume:', data)

    await runner.cleanup()

asyncio.run(test())
" 2>&1 | head -20
# Expect: Status dict with schedule_type, mode etc., Reset/Pause/Resume responses
```

- [ ] **Step 4: Commit**

```bash
git add game-scheduler/game_server.py
git commit -m "refactor: GameScheduler HTTP API with pause/resume/reset/trigger endpoints"
```

---

### Task 4: game-server — "day" → "turn" + scheduler callback

**Files:**

- Modify: `game-server/main.py`

**Interfaces:**

- Consumes: game-scheduler HTTP API at `{GAME_SCHEDULER_URL}`
- Produces: `_notify_scheduler(action)` function, renamed functions

Changes in `game-server/main.py`:

1. Add env var near other env vars (around line 70-90):

```python
GAME_SCHEDULER_URL = os.getenv("GAME_SCHEDULER_URL", "http://game-scheduler:8001")
```

1. Add helper function after imports but before route handlers:

```python
async def _notify_scheduler(action: str) -> None:
    """Fire-and-forget notification to game-scheduler after a turn event."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GAME_SCHEDULER_URL}/scheduler/{action}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Scheduler notification '{action}' returned {resp.status}")
                else:
                    logger.info(f"Scheduler notified: {action}")
    except Exception as e:
        logger.warning(f"Failed to notify scheduler ({action}): {e}")
```

1. Rename `generate_daily_episode` → `generate_turn_episode` (line ~3090):

```python
async def generate_turn_episode(
```

Update docstring and all comments within the function: "day" → "turn", "daily" → "turn".

1. Rename `admin_analyze_day` → `admin_analyze_turn` (line ~4473):

```python
async def admin_analyze_turn(
```

1. In `_background_continue_wrapper` (line ~4511), after successful result:

```python
        if result and result.get("status") == "success":
            await _notify_scheduler("reset")
            await push_gm_notification(
```

(Add the `await _notify_scheduler("reset")` line before `push_gm_notification`.)

1. In `/admin/start-game` handler (~line 3416), after successful start:
Find the return statement and add before it:

```python
        asyncio.create_task(_notify_scheduler("reset"))
```

1. In `/admin/restart-game` handler (~line 5060), after successful restart:
Find the return statement and add before it:

```python
        asyncio.create_task(_notify_scheduler("reset"))
```

1. Fix "day" → "turn" in comments throughout main.py:

- Line ~1586: "days, actions, messages" → "turns, actions, messages"
- Line ~1605: "current day" → "current turn"
- Line ~2296: "Next day hook" → "Next turn hook"
- Line ~2443: "during day generation" → "during turn generation"
- Line ~2777: "before new day briefings" → "before new turn briefings"
- Line ~3114: "previous day summary" → "previous turn summary"
- Line ~4014: "Advance game state to next day" → "Advance game state to next turn"
- Line ~4035: "Day:" → "Turn:"
- Line ~4484: "current completed turn is day-1" → "current completed turn is turn-1"
- Line ~4966: "previous day outcome" → "previous turn outcome"
- Line ~4967: "pushing new day briefings" → "pushing new turn briefings"
- Line ~5037: "before the deleted day" → "before the deleted turn"
- Line ~5039: "the day being regenerated" → "the turn being regenerated"
- Line ~5042: "regenerate the day" → "regenerate the turn"
- Line ~5067: "all game days" → "all game turns"
- Line ~5084: "days" → "turns" (twice)

- [ ] **Step 1: Add GAME_SCHEDULER_URL env var**

Around line 70-90 in `game-server/main.py`, add after other env var definitions:

```python
GAME_SCHEDULER_URL = os.getenv("GAME_SCHEDULER_URL", "http://game-scheduler:8001")
```

- [ ] **Step 2: Add _notify_scheduler function**

Add after imports section (before route handlers), after the existing helper functions.

- [ ] **Step 3: Rename generate_daily_episode → generate_turn_episode**

Use ast-grep or direct edit to rename the function at line 3090.

- [ ] **Step 4: Rename admin_analyze_day → admin_analyze_turn**

Rename the function at line 4473.

- [ ] **Step 5: Add scheduler notification in _background_continue_wrapper**

At line ~4516-4519, add `await _notify_scheduler("reset")`.

- [ ] **Step 6: Add scheduler notification in /admin/start-game**

Before the return in the start-game handler (~line 3460-3470).

- [ ] **Step 7: Add scheduler notification in /admin/restart-game**

Before the return in the restart-game handler.

- [ ] **Step 8: Fix "day" → "turn" in comments**

Edit all the comment lines listed above.

- [ ] **Step 9: Run existing tests**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m unittest discover -s tests -v
# Expect: all tests pass
```

- [ ] **Step 10: Commit**

```bash
git add game-server/main.py
git commit -m "refactor: day->turn terminology, add scheduler callback after turns"
```

---

### Task 5: telegram-bot — /gm_pause, /gm_status + /gm_list scheduler info

**Files:**

- Modify: `telegram-bot/bot.py`
- Modify: `telegram-bot/language.py`

**Interfaces:**

- Consumes: game-scheduler HTTP API at `{GAME_SCHEDULER_URL}`
- Consumes: language strings from `language.py`

### 5a: Language strings

In `telegram-bot/language.py`, add to the `gm_commands` dict for both RU and EN:

For the RU block (around line 104):

```python
        "next_turn_at": "Следующий ход: {time}",
        "next_turn_auto": "Следующий авто-ход: {time}",
        "scheduler_paused": "⚠️ Планировщик на паузе",
        "scheduler_unavailable": "Планировщик недоступен",
        "pause_usage": "❌ Использование: /gm_pause\nПереключает планировщик (пауза/возобновление).",
        "pause_toggled": "Планировщик: {state}",
        "pause_error": "❌ Ошибка переключения планировщика: {error}",
```

For the EN block (around line 110):

```python
        "next_turn_at": "Next turn: {time}",
        "next_turn_auto": "Next auto-turn: {time}",
        "scheduler_paused": "⚠️ Scheduler paused",
        "scheduler_unavailable": "Scheduler unavailable",
        "pause_usage": "❌ Usage: /gm_pause\nToggles the scheduler (pause/resume).",
        "pause_toggled": "Scheduler: {state}",
        "pause_error": "❌ Failed to toggle scheduler: {error}",
```

Also add to `get_gm_commands()` function the new `gm_pause_commands` entry (around line 533-540).

### 5b: /gm_status — add scheduler info

In `cmd_gm_status` (find the function), after building the status message and before sending, add:

```python
    # Fetch scheduler status
    scheduler_url = os.getenv("GAME_SCHEDULER_URL", "http://game-scheduler:8001")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{scheduler_url}/scheduler/status",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    sched = await resp.json()
                    if sched.get("mode") == "paused":
                        lines.append(gm_msgs["scheduler_paused"])
                    elif sched.get("next_run_at"):
                        next_time = sched["next_run_at"]
                        lines.append(gm_msgs["next_turn_at"].format(time=next_time))
    except Exception:
        pass  # Scheduler unavailable, omit the line
```

### 5c: New /gm_pause command

Add handler function and register it:

```python
async def cmd_gm_pause(message: types.Message):
    """GM command: Toggle scheduler pause/resume.
    Usage: /gm_pause
    Only executable by the configured Game Master user.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        gm_msgs = lang.get_gm_commands(player_lang)
        logger.warning(f"Unauthorized /gm_pause attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    gm_msgs = lang.get_gm_commands(player_lang)
    scheduler_url = os.getenv("GAME_SCHEDULER_URL", "http://game-scheduler:8001")

    try:
        # First, check current state
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{scheduler_url}/scheduler/status",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    await message.answer(gm_msgs["scheduler_unavailable"])
                    return
                sched = await resp.json()

            # Toggle: if paused -> resume, else -> pause
            if sched.get("mode") == "paused":
                async with session.post(
                    f"{scheduler_url}/scheduler/resume",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        await message.answer(
                            gm_msgs["pause_toggled"].format(state="resumed"),
                            parse_mode="Markdown",
                        )
                    else:
                        await message.answer(
                            gm_msgs["pause_error"].format(error=f"HTTP {resp.status}"),
                        )
            else:
                async with session.post(
                    f"{scheduler_url}/scheduler/pause",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        await message.answer(
                            gm_msgs["pause_toggled"].format(state="paused"),
                            parse_mode="Markdown",
                        )
                    else:
                        await message.answer(
                            gm_msgs["pause_error"].format(error=f"HTTP {resp.status}"),
                        )
    except Exception as e:
        logger.error(f"Failed to toggle scheduler: {e}", exc_info=True)
        await message.answer(gm_msgs["pause_error"].format(error=e))
```

Register the command handler (add to the list of handlers, alongside other gm_ commands):

```python
dp.message.register(cmd_gm_pause, Command("gm_pause"))
```

### 5d: /gm_list — add scheduler info

In `cmd_gm_list` (around line 2240+), after building the games list and before sending, add:

```python
    # Append scheduler status
    scheduler_url = os.getenv("GAME_SCHEDULER_URL", "http://game-scheduler:8001")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{scheduler_url}/scheduler/status",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    sched = await resp.json()
                    lines.append("")
                    if sched.get("mode") == "paused":
                        lines.append(gm_msgs["scheduler_paused"])
                    elif sched.get("next_run_at"):
                        lines.append(gm_msgs["next_turn_auto"].format(time=sched["next_run_at"]))
    except Exception:
        pass
```

- [ ] **Step 1: Add language strings to telegram-bot/language.py**
- [ ] **Step 2: Add GAME_SCHEDULER_URL env import in bot.py** (near other env vars, around line 78)
- [ ] **Step 3: Modify cmd_gm_status to show scheduler info**
- [ ] **Step 4: Add cmd_gm_pause handler and register**
- [ ] **Step 5: Modify cmd_gm_list to show scheduler info**
- [ ] **Step 6: Verify syntax**

```bash
cd telegram-bot && PYTHONDONTWRITEBYTECODE=1 python -c "import bot; print('Import OK')"
```

- [ ] **Step 7: Commit**

```bash
git add telegram-bot/bot.py telegram-bot/language.py
git commit -m "feat: /gm_pause command, scheduler info in /gm_status and /gm_list"
```

---

### Task 6: Docker Compose — add GAME_SCHEDULER_URL env to services

**Files:**

- Modify: `docker-compose.yaml`

Add `GAME_SCHEDULER_URL` to `game-server` and `telegram-bot` environment blocks:

In `game-server` environment block (find it in docker-compose.yaml), add:

```yaml
      - GAME_SCHEDULER_URL=${GAME_SCHEDULER_URL:-http://game-scheduler:8001}
```

In `telegram-bot` environment block, add:

```yaml
      - GAME_SCHEDULER_URL=${GAME_SCHEDULER_URL:-http://game-scheduler:8001}
```

- [ ] **Step 1: Edit docker-compose.yaml**
- [ ] **Step 2: Commit**

```bash
git add docker-compose.yaml
git commit -m "chore: add GAME_SCHEDULER_URL env to game-server and telegram-bot services"
```

---

### Task 7: Final verification

- [ ] **Step 1: Run game-server tests**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m unittest discover -s tests -v
# Expect: all pass
```

- [ ] **Step 2: Verify game-scheduler starts**

```bash
cd game-scheduler && PYTHONDONTWRITEBYTECODE=1 GAME_SCHEDULER_MODE=single timeout 10 python game_server.py 2>&1 | head -20
# Expect: "Starting Game Scheduler HTTP API", "Running in single mode"
# May error on game-server call (not running) — that's fine
```

- [ ] **Step 3: Grep for remaining "day" in game-scheduler and game-server main.py**

```bash
grep -n 'day\|Day' game-scheduler/game_server.py game-server/main.py 2>/dev/null | grep -v 'daily\|today\|yesterday\|day = \|days=1\|timedelta'
# Expect: zero results (or only "daily" in parse_schedule and _compute_next_run)
```

- [ ] **Step 4: Grep for stale "game-scheduler" references in config files**

```bash
grep -rn 'game-scheduler' docker-compose.yaml .env* 2>/dev/null
# Expect: zero results
```

- [x] **Step 5: Final grep for "GAME_MASTER_MODE"** — renamed to `GAME_SCHEDULER_MODE`

```bash
grep -rn 'GAME_MASTER_MODE' . 2>/dev/null
# Expect: zero results (done)
```

- [ ] **Step 6: Cleanup: remove old scheduler.db if it was created during testing**

```bash
rm -f game-scheduler/scheduler.db
```

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "chore: final verification, remove test artifacts"
```
