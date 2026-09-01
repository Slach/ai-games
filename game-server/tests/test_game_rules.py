"""Unit tests for the game-rules layer (pure functions, no DB/LLM)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_rules import (  # noqa: E402
    MAX_THRESHOLD,
    MIN_THRESHOLD,
    clamp_threshold,
    normalize_mission_objectives,
)


class TestNormalizeObjectives(unittest.TestCase):
    def test_clamp_high_threshold_to_max(self):
        self.assertEqual(clamp_threshold(10), MAX_THRESHOLD)

    def test_clamp_low_threshold_to_min(self):
        self.assertEqual(clamp_threshold(1), MIN_THRESHOLD)

    def test_clamp_keeps_value_in_range(self):
        self.assertEqual(clamp_threshold(4), 4)

    def test_clamp_non_numeric_defaults_to_min(self):
        self.assertEqual(clamp_threshold("oops"), MIN_THRESHOLD)

    def test_normalize_reindexes_strictly_1_based(self):
        objectives = [
            {"stage": 7, "name": "C", "description": "c", "success_threshold": 4},
            {"stage": 2, "name": "A", "description": "a", "success_threshold": 4},
            {"stage": 5, "name": "B", "description": "b", "success_threshold": 4},
        ]
        result = normalize_mission_objectives(objectives)
        self.assertEqual([o["stage"] for o in result], [1, 2, 3])
        self.assertEqual([o["name"] for o in result], ["A", "B", "C"])

    def test_normalize_clamps_thresholds(self):
        objectives = [
            {"name": "A", "success_threshold": 1},
            {"name": "B", "success_threshold": 99},
        ]
        result = normalize_mission_objectives(objectives)
        self.assertEqual(result[0]["success_threshold"], MIN_THRESHOLD)
        self.assertEqual(result[1]["success_threshold"], MAX_THRESHOLD)

    def test_normalize_does_not_mutate_input(self):
        objectives = [{"stage": 1, "name": "A", "success_threshold": 4}]
        normalize_mission_objectives(objectives)
        self.assertEqual(objectives[0]["stage"], 1)


from game_rules import (  # noqa: E402
    apply_mission_progress,
    normalize_mission,
)


def _mission(stages, progress=None):
    """Build a normalized mission with given (name, threshold) stages."""
    objectives = [{"stage": i + 1, "name": n, "description": "", "success_threshold": t} for i, (n, t) in enumerate(stages)]
    return normalize_mission({"objectives": objectives, "stage_progress": progress or {}})


class TestApplyMissionProgress(unittest.TestCase):
    def test_progress_accumulates_to_completion(self):
        m = _mission([("A", 3), ("B", 3)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 2}])
        self.assertFalse(m["completed"])
        self.assertEqual(m["current_stage"], 1)
        m = apply_mission_progress(m, [{"stage": 1, "points": 2}])  # stage1 = 4 >= 3
        self.assertEqual(m["stage_progress"]["1"], 4)
        self.assertFalse(m["completed"])
        self.assertEqual(m["current_stage"], 2)
        m = apply_mission_progress(m, [{"stage": 2, "points": 3}])
        self.assertTrue(m["completed"])

    def test_off_by_one_fixed_current_stage_is_1(self):
        """Spec defect B: current_stage must not stay at 0."""
        m = _mission([("A", 3)])
        self.assertEqual(m["current_stage"], 1)

    def test_no_premature_completion(self):
        """Spec defect C: completing stage N-1 must NOT mark mission complete."""
        m = _mission([("A", 3), ("B", 3), ("C", 3)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 5}])
        m = apply_mission_progress(m, [{"stage": 2, "points": 5}])
        # stage 3 not yet reached -> not complete
        self.assertFalse(m["completed"])
        self.assertEqual(m["current_stage"], 3)

    def test_regression_capped_to_minus_one(self):
        m = _mission([("A", 5)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 4}])
        self.assertEqual(m["stage_progress"]["1"], 4)
        m = apply_mission_progress(m, [{"stage": 1, "points": -9}])
        # cap at -1 -> 4 - 1 = 3 (not 4 - 9 = 0 via floor; regression is bounded)
        self.assertEqual(m["stage_progress"]["1"], 3)

    def test_completed_stage_does_not_rollback(self):
        m = _mission([("A", 3), ("B", 3)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 5}])  # stage1 = 5 >= 3
        self.assertEqual(m["stage_progress"]["1"], 5)
        m = apply_mission_progress(m, [{"stage": 1, "points": -1}])
        # completed stage must not drop below threshold
        self.assertEqual(m["stage_progress"]["1"], 5)

    def test_empty_turn_leaves_stage_progress_unchanged(self):
        """A turn with no mission progress deltas moves nothing (no tempo floor)."""
        m = _mission([("A", 5)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 2}])
        self.assertEqual(m["stage_progress"]["1"], 2)
        m = apply_mission_progress(m, [{"stage": 1, "points": 0}])  # zero-points entry
        self.assertEqual(m["stage_progress"]["1"], 2)
        m = apply_mission_progress(m, [])  # empty mission_progress list
        self.assertEqual(m["stage_progress"]["1"], 2)
        m = apply_mission_progress(m, None)  # missing mission_progress
        self.assertEqual(m["stage_progress"]["1"], 2)
        self.assertFalse(m["completed"])
        self.assertEqual(m["current_stage"], 1)

    def test_ignores_unknown_stage_and_bad_points(self):
        m = _mission([("A", 3)])
        m = apply_mission_progress(
            m,
            [{"stage": 99, "points": 5}, {"stage": 1, "points": "bad"}, {}],
        )
        # nothing applicable -> stage 1 stays at 0
        self.assertEqual(m["stage_progress"]["1"], 0)


from unittest.mock import AsyncMock, patch  # noqa: E402

from game_server import GameServer  # noqa: E402


class TestGenerateMissionNormalization(unittest.IsolatedAsyncioTestCase):
    def _fake_llm_result(self):
        return {
            "name": "Echo Protocol",
            "description": "A test mission.",
            "objectives": [
                {"stage": 3, "name": "C", "description": "c", "success_threshold": 1},
                {"stage": 1, "name": "A", "description": "a", "success_threshold": 99},
                {"stage": 2, "name": "B", "description": "b", "success_threshold": 4},
            ],
        }

    async def test_generate_mission_normalizes_objectives_and_stages(self):

        agent = GameServer(language="en")
        agent.vs_enabled = False
        with patch.object(GameServer, "_call_llm", new=AsyncMock(return_value=self._fake_llm_result())):
            result = await agent.generate_mission(game_id=None, player_id=None, turn=None, kind=None)
        self.assertEqual([o["stage"] for o in result["objectives"]], [1, 2, 3])
        self.assertEqual([o["name"] for o in result["objectives"]], ["A", "B", "C"])
        for o in result["objectives"]:
            self.assertGreaterEqual(o["success_threshold"], MIN_THRESHOLD)
            self.assertLessEqual(o["success_threshold"], MAX_THRESHOLD)
        self.assertEqual(result["current_stage"], 1)
        self.assertEqual(result["total_stages"], 3)
        self.assertFalse(result["completed"])


class TestGameOverFallback(unittest.IsolatedAsyncioTestCase):
    """Regression: a victory whose LLM finale call failed used to return the
    defeat fallback narrative ("Корабль погиб...") because the caller passed
    the localized header as `outcome_type`, so `fallback_{header}` never
    matched and `.get(key, fallback_defeat)` silently returned the defeat
    text. Every verdict token must map to its own fallback."""

    async def _run_failing_finale(self, language, outcome_type, outcome_label):
        agent = GameServer(language=language)
        agent.vs_enabled = False
        with patch.object(GameServer, "_call_llm", new=AsyncMock(side_effect=RuntimeError("boom"))):
            return await agent.generate_game_over_outcome(
                outcome_type=outcome_type,
                outcome_narrative="narrative",
                mission_summary="summary",
                game_id=None,
                player_id=None,
                turn=None,
                kind=None,
                outcome_label=outcome_label,
                end_reason="Причина конца: миссия выполнена",
                hull=50,
                shields=40,
                threat=30,
                dead_crew_count=1,
                alive_crew_count=4,
                turns_played=7,
            )

    async def test_victory_fallback_not_defeat_when_llm_fails(self):
        result = await self._run_failing_finale("ru", "victory", "🏆 МИССИЯ ВЫПОЛНЕНА — ПОБЕДА!")
        self.assertIn("Миссия выполнена", result["finale_narrative"])
        self.assertNotIn("Корабль погиб", result["finale_narrative"])

    async def test_pyrrhic_fallback_not_defeat_when_llm_fails(self):
        result = await self._run_failing_finale("ru", "pyrrhic", "🔥 МИССИЯ ВЫПОЛНЕНА ЛЮБОЙ ЦЕНОЙ — ПИРРОВА ПОБЕДА")
        self.assertIn("Миссия выполнена", result["finale_narrative"])
        self.assertNotIn("Корабль погиб", result["finale_narrative"])

    async def test_defeat_fallback_when_llm_fails(self):
        result = await self._run_failing_finale("ru", "defeat", "💀 КОРАБЛЬ УНИЧТОЖЕН — ПОРАЖЕНИЕ")
        self.assertIn("Корабль погиб", result["finale_narrative"])

    async def test_victory_fallback_english(self):
        result = await self._run_failing_finale("en", "victory", "🏆 MISSION ACCOMPLISHED — VICTORY!")
        self.assertIn("mission", result["finale_narrative"].lower())
        self.assertNotIn("perished", result["finale_narrative"].lower())

    async def test_outcome_label_reaches_llm_prompt_not_the_token(self):
        agent = GameServer(language="ru")
        agent.vs_enabled = False
        captured = {}

        async def _capture(system_prompt, user_prompt, **kwargs):
            captured["user"] = user_prompt
            return {"finale_narrative": "ok", "finale_image_prompt": "ok"}

        with patch.object(GameServer, "_call_llm", side_effect=_capture):
            await agent.generate_game_over_outcome(
                outcome_type="victory",
                outcome_narrative="narrative",
                mission_summary="summary",
                game_id=None,
                player_id=None,
                turn=None,
                kind=None,
                outcome_label="🏆 МИССИЯ ВЫПОЛНЕНА — ПОБЕДА!",
                end_reason="Причина конца: миссия выполнена",
                hull=80,
                shields=60,
                threat=20,
                dead_crew_count=0,
                alive_crew_count=5,
                turns_played=9,
            )
        self.assertIn("🏆 МИССИЯ ВЫПОЛНЕНА — ПОБЕДА!", captured["user"])
        self.assertNotIn("Исход игры: victory", captured["user"])

    async def test_prompt_carries_verdict_token_and_facts_not_decided_by_llm(self):
        agent = GameServer(language="ru")
        agent.vs_enabled = False
        captured = {}

        async def _capture(system_prompt, user_prompt, **kwargs):
            captured["user"] = user_prompt
            return {"finale_narrative": "ok", "finale_image_prompt": "ok"}

        with patch.object(GameServer, "_call_llm", side_effect=_capture):
            await agent.generate_game_over_outcome(
                outcome_type="pyrrhic",
                outcome_narrative="narrative",
                mission_summary="✓ Этап 1: Прорыв (3/3)",
                game_id=None,
                player_id=None,
                turn=None,
                kind=None,
                outcome_label="🔥 МИССИЯ ВЫПОЛНЕНА ЛЮБОЙ ЦЕНОЙ — ПИРРОВА ПОБЕДА",
                end_reason="Причина конца: миссия выполнена",
                hull=0,
                shields=0,
                threat=95,
                dead_crew_count=5,
                alive_crew_count=0,
                turns_played=12,
            )
        self.assertIn("Вердикт правил: pyrrhic", captured["user"])
        self.assertIn("НЕ решаешь исход", captured["user"])
        self.assertIn("Корпус: 0/100", captured["user"])
        self.assertIn("угроза: 95/100", captured["user"])
        self.assertIn("выживших 0, погибших 5", captured["user"])
        self.assertIn("Ходов сыграно: 12", captured["user"])



import random as _random  # noqa: E402

from game_rules import (  # noqa: E402
    FORBIDDEN_OPENINGS,
    MISSION_ARCHETYPES,
    SEED_TABLES,
    select_mission_seeds,
)


class TestMissionSeeds(unittest.TestCase):
    def test_select_returns_archetype_and_all_seed_tables(self):
        rng = _random.Random(42)
        result = select_mission_seeds(language="en", rng=rng)
        self.assertIn(result["archetype"], MISSION_ARCHETYPES)
        self.assertEqual(set(result["seeds"].keys()), set(SEED_TABLES.keys()))

    def test_select_is_deterministic_with_seed(self):
        r1 = select_mission_seeds(language="en", rng=_random.Random(123))
        r2 = select_mission_seeds(language="en", rng=_random.Random(123))
        self.assertEqual(r1, r2)

    def test_ru_and_en_tables_have_matching_keys(self):
        for table, opts in SEED_TABLES.items():
            self.assertIn("ru", opts)
            self.assertIn("en", opts)
            self.assertGreaterEqual(len(opts["ru"]), 4)
            self.assertEqual(len(opts["ru"]), len(opts["en"]))
        self.assertIn("ru", FORBIDDEN_OPENINGS)
        self.assertIn("en", FORBIDDEN_OPENINGS)

    def test_all_archetypes_have_both_languages(self):
        for key, val in MISSION_ARCHETYPES.items():
            self.assertIn("ru", val)
            self.assertIn("en", val)


from prompts import build_mission_prompts  # noqa: E402


class TestMissionPromptInjection(unittest.TestCase):
    def test_prompt_includes_archetype_and_seeds(self):
        seeds = select_mission_seeds(language="en", rng=_random.Random(7))
        system, user = build_mission_prompts("en", archetype=seeds["archetype"], seeds=seeds["seeds"], use_vs=True, vs_k=5)
        self.assertIn(seeds["archetype"], system + user)
        for value in seeds["seeds"].values():
            self.assertIn(value, system + user)

    def test_prompt_lists_forbidden_openings_and_threshold_range(self):
        _, user = build_mission_prompts("ru", archetype=None, seeds=None, use_vs=True, vs_k=5)
        self.assertIn("3-5", user)
        self.assertIn("сигнал", user)  # forbidden list mentions the banned trope


from game_rules import NPC_COUNT, select_npc_role_keys  # noqa: E402


class TestSelectNpcRoleKeys(unittest.TestCase):
    """The NPC pool at game start is capped at NPC_COUNT seats, preferring the
    key roles — a bounded pool is what makes crew_wiped reachable."""

    ALL_ROLES = [
        "captain",
        "chief_engineer",
        "science_officer",
        "communications_officer",
        "security_chief",
        "navigator",
        "medical_officer",
        "tactical_officer",
        "xenobiologist",
        "pilot",
    ]

    def test_exactly_npc_count_roles_for_few_players(self):
        # 2 players → 8 unfilled roles, but only NPC_COUNT seats become NPCs.
        picked = select_npc_role_keys(self.ALL_ROLES[2:])
        self.assertEqual(len(picked), NPC_COUNT)

    def test_prefers_key_roles_in_priority_order(self):
        picked = select_npc_role_keys(self.ALL_ROLES)
        self.assertEqual(picked, ["chief_engineer", "medical_officer", "pilot", "science_officer"])

    def test_fills_from_canonical_order_when_key_roles_taken(self):
        # Players hold medical_officer and pilot → the remaining key roles are
        # still preferred, then the first unfilled non-key roles in canonical
        # order fill the rest.
        available = [r for r in self.ALL_ROLES if r not in ("medical_officer", "pilot")]
        picked = select_npc_role_keys(available)
        self.assertEqual(picked, ["chief_engineer", "science_officer", "captain", "communications_officer"])

    def test_fewer_npcs_when_roles_nearly_full(self):
        # 7+ players: fewer than NPC_COUNT seats left → only those get NPCs.
        picked = select_npc_role_keys(["tactical_officer", "xenobiologist"])
        self.assertEqual(picked, ["tactical_officer", "xenobiologist"])

    def test_empty_available_roles(self):
        self.assertEqual(select_npc_role_keys([]), [])


if __name__ == "__main__":
    unittest.main()
