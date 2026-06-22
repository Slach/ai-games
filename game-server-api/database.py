"""
SQLite database storage for Game Master API
"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from language import LANGUAGE_EN, LANGUAGE_RU, SHIP_ROLES_I18N, get_ship_role_i18n

logger = logging.getLogger(__name__)

# Minimum live players to start a game
GAME_START_MIN_PLAYERS = int(os.getenv("GAME_START_MIN_PLAYERS", "3"))

# Database path
DB_PATH = Path(__file__).parent / "game_master.db"


def get_db_connection():
    """Get a database connection with row factory"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# Migrations applied to upgrade existing databases to the latest schema.
# Each migration runs once, even on fresh databases — so never reference
# columns that may not exist yet (e.g. renaming a column that was already
# created with the new name in the up-to-date CREATE TABLE).
MIGRATIONS: list[tuple[int, str]] = []

SHIP_ROLE_KEYS = list(SHIP_ROLES_I18N.keys())


def init_db():
    """Initialize database: create all tables, apply pending migrations, seed defaults."""
    conn = get_db_connection()
    cursor = conn.cursor()
    conn.execute("PRAGMA journal_mode=WAL")
    cursor.executescript(
        """
    CREATE TABLE IF NOT EXISTS migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS games (
        game_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        setting TEXT DEFAULT 'starship',
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL,
        max_players INTEGER DEFAULT 10,
        started INTEGER DEFAULT 0,
        started_at TEXT DEFAULT NULL
    );
    CREATE TABLE IF NOT EXISTS ship_roles (
        role_key TEXT NOT NULL,
        taken_by INTEGER DEFAULT NULL,
        game_id TEXT NOT NULL DEFAULT 'default_game',
        PRIMARY KEY (role_key, game_id)
    );
    CREATE TABLE IF NOT EXISTS game_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id TEXT NOT NULL DEFAULT 'default_game',
        day INTEGER DEFAULT 1,
        status TEXT DEFAULT 'active',
        ship_alive INTEGER DEFAULT 1,
        crew_health INTEGER DEFAULT 100,
        last_updated TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS onboarding_sessions (
        session_id TEXT PRIMARY KEY,
        player_id INTEGER NOT NULL,
        current_question INTEGER DEFAULT 0,
        answers TEXT DEFAULT '{}',
        completed INTEGER DEFAULT 0,
        language TEXT DEFAULT 'en',
        questions TEXT DEFAULT '[]',
        shuffle_seed INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS player_profiles (
        player_id INTEGER PRIMARY KEY,
        avatar_url TEXT,
        avatar_description TEXT,
        role TEXT NOT NULL,
        role_description TEXT,
        personality_traits TEXT DEFAULT '[]',
        game_id TEXT,
        last_poll TEXT,
        created_at TEXT NOT NULL,
        species TEXT DEFAULT NULL,
        gender TEXT DEFAULT NULL,
        species_description TEXT DEFAULT NULL,
        species_secondary TEXT DEFAULT NULL,
        gender_secondary TEXT DEFAULT NULL,
        is_dead INTEGER DEFAULT 0,
        is_spectator INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS game_days (
        day INTEGER NOT NULL,
        game_id TEXT NOT NULL DEFAULT 'default_game',
        story TEXT NOT NULL,
        crew_dialogues TEXT DEFAULT '[]',
        player_actions TEXT DEFAULT '[]',
        generated_content TEXT DEFAULT '{}',
        teaser TEXT,
        ship_alive INTEGER DEFAULT 1,
        crew_status TEXT DEFAULT '{}',
        previous_day_summary TEXT,
        global_circumstances TEXT DEFAULT '',
        combined_outcome TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (day, game_id)
    );
    CREATE TABLE IF NOT EXISTS player_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL,
        day INTEGER NOT NULL,
        action_id TEXT NOT NULL,
        choice TEXT NOT NULL,
        consequence_result TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY (player_id) REFERENCES player_profiles(player_id)
    );
    CREATE TABLE IF NOT EXISTS game_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        message_type TEXT DEFAULT 'text',
        timestamp TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS game_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        game_id TEXT DEFAULT 'default_game',
        day INTEGER,
        image_url TEXT NOT NULL,
        prompt TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS npc_profiles (
        npc_key TEXT PRIMARY KEY,
        role_key TEXT NOT NULL,
        npc_name TEXT NOT NULL,
        role TEXT NOT NULL,
        role_description TEXT DEFAULT '',
        personality_traits TEXT DEFAULT '[]',
        species TEXT DEFAULT 'Human',
        gender TEXT DEFAULT 'Male',
        avatar_description TEXT DEFAULT '',
        game_id TEXT DEFAULT 'default_game',
        is_active INTEGER DEFAULT 1,
        replaces_player_id INTEGER DEFAULT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS player_kicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kicked_player_id INTEGER NOT NULL,
        replaced_by_npc_key TEXT,
        reason TEXT DEFAULT '',
        kicked_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS player_briefings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day INTEGER NOT NULL,
        player_id INTEGER,
        npc_key TEXT,
        is_npc INTEGER DEFAULT 0,
        briefing TEXT NOT NULL,
        choices TEXT DEFAULT '[]',
        selected_action_id TEXT DEFAULT NULL,
        choice_rationale TEXT DEFAULT '',
        consequence_result TEXT DEFAULT '{}',
        chosen_action_url TEXT DEFAULT NULL,
        game_id TEXT NOT NULL DEFAULT 'default_game',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS game_missions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id TEXT NOT NULL DEFAULT 'default_game',
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        objectives TEXT DEFAULT '[]',
        stage_progress TEXT DEFAULT '{}',
        current_stage INTEGER DEFAULT 0,
        total_stages INTEGER DEFAULT 1,
        completed INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
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
    cursor.execute("SELECT COUNT(*) FROM games")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            """INSERT INTO games (game_id, name, description, setting, status, created_at, max_players)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "default_game",
                "Starship Crew",
                "Join the crew of a starship in space exploration",
                "starship",
                "active",
                datetime.now().isoformat(),
                10,
            ),
        )
    conn.commit()
    conn.close()
    _ensure_game_state("default_game")
    _init_ship_roles("default_game")
    logger.info("Database initialized successfully")


def _init_ship_roles(game_id: str = "default_game"):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ship_roles WHERE game_id = ?", (game_id,))
    if cursor.fetchone()[0] == 0:
        for role_key in SHIP_ROLE_KEYS:
            cursor.execute(
                "INSERT OR IGNORE INTO ship_roles (role_key, taken_by, game_id) VALUES (?, NULL, ?)",
                (role_key, game_id),
            )
        conn.commit()
        logger.info(f"Initialized {len(SHIP_ROLE_KEYS)} ship roles for game {game_id}")
    conn.close()


def _ensure_game_state(game_id: str):
    """Ensure a game_state row exists for the provided game_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM game_state WHERE game_id = ? LIMIT 1", (game_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            """INSERT INTO game_state (game_id, day, status, ship_alive, crew_health, last_updated)
               VALUES (?, 1, 'active', 1, 100, ?)""",
            (game_id, datetime.now().isoformat()),
        )
        conn.commit()
    conn.close()


def _enrich_role_with_i18n(
    role_key: str, taken_by: int | None = None, language: str = LANGUAGE_RU
) -> dict[str, Any]:
    ru = get_ship_role_i18n(role_key, LANGUAGE_RU)
    en = get_ship_role_i18n(role_key, LANGUAGE_EN)
    localized = get_ship_role_i18n(role_key, language)
    return {
        "role_key": role_key,
        "role_name": localized.get("role_name", ru.get("role_name", "")),
        "role_name_en": en.get("role_name", ""),
        "role_description": localized.get(
            "role_description", ru.get("role_description", "")
        ),
        "role_description_en": en.get("role_description", ""),
        "avatar_description": localized.get(
            "avatar_description", ru.get("avatar_description", "")
        ),
        "personality_traits": localized.get(
            "personality_traits", ru.get("personality_traits", [])
        ),
        **({"taken_by": taken_by} if taken_by is not None else {}),
    }


def get_available_roles(
    game_id: str = "default_game", language: str = LANGUAGE_RU
) -> list[dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role_key FROM ship_roles WHERE game_id = ? AND taken_by IS NULL",
        (game_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_enrich_role_with_i18n(row["role_key"], language=language) for row in rows]


def get_all_roles(
    game_id: str = "default_game", language: str = LANGUAGE_RU
) -> list[dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role_key, taken_by FROM ship_roles WHERE game_id = ?", (game_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        _enrich_role_with_i18n(row["role_key"], row["taken_by"], language)
        for row in rows
    ]


def take_role(role_key: str, player_id: int, game_id: str = "default_game") -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ship_roles SET taken_by = ? WHERE role_key = ? AND game_id = ? AND taken_by IS NULL",
        (player_id, role_key, game_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def get_role_by_key(
    role_key: str,
    language: str = LANGUAGE_RU,
    game_id: str = "default_game",
) -> dict[str, Any] | None:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role_key, taken_by FROM ship_roles WHERE role_key = ? AND game_id = ?",
        (role_key, game_id),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return _enrich_role_with_i18n(row["role_key"], row["taken_by"], language)


def reset_roles(game_id: str = "default_game"):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ship_roles SET taken_by = NULL WHERE game_id = ?", (game_id,)
    )
    conn.commit()
    conn.close()


# ============== Onboarding Sessions ==============


def create_onboarding_session(
    player_id: int,
    language: str = "en",
    questions: list[dict[str, Any]] | None = None,
    shuffle_seed: int = 0,
) -> dict[str, Any]:
    """Create a new onboarding session"""
    conn = get_db_connection()
    cursor = conn.cursor()

    session_id = f"onboarding_{player_id}_{datetime.now().timestamp()}"
    created_at = datetime.now().isoformat()

    cursor.execute(
        """INSERT INTO onboarding_sessions
           (session_id, player_id, current_question, answers, completed, language, questions, shuffle_seed, created_at)
           VALUES (?, ?, 0, '{}', 0, ?, ?, ?, ?)""",
        (
            session_id,
            player_id,
            language,
            json.dumps(questions, ensure_ascii=False) if questions else "[]",
            shuffle_seed,
            created_at,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "session_id": session_id,
        "player_id": player_id,
        "current_question": 0,
        "answers": {},
        "completed": False,
        "language": language,
        "questions": questions or [],
        "shuffle_seed": shuffle_seed,
        "created_at": created_at,
    }


def get_onboarding_session(session_id: str) -> dict[str, Any] | None:
    """Get an onboarding session by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM onboarding_sessions WHERE session_id = ?", (session_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "session_id": row["session_id"],
        "player_id": row["player_id"],
        "current_question": row["current_question"],
        "answers": json.loads(row["answers"] or "{}"),
        "completed": bool(row["completed"]),
        "language": row["language"] or "en",
        "questions": json.loads(row["questions"] or "[]"),
        "shuffle_seed": row["shuffle_seed"] or 0,
        "created_at": row["created_at"],
    }


def update_onboarding_session(
    session_id: str,
    current_question: int,
    answers: dict[int, str],
    completed: bool = False,
    language: str | None = None,
    questions: list | None = None,
) -> dict[str, Any] | None:
    """Update an onboarding session"""
    conn = get_db_connection()
    cursor = conn.cursor()

    if language and questions:
        cursor.execute(
            """UPDATE onboarding_sessions
               SET current_question = ?, answers = ?, completed = ?, language = ?, questions = ?
               WHERE session_id = ?""",
            (
                current_question,
                json.dumps(answers, ensure_ascii=False),
                1 if completed else 0,
                language,
                json.dumps(questions, ensure_ascii=False),
                session_id,
            ),
        )
    elif language:
        cursor.execute(
            """UPDATE onboarding_sessions
               SET current_question = ?, answers = ?, completed = ?, language = ?
               WHERE session_id = ?""",
            (
                current_question,
                json.dumps(answers, ensure_ascii=False),
                1 if completed else 0,
                language,
                session_id,
            ),
        )
    elif questions:
        cursor.execute(
            """UPDATE onboarding_sessions
               SET current_question = ?, answers = ?, completed = ?, questions = ?
               WHERE session_id = ?""",
            (
                current_question,
                json.dumps(answers, ensure_ascii=False),
                1 if completed else 0,
                json.dumps(questions, ensure_ascii=False),
                session_id,
            ),
        )
    else:
        cursor.execute(
            """UPDATE onboarding_sessions
               SET current_question = ?, answers = ?, completed = ?
               WHERE session_id = ?""",
            (
                current_question,
                json.dumps(answers, ensure_ascii=False),
                1 if completed else 0,
                session_id,
            ),
        )

    conn.commit()
    conn.close()

    return get_onboarding_session(session_id)


# ============== Player Profiles ==============


def create_player_profile(player_data: dict[str, Any]) -> dict[str, Any] | None:
    """Create or update a player profile"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT OR REPLACE INTO player_profiles
           (player_id, avatar_url, avatar_description, role, role_description, personality_traits,
            game_id, last_poll, created_at, species, gender, species_description,
            species_secondary, gender_secondary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            player_data["player_id"],
            player_data.get("avatar_url"),
            player_data.get("avatar_description"),
            player_data["role"],
            player_data.get("role_description"),
            json.dumps(player_data.get("personality_traits", []), ensure_ascii=False),
            player_data.get("game_id"),
            None,  # last_poll initialized to None
            datetime.now().isoformat(),
            player_data.get("species"),
            player_data.get("gender"),
            player_data.get("species_description"),
            player_data.get("species_secondary"),
            player_data.get("gender_secondary"),
        ),
    )

    conn.commit()
    conn.close()

    return get_player_profile(player_data["player_id"])


def get_player_profile(player_id: int) -> dict[str, Any] | None:
    """Get a player profile by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM player_profiles WHERE player_id = ?", (player_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "player_id": row["player_id"],
        "avatar_url": row["avatar_url"],
        "avatar_description": row["avatar_description"],
        "role": row["role"],
        "role_description": row["role_description"],
        "personality_traits": json.loads(row["personality_traits"] or "[]"),
        "game_id": row["game_id"],
        "last_poll": row["last_poll"],
        "created_at": row["created_at"],
        "species": row["species"],
        "gender": row["gender"],
        "species_description": row["species_description"],
        "species_secondary": row["species_secondary"],
        "gender_secondary": row["gender_secondary"],
        "is_dead": bool(row["is_dead"]) if row["is_dead"] is not None else False,
        "is_spectator": bool(row["is_spectator"])
        if row["is_spectator"] is not None
        else False,
    }


# ============== Game Days ==============


def create_game_day(
    day_data: dict[str, Any], game_id: str = "default_game"
) -> dict[str, Any] | None:
    """Create a new game day"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT OR REPLACE INTO game_days
           (day, story, crew_dialogues, player_actions, generated_content, teaser, ship_alive, crew_status, previous_day_summary, global_circumstances, combined_outcome, created_at, game_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            day_data["day"],
            day_data["story"],
            json.dumps(day_data.get("crew_dialogues", []), ensure_ascii=False),
            json.dumps(day_data.get("player_actions", []), ensure_ascii=False),
            json.dumps(day_data.get("generated_content", {}), ensure_ascii=False),
            day_data.get("teaser"),
            day_data.get("ship_alive", 1),
            json.dumps(day_data.get("crew_status", {}), ensure_ascii=False),
            day_data.get("previous_day_summary"),
            day_data.get("global_circumstances", ""),
            day_data.get("combined_outcome", ""),
            datetime.now().isoformat(),
            game_id,
        ),
    )

    conn.commit()
    conn.close()

    return get_game_day(day_data["day"], game_id)


def get_game_day(day: int, game_id: str = "default_game") -> dict[str, Any] | None:
    """Get a game day by number"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM game_days WHERE day = ? AND game_id = ?", (day, game_id)
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "day": row["day"],
        "story": row["story"],
        "crew_dialogues": json.loads(row["crew_dialogues"] or "[]"),
        "player_actions": json.loads(row["player_actions"] or "[]"),
        "generated_content": json.loads(row["generated_content"] or "{}"),
        "teaser": row["teaser"],
        "ship_alive": bool(row["ship_alive"]),
        "crew_status": json.loads(row["crew_status"] or "{}"),
        "previous_day_summary": row["previous_day_summary"],
        "created_at": row["created_at"],
        "global_circumstances": row["global_circumstances"] or "",
        "combined_outcome": row["combined_outcome"] or "",
        "game_id": row["game_id"],
    }


# ============== Player Actions ==============


def save_player_action(
    player_id: int,
    day: int,
    action_id: str,
    choice: str,
    consequence_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save a player action"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT INTO player_actions (player_id, day, action_id, choice, consequence_result, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            player_id,
            day,
            action_id,
            choice,
            json.dumps(consequence_result or {}, ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )

    action_id_db = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "id": action_id_db,
        "player_id": player_id,
        "day": day,
        "action_id": action_id,
        "choice": choice,
        "consequence_result": consequence_result or {},
    }


def get_player_actions(player_id: int, day: int | None = None) -> list[dict[str, Any]]:
    """Get player actions, optionally filtered by day"""
    conn = get_db_connection()
    cursor = conn.cursor()

    if day:
        cursor.execute(
            "SELECT * FROM player_actions WHERE player_id = ? AND day = ? ORDER BY created_at",
            (player_id, day),
        )
    else:
        cursor.execute(
            "SELECT * FROM player_actions WHERE player_id = ? ORDER BY created_at",
            (player_id,),
        )

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        action_dict = dict(row)
        action_dict["consequence_result"] = json.loads(
            row["consequence_result"] or "{}"
        )
        result.append(action_dict)

    return result


# ============== Game Messages ==============


def add_game_message(
    player_id: int, message: str, message_type: str = "text"
) -> dict[str, Any]:
    """Add a game message"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT INTO game_messages (player_id, message, message_type, timestamp)
           VALUES (?, ?, ?, ?)""",
        (player_id, message, message_type, datetime.now().isoformat()),
    )

    message_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "id": message_id,
        "player_id": player_id,
        "message": message,
        "message_type": message_type,
        "timestamp": datetime.now().isoformat(),
    }


def get_game_messages(player_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """Get recent game messages for a player"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """SELECT * FROM game_messages
           WHERE player_id = ?
           ORDER BY timestamp DESC
           LIMIT ?""",
        (player_id, limit),
    )

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


# ============== Game State ==============


def get_game_state(game_id: str = "default_game") -> dict[str, Any]:
    """Get current game state"""
    _ensure_game_state(game_id)
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM game_state WHERE game_id = ?", (game_id,))
    row = cursor.fetchone()
    conn.close()

    return {
        "day": row["day"],
        "status": row["status"],
        "ship_alive": bool(row["ship_alive"]),
        "crew_health": row["crew_health"],
        "last_updated": row["last_updated"],
    }


def update_game_state(
    day: int,
    status: str = "active",
    ship_alive: bool = True,
    crew_health: int = 100,
    game_id: str = "default_game",
) -> dict[str, Any]:
    """Update game state"""
    _ensure_game_state(game_id)
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE game_state
           SET day = ?, status = ?, ship_alive = ?, crew_health = ?, last_updated = ?
           WHERE game_id = ?""",
        (
            day,
            status,
            1 if ship_alive else 0,
            crew_health,
            datetime.now().isoformat(),
            game_id,
        ),
    )

    conn.commit()
    conn.close()

    return get_game_state(game_id)


def is_game_active(game_id: str = "default_game") -> bool:
    """Check if game is still active (ship and crew alive)"""
    state = get_game_state(game_id)
    return (
        state["status"] == "active" and state["ship_alive"] and state["crew_health"] > 0
    )


def end_game(
    reason: str = "game_over", game_id: str = "default_game"
) -> dict[str, Any]:
    """End the game by setting ship destroyed and crew health to 0"""
    _ensure_game_state(game_id)
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE game_state
           SET status = ?, ship_alive = 0, crew_health = 0, last_updated = ?
           WHERE game_id = ?""",
        (reason, datetime.now().isoformat(), game_id),
    )

    conn.commit()
    conn.close()

    return get_game_state(game_id)


# ============== Games ==============


def create_game(game_data: dict[str, Any]) -> dict[str, Any] | None:
    """Create a new game"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT INTO games (game_id, name, description, setting, status, created_at, max_players)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            game_data["game_id"],
            game_data["name"],
            game_data.get("description"),
            game_data.get("setting", "starship"),
            game_data.get("status", "active"),
            datetime.now().isoformat(),
            game_data.get("max_players", 10),
        ),
    )

    conn.commit()
    conn.close()

    _ensure_game_state(game_data["game_id"])
    _init_ship_roles(game_data["game_id"])

    return get_game(game_data["game_id"])


def get_game(game_id: str) -> dict[str, Any] | None:
    """Get a game by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM games WHERE game_id = ?", (game_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "game_id": row["game_id"],
        "name": row["name"],
        "description": row["description"],
        "setting": row["setting"],
        "status": row["status"],
        "created_at": row["created_at"],
        "max_players": row["max_players"],
    }


def get_available_games() -> list[dict[str, Any]]:
    """Get all available games"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM games WHERE status = 'active'")
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def join_game(game_id: str, player_id: int) -> bool:
    """Join a game as a player"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if player is already in another game
    cursor.execute(
        "SELECT game_id FROM player_profiles WHERE player_id = ?", (player_id,)
    )
    existing_game = cursor.fetchone()

    if existing_game and existing_game["game_id"]:
        conn.close()
        return False  # Player already in a game

    # Check if game has room
    cursor.execute("SELECT COUNT(*) FROM player_profiles WHERE game_id = ?", (game_id,))
    current_players = cursor.fetchone()[0]

    game = get_game(game_id)
    if not game or current_players >= game["max_players"]:
        conn.close()
        return False  # Game is full

    # Update player profile with game_id
    cursor.execute(
        """UPDATE player_profiles SET game_id = ? WHERE player_id = ?""",
        (game_id, player_id),
    )

    conn.commit()
    conn.close()
    return True


def get_players_in_game(game_id: str) -> list[int]:
    """Get list of player IDs in a game"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT player_id FROM player_profiles WHERE game_id = ?", (game_id,)
    )
    rows = cursor.fetchall()
    conn.close()

    return [row["player_id"] for row in rows]


def leave_game(player_id: int) -> bool:
    """Leave current game"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE player_profiles SET game_id = NULL WHERE player_id = ?""",
        (player_id,),
    )

    conn.commit()
    conn.close()
    return True


def update_player_profile_last_poll(player_id: int, last_poll: str):
    """Update player's last_poll timestamp"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE player_profiles SET last_poll = ? WHERE player_id = ?""",
        (last_poll, player_id),
    )

    conn.commit()
    conn.close()


def update_game_title(game_id: str, title: str) -> bool:
    """Update game title in the games table"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE games SET name = ? WHERE game_id = ?""",
        (title, game_id),
    )

    conn.commit()
    conn.close()
    return True


def get_game_title(game_id: str) -> str | None:
    """Get game title from the games table"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM games WHERE game_id = ?", (game_id,))
    row = cursor.fetchone()
    conn.close()

    return row["name"] if row else None


def is_game_started(game_id: str = "default_game") -> bool:
    """Check if the game has officially started (>= 3 players)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT started FROM games WHERE game_id = ?", (game_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return False
    return bool(row["started"])


def start_game(game_id: str = "default_game") -> bool:
    """Mark the game as started (when >= 3 players join)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE games SET started = 1, started_at = ? WHERE game_id = ? AND started = 0",
        (datetime.now().isoformat(), game_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def get_player_count_in_game(game_id: str = "default_game") -> int:
    """Get the number of players in a game"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM player_profiles WHERE game_id = ?", (game_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0


# ============== Game Images (loading / splash) ==============


def save_game_image(
    type: str,
    image_url: str,
    game_id: str = "default_game",
    day: int | None = None,
    prompt: str = "",
) -> int | None:
    """Save a game image URL (loading or splash) to the database.

    Args:
        type: 'splash' or 'loading'
        image_url: ComfyUI URL for the image
        game_id: Game identifier
        day: Game day (None for loading or splash images)
        prompt: Generation prompt used

    Returns:
        The ID of the inserted row, or None on failure.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO game_images (type, game_id, day, image_url, prompt, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (type, game_id, day, image_url, prompt, datetime.now().isoformat()),
        )
        row_id = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"[IMAGE] Saved {type} image #{row_id}: {image_url}...")
        return row_id
    except Exception as e:
        logger.error(f"[IMAGE] Failed to save {type} image: {e}")
        return None


def get_random_game_image(
    type: str,
    game_id: str = "default_game",
    day: int | None = None,
) -> str | None:
    """Get a random game image URL by type.

    Args:
        type: 'splash' or 'loading'
        game_id: Game identifier
        day: Game day filter (only for 'splash' type)

    Returns:
        Random image URL, or None if none exist.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if day is not None:
            cursor.execute(
                """SELECT image_url FROM game_images
                   WHERE type = ? AND game_id = ? AND day = ?
                   ORDER BY RANDOM() LIMIT 1""",
                (type, game_id, day),
            )
        else:
            cursor.execute(
                """SELECT image_url FROM game_images
                   WHERE type = ? AND game_id = ?
                   ORDER BY RANDOM() LIMIT 1""",
                (type, game_id),
            )
        row = cursor.fetchone()
        conn.close()
        return row["image_url"] if row else None
    except Exception as e:
        logger.error(f"[IMAGE] Failed to get random {type} image: {e}")
        return None


def get_game_image_count(
    type: str,
    game_id: str = "default_game",
    day: int | None = None,
) -> int:
    """Count images of a given type."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if day is not None:
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM game_images WHERE type = ? AND game_id = ? AND day = ?",
                (type, game_id, day),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM game_images WHERE type = ? AND game_id = ?",
                (type, game_id),
            )
        row = cursor.fetchone()
        conn.close()
        return row["cnt"] if row else 0
    except Exception as e:
        logger.error(f"[IMAGE] Failed to count {type} images: {e}")
        return 0


# ============== NPC Profiles ==============


def create_npc_profile(npc_data: dict[str, Any]) -> dict[str, Any] | None:
    """Create a persistent NPC profile."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO npc_profiles
           (npc_key, role_key, npc_name, role, role_description, personality_traits,
            species, gender, avatar_description, game_id, is_active, replaces_player_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            npc_data["npc_key"],
            npc_data.get("role_key", ""),
            npc_data["npc_name"],
            npc_data["role"],
            npc_data.get("role_description", ""),
            json.dumps(npc_data.get("personality_traits", []), ensure_ascii=False),
            npc_data.get("species", "Human"),
            npc_data.get("gender", "Male"),
            npc_data.get("avatar_description", ""),
            npc_data.get("game_id", "default_game"),
            1 if npc_data.get("is_active", True) else 0,
            npc_data.get("replaces_player_id"),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return get_npc_profile(npc_data["npc_key"])


def get_npc_profile(npc_key: str) -> dict[str, Any] | None:
    """Get an NPC profile by key."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM npc_profiles WHERE npc_key = ?", (npc_key,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "npc_key": row["npc_key"],
        "role_key": row["role_key"],
        "npc_name": row["npc_name"],
        "role": row["role"],
        "role_description": row["role_description"],
        "personality_traits": json.loads(row["personality_traits"] or "[]"),
        "species": row["species"],
        "gender": row["gender"],
        "avatar_description": row["avatar_description"],
        "game_id": row["game_id"],
        "is_active": bool(row["is_active"]),
        "replaces_player_id": row["replaces_player_id"],
        "created_at": row["created_at"],
    }


def get_all_active_npcs(game_id: str = "default_game") -> list[dict[str, Any]]:
    """Get all active NPCs in a game."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM npc_profiles WHERE game_id = ? AND is_active = 1 ORDER BY created_at",
        (game_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "npc_key": row["npc_key"],
            "role_key": row["role_key"],
            "npc_name": row["npc_name"],
            "role": row["role"],
            "role_description": row["role_description"],
            "personality_traits": json.loads(row["personality_traits"] or "[]"),
            "species": row["species"],
            "gender": row["gender"],
            "avatar_description": row["avatar_description"],
            "game_id": row["game_id"],
            "is_active": bool(row["is_active"]),
            "replaces_player_id": row["replaces_player_id"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_npc_by_role(
    role_key: str, game_id: str = "default_game"
) -> dict[str, Any] | None:
    """Find an active NPC by role key."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM npc_profiles WHERE role_key = ? AND game_id = ? AND is_active = 1 LIMIT 1",
        (role_key, game_id),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "npc_key": row["npc_key"],
        "role_key": row["role_key"],
        "npc_name": row["npc_name"],
        "role": row["role"],
        "role_description": row["role_description"],
        "personality_traits": json.loads(row["personality_traits"] or "[]"),
        "species": row["species"],
        "gender": row["gender"],
        "avatar_description": row["avatar_description"],
        "game_id": row["game_id"],
        "is_active": bool(row["is_active"]),
        "replaces_player_id": row["replaces_player_id"],
        "created_at": row["created_at"],
    }


def deactivate_npc(npc_key: str) -> bool:
    """Deactivate an NPC profile."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE npc_profiles SET is_active = 0 WHERE npc_key = ?",
        (npc_key,),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# ============== Player Kicks ==============


def record_kick(
    kicked_player_id: int, replaced_by_npc_key: str, reason: str = ""
) -> dict[str, Any]:
    """Record a player kick."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO player_kicks (kicked_player_id, replaced_by_npc_key, reason, kicked_at)
           VALUES (?, ?, ?, ?)""",
        (kicked_player_id, replaced_by_npc_key, reason, datetime.now().isoformat()),
    )
    kick_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {
        "id": kick_id,
        "kicked_player_id": kicked_player_id,
        "replaced_by_npc_key": replaced_by_npc_key,
        "reason": reason,
    }


def get_kicked_players() -> list[dict[str, Any]]:
    """Get all kicked players."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM player_kicks ORDER BY kicked_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def is_player_kicked(player_id: int) -> bool:
    """Check if a player has been kicked."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM player_kicks WHERE kicked_player_id = ?",
        (player_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] > 0 if row else False


# ============== Player Briefings (per-player game day content) ==============


def save_player_briefing(
    briefing_data: dict[str, Any], game_id: str = "default_game"
) -> dict[str, Any] | None:
    """Save a per-player daily briefing with choices and consequences."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO player_briefings
           (day, player_id, npc_key, is_npc, briefing, choices,
            selected_action_id, choice_rationale, consequence_result, chosen_action_url, created_at, game_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            briefing_data["day"],
            briefing_data.get("player_id"),
            briefing_data.get("npc_key"),
            1 if briefing_data.get("is_npc", False) else 0,
            briefing_data["briefing"],
            json.dumps(briefing_data.get("choices", []), ensure_ascii=False),
            briefing_data.get("selected_action_id"),
            briefing_data.get("choice_rationale", ""),
            json.dumps(briefing_data.get("consequence_result", {}), ensure_ascii=False),
            briefing_data.get("chosen_action_url"),
            datetime.now().isoformat(),
            game_id,
        ),
    )
    briefing_id = cursor.lastrowid
    conn.commit()
    conn.close()
    briefing_data["id"] = briefing_id
    briefing_data["game_id"] = game_id
    return briefing_data


def get_player_briefing(
    day: int, player_id: int, game_id: str = "default_game"
) -> dict[str, Any] | None:
    """Get a player's briefing for a specific day."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM player_briefings WHERE day = ? AND player_id = ? AND game_id = ? AND is_npc = 0",
        (day, player_id, game_id),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return _briefing_row_to_dict(row)


def _briefing_row_to_dict(row) -> dict[str, Any]:
    """Convert a player_briefings row from the database to a dict."""
    return {
        "id": row["id"],
        "day": row["day"],
        "player_id": row["player_id"],
        "npc_key": row["npc_key"],
        "is_npc": bool(row["is_npc"]),
        "briefing": row["briefing"],
        "choices": json.loads(row["choices"] or "[]"),
        "selected_action_id": row["selected_action_id"],
        "choice_rationale": row["choice_rationale"],
        "consequence_result": json.loads(row["consequence_result"] or "{}"),
        "chosen_action_url": row["chosen_action_url"],
        "created_at": row["created_at"],
        "game_id": row["game_id"],
    }


def get_all_briefings_for_day(
    day: int, game_id: str = "default_game"
) -> list[dict[str, Any]]:
    """Get all briefings (player + NPC) for a specific day."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM player_briefings WHERE day = ? AND game_id = ? ORDER BY is_npc, created_at",
        (day, game_id),
    )
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        result.append(_briefing_row_to_dict(row))
    return result


def update_briefing_choice(
    briefing_id: int,
    selected_action_id: str,
    choice_rationale: str = "",
    consequence_result: dict[str, Any] | None = None,
) -> bool:
    """Update a briefing with the player/NPC's choice."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE player_briefings
           SET selected_action_id = ?, choice_rationale = ?, consequence_result = ?
           WHERE id = ?""",
        (
            selected_action_id,
            choice_rationale,
            json.dumps(consequence_result or {}, ensure_ascii=False),
            briefing_id,
        ),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def update_briefing_chosen_action_url(
    briefing_id: int, chosen_action_url: str | None
) -> bool:
    """Store a chosen action image URL in a player's briefing."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE player_briefings SET chosen_action_url = ? WHERE id = ?",
        (chosen_action_url, briefing_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def get_players_who_need_to_choose(
    day: int, game_id: str = "default_game"
) -> list[dict[str, Any]]:
    """Get real players who haven't made their choice for the day yet."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM player_briefings
           WHERE day = ? AND game_id = ? AND is_npc = 0 AND selected_action_id IS NULL
           ORDER BY created_at""",
        (day, game_id),
    )
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "day": row["day"],
                "player_id": row["player_id"],
                "briefing": row["briefing"],
                "choices": json.loads(row["choices"] or "[]"),
                "game_id": row["game_id"],
            }
        )
    return result


def update_game_day_outcome(
    day: int, combined_outcome: str, game_id: str = "default_game"
) -> bool:
    """Update the combined outcome for a game day after all choices are analyzed."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE game_days SET combined_outcome = ? WHERE day = ? AND game_id = ?",
        (combined_outcome, day, game_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def update_game_day_global_circumstances(
    day: int, circumstances: str, game_id: str = "default_game"
) -> bool:
    """Update global circumstances for a game day."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE game_days SET global_circumstances = ? WHERE day = ? AND game_id = ?",
        (circumstances, day, game_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# ============== Mission Management ==============


def create_mission(
    mission_data: dict[str, Any], game_id: str = "default_game"
) -> dict[str, Any] | None:
    """Create a mission for a game."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO game_missions
           (game_id, name, description, objectives, stage_progress, current_stage, total_stages, completed, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
        (
            game_id,
            mission_data["name"],
            mission_data["description"],
            json.dumps(mission_data.get("objectives", []), ensure_ascii=False),
            json.dumps(mission_data.get("stage_progress", {}), ensure_ascii=False),
            mission_data.get("current_stage", 0),
            mission_data.get("total_stages", 1),
            datetime.now().isoformat(),
        ),
    )
    mission_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return get_mission(mission_id, game_id)


def get_mission(
    mission_id: int | None = None, game_id: str = "default_game"
) -> dict[str, Any] | None:
    """Get the latest mission for a game, or a specific mission by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    if mission_id:
        cursor.execute(
            "SELECT * FROM game_missions WHERE id = ? AND game_id = ?",
            (mission_id, game_id),
        )
    else:
        cursor.execute(
            "SELECT * FROM game_missions WHERE game_id = ? ORDER BY created_at DESC LIMIT 1",
            (game_id,),
        )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "game_id": row["game_id"],
        "name": row["name"],
        "description": row["description"],
        "objectives": json.loads(row["objectives"] or "[]"),
        "stage_progress": json.loads(row["stage_progress"] or "{}"),
        "current_stage": row["current_stage"],
        "total_stages": row["total_stages"],
        "completed": bool(row["completed"]),
        "created_at": row["created_at"],
    }


def update_mission_stage_progress(
    stage_progress: dict[str, Any],
    current_stage: int,
    game_id: str = "default_game",
    completed: bool = False,
) -> bool:
    """Update stage progress for a mission."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE game_missions
           SET stage_progress = ?, current_stage = ?, completed = ?
           WHERE game_id = ? AND id = (SELECT id FROM game_missions WHERE game_id = ? ORDER BY created_at DESC LIMIT 1)""",
        (
            json.dumps(stage_progress, ensure_ascii=False),
            current_stage,
            1 if completed else 0,
            game_id,
            game_id,
        ),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def mark_player_dead(player_id: int, game_id: str = "default_game") -> bool:
    """Mark a player as dead (crew member died)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE player_profiles
           SET is_dead = 1, is_spectator = 1
           WHERE player_id = ? AND game_id = ?""",
        (player_id, game_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def get_dead_players(game_id: str = "default_game") -> list[int]:
    """Get IDs of dead players in a game (spectators)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT player_id FROM player_profiles WHERE game_id = ? AND is_dead = 1",
        (game_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [row["player_id"] for row in rows]


def get_live_players(game_id: str = "default_game") -> list[int]:
    """Get IDs of live (non-dead, non-spectator) players in a game."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT player_id FROM player_profiles WHERE game_id = ? AND (is_dead IS NULL OR is_dead = 0)",
        (game_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [row["player_id"] for row in rows]


def revive_player(player_id: int) -> bool:
    """Revive a dead player (rejoin game in new role)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE player_profiles SET is_dead = 0, is_spectator = 0 WHERE player_id = ?",
        (player_id,),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# ============== Game Reset / Regeneration ==============


def delete_game_state_for_game(game_id: str) -> bool:
    """Delete game_state rows for a specific game."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM game_state WHERE game_id = ?", (game_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def reset_game_state_to_day1(game_id: str = "default_game") -> dict[str, Any]:
    """Reset game state back to day 1."""
    _ensure_game_state(game_id)
    return update_game_state(1, "active", True, 100, game_id)


def delete_game_day(day: int, game_id: str = "default_game") -> bool:
    """Delete a specific game day."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM game_days WHERE day = ? AND game_id = ?", (day, game_id)
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def delete_all_game_days(game_id: str = "default_game") -> int:
    """Delete all game days for a specific game. Returns count deleted."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM game_days WHERE game_id = ?", (game_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def delete_player_briefings_for_day(day: int, game_id: str = "default_game") -> int:
    """Delete all briefings (player + NPC) for a specific game day."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM player_briefings WHERE day = ? AND game_id = ?", (day, game_id)
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def delete_all_player_briefings(game_id: str = "default_game") -> int:
    """Delete all player briefings for a game."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM player_briefings WHERE game_id = ?", (game_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def delete_player_actions_for_day(day: int, game_id: str = "default_game") -> int:
    """Delete player actions for a specific day.

    This is tricky because player_actions doesn't have a game_id column.
    We find actions by matching player_ids who belong to this game.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """DELETE FROM player_actions WHERE day = ? AND player_id IN (
            SELECT player_id FROM player_profiles WHERE game_id = ?
        )""",
        (day, game_id),
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def delete_all_player_actions(game_id: str = "default_game") -> int:
    """Delete all player actions for a game."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """DELETE FROM player_actions WHERE player_id IN (
            SELECT player_id FROM player_profiles WHERE game_id = ?
        )""",
        (game_id,),
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def delete_all_game_messages(game_id: str = "default_game") -> int:
    """Delete all game messages for players in a game."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """DELETE FROM game_messages WHERE player_id IN (
            SELECT player_id FROM player_profiles WHERE game_id = ?
        )""",
        (game_id,),
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def clear_game_started(game_id: str = "default_game") -> bool:
    """Mark the game as not started anymore."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE games SET started = 0, started_at = NULL WHERE game_id = ?",
        (game_id,),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def delete_mission(game_id: str = "default_game") -> bool:
    """Delete the mission for a game."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM game_missions WHERE game_id = ?", (game_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def delete_game_images(game_id: str = "default_game") -> int:
    """Delete all images associated with a game (splash, bridge, etc.),
    but preserve loading images since they are shared."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM game_images WHERE game_id = ? AND type != 'loading'", (game_id,)
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted
