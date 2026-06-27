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
