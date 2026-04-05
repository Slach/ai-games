"""
LLM prompt constants for Game Master API
All prompt strings organized by language (ru/en)
"""

from language import LANGUAGE_RU, LANGUAGE_EN

# LLM prompts for dynamic content generation (legacy format — used by main.py)
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

# Game Master prompts (system/user pairs for each LLM call)
PROMPTS = {
    LANGUAGE_RU: {
        "onboarding_questions": {
            "system": "Ты — дизайнер игр. Генерируешь вопросы для онбординга в космической игре.",
            "user": (
                "Сгенерируй 5 вопросов для онбординга в игре про космический экипаж звездного корабля. "
                "Каждый вопрос — это конкретная ситуация на корабле или во время миссии с выбором из 2-3 вариантов ДЕЙСТВИЙ. "
                "ВАЖНО: Каждый вариант ответа должен описывать КОНКРЕТНОЕ ДЕЙСТВИЕ, которое игрок совершает в этой ситуации. "
                "ПРИМЕР правильных вариантов: 'Бежать в машинное отделение и попытаться починить варп-двигатель', "
                "'Активировать аварийные щиты и вызвать подкрепление'. "
                "НЕПРАВИЛЬНО: 'Инженер — технический специалист', 'Учёный – смелый, ищущий прорыв'. "
                "Никогда не указывайте название роли или тип личности в вариантах ответа — только действия. "
                "Вопросы должны покрывать разные аспекты: реакция на опасность, работа с техникой, взаимодействие с экипажем, "
                "исследование неизвестного, принятие решений в кризисе. "
                "Все тексты на русском языке."
            ),
        },
        "role_assignment": {
            "system": "Ты — аналитик персонала космического корабля. По ответам игрока определяешь подходящую роль в экипаже.",
            "user": (
                "Ответы игрока на вопросы онбординга:\n{answers_text}\n\n"
                "Доступные роли:\n{roles_text}\n\n"
                "Выбери ОДНУ роль из списка доступных, которая лучше всего подходит игроку "
                "на основе его выбранных действий. Отвечай только ключ роли (role_key)."
            ),
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
            "system": (
                "Ты — Game Master космической исследовательской игры в стиле Star Trek. "
                "Создаёшь увлекательные ежедневные эпизоды с конфликтами и выбором."
            ),
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
            "system": (
                "Ты — Game Master космической исследовательской игры в стиле Star Trek. "
                "Отвечай в стиле Game Master, направляя叙事. "
                "Будь увлекательным и атмосферным."
            ),
        },
    },
    LANGUAGE_EN: {
        "onboarding_questions": {
            "system": "You are a game designer. Generate onboarding questions for a space exploration game.",
            "user": (
                "Generate 5 onboarding questions for a starship crew game. "
                "Each question is a specific situation aboard a ship or during a mission with 2-3 ACTION choices. "
                "CRITICAL: Each option must describe a SPECIFIC ACTION the player would take in this situation. "
                "CORRECT example: 'Run to engineering and try to repair the warp drive', "
                "'Activate emergency shields and call for backup'. "
                "INCORRECT: 'Engineer - technical specialist', 'Scientist - bold, seeking breakthrough'. "
                "NEVER include role names or personality types in options — only actions. "
                "Questions should cover: reaction to danger, working with technology, crew interaction, "
                "exploring the unknown, crisis decision-making. "
                "All text in English."
            ),
        },
        "role_assignment": {
            "system": "You are a starship personnel analyst. You assign the best crew role based on a player's onboarding answers.",
            "user": (
                "Player's onboarding answers:\n{answers_text}\n\n"
                "Available roles:\n{roles_text}\n\n"
                "Pick exactly ONE role from the available list that best matches the player "
                "based on their chosen actions. Return only the role_key."
            ),
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
            "system": (
                "You are a Game Master for a Star Trek-style space exploration game. "
                "Create compelling daily episodes with conflicts and player choices."
            ),
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
            "system": (
                "You are the Game Master of a Star Trek-style space exploration game. "
                "Respond in character as the Game Master, guiding the narrative forward. "
                "Keep it engaging and atmospheric."
            ),
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
