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


# Ship roles i18n (10 crew positions)
SHIP_ROLES_I18N = {
    "chief_engineer": {
        LANGUAGE_RU: {
            "role_name": "Инженер-механик",
            "role_description": "Вы отвечаете за техническое состояние корабля — от варп-двигателя до систем жизнеобеспечения. Ваша способность быстро находить решения в критических ситуациях спасает экипаж.",
            "avatar_description": "Инженер в техническом костюме с инструментами, голографические дисплеи с схемами корабля на фоне",
            "personality_traits": ["технический", "практичный", "решительный"],
        },
        LANGUAGE_EN: {
            "role_name": "Chief Engineer",
            "role_description": "You are responsible for the ship's technical systems — from the warp drive to life support. Your ability to find quick solutions in critical situations saves the crew.",
            "avatar_description": "Engineer in technical suit with tools, holographic displays with ship schematics in the background",
            "personality_traits": ["technical", "practical", "decisive"],
        },
    },
    "science_officer": {
        LANGUAGE_RU: {
            "role_name": "Научный офицер",
            "role_description": "Вы исследуете неизвестное и анализируете данные. Ваша способность видеть закономерности открывает новые возможности для миссии.",
            "avatar_description": "Учёный в форменной униформе с научным сканером, вокруг парят голографические графики и данные анализов",
            "personality_traits": ["аналитический", "любопытный", "методичный"],
        },
        LANGUAGE_EN: {
            "role_name": "Science Officer",
            "role_description": "You explore the unknown and analyze data. Your ability to see patterns opens new possibilities for the mission.",
            "avatar_description": "Scientist in uniform with a science scanner, holographic charts and analysis data floating around",
            "personality_traits": ["analytical", "curious", "methodical"],
        },
    },
    "communications_officer": {
        LANGUAGE_RU: {
            "role_name": "Офицер связи",
            "role_description": "Вы — голос корабля. Ведёте переговоры с инопланетными цивилизациями и координируете действия экипажа. Ваше умение находить общий язык решает исход кризисов.",
            "avatar_description": "Офицер связи с коммуникатором, на экранах — сигналы и частоты разных цивилизаций",
            "personality_traits": ["коммуникабельный", "стратегический", "эмпатичный"],
        },
        LANGUAGE_EN: {
            "role_name": "Communications Officer",
            "role_description": "You are the voice of the ship. You negotiate with alien civilizations and coordinate crew actions. Your ability to find common ground resolves crises.",
            "avatar_description": "Communications officer with communicator, screens showing signals and frequencies of different civilizations",
            "personality_traits": ["communicative", "strategic", "empathetic"],
        },
    },
    "security_chief": {
        LANGUAGE_RU: {
            "role_name": "Начальник безопасности",
            "role_description": "Вы — щит экипажа. Оцениваете угрозы, планируете оборону и обеспечиваете безопасность при контактах с неизвестным.",
            "avatar_description": "Офицер безопасности в тактическом снаряжении, за спиной — стелс-щит корабля",
            "personality_traits": ["бдительный", "осторожный", "защитный"],
        },
        LANGUAGE_EN: {
            "role_name": "Security Chief",
            "role_description": "You are the crew's shield. You assess threats, plan defense, and ensure safety during encounters with the unknown.",
            "avatar_description": "Security officer in tactical gear, ship's stealth shield behind",
            "personality_traits": ["vigilant", "cautious", "protective"],
        },
    },
    "navigator": {
        LANGUAGE_RU: {
            "role_name": "Штурман",
            "role_description": "Вы прокладываете курс через звёздные системы. Ваше чутьё на безопасные маршруты и знание аномалий определяет путь корабля.",
            "avatar_description": "Штурман за навигационной консолью, звёздные карты и маршруты проецируются в воздухе",
            "personality_traits": ["ориентированный", "внимательный", "интуитивный"],
        },
        LANGUAGE_EN: {
            "role_name": "Navigator",
            "role_description": "You chart the course through star systems. Your instinct for safe routes and knowledge of anomalies guides the ship's path.",
            "avatar_description": "Navigator at navigation console, star maps and routes projected in the air",
            "personality_traits": ["oriented", "attentive", "intuitive"],
        },
    },
    "medical_officer": {
        LANGUAGE_RU: {
            "role_name": "Медицинский офицер",
            "role_description": "Вы храните здоровье экипажа в глубинах космоса. От инопланетных вирусов до травм при высадке — вы единственная надежда на исцеление.",
            "avatar_description": "Медицинский офицер в белом халате с биосканером, на фоне — медицинский отсек с регенератором",
            "personality_traits": ["заботливый", "наблюдательный", "стойкий"],
        },
        LANGUAGE_EN: {
            "role_name": "Medical Officer",
            "role_description": "You safeguard the crew's health in deep space. From alien viruses to landing injuries — you are the only hope for healing.",
            "avatar_description": "Medical officer in white coat with bioscanner, medical bay with regenerator in the background",
            "personality_traits": ["caring", "observant", "resilient"],
        },
    },
    "tactical_officer": {
        LANGUAGE_RU: {
            "role_name": "Тактический офицер",
            "role_description": "Вы управляете оружейными системами и щитами. В бою ваше решение в долю секунды определяет, выживет ли корабль.",
            "avatar_description": "Тактический офицер за боевым терминалом, на экранах — схемы щитов и цели",
            "personality_traits": ["быстрый", "решительный", "стратегический"],
        },
        LANGUAGE_EN: {
            "role_name": "Tactical Officer",
            "role_description": "You control weapons systems and shields. In battle, your split-second decisions determine whether the ship survives.",
            "avatar_description": "Tactical officer at combat terminal, screens showing shield diagrams and targets",
            "personality_traits": ["quick", "decisive", "strategic"],
        },
    },
    "quartermaster": {
        LANGUAGE_RU: {
            "role_name": "Квартирмейстер",
            "role_description": "Вы управляете ресурсами корабля — припасами, энергией, оборудованием — и ведёте торговлю на космических станциях. Ваша расчётливость позволяет экипажу выживать в самых длинных рейсах.",
            "avatar_description": "Квартирмейстер среди контейнеров с припасами, на дисплее — графики расхода ресурсов",
            "personality_traits": [
                "расчётливый",
                "организованный",
                "предусмотрительный",
            ],
        },
        LANGUAGE_EN: {
            "role_name": "Quartermaster",
            "role_description": "You manage the ship's resources — supplies, energy, equipment — and handle trading at space stations. Your resourcefulness keeps the crew alive on the longest voyages.",
            "avatar_description": "Quartermaster among supply containers, display showing resource consumption graphs",
            "personality_traits": ["calculating", "organized", "provident"],
        },
    },
    "xenobiologist": {
        LANGUAGE_RU: {
            "role_name": "Ксенобиолог",
            "role_description": "Вы изучаете инопланетные формы жизни. Каждый контакт с новым видом — ваша вотчина. Ваша экспертиза предотвращает катастрофы при контакте.",
            "avatar_description": "Ксенобиолог с образцами инопланетной флоры, на фоне — изолированная лаборатория с контейнерами",
            "personality_traits": ["исследовательский", "осторожный", "открытый"],
        },
        LANGUAGE_EN: {
            "role_name": "Xenobiologist",
            "role_description": "You study alien life forms. Every encounter with a new species is your domain. Your expertise prevents catastrophes during contact.",
            "avatar_description": "Xenobiologist with alien flora samples, isolated laboratory with containers in the background",
            "personality_traits": ["exploratory", "cautious", "open-minded"],
        },
    },
    "pilot": {
        LANGUAGE_RU: {
            "role_name": "Пилот",
            "role_description": "Вы ведёте корабль через астероидные поля и атмосферу планет. Ваши рефлексы и мастерство превращают невозможные манёвры в рутину.",
            "avatar_description": "Пилот за штурвалом, через лобовое стекло видны звёзды и астероидное поле",
            "personality_traits": ["дерзкий", "рефлексивный", "уверенный"],
        },
        LANGUAGE_EN: {
            "role_name": "Pilot",
            "role_description": "You fly the ship through asteroid fields and planetary atmospheres. Your reflexes and skill turn impossible maneuvers into routine.",
            "avatar_description": "Pilot at the helm, stars and asteroid field visible through the windshield",
            "personality_traits": ["daring", "reflexive", "confident"],
        },
    },
}


def get_ship_role_i18n(role_key: str, language: str = LANGUAGE_RU) -> dict:
    """Get localized ship role data by role_key and language.

    Returns dict with keys: role_name, role_description, avatar_description, personality_traits.
    Falls back to Russian if language not found.
    """
    role_data = SHIP_ROLES_I18N.get(role_key, {})
    return role_data.get(language, role_data.get(LANGUAGE_RU, {}))


def get_all_ship_roles_i18n(language: str = LANGUAGE_RU) -> dict:
    """Get all ship roles for a specific language.

    Returns dict keyed by role_key, each value containing
    role_name, role_description, avatar_description, personality_traits.
    """
    return {
        role_key: data.get(language, data.get(LANGUAGE_RU, {}))
        for role_key, data in SHIP_ROLES_I18N.items()
    }
