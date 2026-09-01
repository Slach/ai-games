"""Unit tests for the five-outcome game-over matrix (compute_outcome_type).

The verdict is computed by CODE from the end-state; these tests pin every
branch and both sides of each threshold boundary:
triumph > victory > pyrrhic (mission completed) and
defeat / stalemate (mission not completed).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_rules import (  # noqa: E402
    OUTCOME_DEFEAT,
    OUTCOME_PYRRHIC,
    OUTCOME_STALEMATE,
    OUTCOME_TRIUMPH,
    OUTCOME_VICTORY,
    compute_outcome_type,
)


def outcome(**overrides):
    """compute_outcome_type with a neutral baseline; overrides win."""
    args = dict(
        mission_completed=False,
        mission_progress_ratio=0.0,
        hull_ratio=1.0,
        alive_crew_ratio=1.0,
        threat_level=0,
        ship_destroyed=False,
        crew_wiped=False,
    )
    args.update(overrides)
    return compute_outcome_type(**args)


class TestTriumph(unittest.TestCase):
    def test_flawless_completion_is_triumph(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=0.6, alive_crew_ratio=0.7, threat_level=69),
            OUTCOME_TRIUMPH,
        )

    def test_hull_below_triumph_threshold_is_victory(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=0.59, alive_crew_ratio=0.9, threat_level=10),
            OUTCOME_VICTORY,
        )

    def test_crew_below_triumph_threshold_is_victory(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=0.9, alive_crew_ratio=0.69, threat_level=10),
            OUTCOME_VICTORY,
        )

    def test_threat_at_70_blocks_triumph(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=0.9, alive_crew_ratio=0.9, threat_level=70),
            OUTCOME_VICTORY,
        )


class TestVictory(unittest.TestCase):
    def test_victory_threshold_boundaries(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=0.3, alive_crew_ratio=0.4, threat_level=90),
            OUTCOME_VICTORY,
        )

    def test_hull_below_victory_threshold_is_pyrrhic(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=0.29, alive_crew_ratio=0.9, threat_level=10),
            OUTCOME_PYRRHIC,
        )

    def test_crew_below_victory_threshold_is_pyrrhic(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=0.9, alive_crew_ratio=0.39, threat_level=10),
            OUTCOME_PYRRHIC,
        )


class TestPyrrhic(unittest.TestCase):
    def test_mission_completed_with_ship_destroyed_is_pyrrhic(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=0.0, alive_crew_ratio=0.5, ship_destroyed=True),
            OUTCOME_PYRRHIC,
        )

    def test_mission_completed_with_crew_wiped_is_pyrrhic(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=0.8, alive_crew_ratio=0.0, crew_wiped=True),
            OUTCOME_PYRRHIC,
        )

    def test_mission_completed_with_ship_destroyed_and_wiped_is_pyrrhic(self):
        self.assertEqual(
            outcome(
                mission_completed=True,
                hull_ratio=0.0,
                alive_crew_ratio=0.0,
                ship_destroyed=True,
                crew_wiped=True,
            ),
            OUTCOME_PYRRHIC,
        )


class TestStalemate(unittest.TestCase):
    def test_threat_max_with_enough_progress_is_stalemate(self):
        self.assertEqual(
            outcome(threat_level=100, mission_progress_ratio=0.6),
            OUTCOME_STALEMATE,
        )

    def test_threat_max_with_low_progress_is_defeat(self):
        self.assertEqual(
            outcome(threat_level=100, mission_progress_ratio=0.59),
            OUTCOME_DEFEAT,
        )

    def test_threat_below_max_is_defeat_even_with_progress(self):
        self.assertEqual(
            outcome(threat_level=99, mission_progress_ratio=0.9),
            OUTCOME_DEFEAT,
        )


class TestDefeat(unittest.TestCase):
    def test_ship_destroyed_without_mission_is_defeat(self):
        self.assertEqual(outcome(ship_destroyed=True, hull_ratio=0.0), OUTCOME_DEFEAT)

    def test_crew_wiped_without_mission_is_defeat(self):
        self.assertEqual(outcome(crew_wiped=True, alive_crew_ratio=0.0), OUTCOME_DEFEAT)

    def test_ship_destroyed_beats_threat_stalemate(self):
        self.assertEqual(
            outcome(ship_destroyed=True, threat_level=100, mission_progress_ratio=0.9),
            OUTCOME_DEFEAT,
        )

    def test_plain_unfinished_game_is_defeat(self):
        self.assertEqual(outcome(), OUTCOME_DEFEAT)


class TestInvalidInputs(unittest.TestCase):
    def test_all_none_inputs_default_to_defeat(self):
        self.assertEqual(
            compute_outcome_type(
                mission_completed=None,
                mission_progress_ratio=None,
                hull_ratio=None,
                alive_crew_ratio=None,
                threat_level=None,
                ship_destroyed=None,
                crew_wiped=None,
            ),
            OUTCOME_DEFEAT,
        )

    def test_invalid_ratios_fall_back_to_intact_defaults(self):
        # hull/crew invalid → 1.0, threat invalid → 0: a completed mission
        # with unknown damage reads as flawless.
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio="oops", alive_crew_ratio=None, threat_level=None),
            OUTCOME_TRIUMPH,
        )

    def test_invalid_progress_ratio_counts_as_zero(self):
        self.assertEqual(outcome(threat_level=100, mission_progress_ratio="NaN"), OUTCOME_DEFEAT)

    def test_out_of_range_ratios_are_clamped(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=5.0, alive_crew_ratio=2.0),
            OUTCOME_TRIUMPH,
        )
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=-3.0),
            OUTCOME_PYRRHIC,
        )

    def test_threat_above_max_clamps_to_100_and_blocks_triumph(self):
        self.assertEqual(
            outcome(mission_completed=True, hull_ratio=1.0, alive_crew_ratio=1.0, threat_level=500),
            OUTCOME_VICTORY,
        )


if __name__ == "__main__":
    unittest.main()
