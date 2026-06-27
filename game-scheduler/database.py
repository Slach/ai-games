"""
SQLite database for Game Scheduler — persistent scheduler state.
"""

import logging
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
