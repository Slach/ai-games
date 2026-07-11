"""
LLM prompt constants for Game Server API
All prompt strings organized by language (ru/en)
"""

from typing import Any

from language import (
    LANGUAGE_EN,
    LANGUAGE_RU,
    get_dimension_tag_field,
    get_dimension_tags,
    get_tag_display_name,
)
from game_rules import FORBIDDEN_OPENINGS, MISSION_ARCHETYPES
from pydantic import BaseModel
from verbalize_sampling import DIVERSITY_HINTS, verbalize_prompt


class OnboardingQuestion(BaseModel):
    """A single onboarding question"""

    id: int
    text: str
    options: list[dict[str, Any]]
    image_url: str | None = None
    image_prompt: str | None = None


def build_dynamic_sg_question_prompts(
    language: str,
    dimension: str,
    sg_step: int,
    accumulated_tags: dict[str, int],
) -> tuple[str, str]:
    """Build system + user prompts for generating ONE dynamic species/gender question.

    The LLM only authors the question text and one vivid answer label per
    canonical tag. Tags themselves are assigned by the caller, which keeps the
    species/gender determination logic (tag counting) reliable.

    Args:
        language: LANGUAGE_RU / LANGUAGE_EN
        dimension: "species" or "gender"
        sg_step: 1-based index within the alternating S/G/S/G/S sequence
        accumulated_tags: {tag: count} of this dimension picked in prior answers
    """
    tags = get_dimension_tags(dimension)
    tag_field = get_dimension_tag_field(dimension)

    tag_lines = "\n".join(f"  - {tag}: {get_tag_display_name(tag, dimension, language)}" for tag in tags)
    tag_keys_str = ", ".join(tags)

    if accumulated_tags:
        acc_parts = [f"{tag} ({get_tag_display_name(tag, dimension, language)}): {count}" for tag, count in sorted(accumulated_tags.items(), key=lambda x: x[1], reverse=True)]
        accumulated_desc = ", ".join(acc_parts)
    else:
        accumulated_desc = ""

    if language == LANGUAGE_RU:
        if dimension == "species":
            subject = "расы и биологической природы персонажа"
            ask_focus = "Спроси о чём-то, что выявляет природу тела, происхождение, физиологию или способ существования вида — избегай банальных вопросов про 'дом' или 'смерть'. Будь образным и неожиданным: ритуалы, чувства, восприятие, связь со средой."
        else:
            subject = "пола и репродуктивной/идентификационной формы персонажа"
            ask_focus = "Спроси о чём-то, что выявляет половую роль, обращение, идентичность или способ продолжения рода — избегай банальных вопросов. Будь образным и тактичным, без пошлости."

        system = "Ты — креативный нарративный дизайнер sci-fi вселенной в духе Star Trek. Ты сочиняешь живые, нешаблонные вопросы для онбординга, которые помогают игроку определить сущность своего персонажа."
        acc_clause = f"Предыдущие ответы игрока по этому измерению: {accumulated_desc}. Учти это: новый вопрос должен углублять и уточнять, а не повторяться.\n" if accumulated_desc else ""
        user = (
            f"Это вопрос №{sg_step} в серии, определяющей {subject}.\n"
            f"{acc_clause}"
            f"Доступные теги этого измерения (поле «{tag_field}») и их смысл:\n{tag_lines}\n\n"
            f"{ask_focus}\n\n"
            "Сгенерируй:\n"
            "1. text — один вопрос-сценарий (1-2 предложения), творческий и atmospheric, на русском.\n"
            "2. labels — объект, где для КАЖДОГО из перечисленных тегов дан короткий "
            "яркий вариант ответа (2-7 слов), который выбрал бы игрок, чей персонаж "
            "соответствует этому тегу. Варианты должны быть чётко различными по смыслу.\n"
            f"Ключи в labels обязаны быть ровно этими и только ими: {tag_keys_str}.\n"
            "Не добавляй других полей. Весь текст — строго на русском языке."
        )
    else:
        if dimension == "species":
            subject = "the character's species and biological nature"
            ask_focus = (
                "Ask something that reveals the nature of the body, origin, physiology, "
                "or mode of existence of the species — avoid clichéd questions about 'home' or 'death'. "
                "Be imaginative and surprising: rituals, feelings, perception, bond with the environment."
            )
        else:
            subject = "the character's gender and reproductive/identity form"
            ask_focus = "Ask something that reveals gender role, address, identity, or way of reproduction — avoid clichéd questions. Be imaginative and tasteful, never crude."

        system = "You are a creative narrative designer for a Star Trek-style sci-fi universe. You craft vivid, non-generic onboarding questions that help a player define who their character is."
        acc_clause = f"The player's previous answers for this dimension: {accumulated_desc}. Take it into account: the new question should deepen and refine, not repeat.\n" if accumulated_desc else ""
        user = (
            f"This is question #{sg_step} in a series determining {subject}.\n"
            f"{acc_clause}"
            f"Available tags for this dimension (the «{tag_field}» field) and their meaning:\n{tag_lines}\n\n"
            f"{ask_focus}\n\n"
            "Generate:\n"
            "1. text — one scenario question (1-2 sentences), creative and atmospheric, in English.\n"
            "2. labels — an object giving, for EACH listed tag, a short vivid answer option "
            "(2-7 words) that a player whose character matches that tag would pick. "
            "Options must be clearly distinct in meaning.\n"
            f"The keys in labels must be exactly these and only these: {tag_keys_str}.\n"
            "Do not add any other fields. All text must be strictly in English."
        )

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
                    "description": "List of [name, role] who died this turn",
                    "items": {
                        "type": "array",
                        "items": [{"type": "string"}, {"type": "string"}],
                    },
                },
                "ship_destroyed": {
                    "type": "boolean",
                    "description": "Whether the ship was destroyed",
                },
                "ship_hull_integrity": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Ship hull structural integrity percentage (0 = destroyed, 100 = pristine)",
                },
                "ship_shields": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Shield strength percentage (0 = depleted, 100 = full)",
                },
                "ship_systems_offline": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of ship systems that are damaged/offline (e.g. 'warp drive', 'life support', 'weapons', 'communications', 'navigation')",
                },
                "crew_injured": {
                    "type": "array",
                    "description": "List of [name, role, severity] who were injured this turn. Severity: 'critical', 'moderate', 'minor'",
                    "items": {
                        "type": "array",
                        "items": [
                            {"type": "string"},
                            {"type": "string"},
                            {"type": "string"},
                        ],
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
                                "description": "Character name (player name or NPC name)",
                            },
                            "role": {
                                "type": "string",
                                "description": "Role on the ship",
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
                "ship_destroyed",
                "ship_hull_integrity",
                "ship_shields",
                "ship_systems_offline",
                "crew_injured",
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
    "4. next_turn_hook — зацепка для следующего хода, которая создаёт ожидание\n"
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
    "4. next_turn_hook — teaser for the next turn that creates anticipation\n"
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


def build_combined_outcome_prompts(
    language: str,
    *,
    setting: str,
    conflict: str,
    narrative: str,
    previous_summary: str,
    mission_text: str,
    decisions_text: str,
    roster_text: str,
    use_vs: bool = False,
    vs_k: int = 5,
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
            decisions_text=decisions_text,
            roster_text=roster_text,
        )
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

_GAME_OVER_USER_RU = (
    "Исход игры: {outcome_type}\n\n"
    "Последние события:\n{outcome_narrative}\n\n"
    "Статус миссии:\n{mission_summary}\n\n"
    "Напиши финальный нарратив (2-3 абзаца) и промпт для финальной картинки. "
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

_GAME_OVER_USER_EN = (
    "Game outcome: {outcome_type}\n\n"
    "Last events:\n{outcome_narrative}\n\n"
    "Mission status:\n{mission_summary}\n\n"
    "Write a finale narrative (2-3 paragraphs) and an image prompt for the finale scene. "
    "The narrative should wrap up the story and give a sense of closure. "
    "The image should be an epic scene reflecting the finale.\n\n"
    "All text in English."
)


def build_game_over_prompts(
    language: str,
    *,
    outcome_type: str,
    outcome_narrative: str,
    mission_summary: str,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
    """Build system and user prompts for finale/game-over generation.

    Returns:
        (system_prompt, user_prompt)
    """
    if language == LANGUAGE_RU:
        system = _GAME_OVER_SYSTEM_RU
        user = _GAME_OVER_USER_RU.format(
            outcome_type=outcome_type,
            outcome_narrative=outcome_narrative,
            mission_summary=mission_summary,
        )
    else:
        system = _GAME_OVER_SYSTEM_EN
        user = _GAME_OVER_USER_EN.format(
            outcome_type=outcome_type,
            outcome_narrative=outcome_narrative,
            mission_summary=mission_summary,
        )
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["combined_outcome"], k=vs_k)
    return system, user


# ── Onboarding generation prompts ──────────────────────────────────


def build_onboarding_prompts(
    language: str,
    questions_count: int,
    options_count: int,
    role_keys_str: str,
    example_role_scores_json: str,
    underrepresented_hint: str,
) -> tuple[str, str]:
    """Build system and user prompts for onboarding question generation."""
    if language == LANGUAGE_RU:
        system = "Ты — дизайнер игр. Генерируешь вопросы для онбординга в космической игре."
        hint = (
            underrepresented_hint
            if not underrepresented_hint
            else f"🎯 ОСОБОЕ УКАЗАНИЕ: В предыдущих сессиях следующие роли получали меньше всего очков: {underrepresented_hint}. Удели им особое внимание при составлении вопросов — создай для них минимум по 2-3 интересных варианта ответов.\n\n"
        )
        user = (
            f"Сгенерируй {questions_count} вопросов для онбординга в игре про космический экипаж звездного корабля. "
            f"Каждый вопрос — это конкретная ситуация на корабле или во время миссии с выбором из {options_count} вариантов ДЕЙСТВИЙ. "
            "ВАЖНО: Каждый вариант ответа (поле value) должен описывать КОНКРЕТНОЕ ДЕЙСТВИЕ, которое игрок совершает в этой ситуации. "
            "ПРИМЕР правильных вариантов: 'Бежать в машинное отделение и попытаться починить варп-двигатель', "
            "'Активировать аварийные щиты и вызвать подкрепление'. "
            "НЕПРАВИЛЬНО: 'Инженер — технический специалист', 'Учёный – смелый, ищущий прорыв'. "
            "НЕПРАВИЛЬНО: 'A', 'B', 'C' — варианты должны быть ПОЛНЫМИ описаниями действий! "
            "Никогда не указывайте название роли или тип личности в вариантах ответа — только действия. "
            "Каждый вариант (value) должен быть развёрнутым предложением минимум из 5-7 слов, описывающим конкретное действие. "
            "КРИТИЧНО: Соблюдай ограничения по длине текста для Telegram! Поле text вопроса — НЕ БОЛЕЕ 160 символов. "
            "Каждый вариант ответа (поле value) — НЕ БОЛЕЕ 150 символов. "
            "Это нужно, чтобы весь текст вопроса с вариантами поместился в подпись к картинке в Telegram (лимит 1024 символа). "
            "КРИТИЧНО: Все варианты ответа в одном вопросе должны быть РАЗЛИЧНЫМИ и описывать РАЗНЫЕ действия. "
            "Не допускай одинаковых или очень похожих вариантов — каждый должен представлять уникальный подход. "
            "Вопросы должны покрывать разные аспекты: реакция на опасность, работа с техникой, взаимодействие с экипажем, "
            "исследование неизвестного, принятие решений в кризисе. "
            "Все тексты на русском языке.\n\n"
            "КРИТИЧНО: Каждый вариант ответа (option) должен содержать поле role_scores — это объект с очками для ролей. "
            f"Доступные роли (ключи): {role_keys_str}. "
            "Каждому варианту назначь от 1 до 3 ролей, которым это действие больше всего подходит, с очками от 1 до 3. "
            "Остальным ролям поставь 0. Очки отражают насколько выбранное действие характерно для данной роли. "
            "ПРИМЕР role_scores для действия 'Починить варп-двигатель': "
            f"{example_role_scores_json}. "
            "ВАЖНО: В каждом вопросе варианты должны давать очки РАЗНЫМ ролям — чтобы каждый вопрос помогал отличать игроков.\n\n" + hint + "Текст вопроса (text) и все варианты ответов (value) — строго НА РУССКОМ ЯЗЫКЕ.\n"
            "Поле image_prompt — это отдельное поле в JSON, которое должно быть НА АНГЛИЙСКОМ ЯЗЫКЕ (для генерации картинок).\n"
            "ВАЖНО: image_prompt должен визуализировать ТУ ЖЕ САМУЮ СЦЕНУ, что описана в text — то же место, та же ситуация. "
            "Например, если text про обнаружение сигнала снаружи корабля, image_prompt должен показывать космос/объект снаружи, а не лабораторию внутри. "
            "Для КАЖДОГО вопроса сгенерируй image_prompt — детальный промпт на АНГЛИЙСКОМ для генерации изображения сцены. "
            "Промпт должен быть кинематографичным, sci-fi/space opera, 4K. "
            "Пример ТОЛЬКО для поля image_prompt (не для текста вопроса): "
            '"A starship bridge with holographic star maps glowing in blue light, crew members at their stations, cinematic lighting, epic sci-fi atmosphere, 4K quality."'
            " Отделяй русский текст вопроса от английского image_prompt. "
        )
    else:
        system = "You are a game designer. Generate onboarding questions for a space exploration game."
        hint = (
            underrepresented_hint
            if not underrepresented_hint
            else f"🎯 SPECIAL NOTE: The following roles have received the fewest points in previous sessions: {underrepresented_hint}. Pay special attention to them — create at least 2-3 interesting answer options for each.\n\n"
        )
        user = (
            f"Generate {questions_count} onboarding questions for a starship crew game. "
            f"Each question is a specific situation aboard a ship or during a mission with {options_count} ACTION choices. "
            "CRITICAL: Each option (the value field) must describe a SPECIFIC ACTION the player would take in this situation. "
            "CORRECT example: 'Run to engineering and try to repair the warp drive', "
            "'Activate emergency shields and call for backup'. "
            "INCORRECT: 'Engineer - technical specialist', 'Scientist - bold, seeking breakthrough'. "
            "INCORRECT: 'A', 'B', 'C' — options must be FULL action descriptions, NOT single letters! "
            "NEVER include role names or personality types in options — only actions. "
            "Each option (value) must be a detailed sentence of at least 5-7 words describing a specific action. "
            "CRITICAL: Respect Telegram character limits! The text field (question text) — MAX 160 characters. "
            "Each option value — MAX 150 characters. "
            "This is needed so the full question text with options fits in a Telegram photo caption (1024 char limit). "
            "CRITICAL: All answer options for one question must be DIFFERENT and describe DIFFERENT actions. "
            "Do not allow identical or very similar options — each should represent a unique approach. "
            "Questions should cover different aspects: danger response, technical work, crew interaction, "
            "exploration, crisis decision-making. "
            "All text in English.\n\n"
            "CRITICAL: Each option must include a role_scores field — an object mapping roles to points. "
            f"Available roles (keys): {role_keys_str}. "
            "Assign 1-3 roles that this action best suits, with 1-3 points each. "
            "Set all other roles to 0. Points reflect how characteristic this action is for that role. "
            "EXAMPLE role_scores for 'Repair the warp drive': "
            f"{example_role_scores_json}. "
            "IMPORTANT: Options in each question should give points to DIFFERENT roles — so each question helps distinguish players.\n\n" + hint + "Question text (text) and all option values — strictly in ENGLISH.\n"
            "IMPORTANT: image_prompt must visualize the EXACT SAME SCENE as described in text — same location, same situation. "
            "For example, if text is about detecting a signal outside the ship, image_prompt should show space/the object outside, not a lab interior. "
            "For EACH question generate an image_prompt — a detailed English prompt for the scene image. "
            "The prompt should be cinematic, sci-fi/space opera, 4K quality. "
        )
    return system, user


# ── Game title generation prompts ──────────────────────────────────


def build_game_title_prompts(language: str, *, use_vs: bool = False, vs_k: int = 5) -> tuple[str, str]:
    """Build system and user prompts for game title generation."""
    if language == LANGUAGE_RU:
        system = "Ты — креативный писатель-фантаст. Придумываешь названия и описания для космических приключений."
        user = (
            "Придумай название для игры про экипаж звездного корабля и приветственное сообщение. "
            "Название должно быть в формате: название корабля + подзаголовок миссии. "
            "Пример стиля: «Звёздный Крейсер Аврора: За горизонтом известного». "
            "Приветствие должно быть атмосферным — будто игрок заходит на борт корабля. "
            "ВАЖНО: не используй символы звёздочка (*) или подчёркивание (_) в тексте приветствия — "
            "они сломают форматирование при отправке игроку. Используй только обычный текст. "
            "Все тексты на русском языке."
        )
    else:
        system = "You are a creative sci-fi writer. You create titles and descriptions for space adventures."
        user = (
            "Create a title for a starship crew game and a welcome message. "
            "Title format: ship name + mission tagline. "
            "Example style: 'Star Cruiser Aurora: Beyond the Known Horizon'. "
            "The welcome should be atmospheric — as if the player is stepping aboard the ship. "
            "IMPORTANT: do not use asterisk (*) or underscore (_) characters in the welcome text — "
            "they will break formatting when sent to the player. Use plain text only. "
            "All text in English."
        )
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["game_title"], k=vs_k)
    return system, user


# ── Daily story prompts ────────────────────────────────────────────


def build_turn_story_prompts(language: str, turn: int, previous_summary: str, player_role: str, *, use_vs: bool = False, vs_k: int = 5) -> tuple[str, str]:
    """Build system and user prompts for daily story generation."""
    if language == LANGUAGE_RU:
        system = "Ты — Game Master космической исследовательской игры в стиле Star Trek. Создаёшь увлекательные ежедневные эпизоды с конфликтами и выбором."
        player_role_display = player_role or "Член экипажа"
        user = (
            f"Ход: {turn}\n"
            f"Предыдущий день: {previous_summary or 'Первый день миссии'}\n"
            f"Роль игрока: {player_role_display}\n\n"
            "Создай эпизод с:\n"
            "1. Место действия (космос, станция, планета)\n"
            "2. Центральный конфликт или тайна\n"
            "3. 3 точки выбора для игрока с действиями и скрытыми последствиями\n\n"
            "Всё на русском языке."
        )
    else:
        system = "You are a Game Master for a Star Trek-style space exploration game. Create compelling daily episodes with conflicts and player choices."
        player_role_display = player_role or "Crew member"
        user = (
            f"Turn: {turn}\n"
            f"Previous turn: {previous_summary or 'First turn of mission'}\n"
            f"Player role: {player_role_display}\n\n"
            "Create an episode with:\n"
            "1. A setting (space location, station, planet)\n"
            "2. A central conflict or mystery\n"
            "3. 3 decision points for the player with visible actions and hidden consequences\n"
        )
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["turn_story"], k=vs_k)
    return system, user


# ── NPC dialogue prompt builders ───────────────────────────────────


def build_npc_dialogue_lang_note(language: str, player_role: str) -> tuple[str, str]:
    """Build language note and player role display for NPC dialogue."""
    if language == LANGUAGE_RU:
        return "Отвечай на русском.", player_role or "Член экипажа"
    return "Respond in English.", player_role or "Crew member"


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
    game_title: str = "",
    mission_name: str = "",
    mission_description: str = "",
    mission_objectives: str = "",
    turn: int = 1,
    previous_turn_summary: str = "",
    global_circumstances_setting: str = "",
    global_circumstances_conflict: str = "",
    global_circumstances_narrative: str = "",
    crew_context: str = "",
    use_vs: bool = False,
    vs_k: int = 5,
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


def build_species_description_prompts(
    language: str,
    role: str,
    species_display: str,
    species_secondary: str | None,
    species_hybrid: bool,
    gender_display: str,
    gender_secondary: str | None,
    gender_hybrid: bool,
    *,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
    """Build system and user prompts for species description generation."""
    if language == LANGUAGE_RU:
        species_note = f"Тип расы: {species_display}" + (f" (гибрид с {species_secondary})" if species_hybrid else "") + f"\nТип пола: {gender_display}" + (f" (гибрид с {gender_secondary})" if gender_hybrid else "")
        system = "Ты — креативный писатель-фантаст, создающий описания инопланетных персонажей. Опиши, как выглядят и ощущают себя существа такого типа. Будь атмосферным и детальным."
        user = (
            f"Создай яркое нарративное описание персонажа для космической игры Star Trek.\n\n"
            f"Роль: {role}\n"
            f"{species_note}\n\n"
            f"Опиши:\n"
            f"1. Как выглядит и ощущает себя это существо (внешность, физиология, текстура, свечение и т.д.)\n"
            f"2. Как пол/форма размножения проявляется в их культуре и самовосприятии\n"
            f"3. Единый образ — как расовые и половые черты сливаются в одну личность\n\n"
            f"Текст на русском языке, 3-5 предложений, атмосферный и кинематографичный."
        )
    else:
        species_note = f"Species type: {species_display}" + (f" (hybrid with {species_secondary})" if species_hybrid else "") + f"\nGender type: {gender_display}" + (f" (hybrid with {gender_secondary})" if gender_hybrid else "")
        system = "You are a creative sci-fi writer crafting descriptions of alien characters. Describe how beings of this type look and feel. Be atmospheric and detailed."
        user = (
            f"Create a vivid narrative description of a character for a Star Trek-style space game.\n\n"
            f"Role: {role}\n"
            f"{species_note}\n\n"
            f"Describe:\n"
            f"1. How this being looks and feels (appearance, physiology, texture, glow, etc.)\n"
            f"2. How their gender/reproductive form manifests in their culture and self-perception\n"
            f"3. A unified image — how species and gender traits merge into one personality\n\n"
            f"Text in English, 3-5 sentences, atmospheric and cinematic."
        )
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["species_description"], k=vs_k)
    return system, user


# ── NPC decision prompts
# ── NPC decision prompts ───────────────────────────────────────────


def build_npc_decision_prompts(
    language: str,
    npc_name: str,
    npc_role: str,
    traits: str | list[str],
    choices_text: str,
    *,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
    """Build system and user prompts for NPC decision making."""
    traits_str = ", ".join(traits) if isinstance(traits, list) else traits
    if language == LANGUAGE_RU:
        system = f"Ты — {npc_name}, {npc_role} на космическом корабле. Твой характер: {traits_str}. Ты видишь ТОЛЬКО описания действий без последствий. Сделай выбор на основе своей личности и роли."
        user = f"Текущая ситуация на корабле требует твоего решения.\n\nДоступные действия:\n{choices_text}\n\nВыбери одно действие, которое лучше всего соответствует твоему характеру и роли. Ты не знаешь последствий — действуй интуитивно."
    else:
        system = f"You are {npc_name}, {npc_role} aboard a starship. Your personality: {traits_str}. You see ONLY action descriptions with no consequences. Make a choice based on your personality and role."
        user = f"The current situation requires your decision.\n\nAvailable actions:\n{choices_text}\n\nChoose the action that best matches your character and role. You don't know the consequences — act on instinct."
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
    use_vs: bool = False,
    vs_k: int = 5,
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


def build_global_circumstances_prompts(
    language: str,
    turn: int,
    previous_summary: str,
    player_descriptions: str,
    mission_str: str,
    *,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
    """Build system and user prompts for global circumstances generation."""
    if language == LANGUAGE_RU:
        system = (
            "Ты — Game Master космической игры в стиле Star Trek. "
            "Создаёшь ОБЩИЕ обстоятельства дня — ситуацию, которая происходит на корабле или вокруг него. "
            "Эти обстоятельства едины для всех членов экипажа.\n\n"
            "Используй ПОЛНЫЕ ИМЕНА персонажей из списка экипажа в нарративе. "
            "У каждого члена экипажа есть уникальное имя — обращайся к ним по имени.\n"
        )
        user = (
            f"Ход: {turn}\n"
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
            "Each crew member has a unique name — refer to them by name.\n"
        )
        user = (
            f"Turn: {turn}\n"
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
    crew_desc: str,
    archetype: str | None = None,
    seeds: dict | None = None,
    *,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
    """Build system and user prompts for mission generation.

    When archetype/seeds are provided they are injected to force variety (P2);
    a banned-trope list and a balanced threshold range are always included.
    """
    lang = LANGUAGE_RU if language == LANGUAGE_RU else LANGUAGE_EN
    forbidden = ", ".join(FORBIDDEN_OPENINGS[lang])
    arch_hint = ""
    if archetype and archetype in MISSION_ARCHETYPES:
        arch_hint = f"{archetype} — {MISSION_ARCHETYPES[archetype][lang]}"

    if seeds:
        if lang == LANGUAGE_RU:
            seeds_block = f"\nОБЯЗАТЕЛЬНЫЕ элементы миссии (используй их):\n- Место: {seeds.get('setting', '')}\n- Осложнение: {seeds.get('complication', '')}\n- Возможный поворот: {seeds.get('twist', '')}\n- Награда: {seeds.get('reward', '')}\n\n"
        else:
            seeds_block = f"\nMANDATORY mission elements (use them):\n- Setting: {seeds.get('setting', '')}\n- Complication: {seeds.get('complication', '')}\n- Possible twist: {seeds.get('twist', '')}\n- Reward: {seeds.get('reward', '')}\n\n"
    else:
        seeds_block = ""

    if lang == LANGUAGE_RU:
        system = "Ты — Game Master космической игры. Создаёшь миссию для экипажа звёздного корабля. Миссия делится на 2-4 этапа (stages), каждый с прогрессом от 1 до 10." + (f"\nАрхетип миссии: {arch_hint}" if arch_hint else "")
        user = (
            f"Экипаж:\n{crew_desc}\n\n"
            f"{seeds_block}"
            "ЗАПРЕЩЕНО начинать миссию с клише про сигнал бедствия / перехваченный сигнал / "
            f"неопознанную передачу. Запрещённые завязки: {forbidden}.\n"
            "Создай миссию с:\n"
            "1. Название миссии — только кодовое имя и описание (формат: 'Кодовое имя: описание'). "
            "ВАЖНО: слово 'Миссия' в названии НЕ пиши — оно будет добавлено автоматически в интерфейсе.\n"
            "2. Описание — что нужно сделать, 2-3 абзаца\n"
            "3. short_description — сжатое описание миссии в 1-2 предложениях, "
            "не более 500 символов (используется для подписей к картинкам с ограничением длины)\n"
            "4. 2-4 этапа с целями, каждый с success_threshold в диапазоне 3-5\n"
            "Этапы должны быть последовательными, но достижимыми нелинейно.\n"
            "Всё на русском языке."
        )
    else:
        system = "You are a Game Master. Create a mission for a starship crew. The mission is divided into 2-4 stages, each with progress from 1 to 10." + (f"\nMission archetype: {arch_hint}" if arch_hint else "")
        user = (
            f"Crew:\n{crew_desc}\n\n"
            f"{seeds_block}"
            "DO NOT start the mission with the cliché of a distress signal / intercepted signal / "
            f"unidentified transmission. Forbidden openings: {forbidden}.\n"
            "Create a mission with:\n"
            "1. Mission name — code name and description only (format: 'Code Name: description'). "
            "IMPORTANT: do NOT include the word 'Mission' in the name — it will be added automatically by the UI.\n"
            "2. Description — what needs to be done, 2-3 paragraphs\n"
            "3. A short_description — condensed 1-2 sentence summary of the mission, "
            "no more than 500 characters (used for image captions with length limits)\n"
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


def build_personal_briefing_system(language: str) -> str:
    """Build system prompt for personal briefing generation."""
    if language == LANGUAGE_RU:
        return (
            "Ты — Game Master космической игры. Создаёшь ПЕРСОНАЛЬНУЮ вводную для игрока, "
            "основываясь на общих обстоятельствах дня. "
            "Каждый игрок видит ситуацию со своей уникальной точки зрения.\n\n"
            "Каждый ход ДОЛЖЕН ДВИГАТЬ ИСТОРИЮ ВПЕРЁД — неожиданные повороты, открытия, "
            "новые союзники или враги, находки, ухудшение или улучшение ситуации. "
            "Смелые и правильные решения → миссия продвигается, открываются новые возможности. "
            "Пассивные или плохие решения → последствия: повреждения, потери, регресс миссии. "
            "Главное — ИНТЕРЕСНО и НЕПРЕДСКАЗУЕМО, а не просто 'наказать' игрока. "
            "Среди вариантов действий ВСЕГДА должен быть хотя бы один безопасный/оборонительный "
            "выбор (прикрыть, защищаться, эвакуироваться, переждать), предсказуемо снижающий урон."
        )
    return (
        "You are a Game Master. You create PERSONAL briefings for each player "
        "based on the shared global circumstances. "
        "Each player sees the situation from their unique perspective.\n\n"
        "Every turn MUST MOVE THE STORY FORWARD — unexpected twists, discoveries, "
        "new allies or enemies, findings, situation improvements or deteriorations. "
        "Bold and correct decisions → mission progresses, new opportunities open. "
        "Passive or bad decisions → consequences: damage, losses, mission regression. "
        "The key is INTERESTING and UNPREDICTABLE, not just 'punish' the player. "
        "Among the action choices there MUST ALWAYS be at least one safe/defensive option "
        "(cover, defend, evacuate, wait it out) that predictably reduces incoming damage."
    )
