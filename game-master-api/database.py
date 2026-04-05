"""
SQLite database storage for Game Master API
"""

import sqlite3
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from language import SHIP_ROLES_I18N, LANGUAGE_RU, LANGUAGE_EN, get_ship_role_i18n


logger = logging.getLogger(__name__)

# Database path
DB_PATH = Path(__file__).parent / "game_master.db"


def get_db_connection():
    """Get a database connection with row factory"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# Migration management
MIGRATIONS = [
    (
        1,
        """
    CREATE TABLE IF NOT EXISTS migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    ),
    (
        2,
        """
    CREATE TABLE IF NOT EXISTS onboarding_sessions (
        session_id TEXT PRIMARY KEY,
        player_id INTEGER NOT NULL,
        current_question INTEGER DEFAULT 0,
        answers TEXT DEFAULT '{}',
        completed INTEGER DEFAULT 0,
        language TEXT DEFAULT 'en',
        questions TEXT DEFAULT '[]',
        created_at TEXT NOT NULL
    )
    """,
    ),
    (
        3,
        """
    CREATE TABLE IF NOT EXISTS player_profiles (
        player_id INTEGER PRIMARY KEY,
        avatar_url TEXT,
        avatar_description TEXT,
        role TEXT NOT NULL,
        role_description TEXT,
        personality_traits TEXT DEFAULT '[]',
        game_id TEXT,
        last_poll TEXT,
        created_at TEXT NOT NULL
    )
    """,
    ),
    (
        4,
        """
    CREATE TABLE IF NOT EXISTS game_days (
        day INTEGER PRIMARY KEY,
        story TEXT NOT NULL,
        npc_dialogues TEXT DEFAULT '[]',
        player_actions TEXT DEFAULT '[]',
        generated_content TEXT DEFAULT '{}',
        teaser TEXT,
        ship_alive INTEGER DEFAULT 1,
        crew_status TEXT DEFAULT '{}',
        previous_day_summary TEXT,
        created_at TEXT NOT NULL
    )
    """,
    ),
    (
        5,
        """
    CREATE TABLE IF NOT EXISTS player_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL,
        day INTEGER NOT NULL,
        action_id TEXT NOT NULL,
        choice TEXT NOT NULL,
        consequence_result TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY (player_id) REFERENCES player_profiles(player_id)
    )
    """,
    ),
    (
        6,
        """
    CREATE TABLE IF NOT EXISTS game_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        message_type TEXT DEFAULT 'text',
        timestamp TEXT NOT NULL
    )
    """,
    ),
    (
        7,
        """
    CREATE TABLE IF NOT EXISTS game_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        day INTEGER DEFAULT 1,
        status TEXT DEFAULT 'active',
        ship_alive INTEGER DEFAULT 1,
        crew_health INTEGER DEFAULT 100,
        last_updated TEXT NOT NULL
    )
    """,
    ),
    (
        8,
        """
    CREATE TABLE IF NOT EXISTS games (
        game_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        setting TEXT DEFAULT 'starship',
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL,
        max_players INTEGER DEFAULT 10
    )
    """,
    ),
    (
        9,
        """
    CREATE TABLE IF NOT EXISTS ship_roles (
        role_key TEXT PRIMARY KEY,
        taken_by INTEGER DEFAULT NULL,
        game_id TEXT DEFAULT 'default_game'
    )
    """,
    ),
    (
        10,
        """
    ALTER TABLE games ADD COLUMN started INTEGER DEFAULT 0
    """,
    ),
    (
        11,
        """
    ALTER TABLE games ADD COLUMN started_at TEXT DEFAULT NULL
    """,
    ),
]

SHIP_ROLE_KEYS = list(SHIP_ROLES_I18N.keys())


def get_current_schema_version(conn: sqlite3.Connection) -> int:
    cursor = conn.cursor()
    # Check if migrations table exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='migrations'"
    )
    if cursor.fetchone() is None:
        return 0
    cursor.execute("SELECT MAX(version) FROM migrations")
    row = cursor.fetchone()
    return row[0] if row[0] is not None else 0


def run_migrations():
    conn = get_db_connection()
    cursor = conn.cursor()
    current_version = get_current_schema_version(conn)
    for version, sql in MIGRATIONS:
        if version > current_version:
            cursor.executescript(sql)
            cursor.execute(
                "INSERT INTO migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now().isoformat()),
            )
            conn.commit()
    
    # Ensure critical tables exist regardless of migration tracking
    # This handles cases where the database file persisted but tables were lost
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='onboarding_sessions'"
    )
    if cursor.fetchone() is None:
        logger.warning("onboarding_sessions table missing, recreating...")
        cursor.executescript(MIGRATIONS[1][1])  # Migration 2 creates onboarding_sessions
        conn.commit()
    
    conn.close()


def init_db():
    """Initialize database with default data if needed"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Initialize default game if not exists
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

    # Initialize game state if not exists
    cursor.execute("SELECT COUNT(*) FROM game_state")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO game_state (id, day, status, ship_alive, crew_health, last_updated) VALUES (1, 1, 'active', 1, 100, ?)",
            (datetime.now().isoformat(),),
        )

    conn.commit()
    conn.close()
    _init_ship_roles()
    logger.info("Database initialized successfully")


def _init_ship_roles():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ship_roles")
    if cursor.fetchone()[0] == 0:
        for role_key in SHIP_ROLE_KEYS:
            cursor.execute(
                "INSERT INTO ship_roles (role_key, taken_by, game_id) VALUES (?, NULL, 'default_game')",
                (role_key,),
            )
        conn.commit()
        logger.info(f"Initialized {len(SHIP_ROLE_KEYS)} ship roles")
    conn.close()


def _enrich_role_with_i18n(
    role_key: str, taken_by: Optional[int] = None, language: str = LANGUAGE_RU
) -> Dict[str, Any]:
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
) -> List[Dict[str, Any]]:
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
) -> List[Dict[str, Any]]:
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
    role_key: str, language: str = LANGUAGE_RU
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role_key, taken_by FROM ship_roles WHERE role_key = ?", (role_key,)
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
    questions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Create a new onboarding session"""
    conn = get_db_connection()
    cursor = conn.cursor()

    session_id = f"onboarding_{player_id}_{datetime.now().timestamp()}"
    created_at = datetime.now().isoformat()

    cursor.execute(
        """INSERT INTO onboarding_sessions
           (session_id, player_id, current_question, answers, completed, language, questions, created_at)
           VALUES (?, ?, 0, '{}', 0, ?, ?, ?)""",
        (
            session_id,
            player_id,
            language,
            json.dumps(questions) if questions else "[]",
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
        "created_at": created_at,
    }


def get_onboarding_session(session_id: str) -> Optional[Dict[str, Any]]:
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
        "created_at": row["created_at"],
    }


def update_onboarding_session(
    session_id: str,
    current_question: int,
    answers: Dict[int, str],
    completed: bool = False,
    language: Optional[str] = None,
    questions: Optional[list] = None,
) -> Optional[Dict[str, Any]]:
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
                json.dumps(answers),
                1 if completed else 0,
                language,
                json.dumps(questions),
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
                json.dumps(answers),
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
                json.dumps(answers),
                1 if completed else 0,
                json.dumps(questions),
                session_id,
            ),
        )
    else:
        cursor.execute(
            """UPDATE onboarding_sessions
               SET current_question = ?, answers = ?, completed = ?
               WHERE session_id = ?""",
            (current_question, json.dumps(answers), 1 if completed else 0, session_id),
        )

    conn.commit()
    conn.close()

    return get_onboarding_session(session_id)


# ============== Player Profiles ==============


def create_player_profile(player_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Create or update a player profile"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT OR REPLACE INTO player_profiles
           (player_id, avatar_url, avatar_description, role, role_description, personality_traits, game_id, last_poll, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            player_data["player_id"],
            player_data.get("avatar_url"),
            player_data.get("avatar_description"),
            player_data["role"],
            player_data.get("role_description"),
            json.dumps(player_data.get("personality_traits", [])),
            player_data.get("game_id"),
            None,  # last_poll initialized to None
            datetime.now().isoformat(),
        ),
    )

    conn.commit()
    conn.close()

    return get_player_profile(player_data["player_id"])


def get_player_profile(player_id: int) -> Optional[Dict[str, Any]]:
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
    }


# ============== Game Days ==============


def create_game_day(day_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Create a new game day"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT OR REPLACE INTO game_days
           (day, story, npc_dialogues, player_actions, generated_content, teaser, ship_alive, crew_status, previous_day_summary, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            day_data["day"],
            day_data["story"],
            json.dumps(day_data.get("npc_dialogues", [])),
            json.dumps(day_data.get("player_actions", [])),
            json.dumps(day_data.get("generated_content", {})),
            day_data.get("teaser"),
            day_data.get("ship_alive", 1),
            json.dumps(day_data.get("crew_status", {})),
            day_data.get("previous_day_summary"),
            datetime.now().isoformat(),
        ),
    )

    conn.commit()
    conn.close()

    return get_game_day(day_data["day"])


def get_game_day(day: int) -> Optional[Dict[str, Any]]:
    """Get a game day by number"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM game_days WHERE day = ?", (day,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "day": row["day"],
        "story": row["story"],
        "npc_dialogues": json.loads(row["npc_dialogues"] or "[]"),
        "player_actions": json.loads(row["player_actions"] or "[]"),
        "generated_content": json.loads(row["generated_content"] or "{}"),
        "teaser": row["teaser"],
        "ship_alive": bool(row["ship_alive"]),
        "crew_status": json.loads(row["crew_status"] or "{}"),
        "previous_day_summary": row["previous_day_summary"],
        "created_at": row["created_at"],
    }


# ============== Player Actions ==============


def save_player_action(
    player_id: int,
    day: int,
    action_id: str,
    choice: str,
    consequence_result: Dict[str, Any] = None,
) -> Dict[str, Any]:
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
            json.dumps(consequence_result or {}),
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


def get_player_actions(
    player_id: int, day: Optional[int] = None
) -> List[Dict[str, Any]]:
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
) -> Dict[str, Any]:
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


def get_game_messages(player_id: int, limit: int = 10) -> List[Dict[str, Any]]:
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


def get_game_state() -> Dict[str, Any]:
    """Get current game state"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM game_state WHERE id = 1")
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
    day: int, status: str = "active", ship_alive: bool = True, crew_health: int = 100
) -> Dict[str, Any]:
    """Update game state"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE game_state
           SET day = ?, status = ?, ship_alive = ?, crew_health = ?, last_updated = ?
           WHERE id = 1""",
        (day, status, 1 if ship_alive else 0, crew_health, datetime.now().isoformat()),
    )

    conn.commit()
    conn.close()

    return get_game_state()


def is_game_active() -> bool:
    """Check if game is still active (ship and crew alive)"""
    state = get_game_state()
    return (
        state["status"] == "active" and state["ship_alive"] and state["crew_health"] > 0
    )


def end_game(reason: str = "game_over") -> Dict[str, Any]:
    """End the game by setting ship destroyed and crew health to 0"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE game_state
           SET status = ?, ship_alive = 0, crew_health = 0, last_updated = ?
           WHERE id = 1""",
        (reason, datetime.now().isoformat()),
    )

    conn.commit()
    conn.close()

    return get_game_state()


# ============== Games ==============


def create_game(game_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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

    return get_game(game_data["game_id"])


def get_game(game_id: str) -> Optional[Dict[str, Any]]:
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


def get_available_games() -> List[Dict[str, Any]]:
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


def get_players_in_game(game_id: str) -> List[int]:
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


def get_game_title(game_id: str) -> Optional[str]:
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
