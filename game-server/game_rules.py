"""Deterministic game-rules layer between LLM output and the database.

The LLM proposes narrative deltas (mission_progress, deaths, injuries, ...).
Functions in this module enforce fairness: mission objectives are normalized,
progress is accumulated with regression caps (an empty turn means zero
progress), mission completion is computed from real thresholds, crew deaths
are applied as the LLM proposes them, and mission archetype/seeds are
selected deterministically.

Pure functions only: no DB, no LLM, no logging. Easy to unit test.
"""

import math
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
    - an empty turn leaves stage_progress unchanged (zero progress).

    Returns a NEW normalized mission dict. Input is not mutated.
    """
    norm = normalize_mission(mission)
    objectives = norm["objectives"]
    stage_progress = dict(norm["stage_progress"])

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

    current_stage, completed = _compute_stage_state(objectives, stage_progress)
    norm["stage_progress"] = stage_progress
    norm["current_stage"] = current_stage
    norm["completed"] = completed
    return norm


# ── Mission archetype & seed selection (P2) ────────────────────────

MISSION_ARCHETYPES: dict[str, dict[str, str]] = {
    "first_contact": {
        "ru": "Первый контакт — дипломатия с неизвестной цивилизацией на грани войны. Тон: паранойя, этика, языковой барьер, цена ошибки — конфликт галактического масштаба.",
        "en": "First contact — diplomacy with an unknown civilization on the brink of war. Tone: paranoia, ethics, language barrier; the price of error is a galaxy-scale conflict.",
    },
    "rescue": {
        "ru": "Спасательная операция — выжившие или пленники в зоне, где каждую минуту кто-то умирает. Тон: срочность, безвыборность, кого спасать, а кем пожертвовать.",
        "en": "Rescue operation — survivors or captives in a zone where someone dies every minute. Tone: urgency, no good options, choosing who to save and who to leave behind.",
    },
    "mystery": {
        "ru": "Расследование тайны — убийство или необъяснимое событие на борту, где подозревают своих. Тон: паранойя, улики, каждый улика против кого-то из экипажа.",
        "en": "Mystery investigation — a murder or unexplained event aboard, with the crew itself under suspicion. Tone: paranoia, clues, every clue points at someone on the crew.",
    },
    "infiltration": {
        "ru": "Проникновение — скрытная операция в сердце вражеской территории под прицелом. Тон: стелс, обман, одно неверное слово — и экипаж раскрыт и обречён.",
        "en": "Infiltration — a covert op in the heart of enemy territory under the gun. Tone: stealth, deception; one wrong word and the crew is burned and doomed.",
    },
    "defense": {
        "ru": "Оборона — защита объекта, который невозможно удержать, но нельзя сдать. Тон: напряжение, тактика, экипаж гибнет на позициях, кто-то должен закрыть брешь собой.",
        "en": "Defense — holding an objective that cannot be held yet cannot be surrendered. Tone: tension, tactics; the crew dies at their posts, someone must seal the breach with themselves.",
    },
    "intrigue": {
        "ru": "Политическая интрига — фракции, заговор, и один из экипажа — предатель по приказу. Тон: переговоры под дулом, предательство изнутри, союзы, которые убивают.",
        "en": "Political intrigue — factions, conspiracy, and one of the crew is a traitor under orders. Tone: negotiation at gunpoint, betrayal from within, alliances that kill.",
    },
    "anomaly": {
        "ru": "Изучение аномалии — пространственно-временной феномен, который ломает реальность и начинает убивать экипаж. Тон: чудо, обращённое в кошмар, парадокс, безумие.",
        "en": "Anomaly study — a spacetime phenomenon that breaks reality and starts killing the crew. Tone: wonder turned to nightmare, paradox, madness.",
    },
    "assault": {
        "ru": "Штурм — лобовая атака на укреплённую вражескую позицию. Тон: хаос боя, огонь, потери, каждый метр оплачен кровью экипажа.",
        "en": "Assault — a frontal attack on a fortified enemy position. Tone: the chaos of battle, fire, casualties; every meter is paid for in the crew's blood.",
    },
    "siege": {
        "ru": "Осада — экипаж заперт и окружён, ресурсы тают, надежда на спасение исчезает. Тон: удушье, голод, отчаянные вылазки, кто-то не доживёт до рассвета.",
        "en": "Siege — the crew is trapped and surrounded, resources dwindling, hope of rescue fading. Tone: suffocation, starvation, desperate sorties; someone will not live to see the dawn.",
    },
    "pursuit": {
        "ru": "Погоня и перехват — преследование цели, которая не остановится и не пощадит. Тон: скорость, риск столкновения, экипаж на пределе корабля и себя.",
        "en": "Pursuit and interception — chasing a target that will not stop and will not spare them. Tone: speed, the risk of collision; the crew pushed past the ship's and their own limits.",
    },
    "sabotage": {
        "ru": "Диверсия — уничтожить вражеский объект, который обратит оружие на миллионы. Тон: скрытность, таймер, заложник из своих, миссия важнее жизней.",
        "en": "Sabotage — destroy an enemy asset that will turn its weapon on millions. Tone: stealth, a ticking clock, one of their own as hostage; the mission outweighs lives.",
    },
    "last_stand": {
        "ru": "Последний рубеж — экипаж — единственное, что стоит между врагом и гибелью всего. Тон: обречённость, героизм, прощания, победа ценой полного уничтожения.",
        "en": "Last stand — the crew is all that stands between the enemy and total annihilation. Tone: doom, heroism, farewells; victory at the price of total destruction.",
    },
    "mutiny": {
        "ru": "Мятеж — половина экипажа подняла оружие против другой, корабль расколот надвое. Тон: братоубийство, выбор стороны, командир против своих.",
        "en": "Mutiny — half the crew has raised arms against the other, the ship split in two. Tone: fratricide, choosing a side, the commander against their own people.",
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
            "вражеский флот вышел из засады и отрезал пути отхода",
            "взбунтовавшийся бортовой ИИ запер экипаж в отсеках",
            "налётчики прорвали кордон и взяли корабль на абордаж",
            "зараза на борту превращает экипаж в нечто чужое",
            "головорезы враждебной фракции держат заложника из своих",
            "временная аномалия сделала половину корабля призраком",
            "конкурирующая экспедиция открыла огонь без предупреждения",
            "внутренний раскол экипажа дошёл до оружия",
        ],
        "en": [
            "an enemy fleet sprang an ambush and cut off the line of retreat",
            "a shipboard AI gone rogue has sealed the crew in their compartments",
            "raiders broke through the cordon and boarded the ship",
            "an outbreak aboard is turning the crew into something alien",
            "enforcers of a hostile faction hold one of the crew hostage",
            "a temporal anomaly has turned half the ship into a ghost",
            "a rival expedition opened fire without warning",
            "an internal crew schism has come to drawn weapons",
        ],
    },
    "twist": {
        "ru": [
            "союзник оказывается предателем и уже открыл огонь",
            "сигнал приходит из будущего — от обречённой версии экипажа",
            "объект миссии живой, разумен и умоляет о смерти",
            "истинная цель — уничтожить сам экипаж, а не спасти его",
            "награда несёт скрытую цену: её использование обречёт миллионы",
            "противник действует из благих побуждений и прав",
            "карта местности была ловушкой, выход перекрыт",
            "экипаж не один на объекте — вторые уже истребляют первых",
        ],
        "en": [
            "an ally is the traitor and has already opened fire",
            "the signal comes from the future — from a doomed version of the crew",
            "the mission target is alive, sentient, and begs to die",
            "the true objective is to destroy the crew itself, not save them",
            "the reward carries a hidden price: using it will doom millions",
            "the antagonist acts from noble motives and is right",
            "the map was a trap; the exit is sealed shut",
            "the crew is not alone at the site — a second group is already wiping out the first",
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


def select_mission_seeds(language: str, rng: random.Random | None) -> dict:
    """Pick a mission archetype and one entry per seed table (deterministic with rng).

    Returns {"archetype": <key>, "seeds": {table: entry}, "language": language}.
    """
    r = rng or random.Random()
    lang = "ru" if language == "ru" else "en"
    archetype = r.choice(list(MISSION_ARCHETYPES.keys()))
    seeds = {table: r.choice(opts[lang]) for table, opts in SEED_TABLES.items()}
    return {"archetype": archetype, "seeds": seeds, "language": lang}


# ── Ship status (persistent hull / shields / offline systems) ──────
# hull/shields/systems_offline are code-owned state stored in game_state.
# The LLM proposes only per-turn deltas; these functions apply them.

HULL_MAX = 100
SHIELDS_MAX = 100


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def apply_ship_status(
    current_hull: int,
    current_shields: int,
    hull_change: int,
    shields_change: int,
) -> tuple[int, int]:
    """Apply one turn's hull/shields deltas to the persistent ship status.

    Returns (hull, shields) clamped to [0, HULL_MAX] / [0, SHIELDS_MAX].
    Missing/invalid deltas count as 0 (no change). Hull reaching 0 means
    the ship is destroyed — main.py ends the game on that.
    """
    hull = max(0, min(HULL_MAX, _to_int(current_hull, HULL_MAX) + _to_int(hull_change, 0)))
    shields = max(0, min(SHIELDS_MAX, _to_int(current_shields, SHIELDS_MAX) + _to_int(shields_change, 0)))
    return hull, shields


def apply_systems_offline(
    current: list[str],
    taken_offline: list[str],
    restored: list[str],
) -> list[str]:
    """Apply one turn's systems deltas to the persistent offline-systems list.

    Systems named in ``restored`` are removed, systems named in
    ``taken_offline`` are added. Returns a new unique list in stable order
    (existing order preserved, newly offlined systems appended). The input
    is not mutated.
    """
    restored_set = {str(s) for s in restored or []}
    result = [s for s in current or [] if s not in restored_set]
    for s in taken_offline or []:
        if s not in result:
            result.append(s)
    return result


# ── Doom clock: threat level ───────────────────────────────────────
# Threat is code-owned state: it grows every turn by CODE, never by the
# LLM, so a game can be lost even when the narrative is optimistic.
# Hesitation (auto-selected actions), a critically damaged hull and
# mission stagnation accelerate the clock; reaching THREAT_MAX ends the
# game (main.py calls end_game("overwhelmed")).

THREAT_MAX = 100
THREAT_BASE_TICK = 8
THREAT_AUTO_PENALTY = 5
THREAT_HULL_PENALTY = 3
THREAT_HULL_CRITICAL_RATIO = 0.4
THREAT_STAGNATION_PENALTY = 2


def _to_ratio(value: Any, default: float) -> float:
    """Coerce to a finite float ratio clamped to [0, 1]; invalid → default."""
    try:
        r = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(r):
        return default
    return max(0.0, min(1.0, r))


def compute_threat_tick(
    current_threat: Any,
    *,
    auto_ratio: float = 0.0,
    hull_ratio: float = 1.0,
    mission_stagnant: bool = False,
) -> int:
    """Advance the doom clock by one turn.

    Formula (all constants named above for balancing):
    - +THREAT_BASE_TICK every turn;
    - +int(THREAT_AUTO_PENALTY * auto_ratio) — share of auto-selected
      actions among the turn's player decisions;
    - +THREAT_HULL_PENALTY when hull_ratio < THREAT_HULL_CRITICAL_RATIO;
    - +THREAT_STAGNATION_PENALTY when the turn did not advance the mission.

    Returns the new threat clamped to [0, THREAT_MAX]. Invalid inputs fall
    back to safe defaults (threat 0, no auto penalty, intact hull).
    """
    threat = max(0, min(THREAT_MAX, _to_int(current_threat, 0)))
    tick = THREAT_BASE_TICK
    tick += int(THREAT_AUTO_PENALTY * _to_ratio(auto_ratio, 0.0))
    if _to_ratio(hull_ratio, 1.0) < THREAT_HULL_CRITICAL_RATIO:
        tick += THREAT_HULL_PENALTY
    if mission_stagnant:
        tick += THREAT_STAGNATION_PENALTY
    return max(0, min(THREAT_MAX, threat + tick))


# ── VS risk reweighting (combined outcome) ─────────────────────────
# Verbalized Sampling lets the LLM assign its own probabilities to the
# outcome options, so catastrophic options get p=0.05 and are never
# sampled. These functions make outcome risk depend on STATE (damaged
# hull, high threat, reckless decisions) instead of the LLM's taste:
# badness is computed from an option's structural fields, and the
# state-derived risk factor amplifies the probability of bad options.

RISK_AMPLIFY = 3.0
RISK_PROB_CAP = 0.6
BADNESS_DEATH_WEIGHT = 0.34
BADNESS_INJURY_WEIGHT = 0.17


def option_badness(option: Any) -> float:
    """0..1 harm score of a combined-outcome option's structural fields.

    Sums: ship_hull_change < 0 (|delta|/50), ship_shields_change < 0
    (|delta|/50), BADNESS_DEATH_WEIGHT per dead crew member,
    BADNESS_INJURY_WEIGHT per injured crew member, and the worst negative
    mission-points entry (|points|/4, max over entries). Clamped to
    [0, 1]. Missing/invalid fields count as no harm.
    """
    if not isinstance(option, dict):
        return 0.0
    score = 0.0
    hull = _to_int(option.get("ship_hull_change"), 0)
    if hull < 0:
        score += abs(hull) / 50.0
    shields = _to_int(option.get("ship_shields_change"), 0)
    if shields < 0:
        score += abs(shields) / 50.0
    dead = option.get("dead_crew_members")
    if isinstance(dead, list):
        score += BADNESS_DEATH_WEIGHT * len(dead)
    injured = option.get("crew_injured")
    if isinstance(injured, list):
        score += BADNESS_INJURY_WEIGHT * len(injured)
    worst_regression = 0
    regressions = option.get("mission_progress")
    for entry in regressions if isinstance(regressions, list) else []:
        if not isinstance(entry, dict):
            continue
        points = _to_int(entry.get("points"), 0)
        if points < 0:
            worst_regression = max(worst_regression, abs(points))
    score += worst_regression / 4.0
    return max(0.0, min(1.0, score))


def reweight_probabilities(
    probabilities: list[float],
    badness: list[float],
    risk_factor: float,
) -> list[float]:
    """Amplify the probabilities of bad options by the state risk factor.

    p_i *= (1 + RISK_AMPLIFY * risk_factor * badness_i), then renormalized
    to sum 1 with no option above RISK_PROB_CAP. Empty/invalid input
    (mismatched lengths, non-numeric or negative probabilities, zero-sum
    probabilities, risk_factor outside [0, 1]) returns a copy of the input.
    """
    if not isinstance(probabilities, list) or not isinstance(badness, list):
        return list(probabilities)
    if not probabilities or len(probabilities) != len(badness):
        return list(probabilities)
    try:
        probs = [float(p) for p in probabilities]
        bads = [float(b) for b in badness]
        risk = float(risk_factor)
    except (TypeError, ValueError):
        return list(probabilities)
    if not all(math.isfinite(x) for x in [*probs, *bads, risk]):
        return list(probabilities)
    if any(p < 0 for p in probs) or sum(probs) <= 0 or not 0.0 <= risk <= 1.0:
        return list(probabilities)
    if len(probs) == 1:
        return [1.0]
    weights = [p * (1.0 + RISK_AMPLIFY * risk * b) for p, b in zip(probs, bads)]
    return _renormalize_capped(weights)


def _renormalize_capped(weights: list[float]) -> list[float]:
    """Scale weights to sum 1 with no entry above RISK_PROB_CAP.

    Iterative water-filling: entries that would exceed the cap are pinned
    at it and the remainder is redistributed proportionally among the
    rest. With len(weights) >= 2 the cap is always feasible
    (2 * 0.6 > 1), so at most one entry is ever over the cap.
    """
    result = [0.0] * len(weights)
    free = list(range(len(weights)))
    remaining = 1.0
    while free:
        free_total = sum(weights[i] for i in free)
        if free_total <= 0:
            share = remaining / len(free)
            for i in free:
                result[i] = share
            break
        over = [i for i in free if remaining * weights[i] / free_total > RISK_PROB_CAP + 1e-9]
        if not over:
            for i in free:
                result[i] = remaining * weights[i] / free_total
            break
        for i in over:
            result[i] = RISK_PROB_CAP
            remaining -= RISK_PROB_CAP
        free = [i for i in free if i not in over]
    return result


def compute_risk_factor(
    *,
    hull_ratio: Any,
    threat_level: Any,
    reckless_ratio: Any,
) -> float:
    """0..1: how dangerous the current situation is.

    0.5*(1 - hull_ratio) + 0.4*(threat/100) + 0.6*reckless_ratio,
    clamped to [0, 1]. Invalid inputs fall back to safe defaults
    (intact hull, threat 0, no recklessness).
    """
    hull = _to_ratio(hull_ratio, 1.0)
    threat = _to_ratio(_to_int(threat_level, 0) / 100.0, 0.0)
    reckless = _to_ratio(reckless_ratio, 0.0)
    return max(0.0, min(1.0, 0.5 * (1.0 - hull) + 0.4 * threat + 0.6 * reckless))


# ── Wound escalation: injuries accumulate up to death ──────────────
# Wound severity is persistent code-owned state; the LLM proposes only the
# severity of each NEW wound. These functions resolve the incoming wound
# against the stored severity: the result never drops below either side of
# the ladder, and a critically wounded character who takes ANY new wound
# dies — wounds cannot accumulate forever.

WOUND_SEVERITIES = ("minor", "moderate", "critical")
WOUND_DEAD = "dead"


def _wound_rank(severity: Any) -> int:
    """Ladder rank of a severity: healthy (None/"healthy"/"") = -1.

    Unknown values rank as "minor" (rank 0) per the module style: silent
    normalization in pure functions, no loggers here.
    """
    if severity in (None, "", "healthy"):
        return -1
    return WOUND_SEVERITIES.index(severity) if severity in WOUND_SEVERITIES else 0


def resolve_injury(current_severity: str | None, incoming_severity: str) -> str:
    """Resolve one new wound against the stored severity (pure, no DB).

    Ladder: None/"healthy" → "minor" → "moderate" → "critical" → "dead".
    - the result is max(current, incoming) on the ladder — a new wound
      never heals or downgrades the stored one;
    - the result is never below the incoming wound;
    - current "critical" + ANY new wound → WOUND_DEAD ("dead"): the
      character dies of accumulated wounds (main.py applies the death).
    """
    current_rank = _wound_rank(current_severity)
    if current_rank >= WOUND_SEVERITIES.index("critical"):
        return WOUND_DEAD
    # max(0, ...) — a crew_injured event is itself a wound, so the floor
    # is "minor" even when both sides read healthy.
    return WOUND_SEVERITIES[max(0, current_rank, _wound_rank(incoming_severity))]


# ── Crew deaths ─────────────────────────────────────────────────────
# The game can be brutal: every death the LLM assigns in dead_crew_members is
# applied as-is — no per-turn cap, no cooldown. A second death channel is
# mechanical: a critically wounded character who takes any new wound dies
# (resolve_injury returns "dead"). If the whole crew dies, the crew_wiped
# check in main.py ends the game.

# ── NPC loyalty: code-owned morale that can end in mutiny ─────────
# Loyalty is persistent code-owned state (npc_profiles.loyalty, 0-100):
# the LLM never touches it. Every turn _analyze_turn_outcome collects
# the turn's losses (deaths, hull damage, mission regressions, heals)
# and applies compute_loyalty_change to every active NPC. Two active
# NPCs at loyalty <= MUTINY_LOYALTY_THRESHOLD means open mutiny —
# main.py ends the game with end_game("mutiny").

LOYALTY_DEATH_PENALTY = 8
LOYALTY_DEATHS_CAP = 16
LOYALTY_HULL_DAMAGE_PENALTY = 5
LOYALTY_HULL_DAMAGE_THRESHOLD = 15
LOYALTY_MISSION_GAIN = 3
LOYALTY_MISSION_LOSS = 4
LOYALTY_HEAL_BONUS = 2
LOYALTY_HEAL_CAP = 4
LOYALTY_CHANGE_MIN = -25
LOYALTY_CHANGE_MAX = 7

MUTINY_LOYALTY_THRESHOLD = 15
MUTINY_MIN_DISAFFECTED = 2

LOYALTY_BAND_STEADFAST = "steadfast"
LOYALTY_BAND_UNEASY = "uneasy"
LOYALTY_BAND_ON_EDGE = "on_edge"
LOYALTY_BAND_MUTINOUS = "mutinous"
LOYALTY_STEADFAST_MIN = 70
LOYALTY_UNEASY_MIN = 40
LOYALTY_ON_EDGE_MIN = 20


def compute_loyalty_change(*, deaths_count=0, hull_damage=0, mission_delta=0, healed_count=0) -> int:
    """One turn's loyalty delta from the turn's facts (pure, no DB).

    Formula (all constants named above for balancing):
    - -LOYALTY_DEATH_PENALTY per death this turn, capped at
      -LOYALTY_DEATHS_CAP;
    - -LOYALTY_HULL_DAMAGE_PENALTY when hull_damage (the turn's absolute
      hull loss) is >= LOYALTY_HULL_DAMAGE_THRESHOLD;
    - +LOYALTY_MISSION_GAIN when mission_delta (sum of the turn's mission
      points) is positive, -LOYALTY_MISSION_LOSS when negative;
    - +LOYALTY_HEAL_BONUS per healed NPC, capped at LOYALTY_HEAL_CAP.

    The result is clamped to [LOYALTY_CHANGE_MIN, LOYALTY_CHANGE_MAX].
    Invalid inputs count as 0 per field, per the module style.
    """
    deaths = max(0, _to_int(deaths_count, 0))
    hull = max(0, _to_int(hull_damage, 0))
    mission = _to_int(mission_delta, 0)
    healed = max(0, _to_int(healed_count, 0))

    change = max(-LOYALTY_DEATHS_CAP, -LOYALTY_DEATH_PENALTY * deaths)
    if hull >= LOYALTY_HULL_DAMAGE_THRESHOLD:
        change -= LOYALTY_HULL_DAMAGE_PENALTY
    if mission > 0:
        change += LOYALTY_MISSION_GAIN
    elif mission < 0:
        change -= LOYALTY_MISSION_LOSS
    change += min(LOYALTY_HEAL_CAP, LOYALTY_HEAL_BONUS * healed)
    return max(LOYALTY_CHANGE_MIN, min(LOYALTY_CHANGE_MAX, change))


def mutiny_conditions(loyalties: list[int]) -> bool:
    """True when enough active NPCs are at or below MUTINY_LOYALTY_THRESHOLD.

    Invalid entries count as loyal (LOYALTY_CHANGE_MAX-clamped _to_int
    fallback 100), so garbage data can never trigger a mutiny.
    """
    disaffected = sum(1 for v in loyalties if _to_int(v, 100) <= MUTINY_LOYALTY_THRESHOLD)
    return disaffected >= MUTINY_MIN_DISAFFECTED


def loyalty_band(loyalty: int) -> str:
    """Morale band token for display: steadfast / uneasy / on_edge / mutinous.

    Thresholds: >= LOYALTY_STEADFAST_MIN, >= LOYALTY_UNEASY_MIN,
    >= LOYALTY_ON_EDGE_MIN, below that mutinous. Mirrors the loyalty
    rule bands used in the NPC decision prompt (prompts.py).
    """
    v = _to_int(loyalty, 0)
    if v >= LOYALTY_STEADFAST_MIN:
        return LOYALTY_BAND_STEADFAST
    if v >= LOYALTY_UNEASY_MIN:
        return LOYALTY_BAND_UNEASY
    if v >= LOYALTY_ON_EDGE_MIN:
        return LOYALTY_BAND_ON_EDGE
    return LOYALTY_BAND_MUTINOUS


# ── NPC pool ────────────────────────────────────────────────────────
# Ship seats not taken by players are filled by NPCs at game start, but only
# up to NPC_COUNT seats. A bounded pool keeps crew_wiped reachable: with an
# NPC for every unfilled role (plus an NPC replacing every kicked or dead
# player) the crew could never die out. NPC_SEAT_ROLES picks the roles that
# give a small crew the most variety, in fill-priority order.

NPC_COUNT = 4
NPC_SEAT_ROLES = ["chief_engineer", "medical_officer", "pilot", "science_officer"]


def select_npc_role_keys(available_role_keys: list[str]) -> list[str]:
    """Choose up to NPC_COUNT unfilled role keys to fill with NPCs.

    Prefers NPC_SEAT_ROLES in their priority order, then any other unfilled
    roles in the order given (canonical role order). The input is not mutated.
    """
    picked = [r for r in NPC_SEAT_ROLES if r in available_role_keys]
    for role_key in available_role_keys:
        if len(picked) >= NPC_COUNT:
            break
        if role_key not in picked:
            picked.append(role_key)
    return picked[:NPC_COUNT]


# ── Outcome matrix: the five game-over verdicts ────────────────────
# The finale verdict is computed by CODE from the end-state; the LLM only
# writes the narrative in the tone the verdict demands. Ordering from
# flawless to failure: triumph > victory > pyrrhic > stalemate > defeat.
# A completed mission is NOT an automatic victory: finishing it with the
# ship destroyed or the crew wiped is a pyrrhic outcome.

OUTCOME_TRIUMPH = "triumph"
OUTCOME_VICTORY = "victory"
OUTCOME_PYRRHIC = "pyrrhic"
OUTCOME_STALEMATE = "stalemate"
OUTCOME_DEFEAT = "defeat"

TRIUMPH_HULL_RATIO = 0.6
TRIUMPH_CREW_RATIO = 0.7
TRIUMPH_MAX_THREAT = 70
VICTORY_HULL_RATIO = 0.3
VICTORY_CREW_RATIO = 0.4
STALEMATE_PROGRESS_RATIO = 0.6


def compute_outcome_type(
    *,
    mission_completed: Any,
    mission_progress_ratio: Any,
    hull_ratio: Any,
    alive_crew_ratio: Any,
    threat_level: Any,
    ship_destroyed: Any,
    crew_wiped: Any,
) -> str:
    """Compute the game-over verdict from the end-state (pure, no LLM).

    Mission completed:
    - triumph: hull_ratio >= TRIUMPH_HULL_RATIO AND alive_crew_ratio >=
      TRIUMPH_CREW_RATIO AND threat < TRIUMPH_MAX_THREAT (flawless win);
    - victory: hull_ratio >= VICTORY_HULL_RATIO AND alive_crew_ratio >=
      VICTORY_CREW_RATIO;
    - pyrrhic otherwise — including hull <= 0 and a wiped crew: the
      mission was finished at any price.

    Mission not completed:
    - defeat when ship_destroyed or crew_wiped;
    - stalemate when threat reached THREAT_MAX while
      mission_progress_ratio >= STALEMATE_PROGRESS_RATIO (did not finish
      in time, but survived and withdrew with part of the objective);
    - defeat otherwise.

    Invalid inputs fall back to safe defaults (mission not completed,
    intact hull, full crew, threat 0, no progress), per the module style.
    """
    completed = bool(mission_completed)
    hull = _to_ratio(hull_ratio, 1.0)
    crew = _to_ratio(alive_crew_ratio, 1.0)
    threat = max(0, min(THREAT_MAX, _to_int(threat_level, 0)))
    progress = _to_ratio(mission_progress_ratio, 0.0)

    if completed:
        if hull >= TRIUMPH_HULL_RATIO and crew >= TRIUMPH_CREW_RATIO and threat < TRIUMPH_MAX_THREAT:
            return OUTCOME_TRIUMPH
        if hull >= VICTORY_HULL_RATIO and crew >= VICTORY_CREW_RATIO:
            return OUTCOME_VICTORY
        return OUTCOME_PYRRHIC

    if bool(ship_destroyed) or bool(crew_wiped):
        return OUTCOME_DEFEAT
    if threat >= THREAT_MAX and progress >= STALEMATE_PROGRESS_RATIO:
        return OUTCOME_STALEMATE
    return OUTCOME_DEFEAT
