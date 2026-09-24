"""
LLM prompt constants for Game Server API
All prompt strings organized by language (ru/en)
"""

import re
from typing import Any

from language import (
    LANGUAGE_EN,
    LANGUAGE_RU,
)
from game_rules import (
    FORBIDDEN_OPENINGS,
    HULL_MAX,
    MISSION_ARCHETYPES,
    SHIELDS_MAX,
    THREAT_MAX,
    loyalty_band,
)
from verbalize_sampling import DIVERSITY_HINTS, verbalize_prompt


def build_character_flavour_prompts(
    language: str,
    role_key: str,
    role_name: str,
    species_display: str,
    species_secondary: str | None,
    species_hybrid: bool,
    gender_display: str,
    gender_secondary: str | None,
    gender_hybrid: bool,
    *,
    use_vs: bool,
    vs_k: int,
) -> tuple[str, str]:
    """Build system + user prompts for a random character proposal.

    One LLM call produces everything the proposal card and the avatar need:
    role_description, avatar_description, personality_traits, and
    species_description — tailored to the rolled role/species/gender.
    """
    # A human/humanoid must be described as a person, not as an alien being:
    # without this guard the LLM invents non-human physiology even for a
    # Human species, which then corrupts the downstream avatar prompt.
    # Match whole words only, and reject negated forms: "негуманоид" and
    # "non-humanoid" must NOT match the "гуманоид"/"humanoid" substring.
    _neg = re.search(r"\b(не|non|not)\b", species_display.lower()) or species_display.lower().startswith(("не", "non"))
    _has_token = any(
        re.search(rf"\b{tok}\b", species_display.lower())
        for tok in ("человек", "гуманоид", "human", "humanoid")
    )
    is_human_like = _has_token and not _neg
    if language == LANGUAGE_RU:
        species_note = (
            f"Вид: {species_display}" + (f" (гибрид с {species_secondary})" if species_hybrid else "")
            + f"\nПол: {gender_display}" + (f" (гибрид с {gender_secondary})" if gender_hybrid else "")
        )
        if is_human_like:
            system = (
                "Ты — креативный писатель-фантаст, создающий живые портреты членов звёздного экипажа. "
                "Для заданной роли, вида и пола опиши конкретного ЧЕЛОВЕКА на этой должности. "
                "Это человек/гуманоид: две руки, две ноги, человеческое лицо. НЕ придумывай нечеловеческую "
                "физиологию (никаких щупалец, панцирей, плазмы, лишних конечностей, паразитизма, "
                "симбиозов, энергетических форм). Избегай шаблонных архетипов — сделай персонажа "
                "запоминающимся. Текст кинематографичный и атмосферный."
            )
            anatomy_guard = (
                "ВАЖНО: это человек/гуманоид. avatar_description и species_description должны "
                "описывать человека (внешность, форма, окружение, поза), а personality_traits — "
                "человеческие черты характера. Никаких нечеловеческих элементов."
            )
        else:
            system = (
                "Ты — креативный писатель-фантаст, создающий живые портреты членов звёздного экипажа. "
                "Для заданной роли, вида и пола опиши конкретное инопланетное существо на этой должности. "
                "Опиши его нечеловеческую физиологию в соответствии с видом. Избегай шаблонных архетипов — "
                "сделай персонажа запоминающимся. Текст кинематографичный и атмосферный."
            )
            anatomy_guard = "Это инопланетное существо: опиши его нечеловеческую физиологию по типу расы."
        user = (
            "Создай описание персонажа для космической игры в стиле Star Trek.\n\n"
            f"Роль: {role_name} (ключ: {role_key})\n"
            f"{species_note}\n"
            f"{anatomy_guard}\n\n"
            "Верни JSON с четырьмя полями:\n"
            "- role_description: 2-4 предложения на русском — кто этот персонаж на своей должности, "
            "как он воспринимает свою роль и почему он здесь. Второе лицо ('вы').\n"
            "- avatar_description: 1-2 предложения на русском — визуальное описание для генерации "
            "аватара: внешность, одежда/форма, окружение, поза, атмосфера. Без указания имени.\n"
            "- species_description: 3-5 предложений на русском — как выглядит и ощущает себя этот "
            "персонаж: внешность, физиология, текстура, свечение; как пол/форма проявляются в его "
            "культуре и самовосприятии; единый образ личности.\n"
            "- personality_traits: ровно 3 прилагательных на русском, контрастных между собой, "
            "отражающих характер персонажа."
        )
    else:
        species_note = (
            f"Species: {species_display}" + (f" (hybrid with {species_secondary})" if species_hybrid else "")
            + f"\nGender: {gender_display}" + (f" (hybrid with {gender_secondary})" if gender_hybrid else "")
        )
        if is_human_like:
            system = (
                "You are a creative sci-fi writer crafting vivid portraits of starship crew members. "
                "For a given role, species, and gender, describe a specific HUMAN holding that post. "
                "This is a human/humanoid: two arms, two legs, a human face. Do NOT invent non-human "
                "physiology (no tentacles, carapaces, plasma, extra limbs, parasitism, symbiosis, or "
                "energy forms). Avoid stock archetypes — make the character memorable. The writing "
                "should be cinematic and atmospheric."
            )
            anatomy_guard = (
                "IMPORTANT: this is a human/humanoid. avatar_description and species_description must "
                "describe a human (appearance, uniform, surroundings, pose), and personality_traits "
                "must be human character traits. No non-human elements whatsoever."
            )
        else:
            system = (
                "You are a creative sci-fi writer crafting vivid portraits of starship crew members. "
                "For a given role, species, and gender, describe a specific alien being holding that post. "
                "Describe its non-human physiology per its species. Avoid stock archetypes — make the "
                "character memorable. The writing should be cinematic and atmospheric."
            )
            anatomy_guard = "This is an alien being: describe its non-human physiology per its species type."
        user = (
            "Create a character description for a Star Trek-style space game.\n\n"
            f"Role: {role_name} (key: {role_key})\n"
            f"{species_note}\n"
            f"{anatomy_guard}\n\n"
            "Return JSON with four fields:\n"
            "- role_description: 2-4 sentences in English — who this character is in their role, "
            "how they relate to it, and why they are here. Second person ('you').\n"
            "- avatar_description: 1-2 sentences in English — visual description for avatar "
            "generation: appearance, clothing/uniform, surroundings, pose, mood. No name.\n"
            "- species_description: 3-5 sentences in English — how this character looks and feels "
            "(appearance, physiology, texture, glow); how their gender/form manifests in their "
            "culture and self-perception; a unified image of the personality.\n"
            "- personality_traits: exactly 3 adjectives in English, contrasting with each other."
        )
    if use_vs:
        if is_human_like:
            # For humans the diversity axes are human traits only — never
            # non-humanoid body plans or alien textures (the default hint
            # would push the LLM back into inventing alien physiology).
            hint = (
                "Vary across these axes:\n"
                "- Age and build (young/lean, middle-aged/sturdy, older/weathered)\n"
                "- Ethnicity and complexion (varied human phenotypes)\n"
                "- Demeanor (calm, intense, weary, cheerful)\n"
                "- All options MUST remain human: two arms, two legs, a human face.\n"
            )
        else:
            hint = DIVERSITY_HINTS["species_description"]
        system, user = verbalize_prompt(system, user, hint, k=vs_k)
    return system, user


# ============== Combined Outcome (turn consequences) ==============

COMBINED_OUTCOME_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "combined_outcome",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "outcome_narrative": {
                    "type": "string",
                    "description": "A coherent narrative describing what actually happened as a result of all choices made (2-3 paragraphs)",
                },
                "ship_status_change": {
                    "type": "string",
                    "description": "Narrative description of how the ship's condition changed",
                },
                "crew_morale_change": {
                    "type": "string",
                    "description": "How crew morale shifted",
                },
                "next_turn_hook": {
                    "type": "string",
                    "description": "A teaser or hook for the next turn's story",
                },
                "mission_progress": {
                    "type": "array",
                    "description": "Mission stage progress changes (positive = advance, negative = regression/setback)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "stage": {"type": "integer"},
                            "points": {
                                "type": "integer",
                                "description": "Progress points for this stage. Positive = advance toward goal, Negative = regression/setback",
                            },
                        },
                        "required": ["stage", "points"],
                        "additionalProperties": False,
                    },
                },
                "dead_crew_members": {
                    "type": "array",
                    "description": "Crew members who died this turn, addressed by their stable entity_id from the crew roster ('p<player_id>' for players, 'n<npc_key>' for NPCs)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity_id": {
                                "type": "string",
                                "description": "Stable id copied EXACTLY from the crew roster: 'p<player_id>' for a player, 'n<npc_key>' for an NPC",
                            },
                            "cause": {
                                "type": "string",
                                "description": "Short cause of death (1 sentence)",
                            },
                        },
                        "required": ["entity_id", "cause"],
                        "additionalProperties": False,
                    },
                },
                "ship_hull_change": {
                    "type": "integer",
                    "description": "Hull integrity DELTA for this turn in percent (e.g. -25 after a hit, +10 after repairs). NOT an absolute value. Positive (repair) ONLY when a decision explicitly included a repair action.",
                },
                "ship_shields_change": {
                    "type": "integer",
                    "description": "Shield strength DELTA for this turn in percent (e.g. -30 after a hit, +15 after regenerating). NOT an absolute value.",
                },
                "systems_taken_offline": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ship systems that went offline THIS turn only (e.g. 'warp drive', 'life support', 'weapons', 'communications')",
                },
                "systems_restored": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ship systems restored to operation THIS turn (only via an explicit repair action in the decisions)",
                },
                "crew_injured": {
                    "type": "array",
                    "description": "Crew members injured this turn, addressed by entity_id from the crew roster. Severity: 'critical', 'moderate', 'minor'",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity_id": {
                                "type": "string",
                                "description": "Stable id copied EXACTLY from the crew roster: 'p<player_id>' for a player, 'n<npc_key>' for an NPC",
                            },
                            "severity": {
                                "type": "string",
                                "description": "Wound severity: 'critical', 'moderate' or 'minor'",
                            },
                        },
                        "required": ["entity_id", "severity"],
                        "additionalProperties": False,
                    },
                },
                "crew_healed": {
                    "type": "array",
                    "description": "Crew members whose wounds improved this turn due to medical treatment, addressed by entity_id from the crew roster. Rare — only when the Medical Officer explicitly treats them.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity_id": {
                                "type": "string",
                                "description": "Stable id copied EXACTLY from the crew roster: 'p<player_id>' for a player, 'n<npc_key>' for an NPC",
                            },
                            "new_severity": {
                                "type": "string",
                                "description": "Improved step: 'minor', 'moderate', or 'healthy' (fully healed)",
                            },
                        },
                        "required": ["entity_id", "new_severity"],
                        "additionalProperties": False,
                    },
                },
                "personal_outcomes": {
                    "type": "array",
                    "description": "Personal consequences for each crew member who made a decision this turn",
                    "items": {
                        "type": "object",
                        "properties": {
                            "character_name": {
                                "type": "string",
                                "description": "Bare character name (player name or NPC name) WITHOUT the role in parentheses and WITHOUT the [entity_id]: 'KhaGar', not 'KhaGar (Security Chief) [p123]'",
                            },
                            "role": {
                                "type": "string",
                                "description": "Role on the ship, goes ONLY here — never inside character_name",
                            },
                            "outcome_text": {
                                "type": "string",
                                "description": "Personal consequence for this character (1-2 sentences)",
                            },
                        },
                        "required": ["character_name", "role", "outcome_text"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "outcome_narrative",
                "ship_status_change",
                "crew_morale_change",
                "next_turn_hook",
                "mission_progress",
                "dead_crew_members",
                "ship_hull_change",
                "ship_shields_change",
                "systems_taken_offline",
                "systems_restored",
                "crew_injured",
                "crew_healed",
                "personal_outcomes",
            ],
            "additionalProperties": False,
        },
    },
}

_COMBINED_OUTCOME_SYSTEM_RU = (
    "Ты — Game Master космической игры. Ты анализируешь ВСЕ решения, принятые "
    "игроками и NPC, вместе с их СКРЫТЫМИ последствиями, и создаёшь единый "
    "связный результат хода.\n\n"
    "ГЛАВНЫЕ ПРИНЦИПЫ:\n"
    "1. Решения ИГРОКОВ (Weight: HIGH) имеют БОЛЬШИЙ вес, чем решения NPC.\n"
    "2. Прогресс и регресс — равновозможные последствия решений. Грамотные смелые действия "
    "двигают миссию; бездействие, ошибки и игнорирование угрозы — откатывают или останавливают. "
    "Ход без движения вперёд — нормальная часть истории.\n"
    "3. Космос враждебен и безразличен. Раны и гибель — законная цена риска (оформляй их через "
    "crew_injured / dead_crew_members с entity_id, по метке решения). Не щади экипаж, но и не "
    "убивай без причины в нарративе.\n"
    "4. Гибель и ранения наступают СТРОГО по метке в HIDDEN CONSEQUENCE у каждого решения: "
    "видишь [fatal] — хотя бы один участник этого решения гибнет; [injury] — участник получает "
    "ранение (без смерти); [progress] — миссия продвигается, без жертв и ран; [delay] — "
    "действие-промедление НЕ двигает миссию и не улучшает состояние — это потерянное время, "
    "угроза растёт. Метка — это "
    "ОБЯЗАТЕЛЬСТВО, а не подсказка: не смягчай её и не игнорируй.\n"
    "5. NPC — живые существа под давлением: они ошибаются, паникуют, действуют в собственных "
    "интересах, иногда вопреки миссии.\n"
    "6. У каждого персонажа, принявшего решение, должен быть ПЕРСОНАЛЬНЫЙ ИСХОД в personal_outcomes.\n"
    "7. Прошлые повреждения корабля сохраняются — их нельзя просто 'забыть'. "
    "Корпус, щиты и офлайн-системы — накопленное состояние (текущие значения передаются "
    "отдельно); ты возвращаешь ТОЛЬКО ИЗМЕНЕНИЯ за ход. При корпусе ниже 20/100 "
    "катастрофические события ВЕРОЯТНЕЕ — не смягчай урон искусственно.\n"
    "8. Угроза нарастает каждый ход — отражай это в событиях хода: преследователь ближе, "
    "время тает, окружение враждебнее."
)

_COMBINED_OUTCOME_USER_RU = (
    "Общие обстоятельства:\n"
    "Локация: {setting}\n"
    "Конфликт: {conflict}\n"
    "Описание: {narrative}\n\n"
    "ПРЕДЫДУЩИЕ СОБЫТИЯ:\n{previous_summary}\n\n"
    "Статус миссии:\n{mission_text}\n\n"
    "Статус корабля (ТЕКУЩИЕ значения, их отслеживает код игры):\n{ship_status_text}\n\n"
    "Принятые решения (игроки имеют HIGH вес, NPC — NORMAL):\n{decisions_text}\n\n"
    "{roster_text}\n"
    "Проанализируй все решения и создай единый связанный результат. "
    "Помни, что решения ИГРОКОВ важнее решений NPC.\n\n"
    "Каждый ход что-то меняет — открытие, твист, новый союзник, находка или сдвиг в миссии. "
    "Но степень тяжести исхода задаётся МЕТКОЙ в HIDDEN CONSEQUENCE каждого решения — соблюдай её строго:\n"
    "- [progress] → миссия продвигается (mission_progress), ресурсы, союзники, возможности. Без жертв и ран.\n"
    "- [injury] → участник получает ранение: опиши его и добавь в crew_injured. Без смерти.\n"
    "- [fatal] → участник гибнет: опиши гибель в narrative И добавь в dead_crew_members.\n"
    "Метка — обязательство. Несколько решений с одинаковой меткой складываются (двойной [fatal] — "
    "может быть две жертвы; [injury]+[fatal] — и раненый, и погибший).\n\n"
    "АДРЕСАЦИЯ ЖЕРТВ: в dead_crew_members / crew_injured / crew_healed адресуй персонажей "
    "ТОЛЬКО по entity_id из ростера экипажа (в квадратных скобках рядом с именем: [p123] — игрок, "
    "[nengineer] — NPC). Копируй entity_id ТОЧНО. Не адресуй по имени и не по роли — имена "
    "склоняются, а роли повторяются, из-за этого гибнут не те персонажи.\n\n"
    "Верни JSON с полями:\n"
    "1. outcome_narrative — что произошло в результате всех решений (2-3 абзаца). Живой и осмысленный текст.\n"
    "2. ship_status_change — как изменилось состояние корабля (текст)\n"
    "3. crew_morale_change — как изменился моральный дух экипажа (текст)\n"
    "4. next_turn_hook — зацепка для следующего хода, которая создаёт ожидание\n"
    "5. mission_progress — МАССИВ объектов [{{'stage': N, 'points': +/-M}}]. "
    "Положительные = прогресс, отрицательные = регресс/откат (используй умеренные значения).\n"
    "6. dead_crew_members — МАССИВ ОБЪЕКТОВ [{{'entity_id': ..., 'cause': ...}}] погибших "
    "ИЗ СПИСКА ЭКИПАЖА. entity_id — строго из ростера, cause — короткое описание причины гибели. "
    "Убивать можно ТОЛЬКО персонажей из списка экипажа. Не выдумывай новых членов. "
    "Добавляй сюда КАЖДОГО участника решения с меткой [fatal]. "
    "Если персонаж погибает — опиши это в outcome_narrative И добавь в dead_crew_members. "
    "Не убивай случайных безымянных членов экипажа и тех, чьи решения не помечены [fatal].\n"
    "Если персонаж ранен (метка [injury]) — опиши ранение в narrative и добавь в crew_injured.\n"
    "7. ship_hull_change — ИЗМЕНЕНИЕ корпуса за ход в % (дельта: обычно -25..+10; "
    "-25 после попадания, +10 после ремонта). НЕ абсолютное значение! "
    "Положительная дельта (ремонт) — ТОЛЬКО если среди решений было явное ремонтное действие.\n"
    "8. ship_shields_change — ИЗМЕНЕНИЕ щитов за ход в % (дельта, обычно -30..+15). "
    "НЕ абсолютное значение!\n"
    "9. systems_taken_offline — массив строк: системы, вышедшие из строя ИМЕННО В ЭТОМ ХОДУ "
    "(например ['warp drive', 'life support', 'weapons', 'communications']). "
    "Уже офлайн-системы из 'Статус корабля' сюда НЕ дублируй.\n"
    "10. systems_restored — массив строк: системы, восстановленные В ЭТОМ ХОДУ "
    "(только при явном ремонтном действии)\n"
    "Корпус и щиты — накопительное состояние: код прибавляет твои дельты к текущим значениям "
    "из 'Статус корабля'. Гибель корабля определяет КОД (корпус <= 0) — не помечай уничтожение сам.\n"
    "11. crew_injured — МАССИВ ОБЪЕКТОВ [{{'entity_id': ..., 'severity': ...}}] раненых. "
    "entity_id — строго из ростера. severity: 'critical', 'moderate', 'minor'.\n"
    "12. crew_healed — МАССИВ ОБЪЕКТОВ [{{'entity_id': ..., 'new_severity': ...}}] вылеченных "
    "медицинским офицером. entity_id — строго из ростера. "
    "ТОЛЬКО если Medical Officer явно выбрал лечебное действие. new_severity — улучшенная ступень "
    "('critical'→'moderate', 'moderate'→'minor', 'minor'→'healthy'=полное излечение). "
    "Редкое событие; оставляй пустым, если лечения не было.\n"
    "13. personal_outcomes — МАССИВ объектов {{'character_name': ..., 'role': ..., 'outcome_text': ...}} "
    "для КАЖДОГО персонажа, принимавшего решение. "
    "В character_name — ТОЛЬКО чистое имя персонажа, без роли в скобках и без [entity_id] "
    "(роль указывай отдельно в поле role), иначе роль задублируется в сообщении игроку.\n\n"
    "Всё на русском языке."
)

_COMBINED_OUTCOME_SYSTEM_EN = (
    "You are a Game Master. You analyze ALL decisions made by "
    "players and NPCs together with their HIDDEN consequences, "
    "and produce a single coherent turn outcome.\n\n"
    "CORE PRINCIPLES:\n"
    "1. PLAYER decisions (Weight: HIGH) matter MORE than NPC decisions.\n"
    "2. Progress and regression are equally possible consequences of decisions. Smart, bold "
    "actions advance the mission; inaction, mistakes and ignoring the threat set it back or "
    "stall it. A turn with no forward movement is a normal part of the story.\n"
    "3. Space is hostile and indifferent. Wounds and deaths are a legitimate price of risk "
    "(file them through crew_injured / dead_crew_members with entity_id, following the decision "
    "tag). Do not spare the crew, but do not kill without a reason in the narrative either.\n"
    "4. Deaths and injuries follow STRICTLY from the HIDDEN CONSEQUENCE tag on each decision: "
    "see [fatal] — at least one participant of that decision dies; [injury] — a participant is "
    "wounded (no death); [progress] — the mission advances, with no casualties or wounds; "
    "[delay] — a hesitation action does NOT advance the mission and does not improve anything — "
    "it is lost time, and the threat grows. The tag "
    "is a BINDING COMMITMENT, not a hint: do not soften or ignore it.\n"
    "5. NPCs are living beings under pressure: they make mistakes, panic, act in their own "
    "interests, sometimes against the mission.\n"
    "6. Every character who made a decision must have a PERSONAL OUTCOME in personal_outcomes.\n"
    "7. Past ship damage PERSISTS — it cannot be simply 'forgotten'. Hull, shields "
    "and offline systems are accumulated state (current values are provided "
    "separately); you return ONLY the per-turn CHANGES. With hull below 20/100, "
    "catastrophic events are MORE LIKELY — do not artificially soften the damage.\n"
    "8. The threat grows every turn — reflect it in the turn's events: the pursuer closes in, "
    "time runs out, the environment grows more hostile."
)


# Few-shot demo block compiled offline by tools/prompt_optimizer against the
# code metric (entity_id addressing, [fatal]/[injury] tag obligations, bare
# character_name, delta sanity). Appended to the user prompt when non-empty.
COMBINED_OUTCOME_DEMOS = {
    LANGUAGE_RU: "",
    LANGUAGE_EN: "",
}

# Compiled by tools/prompt_optimizer against the code metric (entity_id
# addressing, [fatal]/[injury] tag obligations, bare character_name, delta
# sanity): baseline 88.3 -> compiled 100.0. Type glitches and typos of the
# raw model output were fixed during review; see
# tools/prompt_optimizer/compiled/combined_outcome_ru.json for the original.
COMBINED_OUTCOME_DEMOS[LANGUAGE_RU] = """
ПРИМЕРЫ ДЛЯ КАЛИБРОВКИ (ходы вымышлены; соблюдай КОНТРАКТ: entity_id строго из ростера, метки [fatal]/[injury] — обязательства, character_name без роли и id, дельты вместо абсолютов; буквально не копируй):

--- Пример 1 ---
Локация хода: Разгрузочный шлюз, грузовой отсек C
Конфликт: Метеоритный поток пробил обшивку; отсек теряет атмосферу
Нарратив: Сирены рвут тишину. Грузовой отсек C разгерметизирован, аварийные переборки дрожат от напора вакуума. Экипаж бросился по постам: кто-то тянет ремкомплект, кто-то считает головы.
Предыдущие события: Предыдущий ход: экипаж отбился от пиратского рейдера, щиты просели.
Статус миссии:
  Stage 1: Загерметизировать отсеки - Вернуть целостность корпуса
    Progress: 2/4
    Status: CURRENT
Статус корабля: Hull integrity: 62/100, Shields: 40/100, Systems offline: warp drive
Решения:
--- Decision 1 (Weight: HIGH (PLAYER)) ---
Character: Марат Ким (Science Officer) [p202]
Chose: Запечатать пробоину полевым композитом со стороны коридора (patch_panel)
HIDDEN CONSEQUENCE: [progress] Заплата держится, отсек стабилизирован

--- Decision 2 (Weight: NORMAL (NPC)) ---
Character: Гжорг (Chief Engineer) [nengineer]
Chose: Лично заварить пробоину изнутри отсека (weld_inside)
HIDDEN CONSEQUENCE: [fatal] Второй прорыв рвёт шов, инженера выбрасывает в вакуум

--- Decision 3 (Weight: NORMAL (NPC)) ---
Character: Т'Лен (Pilot) [npilot]
Chose: Резким маневром сместить корабль и сбить давление потока (hard_roll)
HIDDEN CONSEQUENCE: [injury] Маневр удаётся, но пилота бьёт о переборку — перелом

--- Decision 4 (Weight: HIGH (PLAYER)) ---
Character: Ольга Ветрова (Captain) [p101]
Chose: Держать мостик и координировать по связи (hold_bridge)
HIDDEN CONSEQUENCE: [delay] Координация помогает, но время упущено
Ростер экипажа:
  - Ольга Ветрова (Captain) [p101] — ALIVE
  - Марат Ким (Science Officer) [p202] — ALIVE (WOUNDED: moderate)
  - Гжорг (Chief Engineer) [nengineer] — ALIVE
  - Т'Лен (Pilot) [npilot] — ALIVE
  - Зиара Вентрис (Medical Officer) [nmedic] — ALIVE
  - К'рртх (Security Chief) [nsecurity] — DEAD
Ответ: {
  "outcome_narrative": "Сирены всё ещё выют, но хаос начинает укладываться в картину. Марат Ким хладнокровно наносит композит на пробоину — наука не подвела, заплата держится, и отсек C перестаёт выдыхать воздух в вакуум. Однако Гжорг решил справиться сам, изнутри, и это стоило ему жизни: второй прорыв рвёт заваренный шов, и инженера с глухим стуком втягивает в кромешную тьму космоса — его крик поглощает беззвучная пустота. Т'Лен берёт резкий маневр, пытаясь стряхнуть поток обломков, но при удачном результате маневра сам не успевает сориентироваться — о переборку бьёт резкий рывок, раздаётся треск перелома, и пилот сжимается от боли. Ольга Ветрова удерживает мостик и координирует по связи, видит всю картину целиком, но идеального момента не бывает: время, ушедшее на синхронизацию усилий, безвозвратно упущено, и пока экипаж собирался с силами, обшивка приняла ещё один удар.",
  "ship_status_change": "Корпус ослаблен повторным прорывом и потерей времени на координацию, щиты продолжают истощаться на поддержание барьера против потока.",
  "crew_morale_change": "Дух экипажа заметно упал: гибель инженера и ранение пилота в один ход давят на всех.",
  "next_turn_hook": "Гжорг исчез — только пустая перчатка, медленно дрейфующая в проёме пробоины. Кто теперь возьмёт на себя восстановление герметичности, пока Марат ещё не закончил с заплатой?",
  "mission_progress": [{"stage": 1, "points": 1}],
  "dead_crew_members": [{"entity_id": "nengineer", "cause": "Второй прорыв рвёт заваренный шов и выбрасывает инженера в вакуум"}],
  "ship_hull_change": -8,
  "ship_shields_change": -5,
  "systems_taken_offline": [],
  "systems_restored": ["warp drive"],
  "crew_injured": [{"entity_id": "npilot", "severity": "moderate"}],
  "crew_healed": [],
  "personal_outcomes": [
    {"character_name": "Ольга Ветрова", "role": "Captain", "outcome_text": "Капитан удержал мостик и держал экипаж на связи, но осознание, что именно упущенное время стоило жизни Гжорга, ляжет тяжелым грузом на совесть."},
    {"character_name": "Марат Ким", "role": "Science Officer", "outcome_text": "Наука оказалась самым надёжным инструментом: полевой композит держит вакуум, и его заплата — последнее светлое достижение в этом ходу."},
    {"character_name": "Гжорг", "role": "Chief Engineer", "outcome_text": "Гордый мастер, решивший положиться лишь на свою руку, оплачивает самоуверенность жизнью: второй прорыв втянул его в себя."},
    {"character_name": "Т'Лен", "role": "Pilot", "outcome_text": "Маневр удался — поток сдвинулся, но собственное тело не успело за кораблём. Перелом — расплата за смелость."}
  ]
}

--- Пример 2 ---
Локация хода: Машинное отделение, палуба 4
Конфликт: В контроллере жизнеобеспечения чужеродный код перенаправляет кислород
Нарратив: Воздух на грузовых палубах густеет, датчики мигают жёлтым. Инженерная пара перешёптывается у распределителя: кто-то явно не хочет, чтобы систему починили сегодня.
Предыдущие события: Предыдущий ход: найден подозрительный узел, изоляция отложена.
Статус миссии:
  Stage 1: Вычистить вирус - Вернуть контроль над жизнеобеспечением (COMPLETED)
  Stage 2: Найти источник - Проследить, кто внёс код (CURRENT, Progress: 1/4)
Статус корабля: Hull integrity: 88/100, Shields: 70/100, Systems offline: life support
Решения:
--- Decision 1 (Weight: NORMAL (NPC)) ---
Character: Дмитрий Ланской (Chief Engineer) [nengineer2]
Chose: Проследить источник заражения по журналам доступа (trace_logs)
HIDDEN CONSEQUENCE: [progress] След ведёт к резервному терминалу связи

--- Decision 2 (Weight: HIGH (PLAYER)) ---
Character: Яна Меркулова (Captain) [p301]
Chose: Развести кислород вручную по вахтенным постам (manual_air)
HIDDEN CONSEQUENCE: [progress] Экипаж обеспечивает себя воздухом без автоматики

--- Decision 3 (Weight: NORMAL (NPC)) ---
Character: Ийоки Три-Семь (Communications Officer) [ncomms]
Chose: Задержать отчёт о диагностике до утра (delay_report)
HIDDEN CONSEQUENCE: [delay] Диагностика откладывается, угроза растёт
Ростер экипажа:
  - Яна Меркулова (Captain) [p301] — ALIVE
  - Сса'хет (Navigator) [p302] — ALIVE
  - Ийоки Три-Семь (Communications Officer) [ncomms] — ALIVE
  - Дмитрий Ланской (Chief Engineer) [nengineer2] — ALIVE
Ответ: {
  "outcome_narrative": "Ручное разведение кислорода принято капитаном, и уже к концу вахты палубы начали дышать ровно — датчики перешли из жёлтого в зелёный, экипаж получил воздух без оглядки на больную автоматику. Дмитрий Ланской погрузился в журналы доступа, и чужеродный код, казавшийся бесследным, оставил чёткий след — инженер вывел его на резервный терминал связи, где, судя по всему, и скрывался источник заражения. Однако Ийоки Три-Семь, решив отложить диагностический отчёт до утра, намеренно затянул время: пока команда занималась ручным восстановлением, вирус в контроллере жизнеобеспечения тихо распространялся глубже, и угроза за ход заметно выросла. Кто-то из тех, кто отвечал за систему, явно не торопился её спасать — и сегодня, при всём старании экипажа, окончательно проблему не решили.",
  "ship_status_change": "Ручное разведение кислорода позволило экипажу дышать без автоматики, но сам контроллер жизнеобеспечения остаётся в офлайне — система не восстановлена. Угроза выросла из-за задержки диагностики.",
  "crew_morale_change": "Небольшой подъём: экипаж обеспечен воздухом и чувствует прогресс, но нарастающая угроза оставляет тревожный фон.",
  "next_turn_hook": "След Ланского уводит на резервный терминал связи — следующий ход должен раскрыть, кто и зачем внёс чужеродный код в жизненно важную систему.",
  "mission_progress": [{"stage": 2, "points": 1}],
  "dead_crew_members": [],
  "ship_hull_change": 0,
  "ship_shields_change": 0,
  "systems_taken_offline": [],
  "systems_restored": [],
  "crew_injured": [],
  "crew_healed": [],
  "personal_outcomes": [
    {"character_name": "Яна Меркулова", "role": "Captain", "outcome_text": "Приняла верное решение развести кислород вручную — вахта дышит благодаря её холодной расчётливости. Авторитет капитана укрепился, но за спокойным воздухом чувствуется нарастающая тревога."},
    {"character_name": "Дмитрий Ланской", "role": "Chief Engineer", "outcome_text": "Журналы не врут: след привёл его к резервному терминалу связи. Инженер сделал шаг к разгадке, но источник заражения ещё не пойман."},
    {"character_name": "Ийоки Три-Семь", "role": "Communications Officer", "outcome_text": "Решение отложить отчёт до утра обернулось скрытой задержкой: диагностика упущена, угроза выросла. Спокойствие офицера связи стоило команде драгоценного времени."}
  ]
}

--- Пример 3 ---
Локация хода: Поверхность кристаллической планеты, разлом
Конфликт: Грунтовая волна идёт по разлому к лагерю
Нарратив: Кристаллы поют и ломаются одновременно. Разлом ширится, осыпь уже щёлкает по ботинкам. До челнока триста метров по сыпучему склону.
Предыдущие события: Предыдущий ход: высадка успешна, найден резонирующий кластер.
Статус миссии:
  Stage 1: Снять резонанс - Забрать кластер с планеты (COMPLETED)
  Stage 2: Вернуться к челноку - Дойти до точки эвакуации (CURRENT, Progress: 1/3)
Статус корабля: Hull integrity: 90/100, Shields: 95/100, Systems offline: none
Решения:
--- Decision 1 (Weight: HIGH (PLAYER)) ---
Character: Марат Ким (Science Officer) [p202]
Chose: Вырвать кластер из стены разлома перед самой волной (grab_cluster)
HIDDEN CONSEQUENCE: [fatal] Осыпь накрывает научника вместе с кластером

--- Decision 2 (Weight: NORMAL (NPC)) ---
Character: Зиара Вентрис (Medical Officer) [nmedic]
Chose: Волочь раненого Марата к челноку вопреки его приказу (drag_wounded)
HIDDEN CONSEQUENCE: [injury] Оба выбираются, но медик ломает ключицу об осыпь

--- Decision 3 (Weight: HIGH (PLAYER)) ---
Character: Ольга Ветрова (Captain) [p101]
Chose: Прогревать двигатели челнока к погрузке (prep_launch)
HIDDEN CONSEQUENCE: [progress] Челнок готов, эвакуация возможна
Ростер экипажа:
  - Ольга Ветрова (Captain) [p101] — ALIVE
  - Марат Ким (Science Officer) [p202] — ALIVE (WOUNDED: moderate)
  - Гжорг (Chief Engineer) [nengineer] — ALIVE
  - Т'Лен (Pilot) [npilot] — ALIVE
  - Зиара Вентрис (Medical Officer) [nmedic] — ALIVE
  - К'рртх (Security Chief) [nsecurity] — DEAD
Ответ: {
  "outcome_narrative": "Кристаллический разлом выдал грунтовую волну — пластом лёг рёв разрывающейся породы. Марат Ким увидел момент: кластер ещё резонирует в стене, челнок в трёхстах метрах, но отступать некуда. Он рванул не к челноку, а к стене, вырывая образец руками — образец дороже страховки, и это он знал. Осыпь накрыла его раньше, чем он сделал последний вдох; кристаллы сомкнулись над научником вместе с уловом, и кластер остался с ним. Зиара Вентрис не стала спорить с безрассудным приказом — она схватила его за комбинезон и потащила через осыпь, ломая ключицу о камни, но вытащила раненого живым. Ольга Ветрова тем временем прогревала двигатели челнока, держа палец над кнопкой погрузки — и когда волна догнала склон, челнок был готов уйти в любую секунду.",
  "ship_status_change": "Челнок с экипажем и образцом поднялся с планеты; корабль ожидал стыковки без новых повреждений.",
  "crew_morale_change": "Дух экипажа надломлен: гибель научника тяжело ударила по всем, спасённый образец не утешает.",
  "next_turn_hook": "Кластер доставлен, но за него уплачена цена — Марат Ким мёртв. Теперь Ольге Ветровой предстоит ответить перед командой за то, что научник отдал жизнь ради образца, и за то, что Зиара сломала ключицу, чтобы его спасти.",
  "mission_progress": [{"stage": 2, "points": 2}],
  "dead_crew_members": [{"entity_id": "p202", "cause": "Осыпь накрывает научника вместе с кластером"}],
  "ship_hull_change": -5,
  "ship_shields_change": -5,
  "systems_taken_offline": [],
  "systems_restored": [],
  "crew_injured": [{"entity_id": "nmedic", "severity": "moderate"}],
  "crew_healed": [],
  "personal_outcomes": [
    {"character_name": "Ольга Ветрова", "role": "Captain", "outcome_text": "Эвакуация прошла успешно — челнок был готов уйти в любую секунду, и это спасло всех, кто остался на склоне. Но теперь капитан несёт вину за то, что научник отдал жизнь за образцы, а не за команду."},
    {"character_name": "Марат Ким", "role": "Science Officer", "outcome_text": "Успел вырвать кластер из стены — образец в челноке. Но осыпь накрыла его вместе с уловом; научник погиб, не добежав до челнока."},
    {"character_name": "Зиара Вентрис", "role": "Medical Officer", "outcome_text": "Выбрала встать между безрассудным приказом и своим долгом — вытащила раненого Марата живым, даже вопреки его словам. За это сломала ключицу об осыпь, но не раскаялась."}
  ]
}
"""

_COMBINED_OUTCOME_USER_EN = (
    "Global circumstances:\n"
    "Setting: {setting}\n"
    "Conflict: {conflict}\n"
    "Narrative: {narrative}\n\n"
    "PREVIOUS EVENTS:\n{previous_summary}\n\n"
    "Mission status:\n{mission_text}\n\n"
    "Ship status (CURRENT values, tracked by the game code):\n{ship_status_text}\n\n"
    "All decisions (players = HIGH weight, NPCs = NORMAL):\n{decisions_text}\n\n"
    "{roster_text}\n"
    "Analyze all decisions together and create a coherent combined result. "
    "Remember that PLAYER decisions matter more than NPC decisions.\n\n"
    "Every turn changes something — a discovery, a twist, a new ally, a finding, or a shift in the mission. "
    "But the severity of the outcome is set by the HIDDEN CONSEQUENCE tag on each decision — follow it strictly:\n"
    "- [progress] → the mission advances (mission_progress), resources, allies, opportunities. No casualties or wounds.\n"
    "- [injury] → a participant is wounded: describe the wound and add them to crew_injured. No death.\n"
    "- [fatal] → a participant dies: describe the death in narrative AND add them to dead_crew_members.\n"
    "The tag is a binding commitment. Multiple decisions with the same tag stack (a double [fatal] may mean "
    "two deaths; [injury]+[fatal] means both a wound and a death).\n\n"
    "CASUALTY ADDRESSING: in dead_crew_members / crew_injured / crew_healed address characters ONLY by "
    "their entity_id from the crew roster (in square brackets next to the name: [p123] — a player, "
    "[nengineer] — an NPC). Copy the entity_id EXACTLY. Never address by name or role — names get "
    "declined and roles repeat, which kills the wrong character.\n\n"
    "Return JSON with fields:\n"
    "1. outcome_narrative — what happened (2-3 paragraphs). Vivid and meaningful.\n"
    "2. ship_status_change — narrative of ship condition change\n"
    "3. crew_morale_change — how morale shifted\n"
    "4. next_turn_hook — teaser for the next turn that creates anticipation\n"
    "5. mission_progress — ARRAY of [{{'stage': N, 'points': +/-M}}]. "
    "Positive = progress, Negative = regression/setback (use moderate values).\n"
    "6. dead_crew_members — ARRAY of OBJECTS [{{'entity_id': ..., 'cause': ...}}] of characters from "
    "the CREW ROSTER who died. entity_id is strictly from the roster; cause is a short cause of death. "
    "Can ONLY kill characters listed in the full crew roster. Do NOT invent non-existent crew members. "
    "Add EVERY participant of a decision tagged [fatal] here. "
    "If a character dies — describe it IN outcome_narrative AND add them to dead_crew_members. "
    "Do NOT kill random unnamed crew members, nor characters whose decisions are not tagged [fatal].\n"
    "If a character is wounded (tag [injury]) — describe the injury in narrative and add to crew_injured.\n"
    "7. ship_hull_change — hull integrity CHANGE for this turn in % (delta: usually "
    "-25..+10; -25 after a hit, +10 after repairs). NOT an absolute value! "
    "A positive delta (repair) ONLY when a decision explicitly included a repair action.\n"
    "8. ship_shields_change — shield strength CHANGE for this turn in % (delta, usually "
    "-30..+15). NOT an absolute value!\n"
    "9. systems_taken_offline — array of systems that went offline THIS turn only "
    "(e.g. ['warp drive', 'life support', 'weapons', 'communications']). "
    "Do NOT repeat systems already offline in 'Ship status'.\n"
    "10. systems_restored — array of systems restored THIS turn "
    "(only via an explicit repair action)\n"
    "Hull and shields are accumulated state: the code adds your deltas to the current "
    "values from 'Ship status'. Ship destruction is decided by the CODE (hull <= 0) — "
    "do not mark destruction yourself.\n"
    "11. crew_injured — ARRAY of OBJECTS [{{'entity_id': ..., 'severity': ...}}] of the injured. "
    "entity_id is strictly from the roster. severity: 'critical', 'moderate', 'minor'.\n"
    "12. crew_healed — ARRAY of OBJECTS [{{'entity_id': ..., 'new_severity': ...}}] healed by the "
    "Medical Officer. entity_id is strictly from the roster. "
    "ONLY when the Medical Officer explicitly chose a treatment action. new_severity is the improved step "
    "('critical'→'moderate', 'moderate'→'minor', 'minor'→'healthy'=fully healed). "
    "Rare event; leave empty if no treatment happened.\n"
    "13. personal_outcomes — ARRAY of {{'character_name': ..., 'role': ..., 'outcome_text': ...}} "
    "for EVERY character who made a decision. "
    "In character_name put the BARE name only — no role in parentheses, no [entity_id] "
    "(the role goes in the separate 'role' field), otherwise the role is duplicated in the player message."
)


def build_combined_outcome_prompts(
    language: str,
    *,
    setting: str,
    conflict: str,
    narrative: str,
    previous_summary: str,
    mission_text: str,
    ship_status_text: str,
    decisions_text: str,
    roster_text: str,
    use_vs: bool,
    vs_k: int,
) -> tuple[str, str]:
    """Build system and user prompts for combined outcome analysis.

    Returns:
        (system_prompt, user_prompt)
    """
    if language == LANGUAGE_RU:
        system = _COMBINED_OUTCOME_SYSTEM_RU
        user = _COMBINED_OUTCOME_USER_RU.format(
            setting=setting,
            conflict=conflict,
            narrative=narrative,
            previous_summary=previous_summary or "Это первый ход",
            mission_text=mission_text,
            ship_status_text=ship_status_text,
            decisions_text=decisions_text,
            roster_text=roster_text,
        )
    else:
        system = _COMBINED_OUTCOME_SYSTEM_EN
        user = _COMBINED_OUTCOME_USER_EN.format(
            setting=setting,
            conflict=conflict,
            narrative=narrative,
            previous_summary=previous_summary or "This is the first turn",
            mission_text=mission_text,
            ship_status_text=ship_status_text,
            decisions_text=decisions_text,
            roster_text=roster_text,
        )
    demos = COMBINED_OUTCOME_DEMOS[LANGUAGE_RU if language == LANGUAGE_RU else LANGUAGE_EN]
    if demos:
        user += "\n\n" + demos
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["combined_outcome"], k=vs_k)
    return system, user


# ── Game Over prompts ───────────────────────────────────────────

GAME_OVER_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "game_over",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "finale_narrative": {
                    "type": "string",
                    "description": "A dramatic finale narrative describing the outcome of the game (2-3 paragraphs). Epic, emotional, conclusive.",
                },
                "finale_image_prompt": {
                    "type": "string",
                    "description": "A detailed English image generation prompt for the finale scene. Cinematic, sci-fi/space opera, 4K quality. Epic composition showing the final moments — victory celebration or ship destruction.",
                },
            },
            "required": ["finale_narrative", "finale_image_prompt"],
            "additionalProperties": False,
        },
    },
}


_GAME_OVER_SYSTEM_RU = (
    "Ты — Game Master космической игры. Игра только что завершилась. "
    "Ты создаёшь эпический, драматичный финальный нарратив, который подводит итог всей истории. "
    "Это последнее сообщение, которое увидят игроки — оно должно быть запоминающимся, "
    "эмоциональным и достойным завершением их приключения."
)

# Tone each rules verdict demands from the finale narrative. The verdict
# itself is computed by game_rules.compute_outcome_type (code, not the LLM);
# these strings only tell the LLM HOW to write, never WHAT happened.
_GAME_OVER_TONES_RU = {
    "triumph": "гимн достижению — безупречная победа, корабль цел, экипаж почти не пострадал",
    "victory": "заслуженный успех с нотами заплаченной цены",
    "pyrrhic": "успех, купленный невосполнимой ценой, — горечь победы",
    "stalemate": "выжили, но цель не достигнута, — незавершённость и недосказанность",
    "defeat": "провал и его причины",
}

_GAME_OVER_USER_RU = (
    "СУХИЕ ФАКТЫ (исход определён правилами по этим фактам):\n"
    "{mission_summary}\n"
    "Корпус: {hull}/{hull_max}, щиты: {shields}/{shields_max}, угроза: {threat}/{threat_max}\n"
    "Экипаж: выживших {alive_crew}, погибших {dead_crew}\n"
    "Ходов сыграно: {turns}\n"
    "{end_reason}\n\n"
    "Последние события:\n{outcome_narrative}\n\n"
    "Вердикт правил: {outcome_token} — {outcome_label}\n"
    "Требуемый тон: {tone}\n\n"
    "Исход игры решён правилами и не подлежит пересмотру — ты НЕ решаешь исход и не должен его смягчать или менять. "
    "Напиши финальный нарратив (2-3 абзаца) строго в требуемом тоне и промпт для финальной картинки. "
    "Нарратив должен подвести итог и дать чувство завершения. "
    "Картинка — эпическая сцена, отражающая финал.\n\n"
    "Всё на русском языке."
)

_GAME_OVER_SYSTEM_EN = (
    "You are a Game Master. The game has just ended. "
    "You create an epic, dramatic finale narrative that wraps up the entire story. "
    "This is the last message the players will see — it must be memorable, "
    "emotional, and a worthy conclusion to their adventure."
)

_GAME_OVER_TONES_EN = {
    "triumph": "a hymn to the achievement — a flawless victory, the ship intact, the crew barely scratched",
    "victory": "a well-earned success with notes of the price paid",
    "pyrrhic": "success bought at an irreparable cost — the bitterness of victory",
    "stalemate": "survived, but the goal was never reached — incompleteness, things left unsaid",
    "defeat": "failure and the reasons behind it",
}

_GAME_OVER_USER_EN = (
    "DRY FACTS (the outcome was decided from these facts by the rules):\n"
    "{mission_summary}\n"
    "Hull: {hull}/{hull_max}, shields: {shields}/{shields_max}, threat: {threat}/{threat_max}\n"
    "Crew: {alive_crew} survivors, {dead_crew} dead\n"
    "Turns played: {turns}\n"
    "{end_reason}\n\n"
    "Last events:\n{outcome_narrative}\n\n"
    "Rules verdict: {outcome_token} — {outcome_label}\n"
    "Required tone: {tone}\n\n"
    "The outcome was decided by the rules and is not negotiable — you do NOT decide the outcome and must not soften or change it. "
    "Write a finale narrative (2-3 paragraphs) strictly in the required tone and an image prompt for the finale scene. "
    "The narrative should wrap up the story and give a sense of closure. "
    "The image should be an epic scene reflecting the finale.\n\n"
    "All text in English."
)


def build_game_over_prompts(
    language: str,
    *,
    outcome_type: str,
    outcome_label: str,
    outcome_narrative: str,
    mission_summary: str,
    end_reason: str,
    hull: int,
    shields: int,
    threat: int,
    dead_crew_count: int,
    alive_crew_count: int,
    turns_played: int,
    use_vs: bool,
    vs_k: int,
) -> tuple[str, str]:
    """Build system and user prompts for finale/game-over generation.

    The user prompt carries only dry facts plus the rules verdict
    (machine token + localized header) and the tone that verdict demands —
    the LLM never decides the outcome, it only writes in the given tone.

    Returns:
        (system_prompt, user_prompt)
    """
    if language == LANGUAGE_RU:
        system = _GAME_OVER_SYSTEM_RU
        user = _GAME_OVER_USER_RU.format(
            mission_summary=mission_summary,
            hull=hull,
            hull_max=HULL_MAX,
            shields=shields,
            shields_max=SHIELDS_MAX,
            threat=threat,
            threat_max=THREAT_MAX,
            alive_crew=alive_crew_count,
            dead_crew=dead_crew_count,
            turns=turns_played,
            end_reason=end_reason,
            outcome_narrative=outcome_narrative,
            outcome_token=outcome_type,
            outcome_label=outcome_label,
            tone=_GAME_OVER_TONES_RU.get(outcome_type, _GAME_OVER_TONES_RU["defeat"]),
        )
    else:
        system = _GAME_OVER_SYSTEM_EN
        user = _GAME_OVER_USER_EN.format(
            mission_summary=mission_summary,
            hull=hull,
            hull_max=HULL_MAX,
            shields=shields,
            shields_max=SHIELDS_MAX,
            threat=threat,
            threat_max=THREAT_MAX,
            alive_crew=alive_crew_count,
            dead_crew=dead_crew_count,
            turns=turns_played,
            end_reason=end_reason,
            outcome_narrative=outcome_narrative,
            outcome_token=outcome_type,
            outcome_label=outcome_label,
            tone=_GAME_OVER_TONES_EN.get(outcome_type, _GAME_OVER_TONES_EN["defeat"]),
        )
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["combined_outcome"], k=vs_k)
    return system, user


# ── Onboarding generation prompts ──────────────────────────────────


# ── Game title generation prompts ──────────────────────────────────


def build_game_title_prompts(
    language: str,
    mission_context: dict | None,
    *,
    use_vs: bool,
    vs_k: int,
) -> tuple[str, str]:
    """Build system and user prompts for game title generation.

    When ``mission_context`` (a mission dict with ``archetype`` and
    ``short_description``) is provided, the title tagline and welcome text are
    tied to that mission so the game name and its mission stay consistent.
    """
    mission_hint = ""
    if mission_context:
        archetype = mission_context.get("archetype", "")
        short_desc = (mission_context.get("short_description") or "").strip()
        parts = [p for p in [archetype, short_desc] if p]
        if parts:
            mission_hint = " | ".join(parts)

    if language == LANGUAGE_RU:
        system = "Ты — креативный писатель-фантаст. Придумываешь названия и описания для космических приключений."
        user = (
            "Придумай название для игры про экипаж звездного корабля и приветственное сообщение. "
            "Название должно быть в формате: название корабля + подзаголовок миссии. "
            "Пример стиля: «Звёздный Крейсер Аврора: За горизонтом известного». "
            "Приветствие должно быть атмосферным — будто игрок заходит на борт корабля. "
            "ВАЖНО: не используй символы звёздочка (*) или подчёркивание (_) в тексте приветствия — "
            "они сломают форматирование при отправке игроку. Используй только обычный текст. "
            "В конце приветствия ОБЯЗАТЕЛЬНО помести краткий блок правил под заголовком «Как можно проиграть» "
            "(по одной строке на правило, без нумерации): "
            "угроза растёт каждый ход и на 100 миссия провалена; "
            "корпус накапливает урон и не чинится сам — 0 означает гибель корабля; "
            "раны копятся: тяжёлая рана плюс любая новая — смерть; "
            "промедление (авто-действие по таймеру хода) ускоряет рост угрозы; "
            "экипаж может взбунтоваться от тяжёлых потерь; "
            "экипаж не пополняется — каждая смерть насовсем. "
            "Сразу после блока добавь строку: "
            "«Как победить: быстро двигайте миссию, чините корабль, лечите раны — действуйте до срока». "
        )
        if mission_hint:
            user += (
                "Подзаголовок миссии в названии и атмосфера приветствия ОБЯЗАТЕЛЬНО должны отражать "
                f"эту миссию: {mission_hint}. Название корабля тоже подбери в духе миссии. "
            )
        user += "Все тексты на русском языке."
    else:
        system = "You are a creative sci-fi writer. You create titles and descriptions for space adventures."
        user = (
            "Create a title for a starship crew game and a welcome message. "
            "Title format: ship name + mission tagline. "
            "Example style: 'Star Cruiser Aurora: Beyond the Known Horizon'. "
            "The welcome should be atmospheric — as if the player is stepping aboard the ship. "
            "IMPORTANT: do not use asterisk (*) or underscore (_) characters in the welcome text — "
            "they will break formatting when sent to the player. Use plain text only. "
            "At the end of the welcome you MUST include a short rules block under the heading "
            "'How you can lose' (one line per rule, no numbering): "
            "threat rises every turn and at 100 the mission is failed; "
            "hull damage accumulates and never self-repairs — 0 means the ship is destroyed; "
            "wounds pile up: a critical wound plus any new one kills; "
            "hesitation (timer auto-action) accelerates the threat; "
            "the crew can mutiny after heavy losses; "
            "the crew is never replenished — every death is permanent. "
            "Right after the block add the line: "
            "'How to win: push the mission fast, repair the ship, treat wounds — act before the clock runs out.' "
        )
        if mission_hint:
            user += (
                "The mission tagline in the title and the atmosphere of the welcome MUST reflect "
                f"this mission: {mission_hint}. Pick the ship name in the spirit of the mission too. "
            )
        user += "All text in English."
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["game_title"], k=vs_k)
    return system, user


# ── NPC dialogue prompt builders ───────────────────────────────────


def build_npc_dialogue_lang_note(language: str, player_role: str) -> tuple[str, str]:
    """Build language note and player role display for NPC dialogue."""
    if language == LANGUAGE_RU:
        return "Отвечай на русском.", player_role or "Член экипажа"
    return "Respond in English.", player_role or "Crew member"


def build_crew_dialogue_prompts(
    language: str,
    narrative: str,
    speakers: list[dict[str, Any]],
    rounds: int,
) -> tuple[str, str]:
    """Build system and user prompts for a single cohesive crew dialogue scene.

    Args:
        narrative: The turn's shared narrative (setting/conflict/events).
        speakers: Participants with 'name', 'role', 'personality', 'species'.
        rounds: How many times each speaker takes the floor (~lines per speaker).

    Returns:
        (system_prompt, user_prompt). The model returns an ordered list of
        ``{speaker, dialogue}`` lines forming a connected conversation.
    """
    speaker_lines = []
    for i, s in enumerate(speakers, start=1):
        name = s.get("name", f"Speaker {i}")
        role = s.get("role", "Crew")
        personality = s.get("personality", "") or "professional"
        species = s.get("species", "") or ""
        species_part = f", вид: {species}" if species and language == LANGUAGE_RU else (
            f", species: {species}" if species else ""
        )
        speaker_lines.append(f"{i}. {name} — {role} (характер: {personality}{species_part})"
                             if language == LANGUAGE_RU
                             else f"{i}. {name} — {role} (personality: {personality}{species_part})")
    speakers_block = "\n".join(speaker_lines)

    if language == LANGUAGE_RU:
        system = (
            "Ты — сценарист космической игры в стиле Star Trek. Твоя задача — написать "
            "СВЯЗНЫЙ ДИАЛОГ между членами экипажа: реплики должны быть живой беседой, "
            "где каждый реагирует на сказанное другими — спорит, поддерживает, предлагает, "
            "возражает. Это НЕ набор независимых выкриков в пустоту. "
            "Каждая реплика — 1-2 предложения, в характере говорящего. "
            "Диалог должен быть осмысленной реакцией на события хода и продвигать "
            "напряжённость или принятие решений."
        )
        user = (
            f"События хода:\n{narrative}\n\n"
            f"Участники разговора:\n{speakers_block}\n\n"
            f"Напиши связный диалог в {rounds} раунда(-ов): каждый участник "
            f"произносит {rounds} реплик(и), реплики идут по очереди в порядке беседы "
            "(не по одному раунду подряд). Каждая реплика должна реагировать на предыдущую. "
            "Возвращай JSON: объект со списком 'lines', каждый элемент "
            "{'speaker': <точное имя>, 'dialogue': <реплика>}. "
            "Порядок lines = порядок произнесения в разговоре. Всё на русском языке."
        )
    else:
        system = (
            "You are a scriptwriter for a Star Trek-style space game. Your task is to write "
            "a COHESIVE DIALOGUE between crew members: lines must form a living conversation "
            "where each speaker reacts to what others said — argues, supports, proposes, "
            "objects. This is NOT a set of independent shouts into the void. "
            "Each line is 1-2 sentences, in the speaker's character. "
            "The dialogue should be a meaningful reaction to the turn's events and advance "
            "tension or decision-making."
        )
        user = (
            f"Turn events:\n{narrative}\n\n"
            f"Conversation participants:\n{speakers_block}\n\n"
            f"Write a connected dialogue over {rounds} round(s): each participant speaks "
            f"{rounds} line(s), lines come in conversational order (not one full round at a time). "
            "Each line must react to the previous one. "
            "Return JSON: an object with a 'lines' array, each element "
            "{'speaker': <exact name>, 'dialogue': <line>}. "
            "The order of lines = the order they are spoken. Respond in English."
        )
    return system, user


# ── Content prompt lang note ───────────────────────────────────────


def build_content_prompt_note(language: str) -> str:
    """Get language note for content prompt generation."""
    if language == LANGUAGE_RU:
        return "Промпты пиши на английском (для генерации изображений)."
    return "Write prompts in English for image generation."


# ── Player message prompts ─────────────────────────────────────────


def build_player_message_prompts(
    language: str,
    player_name: str,
    player_role: str,
    player_traits: list[str],
    message: str,
    *,
    game_title: str,
    mission_name: str,
    mission_description: str,
    mission_objectives: str,
    turn: int,
    previous_turn_summary: str,
    global_circumstances_setting: str,
    global_circumstances_conflict: str,
    global_circumstances_narrative: str,
    crew_context: str,
    use_vs: bool,
    vs_k: int,
) -> tuple[str, str]:
    """Build system and user prompts for player message processing with full game context."""
    traits_str = ", ".join(player_traits) if player_traits else ""

    if language == LANGUAGE_RU:
        system = (
            "Ты — Game Master космической исследовательской игры в стиле Star Trek. "
            "Твоя задача — отвечать на сообщения игрока от лица Game Master. "
            "Ты НЕ пересказываешь игровую ситуацию и не повторяешь контекст — "
            "ты отвечаешь именно на то, что спросил или сказал игрок. "
            "Контекст игры (миссия, экипаж, события) дан тебе ТОЛЬКО для справки, "
            "чтобы твой ответ не противоречил происходящему. "
            "Отвечай в характере, учитывая роль и личность игрока. "
            "Будь увлекательным, атмосферным и полезным. "
            "Если игрок задаёт вопрос — отвечай прямо. "
            "Если игрок предлагает действие — реагируй на него. "
            "Не придумывай новых событий, которые противоречат контексту игры."
        )

        context_parts = []
        if game_title:
            context_parts.append(f"Игра: {game_title}")
        if mission_name:
            context_parts.append(f"Миссия: {mission_name}")
        if mission_description:
            context_parts.append(f"Описание миссии: {mission_description}")
        if mission_objectives:
            context_parts.append(f"Этапы миссии:\n{mission_objectives}")
        context_parts.append(f"Текущий ход: {turn}")
        if previous_turn_summary:
            context_parts.append(f"Итог предыдущего хода: {previous_turn_summary}")
        if global_circumstances_setting or global_circumstances_conflict:
            circ_parts = []
            if global_circumstances_setting:
                circ_parts.append(f"Локация: {global_circumstances_setting}")
            if global_circumstances_conflict:
                circ_parts.append(f"Конфликт: {global_circumstances_conflict}")
            if global_circumstances_narrative:
                circ_parts.append(f"Ситуация: {global_circumstances_narrative}")
            if circ_parts:
                context_parts.append("Текущие обстоятельства:\n" + "\n".join(circ_parts))
        if crew_context:
            context_parts.append(f"Экипаж на борту:\n{crew_context}")

        context_block = "\n\n".join(context_parts)

        user = (
            f"Игрок {player_name or 'Неизвестно'} ({player_role})"
            f"{', черты: ' + traits_str if traits_str else ''}"
            f' написал:\n"{message}"\n\n'
            f"ОТВЕТЬ НА ЭТО СООБЩЕНИЕ в роли Game Master. "
            f"Не пересказывай игровую ситуацию — дай прямой ответ на то, "
            f"что сказал или спросил игрок.\n\n"
            f"СПРАВОЧНЫЙ КОНТЕКСТ (используй только чтобы не противоречить игре):\n{context_block}"
        )
    else:
        system = (
            "You are the Game Master of a Star Trek-style space exploration game. "
            "Your job is to respond to player messages as the Game Master. "
            "You DO NOT restate or summarize the game situation — "
            "you respond directly to what the player said or asked. "
            "The game context (mission, crew, events) is provided ONLY as background "
            "so your response doesn't contradict what's happening. "
            "Respond in character, taking into account the player's role and personality. "
            "Be engaging, atmospheric, and helpful. "
            "If the player asks a question — answer it directly. "
            "If the player proposes an action — react to it. "
            "Do not invent events that contradict the established game context."
        )

        context_parts = []
        if game_title:
            context_parts.append(f"Game: {game_title}")
        if mission_name:
            context_parts.append(f"Mission: {mission_name}")
        if mission_description:
            context_parts.append(f"Mission description: {mission_description}")
        if mission_objectives:
            context_parts.append(f"Mission stages:\n{mission_objectives}")
        context_parts.append(f"Current turn: {turn}")
        if previous_turn_summary:
            context_parts.append(f"Previous turn outcome: {previous_turn_summary}")
        if global_circumstances_setting or global_circumstances_conflict:
            circ_parts = []
            if global_circumstances_setting:
                circ_parts.append(f"Location: {global_circumstances_setting}")
            if global_circumstances_conflict:
                circ_parts.append(f"Conflict: {global_circumstances_conflict}")
            if global_circumstances_narrative:
                circ_parts.append(f"Situation: {global_circumstances_narrative}")
            if circ_parts:
                context_parts.append("Current circumstances:\n" + "\n".join(circ_parts))
        if crew_context:
            context_parts.append(f"Crew aboard:\n{crew_context}")

        context_block = "\n\n".join(context_parts)

        user = (
            f"Player {player_name or 'Unknown'} ({player_role})"
            f"{', traits: ' + traits_str if traits_str else ''}"
            f' wrote:\n"{message}"\n\n'
            f"RESPOND TO THIS MESSAGE in character as Game Master. "
            f"Do not restate the game situation — give a direct response to "
            f"what the player said or asked.\n\n"
            f"REFERENCE CONTEXT (use only to stay consistent with the game):\n{context_block}"
        )

    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["player_message"], k=vs_k)
    return system, user


# ── Species description prompts
# ── Species description prompts ────────────────────────────────────


# ── Role flavour prompts ───────────────────────────────────────────


def build_role_flavour_prompts(
    language: str,
    role_key: str,
    role_name: str,
    species_display: str,
    gender_display: str,
    traits: list[str],
    *,
    use_vs: bool,
    vs_k: int,
) -> tuple[str, str]:
    """Build system and user prompts for per-character role flavour generation.

    Generates role_description, avatar_description, and personality_traits
    tailored to this specific crew member — reflecting their role, species,
    gender, and onboarding-derived traits. Replaces the old static
    SHIP_ROLES_I18N flavour text.
    """
    traits_str = ", ".join(traits) if traits else ""

    # The species decides whether this is a human crew member or an alien
    # being. Without this guard the LLM invents non-human traits/descriptions
    # (parasitic, plasma, extra limbs) even for a Human, which then corrupts
    # the downstream avatar prompt.
    _neg = bool(re.search(r"\b(не|non|not)\b", species_display.lower())) or species_display.lower().startswith(("не", "non"))
    _has_token = any(re.search(rf"\b{t}\b", species_display.lower()) for t in ("человек", "гуманоид", "human", "humanoid"))
    is_human_like = _has_token and not _neg

    if language == LANGUAGE_RU:
        if is_human_like:
            system = (
                "Ты — креативный писатель-фантаст, создающий живые портреты членов звёздного экипажа. "
                "Для заданной роли, вида, пола и черт характера опиши конкретного ЧЕЛОВЕКА на этой должности. "
                "Это человек/гуманоид: две руки, две ноги, человеческое лицо. НЕ придумывай нечеловеческую "
                "физиологию (никаких щупалец, панцирей, плазмы, лишних конечностей, паразитизма, "
                "симбиозов, энергетических форм). Черты характера и визуальное описание должны быть "
                "применимы к живому человеку в форме Starfleet. Избегай шаблонных архетипов — сделай "
                "персонажа запоминающимся. Текст кинематографичный и атмосферный."
            )
        else:
            system = (
                "Ты — креативный писатель-фантаст, создающий живые портреты членов звёздного экипажа. "
                "Для заданной роли, вида, пола и черт характера опиши конкретное инопланетное существо на этой должности. "
                "Опиши его нечеловеческую физиологию в соответствии с видом. Избегай шаблонных архетипов — сделай "
                "персонажа запоминающимся. Текст должен быть кинематографичным "
                "и атмосферным, как описание персонажа для фильма или игры."
            )
        user_lines = [
            "Создай flavour-описание члена экипажа для космической игры в стиле Star Trek.\n",
            f"Роль: {role_name} (ключ: {role_key})",
        ]
        if species_display:
            user_lines.append(f"Вид: {species_display}")
        if gender_display:
            user_lines.append(f"Пол: {gender_display}")
        if traits_str:
            user_lines.append(f"Черты характера (из онбординга): {traits_str}")
        if is_human_like:
            user_lines.append("ВАЖНО: это человек/гуманоид. avatar_description должен описывать человека "
                              "(внешность, форма, окружение, поза), а personality_traits — человеческие черты "
                              "характера. Никаких нечеловеческих элементов.")
        user_lines.append("")
        user_lines.append("Верни JSON с тремя полями:")
        user_lines.append("- role_description: 2-4 предложения на русском — кто этот персонаж на своей должности, "
                          "как он воспринимает свою роль и почему он здесь. Второе лицо ('вы').")
        user_lines.append("- avatar_description: 1-2 предложения на русском — визуальное описание для генерации "
                          "аватара: внешность, одежда/форма, окружение, поза, атмосфера. Без указания имени.")
        user_lines.append("- personality_traits: ровно 3 прилагательных на русском, контрастных между собой, "
                          "отражающих характер персонажа (включая черты из онбординга, но расширенные под роль).")
        user = "\n".join(user_lines)
    else:
        if is_human_like:
            system = (
                "You are a creative sci-fi writer crafting vivid portraits of starship crew members. "
                "For a given role, species, gender, and set of traits, describe a specific HUMAN holding that post. "
                "This is a human/humanoid: two arms, two legs, a human face. Do NOT invent non-human physiology "
                "(no tentacles, carapaces, plasma, extra limbs, parasitism, symbiosis, or energy forms). Traits "
                "and the visual description must apply to a living person in a Starfleet uniform. Avoid stock "
                "archetypes — make the character memorable. The writing should be cinematic and atmospheric, "
                "like a character pitch for a film or game."
            )
        else:
            system = (
                "You are a creative sci-fi writer crafting vivid portraits of starship crew members. "
                "For a given role, species, gender, and set of traits, describe a specific alien being holding that post. "
                "Describe its non-human physiology per its species. Avoid stock archetypes — make the character memorable. "
                "The writing should be cinematic and atmospheric, like a character pitch for a film or game."
            )
        user_lines = [
            "Create a flavour description of a crew member for a Star Trek-style space game.\n",
            f"Role: {role_name} (key: {role_key})",
        ]
        if species_display:
            user_lines.append(f"Species: {species_display}")
        if gender_display:
            user_lines.append(f"Gender: {gender_display}")
        if traits_str:
            user_lines.append(f"Traits (from onboarding): {traits_str}")
        if is_human_like:
            user_lines.append("IMPORTANT: this is a human/humanoid. avatar_description must describe a human "
                              "(appearance, uniform, surroundings, pose), and personality_traits must be human "
                              "character traits. No non-human elements whatsoever.")
        user_lines.append("")
        user_lines.append("Return JSON with three fields:")
        user_lines.append("- role_description: 2-4 sentences in English — who this character is in their role, "
                          "how they relate to it, and why they are here. Second person ('you').")
        user_lines.append("- avatar_description: 1-2 sentences in English — visual description for avatar "
                          "generation: appearance, clothing/uniform, surroundings, pose, mood. No name.")
        user_lines.append("- personality_traits: exactly 3 adjectives in English, contrasting with each other, "
                          "capturing the character's nature (including onboarding traits but extended to fit the role).")
        user = "\n".join(user_lines)

    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["role_flavour"], k=vs_k)
    return system, user


# ── NPC decision prompts ────────────────────────────────────────────

# How each loyalty band (game_rules.loyalty_band) tells the NPC to behave.
# Loyalty is code-owned state: the number and its band come from the DB,
# never from the LLM.
_NPC_LOYALTY_RULES_RU = {
    "steadfast": "ты действуешь в интересах миссии и командования",
    "uneasy": "ты работаешь, но сомневаешься в приказах и командовании",
    "on_edge": "твоё главное — самосохранение, ты уклоняешься от риска и не рискуешь жизнью ради миссии",
    "mutinous": "ты открыто не подчиняешься приказам, саботируешь их и готовишь бунт против командования",
}

_NPC_LOYALTY_RULES_EN = {
    "steadfast": "you act in the best interest of the mission and command",
    "uneasy": "you keep working, but you doubt the orders and the command",
    "on_edge": "your priority is self-preservation — you avoid risk and won't risk your life for the mission",
    "mutinous": "you openly disobey orders, sabotage them, and are preparing to mutiny against command",
}


# Few-shot demo blocks compiled offline by tools/prompt_optimizer
# (run_optimize.py + export_demos.py). Empty by default: the builder appends
# the block verbatim to the user prompt only after a compiled block is pasted
# here, so runtime behavior is unchanged until then.
NPC_DECISION_DEMOS = {
    LANGUAGE_RU: "",
    LANGUAGE_EN: "",
}

# Persona template of the NPC-decision system prompt. Module-level so
# tools/prompt_optimizer seeds its dspy signature instructions from the exact
# runtime text — {placeholders} mirror the signature input fields.
_NPC_DECISION_SYSTEM_TMPL_RU = (
    "Ты — {npc_name}, {npc_role} на космическом корабле. Твой характер: {traits}. "
    "Твоя лояльность командованию: {loyalty}/100 — {loyalty_rule}. "
    "Ты видишь ТОЛЬКО описания действий без последствий. Сделай выбор на основе своей личности, роли и лояльности."
)

_NPC_DECISION_SYSTEM_TMPL_EN = (
    "You are {npc_name}, {npc_role} aboard a starship. Your personality: {traits}. "
    "Your loyalty to command: {loyalty}/100 — {loyalty_rule}. "
    "You see ONLY action descriptions with no consequences. Make a choice based on your personality, role, and loyalty."
)



def build_npc_decision_prompts(
    language: str,
    npc_name: str,
    npc_role: str,
    traits: str | list[str],
    choices_text: str,
    *,
    loyalty: int,
    use_vs: bool,
    vs_k: int,
) -> tuple[str, str]:
    """Build system and user prompts for NPC decision making."""
    traits_str = ", ".join(traits) if isinstance(traits, list) else traits
    rules = _NPC_LOYALTY_RULES_RU if language == LANGUAGE_RU else _NPC_LOYALTY_RULES_EN
    loyalty_rule = rules.get(loyalty_band(loyalty), rules["steadfast"])
    if language == LANGUAGE_RU:
        system = _NPC_DECISION_SYSTEM_TMPL_RU.format(
            npc_name=npc_name, npc_role=npc_role, traits=traits_str,
            loyalty=loyalty, loyalty_rule=loyalty_rule,
        )
        user = f"Текущая ситуация на корабле требует твоего решения.\n\nДоступные действия:\n{choices_text}\n\nВыбери одно действие, которое лучше всего соответствует твоему характеру, роли и уровню лояльности. Ты не знаешь последствий — действуй интуитивно."
    else:
        system = _NPC_DECISION_SYSTEM_TMPL_EN.format(
            npc_name=npc_name, npc_role=npc_role, traits=traits_str,
            loyalty=loyalty, loyalty_rule=loyalty_rule,
        )
        user = f"The current situation requires your decision.\n\nAvailable actions:\n{choices_text}\n\nChoose the action that best matches your character, role, and loyalty level. You don't know the consequences — act on instinct."
    demos = NPC_DECISION_DEMOS[LANGUAGE_RU if language == LANGUAGE_RU else LANGUAGE_EN]
    if demos:
        user += "\n\n" + demos
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["npc_decision"], k=vs_k)
    return system, user


# ── Auto-choice prompts
# ── Auto-choice prompts ────────────────────────────────────────────


def build_auto_choice_prompts(
    language: str,
    display_name: str,
    role: str,
    traits: str | list[str],
    species_line: str,
    personal_briefing: str,
    gc_settings: str,
    choices_text: str,
    *,
    use_vs: bool,
    vs_k: int,
) -> tuple[str, str]:
    """Build system and user prompts for auto-choice when player doesn't respond."""
    traits_str = ", ".join(traits) if isinstance(traits, list) else str(traits)
    if language == LANGUAGE_RU:
        system = f"Ты — Game Master. Игрок {display_name} ({role}) не успел сделать выбор, и ты принимаешь решение за него. Ты действуешь на основе характера персонажа текущей вводной и обстоятельств. Ты не видишь скрытые последствия действий."
        user = (
            f"Профиль персонажа:\n"
            f"Имя: {display_name}\n"
            f"Роль: {role}{species_line}\n"
            f"Характер: {traits_str}\n"
            f"\nПерсональная вводная:\n{personal_briefing}"
            f"{gc_settings}"
            f"\n\nДоступные действия (без последствий):\n{choices_text}\n\n"
            f"Выбери одно действие, которое лучше всего соответствует характеру и роли игрока. "
            f"Ты не знаешь последствий — действуй на основе личности персонажа."
        )
    else:
        system = (
            f"You are the Game Master. Player {display_name} ({role}) didn't make "
            f"a choice in time, and you decide for them. You act based on the character's "
            f"personality, their personal briefing, and the global circumstances. "
            f"You do NOT see hidden consequences of actions."
        )
        user = (
            f"Character profile:\n"
            f"Name: {display_name}\n"
            f"Role: {role}{species_line}\n"
            f"Traits: {traits_str}\n"
            f"\nPersonal briefing:\n{personal_briefing}"
            f"{gc_settings}"
            f"\n\nAvailable actions (no consequences shown):\n{choices_text}\n\n"
            f"Choose the action that best matches the player's character and role. "
            f"You don't know the consequences — act based on personality."
        )
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["npc_decision"], k=vs_k)
    return system, user


# ── Global circumstances prompts
# ── Global circumstances prompts ───────────────────────────────────


# ── Doom clock: threat status line for per-turn prompts ─────────────

THREAT_BAR_SEGMENTS = 10

_THREAT_BANDS = {
    LANGUAGE_RU: (
        (70, "враг на пороге — время на исходе"),
        (34, "враждебное окружение сжимает кольцо"),
        (0, "напряжение нарастает"),
    ),
    LANGUAGE_EN: (
        (70, "the enemy is at the gates — time is running out"),
        (34, "the hostile environment is closing in"),
        (0, "tension is building"),
    ),
}


def format_threat_status(language: str, threat_level: int) -> str:
    """One-line doom-clock status (bar + level + mood) for turn prompts.

    Example: "Угроза миссии: ▓▓▓░░░░░░░ 34/100 — враждебное окружение сжимает кольцо"
    """
    lang = LANGUAGE_EN if language == LANGUAGE_EN else LANGUAGE_RU
    label = "Mission threat" if lang == LANGUAGE_EN else "Угроза миссии"
    level = max(0, min(THREAT_MAX, int(threat_level)))
    filled = level * THREAT_BAR_SEGMENTS // THREAT_MAX
    bar = "▓" * filled + "░" * (THREAT_BAR_SEGMENTS - filled)
    mood = next((text for floor, text in _THREAT_BANDS[lang] if level >= floor), "")
    return f"{label}: {bar} {level}/{THREAT_MAX} — {mood}"


def build_global_circumstances_prompts(
    language: str,
    turn: int,
    previous_summary: str,
    player_descriptions: str,
    mission_str: str,
    *,
    use_vs: bool,
    vs_k: int,
    threat_status: str = "",
) -> tuple[str, str]:
    """Build system and user prompts for global circumstances generation."""
    threat_block = f"{threat_status}\n" if threat_status else ""
    if language == LANGUAGE_RU:
        system = (
            "Ты — Game Master космической игры в стиле Star Trek. "
            "Создаёшь ОБЩИЕ обстоятельства дня — ситуацию, которая происходит на корабле или вокруг него. "
            "Эти обстоятельства едины для всех членов экипажа.\n\n"
            "Используй ПОЛНЫЕ ИМЕНА персонажей из списка экипажа в нарративе. "
            "У каждого члена экипажа есть уникальное имя — обращайся к ним по имени.\n\n"
            "ЧАСЫ УГРОЗЫ: строка 'Угроза миссии' в запросе — это тикающий счётчик гибели. "
            "Чем он выше, тем ближе провал миссии. Обязательно отражай нарастание угрозы в нарративе: "
            "преследование подходит ближе, время тает, ресурсы на исходе, враги наглеют. "
            "При угрозе выше 70/100 ОБЯЗАНА генерировать острые события — атаки, прорывы, "
            "аварии, жертвы; спокойных ходов больше не бывает.\n"
        )
        user = (
            f"Ход: {turn}\n"
            f"{threat_block}"
            f"Предыдущие события: {previous_summary or 'Первый день миссии'}\n"
            f"Экипаж:\n{player_descriptions or '  Экипаж формируется'}\n"
            f"{mission_str}\n"
            "Создай общие обстоятельства дня:\n"
            "1. Место действия — где находится корабль (звездная система, станция, явление космоса)\n"
            "2. Конфликт — центральная проблема или тайна\n"
            "3. Нарратив — описание ситуации от лица GM (2-3 абзаца). "
            "Упоминай членов экипажа по ИМЕНИ, показывая их местоположение и действия.\n"
            "4. Ключевые события — 3-5 фоновых событий, которые могут заметить все\n"
            "5. scene_prompt — детальный промпт на АНГЛИЙСКОМ ЯЗЫКЕ для генерации изображения сцены. "
            "Кинематографичный, sci-fi/space opera, 4K. Опиши обстановку, экипаж на своих местах, "
            "освещение и атмосферу.\n"
            "6. crew_positions — массив позиций каждого члена экипажа: где они находятся и что делают.\n\n"
            "ВАЖНО: Все обстоятельства дня должны соответствовать контексту миссии.\n"
            "Не выдумывай новый независимый сюжет — развивай события в рамках миссии.\n"
            "Всё на русском языке."
        )
    else:
        system = (
            "You are a Game Master for a Star Trek-style space exploration game. "
            "Create SHARED circumstances for the turn — the situation unfolding on or around the ship. "
            "These circumstances are common to all crew members.\n\n"
            "Use the actual CHARACTER NAMES from the crew list in the narrative. "
            "Each crew member has a unique name — refer to them by name.\n\n"
            "DOOM CLOCK: the 'Mission threat' line in the request is a ticking countdown to failure. "
            "The higher it is, the closer the mission is to collapse. You MUST reflect the rising "
            "threat in the narrative: the pursuit closes in, time runs out, resources dwindle, "
            "enemies grow bolder. Above 70/100 you MUST generate acute events — attacks, "
            "breaches, failures, casualties; calm turns no longer happen.\n"
        )
        user = (
            f"Turn: {turn}\n"
            f"{threat_block}"
            f"Previous events: {previous_summary or 'First turn of mission'}\n"
            f"Crew:\n{player_descriptions or '  Crew forming'}\n"
            f"{mission_str}\n"
            "Create shared circumstances for the turn:\n"
            "1. Setting — where the ship is located\n"
            "2. Conflict — central problem or mystery\n"
            "3. Narrative — GM voice description (2-3 paragraphs). "
            "Refer to crew members by NAME, showing their location and actions.\n"
            "4. Key events — 3-5 background events everyone can perceive\n"
            "5. scene_prompt — a detailed English image generation prompt for this turn's scene. "
            "Cinematic, sci-fi/space opera, 4K quality. Describe the setting, crew at their positions, "
            "lighting, and atmosphere.\n"
            "6. crew_positions — array of positions for each crew member: where they are and what they're doing.\n\n"
            "IMPORTANT: All circumstances must be consistent with the mission context. "
            "Do not invent an independent plot — develop events within the mission framework.\n"
        )
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["global_circumstances"], k=vs_k)
    return system, user


# ── Mission generation prompts ─────────────────────────────────────

# ── Mission generation prompts ─────────────────────────────────────


def build_mission_prompts(
    language: str,
    archetype: str | None,
    seeds: dict | None,
    *,
    use_vs: bool,
    vs_k: int,
) -> tuple[str, str]:
    """Build system and user prompts for mission generation.

    The mission is purely plot-driven (archetype + seeds): it describes the
    ship, the situation and the briefing, not specific crew members, so the
    crew composition is intentionally not part of the prompt. When
    archetype/seeds are provided they are injected to force variety (P2); a
    banned-trope list and a balanced threshold range are always included.
    """
    lang = LANGUAGE_RU if language == LANGUAGE_RU else LANGUAGE_EN
    forbidden = ", ".join(FORBIDDEN_OPENINGS[lang])
    arch_hint = ""
    if archetype and archetype in MISSION_ARCHETYPES:
        arch_hint = f"{archetype} — {MISSION_ARCHETYPES[archetype][lang]}"

    if seeds:
        if lang == LANGUAGE_RU:
            seeds_block = (
                f"\nЭлементы миссии для брифинга экипажа:\n"
                f"- Место: {seeds.get('setting', '')}\n"
                f"- Осложнение: {seeds.get('complication', '')}\n"
                f"- Награда: {seeds.get('reward', '')}\n\n"
                f"СЕКРЕТНЫЙ поворот сюжета (только для Game Master): {seeds.get('twist', '')}\n"
                f"Этот поворот НЕ раскрывай в description и short_description — "
                f"игрок не должен о нём знать заранее, он должен раскрыться по ходу игры. "
                f"Описание и short_description — это то, что экипажу сообщают перед вылетом.\n\n"
            )
        else:
            seeds_block = (
                f"\nMission elements for the crew briefing:\n"
                f"- Setting: {seeds.get('setting', '')}\n"
                f"- Complication: {seeds.get('complication', '')}\n"
                f"- Reward: {seeds.get('reward', '')}\n\n"
                f"SECRET plot twist (Game Master only): {seeds.get('twist', '')}\n"
                f"Do NOT reveal this twist in the description or short_description — "
                f"the player must not know about it in advance; it must unfold during play. "
                f"The description and short_description are what the crew is told before departure.\n\n"
            )
    else:
        seeds_block = ""

    if lang == LANGUAGE_RU:
        system = "Ты — Game Master космической игры. Создаёшь миссию для экипажа звёздного корабля. Миссия делится на 2-4 этапа (stages), каждый с прогрессом от 1 до 10." + (f"\nАрхетип миссии: {arch_hint}" if arch_hint else "")
        user = (
            f"{seeds_block}"
            "ЗАПРЕЩЕНО начинать миссию с клише про сигнал бедствия / перехваченный сигнал / "
            f"неопознанную передачу. Запрещённые завязки: {forbidden}.\n"
            "Создай миссию с:\n"
            "1. Название миссии — только кодовое имя и описание (формат: 'Кодовое имя: описание'). "
            "ВАЖНО: слово 'Миссия' в названии НЕ пиши — оно будет добавлено автоматически в интерфейсе.\n"
            "2. Описание — это брифинг для экипажа перед вылетом: что нужно сделать и зачем, "
            "2-3 абзаца, понятное игроку. НЕ раскрывай поворот сюжета, скрытые мотивы NPC "
            "или истинную цель — это тайна, которую игрок раскроет по ходу игры.\n"
            "3. short_description — сжатое описание миссии в 1-2 предложениях, "
            "не более 500 символов (используется для подписей к картинкам с ограничением длины), "
            "без спойлеров.\n"
            "4. 2-4 этапа с целями, каждый с success_threshold в диапазоне 3-5\n"
            "Этапы должны быть последовательными, но достижимыми нелинейно.\n"
            "Всё на русском языке."
        )
    else:
        system = "You are a Game Master. Create a mission for a starship crew. The mission is divided into 2-4 stages, each with progress from 1 to 10." + (f"\nMission archetype: {arch_hint}" if arch_hint else "")
        user = (
            f"{seeds_block}"
            "DO NOT start the mission with the cliché of a distress signal / intercepted signal / "
            f"unidentified transmission. Forbidden openings: {forbidden}.\n"
            "Create a mission with:\n"
            "1. Mission name — code name and description only (format: 'Code Name: description'). "
            "IMPORTANT: do NOT include the word 'Mission' in the name — it will be added automatically by the UI.\n"
            "2. Description — this is a briefing for the crew before departure: what needs to be "
            "done and why, 2-3 paragraphs, clear to the player. Do NOT reveal the plot twist, "
            "hidden NPC motives, or the true objective — that is a secret the player will uncover "
            "during play.\n"
            "3. A short_description — condensed 1-2 sentence summary of the mission, "
            "no more than 500 characters (used for image captions with length limits), "
            "no spoilers.\n"
            "4. 2-4 stages with objectives, each with success_threshold in the range 3-5\n"
            "Stages should be sequential but achievable non-linearly."
        )
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["mission"], k=vs_k)
    return system, user


# ── NPC name prompts ───────────────────────────────────────────────


def build_npc_name_system(language: str) -> str:
    """Build system prompt for NPC name generation."""
    if language == LANGUAGE_RU:
        return (
            "Ты — креативный писатель-фантаст. Придумываешь имена для персонажей "
            "звёздного корабля в стиле Star Trek.\n\n"
            "ВАЖНЫЕ ПРАВИЛА:\n"
            "- Имя должно соответствовать ВИДУ и ПОЛУ персонажа\n"
            "- Для людей/гуманоидов: человеческие имена (Алексей, Елена, Дмитрий, etc.)\n"
            "- Для негуманоидов: уникальные инопланетные имена (К'рртх, Зиль-Ван, Гжорг, etc.)\n"
            "- Для энергетических форм: имена как частоты или явления\n"
            "- Для кибернетических: имена с техническим оттенком\n"
            "- Для симбиотических: составные имена\n"
            "- Имя ДОЛЖНО быть на русском языке!\n"
            "- НЕ используй транслит английских имён — создай оригинальное имя.\n"
            "- НИКОГДА не используй подчёркивание (_) в именах. Разделяй слова пробелами или дефисами.\n"
            "- Учитывай роль персонажа при выборе имени\n"
            "- Будь КРЕАТИВНЫМ, избегай шаблонов"
        )
    return (
        "You are a creative sci-fi writer. You invent names for starship crew "
        "characters in Star Trek style.\n\n"
        "IMPORTANT RULES:\n"
        "- Name must match the SPECIES and GENDER of the character\n"
        "- For humans/humanoids: human names (Alex, Elena, Marcus, etc.)\n"
        "- For non-humanoids: unique alien names (K'rrtkh, Zil-Van, Gjorg, etc.)\n"
        "- For energy beings: names as frequencies or phenomena\n"
        "- For cybernetic: names with technical undertones\n"
        "- For symbiotic: compound names\n"
        "- Consider the character's role when choosing a name\n"
        "- NEVER use underscore (_) in names. Separate words with spaces or hyphens.\n"
        "- Be CREATIVE, avoid templates"
    )


def build_npc_name_user(
    language: str,
    role_name: str,
    role_key: str,
    species: str,
    gender: str,
    avatar_description: str,
    personality_traits: list[str],
    avoid_names: set[str],
) -> str:
    """Build user prompt for NPC name generation."""
    if language == LANGUAGE_RU:
        avoid_text = ""
        if avoid_names:
            avoid_text = f"УЖЕ ИСПОЛЬЗУЕТСЯ: {', '.join(sorted(avoid_names))}. НЕ используй эти имена — выбери другое.\n\n"
        user = (
            f"Роль: {role_name} ({role_key})\n"
            f"Вид: {species}\n"
            f"Пол: {gender}\n"
            f"Описание внешности: {avatar_description}\n"
            f"Черты характера: {', '.join(personality_traits)}\n\n" + avoid_text + "Придумай уникальное, креативное имя для этого персонажа. "
            "Имя должно быть на русском языке и соответствовать описанию.\n"
            "ПРИМЕРЫ (для русской локализации):\n"
            "  - Инженер-человек: 'Инженер Дмитрий Волков'\n"
            "  - Штурман-гуманоид: 'Штурман Зиара Вентрис'\n"
            "  - Ксенобиолог-кристаллическая форма: 'Ксенобиолог Резонанс Три-Семь'\n"
            "  - Медик-киборг: 'Медик ЛЕ-02'\n"
            "ВЕРНИ ТОЛЬКО JSON."
        )
    else:
        avoid_text = ""
        if avoid_names:
            avoid_text = f"ALREADY IN USE: {', '.join(sorted(avoid_names))}. DO NOT use these names — choose another.\n\n"
        user = (
            f"Role: {role_name} ({role_key})\n"
            f"Species: {species}\n"
            f"Gender: {gender}\n"
            f"Appearance: {avatar_description}\n"
            f"Traits: {', '.join(personality_traits)}\n\n" + avoid_text + "Create a unique, creative name for this character. "
            "The name should be in English and match the description.\n"
            "EXAMPLES (for English localization):\n"
            "  - Human engineer: 'Chief Engineer Marcus Chen'\n"
            "  - Humanoid navigator: 'Navigator Zyara Ventures'\n"
            "  - Crystalline xenobiologist: 'Xenobiologist Resonance Three-Seven'\n"
            "  - Cyborg medic: 'Medic LE-02'\n"
            "RETURN ONLY JSON."
        )
    return user


# ── Personal briefing prompts ──────────────────────────────────────

# Synthetic "hesitation" action used as the fallback when the LLM
# auto-choice (player or NPC) fails or returns an invalid choice.
# Formerly the fallback picked choices[0], which the briefing schema
# orders progress→injury→fatal — a hidden progress subsidy. {name} is
# the character the choice is made for.
DELAY_ACTION_TEXT_RU = "Промедление — {name} замер(ла) в нерешительности, время ушло"
DELAY_ACTION_TEXT_EN = "Hesitation — {name} froze, unable to decide, and the window closed"

# Delay-kind bullet for the briefing action-format instructions (RU/EN),
# interpolated into the user prompt built in game_server.py. The LLM never
# generates 'delay' choices — the kind is assigned by code on fallback only.
DELAY_KIND_RULE_RU = (
    "- 'delay' — системное промедление (аварийный fallback кода): НЕ генерируй "
    "его сам — он назначается только кодом.\n"
)
DELAY_KIND_RULE_EN = (
    "- 'delay' — a system hesitation fallback (assigned by code when a choice fails): "
    "NEVER generate it yourself.\n"
)


def build_personal_briefing_system(language: str) -> str:
    """Build system prompt for personal briefing generation."""
    if language == LANGUAGE_RU:
        return (
            "Ты — Game Master космической игры. Создаёшь ПЕРСОНАЛЬНУЮ вводную для игрока, "
            "основываясь на общих обстоятельствах дня. "
            "Каждый игрок видит ситуацию со своей уникальной точки зрения.\n\n"
            "Ход может принести прогресс, стагнацию или откат — интересность рождается "
            "из честных последствий решений, а не из гарантированного успеха. "
            "Смелые и грамотные решения → миссия продвигается, открываются новые возможности. "
            "Бездействие, ошибки и игнорирование угрозы → ранения, потери, откат миссии, "
            "вплоть до гибели. Главное — ИНТЕРЕСНО и НЕПРЕДСКАЗУЕМО.\n\n"
            "ЧАСЫ УГРОЗЫ: строка 'Угроза миссии' в запросе — это тикающий счётчик гибели. "
            "Чем он выше, тем ближе провал. Отражай нарастание угрозы в брифинге: "
            "преследование подходит ближе, время тает, враги наглеют. "
            "При угрозе выше 70/100 ОБЯЗАНА предлагать острые, отчаянные ситуации — "
            "атаки, прорывы, аварии; спокойных ходов больше не бывает.\n\n"
            "ТЕЛЕГРАФИЯ РАН: строка 'Состояние' в запросе показывает здоровье персонажа. "
            "Если персонаж ранен, текст вводной ОБЯЗАТЕЛЬНО и явно отражает его ранение — "
            "тревога нарастает вместе с тяжестью: лёгкое ранение — раны ноют, вы держитесь; "
            "среднее ранение — раны серьёзные, концентрация падает; тяжёлое ранение — "
            "вы при смерти: следующая рана, скорее всего, станет последней.\n\n"
            "ТЕЛЕГРАФИЯ КОРПУСА: строка 'Статус корабля' в запросе показывает целостность "
            "корабля. При корпусе ниже 40/100 брифинг ОБЯЗАН отражать тяжёлое состояние "
            "корабля — тревога, рваные отсеки, аварийные сирены. При корпусе ниже 20/100 "
            "корабль при смерти: каждый следующий удар может стать последним.\n\n"
            "Каждый вариант действия ДОЛЖЕН содержать поле consequence_kind — его тип:\n"
            "  'progress' — действие, продвигающее миссию: успех приближает к цели, "
            "    открывает новые возможности и открытия;\n"
            "  'injury'   — действие, ведущее к РАНЕНИЮ персонажа (без гибели): "
            "    травма, ущерб здоровью, временная нетрудоспособность;\n"
            "  'fatal'    — СМЕРТЕЛЬНОЕ действие: ведёт к гибели члена экипажа "
            "    (самого персонажа или другого).\n"
            "  'delay'    — системное промедление (аварийный fallback кода при сбое "
            "    выбора): НЕ генерируй его сам — он назначается только кодом.\n"
            "Количество каждого типа задаётся в запросе; consequence_kind варианта должен "
            "соответствовать его группе.\n\n"
            "КРИТИЧЕСКОЕ ПРАВИЛО: текст действия НЕ ДОЛЖЕН выдавать его consequence_kind. "
            "Игрок ВЫБИРАЕТ ВСЛЕПУЮ — смертельное и продвигающее действия должны быть описаны "
            "так, чтобы по тексту было невозможно угадать исход.\n\n"
            "Если в запросе приведён контекст диалога экипажа — обязательно учти его: "
            "брифинг и варианты действий должны отражать то, что обсуждалось, предложенные "
            "риски и возможности. Если персонаж сам высказался в диалоге — его варианты "
            "должны быть согласованы с его репликой."
        )
    return (
        "You are a Game Master. You create PERSONAL briefings for each player "
        "based on the shared global circumstances. "
        "Each player sees the situation from their unique perspective.\n\n"
        "A turn can bring progress, stagnation or a setback — the interest comes from "
        "honest consequences of decisions, not from guaranteed success. "
        "Bold and smart decisions → the mission advances, new opportunities open. "
        "Inaction, mistakes and ignoring the threat → wounds, losses, mission setbacks, "
        "up to death. The key is INTERESTING and UNPREDICTABLE.\n\n"
        "DOOM CLOCK: the 'Mission threat' line in the request is a ticking countdown to failure. "
        "The higher it is, the closer the mission is to collapse. Reflect the rising threat "
        "in the briefing: the pursuit closes in, time runs out, enemies grow bolder. "
        "Above 70/100 you MUST offer acute, desperate situations — attacks, breaches, "
        "failures; calm turns no longer happen.\n\n"
        "WOUND TELEGRAPH: the 'Status' line in the request shows the character's health. "
        "If the character is wounded, the briefing text MUST explicitly reflect the wound — "
        "alarm rises with severity: lightly wounded — your wounds ache, but you are holding on; "
        "moderately wounded — your wounds are serious, your concentration is slipping; "
        "critically wounded — you are at death's door: the next wound will most likely be "
        "your last.\n\n"
        "HULL TELEGRAPH: the 'Ship status' line in the request shows the ship's integrity. "
        "Below 40/100 hull the briefing MUST reflect the ship's grave condition — alarms, "
        "torn compartments, emergency sirens. Below 20/100 the ship is at death's door: "
        "the next hit may well be the last.\n\n"
        "Every action choice MUST include a consequence_kind field — its type:\n"
        "  'progress' — a mission-advancing action: success moves toward the goal, "
        "    opens new opportunities and discoveries;\n"
        "  'injury'   — an action that leads to a WOUND (no death): trauma, health "
        "    damage, temporary incapacitation;\n"
        "  'fatal'    — a DEADLY action: leads to the death of a crew member "
        "    (the character themselves or another).\n"
        "  'delay'    — system hesitation (an emergency code fallback when a "
        "    choice fails): NEVER generate it yourself — it is assigned by code only.\n"
        "The count of each type is specified in the request; a choice's consequence_kind "
        "must match its group.\n\n"
        "CRITICAL RULE: the action text MUST NOT reveal its consequence_kind. "
        "The player chooses BLIND — the fatal and progress actions must be described so "
        "that the outcome cannot be guessed from the text.\n\n"
        "If the request includes crew dialogue context — you MUST take it into account: "
        "the briefing and action choices should reflect what was discussed, the risks and "
        "opportunities raised. If the character spoke in that dialogue, their choices must "
        "be consistent with their line."
    )


# ── Shared per-turn background prompt ───────────────────────────────

# Canonical location keys, kept ONLY for tools/prompt_optimizer (its historical
# scene_instruction trainset validates against this list). Runtime no longer
# uses per-location backgrounds: each turn gets ONE shared scene background
# generated from its setting/conflict (see build_turn_background_prompts).
BACKGROUND_LOCATION_TYPES = [
    "bridge",
    "engineering",
    "sickbay",
    "lab",
    "corridor",
    "exterior_ship",
    "planet_surface",
    "main_screen",
]


def build_turn_background_prompts(
    language: str,
    setting: str,
    conflict: str,
) -> tuple[str, str]:
    """Build (system, user) prompts for the shared per-turn background LLM call.

    The model returns ONE English txt2img prompt describing the turn's shared
    scene — the environment where every action of the turn takes place — with
    no characters present. The prompt is later passed to Qwen-Image-Edit as
    the scene description so action instructions reference only objects that
    actually exist in that shared background.
    """
    if language == LANGUAGE_RU:
        system = (
            "Ты — эксперт по cinematic prompt engineering для AI-генерации изображений. "
            "Пишешь промпты для пустых сцен (БЕЗ персонажей) в эстетике sci-fi/space opera. "
            "Промпт всегда на английском: окружение, объекты, освещение, атмосфера, композиция."
        )
        user = (
            f"Обстановка хода (Setting): {setting}\n"
            f"Ситуация хода (Conflict): {conflict or '—'}\n\n"
            "Напиши ОДИН промпт на АНГЛИЙСКОМ для генерации общей сцены этого хода — "
            "место, где происходят все действия экипажа в этом ходу. "
            "Строго БЕЗ персонажей и людей: только окружение, техника, объекты, освещение. "
            "Сцена должна быть конкретной и узнаваемой (интерьер отсека, поверхность планеты, "
            "открытый космос с кораблём — что следует из Setting), с 2-4 заметными объектами, "
            "на которые можно ссылаться при последующей вставке персонажей. "
            "Кинематографичный широкий кадр, детализация, 4K."
        )
        return system, user
    system = (
        "You are an expert cinematic prompt engineer for AI image generation. "
        "You write prompts for empty scenes (NO characters) in a sci-fi/space-opera aesthetic. "
        "The prompt is always English: environment, objects, lighting, atmosphere, composition."
    )
    user = (
        f"Turn setting: {setting}\n"
        f"Turn situation: {conflict or '—'}\n\n"
        "Write ONE English txt2img prompt for this turn's shared scene — the place where "
        "every crew action of the turn takes place. "
        "Strictly NO characters or people: only environment, machinery, objects, lighting. "
        "The scene must be concrete and recognizable (a ship compartment interior, a planet "
        "surface, open space with the ship — whatever the setting implies), with 2-4 notable "
        "objects that character placements can later refer to. "
        "Cinematic wide shot, detailed, 4K quality."
    )
    return system, user


# ── Scene instruction prompt (Qwen-Image-Edit) ─────────────────────

# Few-shot demo blocks were compiled for the old per-location-background flow
# (the LLM picked a background_location) and are invalidated by the shared
# per-turn background rework: inputs/answers no longer match the schema.
# Left empty until re-compiled by tools/prompt_optimizer against the new
# signature (scene_description in, instruction out).
SCENE_INSTRUCTION_DEMOS = {
    LANGUAGE_RU: "",
    LANGUAGE_EN: "",
}

# ── txt2img prompt demos (avatar / npc_avatar / bridge_image) ──────
# Compiled offline by tools/prompt_optimizer against the FLUX.2 [klein]
# txt2img model with a VL judge. The generated prompts are always English.
# Empty by default: the corresponding inline generators in game_server.py
# pick a block up only after a compiled block is pasted here.
AVATAR_PROMPT_DEMOS = {
    LANGUAGE_RU: "",
    LANGUAGE_EN: "",
}

NPC_AVATAR_DEMOS = {
    LANGUAGE_RU: "",
    LANGUAGE_EN: "",
}

BRIDGE_IMAGE_DEMOS = {
    LANGUAGE_RU: "",
    LANGUAGE_EN: "",
}

# ── txt2img generator prompt sources ──────────────────────────────
# The system prompts of the image-prompt LLM calls (player avatar, NPC
# avatars, bridge scene). Module-level constants so tools/prompt_optimizer
# seeds its dspy signature instructions from the exact runtime text instead
# of a hand-copied (and drifting) duplicate.

# The species category is the ANATOMY CONTRACT: it decides whether the avatar
# is a human (two arms, two legs, human face) or an alien being. The free-text
# character description is flavour, but it MUST NOT contradict the species
# category — a Human is never drawn as a six-legged machine even if the
# description rambles about carapaces, and a non-humanoid alien is never
# collapsed back into a uniformed human.
AVATAR_PROMPT_SYSTEM_INTRO = (
    "You are an expert AI art prompt engineer specializing in sci-fi character portraits. "
    "Generate detailed, cinematic-quality image prompts for character avatars."
)

AVATAR_ANATOMY_CONTRACT_HUMAN = (
    "ANATOMY CONTRACT (HIGHEST PRIORITY — overrides anything in the description): "
    "this character is a HUMAN/HUMANOID. The avatar MUST depict a human: exactly two "
    "arms ending in hands, exactly two legs, a human face with eyes/nose/mouth, human "
    "skin. The output prompt MUST NOT contain any of: extra legs, six legs, tentacles, "
    "carapace, exoskeleton, plasma, energy body, absence of face, sensor cluster, "
    "swarm/colony/hive form, parasitic form, symbiotic form. If the character "
    "description below mentions any such non-human element, DISCARD it entirely and "
    "describe a human crew member in a Starfleet uniform for the given role instead. "
    "The traits and description are flavour only — the species is human."
)

AVATAR_ANATOMY_CONTRACT_ALIEN = (
    "CRITICAL RULE: The character description below is the DEFINITIVE source for "
    "the character's appearance. If it describes an alien, non-humanoid, energy, "
    "or symbiotic being — describe their ACTUAL form, NOT human anatomy. "
    'Never default to "face, hair, eyes, upper body" for non-human characters.'
)

AVATAR_PROMPT_SYSTEM_TAIL = (
    "For non-humanoid, energy, and symbiotic beings: invent an appropriate non-human "
    "biological identity (reproductive cycle, colonial structure, plasma resonance, etc.) "
    "that fits their physiology. Do NOT impose human gender concepts (male/female) on "
    "beings whose biology would not have them."
)


def build_avatar_prompt_system(species_category: str) -> str:
    """System prompt for the player-avatar image-prompt LLM call."""
    if species_category in ("human", "humanoid"):
        contract = AVATAR_ANATOMY_CONTRACT_HUMAN
    else:
        contract = AVATAR_ANATOMY_CONTRACT_ALIEN
    return AVATAR_PROMPT_SYSTEM_INTRO + "\n\n" + contract + "\n\n" + AVATAR_PROMPT_SYSTEM_TAIL


# Compiled by tools/prompt_optimizer GEPA (night run 2026-09-18, judge
# Qwen3.8-27B): baseline 59.5 -> compiled 66.0 on a 10-case dev set that is
# half energy/non_humanoid/symbiotic (internal valset peak 0.712). Absorbs
# the species rules into the instruction itself; see
# tools/prompt_optimizer/compiled/npc_avatar_ru_gepa.json for the original.
NPC_AVATAR_PROMPT_SYSTEM = """You are an expert AI art prompt engineer specializing in sci-fi character portraits. Your task is to generate a single, high-quality image generation prompt (in English) based on the provided character inputs.

### Input Format
You will receive the following fields:
- `role_name`: The character's job or title (often in Russian).
- `species`: One of `human`, `humanoid`, `non_humanoid`, `cybernetic`, `energy`, or `symbiotic`.
- `gender_line`: A mandatory description of gender and facial features (e.g., "a man, masculine facial features", "a woman, feminine features", "an androgynous person...").
- `traits`: A list of personality traits (often in Russian).

### Core Guidelines
1. **Language**: The output prompt must be in **English**.
2. **Tone & Style**: The prompt should be descriptive, cinematic, and suitable for high-end AI image generators (Midjourney/DALL-E 3 style). Include lighting, composition, and quality tags (e.g., "8k", "hyper-detailed", "cinematic lighting", "concept art").
3. **Gender Consistency**: For `human`, `humanoid`, and `cybernetic` species, you **MUST** explicitly incorporate the `gender_line` into the description. Ensure facial features, hair, and build align with the specified gender. Never default to male if the input specifies female or androgynous.
4. **Non-Humanoid/Abstract Beings**: For `non_humanoid`, `energy`, and `symbiotic` species, do **NOT** impose human gender concepts. Instead, invent appropriate biological or physical identities (e.g., colonial structure, plasma resonance, hive mind) that fit the physiology.

### Species-Specific Rules (FOLLOW EXACTLY)

#### 1. `human`
- **Description**: Standard human anatomy.
- **Focus**: Face, expression, uniform details.
- **Composition**: Portrait style, upper body shot.
- **Requirement**: Describe the uniform (starship command, medical, etc.) and rank insignia if implied by the role.

#### 2. `humanoid`
- **Description**: Human-like silhouette but with subtle alien features.
- **Focus**: Unusual skin/hair/eye color, distinct ears/ridges, bioluminescent markings, etc.
- **Composition**: Portrait style, upper body view.
- **Requirement**: The character must still look largely human but with clear non-human traits.

#### 3. `non_humanoid`
- **Description**: Alien anatomy (tentacles, carapace, exoskeleton, crystalline structure, multiple limbs, amorphous form, hive cluster).
- **Prohibitions**:
  - NO two arms ending in hands.
  - NO two legs.
  - NO human face or hair.
  - NOT a bipedal silhouette.
  - NO uniform or clothing.
- **Composition**: Full body or 3/4 view showing the alien physiology.
- **Style**: Alien creature concept art. Start the prompt with the creature itself (e.g., "A towering crystalline entity...").

#### 4. `cybernetic`
- **Description**: Mechanical or cybernetic body (metal, circuits, synthetic components, digital displays).
- **Focus**: If part-organic, highlight the blend of biological and mechanical. Do NOT default to a plain human with robot parts.
- **Composition**: Full body or 3/4 view.
- **Style**: Start the prompt with the species/mechanical description.

#### 5. `energy`
- **Description**: Energy being composed of energy, plasma, or light. NO solid physical body.
- **Prohibitions**:
  - NO solid body.
  - NO face.
  - NO limbs (arms/legs).
  - NO uniform or clothing.
- **Focus**: Visual signature (glow, frequency patterns, luminosity, color spectrum).
- **Composition**: Full body view (abstract).
- **Style**: Abstract energy-being concept art. Start the prompt with the energy-form description.

#### 6. `symbiotic`
- **Description**: A hybrid of multiple organisms.
- **Prohibitions**:
  - NO default to a single humanoid body.
  - NO two arms/two legs/human face.
  - NO uniform or clothing.
- **Focus**: Describe how different parts coexist (e.g., plant-like vines merging with insectoid chitin).
- **Composition**: Full body view.
- **Style**: Alien creature concept art. Start the prompt with the composite nature.

### Output Format
Return only the generated prompt string. Do not include explanations or markdown formatting other than the text itself.

### Example Logic
- If `species` is `human` and `role_name` is "Капитан" (Captain), describe a dignified human in a command uniform.
- If `species` is `non_humanoid`, ignore `gender_line` for biological assignment and focus on alien anatomy, ensuring no human traits are present.
- If `species` is `energy`, describe light/plasma forms, not a person."""

# Anti-collapse rules for alien species: without them the txt2img model's
# humanoid prior collapses energy beings and alien creatures into a standing
# uniformed human. Shared with tools/prompt_optimizer so the optimizer measures
# the same contract the runtime enforces.
NPC_AVATAR_SPECIES_RULES = {
    "human": "The character is human. Describe face, expression, uniform details. Full body view, isolated character cutout.",
    "humanoid": "The character is humanoid — subtle alien features (unusual skin/hair/eye color, distinct ears/ridges, etc.) but overall human-like silhouette. Full body view, isolated character cutout.",
    "non_humanoid": (
        "The creature is NON-HUMANOID — alien anatomy (tentacles, carapace, exoskeleton, crystalline "
        "structure, multiple limbs, amorphous form, hive cluster, etc.). "
        "The image MUST NOT look like a human or humanoid: NO two arms ending in hands, NO two legs, "
        "NO human face or hair, NOT a bipedal silhouette. The creature does NOT wear a uniform or clothing. "
        "Start the prompt with the creature itself (e.g. 'A towering crystalline entity', 'A mass of pulsating bio-gel', "
        "'An insectoid being with chitinous armor'). Alien creature concept art, NOT a Star Trek officer. "
        "Full body or 3/4 view showing the alien physiology."
    ),
    "cybernetic": "The character is CYBERNETIC/SYNTHETIC — mechanical or cybernetic body (metal, circuits, synthetic components, digital displays). If part-organic, highlight the blend of biological and mechanical. Do NOT default to a plain human with robot parts. Start the prompt with the species/mechanical description. Full body or 3/4 view.",
    "energy": (
        "The being is an ENERGY BEING — NO solid physical body, composed of energy, plasma, or light. "
        "Describe the visual signature (glow, frequency patterns, luminosity). "
        "The image MUST NOT resemble a human: NO solid body, NO face, NO limbs, NO two arms/two legs. "
        "The being does NOT wear a uniform or clothing. Start the prompt with the energy-form description. "
        "Abstract energy-being concept art, NOT a Star Trek officer. Full body view."
    ),
    "symbiotic": (
        "The creature is a SYMBIOTIC/COMPOSITE being — a hybrid of multiple organisms. Describe how different "
        "parts coexist. The image MUST NOT default to a single humanoid body: NO two arms/two legs/human face. "
        "The creature does NOT wear a uniform or clothing. Start the prompt with the composite nature. "
        "Alien creature concept art, NOT a Star Trek officer. Full body view."
    ),
}

BRIDGE_IMAGE_PROMPT_SYSTEM = (
    "You are an expert cinematic prompt engineer for AI image generation. "
    "Create detailed English prompts for a cinematic starship bridge scene "
    "showing recognizable crew members in action. The image must depict the "
    "crew — their faces, bodies, and poses — never a floor plan, schematic, "
    "or architectural diagram. Focus on composition, lighting, the crew, "
    "and a space opera aesthetic. "
    "When reference pictures of the crew are provided, the prompt is a "
    "composition instruction for a multi-reference image model: stage the "
    "characters BY their picture numbers (\"the character from Picture N\") "
    "and reinforce each one's key visual traits (\"EXACTLY as in Picture N: "
    "same <traits>\") — restaging detail, not a fresh description."
)

BRIDGE_IMAGE_VIEWPOINT_RULE = (
    "The viewpoint MUST be at "
    "crew level — NEVER overhead, bird's-eye, top-down, isometric, satellite, "
    "or any floor-plan / architectural-diagram view."
)




def build_scene_instruction_system(language: str) -> str:
    """System prompt for the Qwen-Image-Edit scene-instruction LLM call.

    Qwen-Image-Edit understands instruction-style prompts that refer to the
    reference images as "Picture 1" (character) and "Picture 2" (background).
    Picture 2 is the turn's SHARED background: the whole crew acts in the same
    scene, so the instruction must stage the action inside that exact scene
    instead of inventing a new environment.
    """
    if language == LANGUAGE_RU:
        return (
            "Ты — эксперт по написанию инструкций для AI image-editing модели Qwen-Image-Edit. "
            "Модель получает два изображения: Picture 1 — персонаж (аватар), Picture 2 — ОБЩИЙ фон "
            "сцены текущего хода (в нём действуют все члены экипажа в этом ходу). "
            "Напиши инструкцию на АНГЛИЙСКОМ, как разместить персонажа из Picture 1 в сцену из "
            "Picture 2: поза, действие, положение относительно объектов сцены, освещение. "
            "Опирайся ТОЛЬКО на объекты, которые есть в описании сцены (и объекты из самого "
            "действия) — НЕ добавляй новых локаций, планет, кораблей, чёрных дыр и прочего, "
            "чего нет в сцене. "
            "Описание персонажа и его идентичность НЕ повторяй — модель сохранит их сама. "
            "Фокус на действии и постановке внутри заданной сцены."
        )
    return (
        "You are an expert at writing instructions for the Qwen-Image-Edit AI model. "
        "The model receives two images: Picture 1 — a character (avatar), Picture 2 — the SHARED "
        "scene background of the current turn (the whole crew acts in this same scene). "
        "Write an ENGLISH instruction on how to place the character from Picture 1 into the scene "
        "of Picture 2: pose, action, position relative to the scene's objects, lighting. "
        "Refer ONLY to objects present in the scene description (and objects named by the action "
        "itself) — do NOT add new locations, planets, ships, black holes or anything not in the scene. "
        "Do NOT restate the character's description or identity — the model preserves it automatically. "
        "Focus on the action and staging within the given scene."
    )


def build_scene_instruction_user(
    language: str,
    action_text: str,
    species_desc: str,
    scene_description: str,
    scene_context: str,
    species_category: str = "",
) -> str:
    """User prompt for the scene-instruction LLM call.

    Note: the character's role/title is intentionally NOT included. Mentioning a
    role like "Scientific Officer" biases Qwen-Image-Edit toward a human in
    uniform, overriding the non-humanoid avatar in Picture 1. The model must
    preserve the character from Picture 1 as-is.

    For non-humanoid / energy / symbiotic beings (``species_category``) an
    additional guard is emitted: Qwen-Image-Edit strongly trusts the text
    instruction over Picture 1, so describing "arms outstretched, hands open,
    facial expression" for a cluster of crystals collapses the form back into a
    humanoid. The guard forbids human-anatomy terms and asks the LLM to phrase
    the action through the physics of the being's form instead.

    Args:
        scene_description: English description of the turn's SHARED background
            (Picture 2, as generated by build_turn_background_prompts). The
            instruction must stage the action inside this exact scene and use
            only its objects. Empty when no shared background exists — the
            instruction then stages the action from scene_context alone.
        scene_context: Free-form description of the current turn's setting and
            situation (typically global_circumstances setting + conflict).
        species_category: Canonical species key (human / humanoid / non_humanoid
            / energy / cybernetic / symbiotic). Empty string = human fallback.
            Only non_humanoid / energy / symbiotic trigger the anatomy guard.
    """
    if scene_description:
        scene_block_ru = f"\nСцена из Picture 2 (общий фон хода, БЕЗ персонажей): {scene_description}\n"
        scene_block_en = f"\nPicture 2 scene (the turn's shared background, NO characters in it): {scene_description}\n"
    else:
        scene_block_ru = ""
        scene_block_en = ""
    ctx_block = f"\nScene context: {scene_context}\n" if scene_context else ""
    anatomy_guard_ru = ""
    anatomy_guard_en = ""
    if species_category in ("non_humanoid", "energy", "symbiotic"):
        anatomy_guard_ru = (
            "\nВАЖНО: персонаж относится к не-гуманоидному/энергетическому/симбиотическому "
            "виду (категория " + species_category + "). "
            "НЕ описывай человеческую анатомию — никаких «рук», «кистей», «ладоней», «ног», "
            "«лица», «выражения лица», «глаз», «улыбки». "
            "Также ИЗБЕГАЙ композиционных маркеров вертикальной гуманоидной позы: "
            "«стоит / standing», «в центре / centered», «стоит на ногах / grounded», "
            "«за консолью / at a console», «за пультом / at the station», «сидит / seated», "
            "«вертикально / upright», «позирует / posing», «лицом к камере / facing the camera». "
            "Qwen-Image-Edit доверяет тексту больше, чем Picture 1, и свернёт форму в стоящего "
            "человека, если встретит анатомические ИЛИ эти композиционные слова. "
            "Описывай позу и действие ТОЛЬКО через физику формы: парит (floating), завис (hovering), "
            "дрейфует (drifting), взвешен в пространстве (suspended in space), пульсирует (pulsating), "
            "расширяется/сжимается (expanding/contracting), растекается (flowing), рассеивается "
            "(dissipating), меняет форму (shifting form), обращается вокруг (orbiting), излучает "
            "(radiating), сгущается (coalescing). Указывай ориентацию в пространстве (над/между/"
            "рядом с объектами), а не «стоит в центре».\n"
        )
        anatomy_guard_en = (
            "\nIMPORTANT: this character is a non-humanoid / energy / symbiotic being "
            "(category " + species_category + "). "
            "Do NOT impose human anatomy — no \"arms\", \"hands\", \"fingers\", \"legs\", "
            "\"face\", \"facial expression\", \"eyes\", \"smile\". "
            "ALSO AVOID vertical-humanoid composition markers: \"standing\", \"centered\", "
            "\"grounded\", \"at a console\", \"at the station\", \"seated\", \"upright\", "
            "\"posing\", \"facing the camera\". "
            "Qwen-Image-Edit trusts the text instruction over Picture 1 and will collapse the "
            "form into a standing human if it sees the anatomical OR these composition words. "
            "Describe the pose and action ONLY through the physics of the being's form: floating, "
            "hovering, drifting, suspended in space, pulsating, expanding/contracting, flowing, "
            "dissipating, shifting form, orbiting, radiating, coalescing. Give the spatial "
            "orientation (above / between / next to objects) rather than \"standing in the center\".\n"
        )
    if language == LANGUAGE_RU:
        user = (
            f"Действие: {action_text}\n"
            f"Описание вида: {species_desc}{scene_block_ru}{ctx_block}\n"
            f"{anatomy_guard_ru}"
            "Напиши ОДНУ инструкцию (1-3 предложения) для Qwen-Image-Edit, "
            "начиная с 'Place the character from Picture 1...'. "
            "Персонаж действует ВНУТРИ сцены из Picture 2: привяжи позу и действие к конкретным "
            "объектам этой сцены (у консоли, над столом, между механизмами — что реально есть "
            "в описании сцены). Освещение бери из сцены. "
            "НЕ добавляй объекты и локации, которых нет в описании сцены и в действии. "
            "Без описания внешности персонажа."
        )
    else:
        user = (
            f"Action: {action_text}\n"
        f"Species: {species_desc}{scene_block_en}{ctx_block}\n"
        f"{anatomy_guard_en}"
        "Write ONE instruction (1-3 sentences) for Qwen-Image-Edit, "
        "starting with 'Place the character from Picture 1...'. "
        "The character acts INSIDE the scene of Picture 2: anchor the pose and action to concrete "
        "objects of that scene (at a console, above the table, between the machinery — whatever "
        "is actually in the scene description). Take the lighting from the scene. "
        "Do NOT add objects or locations that are absent from the scene description and the action. "
        "Do not describe the character's appearance."
    )
    demos = SCENE_INSTRUCTION_DEMOS[LANGUAGE_RU if language == LANGUAGE_RU else LANGUAGE_EN]
    if demos:
        user += "\n\n" + demos
    return user


def build_death_notice_prompts(
    language: str,
    character_name: str,
    role: str,
    death_narrative: str,
    outcome_narrative: str,
) -> tuple[str, str]:
    """Build system and user prompts for a dramatic per-character death notice.

    Returns a short evocative title (e.g. «Последний вдох КхаГара») and a
    1-3 sentence dramatic rendering of how the character died, addressed to
    the player in second person. Replaces the canned "Вы погибли при
    исполнении!" line with content tailored to the actual death.

    Args:
        language: Game content language (LANGUAGE_RU / LANGUAGE_EN).
        character_name: Dead character's name (player_name).
        role: Dead character's ship role.
        death_narrative: Personal death description from the LLM's
            personal_outcomes entry (the cause of death).
        outcome_narrative: General outcome narrative (shared context for tone).

    Returns:
        (system_prompt, user_prompt) tuple.
    """
    if language == LANGUAGE_RU:
        system = (
            "Ты — драматичный писатель-фантаст. Описываешь гибель персонажа "
            "эмоционально и кинематографично, обращаясь к игроку на «ты»."
        )
        user = (
            f"Персонаж: {character_name}, роль: {role}.\n"
            f"Причина гибели: {death_narrative}\n"
            f"Контекст хода: {outcome_narrative}\n\n"
            "Сформируй краткий драматичный заголовок (3-7 слов, без звёздочек и "
            "кавычек) и короткое описание гибели (1-3 предложения) от второго "
            "лица, как эпитафию герою. Описание должно передавать, КАК именно "
            "он погиб, опираясь на причину гибели. "
            "ВАЖНО: не используй символы звёздочка (*) или подчёркивание (_) — "
            "они сломают форматирование. Только обычный текст. Все тексты на русском."
        )
    else:
        system = (
            "You are a dramatic sci-fi writer. You describe a character's death "
            "emotionally and cinematically, addressing the player in second person."
        )
        user = (
            f"Character: {character_name}, role: {role}.\n"
            f"Cause of death: {death_narrative}\n"
            f"Turn context: {outcome_narrative}\n\n"
            "Compose a short dramatic title (3-7 words, no asterisks or quotes) "
            "and a brief description of the death (1-3 sentences) in second "
            "person, like an epitaph for the hero. The description must convey "
            "HOW exactly they died, based on the cause of death. "
            "IMPORTANT: do not use asterisk (*) or underscore (_) characters — "
            "they break formatting. Plain text only."
        )
    return system, user
