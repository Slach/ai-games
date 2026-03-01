"""
SQLite database storage for Game Master API
"""

import sqlite3
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path


logger = logging.getLogger(__name__)

# Database path
DB_PATH = Path(__file__).parent / "game_master.db"


def get_db_connection():
    """Get a database connection with row factory"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Onboarding sessions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_sessions (
            session_id TEXT PRIMARY KEY,
            player_id INTEGER NOT NULL,
            current_question INTEGER DEFAULT 0,
            answers TEXT DEFAULT '{}',
            completed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    # Player profiles table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_profiles (
            player_id INTEGER PRIMARY KEY,
            avatar_description TEXT,
            role TEXT NOT NULL,
            role_description TEXT,
            personality_traits TEXT DEFAULT '[]',
            created_at TEXT NOT NULL
        )
    """)

    # Game days table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_days (
            day INTEGER PRIMARY KEY,
            story TEXT NOT NULL,
            npc_dialogues TEXT DEFAULT '[]',
            player_actions TEXT DEFAULT '[]',
            generated_content TEXT DEFAULT '{}',
            teaser TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Player actions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            day INTEGER NOT NULL,
            action_id TEXT NOT NULL,
            choice TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (player_id) REFERENCES player_profiles(player_id)
        )
    """)

    # Game messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            message_type TEXT DEFAULT 'text',
            timestamp TEXT NOT NULL
        )
    """)

    # Game state table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            day INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            last_updated TEXT NOT NULL
        )
    """)

    # Initialize game state if not exists
    cursor.execute("SELECT COUNT(*) FROM game_state")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO game_state (id, day, status, last_updated) VALUES (1, 1, 'active', ?)",
            (datetime.now().isoformat(),)
        )

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")


# ============== Onboarding Sessions ==============

def create_onboarding_session(player_id: int) -> Dict[str, Any]:
    """Create a new onboarding session"""
    conn = get_db_connection()
    cursor = conn.cursor()

    session_id = f"onboarding_{player_id}_{datetime.now().timestamp()}"
    created_at = datetime.now().isoformat()

    cursor.execute(
        """INSERT INTO onboarding_sessions
           (session_id, player_id, current_question, answers, completed, created_at)
           VALUES (?, ?, 0, '{}', 0, ?)""",
        (session_id, player_id, created_at)
    )

    conn.commit()
    conn.close()

    return {
        "session_id": session_id,
        "player_id": player_id,
        "current_question": 0,
        "answers": {},
        "completed": False,
        "created_at": created_at
    }


def get_onboarding_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get an onboarding session by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM onboarding_sessions WHERE session_id = ?", (session_id,))
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
        "created_at": row["created_at"]
    }


def update_onboarding_session(
    session_id: str,
    current_question: int,
    answers: Dict[int, str],
    completed: bool = False
) -> Optional[Dict[str, Any]]:
    """Update an onboarding session"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE onboarding_sessions
           SET current_question = ?, answers = ?, completed = ?
           WHERE session_id = ?""",
        (current_question, json.dumps(answers), 1 if completed else 0, session_id)
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
           (player_id, avatar_description, role, role_description, personality_traits, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            player_data["player_id"],
            player_data.get("avatar_description"),
            player_data["role"],
            player_data.get("role_description"),
            json.dumps(player_data.get("personality_traits", [])),
            datetime.now().isoformat()
        )
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
        "avatar_description": row["avatar_description"],
        "role": row["role"],
        "role_description": row["role_description"],
        "personality_traits": json.loads(row["personality_traits"] or "[]"),
        "created_at": row["created_at"]
    }


# ============== Game Days ==============

def create_game_day(day_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Create a new game day"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT OR REPLACE INTO game_days
           (day, story, npc_dialogues, player_actions, generated_content, teaser, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            day_data["day"],
            day_data["story"],
            json.dumps(day_data.get("npc_dialogues", [])),
            json.dumps(day_data.get("player_actions", [])),
            json.dumps(day_data.get("generated_content", {})),
            day_data.get("teaser"),
            datetime.now().isoformat()
        )
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
        "created_at": row["created_at"]
    }


# ============== Player Actions ==============

def save_player_action(player_id: int, day: int, action_id: str, choice: str) -> Dict[str, Any]:
    """Save a player action"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT INTO player_actions (player_id, day, action_id, choice, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (player_id, day, action_id, choice, datetime.now().isoformat())
    )

    action_id_db = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "id": action_id_db,
        "player_id": player_id,
        "day": day,
        "action_id": action_id,
        "choice": choice
    }


def get_player_actions(player_id: int, day: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get player actions, optionally filtered by day"""
    conn = get_db_connection()
    cursor = conn.cursor()

    if day:
        cursor.execute(
            "SELECT * FROM player_actions WHERE player_id = ? AND day = ? ORDER BY created_at",
            (player_id, day)
        )
    else:
        cursor.execute(
            "SELECT * FROM player_actions WHERE player_id = ? ORDER BY created_at",
            (player_id,)
        )

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


# ============== Game Messages ==============

def add_game_message(player_id: int, message: str, message_type: str = "text") -> Dict[str, Any]:
    """Add a game message"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT INTO game_messages (player_id, message, message_type, timestamp)
           VALUES (?, ?, ?, ?)""",
        (player_id, message, message_type, datetime.now().isoformat())
    )

    message_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "id": message_id,
        "player_id": player_id,
        "message": message,
        "message_type": message_type,
        "timestamp": datetime.now().isoformat()
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
        (player_id, limit)
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
        "last_updated": row["last_updated"]
    }


def update_game_state(day: int, status: str = "active") -> Dict[str, Any]:
    """Update game state"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE game_state
           SET day = ?, status = ?, last_updated = ?
           WHERE id = 1""",
        (day, status, datetime.now().isoformat())
    )

    conn.commit()
    conn.close()

    return get_game_state()