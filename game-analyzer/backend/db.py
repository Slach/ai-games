"""Read-only SQLite access to the game-server and telegram-bot databases."""

import logging
import sqlite3

import config

logger = logging.getLogger(__name__)


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def query(db_path: str, sql: str, args: tuple = ()) -> list[dict]:
    """Run a SELECT and return rows as dicts. Read-only, fresh connection."""
    try:
        conn = _connect(db_path)
    except sqlite3.OperationalError:
        logger.error("Cannot open database %s", db_path, exc_info=True)
        raise
    try:
        return [dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def query_game_server(sql: str, args: tuple = ()) -> list[dict]:
    return query(config.GAME_SERVER_DB, sql, args)


def query_bot(sql: str, args: tuple = ()) -> list[dict]:
    return query(config.BOT_DB, sql, args)
