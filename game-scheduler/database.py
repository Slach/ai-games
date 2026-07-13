"""
SQLite database for Game Scheduler — persistent per-game scheduler state.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "scheduler.db"

# ── Migrations ──────────────────────────────────────────────────────
# Each entry: (version, SQL). Applied sequentially on init_db().
# DO NOT edit existing entries — add new ones.

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        -- Upgrade: add per-game schedule table, migrate old single-row data,
        -- apply env-var default for games not yet tracked.
        CREATE TABLE IF NOT EXISTS game_schedules (
            game_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL DEFAULT 'scheduled',
            schedule_type TEXT NOT NULL DEFAULT 'interval',
            schedule_value TEXT NOT NULL DEFAULT '8h',
            last_run_at TEXT,
            next_run_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- Migrate old single-row into per-game entry IF it exists
        INSERT OR IGNORE INTO game_schedules
            (game_id, mode, schedule_type, schedule_value,
             last_run_at, next_run_at, created_at, updated_at)
        SELECT
            game_id, mode, schedule_type, schedule_value,
            last_run_at, next_run_at, updated_at, updated_at
        FROM scheduler_state WHERE id = 1;
        """,
    ),
]


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(default_schedule: str) -> None:
    """Initialize database: create tables, apply migrations, seed defaults.

    Args:
        default_schedule: The env-var default schedule string (e.g. "8h")
                          used when creating entries for games without a
                          persisted schedule.
    """
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
                (version, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
    conn.commit()
    conn.close()
    logger.info("Scheduler database initialized")


# ── Per-game schedule helpers ───────────────────────────────────────


def load_game_schedule(game_id: str) -> dict[str, Any] | None:
    """Load persisted schedule for one game. Returns None if not found."""
    conn = get_db_connection()
    cursor = conn.cursor()
    row = cursor.execute("SELECT * FROM game_schedules WHERE game_id = ?", (game_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def save_game_schedule(
    game_id: str,
    mode: str,
    schedule_type: str,
    schedule_value: str,
    last_run_at: str | None,
    next_run_at: str | None,
) -> None:
    """Upsert a schedule for a specific game."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO game_schedules
            (game_id, mode, schedule_type, schedule_value,
             last_run_at, next_run_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id) DO UPDATE SET
            mode = excluded.mode,
            schedule_type = excluded.schedule_type,
            schedule_value = excluded.schedule_value,
            last_run_at = excluded.last_run_at,
            next_run_at = excluded.next_run_at,
            updated_at = excluded.updated_at
        """,
        (game_id, mode, schedule_type, schedule_value, last_run_at, next_run_at, now, now),
    )
    conn.commit()
    conn.close()


def delete_game_schedule(game_id: str) -> bool:
    """Remove a game from the scheduler. Returns True if deleted."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM game_schedules WHERE game_id = ?", (game_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def list_game_schedules() -> list[dict[str, Any]]:
    """Load all persisted game schedules."""
    conn = get_db_connection()
    cursor = conn.cursor()
    rows = cursor.execute("SELECT * FROM game_schedules ORDER BY game_id").fetchall()
    conn.close()
    return [dict(r) for r in rows]
