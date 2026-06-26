"""
LLM prompt constants for Game Master API
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
from pydantic import BaseModel


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


LLM_PROMPTS = {
    LANGUAGE_RU: {
        "onboarding_questions": """
Сгенерируй вопросы для онбординга в игре про космические исследования.
Вопросы должны быть конкретные сценарии с вариантами действий.
Каждый вариант содержит role_scores — очки для ролей.

Верни ТОЛЬКО валидный JSON без каких-либо дополнительных текстов, markdown кода или комментариев.
Структура:
[
    {{
        "id": 1,
        "text": "текст вопроса",
        "options": [
            {{
                "value": "значение_варианта",
                "label": "Полное описание действия",
                "role_scores": {{
                    "chief_engineer": 0, "science_officer": 0, "communications_officer": 0,
                    "security_chief": 0, "navigator": 0, "medical_officer": 0,
                    "tactical_officer": 0, "captain": 0, "xenobiologist": 0, "pilot": 0
                }}
            }}
        ]
    }}
]

Важно: НЕ используй markdown блоки, НЕ добавляй пояснений. ТОЛЬКО чистый JSON.
""",
        "avatar_prompt": """
Ты генерируешь промпт для AI генерации изображения персонажа.
На основе роли, черт характера и описания аватара, создай детальный промпт на АНГЛИЙСКОМ ЯЗЫКЕ для генерации портрета персонажа в sci-fi стиле.

Роль: {role}
Черты характера: {traits}
Описание аватара: {avatar_description}

ВАЖНО: Описание аватара — это ОКОНЧАТЕЛЬНЫЙ источник внешности персонажа. Ни в коем случае не интерпретируй его как описание корабля, транспортного средства или фона. Промпт должен описывать исключительно персонажа.
Определи вид персонажа по описанию и подбери соответствующее описание внешности:
- Человек / Гуманоид → лицо, волосы, униформа, портрет верхней части тела
- Негуманоид → реальная альен-анатомия (щупальца, панцирь, экзоскелет), БЕЗ человеческих черт, полный рост
- Энергетическая форма → свечение, плазма, частоты, нет твёрдого тела, полный рост
- Кибернетическая форма → механическое тело, схемы, металл, синтетика, полный рост
- Симбиотическая форма → составное тело из нескольких организмов, полный рост

Промпт должен содержать:
- Внешность персонажа строго по описанию (человеческая ИЛИ нечеловеческая анатомия)
- Одежду / оболочку / естественное покрытие (униформа, панцирь, свечение, кристаллы)
- Окружение (мостик корабля, лаборатория, и т.д.) — только как фон, не главный объект
- Освещение и стиль (кинематографичное, детализированное)
- Качество (4K, high quality, detailed)
- Чёткое указание, что основной фокус — персонаж, а не корабль или техника

Ответь ТОЛЬКО промптом на английском языке, без пояснений. Одним абзацем.
""",
        "scene_prompt": """
Создай промпт на АНГЛИЙСКОМ ЯЗЫКЕ для генерации sci-fi сцены.

Сцена: {scene_description}
Персонажи: {characters}
Атмосфера: {mood}

Промпт должен описывать:
- Детальное окружение (космический корабль, планета, и т.д.)
- Действие или момент
- Освещение и цветовую палитру
- Стиль (space opera, cinematic, 4K)

Ответь ТОЛЬКО промптом на английском языке, без пояснений. Одним абзацем.
""",
    },
    LANGUAGE_EN: {
        "onboarding_questions": """
Generate onboarding questions for a space exploration game.
Questions should be specific scenarios with action choices.
Each option contains role_scores — points for roles reflecting how characteristic the action is.

Return ONLY valid JSON without any additional text, markdown code blocks, or comments.
Structure:
[
    {{
        "id": 1,
        "text": "question text",
        "options": [
            {{
                "value": "option_value",
                "label": "Full action description",
                "role_scores": {{
                    "chief_engineer": 0, "science_officer": 0, "communications_officer": 0,
                    "security_chief": 0, "navigator": 0, "medical_officer": 0,
                    "tactical_officer": 0, "captain": 0, "xenobiologist": 0, "pilot": 0
                }}
            }}
        ]
    }}
]

Important: DO NOT use markdown code blocks, DO NOT add any explanations. ONLY pure JSON.
""",
        "avatar_prompt": """
Generate an image generation prompt for a sci-fi character portrait.
Based on the role, personality traits, and avatar description, create a detailed prompt for generating a character portrait.

Role: {role}
Personality traits: {traits}
Avatar description: {avatar_description}

IMPORTANT: The avatar description is the DEFINITIVE source for the character's appearance. Do NOT interpret it as a description of a spacecraft, vehicle, or background. The prompt must describe ONLY the character.
Determine the species type from the description and adapt the visual focus accordingly:
- Human / Humanoid → face, hair, uniform, portrait upper body
- Non-Humanoid → actual alien anatomy (tentacles, carapace, exoskeleton), NO human features, full body
- Energy Being → glow, plasma, frequencies, no solid body, full body
- Cybernetic → mechanical body, circuits, metal, synthetic, full body
- Symbiotic → composite body of multiple organisms, full body

The prompt must include:
- Character appearance exactly as described (human OR non-human anatomy)
- Clothing / shell / natural covering (uniform, carapace, glow, crystals)
- Environment (ship bridge, laboratory, etc.) — only as background, not the main subject
- Lighting and style (cinematic, detailed)
- Quality (4K, high quality, detailed)
- Clear statement that the primary focus is the character, not a ship or technology

Respond with ONLY the prompt text. No explanations. Single paragraph.
""",
        "scene_prompt": """
Create an image generation prompt for a sci-fi scene.

Scene: {scene_description}
Characters: {characters}
Mood: {mood}

The prompt must describe:
- Detailed environment (spaceship, planet, etc.)
- Action or moment
- Lighting and color palette
- Style (space opera, cinematic, 4K)

Respond with ONLY the prompt text. No explanations. Single paragraph.
""",
    },
}

# LLM language directives for prompts
LLM_DIRECTIVES = {
    LANGUAGE_RU: {
        "onboarding_questions": "ВАЖНО: Отвечай ТОЛЬКО на русском языке. Все вопросы и варианты ответов должны быть на русском.",
        "daily_story": "ВАЖНО: Отвечай ТОЛЬКО на русском языке. Все повествование, действия и последствия должны быть на русском языке.",
        "npc_dialogue": "Отвечай на русском языке.",
        "content_prompts": "Отвечай на русском языке.",
        "player_message": "Отвечай на русском языке.",
    },
    LANGUAGE_EN: {
        "onboarding_questions": "IMPORTANT: Respond in ENGLISH ONLY. All questions and options must be in English.",
        "daily_story": "IMPORTANT: Respond entirely in ENGLISH. All narrative, actions, and consequences must be in English language.",
        "npc_dialogue": "Respond in ENGLISH.",
        "content_prompts": "Respond in ENGLISH.",
        "player_message": "Respond in ENGLISH.",
    },
}

# Game Master prompts (system/user pairs for each LLM call)
PROMPTS = {
    LANGUAGE_RU: {
        "onboarding_questions": {
            "system": "Ты — дизайнер игр. Генерируешь вопросы для онбординга в космической игре.",
            "user": (
                "Сгенерируй вопросы для онбординга в игре про космический экипаж звездного корабля. "
                "Каждый вопрос — это конкретная ситуация на корабле или во время миссии с вариантами ДЕЙСТВИЙ. "
                "Каждый вариант содержит role_scores — очки для ролей. "
                "Каждому варианту назначь 1-3 роли с очками 1-3, остальные 0. "
                "Варианты в одном вопросе должны давать очки РАЗНЫМ ролям. "
                "Все тексты на русском языке."
            ),
        },
        "role_assignment": {
            "system": "Роль определяется по сумме очков из ответов на вопросы. Анализ LLM не требуется.",
            "user": ("Ответы игрока уже содержат role_scores. Роль выбирается детерминированно по максимальной сумме очков."),
        },
        "game_title": {
            "system": "Ты — креативный писатель-фантаст. Придумываешь названия и описания для космических приключений.",
            "user": (
                "Придумай название для игры про экипаж звездного корабля и приветственное сообщение. "
                "Название должно быть в формате: название корабля + подзаголовок миссии. "
                "Пример стиля: «Звёздный Крейсер Аврора: За горизонтом известного». "
                "Приветствие должно быть атмосферным — будто игрок заходит на борт корабля. "
                "Все тексты на русском языке."
            ),
        },
        "daily_story": {
            "system": ("Ты — Game Master космической исследовательской игры в стиле Star Trek. Создаёшь увлекательные ежедневные эпизоды с конфликтами и выбором."),
            "user": (
                "День: {day}\n"
                "Предыдущий день: {previous_summary}\n"
                "Роль игрока: {player_role}\n\n"
                "Создай эпизод с:\n"
                "1. Место действия (космос, станция, планета)\n"
                "2. Центральный конфликт или тайна\n"
                "3. 3 точки выбора для игрока с действиями и скрытыми последствиями\n\n"
                "Всё на русском языке."
            ),
        },
        "npc_dialogue": {
            "lang_note": "Отвечай на русском.",
            "player_role_default": "Член экипажа",
        },
        "content_prompts": {
            "lang_note": "Промпты пиши на английском (для генерации изображений).",
        },
        "player_message": {
            "system": ("Ты — Game Master космической исследовательской игры в стиле Star Trek. Отвечай в стиле Game Master, направляя叙事. Будь увлекательным и атмосферным."),
        },
    },
    LANGUAGE_EN: {
        "onboarding_questions": {
            "system": "You are a game designer. Generate onboarding questions for a space exploration game.",
            "user": (
                "Generate onboarding questions for a starship crew game. "
                "Each question is a specific situation aboard a ship or during a mission with ACTION choices. "
                "Each option contains role_scores — points for roles. "
                "For each option, assign 1-3 roles with points 1-3, rest 0. "
                "Options within a question should give points to DIFFERENT roles. "
                "All text in English."
            ),
        },
        "role_assignment": {
            "system": "Role is determined by sum of points from question answers. LLM analysis not required.",
            "user": ("Player answers already contain role_scores. Role is selected deterministically by highest point sum."),
        },
        "game_title": {
            "system": "You are a creative sci-fi writer. You create titles and descriptions for space adventures.",
            "user": (
                "Create a title for a starship crew game and a welcome message. "
                "Title format: ship name + mission tagline. "
                "Example style: 'Star Cruiser Aurora: Beyond the Known Horizon'. "
                "The welcome should be atmospheric — as if the player is stepping aboard the ship. "
                "All text in English."
            ),
        },
        "daily_story": {
            "system": ("You are a Game Master for a Star Trek-style space exploration game. Create compelling daily episodes with conflicts and player choices."),
            "user": (
                "Day: {day}\n"
                "Previous day: {previous_summary}\n"
                "Player role: {player_role}\n\n"
                "Create an episode with:\n"
                "1. A setting (space location, station, planet)\n"
                "2. A central conflict or mystery\n"
                "3. 3 decision points for the player with visible actions and hidden consequences\n"
            ),
        },
        "npc_dialogue": {
            "lang_note": "Respond in English.",
            "player_role_default": "Crew member",
        },
        "content_prompts": {
            "lang_note": "Write prompts in English for image generation.",
        },
        "player_message": {
            "system": ("You are the Game Master of a Star Trek-style space exploration game. Respond in character as the Game Master, guiding the narrative forward. Keep it engaging and atmospheric."),
        },
    },
}


def get_prompt(key: str, language: str = LANGUAGE_RU) -> dict:
    """Get prompt pair (system/user) by key and language."""
    prompts = PROMPTS.get(language, PROMPTS[LANGUAGE_RU])
    return prompts.get(key, {})


def get_llm_prompt(prompt_key: str, language: str = LANGUAGE_RU) -> str:
    """Get LLM prompt template for a specific type and language"""
    prompts = LLM_PROMPTS.get(language, LLM_PROMPTS[LANGUAGE_RU])
    return prompts.get(prompt_key, "")


def get_llm_directive(directive_key: str, language: str = LANGUAGE_RU) -> str:
    """Get LLM directive for a specific prompt type and language"""
    directives = LLM_DIRECTIVES.get(language, LLM_DIRECTIVES[LANGUAGE_RU])
    return directives.get(directive_key, "")


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
                "next_day_hook": {
                    "type": "string",
                    "description": "A teaser or hook for the next day's story",
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
                "next_day_hook",
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
    "ВАЖНЕЙШИЕ ПРАВИЛА:\n"
    "1. Решения ИГРОКОВ (Weight: HIGH) имеют БОЛЬШИЙ вес, чем решения NPC\n"
    "2. Прогресс миссии нелинейный — правильные действия НАКАПЛИВАЮТСЯ, "
    "неправильные — ОТКАТЫВАЮТ прогресс назад\n"
    "3. Смелые и правильные решения игроков → продвижение миссии, находки, успехи.\n"
    "4. Пассивные, трусливые или ошибочные решения → повреждения корабля, регресс миссии, ранения и гибель экипажа.\n"
    "5. Сюжет ДОЛЖЕН ДВИГАТЬСЯ — каждый ход приближает к цели миссии ИЛИ отдаляет от неё, в зависимости от качества решений.\n"
    "6. У каждого персонажа должен быть ПЕРСОНАЛЬНЫЙ ИСХОД в personal_outcomes — последствия его выбора.\n"
    "7. Прошлые повреждения корабля СОХРАНЯЮТСЯ — их нельзя просто 'забыть'.\n"
    "8. Гибель членов экипажа возможна, но только как СЛЕДСТВИЕ неудачных решений или рискованных действий. "
    "Если игрок действует умно и смело — экипаж в безопасности, миссия продвигается.\n"
    "9. Смерти и ранения НЕ должны быть 'фоном' — каждая потеря должна быть значимой и вытекать из конкретного решения."
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
    "Учти, что решения ИГРОКОВ важнее решений NPC.\n\n"
    "КРИТИЧЕСКИ ВАЖНО: Каждый ход что-то ДОЛЖНО МЕНЯТЬСЯ. "
    "Сюжет должен двигаться в зависимости от решений игрока.\n"
    "- Если игрок выбрал смелое, правильное действие → миссия продвигается, находятся ресурсы, союзники, открываются новые возможности.\n"
    "- Если игрок выбрал пассивное, трусливое или ошибочное действие → миссия откатывается, корабль получает повреждения, экипаж страдает.\n\n"
    "Верни JSON с полями:\n"
    "1. outcome_narrative — что произошло в результате всех решений (2-3 абзаца). Должен быть ДРАМАТИЧЕСКИМ.\n"
    "2. ship_status_change — как изменилось состояние корабля (текст)\n"
    "3. crew_morale_change — как изменился моральный дух экипажа (текст)\n"
    "4. next_day_hook — зацепка для следующего хода, которая создаёт ожидание\n"
    "5. mission_progress — МАССИВ объектов [{{'stage': N, 'points': +/-M}}]. "
    "Положительные = прогресс, отрицательные = регресс/откат.\n"
    "6. dead_crew_members — список [[name, role]] погибших из СПИСКА ЭКИПАЖА (full crew roster). "
    "Убивать можно ТОЛЬКО персонажей из списка экипажа. НЕ выдумывай новых членов экипажа. "
    "Если безопасных смертей нет — можно оставить пустым.\n"
    "ВАЖНО: Если персонаж погибает — опиши это В outcome_narrative И добавь в dead_crew_members. "
    "Смерть и ранения возможны ТОЛЬКО для активных участников (NPC или игроков из списка экипажа), "
    "и ТОЛЬКО когда это описано в hidden consequences выбранного действия. "
    "Не убивай рандомных безымянных членов экипажа.\n"
    "Аналогично: если персонаж ранен — опиши ранение в narrative и добавь в crew_injured.\n"
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
    "CRITICAL RULES:\n"
    "1. PLAYER decisions (Weight: HIGH) matter MORE than NPC decisions\n"
    "2. Mission progress is NON-LINEAR — correct actions ACCUMULATE, "
    "wrong actions REGRESS progress backward\n"
    "3. Bold and correct player decisions → mission progress, discoveries, successes.\n"
    "4. Passive, cowardly, or wrong decisions → ship damage, mission regression, injuries and crew deaths.\n"
    "5. The story MUST MOVE — every turn brings the mission closer OR pushes it further away, depending on decision quality.\n"
    "6. Every character must have a PERSONAL OUTCOME in personal_outcomes — consequences of their choice.\n"
    "7. Past ship damage PERSISTS — it cannot be simply 'forgotten'.\n"
    "8. Crew deaths are possible, but only as a CONSEQUENCE of bad decisions or risky actions. "
    "If the player acts smartly and boldly — the crew is safe, the mission advances.\n"
    "9. Deaths and injuries should NOT be 'background noise' — every loss must be meaningful and stem from a specific decision."
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
    "CRITICALLY IMPORTANT: Every turn MUST CHANGE something. "
    "The story must move based on the player's decisions.\n"
    "- If the player chose a bold, correct action → mission advances, resources are found, allies appear, new opportunities open.\n"
    "- If the player chose a passive, cowardly, or wrong action → mission regresses, ship takes damage, crew suffers.\n\n"
    "Return JSON with fields:\n"
    "1. outcome_narrative — what happened (2-3 paragraphs). Must be DRAMATIC.\n"
    "2. ship_status_change — narrative of ship condition change\n"
    "3. crew_morale_change — how morale shifted\n"
    "4. next_day_hook — teaser for the next turn that creates anticipation\n"
    "5. mission_progress — ARRAY of [{{'stage': N, 'points': +/-M}}]. "
    "Positive = progress, Negative = regression/setback.\n"
    "6. dead_crew_members — list of [name, role] from the CREW ROSTER. "
    "Can ONLY kill characters listed in the full crew roster. Do NOT invent non-existent crew members. "
    "May be left empty if no safe deaths.\n"
    "IMPORTANT: If a character dies — describe it IN outcome_narrative AND add them to dead_crew_members. "
    "Death and injury can ONLY happen to active participants (NPC or player from the crew roster), "
    "and ONLY when the hidden consequences of the chosen action describe it. "
    "Do NOT kill random unnamed crew members.\n"
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


def build_game_title_prompts(language: str) -> tuple[str, str]:
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
    return system, user


# ── Daily story prompts ────────────────────────────────────────────


def build_daily_story_prompts(language: str, day: int, previous_summary: str, player_role: str) -> tuple[str, str]:
    """Build system and user prompts for daily story generation."""
    if language == LANGUAGE_RU:
        system = "Ты — Game Master космической исследовательской игры в стиле Star Trek. Создаёшь увлекательные ежедневные эпизоды с конфликтами и выбором."
        player_role_display = player_role or "Член экипажа"
        user = (
            f"День: {day}\n"
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
            f"Day: {day}\n"
            f"Previous day: {previous_summary or 'First day of mission'}\n"
            f"Player role: {player_role_display}\n\n"
            "Create an episode with:\n"
            "1. A setting (space location, station, planet)\n"
            "2. A central conflict or mystery\n"
            "3. 3 decision points for the player with visible actions and hidden consequences\n"
        )
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


def build_player_message_system(language: str) -> str:
    """Build system prompt for player message processing."""
    if language == LANGUAGE_RU:
        return "Ты — Game Master космической исследовательской игры в стиле Star Trek. Отвечай в стиле Game Master, направляя叙事. Будь увлекательным и атмосферным."
    return "You are the Game Master of a Star Trek-style space exploration game. Respond in character as the Game Master, guiding the narrative forward. Keep it engaging and atmospheric."


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
    return system, user


# ── NPC decision prompts ───────────────────────────────────────────


def build_npc_decision_prompts(
    language: str,
    npc_name: str,
    npc_role: str,
    traits: str | list[str],
    choices_text: str,
) -> tuple[str, str]:
    """Build system and user prompts for NPC decision making."""
    traits_str = ", ".join(traits) if isinstance(traits, list) else traits
    if language == LANGUAGE_RU:
        system = f"Ты — {npc_name}, {npc_role} на космическом корабле. Твой характер: {traits_str}. Ты видишь ТОЛЬКО описания действий без последствий. Сделай выбор на основе своей личности и роли."
        user = f"Текущая ситуация на корабле требует твоего решения.\n\nДоступные действия:\n{choices_text}\n\nВыбери одно действие, которое лучше всего соответствует твоему характеру и роли. Ты не знаешь последствий — действуй интуитивно."
    else:
        system = f"You are {npc_name}, {npc_role} aboard a starship. Your personality: {traits_str}. You see ONLY action descriptions with no consequences. Make a choice based on your personality and role."
        user = f"The current situation requires your decision.\n\nAvailable actions:\n{choices_text}\n\nChoose the action that best matches your character and role. You don't know the consequences — act on instinct."
    return system, user


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
    return system, user


# ── Global circumstances prompts ───────────────────────────────────


def build_global_circumstances_prompts(
    language: str,
    day: int,
    previous_summary: str,
    player_descriptions: str,
    mission_str: str,
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
            f"День: {day}\n"
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
            "Create SHARED circumstances for the day — the situation unfolding on or around the ship. "
            "These circumstances are common to all crew members.\n\n"
            "Use the actual CHARACTER NAMES from the crew list in the narrative. "
            "Each crew member has a unique name — refer to them by name.\n"
        )
        user = (
            f"Day: {day}\n"
            f"Previous events: {previous_summary or 'First day of mission'}\n"
            f"Crew:\n{player_descriptions or '  Crew forming'}\n"
            f"{mission_str}\n"
            "Create shared circumstances for the day:\n"
            "1. Setting — where the ship is located\n"
            "2. Conflict — central problem or mystery\n"
            "3. Narrative — GM voice description (2-3 paragraphs). "
            "Refer to crew members by NAME, showing their location and actions.\n"
            "4. Key events — 3-5 background events everyone can perceive\n"
            "5. scene_prompt — a detailed English image generation prompt for this day's scene. "
            "Cinematic, sci-fi/space opera, 4K quality. Describe the setting, crew at their positions, "
            "lighting, and atmosphere.\n"
            "6. crew_positions — array of positions for each crew member: where they are and what they're doing.\n\n"
            "IMPORTANT: All circumstances must be consistent with the mission context. "
            "Do not invent an independent plot — develop events within the mission framework.\n"
        )
    return system, user


# ── Mission generation prompts ─────────────────────────────────────


def build_mission_prompts(language: str, crew_desc: str) -> tuple[str, str]:
    """Build system and user prompts for mission generation."""
    if language == LANGUAGE_RU:
        system = "Ты — Game Master космической игры. Создаёшь миссию для экипажа звёздного корабля. Миссия делится на 2-4 этапа (stages), каждый с прогрессом от 1 до 10."
        user = (
            f"Экипаж:\n{crew_desc}\n\n"
            "Создай миссию с:\n"
            "1. Название миссии — только кодовое имя и описание (формат: 'Кодовое имя: описание'). "
            "ВАЖНО: слово 'Миссия' в названии НЕ пиши — оно будет добавлено автоматически в интерфейсе.\n"
            "2. Описание — что нужно сделать, 2-3 абзаца\n"
            "3. 2-4 этапа с целями, каждый с success_threshold (1-10)\n"
            "Этапы должны быть последовательными, но достижимыми нелинейно.\n"
            "Всё на русском языке."
        )
    else:
        system = "You are a Game Master. Create a mission for a starship crew. The mission is divided into 2-4 stages, each with progress from 1 to 10."
        user = (
            f"Crew:\n{crew_desc}\n\n"
            "Create a mission with:\n"
            "1. Mission name — code name and description only (format: 'Code Name: description'). "
            "IMPORTANT: do NOT include the word 'Mission' in the name — it will be added automatically by the UI.\n"
            "2. Description — what needs to be done, 2-3 paragraphs\n"
            "3. 2-4 stages with objectives, each with success_threshold (1-10)\n"
            "Stages should be sequential but achievable non-linearly."
        )
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
            "Главное — ИНТЕРЕСНО и НЕПРЕДСКАЗУЕМО, а не просто 'наказать' игрока."
        )
    return (
        "You are a Game Master. You create PERSONAL briefings for each player "
        "based on the shared global circumstances. "
        "Each player sees the situation from their unique perspective.\n\n"
        "Every turn MUST MOVE THE STORY FORWARD — unexpected twists, discoveries, "
        "new allies or enemies, findings, situation improvements or deteriorations. "
        "Bold and correct decisions → mission progresses, new opportunities open. "
        "Passive or bad decisions → consequences: damage, losses, mission regression. "
        "The key is INTERESTING and UNPREDICTABLE, not just 'punish' the player."
    )
