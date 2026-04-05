"""
Language constants for Game Master API
All user-facing strings should be defined here with Russian and English versions
"""

LANGUAGE_RU = "ru"
LANGUAGE_EN = "en"

# Player role descriptions
PLAYER_ROLES = {
    LANGUAGE_RU: {
        "technical": {
            "role": "Chief Engineer",
            "description": "Вы отвечаете за техническое состояние корабля. Ваша способность быстро находить решения в критических ситуациях спасает экипаж.",
            "avatar": "Техничный специалист в инженерном костюме, с инструментами и голографическими дисплеями вокруг",
            "traits": ["технический", "практичный", "решительный"],
        },
        "diplomatic": {
            "role": "XO (First Officer)",
            "description": "Вы координируете действия экипажа и ведёте переговоры с внешними контактами. Ваше умение находить общий язык решает исход кризисов.",
            "avatar": "Офицер связи в форменной униформе, с коммуникатором и уверенным взглядом",
            "traits": ["коммуникабельный", "стратегический", "эмпатичный"],
        },
        "exploration": {
            "role": "Science Officer",
            "description": "Вы исследуете неизвестное и анализируете данные. Ваша способность видеть закономерности открывает новые возможности.",
            "avatar": "Учёный в лабораторном халате, с сканером и научными приборами",
            "traits": ["аналитический", "любопытный", "методичный"],
        },
    },
    LANGUAGE_EN: {
        "technical": {
            "role": "Chief Engineer",
            "description": "You are responsible for the technical condition of the ship. Your ability to quickly find solutions in critical situations saves the crew.",
            "avatar": "Technical specialist in engineering suit, with tools and holographic displays around",
            "traits": ["technical", "practical", "decisive"],
        },
        "diplomatic": {
            "role": "XO (First Officer)",
            "description": "You coordinate crew actions and conduct negotiations with external contacts. Your ability to find common language resolves crises.",
            "avatar": "Communications officer in uniform, with communicator and confident look",
            "traits": ["communicative", "strategic", "empathetic"],
        },
        "exploration": {
            "role": "Science Officer",
            "description": "You explore the unknown and analyze data. Your ability to see patterns opens up new possibilities.",
            "avatar": "Scientist in lab coat, with scanner and scientific instruments",
            "traits": ["analytical", "curious", "methodical"],
        },
    },
}

# Personality traits
PERSONALITY_TRAITS = {
    LANGUAGE_RU: {
        "cautious": "осторожный",
        "bold": "смелый",
        "empathetic": "эмпатичный",
        "logical": "логичный",
    },
    LANGUAGE_EN: {
        "cautious": "cautious",
        "bold": "bold",
        "empathetic": "empathetic",
        "logical": "logical",
    },
}

# Role names for display
ROLE_DISPLAY = {
    LANGUAGE_RU: {
        "Chief Engineer": "Главный инженер",
        "XO (First Officer)": "Первый офицер",
        "Science Officer": "Научный офицер",
        "Crew Member": "Член экипажа",
    },
    LANGUAGE_EN: {
        "Chief Engineer": "Chief Engineer",
        "XO (First Officer)": "First Officer",
        "Science Officer": "Science Officer",
        "Crew Member": "Crew Member",
    },
}

# LLM prompts for dynamic content generation
LLM_PROMPTS = {
    LANGUAGE_RU: {
        "onboarding_questions": """
Сгенерируй 2-3 вопроса для онбординга в игре про космические исследования.
Вопросы должны быть о "что бы ты сделал в этой ситуации" или "А или Б выбор".
Вопросы помогают определить роль игрока и черты его личности.

Верни ТОЛЬКО валидный JSON без каких-либо дополнительных текстов, markdown кода или комментариев.
Структура:
[
    {{
        "id": 1,
        "text": "текст вопроса",
        "options": [
            {{"value": "значение_варианта_1", "label": "Отображаемый текст варианта 1"}},
            {{"value": "значение_варианта_2", "label": "Отображаемый текст варианта 2"}}
        ]
    }}
]

Важно: НЕ используй markdown блоки (```json), НЕ добавляй никаких пояснений. ТОЛЬКО чистый JSON.
""",
        "avatar_prompt": """
Ты генерируешь промпт для AI генерации изображения персонажа.
На основе роли, черт характера и описания аватара, создай детальный промпт на АНГЛИЙСКОМ ЯЗЫКЕ для генерации портрета персонажа в sci-fi стиле.

Роль: {role}
Черты характера: {traits}
Описание аватара: {avatar_description}

Промпт должен содержать:
- Внешность персонажа (лицо, волосы, глаза)
- Одежду (космическая униформа, детали)
- Окружение (мостик корабля, лаборатория, и т.д.)
- Освещение и стиль (кинематографичное, детализированное)
- Качество (4K, high quality, detailed)

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
Generate 2-3 onboarding questions for a space exploration game.
Questions should be about "what would you do in this situation" or "A or B preference".
Questions help determine player role and personality traits.

Return ONLY valid JSON without any additional text, markdown code blocks, or comments.
Structure:
[
    {{
        "id": 1,
        "text": "question text",
        "options": [
            {{"value": "option_value_1", "label": "Option 1 display text"}},
            {{"value": "option_value_2", "label": "Option 2 display text"}}
        ]
    }}
]

Important: DO NOT use markdown code blocks (```json), DO NOT add any explanations. ONLY pure JSON.
""",
        "avatar_prompt": """
Generate an image generation prompt for a sci-fi character portrait.
Based on the role, personality traits, and avatar description, create a detailed prompt for generating a character portrait.

Role: {role}
Personality traits: {traits}
Avatar description: {avatar_description}

The prompt must include:
- Character appearance (face, hair, eyes)
- Clothing (space uniform, details)
- Environment (ship bridge, laboratory, etc.)
- Lighting and style (cinematic, detailed)
- Quality (4K, high quality, detailed)

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


def get_llm_prompt(prompt_key: str, language: str = LANGUAGE_RU) -> str:
    """Get LLM prompt template for a specific type and language"""
    prompts = LLM_PROMPTS.get(language, LLM_PROMPTS[LANGUAGE_RU])
    return prompts.get(prompt_key, "")


def get_llm_directive(directive_key: str, language: str = LANGUAGE_RU) -> str:
    """Get LLM directive for a specific prompt type and language"""
    directives = LLM_DIRECTIVES.get(language, LLM_DIRECTIVES[LANGUAGE_RU])
    return directives.get(directive_key, "")


def get_player_roles(language: str = LANGUAGE_RU):
    """Get player roles for a specific language"""
    return PLAYER_ROLES.get(language, PLAYER_ROLES[LANGUAGE_RU])


def get_personality_traits(language: str = LANGUAGE_RU):
    """Get personality traits for a specific language"""
    return PERSONALITY_TRAITS.get(language, PERSONALITY_TRAITS[LANGUAGE_RU])


def get_role_display(role: str, language: str = LANGUAGE_RU):
    """Get localized role name"""
    role_map = ROLE_DISPLAY.get(language, ROLE_DISPLAY[LANGUAGE_RU])
    return role_map.get(role, role)
