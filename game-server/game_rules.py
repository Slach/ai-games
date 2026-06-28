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


def _compute_stage_state(objectives: list[dict], stage_progress: dict[str, int]) -> tuple[int, bool]:
    """Return (current_stage, completed).

    current_stage = number of the first not-yet-completed stage (1-based),
    or total_stages + 1 when all stages reached their threshold.
    completed = whether ALL stages reached their threshold.
    """
    for o in objectives:
        if stage_progress.get(str(o["stage"]), 0) < o["success_threshold"]:
            return o["stage"], False
    return len(objectives) + 1, True


def apply_mission_progress(mission: dict, progress_entries: list[dict] | None) -> dict:
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
            "огромный космический дереликв",
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


def select_mission_seeds(language: str = "en", rng: random.Random | None = None) -> dict:
    """Pick a mission archetype and one entry per seed table (deterministic with rng).

    Returns {"archetype": <key>, "seeds": {table: entry}, "language": language}.
    """
    r = rng or random.Random()
    lang = "ru" if language == "ru" else "en"
    archetype = r.choice(list(MISSION_ARCHETYPES.keys()))
    seeds = {table: r.choice(opts[lang]) for table, opts in SEED_TABLES.items()}
    return {"archetype": archetype, "seeds": seeds, "language": lang}


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
    turn: int,
    last_death_turn: int,
    alive_count: int | None = None,
    min_alive: int = 1,
    cooldown: int = DEATH_COOLDOWN_TURNS,
) -> tuple[dict, int]:
    """Enforce crew-death rate limits on a raw combined outcome (P3).

    - At most one crew death per `cooldown` turns; extra proposed deaths are
      demoted to 'critical' injuries (appended to crew_injured).
    - Never accept a death that would drop the living roster below `min_alive`.
    - Whole-ship destruction (``ship_destroyed``) is NOT throttled.

    Returns (new_outcome, new_last_death_turn). Input is not mutated.
    """
    result = dict(outcome)
    proposed = result.get("dead_crew_members", []) or []
    if result.get("ship_destroyed") or not proposed:
        return result, last_death_turn

    on_cooldown = bool(last_death_turn) and (turn - last_death_turn) < cooldown
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

    new_last_death_turn = turn if accepted else last_death_turn
    return result, new_last_death_turn
