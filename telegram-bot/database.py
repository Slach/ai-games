"""
Database initialization and migration management for the Telegram bot.

Follows the same pattern as game-server-api/database.py:
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
