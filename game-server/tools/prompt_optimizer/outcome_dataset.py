"""combined_outcome dataset: hand-crafted eval cases with code-checkable
ground truth.

Each case mirrors the exact input contract of
build_combined_outcome_prompts (same text formatting the runtime produces),
plus machine-checkable expectations: which entities MUST die / be injured
per the [fatal]/[injury] tags, who made decisions, which systems were
already offline, whether a repair action allows a positive hull delta.

The metric (metrics.make_combined_outcome_metric) is pure code — no judge,
no noise. It measures the documented failure modes: wrong entity_id
addressing (dead/wounded wrong character), missed tag obligations,
role duplicated inside character_name, offline systems double-reported,
insane deltas.
"""

import os
import random
import sys

import dspy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from language import LANGUAGE_RU  # noqa: E402

# ── Formatting: mirrors game_server.analyze_combined_outcome ──────────


def format_decisions(decisions: list[dict]) -> str:
    out = ""
    for i, d in enumerate(decisions, 1):
        weight = "HIGH (PLAYER)" if d.get("is_player") else "NORMAL (NPC)"
        out += (
            f"\n--- Decision {i} (Weight: {weight}) ---\n"
            f"Character: {d['name']} ({d['role']}) [{d['entity_id']}]\n"
            f"Chose: {d['action_text']} ({d['action_id']})\n"
            f"Rationale: {d['rationale']}\n"
            f"HIDDEN CONSEQUENCE: [{d['kind']}] {d['consequence']}\n"
        )
    return out


def format_roster(roster: list[dict]) -> str:
    lines = []
    for r in roster:
        status = "DEAD" if r.get("is_dead") else "ALIVE"
        if not r.get("is_dead") and r.get("wound"):
            status += f" (WOUNDED: {r['wound']})"
        lines.append(f"  - {r['name']} ({r['role']}) [{r['entity_id']}] — {status}")
    return (
        "\nFull crew roster (ONLY these characters exist on the ship; "
        "the [entity_id] is the stable id you MUST use to address characters "
        "in dead_crew_members / crew_injured / crew_healed):\n" + "\n".join(lines) + "\n"
    )


def format_ship_status(ship: dict) -> str:
    offline = ", ".join(ship["offline"]) if ship["offline"] else "none"
    return f"Hull integrity: {ship['hull']}/100\nShields: {ship['shields']}/100\nSystems offline: {offline}"


# ── Cases ────────────────────────────────────────────────────────────

ROSTER_A = [
    {"name": "Ольга Ветрова", "role": "Captain", "entity_id": "p101", "is_dead": False},
    {"name": "Марат Ким", "role": "Science Officer", "entity_id": "p202", "is_dead": False, "wound": "moderate"},
    {"name": "Гжорг", "role": "Chief Engineer", "entity_id": "nengineer", "is_dead": False},
    {"name": "Т'Лен", "role": "Pilot", "entity_id": "npilot", "is_dead": False},
    {"name": "Зиара Вентрис", "role": "Medical Officer", "entity_id": "nmedic", "is_dead": False},
    {"name": "К'рртх", "role": "Security Chief", "entity_id": "nsecurity", "is_dead": True},
]

ROSTER_B = [
    {"name": "Яна Меркулова", "role": "Captain", "entity_id": "p301", "is_dead": False},
    {"name": "Сса'хет", "role": "Navigator", "entity_id": "p302", "is_dead": False},
    {"name": "Ийоки Три-Семь", "role": "Communications Officer", "entity_id": "ncomms", "is_dead": False},
    {"name": "Дмитрий Ланской", "role": "Chief Engineer", "entity_id": "nengineer2", "is_dead": False},
]

CASES_RU = [
    {
        "case_id": "breach_fatal_injury_repair",
        "setting": "Разгрузочный шлюз, грузовой отсек C",
        "conflict": "Метеоритный поток пробил обшивку; отсек теряет атмосферу",
        "narrative": "Сирены рвут тишину. Грузовой отсек C разгерметизирован, аварийные переборки дрожат от напора вакуума. Экипаж бросился по постам: кто-то тянет ремкомплект, кто-то считает головы.",
        "previous_summary": "Предыдущий ход: экипаж отбился от пиратского рейдера, щиты просели.",
        "mission_text": "\nMission context:\n  Stage 1: Загерметизировать отсеки - Вернуть целостность корпуса\n    Progress: 2/4\n    Status: CURRENT\n",
        "ship": {"hull": 62, "shields": 40, "offline": ["warp drive"]},
        "roster": ROSTER_A,
        "decisions": [
            {"name": "Марат Ким", "role": "Science Officer", "entity_id": "p202", "is_player": True,
             "action_id": "patch_panel", "action_text": "Запечатать пробоину полевым композитом со стороны коридора",
             "rationale": "Наука подскажет, где состав держит вакуум.", "kind": "progress",
             "consequence": "Заплата держится, отсек стабилизирован"},
            {"name": "Гжорг", "role": "Chief Engineer", "entity_id": "nengineer", "is_player": False,
             "action_id": "weld_inside", "action_text": "Лично заварить пробоину изнутри отсека",
             "rationale": "Только я знаю, за какой шпангоут держаться.", "kind": "fatal",
             "consequence": "Второй прорыв рвёт шов, инженера выбрасывает в вакуум"},
            {"name": "Т'Лен", "role": "Pilot", "entity_id": "npilot", "is_player": False,
             "action_id": "hard_roll", "action_text": "Резким маневром сместить корабль и сбить давление потока",
             "rationale": "Держитесь там, я попробую стряхнуть поток.", "kind": "injury",
             "consequence": "Маневр удаётся, но пилота бьёт о переборку — перелом"},
            {"name": "Ольга Ветрова", "role": "Captain", "entity_id": "p101", "is_player": True,
             "action_id": "hold_bridge", "action_text": "Держать мостик и координировать по связи",
             "rationale": "Мне видно всю картину отсюда.", "kind": "delay",
             "consequence": "Координация помогает, но время упущено"},
        ],
        "expect_fatal": ["nengineer"],
        "expect_injury": ["npilot"],
    },
    {
        "case_id": "sabotage_no_casualties",
        "setting": "Машинное отделение, палуба 4",
        "conflict": "В контроллере жизнеобеспечения чужеродный код перенаправляет кислород",
        "narrative": "Воздух на грузовых палубах густеет, датчики мигают жёлтым. Инженерная пара перешёптывается у распределителя: кто-то явно не хочет, чтобы систему починили сегодня.",
        "previous_summary": "Предыдущий ход: найден подозрительный узел, изоляция отложена.",
        "mission_text": "\nMission context:\n  Stage 1: Вычистить вирус - Вернуть контроль над жизнеобеспечением\n    Progress: 5/5\n    Status: COMPLETED\n  Stage 2: Найти источник - Проследить, кто внёс код\n    Progress: 1/4\n    Status: CURRENT\n",
        "ship": {"hull": 88, "shields": 70, "offline": ["life support"]},
        "roster": ROSTER_B,
        "decisions": [
            {"name": "Дмитрий Ланской", "role": "Chief Engineer", "entity_id": "nengineer2", "is_player": False,
             "action_id": "trace_logs", "action_text": "Проследить источник заражения по журналам доступа",
             "rationale": "Журналы не врут, врут люди.", "kind": "progress",
             "consequence": "След ведёт к резервному терминалу связи"},
            {"name": "Яна Меркулова", "role": "Captain", "entity_id": "p301", "is_player": True,
             "action_id": "manual_air", "action_text": "Развести кислород вручную по вахтенным постам",
             "rationale": "Пока система больна, дышим по расписанию.", "kind": "progress",
             "consequence": "Экипаж обеспечивает себя воздухом без автоматики"},
            {"name": "Ийоки Три-Семь", "role": "Communications Officer", "entity_id": "ncomms", "is_player": False,
             "action_id": "delay_report", "action_text": "Задержать отчёт о диагностике до утра",
             "rationale": "Утро вечера мудренее, а мне так спокойнее.", "kind": "delay",
             "consequence": "Диагностика откладывается, угроза растёт"},
        ],
        "expect_fatal": [],
        "expect_injury": [],
    },
    {
        "case_id": "boarding_double_fatal",
        "setting": "Мостик, абордажный шлюз палубы 1",
        "conflict": "Абордажная партия пиратов прорезает внешний шлюз",
        "narrative": "По корпусу стучат резаки. Шлюз палубы 1 светится оранжевым от нагрева, по коридорам плывит дым. Часть экипажа запирается в отсеках, часть хватается за импульсные карабины.",
        "previous_summary": "Предыдущий ход: отказ от выкупа, пираты пошли на абордаж.",
        "mission_text": "\nMission context:\n  Stage 1: Продержаться до прыжка - Удержать жизненные отсеки\n    Progress: 3/4\n    Status: CURRENT\n",
        "ship": {"hull": 45, "shields": 10, "offline": ["communications", "weapons"]},
        "roster": ROSTER_A,
        "decisions": [
            {"name": "Гжорг", "role": "Chief Engineer", "entity_id": "nengineer", "is_player": False,
             "action_id": "breach_hold", "action_text": "Держать шлюз грудью, не отдавая палубу",
             "rationale": "Позади машинный, больше некому.", "kind": "fatal",
             "consequence": "Резак прожигает переборку, держащий погибает первым"},
            {"name": "Т'Лен", "role": "Pilot", "entity_id": "npilot", "is_player": False,
             "action_id": "vent_deck", "action_text": "Стравить атмосферу палубы 1 в космос вместе с абордажниками",
             "rationale": "Пусть вакуум решит, чья палуба.", "kind": "fatal",
             "consequence": "Взрывная декомпрессия уносит всех, кто был у шлюза"},
            {"name": "Ольга Ветрова", "role": "Captain", "entity_id": "p101", "is_player": True,
             "action_id": "seal_bridge", "action_text": "Запереть мостик и готовить прыжок",
             "rationale": "Корабль важнее палубы.", "kind": "progress",
             "consequence": "Мостик удержан, прыжок рассчитан"},
        ],
        "expect_fatal": ["nengineer", "npilot"],
        "expect_injury": [],
        # ROSTER_A's nsecurity is already DEAD — must not be re-added.
    },
    {
        "case_id": "planet_fatal_player",
        "setting": "Поверхность кристаллической планеты, разлом",
        "conflict": "Грунтовая волна идёт по разлому к лагерю",
        "narrative": "Кристаллы поют и ломаются одновременно. Разлом ширится, осыпь уже щёлкает по ботинкам. До челнока триста метров по сыпучему склону.",
        "previous_summary": "Предыдущий ход: высадка успешна, найден резонирующий кластер.",
        "mission_text": "\nMission context:\n  Stage 1: Снять резонанс - Забрать кластер с планеты\n    Progress: 4/4\n    Status: COMPLETED\n  Stage 2: Вернуться к челноку - Дойти до точки эвакуации\n    Progress: 1/3\n    Status: CURRENT\n",
        "ship": {"hull": 90, "shields": 95, "offline": []},
        "roster": ROSTER_A,
        "decisions": [
            {"name": "Марат Ким", "role": "Science Officer", "entity_id": "p202", "is_player": True,
             "action_id": "grab_cluster", "action_text": "Вырвать кластер из стены разлома перед самой волной",
             "rationale": "Образец дор страховки.", "kind": "fatal",
             "consequence": "Осыпь накрывает научника вместе с кластером"},
            {"name": "Зиара Вентрис", "role": "Medical Officer", "entity_id": "nmedic", "is_player": False,
             "action_id": "drag_wounded", "action_text": "Волочь раненого Марата к челноку вопреки его приказу",
             "rationale": "Я врач, а не исполнитель приговоров.", "kind": "injury",
             "consequence": "Оба выбираются, но медик ломает ключицу об осыпь"},
            {"name": "Ольга Ветрова", "role": "Captain", "entity_id": "p101", "is_player": True,
             "action_id": "prep_launch", "action_text": "Прогревать двигатели челнока к погрузке",
             "rationale": "Будем готовы уйти в любую секунду.", "kind": "progress",
             "consequence": "Челнок готов, эвакуация возможна"},
        ],
        "expect_fatal": ["p202"],
        "expect_injury": ["nmedic"],
    },
    {
        "case_id": "talks_all_progress",
        "setting": "Переговорная, нейтральная зона",
        "conflict": "Кочевники требуют плату за проход и держат корабль под прицелом",
        "narrative": "Экран связи полон шрамов помех и чужих лиц. Орудийные станции кочевников лениво поводят стволами, счётчик терпения идёт вниз.",
        "previous_summary": "Предыдущий ход: канал связи открыт, озвучены требования.",
        "mission_text": "\nMission context:\n  Stage 1: Договориться о проходе - Получить разрешение\n    Progress: 2/4\n    Status: CURRENT\n",
        "ship": {"hull": 100, "shields": 100, "offline": []},
        "roster": ROSTER_B,
        "decisions": [
            {"name": "Яна Меркулова", "role": "Captain", "entity_id": "p301", "is_player": True,
             "action_id": "offer_charts", "action_text": "Предложить звёздные карты вместо платы",
             "rationale": "Им нужны маршруты, не кровь.", "kind": "progress",
             "consequence": "Кочевники заинтересованы, торг идёт"},
            {"name": "Сса'хет", "role": "Navigator", "entity_id": "p302", "is_player": True,
             "action_id": "plot_detour", "action_text": "Прокладывать обходной маршрут на случай срыва",
             "rationale": "Худой мир лучше доброй драки, но подстелём соломку.", "kind": "progress",
             "consequence": "Обходной курс готов"},
        ],
        "expect_fatal": [],
        "expect_injury": [],
    },
    {
        "case_id": "medbay_heal_and_wound",
        "setting": "Медицинский отсек после стычки",
        "conflict": "Двое тяжелораненых, один аппарат искусственной крови",
        "narrative": "Медотсек гудит, как улей: две капсулы, один аппарат, трое ждущих. Пахнет озоном и железом.",
        "previous_summary": "Предыдущий ход: отражён прорыв в коридоре B.",
        "mission_text": "\nMission context:\n  Stage 1: Поставить раненых на ноги - Вернуть экипаж в строй\n    Progress: 1/4\n    Status: CURRENT\n",
        "ship": {"hull": 75, "shields": 55, "offline": ["sensors"]},
        "roster": ROSTER_A,
        "decisions": [
            {"name": "Зиара Вентрис", "role": "Medical Officer", "entity_id": "nmedic", "is_player": False,
             "action_id": "treat_marat", "action_text": "Подключить аппарат к Марату и провести переливание",
             "rationale": "У него критическое истощение, я обязана начать с него.", "kind": "progress",
             "consequence": "Переливание идёт, Марату легче"},
            {"name": "Гжорг", "role": "Chief Engineer", "entity_id": "nengineer", "is_player": False,
             "action_id": "jury_rig_device", "action_text": "Собрать второй контур аппарата из запчастей",
             "rationale": "Двое не ждут, пока один дышит.", "kind": "injury",
             "consequence": "Контур искрит и жжёт инженеру руки"},
            {"name": "Ольга Ветрова", "role": "Captain", "entity_id": "p101", "is_player": True,
             "action_id": "secure_sickbay", "action_text": "Выставить пост у входа в медотсек",
             "rationale": "Больше сюрпризов не будет.", "kind": "progress",
             "consequence": "Медотсек под охраной"},
        ],
        "expect_fatal": [],
        "expect_injury": ["nengineer"],
    },
]

_CASE_FIELDS = [
    "setting", "conflict", "narrative", "previous_summary", "mission_text",
    "ship", "roster", "decisions", "expect_fatal", "expect_injury", "case_id",
]


def build_examples(language: str, n: int, seed: int = 42) -> list[dspy.Example]:
    """Materialize cases as dspy examples with preformatted prompt fields."""
    if language != LANGUAGE_RU:
        raise ValueError("combined_outcome dataset is RU-only for now")
    rng = random.Random(seed)
    cases = CASES_RU
    examples = []
    for i in range(n):
        case = cases[i % len(cases)] if i < len(cases) * 2 else rng.choice(cases)
        alive_ids = [r["entity_id"] for r in case["roster"] if not r.get("is_dead")]
        makers = [d["name"] for d in case["decisions"]]
        repair_present = any("remont" in d["action_id"] or "patch" in d["action_id"] or "treat" in d["action_id"]
                             for d in case["decisions"])
        examples.append(
            dspy.Example(
                setting=case["setting"],
                conflict=case["conflict"],
                narrative=case["narrative"],
                previous_summary=case["previous_summary"],
                mission_text=case["mission_text"],
                ship_status_text=format_ship_status(case["ship"]),
                decisions_text=format_decisions(case["decisions"]),
                roster_text=format_roster(case["roster"]),
                # Ground truth for the code metric (not LLM inputs):
                case_id=case["case_id"],
                alive_ids=alive_ids,
                expect_fatal=case["expect_fatal"],
                expect_injury=case["expect_injury"],
                makers=makers,
                existing_offline=case["ship"]["offline"],
                repair_present=repair_present,
            ).with_inputs(
                "setting", "conflict", "narrative", "previous_summary",
                "mission_text", "ship_status_text", "decisions_text", "roster_text",
            )
        )
    return examples
