"""NPC loyalty: code-owned morale that drops from losses and can end in mutiny.

compute_loyalty_change turns the turn's facts (deaths, hull damage, mission
points, healed NPCs) into a loyalty delta clamped to [-25, +7]; every active
NPC gets the same delta via adjust_npc_loyalty. mutiny_conditions fires when
two or more active NPCs sit at loyalty <= MUTINY_LOYALTY_THRESHOLD —
_analyze_turn_outcome then ends the game with end_game("mutiny"), a defeat
path of its own. These tests cover the pure functions, the DB roundtrip with
clamps, the end_game("mutiny") integration (test_end_game pattern), and the
localized band/reason strings.
"""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_rules import (  # noqa: E402
    LOYALTY_CHANGE_MAX,
    LOYALTY_CHANGE_MIN,
    LOYALTY_DEATH_PENALTY,
    LOYALTY_DEATHS_CAP,
    LOYALTY_HEAL_BONUS,
    LOYALTY_HEAL_CAP,
    LOYALTY_HULL_DAMAGE_PENALTY,
    LOYALTY_HULL_DAMAGE_THRESHOLD,
    LOYALTY_MISSION_GAIN,
    LOYALTY_MISSION_LOSS,
    MUTINY_LOYALTY_THRESHOLD,
    MUTINY_MIN_DISAFFECTED,
    compute_loyalty_change,
    compute_outcome_type,
    loyalty_band,
    mutiny_conditions,
)
import database as db  # noqa: E402
from language import LANGUAGE_EN, LANGUAGE_RU, get_game_strings  # noqa: E402
from prompts import build_npc_decision_prompts  # noqa: E402

logger = logging.getLogger(__name__)


class TestComputeLoyaltyChange(unittest.TestCase):
    def test_no_events_is_zero(self):
        self.assertEqual(compute_loyalty_change(), 0)
        self.assertEqual(compute_loyalty_change(deaths_count=0, hull_damage=0, mission_delta=0, healed_count=0), 0)

    def test_death_penalty(self):
        self.assertEqual(compute_loyalty_change(deaths_count=1), -LOYALTY_DEATH_PENALTY)
        self.assertEqual(compute_loyalty_change(deaths_count=2), -2 * LOYALTY_DEATH_PENALTY)
        # capped at LOYALTY_DEATHS_CAP no matter how many died this turn
        self.assertEqual(compute_loyalty_change(deaths_count=3), -LOYALTY_DEATHS_CAP)
        self.assertEqual(compute_loyalty_change(deaths_count=10), -LOYALTY_DEATHS_CAP)

    def test_hull_damage_penalty(self):
        self.assertEqual(compute_loyalty_change(hull_damage=LOYALTY_HULL_DAMAGE_THRESHOLD), -LOYALTY_HULL_DAMAGE_PENALTY)
        self.assertEqual(compute_loyalty_change(hull_damage=60), -LOYALTY_HULL_DAMAGE_PENALTY)
        # below the threshold there is no penalty
        self.assertEqual(compute_loyalty_change(hull_damage=LOYALTY_HULL_DAMAGE_THRESHOLD - 1), 0)

    def test_mission_delta(self):
        self.assertEqual(compute_loyalty_change(mission_delta=1), LOYALTY_MISSION_GAIN)
        self.assertEqual(compute_loyalty_change(mission_delta=5), LOYALTY_MISSION_GAIN)
        self.assertEqual(compute_loyalty_change(mission_delta=-1), -LOYALTY_MISSION_LOSS)
        self.assertEqual(compute_loyalty_change(mission_delta=0), 0)

    def test_healed_bonus(self):
        self.assertEqual(compute_loyalty_change(healed_count=1), LOYALTY_HEAL_BONUS)
        self.assertEqual(compute_loyalty_change(healed_count=2), 2 * LOYALTY_HEAL_BONUS)
        # capped at LOYALTY_HEAL_CAP
        self.assertEqual(compute_loyalty_change(healed_count=3), LOYALTY_HEAL_CAP)
        self.assertEqual(compute_loyalty_change(healed_count=9), LOYALTY_HEAL_CAP)

    def test_mixed_turns(self):
        # a death partly offset by mission progress and a heal
        self.assertEqual(
            compute_loyalty_change(deaths_count=1, mission_delta=2, healed_count=1),
            -LOYALTY_DEATH_PENALTY + LOYALTY_MISSION_GAIN + LOYALTY_HEAL_BONUS,
        )
        # every loss at once — the worst possible turn
        self.assertEqual(
            compute_loyalty_change(deaths_count=5, hull_damage=40, mission_delta=-2),
            -LOYALTY_DEATHS_CAP - LOYALTY_HULL_DAMAGE_PENALTY - LOYALTY_MISSION_LOSS,
        )

    def test_clamped_to_bounds(self):
        # the worst turn lands exactly on LOYALTY_CHANGE_MIN
        worst = compute_loyalty_change(deaths_count=5, hull_damage=40, mission_delta=-2)
        self.assertEqual(worst, LOYALTY_CHANGE_MIN)
        # and can never go below it
        self.assertEqual(compute_loyalty_change(deaths_count=99, hull_damage=100, mission_delta=-99), LOYALTY_CHANGE_MIN)
        # the best turn lands exactly on LOYALTY_CHANGE_MAX
        best = compute_loyalty_change(mission_delta=3, healed_count=9)
        self.assertEqual(best, LOYALTY_CHANGE_MAX)

    def test_invalid_inputs_count_as_zero(self):
        self.assertEqual(compute_loyalty_change(deaths_count="garbage"), 0)
        self.assertEqual(compute_loyalty_change(hull_damage=None), 0)
        self.assertEqual(compute_loyalty_change(mission_delta="bad"), 0)
        self.assertEqual(compute_loyalty_change(healed_count=None), 0)
        # a numeric string still coerces (module _to_int style)
        self.assertEqual(compute_loyalty_change(deaths_count="1", mission_delta="+2", healed_count="1"), -3)
        # negative counts make no sense and read as zero
        self.assertEqual(compute_loyalty_change(deaths_count=-5, healed_count=-3), 0)


class TestMutinyConditions(unittest.TestCase):
    def test_two_at_threshold_mutiny(self):
        self.assertTrue(mutiny_conditions([MUTINY_LOYALTY_THRESHOLD, MUTINY_LOYALTY_THRESHOLD]))
        self.assertTrue(mutiny_conditions([0, 0, 90, 90]))

    def test_above_threshold_is_safe(self):
        self.assertFalse(mutiny_conditions([MUTINY_LOYALTY_THRESHOLD + 1, MUTINY_LOYALTY_THRESHOLD + 1]))
        self.assertFalse(mutiny_conditions([90, 80, 70, 60]))

    def test_one_disaffected_is_not_enough(self):
        self.assertFalse(mutiny_conditions([0]))
        self.assertFalse(mutiny_conditions([0, 90, 90, 90]))

    def test_empty_roster_never_mutinies(self):
        self.assertFalse(mutiny_conditions([]))

    def test_requires_min_disaffected(self):
        loyalties = [MUTINY_LOYALTY_THRESHOLD] * (MUTINY_MIN_DISAFFECTED - 1) + [100] * 3
        self.assertFalse(mutiny_conditions(loyalties))
        loyalties[0] = MUTINY_LOYALTY_THRESHOLD - 1
        loyalties[1] = MUTINY_LOYALTY_THRESHOLD
        self.assertTrue(mutiny_conditions(loyalties))

    def test_invalid_entries_count_as_loyal(self):
        self.assertFalse(mutiny_conditions([None, "garbage", 10]))


class TestLoyaltyBand(unittest.TestCase):
    def test_band_boundaries(self):
        self.assertEqual(loyalty_band(100), "steadfast")
        self.assertEqual(loyalty_band(70), "steadfast")
        self.assertEqual(loyalty_band(69), "uneasy")
        self.assertEqual(loyalty_band(40), "uneasy")
        self.assertEqual(loyalty_band(39), "on_edge")
        self.assertEqual(loyalty_band(20), "on_edge")
        self.assertEqual(loyalty_band(19), "mutinous")
        self.assertEqual(loyalty_band(0), "mutinous")

    def test_invalid_falls_to_mutinous(self):
        self.assertEqual(loyalty_band(None), "mutinous")
        self.assertEqual(loyalty_band("garbage"), "mutinous")


class TestLoyaltyDb(unittest.TestCase):
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

    def _make_npc(self, npc_key: str, game_id: str = "g1") -> None:
        db.create_npc_profile({"npc_key": npc_key, "role": "Инженер", "game_id": game_id, "npc_name": "N"})

    def test_new_npc_loyalty_defaults_to_70(self):
        self._make_npc("npc_a")
        profile = db.get_npc_profile("npc_a")
        self.assertEqual(profile["loyalty"], 70)

    def test_adjust_roundtrip(self):
        self._make_npc("npc_b")
        self.assertEqual(db.adjust_npc_loyalty("npc_b", -10), 60)
        self.assertEqual(db.get_npc_profile("npc_b")["loyalty"], 60)
        self.assertEqual(db.adjust_npc_loyalty("npc_b", 25), 85)
        self.assertEqual(db.get_npc_profile("npc_b")["loyalty"], 85)

    def test_adjust_clamps_to_zero(self):
        self._make_npc("npc_c")
        self.assertEqual(db.adjust_npc_loyalty("npc_c", -500), 0)
        self.assertEqual(db.get_npc_profile("npc_c")["loyalty"], 0)
        # staying at the floor
        self.assertEqual(db.adjust_npc_loyalty("npc_c", -5), 0)

    def test_adjust_clamps_to_hundred(self):
        self._make_npc("npc_d")
        self.assertEqual(db.adjust_npc_loyalty("npc_d", 500), 100)
        self.assertEqual(db.get_npc_profile("npc_d")["loyalty"], 100)
        self.assertEqual(db.adjust_npc_loyalty("npc_d", 5), 100)

    def test_adjust_unknown_npc_returns_zero(self):
        self.assertEqual(db.adjust_npc_loyalty("npc_missing", -10), 0)


class TestMutinyEndsGame(unittest.TestCase):
    """Integration: two active NPCs dropping to the threshold end the game.

    Mirrors the loyalty block in _analyze_turn_outcome (main.py): collect the
    turn's facts, apply compute_loyalty_change to every active NPC via
    adjust_npc_loyalty, and end_game("mutiny") when mutiny_conditions fires.
    The finale verdict for that end-state is a plain defeat.
    """

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

    def _apply_turn(self, game_id: str, *, deaths_count=0, hull_damage=0, mission_delta=0, healed_count=0) -> bool:
        change = compute_loyalty_change(
            deaths_count=deaths_count,
            hull_damage=hull_damage,
            mission_delta=mission_delta,
            healed_count=healed_count,
        )
        new_loyalties = [db.adjust_npc_loyalty(n["npc_key"], change) for n in db.get_all_active_npcs(game_id)]
        if mutiny_conditions(new_loyalties):
            db.end_game("mutiny", game_id=game_id)
            return True
        return False

    def test_loyalty_death_spiral_ends_in_mutiny(self):
        game_id = "g1"
        db.create_game({"game_id": game_id, "name": "T", "setting": "starship", "language": "ru"})
        db.create_npc_profile({"npc_key": "npc_eng", "role": "Инженер", "game_id": game_id, "npc_name": "N1"})
        db.create_npc_profile({"npc_key": "npc_med", "role": "Медик", "game_id": game_id, "npc_name": "N2"})
        db.create_npc_profile({"npc_key": "npc_pil", "role": "Пилот", "game_id": game_id, "npc_name": "N3"})
        self.assertTrue(db.is_game_active(game_id))

        # 70 - 5*16 = floor 0 after three brutal turns
        self.assertFalse(self._apply_turn(game_id, deaths_count=3, hull_damage=30))
        self.assertEqual(db.get_game_state(game_id)["status"], "active")
        self.assertFalse(self._apply_turn(game_id, deaths_count=3, hull_damage=30))
        # third turn drops everyone to 0 → two disaffected → mutiny
        self.assertTrue(self._apply_turn(game_id, deaths_count=3, hull_damage=30))

        state = db.get_game_state(game_id)
        self.assertEqual(state["status"], "mutiny")
        self.assertFalse(state["ship_alive"])
        self.assertFalse(db.is_game_active(game_id))
        self.assertEqual(db.get_game(game_id)["status"], "ended")

    def test_mutiny_end_state_verdicts_as_defeat(self):
        outcome = compute_outcome_type(
            mission_completed=False,
            mission_progress_ratio=0.5,
            hull_ratio=0.8,
            alive_crew_ratio=1.0,
            threat_level=40,
            ship_destroyed=False,
            crew_wiped=False,
        )
        self.assertEqual(outcome, "defeat")

    def test_quiet_crew_never_mutinies(self):
        game_id = "g2"
        db.create_game({"game_id": game_id, "name": "T", "setting": "starship", "language": "ru"})
        db.create_npc_profile({"npc_key": "npc_eng2", "role": "Инженер", "game_id": game_id, "npc_name": "N1"})
        db.create_npc_profile({"npc_key": "npc_med2", "role": "Медик", "game_id": game_id, "npc_name": "N2"})

        # mission progress slowly lifts morale by the per-turn max
        self.assertFalse(self._apply_turn(game_id, mission_delta=2, healed_count=2))
        self.assertFalse(self._apply_turn(game_id, mission_delta=2, healed_count=2))
        self.assertEqual(db.get_game_state(game_id)["status"], "active")
        loyalties = [db.get_npc_profile(k)["loyalty"] for k in ("npc_eng2", "npc_med2")]
        self.assertEqual(loyalties, [70 + 2 * LOYALTY_CHANGE_MAX, 70 + 2 * LOYALTY_CHANGE_MAX])


class TestNpcDecisionPromptCarriesLoyalty(unittest.TestCase):
    """The NPC must be told its loyalty and how that band makes it act."""

    def _system(self, language: str, loyalty: int) -> str:
        system, _user = build_npc_decision_prompts(
            language,
            "Рипли",
            "Инженер",
            ["хладнокровная"],
            "  [a1] Починить реактор",
            loyalty=loyalty,
            use_vs=False,
            vs_k=3,
        )
        return system

    def test_ru_prompt_states_loyalty_and_band_rule(self):
        system = self._system(LANGUAGE_RU, 75)
        self.assertIn("лояльность командованию: 75/100", system)
        self.assertIn("в интересах миссии", system)
        self.assertIn("самосохранение", self._system(LANGUAGE_RU, 30))
        self.assertIn("саботируешь", self._system(LANGUAGE_RU, 5))

    def test_en_prompt_states_loyalty_and_band_rule(self):
        system = self._system(LANGUAGE_EN, 45)
        self.assertIn("loyalty to command: 45/100", system)
        self.assertIn("doubt the orders", system)
        self.assertIn("mutiny against command", self._system(LANGUAGE_EN, 10))


class TestLoyaltyStrings(unittest.TestCase):
    def test_ru_band_words_and_mutiny_reason(self):
        gs = get_game_strings(LANGUAGE_RU)
        self.assertEqual(gs["npc_loyalty"]["steadfast"], "предан")
        self.assertEqual(gs["npc_loyalty"]["uneasy"], "нервничает")
        self.assertEqual(gs["npc_loyalty"]["on_edge"], "на грани")
        self.assertEqual(gs["npc_loyalty"]["mutinous"], "готов взбунтоваться")
        self.assertEqual(gs["game_over"]["reason_mutiny"], "Причина конца: мятеж экипажа")

    def test_en_band_words_and_mutiny_reason(self):
        gs = get_game_strings(LANGUAGE_EN)
        self.assertEqual(gs["npc_loyalty"]["steadfast"], "steadfast")
        self.assertEqual(gs["npc_loyalty"]["uneasy"], "uneasy")
        self.assertEqual(gs["npc_loyalty"]["on_edge"], "on edge")
        self.assertEqual(gs["npc_loyalty"]["mutinous"], "ready to mutiny")
        self.assertEqual(gs["game_over"]["reason_mutiny"], "End reason: crew mutiny")

    def test_every_end_status_has_a_reason_line(self):
        for lang in (LANGUAGE_RU, LANGUAGE_EN):
            reasons = get_game_strings(lang)["game_over"]
            for status in ("mission_complete", "ship_destroyed", "crew_wiped", "overwhelmed", "mutiny"):
                self.assertIn(f"reason_{status}", reasons)
                self.assertTrue(reasons[f"reason_{status}"].strip())


if __name__ == "__main__":
    unittest.main()
