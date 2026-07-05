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
import sqlite3
from datetime import datetime
from typing import Any

from database import DB_PATH, init_db

logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime objects."""

    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

DEFAULT_STATE: dict[str, Any] = {
    "game_id": None,
    "onboarding_session_id": None,
    "current_question_id": None,
    "current_options": None,
    "current_question_text": None,
    "current_question_image_url": None,
    "language": "en",
}


# Run database initialization once at module load
init_db()


def _conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Create a new SQLite connection with WAL mode and dict row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Public API — mirrors the old dict interface exactly
# ---------------------------------------------------------------------------


def get_player_state(player_id: int) -> dict[str, Any]:
    """Get or create player state.

    Returns a dict with the same keys as the old in-memory dict:
      game_id, onboarding_session_id,
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
                    (player_id, game_id, onboarding_session_id,
                     current_question_id, current_options, current_question_text,
                     current_question_image_url, last_poll, pending_updates, language)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    DEFAULT_STATE["game_id"],
                    DEFAULT_STATE["onboarding_session_id"],
                    DEFAULT_STATE["current_question_id"],
                    json.dumps(DEFAULT_STATE["current_options"]) if DEFAULT_STATE["current_options"] else None,
                    DEFAULT_STATE["current_question_text"],
                    DEFAULT_STATE["current_question_image_url"],
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
                "current_question_id": None,
                "current_options": None,
                "current_question_text": None,
                "current_question_image_url": None,
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
            "current_question_id": row["current_question_id"],
            "current_options": current_options,
            "current_question_text": row["current_question_text"],
            "current_question_image_url": row["current_question_image_url"],
            "last_poll": last_poll,
            "pending_updates": pending_list,
            "last_briefing_turn_sent": row["last_briefing_turn_sent"],
            "language": row["language"] or "en",
        }
    finally:
        conn.close()


# SQL-safe column references for update_player_state(). Keys are validated
# against this dict — any key not present here is rejected, so there is no
# path for user input to reach a column name.
_PLAYER_STATE_COLUMNS: dict[str, str] = {
    "game_id": "game_id = ?",
    "onboarding_session_id": "onboarding_session_id = ?",
    "current_question_id": "current_question_id = ?",
    "current_options": "current_options = ?",
    "current_question_text": "current_question_text = ?",
    "current_question_image_url": "current_question_image_url = ?",
    "last_poll": "last_poll = ?",
    "pending_updates": "pending_updates = ?",
    "last_briefing_turn_sent": "last_briefing_turn_sent = ?",
    "last_outcome_turn_sent": "last_outcome_turn_sent = ?",
    "language": "language = ?",
}


def update_player_state(player_id: int, **kwargs: Any) -> None:
    """Update player state columns.

    Accepts the same keyword arguments that were previously written to the
    in-memory dict.  Datetime values are serialised to ISO strings, lists/dicts
    to JSON strings.
    """
    conn = _conn()
    try:
        # Warn about unknown keys
        unknown = kwargs.keys() - _PLAYER_STATE_COLUMNS.keys()
        for key in unknown:
            logger.warning("Unknown player_state key '%s' — skipping", key)

        provided = {k for k in kwargs if k in _PLAYER_STATE_COLUMNS}
        if not provided:
            return  # nothing to update

        # Build params with a (provided_flag, value) pair per column.
        # CASE WHEN ? THEN ? ELSE col END keeps columns untouched when not
        # provided, while allowing explicit NULL.  Column order mirrors
        # _PLAYER_STATE_COLUMNS (insertion-ordered, Python 3.7+).
        params: list[Any] = []
        for col in _PLAYER_STATE_COLUMNS:
            if col in provided:
                value = kwargs[col]
                if col == "last_poll" and isinstance(value, datetime):
                    value = value.isoformat()
                elif col in ("pending_updates", "current_options"):
                    value = json.dumps(value, cls=DateTimeEncoder) if value is not None else None
                params.extend([True, value])
            else:
                params.extend([False, None])

        params.append(player_id)  # WHERE clause

        conn.execute(
            """UPDATE player_states SET
                game_id = CASE WHEN ? THEN ? ELSE game_id END,
                onboarding_session_id = CASE WHEN ? THEN ? ELSE onboarding_session_id END,
                current_question_id = CASE WHEN ? THEN ? ELSE current_question_id END,
                current_options = CASE WHEN ? THEN ? ELSE current_options END,
                current_question_text = CASE WHEN ? THEN ? ELSE current_question_text END,
                current_question_image_url = CASE WHEN ? THEN ? ELSE current_question_image_url END,
                last_poll = CASE WHEN ? THEN ? ELSE last_poll END,
                pending_updates = CASE WHEN ? THEN ? ELSE pending_updates END,
                last_briefing_turn_sent = CASE WHEN ? THEN ? ELSE last_briefing_turn_sent END,
                last_outcome_turn_sent = CASE WHEN ? THEN ? ELSE last_outcome_turn_sent END,
                language = CASE WHEN ? THEN ? ELSE language END,
                updated_at = datetime('now')
            WHERE player_id = ?""",
            (*params,),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_briefing_dedup() -> dict[tuple[int, str], int]:
    """Return {（player_id, game_id): last_briefing_turn} for every entry that
    has a non-NULL briefing turn. Loaded once at startup into the in-memory
    dedup cache. Keyed per-(player, game) so different games never collide.
    """
    conn = _conn()
    try:
        result: dict[tuple[int, str], int] = {}
        for row in conn.execute("SELECT player_id, game_id, last_briefing_turn FROM delivery_dedup WHERE last_briefing_turn IS NOT NULL"):
            result[(int(row["player_id"]), str(row["game_id"]))] = int(row["last_briefing_turn"])
        return result
    finally:
        conn.close()


def get_all_outcome_dedup() -> dict[tuple[int, str], int]:
    """Return {(player_id, game_id): last_outcome_turn} for every entry that
    has a non-NULL outcome turn. Loaded once at startup into the in-memory
    dedup cache.
    """
    conn = _conn()
    try:
        result: dict[tuple[int, str], int] = {}
        for row in conn.execute("SELECT player_id, game_id, last_outcome_turn FROM delivery_dedup WHERE last_outcome_turn IS NOT NULL"):
            result[(int(row["player_id"]), str(row["game_id"]))] = int(row["last_outcome_turn"])
        return result
    finally:
        conn.close()


def set_briefing_dedup(player_id: int, game_id: str, turn: int) -> None:
    """Record that the briefing for *turn* of *game_id* was delivered to
    *player_id*. Per-(player, game) so it survives bot restarts without
    cross-game or cross-epoch bleed.
    """
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO delivery_dedup (player_id, game_id, last_briefing_turn, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(player_id, game_id) DO UPDATE SET
                 last_briefing_turn = excluded.last_briefing_turn,
                 updated_at = datetime('now')""",
            (player_id, game_id, turn),
        )
        conn.commit()
    finally:
        conn.close()


def set_outcome_dedup(player_id: int, game_id: str, turn: int) -> None:
    """Record that the outcome for *turn* of *game_id* was delivered to
    *player_id*.
    """
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO delivery_dedup (player_id, game_id, last_outcome_turn, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(player_id, game_id) DO UPDATE SET
                 last_outcome_turn = excluded.last_outcome_turn,
                 updated_at = datetime('now')""",
            (player_id, game_id, turn),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_game_over_dedup() -> dict[tuple[int, str], str]:
    """Return {(player_id, game_id): last_game_over} for every entry that has
    a non-NULL game-over marker. Loaded once at startup so the game-over dedup
    survives bot restarts (previously it was an in-memory-only dict that reset
    on every restart, allowing duplicate finales).
    """
    conn = _conn()
    try:
        result: dict[tuple[int, str], str] = {}
        for row in conn.execute("SELECT player_id, game_id, last_game_over FROM delivery_dedup WHERE last_game_over IS NOT NULL"):
            result[(int(row["player_id"]), str(row["game_id"]))] = str(row["last_game_over"])
        return result
    finally:
        conn.close()


def set_game_over_dedup(player_id: int, game_id: str) -> None:
    """Record that the game-over finale for *game_id* was delivered to
    *player_id*. Per-(player, game) so it survives bot restarts and never
    blocks a different game's finale.
    """
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO delivery_dedup (player_id, game_id, last_game_over, updated_at)
               VALUES (?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(player_id, game_id) DO UPDATE SET
                 last_game_over = datetime('now'),
                 updated_at = datetime('now')""",
            (player_id, game_id),
        )
        conn.commit()
    finally:
        conn.close()


def clear_dedup_for_game(game_id: str) -> int:
    """Delete every delivery_dedup row for *game_id*.

    Used at a game-restart epoch boundary so the new epoch delivers briefings
    and outcomes fresh (the per-turn dedup would otherwise skip regenerated
    turns). Returns the number of rows deleted.
    """
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM delivery_dedup WHERE game_id = ?", (game_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def clear_dedup_for_player(player_id: int) -> int:
    """Delete every delivery_dedup row for *player_id* (across all games).

    Used on /reset so a wiped profile starts clean. Returns rows deleted.
    """
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM delivery_dedup WHERE player_id = ?", (player_id,))
        conn.commit()
        return cur.rowcount
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
