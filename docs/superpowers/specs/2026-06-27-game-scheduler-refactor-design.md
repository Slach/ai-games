# Game Scheduler Refactor — Design Spec

**Date:** 2026-06-27
**Status:** approved

## Motivation

1. **Terminology drift:** `game-scheduler/game_master.py` uses "day" everywhere (~22 occurrences) while the rest of the codebase has moved to "turn".
2. **No synchronization:** When GM manually runs `/gm_continue`, the scheduler's timer doesn't know — it keeps its own schedule independently, potentially generating turns too soon after a manual trigger.
3. **Rename:** `game-scheduler` (the scheduling service) → `game-scheduler` to distinguish it from the `GameMasterAgent` (the LLM-based AI agent in `game-server-api`).

## Scope

- `game-scheduler/` directory → renamed to `game-scheduler/`
- `game-scheduler` Docker service → `game-scheduler`
- `game-scheduler/game_master.py` → refactored to HTTP API service with scheduling loop as background task
- `game-server-api/main.py` → residual "day" → "turn" terminology, callback to scheduler
- `telegram-bot/bot.py` → `/gm_status` shows next turn time from scheduler API
- Docker Compose changes

## What stays the same

- `game-server-api/game_master.py` — the LLM `GameMasterAgent` class. NOT renamed. It's the AI game master, not the scheduler.
- `game-server-api/game_master.db` — database file. NOT renamed.
- `TELEGRAM_BOT_GAME_MASTER_ID` — the Telegram user ID of the human Game Master. NOT renamed (it's about the role, not the service).
- The bot's existing env var `GAME_MASTER_API_URL` — still points to game-server-api (used by bot for `/admin/*` calls).
- **Env var renamed in scheduler only:** `GAME_MASTER_API_URL` → `GAME_SERVER_API_URL` in the `game-scheduler` service (it points to game-server-api, the name was misleading). The bot keeps `GAME_MASTER_API_URL` for backward compat.

## Architecture

```
telegram-bot ──GET /scheduler/status──▶ game-scheduler (port 8001)
     │                                      │
     │ POST /admin/continue-game            │ POST /admin/continue-game
     ▼                                      ▼
game-server-api (port 8000) ◀───────────────┘
     │
     │ POST /scheduler/reset (fire-and-forget)
     ▼
game-scheduler (port 8001)
```

## Section 1: game-scheduler refactor

### 1.1 Rename

- `git mv game-scheduler/ game-scheduler/`
- Docker service: `game-scheduler` → `game-scheduler`
- Docker image: `game-scheduler:spark-full` → `game-scheduler:spark-full`
- Env var: `GAME_MASTER_MODE` → `GAME_SCHEDULER_MODE`
- Internal class/file: `GameMasterScheduler` → `GameScheduler`
- All internal variable names and comments "day" → "turn"

### 1.2 HTTP API

Add a minimal aiohttp web server running alongside the scheduling loop in the same asyncio process.

**Port:** `GAME_SCHEDULER_PORT` env (default `8001`).

**Endpoints:**

| Method | Path | Response | Description |
|--------|------|----------|-------------|
| `GET` | `/scheduler/status` | `{schedule_type, schedule_value, last_run_at, next_run_at, mode}` | Current scheduler state |
| `POST` | `/scheduler/reset` | `{status: "ok", next_run_at}` | Reset timer: next_run = now + interval (daily → next HH:MM after now) |
| `POST` | `/scheduler/pause` | `{status: "ok", mode: "paused"}` | Pause scheduling loop |
| `POST` | `/scheduler/resume` | `{status: "ok", mode: "scheduled", next_run_at}` | Resume + reset timer |
| `POST` | `/scheduler/trigger` | `{status, turn, ...}` | Run `generate_scheduled_turn()` now, then reset timer |

### 1.3 Internal state

`GameScheduler` tracks: `last_generation: datetime | None`, `next_run_at: datetime`, `mode: str` ("scheduled" | "paused").

The scheduling loop waits on `next_run_at` rather than computing delay each iteration, so endpoint changes take effect immediately.

### 1.4 Docker

```yaml
game-scheduler:
  image: game-scheduler:spark-full
  build:
    context: game-scheduler/
    dockerfile: Dockerfile.spark
  ports:
    - "${GAME_SCHEDULER_PORT:-8001}:8001"
  depends_on:
    game-server-api:
      condition: service_healthy
  environment:
    - GAME_SERVER_API_URL=${GAME_SERVER_API_URL:-http://game-server-api:8000}
    - GAME_SCHEDULE=${GAME_SCHEDULE:-8h}
    - GAME_SCHEDULER_MODE=${GAME_SCHEDULER_MODE:-scheduled}
    - GAME_SCHEDULER_PORT=8001
```

### 1.5 Database

New `game-scheduler/database.py` following the same pattern as `game-server-api/database.py`:

- SQLite file: `game-scheduler/scheduler.db`
- `MIGRATIONS` list for schema evolution
- `init_db()` creates table + applies pending migrations

**Table `scheduler_state`:**

```sql
CREATE TABLE IF NOT EXISTS scheduler_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
    mode TEXT NOT NULL DEFAULT 'scheduled',
    last_run_at TEXT,
    next_run_at TEXT,
    schedule_type TEXT NOT NULL DEFAULT 'interval',
    schedule_value TEXT NOT NULL DEFAULT '8h',
    game_id TEXT NOT NULL DEFAULT 'default_game',
    updated_at TEXT NOT NULL
);
```

On startup: `init_db()` loads persisted state. If no row exists, inserts defaults from env vars.
On every state change (reset/pause/resume): updates the singleton row.
`GET /scheduler/status` reads from in-memory state (initialized from DB on startup).

## Section 2: game-server-api changes

### 2.1 "day" → "turn"

In `main.py`:

- `generate_daily_episode` → `generate_turn_episode`
- `admin_analyze_day` → `admin_analyze_turn`
- Comments: "previous day", "next day", "day-1" → "previous turn", "next turn", "turn-1"

### 2.2 Scheduler callback

New function `_notify_scheduler(action, game_id, turn)`:

- Fire-and-forget `POST` to `{GAME_SCHEDULER_URL}/scheduler/{action}`
- Called from `_background_continue_wrapper` after successful turn → `POST /scheduler/reset`
- Called from `/admin/start-game` after game starts → `POST /scheduler/reset`
- Called from `/admin/restart-game` after game reset → `POST /scheduler/reset` (game restarted, timer should start fresh)
- `/admin/regenerate-turn` calls `admin_continue_game()` internally → callback happens automatically via `_background_continue_wrapper`
- On connection error: log warning, do not fail the main operation

**Summary of /gm_\* commands and scheduler effects:**

| Command | API endpoint | Scheduler action |
|---------|-------------|-------------------|
| `/gm_start` | `/admin/start-game` | reset timer |
| `/gm_continue` | `/admin/continue-game` | reset timer (via wrapper callback) |
| `/gm_turn` | `/admin/regenerate-turn` | reset timer (internally calls continue-game) |
| `/gm_restart` | `/admin/restart-game` | reset timer |
| `/gm_pause` | — (bot calls scheduler directly) | toggle pause/resume scheduler |
| `/gm_kick` | `/admin/kick-player` | none |
| `/gm_lang` | `/admin/set-language` | none |
| `/gm_list` | `/admin/list-games` + `/scheduler/status` | shows next turn time per game |
| `/gm_status` | `/game/status` + `/scheduler/status` | read-only |

New env var: `GAME_SCHEDULER_URL` (default `http://game-scheduler:8001`).

## Section 3: telegram-bot changes

### 3.1 `/gm_status` enhancement

`cmd_gm_status` makes an additional `GET` to `{GAME_SCHEDULER_URL}/scheduler/status` and appends scheduling info:

- If mode="scheduled": shows "Next turn: {next_run_at}"
- If mode="paused": shows "⚠️ Scheduler paused"
- If scheduler unreachable: line omitted (no error)

### 3.2 New `/gm_pause` command

`cmd_gm_pause` toggles the scheduler between paused and running:

- Calls `POST {GAME_SCHEDULER_URL}/scheduler/pause` or `/scheduler/resume`
- Only executable by the configured Game Master user
- Usage: `/gm_pause` (no args — toggles current state)

### 3.3 `/gm_list` enhancement

`cmd_gm_list` also fetches `GET /scheduler/status` and appends:

- "Scheduler: next turn at {time}" (or "paused") for the configured game
- If scheduler unreachable: line omitted

### 3.4 Language strings

Add to `language.py` `gm_commands` dict:

- `"next_turn_at": "Next turn: {time}"` / `"Следующий ход: {time}"`
- `"scheduler_paused": "Scheduler paused"` / `"Планировщик на паузе"`

## What is NOT included

- Tests for game-scheduler HTTP API (testable later when needed)
- Redis / Celery / message queue (unnecessary complexity at this stage)
