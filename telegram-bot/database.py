"""
Database initialization and migration management for the Telegram bot.

Follows the same pattern as game-server/database.py:
- A global MIGRATIONS list tracks schema changes by version number.
- init_db() creates the migrations table, all current tables with full
  schemas, then applies any pending migrations to bring existing databases
  up to date.

This means a fresh database gets every column from CREATE TABLE and skips
all migrations. An existing database gets only the columns it's missing.
"""

import logging
import os
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("PLAYER_STATE_DB", "/app/player_states.db")

# ---------------------------------------------------------------------------
# Migrations — ordered list of (version, SQL) tuples.
#
# All schema changes go here as ALTER TABLE statements.  The CREATE TABLE in
# init_db() already has every column from every migration, so a fresh DB
# skips the full migration list.  Existing DBs that lack columns catch up via
# these entries.
# ---------------------------------------------------------------------------

MIGRATIONS: list[tuple[int, str]] = [
    (1, "ALTER TABLE player_states ADD COLUMN last_briefing_day_sent INTEGER DEFAULT NULL;"),
    (2, "ALTER TABLE player_states ADD COLUMN language TEXT DEFAULT 'en';"),
    (3, "ALTER TABLE player_states ADD COLUMN current_question_text TEXT DEFAULT NULL;"),
    (4, "ALTER TABLE player_states ADD COLUMN current_question_image_url TEXT DEFAULT NULL;"),
    (5, "ALTER TABLE player_states RENAME COLUMN last_briefing_day_sent TO last_briefing_turn_sent;"),
    (
        6,
        """
        CREATE TABLE IF NOT EXISTS push_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id   INTEGER NOT NULL,
            push_type   TEXT NOT NULL,
            payload     TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            error       TEXT DEFAULT NULL,
            retry_count INTEGER DEFAULT 0,
            turn        INTEGER DEFAULT NULL,
            game_id     TEXT DEFAULT NULL,
            created_at  TEXT NOT NULL,
            sent_at     TEXT DEFAULT NULL
        );
        """,
    ),
]


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def get_db_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Create a new SQLite connection with WAL mode and dict row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    """Initialize database: create tables and apply pending migrations.

    Safe to call multiple times — uses IF NOT EXISTS and tracks applied
    migrations in the ``migrations`` table.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.executescript(
        """
    CREATE TABLE IF NOT EXISTS migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS player_states (
        player_id                INTEGER  PRIMARY KEY,
        game_id                  TEXT,
        onboarding_session_id    TEXT,
        current_question         INTEGER  DEFAULT 0,
        current_question_id      INTEGER,
        current_options          TEXT,
        current_question_text    TEXT     DEFAULT NULL,
        current_question_image_url TEXT    DEFAULT NULL,
        last_poll                TEXT,
        pending_updates          TEXT     DEFAULT '[]',
        last_briefing_turn_sent   INTEGER  DEFAULT NULL,
        language                 TEXT     DEFAULT 'en',
        created_at               TEXT     DEFAULT (datetime('now')),
        updated_at               TEXT     DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS "references" (
        referred_id  INTEGER  NOT NULL,
        referrer_id  INTEGER  NOT NULL,
        game_id      TEXT     NOT NULL,
        created_at   TEXT     DEFAULT (datetime('now')),
        PRIMARY KEY (referred_id, referrer_id, game_id)
    );

    CREATE TABLE IF NOT EXISTS push_queue (
        id          INTEGER  PRIMARY KEY AUTOINCREMENT,
        player_id   INTEGER  NOT NULL,
        push_type   TEXT     NOT NULL,
        payload     TEXT     NOT NULL,
        status      TEXT     NOT NULL DEFAULT 'pending',
        error       TEXT     DEFAULT NULL,
        retry_count INTEGER  DEFAULT 0,
        turn        INTEGER  DEFAULT NULL,
        game_id     TEXT     DEFAULT NULL,
        created_at  TEXT     NOT NULL,
        sent_at     TEXT     DEFAULT NULL
    );
    """
    )

    # Apply pending migrations
    cursor.execute("SELECT COALESCE(MAX(version), 0) FROM migrations")
    current_version: int = cursor.fetchone()[0]

    for version, sql in MIGRATIONS:
        if version > current_version:
            try:
                cursor.executescript(sql)
                cursor.execute(
                    "INSERT INTO migrations (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now().isoformat()),
                )
                conn.commit()
                logger.info("[DB] Applied migration #%d", version)
            except Exception as e:
                logger.warning("[DB] Migration #%d failed (may already exist): %s", version, e)
                conn.rollback()

    conn.close()


# ── Push Queue helpers ────────────────────────────────────────────


def insert_push_message(
    player_id: int,
    push_type: str,
    payload: str,
    turn: int | None = None,
    game_id: str | None = None,
    db_path: str = DB_PATH,
) -> int:
    """Insert a pending push message. Returns the new row id."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO push_queue (player_id, push_type, payload, status, turn, game_id, created_at)
           VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
        (player_id, push_type, payload, turn, game_id, datetime.now().isoformat()),
    )
    row_id: int = cursor.lastrowid or 0
    conn.commit()
    conn.close()
    return row_id


def mark_push_sent(push_id: int, db_path: str = DB_PATH) -> bool:
    """Mark a push message as successfully sent."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE push_queue SET status = 'sent', sent_at = ? WHERE id = ?",
        (datetime.now().isoformat(), push_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def mark_push_failed(push_id: int, error: str, db_path: str = DB_PATH) -> bool:
    """Mark a push message as failed, incrementing retry_count."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE push_queue SET status = 'failed', error = ?, retry_count = retry_count + 1 WHERE id = ?",
        (error, push_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def mark_push_expired(push_id: int, db_path: str = DB_PATH) -> bool:
    """Mark a push message as expired (turn already passed)."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE push_queue SET status = 'expired', sent_at = ? WHERE id = ?",
        (datetime.now().isoformat(), push_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def get_pending_push_messages(db_path: str = DB_PATH) -> list[dict]:
    """Get all pending push messages, ordered by id (insertion order)."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM push_queue WHERE status = 'pending' ORDER BY id")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_for_player(player_id: int, db_path: str = DB_PATH) -> list[dict]:
    """Get pending push messages for a specific player, ordered by id."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM push_queue WHERE status = 'pending' AND player_id = ? ORDER BY id",
        (player_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
