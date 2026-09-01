"""Balance simulator: an integration bench over the pure game_rules functions.

This is a POLICY SIMULATOR, not a correctness test. It runs the whole turn
loop (apply_mission_progress → apply_ship_status → compute_threat_tick →
end checks → compute_outcome_type) with three deterministic crew strategies
against one canonical mission (3 stages, thresholds 3/4/5) and asserts the
TIMINGS at which each strategy ends. No LLM, no DB, no randomness.

The assertion ranges below encode the intended game balance:
- PASSIVE (no progress at all, every action auto-selected) must lose to the
  doom clock well before a steady crew could finish, and the verdict must be
  defeat;
- STEADY (+2 mission points per turn, mostly manual actions) must finish the
  mission in roughly the same window the threat clock needs to become
  dangerous, and win;
- RECKLESS (same progress but heavy hull damage every turn) must still finish
  the mission, but pay for it with a pyrrhic-grade verdict.

If these asserts fail after you changed game_rules constants (THREAT_*,
HULL_*, thresholds, outcome ratios...), THE BALANCE HAS CHANGED. Do not
blindly update the ranges: re-run the simulation, read the new timings and
decide consciously whether the new balance is the one you want, then update
the ranges (and this docstring) in the same commit.

Reference timings this file was calibrated against (constants as of the
threat/wound/outcome refactor):
- PASSIVE  — threat tick 15/turn (base 8 + auto 5 + stagnation 2): lost at
             turn 7, verdict defeat (progress 0 < stalemate ratio 0.6);
- STEADY   — tick 9/turn (base 8 + auto 1): mission completed at turn 7
             (sum 14 over thresholds 3/4/5), threat 63, hull intact → triumph;
- RECKLESS — same progress, hull -12/turn (hull penalty +3 from turn 6):
             mission completed at turn 7 with hull 16, threat 69 → pyrrhic
             (hull would reach 0 only at turn 9 — the mission finishes first).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_rules import (  # noqa: E402
    HULL_MAX,
    SHIELDS_MAX,
    THREAT_MAX,
    apply_mission_progress,
    apply_ship_status,
    compute_outcome_type,
    compute_threat_tick,
    normalize_mission,
)

MAX_TURNS = 30
# 3 stages with thresholds 3/4/5: the perfect-progress denominator.
STAGE_THRESHOLDS = (3, 4, 5)
TOTAL_THRESHOLD = sum(STAGE_THRESHOLDS)


def make_mission() -> dict:
    return normalize_mission(
        {
            "name": "Balance sim mission",
            "objectives": [
                {"stage": i + 1, "name": f"Stage {i + 1}", "description": "", "success_threshold": thr}
                for i, thr in enumerate(STAGE_THRESHOLDS)
            ],
            "stage_progress": {},
        }
    )


def simulate_turn_loop(
    entries_fn,
    *,
    auto_ratio: float,
    hull_delta: int = 0,
    shields_delta: int = 0,
) -> dict:
    """Run the deterministic per-turn pipeline until the game ends.

    Each turn: apply_mission_progress → apply_ship_status →
    compute_threat_tick (hull_ratio from the CURRENT hull, stagnation =
    the progress sum did not grow this turn) → end checks in the order
    mission-completed / hull<=0 / threat>=THREAT_MAX, each resolved with
    compute_outcome_type from the actual end-state. Crew is never injured
    or killed by these strategies, so alive_crew_ratio stays 1.0.
    """
    mission = make_mission()
    hull, shields = HULL_MAX, SHIELDS_MAX
    threat = 0
    for turn in range(1, MAX_TURNS + 1):
        prev_sum = sum(mission["stage_progress"].values())
        mission = apply_mission_progress(mission, entries_fn(mission))
        hull, shields = apply_ship_status(hull, shields, hull_delta, shields_delta)
        cur_sum = sum(mission["stage_progress"].values())
        threat = compute_threat_tick(
            threat,
            auto_ratio=auto_ratio,
            hull_ratio=hull / HULL_MAX,
            mission_stagnant=cur_sum <= prev_sum,
        )
        progress_ratio = cur_sum / TOTAL_THRESHOLD

        if mission["completed"]:
            return _end(
                turn=turn,
                outcome=compute_outcome_type(
                    mission_completed=True,
                    mission_progress_ratio=1.0,
                    hull_ratio=hull / HULL_MAX,
                    alive_crew_ratio=1.0,
                    threat_level=threat,
                    ship_destroyed=hull <= 0,
                    crew_wiped=False,
                ),
                hull=hull,
                threat=threat,
                progress_sum=cur_sum,
                mission_completed=True,
            )
        if hull <= 0:
            return _end(
                turn=turn,
                outcome=compute_outcome_type(
                    mission_completed=False,
                    mission_progress_ratio=progress_ratio,
                    hull_ratio=0.0,
                    alive_crew_ratio=1.0,
                    threat_level=threat,
                    ship_destroyed=True,
                    crew_wiped=False,
                ),
                hull=hull,
                threat=threat,
                progress_sum=cur_sum,
                mission_completed=False,
            )
        if threat >= THREAT_MAX:
            return _end(
                turn=turn,
                outcome=compute_outcome_type(
                    mission_completed=False,
                    mission_progress_ratio=progress_ratio,
                    hull_ratio=hull / HULL_MAX,
                    alive_crew_ratio=1.0,
                    threat_level=threat,
                    ship_destroyed=False,
                    crew_wiped=False,
                ),
                hull=hull,
                threat=threat,
                progress_sum=cur_sum,
                mission_completed=False,
            )
    return _end(
        turn=MAX_TURNS,
        outcome="no_end",
        hull=hull,
        threat=threat,
        progress_sum=sum(mission["stage_progress"].values()),
        mission_completed=mission["completed"],
    )


def _end(*, turn, outcome, hull, threat, progress_sum, mission_completed) -> dict:
    return {
        "turn": turn,
        "outcome": outcome,
        "hull": hull,
        "threat": threat,
        "progress_sum": progress_sum,
        "mission_completed": mission_completed,
    }


def steady_entries(mission: dict) -> list[dict]:
    """LLM-typical progress: several players push the working stage by +2."""
    return [{"stage": mission["current_stage"], "points": 2}]


def passive_entries(_mission: dict) -> list[dict]:
    """Every action auto-selected by timer: no mission progress at all."""
    return []


class TestPassiveStrategy(unittest.TestCase):
    """PASSIVE: total inaction must lose to the doom clock, and early."""

    def test_loses_to_threat_within_balance_window(self):
        result = simulate_turn_loop(passive_entries, auto_ratio=1.0)
        # Calibrated: threat tick 15/turn (8 base + 5 auto + 2 stagnation)
        # reaches 100 at turn 7. The window is centered on that.
        self.assertTrue(
            6 <= result["turn"] <= 8,
            f"PASSIVE should lose to threat on turns 6-8, got turn {result['turn']} "
            f"(threat {result['threat']}, hull {result['hull']}) — balance changed, "
            "see module docstring",
        )
        self.assertEqual(result["outcome"], "defeat", f"got {result['outcome']}")
        self.assertFalse(result["mission_completed"])
        self.assertEqual(result["threat"], THREAT_MAX)
        self.assertEqual(result["hull"], HULL_MAX, "nothing damages a passive crew's hull")

    def test_threat_grows_every_turn(self):
        mission = make_mission()
        hull, shields = HULL_MAX, SHIELDS_MAX
        threat = 0
        for _ in range(3):
            prev_sum = sum(mission["stage_progress"].values())
            mission = apply_mission_progress(mission, passive_entries(mission))
            hull, shields = apply_ship_status(hull, shields, 0, 0)
            cur_sum = sum(mission["stage_progress"].values())
            prev_threat = threat
            threat = compute_threat_tick(
                threat,
                auto_ratio=1.0,
                hull_ratio=hull / HULL_MAX,
                mission_stagnant=cur_sum <= prev_sum,
            )
            self.assertGreater(threat, prev_threat)
        self.assertEqual(threat, 45, "3 passive turns at tick 15")


class TestSteadyStrategy(unittest.TestCase):
    """STEADY: constant moderate progress must finish and win."""

    def test_completes_mission_within_balance_window(self):
        result = simulate_turn_loop(steady_entries, auto_ratio=0.2)
        # Calibrated: +2/turn over thresholds 3/4/5 finishes on turn 7 with
        # threat 63 (tick 9/turn) — comfortably before the clock, intact hull.
        self.assertTrue(
            7 <= result["turn"] <= 10,
            f"STEADY should finish the mission on turns 7-10, got turn {result['turn']} "
            f"(threat {result['threat']}, hull {result['hull']}) — balance changed, "
            "see module docstring",
        )
        self.assertIn(result["outcome"], ("triumph", "victory"), f"got {result['outcome']}")
        self.assertTrue(result["mission_completed"])
        self.assertLess(result["threat"], THREAT_MAX, "a steady crew must beat the doom clock")

    def test_full_threshold_sum_is_not_completion(self):
        """Reaching TOTAL_THRESHOLD points is NOT enough: points must be
        spread across ALL stages (sum 12/12 with stage 3 at 4 < 5 keeps the
        mission open). Guards against 'farm one stage' degenerate play."""
        mission = apply_mission_progress(make_mission(), [{"stage": 1, "points": 4}])
        mission = apply_mission_progress(mission, [{"stage": 2, "points": 4}])
        mission = apply_mission_progress(mission, [{"stage": 3, "points": 4}])
        self.assertEqual(sum(mission["stage_progress"].values()), TOTAL_THRESHOLD)
        self.assertFalse(mission["completed"])


class TestRecklessStrategy(unittest.TestCase):
    """RECKLESS: same progress, heavy hull bleed every turn — the mission
    still gets finished, but paid for with the ship."""

    def test_finishes_mission_at_pyrrhic_cost(self):
        result = simulate_turn_loop(steady_entries, auto_ratio=0.3, hull_delta=-12)
        # Calibrated: mission completes on turn 7 (same +2/turn progress)
        # while hull is at 16 (100 - 6*12, plus threat 69 with the <40% hull
        # penalty from turn 6) → hull_ratio 0.16 < VICTORY_HULL_RATIO 0.3 →
        # pyrrhic. Hull would reach 0 only on turn 9 — the mission ends the
        # game first, so 'defeat' here would mean hull 0 arrived BEFORE
        # completion (heavier bleeding than -12/turn).
        self.assertTrue(
            6 <= result["turn"] <= 8,
            f"RECKLESS should end on turns 6-8, got turn {result['turn']} "
            f"(hull {result['hull']}, threat {result['threat']}) — balance changed, "
            "see module docstring",
        )
        self.assertTrue(result["mission_completed"], "reckless progress still finishes the mission")
        self.assertIn(result["outcome"], ("pyrrhic", "defeat"), f"got {result['outcome']}")
        self.assertLess(result["hull"], HULL_MAX * 0.3, "the recklessness must show on the hull")

    def test_hull_bleed_alone_reaches_zero_before_turn_10(self):
        """Without the mission finishing first, -12/turn hull bleed destroys
        the ship: 100 - 12*9 < 0 on turn 9 → defeat. Pins the hull-destruction
        timing separately from the mission race."""
        result = simulate_turn_loop(passive_entries, auto_ratio=0.0, hull_delta=-12)
        self.assertTrue(8 <= result["turn"] <= 10, f"got turn {result['turn']}")
        self.assertEqual(result["outcome"], "defeat", f"got {result['outcome']}")
        self.assertEqual(result["hull"], 0)


if __name__ == "__main__":
    unittest.main()
