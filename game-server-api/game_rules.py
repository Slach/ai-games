"""Deterministic game-rules layer between LLM output and the database.

The LLM proposes narrative deltas (mission_progress, deaths, injuries, ...).
Functions in this module enforce fairness: mission objectives are normalized,
progress is accumulated with regression caps and a tempo floor, mission
completion is computed from real thresholds, crew deaths are rate-limited, and
mission archetype/seeds are selected deterministically.

Pure functions only: no DB, no LLM, no logging. Easy to unit test.
"""

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
    - regression capped at -MAX_REGRESSION per entry on an incomplete stage.
    - already-completed stages are frozen: any regression on them is ignored.
    - tempo floor: the current working stage advances at least +1 per turn,
      but only on a turn that neither advanced nor explicitly regressed it.

    Returns a NEW normalized mission dict. Input is not mutated.
    """
    norm = normalize_mission(mission)
    objectives = norm["objectives"]
    stage_progress = dict(norm["stage_progress"])
    total_stages = norm["total_stages"]

    working_stage, _ = _compute_stage_state(objectives, stage_progress)
    advanced_working = False
    regressed_working = False

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
        key = str(stage_num)
        old = stage_progress.get(key, 0)
        stage_was_completed = old >= threshold
        if points >= 0:
            new = max(0, old + points)
        elif stage_was_completed:
            new = old  # P1: completed stages are frozen — no rollback at all
        else:
            new = max(0, old + max(points, -MAX_REGRESSION))  # P1: cap regression
        stage_progress[key] = new
        if stage_num == working_stage:
            if points > 0:
                advanced_working = True
            elif points < 0 and not stage_was_completed:
                regressed_working = True

    # P1: tempo floor — the working stage advances at least +1 per turn,
    # but only on a turn that neither advanced nor explicitly regressed it.
    if 1 <= working_stage <= total_stages:
        key = str(working_stage)
        threshold = threshold_by_stage[working_stage]
        uneventful = not advanced_working and not regressed_working
        if uneventful and stage_progress.get(key, 0) < threshold:
            stage_progress[key] = stage_progress.get(key, 0) + 1

    current_stage, completed = _compute_stage_state(objectives, stage_progress)
    norm["stage_progress"] = stage_progress
    norm["current_stage"] = current_stage
    norm["completed"] = completed
    return norm
