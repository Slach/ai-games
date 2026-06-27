# Game Dynamics & Mission Completion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make missions actually completable and the game dynamic/non-repetitive, by introducing a deterministic rules layer between LLM output and the database.

**Architecture:** All balance decisions (progress deltas, deaths, damage) are currently made by a single LLM call with zero mechanical guardrails. This plan adds a pure, unit-tested module `game_rules.py` that sits between the LLM's raw `combined_outcome` and the DB write: it normalizes mission objectives, accumulates progress with regression caps + a tempo floor, computes completion from real thresholds, rate-limits crew deaths, and selects mission archetype/seeds deterministically. The LLM keeps its creative role (narrative, twists); the engine guarantees fairness.

**Tech Stack:** Python 3, FastAPI backend, SQLite (`database.py`), OpenAI SDK (`game_master.py`), `unittest` for tests (matches existing `tests/test_comfyui.py`).

**Spec:** `docs/superpowers/specs/2026-06-27-game-dynamics-and-mission-completion-design.md`

**Scope:** Phases P0 (completion bug), P1 (progress caps + tempo), P2 (archetype variety), P3 (death rate-limits + prompt rework). **P4 (full HP/injury system) is explicitly deferred** per spec §8 — out of scope for this plan.

## Global Constraints

(From `AGENTS.md` + spec — every task implicitly includes these.)

- **LLM prompts live only in `prompts.py`.** Never embed prompt strings in handlers or other modules.
- **Locale checks use `LANGUAGE_RU`/`LANGUAGE_EN` constants** (imported from `language.py`), never raw `== 'ru'`/`== 'en'`.
- **Schema changes only via new entries in the `MIGRATIONS` list** in `database.py` — never edit existing `CREATE TABLE` statements. Current last migration version is **5**; new ones start at **6**.
- **Use real UTF-8 characters** in source (Russian text), never `\uXXXX` escapes.
- **All imports at top of file.** No local/conditional imports.
- **No `contextlib.suppress`.** Use explicit `try/except` with logging at boundaries.
- **Fix causes, not symptoms.** No `_clean_*`/`_sanitize_*` shims over upstream output — fix at the source (here, the rules layer enforces the contract).
- Tests follow the existing `unittest` style in `game-server-api/tests/test_comfyui.py` (with `sys.path.insert` for imports). Run from `game-server-api/` dir.
- `game_rules.py` is **pure** (only `import random` + stdlib typing) — no DB, no LLM, no logging, no imports from this project — to keep it unit-testable and cycle-free.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `game-server-api/game_rules.py` | **Create** | Pure rules layer: objective normalization, progress accumulation (caps/floor/completion), death rate-limiting, archetype/seed selection. |
| `game-server-api/tests/test_game_rules.py` | **Create** | Unit tests for `game_rules.py` (pure, fast). |
| `game-server-api/tests/test_mission_db.py` | **Create** | DB-level tests (temp DB) for mission persistence/normalization. |
| `game-server-api/database.py` | Modify | Migrations 6+7; `create_mission` derives `total_stages`; `get_mission` normalizes on read (fixes existing stuck data); `get_game_state`/new `set_last_death_day` for death cooldown; persist `archetype`/`seeds`. |
| `game-server-api/game_master.py` | Modify | `generate_mission` normalizes objectives + sets `current_stage`/`total_stages`, selects & stores seeds; import `game_rules`. |
| `game-server-api/main.py` | Modify | `_analyze_day_outcome` uses `apply_mission_progress` and `apply_death_limits` instead of the broken inline logic. |
| `game-server-api/prompts.py` | Modify | `build_mission_prompts` injects archetype/seeds + forbidden openings; combined-outcome prompts reworked (P3); `build_personal_briefing_system` guarantees a defensive action. |

**Import graph (cycle-free):** `game_rules` → stdlib only. `database` → `game_rules`. `game_master` → `database`, `game_rules`, `prompts`, `language`. `main` → `database`, `game_rules`, `game_master`, `prompts`. No module imports `main`.

---

## Task 1: Rules layer — normalize mission objectives

**Files:**

- Create: `game-server-api/game_rules.py`
- Create: `game-server-api/tests/test_game_rules.py`

**Interfaces:**

- Produces: `clamp_threshold(value: int) -> int`, `normalize_mission_objectives(objectives: list[dict]) -> list[dict]`, `MIN_THRESHOLD`, `MAX_THRESHOLD`.

- [ ] **Step 1: Write the failing test**

Create `game-server-api/tests/test_game_rules.py`:

```python
"""Unit tests for the game-rules layer (pure functions, no DB/LLM)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_rules import (
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd game-server-api && python -m unittest tests.test_game_rules -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'game_rules'`.

- [ ] **Step 3: Write minimal implementation**

Create `game-server-api/game_rules.py`:

```python
"""Deterministic game-rules layer between LLM output and the database.

The LLM proposes narrative deltas (mission_progress, deaths, injuries, ...).
Functions in this module enforce fairness: mission objectives are normalized,
progress is accumulated with regression caps and a tempo floor, mission
completion is computed from real thresholds, crew deaths are rate-limited, and
mission archetype/seeds are selected deterministically.

Pure functions only: no DB, no LLM, no logging. Easy to unit test.
"""

import random
from typing import Any

# ── Mission objective normalization ────────────────────────────────

MIN_THRESHOLD = 3
MAX_THRESHOLD = 5


def clamp_threshold(value: Any) -> int:
    """Clamp a stage's success_threshold into the balanced [MIN, MAX] range."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = MIN_THRESHOLD
    return max(MIN_THRESHOLD, min(MAX_THRESHOLD, v))


def normalize_mission_objectives(objectives: list[dict]) -> list[dict]:
    """Sort stages by their stage number, re-index strictly 1-based, clamp thresholds.

    Returns a new list; the input is not mutated. Entries without a name are
    dropped. Stable sort preserves original order for equal/missing stage numbers.
    """
    valid = [o for o in objectives if o.get("name")]
    indexed = sorted(enumerate(valid), key=lambda iv: (iv[1].get("stage", 0), iv[0]))
    result: list[dict] = []
    for _, o in indexed:
        result.append(
            {
                "stage": len(result) + 1,
                "name": o["name"],
                "description": o.get("description", ""),
                "success_threshold": clamp_threshold(o.get("success_threshold", MIN_THRESHOLD)),
            }
        )
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd game-server-api && python -m unittest tests.test_game_rules -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add game-server-api/game_rules.py game-server-api/tests/test_game_rules.py
git commit -m "feat(rules): add mission objective normalization (P0)"
```

---

## Task 2: Rules layer — progress accumulation, completion, regression caps, tempo floor

**Files:**

- Modify: `game-server-api/game_rules.py`
- Modify: `game-server-api/tests/test_game_rules.py`

**Interfaces:**

- Consumes: `normalize_mission_objectives`, `clamp_threshold` (Task 1).
- Produces: `normalize_mission(mission: dict) -> dict`, `apply_mission_progress(mission: dict, progress_entries: list[dict]) -> dict`. The returned dict carries keys `objectives`, `stage_progress` (dict[str,int]), `current_stage` (int, 1-based index of first incomplete stage, or total_stages+1 when complete), `total_stages` (int), `completed` (bool).

- [ ] **Step 1: Write the failing tests**

Append to `game-server-api/tests/test_game_rules.py` (before the `if __name__` guard):

```python
from game_rules import apply_mission_progress, normalize_mission


def _mission(stages, progress=None):
    """Build a normalized mission with given (name, threshold) stages."""
    objectives = [
        {"stage": i + 1, "name": n, "description": "", "success_threshold": t}
        for i, (n, t) in enumerate(stages)
    ]
    return normalize_mission(
        {"objectives": objectives, "stage_progress": progress or {}}
    )


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

    def test_tempo_floor_advances_current_stage_by_one(self):
        """A turn with no positive progress on the current stage still nudges +1."""
        m = _mission([("A", 5)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 2}])
        self.assertEqual(m["stage_progress"]["1"], 2)
        m = apply_mission_progress(m, [{"stage": 1, "points": 0}])  # no advance proposed
        self.assertEqual(m["stage_progress"]["1"], 3)

    def test_ignores_unknown_stage_and_bad_points(self):
        m = _mission([("A", 3)])
        m = apply_mission_progress(
            m,
            [{"stage": 99, "points": 5}, {"stage": 1, "points": "bad"}, {}],
        )
        # tempo floor still applies to stage 1 -> 1
        self.assertEqual(m["stage_progress"]["1"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd game-server-api && python -m unittest tests.test_game_rules -v`
Expected: FAIL — `ImportError: cannot import name 'apply_mission_progress'`.

- [ ] **Step 3: Write minimal implementation**

Append to `game-server-api/game_rules.py` (after `normalize_mission_objectives`):

```python
# ── Mission state computation ──────────────────────────────────────

MAX_REGRESSION = 1  # max points a single turn can subtract from one stage


def normalize_mission(mission: dict) -> dict:
    """Return a normalized copy of a mission dict.

    - objectives normalized (1-based stage numbers, thresholds clamped to [3,5])
    - stage_progress coerced to int, keyed by str(stage)
    - current_stage / total_stages / completed computed from real thresholds

    Fixes existing missions that were created with current_stage=0 / total_stages=1
    (spec defect A) simply by being read through this function.
    """
    objectives = normalize_mission_objectives(mission.get("objectives", []))
    total_stages = len(objectives)
    raw_sp = mission.get("stage_progress", {}) or {}
    stage_progress: dict[str, int] = {}
    for o in objectives:
        key = str(o["stage"])
        try:
            stage_progress[key] = int(raw_sp.get(key, raw_sp.get(o["stage"], 0)))
        except (TypeError, ValueError):
            stage_progress[key] = 0
    current_stage, completed = _compute_stage_state(objectives, stage_progress)
    result = dict(mission)
    result["objectives"] = objectives
    result["stage_progress"] = stage_progress
    result["total_stages"] = total_stages
    result["current_stage"] = current_stage
    result["completed"] = completed
    return result


def _compute_stage_state(
    objectives: list[dict], stage_progress: dict[str, int]
) -> tuple[int, bool]:
    """Return (current_stage, completed).

    current_stage = number of the first not-yet-completed stage (1-based),
    or total_stages + 1 when all stages reached their threshold.
    completed = whether ALL stages reached their threshold.
    """
    for o in objectives:
        if stage_progress.get(str(o["stage"]), 0) < o["success_threshold"]:
            return o["stage"], False
    return len(objectives) + 1, True


def apply_mission_progress(
    mission: dict, progress_entries: list[dict] | None
) -> dict:
    """Apply one turn's mission_progress deltas under the rules layer.

    Rules (spec P0 + P1):
    - objectives normalized (thresholds 3-5, 1-based).
    - regression capped at -MAX_REGRESSION per entry.
    - already-completed stages do not roll back below their threshold.
    - tempo floor: the current working stage advances at least +1 per turn
      (the crew makes incremental progress even on uneventful turns).

    Returns a NEW normalized mission dict. Input is not mutated.
    """
    norm = normalize_mission(mission)
    objectives = norm["objectives"]
    stage_progress = dict(norm["stage_progress"])
    total_stages = norm["total_stages"]

    working_stage, _ = _compute_stage_state(objectives, stage_progress)
    advanced_working = False

    threshold_by_stage = {o["stage"]: o["success_threshold"] for o in objectives}

    for entry in progress_entries or []:
        if not isinstance(entry, dict):
            continue
        stage_num = entry.get("stage")
        if stage_num is None:
            continue
        try:
            stage_num = int(stage_num)
        except (TypeError, ValueError):
            continue
        threshold = threshold_by_stage.get(stage_num)
        if threshold is None:
            continue  # ignore unknown stages
        try:
            points = int(entry.get("points", 0))
        except (TypeError, ValueError):
            continue
        if points < 0:
            points = max(points, -MAX_REGRESSION)  # P1: cap regression
        key = str(stage_num)
        old = stage_progress.get(key, 0)
        new = max(0, old + points)
        if old >= threshold and new < threshold:
            new = threshold  # P1: no rollback of completed stages
        stage_progress[key] = new
        if points > 0 and stage_num == working_stage:
            advanced_working = True

    # P1: tempo floor on the working stage
    if 1 <= working_stage <= total_stages:
        key = str(working_stage)
        threshold = threshold_by_stage[working_stage]
        if not advanced_working and stage_progress.get(key, 0) < threshold:
            stage_progress[key] = stage_progress.get(key, 0) + 1

    current_stage, completed = _compute_stage_state(objectives, stage_progress)
    norm["stage_progress"] = stage_progress
    norm["current_stage"] = current_stage
    norm["completed"] = completed
    return norm
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd game-server-api && python -m unittest tests.test_game_rules -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add game-server-api/game_rules.py game-server-api/tests/test_game_rules.py
git commit -m "feat(rules): progress accumulation, completion-from-thresholds, regression caps, tempo floor (P0+P1)"
```

---

## Task 3: generate_mission — normalize objectives, set current/total stages

**Files:**

- Modify: `game-server-api/game_master.py` (imports ~L14-22; `generate_mission` L2372-2406; `MISSION_SCHEMA` L600-650)
- Modify: `game-server-api/tests/test_game_rules.py` (add a test using a mocked `_call_llm`)

**Interfaces:**

- Consumes: `normalize_mission` from `game_rules` (Task 1/2).
- Produces: `GameMasterAgent.generate_mission` now always returns a dict whose `objectives` are 1-based with thresholds 3-5, and which carries `current_stage=1`, `total_stages=len(objectives)`, `completed=False`.

- [ ] **Step 1: Write the failing test**

Append to `game-server-api/tests/test_game_rules.py`:

```python
from unittest.mock import patch

from game_master import GameMasterAgent


class TestGenerateMissionNormalization(unittest.TestCase):
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

    def test_generate_mission_normalizes_objectives_and_stages(self):
        agent = GameMasterAgent(language="en")
        with patch.object(
            GameMasterAgent, "_call_llm", return_value=self._fake_llm_result()
        ):
            result = agent.generate_mission([{"role": "Pilot", "type": "player"}])
        self.assertEqual([o["stage"] for o in result["objectives"]], [1, 2, 3])
        self.assertEqual([o["name"] for o in result["objectives"]], ["A", "B", "C"])
        for o in result["objectives"]:
            self.assertGreaterEqual(o["success_threshold"], MIN_THRESHOLD)
            self.assertLessEqual(o["success_threshold"], MAX_THRESHOLD)
        self.assertEqual(result["current_stage"], 1)
        self.assertEqual(result["total_stages"], 3)
        self.assertFalse(result["completed"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd game-server-api && python -m unittest tests.test_game_rules.TestGenerateMissionNormalization -v`
Expected: FAIL — `KeyError: 'current_stage'` or assertion error (current `generate_mission` returns raw LLM result without `current_stage`/`total_stages`).

- [ ] **Step 3: Write minimal implementation**

In `game-server-api/game_master.py`, add `game_rules` to the existing imports near the top (after the `from database import SHIP_ROLE_KEYS` line, ~L14):

```python
from game_rules import normalize_mission
```

Then replace the body of `generate_mission` (L2372-2406). Replace the whole method with:

```python
    def generate_mission(self, all_participants: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate a mission with stages/objectives for the game.

        Objectives are normalized (1-based, thresholds 3-5) and the mission
        carries current_stage=1 / total_stages=len(objectives) so it is
        completable from the start (spec defect A fix).
        """
        logger.info(f"[MISSION] Generating mission for {len(all_participants)} participants")

        crew_desc = "\n".join([f"  - {p.get('role', '?')} ({p.get('type', '?')})" for p in all_participants])

        system, user = build_mission_prompts(self.language, crew_desc)

        try:
            result = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=MISSION_SCHEMA,
                max_tokens=4096,
                temperature=0.8,
            )
        except Exception as e:
            logger.error(f"[MISSION] Generation failed: {e}")
            gs = get_game_strings(self.language)
            mf = gs["gm_fallback"]["mission_fallback"]
            result = {
                "name": mf["name"],
                "description": mf["description"],
                "objectives": [
                    {"stage": i + 1, "name": s["name"], "description": s["description"], "success_threshold": [3, 5, 7][i]}
                    for i, s in enumerate(mf["stages"])
                ],
            }

        # normalize: 1-based stages, thresholds 3-5, derive current/total/completed
        result = normalize_mission(result)
        logger.info(f"[MISSION] Generated: {result.get('name', '')} ({result['total_stages']} stages)")
        return result
```

This is the complete method: the `try/except` builds `result` (either from the LLM or the fallback), then the final three statements normalize it and return. Replace the entire current method body with exactly the code above.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd game-server-api && python -m unittest tests.test_game_rules.TestGenerateMissionNormalization -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add game-server-api/game_master.py game-server-api/tests/test_game_rules.py
git commit -m "feat(mission): normalize generated objectives and set completable stages (P0 defect A)"
```

---

## Task 4: database — derive total_stages on write, normalize on read (fixes existing stuck data)

**Files:**

- Modify: `game-server-api/database.py` (`create_mission` L1744-1768; `get_mission` L1769-1797; imports)
- Create: `game-server-api/tests/test_mission_db.py`

**Interfaces:**

- Consumes: `normalize_mission` from `game_rules` (Task 1/2).
- Produces: `create_mission` stores a `total_stages` derived from `len(objectives)` (never the stale default 1). `get_mission` returns a fully normalized dict, so the existing stuck mission (`current_stage=0, total_stages=1`) is repaired on next read.

- [ ] **Step 1: Write the failing test**

Create `game-server-api/tests/test_mission_db.py`:

```python
"""DB-level tests for mission persistence and read-time normalization."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db


class TestMissionPersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        os.unlink(self._tmp.name)

    def _raw_mission(self):
        return {
            "name": "Test",
            "description": "d",
            "objectives": [
                {"stage": 1, "name": "A", "description": "a", "success_threshold": 3},
                {"stage": 2, "name": "B", "description": "b", "success_threshold": 3},
            ],
        }

    def test_create_derives_total_stages_from_objectives(self):
        result = db.create_mission(self._raw_mission(), "g1")
        self.assertEqual(result["total_stages"], 2)
        self.assertEqual(result["current_stage"], 1)
        self.assertFalse(result["completed"])

    def test_get_mission_normalizes_stale_row(self):
        # Simulate the legacy bug: write a row with stale current/total,
        # then confirm get_mission repairs it via normalize_mission.
        raw = self._raw_mission()
        raw["current_stage"] = 0
        raw["total_stages"] = 1
        raw["stage_progress"] = {"1": 5, "2": 5}
        db.create_mission(raw, "g2")
        got = db.get_mission(None, "g2")
        self.assertEqual(got["total_stages"], 2)
        self.assertEqual(got["current_stage"], 3)  # both stages >= threshold
        self.assertTrue(got["completed"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd game-server-api && python -m unittest tests.test_mission_db -v`
Expected: FAIL — `create_mission` stores `total_stages=1`; `get_mission` returns `total_stages=1` / `current_stage=0` / `completed=False`.

- [ ] **Step 3: Write minimal implementation**

In `game-server-api/database.py`, add near the top imports (after the existing `from typing import ...` / stdlib imports, before any project import to avoid cycles — `game_rules` imports nothing from this project):

```python
from game_rules import normalize_mission
```

In `create_mission` (L1744), replace the INSERT argument list so `total_stages` is derived. Replace:

```python
            mission_data.get("current_stage", 0),
            mission_data.get("total_stages", 1),
```

with:

```python
            mission_data.get("current_stage", 1),
            mission_data.get("total_stages") or len(mission_data.get("objectives", []) or []) or 1,
```

In `get_mission` (L1769-1797), replace the final `return { ... }` block with a normalized return. Replace the whole returned-dict construction:

```python
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
```

with:

```python
    return normalize_mission(
        {
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
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd game-server-api && python -m unittest tests.test_mission_db -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add game-server-api/database.py game-server-api/tests/test_mission_db.py
git commit -m "fix(db): derive total_stages on write, normalize mission on read (P0 defects A/B/C, repairs existing data)"
```

---

## Task 5: main — wire apply_mission_progress into _analyze_day_outcome

**Files:**

- Modify: `game-server-api/main.py` (imports L17-92; `_analyze_day_outcome` outcome block L2485-2535)

**Interfaces:**

- Consumes: `apply_mission_progress` from `game_rules` (Task 2), `update_mission_stage_progress` from `database`.
- Produces: `_analyze_day_outcome` no longer uses the broken inline `current_stage`/`completed` logic; mission completion now actually triggers.

- [ ] **Step 1: Confirm the broken block is present**

Run: `cd game-server-api && grep -n "if points_int > 0 and stage_num == current_stage" main.py`
Expected: a single match around L2517 — confirms the defect-B line still exists.

- [ ] **Step 2: Write the implementation**

In `game-server-api/main.py`, add a top-level import for the rules layer (place it right after the existing `from database import (...)` block, around L17-92). Note: `apply_mission_progress` comes from `game_rules`, **not** `database`:

```python
from game_rules import apply_mission_progress
```

In `_analyze_day_outcome`, replace the entire progress-application block. Find this exact block (L2487-2535) and replace it:

```python
        # Update mission progress if provided (new array format: [{stage, points}])
        mission_progress = outcome.get("mission_progress", [])
        if mission_progress and mission:
            stage_progress = mission.get("stage_progress", {})
            current_stage = mission.get("current_stage", 0)
            total_stages = mission.get("total_stages", 1)

            for entry in mission_progress:
                stage_num = entry.get("stage")
                points = entry.get("points", 0)

                if stage_num is None:
                    logger.info(f"[MISSION] Skipping entry without stage: {entry}")
                    continue

                try:
                    points_int = int(points)
                except (ValueError, TypeError):
                    logger.warning(f"[MISSION] Skipping non-integer points: {points}")
                    continue

                stage_key = str(stage_num)
                old_progress = stage_progress.get(stage_key, 0)
                new_progress = max(0, old_progress + points_int)
                stage_progress[stage_key] = new_progress

                log_direction = "advance" if points_int > 0 else "setback" if points_int < 0 else "neutral"
                logger.info(f"[MISSION] Stage {stage_num}: {old_progress} -> {new_progress} ({log_direction}, delta={points_int})")

                # Check if current stage is now completed (only on positive progress)
                if points_int > 0 and stage_num == current_stage:
                    for obj in mission.get("objectives", []):
                        if obj.get("stage") == stage_num:
                            threshold = obj.get("success_threshold", 5)
                            if new_progress >= threshold:
                                current_stage = min(current_stage + 1, total_stages)
                                logger.info(f"[MISSION] Stage {stage_num} completed!")
                                break

            completed = current_stage >= total_stages
            update_mission_stage_progress(
                stage_progress,
                current_stage,
                game_id,
                completed,
            )

            if completed:
                logger.info("[MISSION] MISSION COMPLETE! Notifying players...")
```

Replace it with:

```python
        # Apply mission progress through the rules layer (P0+P1):
        # normalizes objectives, accumulates with regression caps + tempo floor,
        # and computes completion from real thresholds (fixes defect B/C).
        mission_progress = outcome.get("mission_progress", [])
        if mission:
            updated_mission = apply_mission_progress(mission, mission_progress)
            update_mission_stage_progress(
                updated_mission["stage_progress"],
                updated_mission["current_stage"],
                game_id=game_id,
                completed=updated_mission["completed"],
            )
            for stage_key, pts in updated_mission["stage_progress"].items():
                logger.info(f"[MISSION] Stage {stage_key} progress now {pts}")
            if updated_mission["completed"]:
                logger.info("[MISSION] MISSION COMPLETE! Notifying players...")
            mission = updated_mission
```

- [ ] **Step 3: Verify no other caller depends on the old behavior**

Run: `cd game-server-api && grep -n "current_stage >= total_stages\|stage_num == current_stage" main.py`
Expected: no matches (the broken logic is gone).

- [ ] **Step 4: Run the full test suite to confirm nothing broke**

Run: `cd game-server-api && python -m unittest discover -s tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add game-server-api/main.py
git commit -m "feat(main): route mission progress through rules layer (P0+P1 wiring)"
```

---

## Task 6: Rules layer — mission archetypes + seed tables + selector

**Files:**

- Modify: `game-server-api/game_rules.py`
- Modify: `game-server-api/tests/test_game_rules.py`

**Interfaces:**

- Produces: `MISSION_ARCHETYPES: dict[str, dict[str, str]]`, `SEED_TABLES: dict[str, dict[str, list[str]]]`, `FORBIDDEN_OPENINGS: dict[str, list[str]]`, `select_mission_seeds(language: str = "en", rng: random.Random | None = None) -> dict`. The returned dict has keys `archetype` (str key), `seeds` (dict[str,str]: one entry per seed table), `language`.

- [ ] **Step 1: Write the failing test**

Append to `game-server-api/tests/test_game_rules.py`:

```python
from game_rules import (
    FORBIDDEN_OPENINGS,
    MISSION_ARCHETYPES,
    SEED_TABLES,
    select_mission_seeds,
)
import random as _random


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd game-server-api && python -m unittest tests.test_game_rules.TestMissionSeeds -v`
Expected: FAIL — `ImportError: cannot import name 'MISSION_ARCHETYPES'`.

- [ ] **Step 3: Write minimal implementation**

Append to `game-server-api/game_rules.py`:

```python
# ── Mission archetype & seed selection (P2) ────────────────────────

MISSION_ARCHETYPES: dict[str, dict[str, str]] = {
    "first_contact": {
        "ru": "Первый контакт — дипломатия с неизвестной цивилизацией. Тон: осторожность, этика, языковой барьер.",
        "en": "First contact — diplomacy with an unknown civilization. Tone: caution, ethics, language barrier.",
    },
    "rescue": {
        "ru": "Спасательная операция — выжившие или пленники в опасной зоне. Тон: срочность, риск, мораль.",
        "en": "Rescue operation — survivors or captives in a hazardous zone. Tone: urgency, risk, morals.",
    },
    "survey": {
        "ru": "Научная разведка — сбор данных о планете/объекте. Тон: любопытство, методичность, открытия.",
        "en": "Scientific survey — gather data on a planet/object. Tone: curiosity, method, discovery.",
    },
    "mystery": {
        "ru": "Расследование тайны — необъяснимые события или преступление. Тон: интрига, улики, неопределённость.",
        "en": "Mystery investigation — unexplained events or a crime. Tone: intrigue, clues, uncertainty.",
    },
    "infiltration": {
        "ru": "Проникновение — скрытная операция на враждебной территории. Тон: стелс, обман, ставки.",
        "en": "Infiltration — covert op on hostile ground. Tone: stealth, deception, stakes.",
    },
    "defense": {
        "ru": "Оборона — защита объекта или эвакуация под угрозой. Тон: напряжение, тактика, жертвы.",
        "en": "Defense — protect an asset or evacuate under threat. Tone: tension, tactics, sacrifice.",
    },
    "intrigue": {
        "ru": "Политическая интрига — фракции, заговор, двойные интересы. Тон: переговоры, предательство, союзы.",
        "en": "Political intrigue — factions, conspiracy, double interests. Tone: negotiation, betrayal, alliances.",
    },
    "trade": {
        "ru": "Торговая миссия — сделка, обмен, дефицитный ресурс. Тон: выгода, репутация, торг.",
        "en": "Trade mission — a deal, exchange, scarce resource. Tone: profit, reputation, haggling.",
    },
    "anomaly": {
        "ru": "Изучение аномалии — пространственно-временной или энергетический феномен. Тон: чудо, опасность, парадокс.",
        "en": "Anomaly study — a spacetime or energy phenomenon. Tone: wonder, danger, paradox.",
    },
    "exploration": {
        "ru": "Исследование неизведанного — новый регион космоса. Тон: открытие, неизвестность, первопроходцы.",
        "en": "Deep exploration — an uncharted region. Tone: discovery, the unknown, pioneers.",
    },
}

SEED_TABLES: dict[str, dict[str, list[str]]] = {
    "setting": {
        "ru": [
            "поверхность негостеприимной планеты",
            "заброшенная орбитальная станция",
            "туманность с ионными бурями",
            "руины исчезнувшей цивилизации",
            "огромный космический дереликт",
            "зона у горизонта событий чёрной дыры",
            "верхние слои газового гиганта",
            "плотное астероидное поле",
        ],
        "en": [
            "surface of an inhospitable planet",
            "abandoned orbital station",
            "nebula swept by ion storms",
            "ruins of a vanished civilization",
            "a colossal space derelict",
            "the edge of a black hole's event horizon",
            "upper layers of a gas giant",
            "a dense asteroid field",
        ],
    },
    "complication": {
        "ru": [
            "разбушевавшееся природное явление",
            "взбунтовавшийся бортовой ИИ",
            "налётчики или пираты",
            "зараза на борту",
            "вмешательство враждебной фракции",
            "временная аномалия",
            "конкурирующая экспедиция",
            "внутренний раскол экипажа",
        ],
        "en": [
            "a raging natural phenomenon",
            "a shipboard AI gone rogue",
            "raiders or pirates",
            "an outbreak aboard",
            "interference from a hostile faction",
            "a temporal anomaly",
            "a rival expedition",
            "an internal crew schism",
        ],
    },
    "twist": {
        "ru": [
            "союзник оказывается предателем",
            "сигнал приходит из будущего",
            "объект миссии живой и разумен",
            "истинная цель отличается от заявленной",
            "награда несёт скрытую цену",
            "противник действует из благих побуждений",
            "карта местности была ложной",
            "экипаж не один на объекте",
        ],
        "en": [
            "an ally is the traitor",
            "the signal comes from the future",
            "the mission target is alive and sentient",
            "the true objective differs from the stated one",
            "the reward carries a hidden price",
            "the antagonist acts from noble motives",
            "the map was a decoy",
            "the crew is not alone at the site",
        ],
    },
    "reward": {
        "ru": [
            "чужая технология",
            "древний артефакт",
            "новый союзник",
            "звёздные карты неизведанного",
            "ценные научные данные",
            "редкий ресурс",
            "рост репутации и влияния",
            "секрет, меняющий баланс сил",
        ],
        "en": [
            "alien technology",
            "an ancient artifact",
            "a new ally",
            "star charts of the unknown",
            "valuable scientific data",
            "a rare resource",
            "a boost to reputation and influence",
            "a secret that shifts the balance of power",
        ],
    },
}

FORBIDDEN_OPENINGS: dict[str, list[str]] = {
    "ru": [
        "перехвачен сигнал",
        "неопознанный сигнал",
        "сигнал бедствия",
        "SOS",
        "аномальное излучение",
        "загадочная передача",
        "обрывок transmissions",
    ],
    "en": [
        "intercepted signal",
        "unidentified signal",
        "distress signal",
        "SOS",
        "anomalous emission",
        "mysterious transmission",
        "fragment of a transmission",
    ],
}


def select_mission_seeds(
    language: str = "en", rng: random.Random | None = None
) -> dict:
    """Pick a mission archetype and one entry per seed table (deterministic with rng).

    Returns {"archetype": <key>, "seeds": {table: entry}, "language": language}.
    """
    r = rng or random.Random()
    lang = "ru" if language == "ru" else "en"
    archetype = r.choice(list(MISSION_ARCHETYPES.keys()))
    seeds = {table: r.choice(opts[lang]) for table, opts in SEED_TABLES.items()}
    return {"archetype": archetype, "seeds": seeds, "language": lang}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd game-server-api && python -m unittest tests.test_game_rules.TestMissionSeeds -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add game-server-api/game_rules.py game-server-api/tests/test_game_rules.py
git commit -m "feat(rules): mission archetype catalog, seed tables, selector (P2)"
```

---

## Task 7: prompts — inject archetype/seeds + forbidden openings into mission generation

**Files:**

- Modify: `game-server-api/prompts.py` (`build_mission_prompts` L1043-1069)
- Modify: `game-server-api/game_master.py` (`generate_mission` — pass seeds)
- Modify: `game-server-api/tests/test_game_rules.py` (add prompt test)

**Interfaces:**

- Consumes: `select_mission_seeds`, `MISSION_ARCHETYPES`, `FORBIDDEN_OPENINGS` from `game_rules` (Task 6).
- Produces: `build_mission_prompts(language, crew_desc, archetype=None, seeds=None)`. `generate_mission` selects seeds once and stores them on the result under keys `archetype` and `seeds`.

- [ ] **Step 1: Write the failing test**

Append to `game-server-api/tests/test_game_rules.py`:

```python
from prompts import build_mission_prompts


class TestMissionPromptInjection(unittest.TestCase):
    def test_prompt_includes_archetype_and_seeds(self):
        seeds = select_mission_seeds(language="en", rng=_random.Random(7))
        system, user = build_mission_prompts(
            "en", "  - Pilot (player)", archetype=seeds["archetype"], seeds=seeds["seeds"]
        )
        self.assertIn(seeds["archetype"], system + user)
        for value in seeds["seeds"].values():
            self.assertIn(value, system + user)

    def test_prompt_lists_forbidden_openings_and_threshold_range(self):
        _, user = build_mission_prompts("ru", "  - Пилот (игрок)")
        self.assertIn("3-5", user)
        self.assertIn("сигнал", user)  # forbidden list mentions the banned trope
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd game-server-api && python -m unittest tests.test_game_rules.TestMissionPromptInjection -v`
Expected: FAIL — `TypeError: build_mission_prompts() takes 2 positional arguments but 4 were given`.

- [ ] **Step 3: Write minimal implementation**

In `game-server-api/prompts.py`, first add a top-level import after the existing `from language import (...)` block (around L8-13), so all imports stay at the top per AGENTS.md:

```python
from game_rules import FORBIDDEN_OPENINGS, MISSION_ARCHETYPES
```

(`game_rules` imports only stdlib, so this introduces no cycle.) Then replace the whole `build_mission_prompts` function (L1043-1069) with:

```python
def build_mission_prompts(
    language: str,
    crew_desc: str,
    archetype: str | None = None,
    seeds: dict | None = None,
) -> tuple[str, str]:
    """Build system and user prompts for mission generation.

    When archetype/seeds are provided they are injected to force variety (P2);
    a banned-trope list and a balanced threshold range are always included.
    """
    lang = LANGUAGE_RU if language == LANGUAGE_RU else LANGUAGE_EN
    forbidden = ", ".join(FORBIDDEN_OPENINGS[lang])
    arch_hint = ""
    if archetype and archetype in MISSION_ARCHETYPES:
        arch_hint = MISSION_ARCHETYPES[archetype][lang]

    if seeds:
        if lang == LANGUAGE_RU:
            seeds_block = (
                "\nОБЯЗАТЕЛЬНЫЕ элементы миссии (используй их):\n"
                f"- Место: {seeds.get('setting', '')}\n"
                f"- Осложнение: {seeds.get('complication', '')}\n"
                f"- Возможный поворот: {seeds.get('twist', '')}\n"
                f"- Награда: {seeds.get('reward', '')}\n\n"
            )
        else:
            seeds_block = (
                "\nMANDATORY mission elements (use them):\n"
                f"- Setting: {seeds.get('setting', '')}\n"
                f"- Complication: {seeds.get('complication', '')}\n"
                f"- Possible twist: {seeds.get('twist', '')}\n"
                f"- Reward: {seeds.get('reward', '')}\n\n"
            )
    else:
        seeds_block = ""

    if lang == LANGUAGE_RU:
        system = (
            "Ты — Game Master космической игры. Создаёшь миссию для экипажа звёздного корабля. "
            "Миссия делится на 2-4 этапа (stages), каждый с прогрессом от 1 до 10."
            + (f"\nАрхетип миссии: {arch_hint}" if arch_hint else "")
        )
        user = (
            f"Экипаж:\n{crew_desc}\n\n"
            f"{seeds_block}"
            "ЗАПРЕЩЕНО начинать миссию с клише про сигнал бедствия / перехваченный сигнал / "
            f"неопознанную передачу. Запрещённые завязки: {forbidden}.\n"
            "Создай миссию с:\n"
            "1. Название миссии — только кодовое имя и описание (формат: 'Кодовое имя: описание'). "
            "ВАЖНО: слово 'Миссия' в названии НЕ пиши — оно будет добавлено автоматически в интерфейсе.\n"
            "2. Описание — что нужно сделать, 2-3 абзаца\n"
            "3. 2-4 этапа с целями, каждый с success_threshold в диапазоне 3-5\n"
            "Этапы должны быть последовательными, но достижимыми нелинейно.\n"
            "Всё на русском языке."
        )
    else:
        system = (
            "You are a Game Master. Create a mission for a starship crew. "
            "The mission is divided into 2-4 stages, each with progress from 1 to 10."
            + (f"\nMission archetype: {arch_hint}" if arch_hint else "")
        )
        user = (
            f"Crew:\n{crew_desc}\n\n"
            f"{seeds_block}"
            "DO NOT start the mission with the cliché of a distress signal / intercepted signal / "
            f"unidentified transmission. Forbidden openings: {forbidden}.\n"
            "Create a mission with:\n"
            "1. Mission name — code name and description only (format: 'Code Name: description'). "
            "IMPORTANT: do NOT include the word 'Mission' in the name — it will be added automatically by the UI.\n"
            "2. Description — what needs to be done, 2-3 paragraphs\n"
            "3. 2-4 stages with objectives, each with success_threshold in the range 3-5\n"
            "Stages should be sequential but achievable non-linearly."
        )
    return system, user
```

Then in `game-server-api/game_master.py`, extend the top-level import added in Task 3 so seeds are selected without an in-function import:

```python
from game_rules import normalize_mission, select_mission_seeds
```

Inside `generate_mission`, replace the `system, user = build_mission_prompts(self.language, crew_desc)` line (near the top of the method) with:

```python
        mission_seeds = select_mission_seeds(self.language)
        system, user = build_mission_prompts(
            self.language, crew_desc, archetype=mission_seeds["archetype"], seeds=mission_seeds["seeds"]
        )
```

And at the end of `generate_mission`, just before `result = normalize_mission(result)`, attach the seeds so they are persisted (Task 8 stores them):

```python
        result["archetype"] = mission_seeds["archetype"]
        result["seeds"] = mission_seeds["seeds"]
        result = normalize_mission(result)
        logger.info(f"[MISSION] Generated: {result.get('name', '')} ({result['total_stages']} stages)")
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd game-server-api && python -m unittest tests.test_game_rules.TestMissionPromptInjection -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add game-server-api/prompts.py game-server-api/game_master.py game-server-api/tests/test_game_rules.py
git commit -m "feat(prompts): inject archetype/seeds + banned-trope list into mission gen (P2)"
```

---

## Task 8: database — migration 6 (archetype, seeds) + persistence

**Files:**

- Modify: `game-server-api/database.py` (`MIGRATIONS` list ~L33-49; `create_mission` L1744; `get_mission` L1769)
- Modify: `game-server-api/tests/test_mission_db.py`

**Interfaces:**

- Consumes: `normalize_mission` from `game_rules` (already wired in Task 4; it passes `archetype`/`seeds` through via `dict(mission)` copy).
- Produces: `game_missions` table gains `archetype TEXT DEFAULT ''` and `seeds TEXT DEFAULT '{}'`. `create_mission`/`get_mission` round-trip these fields.

- [ ] **Step 1: Write the failing test**

Append to `game-server-api/tests/test_mission_db.py` `TestMissionPersistence`:

```python
    def test_archetype_and_seeds_round_trip(self):
        raw = self._raw_mission()
        raw["archetype"] = "first_contact"
        raw["seeds"] = {"setting": "orbital station", "complication": "pirates"}
        db.create_mission(raw, "g3")
        got = db.get_mission(None, "g3")
        self.assertEqual(got["archetype"], "first_contact")
        self.assertEqual(got["seeds"]["complication"], "pirates")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd game-server-api && python -m unittest tests.test_mission_db -v`
Expected: FAIL — `sqlite3.OperationalError: table game_missions has no column named archetype`.

- [ ] **Step 3: Write minimal implementation**

In `game-server-api/database.py`, append a single migration (version 6) to the `MIGRATIONS` list, right after the version-5 entry. It adds both new columns:

```python
    (
        6,
        """
        ALTER TABLE game_missions ADD COLUMN archetype TEXT DEFAULT '';
        ALTER TABLE game_missions ADD COLUMN seeds TEXT DEFAULT '{}';
        """.strip(),
    ),
```

(Migration 7 is added later in Task 10.)

Then in `create_mission`, add the two columns to the INSERT. Replace the `cursor.execute(` block's SQL and params:

```python
    cursor.execute(
        """INSERT INTO game_missions
           (game_id, name, description, objectives, stage_progress, current_stage, total_stages, completed, archetype, seeds, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
        (
            game_id,
            mission_data["name"],
            mission_data["description"],
            json.dumps(mission_data.get("objectives", []), ensure_ascii=False),
            json.dumps(mission_data.get("stage_progress", {}), ensure_ascii=False),
            mission_data.get("current_stage", 1),
            mission_data.get("total_stages") or len(mission_data.get("objectives", []) or []) or 1,
            mission_data.get("archetype", ""),
            json.dumps(mission_data.get("seeds", {}), ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )
```

Then in `get_mission`'s returned dict (the one wrapped in `normalize_mission(...)` from Task 4), add the two fields inside the dict literal:

```python
            "archetype": row["archetype"] if "archetype" in row.keys() else "",
            "seeds": json.loads(row["seeds"] or "{}") if "seeds" in row.keys() else {},
```

(Place these two lines inside the dict passed to `normalize_mission`, e.g. after `"created_at": ...`. The `if "archetype" in row.keys()` guard keeps `get_mission` working even on DBs where migration 6 has not yet run.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd game-server-api && python -m unittest tests.test_mission_db -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add game-server-api/database.py game-server-api/tests/test_mission_db.py
git commit -m "feat(db): persist mission archetype/seeds (migration 6) (P2)"
```

---

## Task 9: prompts — rework combined-outcome prompts (less punishment, fewer deaths)

**Files:**

- Modify: `game-server-api/prompts.py` (`_COMBINED_OUTCOME_SYSTEM_RU` L521, `_COMBINED_OUTCOME_USER_RU` L539, `_COMBINED_OUTCOME_SYSTEM_EN` L580, `_COMBINED_OUTCOME_USER_EN` L598)

**Interfaces:**

- No signature change. Replaces the four constant strings only. `COMBINED_OUTCOME_SCHEMA` (L397) is unchanged.

- [ ] **Step 1: Confirm current text locations**

Run: `cd game-server-api && grep -n "Must be DRAMATIC\|MUST CHANGE something" prompts.py`
Expected: 2 matches inside the EN/RU user prompts — confirms the punishment framing to remove.

- [ ] **Step 2: Write the implementation**

In `game-server-api/prompts.py`, replace `_COMBINED_OUTCOME_SYSTEM_RU` (L521) with:

```python
_COMBINED_OUTCOME_SYSTEM_RU = (
    "Ты — Game Master космической игры. Ты анализируешь ВСЕ решения, принятые "
    "игроками и NPC, вместе с их СКРЫТЫМИ последствиями, и создаёшь единый "
    "связный результат хода.\n\n"
    "ГЛАВНЫЕ ПРИНЦИПЫ:\n"
    "1. Решения ИГРОКОВ (Weight: HIGH) имеют БОЛЬШИЙ вес, чем решения NPC.\n"
    "2. Прогресс миссии накапливается от смелых и грамотных решений. Регресс возможен, "
    "но только от явно рискованных или ошибочных действий.\n"
    "3. Движущая сила истории — ОТКРЫТИЯ, повороты сюжета, новые союзники и враги, находки. "
    "Драма рождается из событий и открытий, а не из количества трупов и повреждений.\n"
    "4. Гибель и ранения — РЕДКОЕ и кумулятивное следствие явного риска или серии неудач, "
    "а не фон каждого хода. Если экипаж действует разумно и смело — он выживает и продвигается.\n"
    "5. NPC действуют КОМПЕТЕНТНО в рамках своей роли и редко вредят миссии.\n"
    "6. У каждого персонажа, принявшего решение, должен быть ПЕРСОНАЛЬНЫЙ ИСХОД в personal_outcomes.\n"
    "7. Прошлые повреждения корабля сохраняются — их нельзя просто 'забыть'."
)
```

Replace `_COMBINED_OUTCOME_USER_RU` (L539) with:

```python
_COMBINED_OUTCOME_USER_RU = (
    "Общие обстоятельства:\n"
    "Локация: {setting}\n"
    "Конфликт: {conflict}\n"
    "Описание: {narrative}\n\n"
    "ПРЕДЫДУЩИЕ СОБЫТИЯ:\n{previous_summary}\n\n"
    "Статус миссии:\n{mission_text}\n\n"
    "Принятые решения (игроки имеют HIGH вес, NPC — NORMAL):\n{decisions_text}\n\n"
    "{roster_text}\n"
    "Проанализируй все решения и создай единый связанный результат. "
    "Помни, что решения ИГРОКОВ важнее решений NPC.\n\n"
    "Каждый ход что-то меняет — но перемена это не обязательно урон или гибель. "
    "Чаще всего это открытие, твист, новый союзник, находка или сдвиг в миссии.\n"
    "- Смелое и грамотное действие → миссия продвигается, находятся ресурсы, союзники, открываются возможности.\n"
    "- Пассивное, трусливое или ошибочное действие → локальный регресс или повреждение (небольшое).\n"
    "- Гибель и тяжелые ранения — только для явно рискованных действий или накопленных неудач.\n\n"
    "Верни JSON с полями:\n"
    "1. outcome_narrative — что произошло в результате всех решений (2-3 абзаца). Живой и осмысленный текст.\n"
    "2. ship_status_change — как изменилось состояние корабля (текст)\n"
    "3. crew_morale_change — как изменился моральный дух экипажа (текст)\n"
    "4. next_day_hook — зацепка для следующего хода, которая создаёт ожидание\n"
    "5. mission_progress — МАССИВ объектов [{{'stage': N, 'points': +/-M}}]. "
    "Положительные = прогресс, отрицательные = регресс/откат (используй умеренные значения).\n"
    "6. dead_crew_members — список [[name, role]] погибших ИЗ СПИСКА ЭКИПАЖА. "
    "Убивать можно ТОЛЬКО персонажей из списка экипажа. Не выдумывай новых членов. "
    "Чаще оставляй пустым — смерть должна быть редкой и обоснованной.\n"
    "Если персонаж погибает — опиши это в outcome_narrative И добавь в dead_crew_members. "
    "Смерть и ранения возможны ТОЛЬКО для активных участников и ТОЛЬКО когда скрытое последствие "
    "выбранного действия этого требует. Не убивай случайных безымянных членов экипажа.\n"
    "Если персонаж ранен — опиши ранение в narrative и добавь в crew_injured.\n"
    "7. ship_destroyed — true/false\n"
    "8. ship_hull_integrity — целостность корпуса в % (0-100). УМЕНЬШАЕТСЯ от повреждений.\n"
    "9. ship_shields — состояние щитов в % (0-100)\n"
    "10. ship_systems_offline — массив строк: какие системы корабля вышли из строя "
    "(например ['warp drive', 'life support', 'weapons', 'communications'])\n"
    "11. crew_injured — список [[name, role, severity]] раненых. severity: 'critical', 'moderate', 'minor'.\n"
    "12. personal_outcomes — МАССИВ объектов {{'character_name': ..., 'role': ..., 'outcome_text': ...}} "
    "для КАЖДОГО персонажа, принимавшего решение.\n\n"
    "Всё на русском языке."
)
```

Replace `_COMBINED_OUTCOME_SYSTEM_EN` (L580) with:

```python
_COMBINED_OUTCOME_SYSTEM_EN = (
    "You are a Game Master. You analyze ALL decisions made by "
    "players and NPCs together with their HIDDEN consequences, "
    "and produce a single coherent turn outcome.\n\n"
    "CORE PRINCIPLES:\n"
    "1. PLAYER decisions (Weight: HIGH) matter MORE than NPC decisions.\n"
    "2. Mission progress accumulates from bold, smart decisions. Regression is possible, "
    "but only from explicitly risky or wrong actions.\n"
    "3. The engine of the story is DISCOVERIES, plot twists, new allies and enemies, findings. "
    "Drama comes from events and revelations, not from a body count and damage totals.\n"
    "4. Deaths and injuries are a RARE, cumulative consequence of explicit risk or a run of bad luck, "
    "not background noise for every turn. If the crew acts smartly and boldly, it survives and advances.\n"
    "5. NPCs act COMPETENTLY within their role and rarely harm the mission.\n"
    "6. Every character who made a decision must have a PERSONAL OUTCOME in personal_outcomes.\n"
    "7. Past ship damage PERSISTS — it cannot be simply 'forgotten'."
)
```

Replace `_COMBINED_OUTCOME_USER_EN` (L598) with:

```python
_COMBINED_OUTCOME_USER_EN = (
    "Global circumstances:\n"
    "Setting: {setting}\n"
    "Conflict: {conflict}\n"
    "Narrative: {narrative}\n\n"
    "PREVIOUS EVENTS:\n{previous_summary}\n\n"
    "Mission status:\n{mission_text}\n\n"
    "All decisions (players = HIGH weight, NPCs = NORMAL):\n{decisions_text}\n\n"
    "{roster_text}\n"
    "Analyze all decisions together and create a coherent combined result. "
    "Remember that PLAYER decisions matter more than NPC decisions.\n\n"
    "Every turn changes something — but a change is not necessarily damage or death. "
    "Most often it is a discovery, a twist, a new ally, a finding, or a shift in the mission.\n"
    "- A bold, smart action → the mission advances, resources are found, allies appear, opportunities open.\n"
    "- A passive, cowardly, or wrong action → a local setback or (minor) damage.\n"
    "- Death and severe injuries — only for explicitly risky actions or accumulated bad luck.\n\n"
    "Return JSON with fields:\n"
    "1. outcome_narrative — what happened (2-3 paragraphs). Vivid and meaningful.\n"
    "2. ship_status_change — narrative of ship condition change\n"
    "3. crew_morale_change — how morale shifted\n"
    "4. next_day_hook — teaser for the next turn that creates anticipation\n"
    "5. mission_progress — ARRAY of [{{'stage': N, 'points': +/-M}}]. "
    "Positive = progress, Negative = regression/setback (use moderate values).\n"
    "6. dead_crew_members — list of [name, role] from the CREW ROSTER. "
    "Can ONLY kill characters listed in the full crew roster. Do NOT invent non-existent crew members. "
    "Leave empty most of the time — death should be rare and justified.\n"
    "If a character dies — describe it IN outcome_narrative AND add them to dead_crew_members. "
    "Death and injury can ONLY happen to active participants and ONLY when the hidden consequence of "
    "the chosen action requires it. Do NOT kill random unnamed crew members.\n"
    "Similarly: if a character is injured — describe the injury in narrative and add to crew_injured.\n"
    "7. ship_destroyed — true/false\n"
    "8. ship_hull_integrity — hull integrity % (0-100). DECREASES with damage.\n"
    "9. ship_shields — shield strength % (0-100)\n"
    "10. ship_systems_offline — array of offline/damaged systems "
    "(e.g. ['warp drive', 'life support', 'weapons', 'communications'])\n"
    "11. crew_injured — list of [name, role, severity] injured. severity: 'critical', 'moderate', 'minor'.\n"
    "12. personal_outcomes — ARRAY of {{'character_name': ..., 'role': ..., 'outcome_text': ...}} "
    "for EVERY character who made a decision.\n"
)
```

- [ ] **Step 3: Verify the punishment framing is gone and `.format()` keys still match**

Run: `cd game-server-api && grep -n "Must be DRAMATIC\|MUST CHANGE something" prompts.py`
Expected: no matches.

Run: `cd game-server-api && python -c "from prompts import _COMBINED_OUTCOME_USER_RU, _COMBINED_OUTCOME_USER_EN; _COMBINED_OUTCOME_USER_RU.format(setting='',conflict='',narrative='',previous_summary='',mission_text='',decisions_text='',roster_text=''); _COMBINED_OUTCOME_USER_EN.format(setting='',conflict='',narrative='',previous_summary='',mission_text='',decisions_text='',roster_text=''); print('format keys OK')"`
Expected: prints `format keys OK` (confirms no stray `{` braces broke `.format()`).

- [ ] **Step 4: Run the full test suite**

Run: `cd game-server-api && python -m unittest discover -s tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add game-server-api/prompts.py
git commit -m "feat(prompts): rework combined-outcome prompts — discoveries over punishment, rarer deaths (P3)"
```

---

## Task 10: Rules layer — death rate-limiting + migration 7 + DB get/set last_death_day

**Files:**

- Modify: `game-server-api/game_rules.py`
- Modify: `game-server-api/tests/test_game_rules.py`
- Modify: `game-server-api/database.py` (`MIGRATIONS`; `get_game_state` L911; new `set_last_death_day`)
- Modify: `game-server-api/tests/test_mission_db.py`

**Interfaces:**

- Produces: `game_rules.DEATH_COOLDOWN_TURNS`, `game_rules.apply_death_limits(outcome, current_day, last_death_day, alive_count=None, min_alive=1, cooldown=...) -> tuple[dict,int]`. Returns `(new_outcome, new_last_death_day)`. Excess proposed deaths are demoted to `critical` injuries (appended to `crew_injured`). Whole-ship destruction (`ship_destroyed`) is NOT throttled.
- Produces (DB): `game_state.last_death_day` column; `get_game_state` returns `last_death_day`; `set_last_death_day(game_id, day)` writes it.

- [ ] **Step 1: Write the failing tests**

Append to `game-server-api/tests/test_game_rules.py`:

```python
from game_rules import DEATH_COOLDOWN_TURNS, apply_death_limits


class TestDeathLimits(unittest.TestCase):
    def test_first_death_allowed(self):
        outcome = {"dead_crew_members": [["A", "Pilot"]], "crew_injured": []}
        out, last = apply_death_limits(outcome, day=3, last_death_day=0, alive_count=5)
        self.assertEqual(out["dead_crew_members"], [["A", "Pilot"]])
        self.assertEqual(last, 3)
        self.assertEqual(out["crew_injured"], [])

    def test_second_death_on_cooldown_is_demoted_to_critical(self):
        outcome = {"dead_crew_members": [["B", "Medic"]], "crew_injured": []}
        out, last = apply_death_limits(
            outcome, day=4, last_death_day=3, alive_count=5
        )
        self.assertEqual(out["dead_crew_members"], [])
        self.assertEqual(out["crew_injured"], [["B", "Medic", "critical"]])
        self.assertEqual(last, 3)  # unchanged, no new death accepted

    def test_death_after_cooldown_allowed_again(self):
        outcome = {"dead_crew_members": [["C", "Engineer"]], "crew_injured": []}
        out, last = apply_death_limits(
            outcome, day=3 + DEATH_COOLDOWN_TURNS, last_death_day=3, alive_count=4
        )
        self.assertEqual(out["dead_crew_members"], [["C", "Engineer"]])
        self.assertEqual(last, 3 + DEATH_COOLDOWN_TURNS)

    def test_extra_deaths_in_one_turn_demoted(self):
        outcome = {
            "dead_crew_members": [["A", "Pilot"], ["B", "Medic"], ["C", "Eng"]],
            "crew_injured": [],
        }
        out, _ = apply_death_limits(outcome, day=5, last_death_day=0, alive_count=6)
        self.assertEqual(len(out["dead_crew_members"]), 1)
        self.assertEqual(len(out["crew_injured"]), 2)
        self.assertTrue(all(i[2] == "critical" for i in out["crew_injured"]))

    def test_never_kill_below_min_alive(self):
        outcome = {"dead_crew_members": [["A", "Pilot"]], "crew_injured": []}
        out, last = apply_death_limits(
            outcome, day=2, last_death_day=0, alive_count=1, min_alive=1
        )
        # only 1 alive, min_alive=1 -> cannot drop to 0
        self.assertEqual(out["dead_crew_members"], [])
        self.assertEqual(last, 0)
        self.assertEqual(out["crew_injured"], [["A", "Pilot", "critical"]])

    def test_ship_destruction_not_throttled(self):
        outcome = {
            "ship_destroyed": True,
            "dead_crew_members": [["A", "Pilot"], ["B", "Medic"]],
        }
        out, last = apply_death_limits(outcome, day=4, last_death_day=3, alive_count=5)
        self.assertEqual(len(out["dead_crew_members"]), 2)
        self.assertEqual(last, 3)  # unchanged: not a normal death event
```

Append to `game-server-api/tests/test_mission_db.py` a new test class:

```python
class TestLastDeathDay(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_get_returns_zero_default_and_set_persists(self):
        state = db.get_game_state("gd1")
        self.assertEqual(state["last_death_day"], 0)
        db.set_last_death_day("gd1", 7)
        self.assertEqual(db.get_game_state("gd1")["last_death_day"], 7)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd game-server-api && python -m unittest tests.test_game_rules.TestDeathLimits tests.test_mission_db.TestLastDeathDay -v`
Expected: FAIL — `ImportError: cannot import name 'apply_death_limits'`; and `KeyError: 'last_death_day'` / `AttributeError: ... set_last_death_day`.

- [ ] **Step 3: Write minimal implementation**

Append to `game-server-api/game_rules.py`:

```python
# ── Crew death rate-limiting (P3) ──────────────────────────────────

DEATH_COOLDOWN_TURNS = 4  # minimum turns between crew deaths in a game


def _demote_to_critical(entry) -> list:
    """Turn a rejected [name, role] death into a [name, role, 'critical'] injury."""
    if isinstance(entry, list):
        name = entry[0] if len(entry) > 0 else "Unknown"
        role = entry[1] if len(entry) > 1 else "Unknown"
    else:
        name = role = "Unknown"
    return [name, role, "critical"]


def apply_death_limits(
    outcome: dict,
    current_day: int,
    last_death_day: int,
    alive_count: int | None = None,
    min_alive: int = 1,
    cooldown: int = DEATH_COOLDOWN_TURNS,
) -> tuple[dict, int]:
    """Enforce crew-death rate limits on a raw combined outcome (P3).

    - At most one crew death per `cooldown` turns; extra proposed deaths are
      demoted to 'critical' injuries (appended to crew_injured).
    - Never accept a death that would drop the living roster below `min_alive`.
    - Whole-ship destruction (``ship_destroyed``) is NOT throttled.

    Returns (new_outcome, new_last_death_day). Input is not mutated.
    """
    result = dict(outcome)
    proposed = result.get("dead_crew_members", []) or []
    if result.get("ship_destroyed") or not proposed:
        return result, last_death_day

    on_cooldown = bool(last_death_day) and (current_day - last_death_day) < cooldown
    accepted: list = []
    demoted: list = []

    for entry in proposed:
        slot_available = (not on_cooldown) and len(accepted) == 0
        leaves_enough = alive_count is None or (alive_count - len(accepted)) > min_alive
        if slot_available and leaves_enough:
            accepted.append(entry)
            on_cooldown = True
        else:
            demoted.append(_demote_to_critical(entry))

    result["dead_crew_members"] = accepted
    if demoted:
        injuries = list(result.get("crew_injured", []) or [])
        injuries.extend(demoted)
        result["crew_injured"] = injuries

    new_last_death_day = current_day if accepted else last_death_day
    return result, new_last_death_day
```

In `game-server-api/database.py`, append migration 7 to the `MIGRATIONS` list (after the version-6 entry from Task 8):

```python
    (
        7,
        "ALTER TABLE game_state ADD COLUMN last_death_day INTEGER DEFAULT 0;",
    ),
```

In `get_game_state` (L911), add the field to the returned dict. Replace:

```python
    return {
        "day": row["day"],
        "status": row["status"],
        "ship_alive": bool(row["ship_alive"]),
        "crew_health": row["crew_health"],
        "last_updated": row["last_updated"],
    }
```

with:

```python
    return {
        "day": row["day"],
        "status": row["status"],
        "ship_alive": bool(row["ship_alive"]),
        "crew_health": row["crew_health"],
        "last_death_day": row["last_death_day"] if "last_death_day" in row.keys() else 0,
        "last_updated": row["last_updated"],
    }
```

Add a new function right after `update_game_state` (after its `return`):

```python
def set_last_death_day(game_id: str = "default_game", day: int = 0) -> bool:
    """Record the day of the most recent crew death (death cooldown tracking)."""
    _ensure_game_state(game_id)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE game_state SET last_death_day = ? WHERE game_id = ?",
        (int(day), game_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd game-server-api && python -m unittest tests.test_game_rules.TestDeathLimits tests.test_mission_db.TestLastDeathDay -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add game-server-api/game_rules.py game-server-api/database.py game-server-api/tests/test_game_rules.py game-server-api/tests/test_mission_db.py
git commit -m "feat(rules): rate-limit crew deaths (cooldown + min-alive), migration 7 (P3)"
```

---

## Task 11: main — wire death limits into _analyze_day_outcome; briefing defensive action

**Files:**

- Modify: `game-server-api/main.py` (imports; `_analyze_day_outcome` death block ~L2560-2600)
- Modify: `game-server-api/prompts.py` (`build_personal_briefing_system` L1160)

**Interfaces:**

- Consumes: `apply_death_limits` from `game_rules` (Task 10), `get_game_state`, `set_last_death_day` from `database`.

- [ ] **Step 1: Confirm the death block and game_state read exist**

Run: `cd game-server-api && grep -n "dead_crew = outcome.get\|state = get_game_state(game_id)" main.py`
Expected: matches around L2560 and L2590.

- [ ] **Step 2: Write the implementation**

In `game-server-api/main.py`, add to the top imports (next to the Task 5 `from game_rules import apply_mission_progress`):

```python
from game_rules import apply_death_limits
```

And add `set_last_death_day` to the existing `from database import (...)` block.

In `_analyze_day_outcome`, immediately AFTER the mission-progress block (the one replaced in Task 5, which ends by setting `mission = updated_mission`) and BEFORE the line `ship_hull = outcome.get("ship_hull_integrity", 100)`, insert the death-limiting block:

```python
        # Rate-limit crew deaths through the rules layer (P3):
        # at most one death per DEATH_COOLDOWN_TURNS, never below min_alive;
        # excess proposed deaths are demoted to critical injuries.
        state = get_game_state(game_id)
        alive_count = sum(1 for r in crew_roster if not r.get("is_dead"))
        outcome, new_last_death_day = apply_death_limits(
            outcome,
            current_day=day,
            last_death_day=int(state.get("last_death_day", 0) or 0),
            alive_count=alive_count,
        )
        if new_last_death_day != int(state.get("last_death_day", 0) or 0):
            set_last_death_day(game_id, new_last_death_day)
            logger.info(f"[DEATH] Cooldown window starts at day {new_last_death_day}")
```

> Note: `crew_roster` is already built earlier in `_analyze_day_outcome` (the loop building `{"name","role","is_dead"}` dicts). The existing death-processing loop (`for death_entry in dead_crew:`) downstream now operates on the already-rate-limited `outcome["dead_crew_members"]`, so it needs no further change — but confirm it reads `dead_crew = outcome.get("dead_crew_members", [])` AFTER this insertion point (it does, ~L2560). If the existing line reads `outcome.get` into `dead_crew` later in the function, no edit is needed there.

In `game-server-api/prompts.py`, add a defensive-action guarantee to `build_personal_briefing_system` (L1160). In the RU return string, append before the closing `)`:

```python
            "Среди вариантов действий ВСЕГДА должен быть хотя бы один безопасный/оборонительный "
            "выбор (прикрыть, защищаться, эвакуироваться, переждать), предсказуемо снижающий урон."
```

And in the EN return string, append before the closing `)`:

```python
            "Among the action choices there MUST ALWAYS be at least one safe/defensive option "
            "(cover, defend, evacuate, wait it out) that predictably reduces incoming damage."
```

- [ ] **Step 3: Verify imports resolve and no syntax errors**

Run: `cd game-server-api && python -c "import main; import prompts; print('imports OK')"`
Expected: prints `imports OK`.

- [ ] **Step 4: Run the full test suite**

Run: `cd game-server-api && python -m unittest discover -s tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add game-server-api/main.py game-server-api/prompts.py
git commit -m "feat(main): apply death limits in outcome; guarantee defensive briefing action (P3 wiring)"
```

---

## Verification & rollout

After all tasks land:

- [ ] **Full test suite green:** `cd game-server-api && python -m unittest discover -s tests -v`
- [ ] **Import smoke test:** `cd game-server-api && python -c "import main, game_master, game_rules, database, prompts; print('OK')"`
- [ ] **Existing-data repair check (per spec §9):** the stuck `default_game` mission (`current_stage=0, total_stages=1`) is now repaired on read by `get_mission` → `normalize_mission`. Verify:
  `cd game-server-api && python -c "import database as db; db.DB_PATH=db.DB_PATH; m=db.get_mission(None,'default_game'); print('total_stages',m['total_stages'],'current_stage',m['current_stage'],'completed',m['completed'])"`
  Expected: `total_stages` equals the number of objectives (≥2), `current_stage` reflects real progress, `completed` may be True if thresholds already met by `{1:6,2:6,...}`.
- [ ] **Deploy & regenerate:** apply code without wiping data:
  `docker compose --progress=plain stop telegram-bot game-master game-server-api --timeout=1 && docker compose --progress=plain up -d --force-recreate telegram-bot game-master game-server-api`
  Then trigger a new mission via Telegram `/gm_start_game <game_id>` (full-wipe) OR continue an existing game `/gm_continue_game <game_id>` to observe the rules layer in action.

## Out of scope (deferred — spec P4)

- Full multi-level HP/injury system with healing over turns and a dedicated medic role.
- `crew_injuries` history table for per-injury tracking.
- Weighted archetype selection by crew composition.
