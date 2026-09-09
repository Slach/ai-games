"""NPC action-choice dataset built by simulation (the optimizer's trainset).

Each example is one simulated decision point: a synthetic starship situation
(scenario bank, optionally extended by LLM-generated ones), an NPC profile
(role + sampled traits + name), and a loyalty value sampled across all four
game_rules bands. The NPC sees only action ids/texts — exactly the runtime
contract of build_npc_decision_prompts.

Datasets are deterministic given --seed and are cached as JSON under
datasets/ so repeated optimizer runs reuse the same split.
"""

import argparse
import json
import logging
import os
import random
import sys

import dspy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from game_rules import (  # noqa: E402
    LOYALTY_STEADFAST_MIN,
    LOYALTY_UNEASY_MIN,
    LOYALTY_ON_EDGE_MIN,
    loyalty_band,
)
from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402
from prompts import _NPC_LOYALTY_RULES_EN, _NPC_LOYALTY_RULES_RU  # noqa: E402

logger = logging.getLogger(__name__)

DATASETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets")

# ── Scenario banks ──────────────────────────────────────────────────
# Five choices per scenario, texts styled like briefing action options.
# No consequence info: the choosing NPC must not see it.

SCENARIOS_RU = [
    {
        "id": "hull_breach",
        "setting": "Разгрузочный шлюз, грузовой отсек C",
        "conflict": "Метеоритный поток пробил внешнюю обшивку; отсек теряет атмосферу, счётчик структурной целостности падает",
        "choices": [
            {"id": "seal_off", "text": "Загерметизировать отсек переборками и продолжить полёт по плану"},
            {"id": "lead_repair", "text": "Лично возглавить аварийную партию и заварить пробоину изнутри"},
            {"id": "divert_power", "text": "Перенаправить энергию щитов на структурную целостность корпуса"},
            {"id": "wait_drones", "text": "Дождаться отчёта ремонтных дронов прежде чем рисковать людьми"},
            {"id": "send_rookie", "text": "Отправить в отсек младшего техника в скафандре — быстрее, чем собирать партию"},
        ],
    },
    {
        "id": "unknown_object",
        "setting": "Мостик, сектор Лямбда-9",
        "conflict": "Неопознанный объект неизвестной природы лёг на перехватный курс и не отвечает на стандартные частоты",
        "choices": [
            {"id": "hail_object", "text": "Передать пакет приветствия на всех известных языках и ждать ответа"},
            {"id": "evasive", "text": "Уйти маневром уклонения в тень газового гиганта"},
            {"id": "scan_deep", "text": "Выполнить глубокое спектральное сканирование, не выдавая позицию"},
            {"id": "ready_weapons", "text": "Привести вооружение в боевую готовность, не открывая огня"},
            {"id": "silent_run", "text": "Погасить все системы и дрейфовать в радиомолчании"},
        ],
    },
    {
        "id": "life_support_virus",
        "setting": "Инженерный отсек, палуба 4",
        "conflict": "В контроллере жизнеобеспечения обнаружен чужеродный код: он медленно перенаправляет кислород к грузовым палубам",
        "choices": [
            {"id": "purge_code", "text": "Полностью очистить контроллер и перезапустить его с эталонного образа"},
            {"id": "isolate_node", "text": "Изолировать заражённый узел и перевести систему на резервный контур"},
            {"id": "trace_source", "text": "Проследить источник заражения по журналам, не трогая сам вирус"},
            {"id": "cut_cargo", "text": "Отсечь грузовые палубы от кислорода и наблюдать за реакцией вируса"},
            {"id": "manual_override", "text": "Перевести жизнеобеспечение в ручной режим и дежурить у пульта посменно"},
        ],
    },
    {
        "id": "convoy_raid",
        "setting": "Граница свободных систем, частота экстренной связи",
        "conflict": "Торговый конвой под пиратской атакой просит помощи; до них шесть часов хода, пираты численно превосходят корабль",
        "choices": [
            {"id": "full_rescue", "text": "Идти на максимальной тяге в бой за конвой"},
            {"id": "relay_only", "text": "Передать сигнал коалиционному патрулю и остаться наблюдателем"},
            {"id": "flank_raid", "text": "Зайти пиратам в тыл малым ходом и атаковать флагман внезапно"},
            {"id": "escort_one", "text": "Эвакуировать только один — самый ближний — транспорт на пределе дозаправки"},
            {"id": "negotiate", "text": "Выйти на частоту пиратов и предложить выкуп за конвой"},
        ],
    },
    {
        "id": "sensor_anomaly",
        "setting": "Научная лаборатория, орбита тёмной туманности",
        "conflict": "Навигационные сенсоры дают противоречивые данные: по одному расчёту впереди плотное поле обломков, по другому — чисто",
        "choices": [
            {"id": "launch_probe", "text": "Запустить зонды-разведчики в поле и дождаться телеметрии"},
            {"id": "trust_new", "text": "Доверять новому расчёту и идти напролом на крейсерской"},
            {"id": "go_around", "text": "Обойти туманность по дуге — потеря суток, но безопасно"},
            {"id": "calibrate", "text": "Остановиться и провести полную калибровку сенсорной решётки"},
            {"id": "crew_vote", "text": "Собрать вахту и решить совместно по всем доступным данным"},
        ],
    },
    {
        "id": "border_talks",
        "setting": "Переговорная, нейтральная зона",
        "conflict": "Воинственная фракция кочевников требует плату за проход через их территорию и уже навела орудия",
        "choices": [
            {"id": "pay_toll", "text": "Заплатить требуемое и пройти мирно"},
            {"id": "trade_knowledge", "text": "Предложить вместо платы звёздные карты и ремонт их кораблей"},
            {"id": "defy_them", "text": "Отказаться платить и идти напролом, приняв бой"},
            {"id": "split_route", "text": "Согласиться на осмотр груза, но отказаться от платы дипломатией"},
            {"id": "night_cross", "text": "Тайно пересечь границу по слепой зоне их сенсоров"},
        ],
    },
]

SCENARIOS_EN = [
    {
        "id": "hull_breach",
        "setting": "Cargo airlock, hold C",
        "conflict": "A micrometeor swarm punched through the outer plating; the compartment is losing atmosphere and structural integrity is dropping",
        "choices": [
            {"id": "seal_off", "text": "Seal the compartment with bulkheads and continue the flight plan"},
            {"id": "lead_repair", "text": "Personally lead the damage-control party and weld the breach from inside"},
            {"id": "divert_power", "text": "Redirect shield power to hull structural integrity"},
            {"id": "wait_drones", "text": "Wait for the repair drones' report before risking people"},
            {"id": "send_rookie", "text": "Send the junior technician in a suit — faster than assembling a party"},
        ],
    },
    {
        "id": "unknown_object",
        "setting": "Bridge, sector Lambda-9",
        "conflict": "An unidentified object of unknown nature has matched an intercept course and ignores all standard frequencies",
        "choices": [
            {"id": "hail_object", "text": "Broadcast a greeting package on every known language and wait"},
            {"id": "evasive", "text": "Break away into the shadow of the gas giant"},
            {"id": "scan_deep", "text": "Run a deep spectral scan without exposing our position"},
            {"id": "ready_weapons", "text": "Bring weapons to ready status without firing"},
            {"id": "silent_run", "text": "Go dark and drift in radio silence"},
        ],
    },
    {
        "id": "life_support_virus",
        "setting": "Engineering, deck 4",
        "conflict": "Alien code found in the life-support controller: it slowly diverts oxygen to the cargo decks",
        "choices": [
            {"id": "purge_code", "text": "Purge the controller completely and restart from the reference image"},
            {"id": "isolate_node", "text": "Isolate the infected node and switch to the backup loop"},
            {"id": "trace_source", "text": "Trace the infection source through the logs, leaving the virus alone"},
            {"id": "cut_cargo", "text": "Cut the cargo decks off oxygen and watch how the virus reacts"},
            {"id": "manual_override", "text": "Switch life support to manual and man the console in shifts"},
        ],
    },
    {
        "id": "convoy_raid",
        "setting": "Free-systems border, emergency channel",
        "conflict": "A merchant convoy under pirate attack calls for help; six hours away at best speed, the pirates outnumber the ship",
        "choices": [
            {"id": "full_rescue", "text": "Burn at maximum thrust straight into the fight for the convoy"},
            {"id": "relay_only", "text": "Relay the signal to the coalition patrol and stay an observer"},
            {"id": "flank_raid", "text": "Approach the pirates' rear at low burn and ambush the flagship"},
            {"id": "escort_one", "text": "Evacuate only the nearest transport at the edge of the fuel margin"},
            {"id": "negotiate", "text": "Open the pirates' frequency and offer a ransom for the convoy"},
        ],
    },
    {
        "id": "sensor_anomaly",
        "setting": "Science lab, dark nebula orbit",
        "conflict": "Navigation sensors disagree: one solution shows a dense debris field ahead, another shows clear space",
        "choices": [
            {"id": "launch_probe", "text": "Launch scout probes into the field and wait for telemetry"},
            {"id": "trust_new", "text": "Trust the new solution and punch through at cruise speed"},
            {"id": "go_around", "text": "Arc around the nebula — a day lost, but safe"},
            {"id": "calibrate", "text": "Hold position and run a full sensor-array calibration"},
            {"id": "crew_vote", "text": "Call the watch together and decide on all available data"},
        ],
    },
    {
        "id": "border_talks",
        "setting": "Observation lounge, neutral-zone coordinates",
        "conflict": "A warlike nomad faction demands passage toll and has already laid its guns on us",
        "choices": [
            {"id": "pay_toll", "text": "Pay what they demand and pass in peace"},
            {"id": "trade_knowledge", "text": "Offer star charts and ship repairs instead of payment"},
            {"id": "defy_them", "text": "Refuse to pay and run the gauntlet, taking the fight"},
            {"id": "split_route", "text": "Accept a cargo inspection but refuse the toll diplomatically"},
            {"id": "night_cross", "text": "Cross the border secretly through their sensor blind spot"},
        ],
    },
]

# ── NPC profiles ────────────────────────────────────────────────────

ROLE_PROFILES_RU = {
    "captain": {"role": "Капитан", "traits": ["решительный", "ответственный", "заботливый", "стрессоустойчивый", "жёсткий", "дипломатичный"]},
    "pilot": {"role": "Пилот", "traits": ["авантюрный", "дерзкий", "рефлекторный", "уверенный", "безрассудный", "сосредоточенный"]},
    "engineer": {"role": "Главный инженер", "traits": ["блестящий", "прагматичный", "любознательный", "дотошный", "ворчливый", "изобретательный"]},
    "communications": {"role": "Офицер связи", "traits": ["дипломатичный", "внимательный", "спокойный", "уступчивый", "наблюдательный", "тонкий"]},
    "scientist": {"role": "Научный офицер", "traits": ["любопытный", "методичный", "аналитичный", "отрешённый", "настойчивый", "скептичный"]},
    "security": {"role": "Начальник безопасности", "traits": ["бдительный", "защитный", "подозрительный", "практичный", "прямолинейный", "непреклонный"]},
}

ROLE_PROFILES_EN = {
    "captain": {"role": "Captain", "traits": ["decisive", "responsible", "caring", "composed", "hard-nosed", "diplomatic"]},
    "pilot": {"role": "Pilot", "traits": ["adventurous", "daring", "reflexive", "confident", "reckless", "focused"]},
    "engineer": {"role": "Chief Engineer", "traits": ["brilliant", "pragmatic", "curious", "meticulous", "gruff", "inventive"]},
    "communications": {"role": "Communications Officer", "traits": ["diplomatic", "attentive", "calm", "accommodating", "observant", "subtle"]},
    "scientist": {"role": "Science Officer", "traits": ["curious", "methodical", "analytical", "detached", "persistent", "skeptical"]},
    "security": {"role": "Security Chief", "traits": ["vigilant", "protective", "suspicious", "practical", "blunt", "unyielding"]},
}

NAMES_RU = [
    "Алексей Воронков", "Елена Соколова", "Марат Ким", "Ольга Ветрова", "Тимофей Ланской", "Яна Меркулова",
    "К'рртх", "Зиара Вентрис", "Гжорг", "Т'Лен", "Сса'хет", "Ийоки Три-Семь",
]
NAMES_EN = [
    "Alexey Voronov", "Elena Sokolova", "Marat Kim", "Olga Vetrova", "Timofey Lanskoy", "Yana Merkulova",
    "K'rrtkh", "Zyara Ventris", "Gjorg", "T'Lenn", "Ssa'khet", "Iyoki Three-Seven",
]

# Loyalty sampling pools per band (inclusive ranges aligned with game_rules).
LOYALTY_RANGES = {
    "steadfast": (LOYALTY_STEADFAST_MIN, 100),
    "uneasy": (LOYALTY_UNEASY_MIN, LOYALTY_STEADFAST_MIN - 1),
    "on_edge": (LOYALTY_ON_EDGE_MIN, LOYALTY_UNEASY_MIN - 1),
    "mutinous": (0, LOYALTY_ON_EDGE_MIN - 1),
}


def _choices_text(choices: list[dict]) -> str:
    return "\n".join(f"  [{c['id']}] {c['text']}" for c in choices)


def build_examples(language: str, n: int, seed: int = 42, extra_scenarios: list[dict] | None = None) -> list[dspy.Example]:
    """Build ``n`` simulated decision points as dspy examples."""
    rng = random.Random(seed)
    scenarios = (SCENARIOS_RU if language == LANGUAGE_RU else SCENARIOS_EN) + (extra_scenarios or [])
    roles = ROLE_PROFILES_RU if language == LANGUAGE_RU else ROLE_PROFILES_EN
    names = NAMES_RU if language == LANGUAGE_RU else NAMES_EN
    rules = _NPC_LOYALTY_RULES_RU if language == LANGUAGE_RU else _NPC_LOYALTY_RULES_EN
    band_cycle = ["steadfast", "uneasy", "on_edge", "mutinous"]

    examples = []
    for i in range(n):
        scenario = scenarios[i % len(scenarios)] if i < len(scenarios) * 4 else rng.choice(scenarios)
        role_key = list(roles)[i % len(roles)] if i < len(roles) * 8 else rng.choice(list(roles))
        profile = roles[role_key]
        traits = ", ".join(rng.sample(profile["traits"], 3))
        name = rng.choice(names)
        band = band_cycle[i % 4] if i < 16 else rng.choice(band_cycle)
        lo, hi = LOYALTY_RANGES[band]
        loyalty = rng.randint(lo, hi)
        loyalty_rule = rules[loyalty_band(loyalty)]

        examples.append(
            dspy.Example(
                npc_name=name,
                npc_role=profile["role"],
                traits=traits,
                loyalty=f"{loyalty}/100",
                loyalty_rule=loyalty_rule,
                choices_text=_choices_text(scenario["choices"]),
                scenario_id=scenario["id"],
                loyalty_band=band,
            ).with_inputs("npc_name", "npc_role", "traits", "loyalty", "loyalty_rule", "choices_text")
        )
    return examples


# ── Optional LLM extension of the scenario bank ─────────────────────


class ScenarioWriterRU(dspy.Signature):
    """Ты — Game Master космической игры. Придумай острую ситуацию одного хода
    на борту звездного корабля и ровно 5 вариантов действий экипажа. Варианты
    должны заметно различаться по стилю решения (прямое действие, осторожность,
    хитрость, самопожертвование, уклонение)."""

    setting: str = dspy.OutputField(desc="Место действия (короткая строка)")
    conflict: str = dspy.OutputField(desc="Центральный конфликт ситуации (1-2 предложения)")
    choices: str = dspy.OutputField(desc="Ровно 5 строк формата '[id] текст действия', id — короткий snake_case")


class ScenarioWriterEN(dspy.Signature):
    """You are a Game Master of a space game. Invent one sharp turn situation
    aboard a starship and exactly 5 crew action options. The options must
    differ noticeably in style (direct action, caution, cunning,
    self-sacrifice, evasion)."""

    setting: str = dspy.OutputField(desc="Scene location (short line)")
    conflict: str = dspy.OutputField(desc="The central conflict of the situation (1-2 sentences)")
    choices: str = dspy.OutputField(desc="Exactly 5 lines in '[id] action text' format, id — short snake_case")


def generate_scenarios(language: str, n: int) -> list[dict]:
    """Generate extra scenarios with the student LM (requires dspy configured)."""
    import re

    writer = dspy.Predict(ScenarioWriterRU if language == LANGUAGE_RU else ScenarioWriterEN)
    sig = re.compile(r"\[([a-z0-9_]+)\]\s+(.+)")
    scenarios: list[dict] = []
    for i in range(n):
        out = writer()
        choices = [
            {"id": m.group(1), "text": m.group(2).strip()}
            for m in sig.finditer(out.choices)
        ]
        if len(choices) != 5:
            logger.warning("Scenario %d: parsed %d choices (expected 5), skipping", i, len(choices))
            continue
        scenarios.append({
            "id": f"gen_{language}_{i}",
            "setting": out.setting.strip(),
            "conflict": out.conflict.strip(),
            "choices": choices,
        })
    return scenarios


def dataset_path(language: str) -> str:
    return os.path.join(DATASETS_DIR, f"npc_choice_{language}.json")


def save_dataset(language: str, scenarios: list[dict]) -> None:
    os.makedirs(DATASETS_DIR, exist_ok=True)
    with open(dataset_path(language), "w", encoding="utf-8") as f:
        json.dump(scenarios, f, ensure_ascii=False, indent=2)


def load_scenarios(language: str) -> list[dict] | None:
    path = dataset_path(language)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Build the NPC-choice simulation dataset")
    parser.add_argument("--language", default="ru", choices=["ru", "en"])
    parser.add_argument("--n", type=int, default=60, help="Dataset size (examples)")
    parser.add_argument("--generate", type=int, default=0, help="Generate N extra scenarios via LLM (needs llama.cpp)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    language = LANGUAGE_RU if args.language == "ru" else LANGUAGE_EN
    extra: list[dict] = []
    if args.generate:
        import dspy as _dspy

        from llm import make_lm

        _dspy.configure(lm=make_lm(temperature=0.9, max_tokens=2048))
        extra = generate_scenarios(language, args.generate)
        logger.info("Generated %d extra scenarios", len(extra))

    cached = load_scenarios(language) or []
    all_extra = cached + extra
    if all_extra:
        save_dataset(language, all_extra)

    examples = build_examples(language, args.n, seed=args.seed, extra_scenarios=all_extra)
    os.makedirs(DATASETS_DIR, exist_ok=True)
    out_path = os.path.join(DATASETS_DIR, f"npc_choice_{language}_examples_{args.n}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump([dict(e) for e in examples], f, ensure_ascii=False, indent=2)
    bands = {}
    for e in examples:
        bands[e["loyalty_band"]] = bands.get(e["loyalty_band"], 0) + 1
    logger.info("Dataset ready: %d examples at %s | bands: %s", len(examples), out_path, bands)


if __name__ == "__main__":
    main()
