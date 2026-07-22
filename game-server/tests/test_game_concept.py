"""Tests for the linked game concept pipeline (generate_game_concept) and the
one-mission-per-game guarantee (uq_game_mission + ON CONFLICT)."""

import os
import sys
import asyncio
import tempfile
import logging
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import game_concept  # noqa: E402

logger = logging.getLogger(__name__)


def _fake_mission(name="Operation Test"):
    return {
        "name": name,
        "description": "A test mission briefing.",
        "short_description": "Short test mission.",
        "objectives": [
            {"stage": 1, "name": "A", "description": "a", "success_threshold": 3},
            {"stage": 2, "name": "B", "description": "b", "success_threshold": 3},
        ],
        "archetype": "anomaly",
        "seeds": {"setting": "nebula", "complication": "pirates"},
    }


class FakeGameServer:
    def __init__(self, *args, **kwargs):
        self.mission_calls = 0
        self.title_calls = 0

    async def generate_mission(self, **kwargs):
        self.mission_calls += 1
        return _fake_mission()

    async def generate_game_title(self, *, game_id, player_id, turn, kind, mission_context=None):
        self.title_calls += 1
        return {
            "title": f"Star Cruiser Test: {mission_context.get('short_description', '') if mission_context else 'tagline'}",
            "welcome_text": "Welcome aboard the test cruiser.",
        }


class TestUniqueMissionPerGame(unittest.TestCase):
    """The uq_game_mission index + ON CONFLICT must keep one mission per game."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name, exc_info=True)

    def test_duplicate_insert_is_ignored(self):
        first = db.create_mission(_fake_mission("First"), "g1")
        self.assertIsNotNone(first)
        # A second insert for the same game must NOT raise and must NOT create
        # a second row — the existing mission is returned.
        second = db.create_mission(_fake_mission("Second"), "g1")
        self.assertIsNotNone(second)
        self.assertEqual(second["name"], "First")
        # Only one row exists.
        conn = db.get_db_connection()
        count = conn.execute("SELECT COUNT(*) FROM game_missions WHERE game_id = ?", ("g1",)).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


class TestGenerateGameConcept(unittest.TestCase):
    """_generate_game_concept: idempotency, mission→title linkage, concurrency."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()
        db.create_game({
            "game_id": "g1",
            "name": "placeholder",
            "description": "",
            "setting": "starship",
            "status": "active",
            "max_players": 10,
            "language": "en",
            "created_at": "2026-01-01T00:00:00",
        })

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name, exc_info=True)

    def test_generates_mission_then_title_linked_to_it(self):
        fake = FakeGameServer()
        with patch.object(game_concept, "create_game_server", return_value=fake):
            concept = asyncio.run(game_concept.generate_game_concept("g1", "en"))

        self.assertEqual(concept["mission"]["name"], "Operation Test")
        self.assertIn("Short test mission.", concept["title"])
        self.assertEqual(fake.mission_calls, 1)
        self.assertEqual(fake.title_calls, 1)
        # Persisted to DB.
        self.assertEqual(db.get_game_title("g1"), concept["title"])

    def test_idempotent_second_call_reuses_mission_and_title(self):
        fake = FakeGameServer()
        with patch.object(game_concept, "create_game_server", return_value=fake):
            first = asyncio.run(game_concept.generate_game_concept("g1", "en"))
            second = asyncio.run(game_concept.generate_game_concept("g1", "en"))

        # Mission + title generated exactly once each across both calls.
        self.assertEqual(fake.mission_calls, 1)
        self.assertEqual(fake.title_calls, 1)
        self.assertEqual(first["title"], second["title"])
        self.assertEqual(first["mission"]["name"], second["mission"]["name"])

    def test_concurrent_calls_produce_one_mission(self):
        """Two near-simultaneous _generate_game_concept calls for the same
        game must not produce two missions (per-game lock + unique index)."""

        async def _run_both():
            return await asyncio.gather(
                game_concept.generate_game_concept("g1", "en"),
                game_concept.generate_game_concept("g1", "en"),
            )

        fake = FakeGameServer()
        with patch.object(game_concept, "create_game_server", return_value=fake):
            results = asyncio.run(_run_both())

        # Exactly one mission row regardless of how many LLM calls raced.
        conn = db.get_db_connection()
        count = conn.execute("SELECT COUNT(*) FROM game_missions WHERE game_id = ?", ("g1",)).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)
        # Both callers received the same mission.
        self.assertEqual(results[0]["mission"]["name"], results[1]["mission"]["name"])
        self.assertEqual(results[0]["title"], results[1]["title"])


if __name__ == "__main__":
    unittest.main()
