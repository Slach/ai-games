"""Tests for entity_id-based matching of combined-outcome casualties.

The LLM addresses dead/injured/healed crew in dead_crew_members /
crew_injured / crew_healed by the stable entity_id from the crew roster
("p<player_id>" for players, "n<npc_key>" for NPCs). Legacy [name, role]
lists are a schema violation handled by an exact-roster-name fallback.
"""

import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

# Player p200 and NPC nengineer deliberately share the role "Engineer" —
# the old name/role matcher killed the wrong one when the LLM distorted names.
ROSTER = [
    {"entity_id": "p100", "name": "Аня", "role": "Pilot", "is_dead": False, "wound_severity": None},
    {"entity_id": "p200", "name": "Boris", "role": "Engineer", "is_dead": False, "wound_severity": None},
    {"entity_id": "nengineer", "name": "R5-D4", "role": "Engineer", "is_dead": False, "wound_severity": None},
    {"entity_id": "nmedic", "name": "Doc", "role": "Medical Officer", "is_dead": True, "wound_severity": None},
]


class TestParseEntityId(unittest.TestCase):
    def test_player_id(self):
        self.assertEqual(main._parse_entity_id("p100"), (100, None))

    def test_npc_key(self):
        self.assertEqual(main._parse_entity_id("nengineer"), (None, "engineer"))

    def test_invalid_ids(self):
        self.assertEqual(main._parse_entity_id("x100"), (None, None))
        self.assertEqual(main._parse_entity_id("pabc"), (None, None))
        self.assertEqual(main._parse_entity_id("n"), (None, None))
        self.assertEqual(main._parse_entity_id(123), (None, None))
        self.assertEqual(main._parse_entity_id(None), (None, None))


class TestResolveOutcomeEntity(unittest.TestCase):
    def test_player_entity_id_resolves(self):
        resolved = main._resolve_outcome_entity({"entity_id": "p100", "cause": "hull breach"}, ROSTER)
        self.assertEqual(resolved["name"], "Аня")
        self.assertEqual(main._parse_entity_id(resolved["entity_id"]), (100, None))

    def test_npc_entity_id_resolves(self):
        resolved = main._resolve_outcome_entity({"entity_id": "nengineer", "severity": "minor"}, ROSTER)
        self.assertEqual(resolved["name"], "R5-D4")
        self.assertEqual(main._parse_entity_id(resolved["entity_id"]), (None, "engineer"))

    def test_shared_role_is_never_matched(self):
        # The old matcher compared roles: a death addressed at the NPC
        # "Engineer" could deactivate the PLAYER "Engineer" instead.
        resolved = main._resolve_outcome_entity({"entity_id": "nengineer"}, ROSTER)
        self.assertEqual(resolved["entity_id"], "nengineer")

    def test_unknown_entity_id_ignored_with_warning(self):
        with self.assertLogs("main", level="WARNING") as cm:
            resolved = main._resolve_outcome_entity({"entity_id": "nghost", "cause": "?"}, ROSTER)
        self.assertIsNone(resolved)
        self.assertTrue(any("Unknown entity_id" in line for line in cm.output))

    def test_legacy_list_falls_back_to_exact_name(self):
        # Legacy [name, role] lists are a schema violation, but the exact
        # roster name still resolves — the role is ignored completely.
        with self.assertLogs("main", level="WARNING") as cm:
            resolved = main._resolve_outcome_entity(["Boris", "Chief Bottle Washer"], ROSTER)
        self.assertEqual(resolved["entity_id"], "p200")
        self.assertTrue(any("Legacy" in line for line in cm.output))

    def test_legacy_distorted_name_is_not_matched(self):
        # A declined/distorted name must NOT resolve — better to skip the
        # casualty than to apply it to the wrong character.
        with self.assertLogs("main", level="WARNING") as cm:
            resolved = main._resolve_outcome_entity(["Борису", "Engineer"], ROSTER)
        self.assertIsNone(resolved)
        self.assertTrue(any("not found in roster" in line for line in cm.output))

    def test_malformed_entry_skipped(self):
        with self.assertLogs("main", level="WARNING") as cm:
            resolved = main._resolve_outcome_entity("explosion", ROSTER)
        self.assertIsNone(resolved)
        self.assertTrue(any("Malformed outcome entry" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
