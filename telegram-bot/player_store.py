"""
Persistent SQLite storage for player state.

Replaces the in-memory player_states dict with a SQLite-backed store
that survives bot restarts. Stores:
- Which game the player is in
- Onboarding session ID and progress
- Polling timestamps
- Pending game updates

FSM (Finite State Machine) states are already persisted separately
via aiogram_sqlite_storage.SQLStorage (AI_FSM_DB env var).
This store handles the business-level state that the polling loop
and various handlers need across restarts.
"""

import json
import logging
import os
import sqlite3
from contextlib import suppress
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime objects."""

    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


DB_PATH = os.getenv("PLAYER_STATE_DB", "/app/player_states.db")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

DEFAULT_STATE: dict[str, Any] = {
    "game_id": None,
    "onboarding_session_id": None,
    "current_question": 0,
    "current_question_id": None,
    "current_options": None,
    "language": "en",
}


def _conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Create a new SQLite connection with WAL mode and dict row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_states (
            player_id           INTEGER  PRIMARY KEY,
            game_id             TEXT,
            onboarding_session_id TEXT,
            current_question    INTEGER  DEFAULT 0,
            current_question_id INTEGER,
            current_options     TEXT,
            last_poll           TEXT,
            pending_updates     TEXT     DEFAULT '[]',
            last_briefing_day_sent INTEGER DEFAULT NULL,
            created_at          TEXT     DEFAULT (datetime('now')),
            updated_at          TEXT     DEFAULT (datetime('now'))
        )
        """
    )
    # Add columns if they don't exist (for databases created before these migrations)
    with suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE player_states ADD COLUMN last_briefing_day_sent INTEGER DEFAULT NULL")
    with suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE player_states ADD COLUMN language TEXT DEFAULT 'en'")

    # Referral tracking: who invited whom into which game.
    # One row per (referred_id, referrer_id, game_id) — deduplicated via UNIQUE.
    # "references" is double-quoted because it is a SQL keyword (REFERENCES).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS "references" (
            referred_id  INTEGER  NOT NULL,
            referrer_id  INTEGER  NOT NULL,
            game_id      TEXT     NOT NULL,
            created_at   TEXT     DEFAULT (datetime('now')),
            PRIMARY KEY (referred_id, referrer_id, game_id)
        )
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Public API — mirrors the old dict interface exactly
# ---------------------------------------------------------------------------


def get_player_state(player_id: int) -> dict[str, Any]:
    """Get or create player state.

    Returns a dict with the same keys as the old in-memory dict:
      game_id, onboarding_session_id, current_question,
      current_question_id, current_options, last_poll, pending_updates
    """
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM player_states WHERE player_id = ?", (player_id,)).fetchone()

        if row is None:
            # Insert a new default row
            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO player_states
                    (player_id, game_id, onboarding_session_id, current_question,
                     current_question_id, current_options, last_poll, pending_updates, language)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    DEFAULT_STATE["game_id"],
                    DEFAULT_STATE["onboarding_session_id"],
                    DEFAULT_STATE["current_question"],
                    DEFAULT_STATE["current_question_id"],
                    json.dumps(DEFAULT_STATE["current_options"]) if DEFAULT_STATE["current_options"] else None,
                    now,
                    "[]",
                    DEFAULT_STATE["language"],
                ),
            )
            conn.commit()
            return {
                "player_id": player_id,
                "game_id": None,
                "onboarding_session_id": None,
                "current_question": 0,
                "current_question_id": None,
                "current_options": None,
                "last_poll": datetime.now(),
                "pending_updates": [],
                "language": DEFAULT_STATE["language"],
            }

        # Convert DB row to the dict format the rest of bot.py expects
        pending_raw = row["pending_updates"] or "[]"
        pending_list = json.loads(pending_raw)

        # Convert ISO timestamps back to datetime
        last_poll_str = row["last_poll"]
        last_poll = datetime.fromisoformat(last_poll_str) if last_poll_str else datetime.now()

        current_options_raw = row["current_options"]
        current_options = json.loads(current_options_raw) if current_options_raw else None

        return {
            "player_id": row["player_id"],
            "game_id": row["game_id"],
            "onboarding_session_id": row["onboarding_session_id"],
            "current_question": row["current_question"] or 0,
            "current_question_id": row["current_question_id"],
            "current_options": current_options,
            "last_poll": last_poll,
            "pending_updates": pending_list,
            "last_briefing_day_sent": row["last_briefing_day_sent"],
            "language": row["language"] or "en",
        }
    finally:
        conn.close()


def update_player_state(player_id: int, **kwargs: Any) -> None:
    """Update player state columns.

    Accepts the same keyword arguments that were previously written to the
    in-memory dict. Datetime values are serialised to ISO strings, lists/dicts
    to JSON strings.
    """
    conn = _conn()
    try:
        # Build SET clause dynamically
        set_parts: list[str] = []
        values: list[Any] = []

        for key, value in kwargs.items():
            if key not in (
                "game_id",
                "onboarding_session_id",
                "current_question",
                "current_question_id",
                "current_options",
                "last_poll",
                "pending_updates",
                "last_briefing_day_sent",
                "language",
            ):
                logger.warning("Unknown player_state key '%s' — skipping", key)
                continue

            # Serialise non-scalar types
            if key == "last_poll" and isinstance(value, datetime):
                value = value.isoformat()
            elif key in ("pending_updates", "current_options") and value is not None:
                value = json.dumps(value, cls=DateTimeEncoder)

            set_parts.append(f"{key} = ?")
            values.append(value)

        if not set_parts:
            return  # nothing to update

        # Always bump updated_at
        set_parts.append("updated_at = datetime('now')")
        values.append(player_id)

        conn.execute(
            f"UPDATE player_states SET {', '.join(set_parts)} WHERE player_id = ?",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def get_all_briefing_days() -> dict[int, int]:
    """Return a dict of {player_id: last_briefing_day_sent} for all players
    that have a non-NULL value. Single query — avoids N+1 on startup."""
    conn = _conn()
    try:
        result: dict[int, int] = {}
        for row in conn.execute("SELECT player_id, last_briefing_day_sent FROM player_states WHERE last_briefing_day_sent IS NOT NULL"):
            result[row["player_id"]] = int(row["last_briefing_day_sent"])
        return result
    finally:
        conn.close()


def delete_player_state(player_id: int) -> None:
    """Remove a player's state row entirely (cleanup on profile deletion)."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM player_states WHERE player_id = ?", (player_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Referral tracking (references table)
# ---------------------------------------------------------------------------


def record_reference(referred_id: int, referrer_id: int, game_id: str) -> bool:
    """Record that ``referrer_id`` invited ``referred_id`` into ``game_id``.

    Silently no-ops when the referrer and the referred player are the same
    person (self-referral) or when ``game_id`` is empty. The row is
    deduplicated via the table PRIMARY KEY, so repeated /start deep-link
    clicks from the same link only produce a single reference.

    Returns True when a new row was inserted, False otherwise.
    """
    if not game_id or referred_id == referrer_id:
        return False

    conn = _conn()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO "references" (referred_id, referrer_id, game_id)
            VALUES (?, ?, ?)
            """,
            (referred_id, referrer_id, game_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_referrer_id(referred_id: int, game_id: str) -> int | None:
    """Return the referrer_id who invited ``referred_id`` into ``game_id``."""
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT referrer_id FROM "references"
            WHERE referred_id = ? AND game_id = ?
            ORDER BY created_at ASC LIMIT 1
            """,
            (referred_id, game_id),
        ).fetchone()
        return row["referrer_id"] if row else None
    finally:
        conn.close()
