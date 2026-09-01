"""Tests for mechanical wound escalation (unit 9).

Wounds are persistent state: each new wound escalates the stored severity
by game_rules.resolve_injury (result = max(current, incoming) on the
ladder), and a critically wounded character who takes ANY new wound dies
— the only other death channel besides the LLM's [fatal] tag. The
crew_injured handler in main.py (_apply_crew_injuries) applies the same
rules against real profiles.
"""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_rules import WOUND_DEAD, resolve_injury  # noqa: E402

# main.py hardcodes a daily /app/logs file handler at import time — that
# directory only exists inside the container. Redirect file logging to a
# temp file for the duration of the import so the module loads on the host.
_orig_file_handler = logging.FileHandler


class _TempFileHandler(logging.FileHandler):
    def __init__(self, filename, *args, **kwargs):
        if str(filename).startswith("/app/logs/"):
            filename = os.path.join(tempfile.mkdtemp(), os.path.basename(filename))
        super().__init__(filename, *args, **kwargs)


logging.FileHandler = _TempFileHandler
import main  # noqa: E402

logging.FileHandler = _orig_file_handler

logger = logging.getLogger(__name__)

ROSTER = [
    {"entity_id": "p1", "name": "Aня", "role": "Pilot", "is_dead": False, "wound_severity": None},
    {"entity_id": "p2", "name": "Boris", "role": "Engineer", "is_dead": False, "wound_severity": None},
    {"entity_id": "nmedic", "name": "Doc", "role": "Medical Officer", "is_dead": False, "wound_severity": None},
]


class TestResolveInjury(unittest.TestCase):
    def test_ladder_from_healthy(self):
        self.assertEqual(resolve_injury(None, "minor"), "minor")
        self.assertEqual(resolve_injury("healthy", "minor"), "minor")
        self.assertEqual(resolve_injury(None, "moderate"), "moderate")
        self.assertEqual(resolve_injury(None, "critical"), "critical")

    def test_result_is_max_on_ladder(self):
        self.assertEqual(resolve_injury("minor", "moderate"), "moderate")
        self.assertEqual(resolve_injury("moderate", "minor"), "moderate")
        self.assertEqual(resolve_injury("moderate", "critical"), "critical")

    def test_critical_plus_any_new_wound_is_death(self):
        self.assertEqual(resolve_injury("critical", "minor"), WOUND_DEAD)
        self.assertEqual(resolve_injury("critical", "moderate"), WOUND_DEAD)
        self.assertEqual(resolve_injury("critical", "critical"), WOUND_DEAD)

    def test_unknown_severities_treated_as_minor(self):
        self.assertEqual(resolve_injury(None, "scratched"), "minor")
        self.assertEqual(resolve_injury(None, ""), "minor")
        self.assertEqual(resolve_injury(None, None), "minor")
        # Unknown stored severity ranks as minor, never healthy-zero.
        self.assertEqual(resolve_injury("wounded", "minor"), "minor")
        self.assertEqual(resolve_injury("wounded", "critical"), "critical")

    def test_none_current_plus_moderate_is_moderate(self):
        self.assertEqual(resolve_injury(None, "moderate"), "moderate")
        self.assertEqual(resolve_injury("healthy", "moderate"), "moderate")


class TestApplyCrewInjuriesHandler(unittest.TestCase):
    """The crew_injured handler escalates wounds and kills mechanically.

    main.py imports the DB functions directly, so they are patched here —
    the wiring (which function is called with what) is what matters.
    """

    GAME_ID = "g9"

    def _run(self, outcome, profiles):
        with (
            patch("main.get_player_profile", side_effect=lambda pid: profiles.get(pid)),
            patch("main.get_npc_profile", side_effect=lambda key: profiles.get(key)),
            patch("main.set_player_wound_severity") as set_player,
            patch("main.set_npc_wound_severity") as set_npc,
            patch("main.mark_player_dead") as mark_dead,
            patch("main.deactivate_npc") as deactivate,
        ):
            notices, newly_dead = main._apply_crew_injuries(outcome, ROSTER, game_id=self.GAME_ID)
        return notices, newly_dead, set_player, set_npc, mark_dead, deactivate

    def test_critical_player_plus_new_injury_dies(self):
        profiles = {1: {"wound_severity": "critical"}}
        outcome = {"crew_injured": [{"entity_id": "p1", "severity": "minor"}]}
        notices, newly_dead, set_player, _set_npc, mark_dead, _deactivate = self._run(outcome, profiles)

        mark_dead.assert_called_once_with(1, self.GAME_ID)
        set_player.assert_not_called()
        self.assertEqual(newly_dead, {1})
        # The dead character gets no injury notice — their death surfaces via
        # the death-notice roster instead.
        self.assertEqual(notices, [])

    def test_healthy_player_wound_escalates_to_incoming(self):
        profiles = {2: {"wound_severity": None}}
        outcome = {"crew_injured": [{"entity_id": "p2", "severity": "moderate"}]}
        notices, newly_dead, set_player, _set_npc, mark_dead, _deactivate = self._run(outcome, profiles)

        set_player.assert_called_once_with(2, self.GAME_ID, "moderate")
        mark_dead.assert_not_called()
        self.assertEqual(newly_dead, set())
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["severity"], "moderate")

    def test_moderate_npc_not_downgraded_by_minor(self):
        profiles = {"medic": {"wound_severity": "moderate"}}
        outcome = {"crew_injured": [{"entity_id": "nmedic", "severity": "minor"}]}
        _notices, _newly_dead, _set_player, set_npc, _mark_dead, deactivate = self._run(outcome, profiles)

        set_npc.assert_called_once_with("medic", "moderate")
        deactivate.assert_not_called()

    def test_critical_npc_dies_of_accumulated_wounds(self):
        profiles = {"medic": {"wound_severity": "critical"}}
        outcome = {"crew_injured": [{"entity_id": "nmedic", "severity": "moderate"}]}
        notices, newly_dead, _set_player, set_npc, _mark_dead, deactivate = self._run(outcome, profiles)

        deactivate.assert_called_once_with("medic")
        set_npc.assert_not_called()
        self.assertEqual(newly_dead, set())
        self.assertEqual(notices, [])


class TestApplyCrewInjuriesWithDB(unittest.TestCase):
    """End-to-end against a real temp DB: the handler must mark a
    critically wounded player dead and persist the escalated severity."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        import database as db

        db.DB_PATH = Path(self._tmp.name)
        db.init_db()
        db.create_game(
            {
                "game_id": "g1",
                "name": "Test Game",
                "setting": "starship",
                "language": "ru",
            }
        )

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name, exc_info=True)

    def test_critical_player_marked_dead_in_db(self):
        import database as db

        db.create_player_profile(
            {"player_id": 1, "role": "Пилот", "game_id": "g1", "player_name": "A"}
        )
        db.set_player_wound_severity(1, "g1", "critical")

        outcome = {"crew_injured": [{"entity_id": "p1", "severity": "minor"}]}
        notices, newly_dead = main._apply_crew_injuries(outcome, ROSTER, game_id="g1")

        self.assertTrue(db.get_player_profile(1)["is_dead"])
        self.assertEqual(newly_dead, {1})
        self.assertEqual(notices, [])

    def test_escalated_severity_persisted_in_db(self):
        import database as db

        db.create_player_profile(
            {"player_id": 1, "role": "Пилот", "game_id": "g1", "player_name": "A"}
        )
        db.set_player_wound_severity(1, "g1", "minor")

        outcome = {"crew_injured": [{"entity_id": "p1", "severity": "moderate"}]}
        notices, newly_dead = main._apply_crew_injuries(outcome, ROSTER, game_id="g1")

        self.assertEqual(db.get_player_profile(1)["wound_severity"], "moderate")
        self.assertFalse(db.get_player_profile(1)["is_dead"])
        self.assertEqual(newly_dead, set())
        self.assertEqual(len(notices), 1)


if __name__ == "__main__":
    unittest.main()
