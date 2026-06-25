"""
LLM prompt constants for Game Master API
All prompt strings organized by language (ru/en)
"""

import random
from typing import Any

from language import (
    LANGUAGE_EN,
    LANGUAGE_RU,
    get_gender_questions_data,
    get_species_questions_data,
)
from pydantic import BaseModel


class OnboardingQuestion(BaseModel):
    """A single onboarding question"""

    id: int
    text: str
    options: list[dict[str, Any]]
    image_url: str | None = None
    image_prompt: str | None = None


def build_species_gender_questions(language: str = LANGUAGE_RU) -> list:
    """Build static species and gender questions with tags from language data.

    Legacy: all species first, then all gender. No shuffling.
    Kept for backward compatibility (STATIC_ONBOARDING_QUESTIONS legacy path).
    """
    questions = []
    next_id = 1
    species_data = get_species_questions_data(language)
    for i, q_data in enumerate(species_data, start=next_id):
        options = []
        for opt in q_data["options"]:
            option = {
                "value": opt["value"],
                "label": opt["label"],
                "role_scores": {},
                "species_tags": opt.get("species_tags", []),
            }
            options.append(option)
        questions.append(OnboardingQuestion(id=i, text=q_data["text"], options=options))
    next_id = len(questions) + 1
    gender_data = get_gender_questions_data(language)
    for i, q_data in enumerate(gender_data, start=next_id):
        options = []
        for opt in q_data["options"]:
            option = {
                "value": opt["value"],
                "label": opt["label"],
                "role_scores": {},
                "gender_tags": opt.get("gender_tags", []),
            }
            options.append(option)
        questions.append(OnboardingQuestion(id=i, text=q_data["text"], options=options))
    return questions


def build_interleaved_species_gender_questions(
    language: str = LANGUAGE_RU,
    shuffle_seed: int = 0,
) -> list:
    """Build species and gender questions INTERLEAVED (alternating),
    with OPTIONS shuffled deterministically by shuffle_seed.

    Flow:
    1. Randomly shuffle the species question pool
    2. Randomly shuffle the gender question pool
    3. Take one from each pool alternately: species → gender → species → ...
    4. Within each question, shuffle its options with the same seed

    This ensures:
    - Species and gender questions never cluster together
    - Option order is different every session (seed varies per player)
    - No player can press [1] repeatedly to get "human + male"
    """
    rng = random.Random(shuffle_seed)

    species_data = get_species_questions_data(language)
    gender_data = get_gender_questions_data(language)

    # Shuffle question indices for each pool
    species_indices = list(range(len(species_data)))
    gender_indices = list(range(len(gender_data)))
    rng.shuffle(species_indices)
    rng.shuffle(gender_indices)

    questions = []
    next_id = 1
    i, j = 0, 0

    while i < len(species_indices) or j < len(gender_indices):
        # Take a species question
        if i < len(species_indices):
            q_data = species_data[species_indices[i]]
            options = []
            for opt in q_data["options"]:
                option = {
                    "value": opt["value"],
                    "label": opt["label"],
                    "role_scores": {},
                    "species_tags": opt.get("species_tags", []),
                }
                options.append(option)
            # Shuffle options within this question
            rng.shuffle(options)
            questions.append(OnboardingQuestion(id=next_id, text=q_data["text"], options=options))
            next_id += 1
            i += 1

        # Take a gender question
        if j < len(gender_indices):
            q_data = gender_data[gender_indices[j]]
            options = []
            for opt in q_data["options"]:
                option = {
                    "value": opt["value"],
                    "label": opt["label"],
                    "role_scores": {},
                    "gender_tags": opt.get("gender_tags", []),
                }
                options.append(option)
            # Shuffle options within this question
            rng.shuffle(options)
            questions.append(OnboardingQuestion(id=next_id, text=q_data["text"], options=options))
            next_id += 1
            j += 1

    return questions


# New export name for cleaner imports
build_questions_v2 = build_interleaved_species_gender_questions


# Static onboarding questions (fallback when LLM generation fails)
STATIC_ONBOARDING_QUESTIONS = [
    OnboardingQuestion(
        id=1,
        text="Корабль обнаружил неизвестный сигнал. Ваши действия?",
        options=[
            {
                "value": "repair_systems",
                "label": "Проверить все системы корабля и подготовить оборудование к возможным перегрузкам",
                "role_scores": {
                    "chief_engineer": 3,
                    "tactical_officer": 1,
                    "captain": 1,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "analyze_signal",
                "label": "Начать детальный анализ сигнала и собрать данные о его происхождении",
                "role_scores": {
                    "science_officer": 3,
                    "xenobiologist": 2,
                    "communications_officer": 1,
                    "chief_engineer": 0,
                    "tactical_officer": 0,
                    "captain": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "hail_signal",
                "label": "Немедленно установить контакт и начать переговоры с источником сигнала",
                "role_scores": {
                    "communications_officer": 3,
                    "xenobiologist": 1,
                    "security_chief": 0,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "tactical_officer": 0,
                    "captain": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "secure_perimeter",
                "label": "Активировать боевой режим, поднять щиты и подготовить оружие к бою",
                "role_scores": {
                    "security_chief": 3,
                    "tactical_officer": 3,
                    "navigator": 1,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "captain": 0,
                    "medical_officer": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "emergency_medical",
                "label": "Подготовить медицинский отсек к приёму пострадавших и проверить запасы медикаментов",
                "role_scores": {
                    "medical_officer": 3,
                    "captain": 1,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "tactical_officer": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
        ],
    ),
    OnboardingQuestion(
        id=2,
        text="Во время высадки на планету вы оказались в зоне обвала. Что вы сделаете?",
        options=[
            {
                "value": "fly_out",
                "label": "Запрыгнуть в шаттл и совершить рискованный манёвр чтобы вырваться из зоны обвала",
                "role_scores": {
                    "pilot": 3,
                    "navigator": 2,
                    "tactical_officer": 1,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "medical_officer": 0,
                    "captain": 0,
                    "xenobiologist": 0,
                },
            },
            {
                "value": "build_shelter",
                "label": "Использовать обломки пород чтобы построить временное укрытие и усилить конструкцию",
                "role_scores": {
                    "chief_engineer": 3,
                    "captain": 2,
                    "security_chief": 1,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "tactical_officer": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "triage_injured",
                "label": "Немедленно оказать первую помощь раненым и распределить ресурсы для выживания",
                "role_scores": {
                    "medical_officer": 3,
                    "captain": 2,
                    "communications_officer": 1,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "tactical_officer": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "study_cave",
                "label": "Исследовать пещеру — возможно обвал открыл проход к неизвестным пещерным формациям",
                "role_scores": {
                    "xenobiologist": 3,
                    "science_officer": 2,
                    "navigator": 1,
                    "chief_engineer": 0,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "medical_officer": 0,
                    "tactical_officer": 0,
                    "captain": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "coordinate_rescue",
                "label": "Связаться с кораблём и координировать спасательную операцию с других членов экипажа",
                "role_scores": {
                    "communications_officer": 3,
                    "security_chief": 2,
                    "tactical_officer": 1,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "captain": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
        ],
    ),
    OnboardingQuestion(
        id=3,
        text="На борту вспыхнул конфликт между двумя членами экипажа. Как вы поступите?",
        options=[
            {
                "value": "negotiate",
                "label": "Поговорить с обоими, выслушать каждую сторону и найти компромисс",
                "role_scores": {
                    "communications_officer": 3,
                    "medical_officer": 1,
                    "xenobiologist": 1,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "tactical_officer": 0,
                    "captain": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "data_driven",
                "label": "Собрать все данные о ситуации и предложить решение на основе анализа фактов",
                "role_scores": {
                    "science_officer": 3,
                    "captain": 1,
                    "chief_engineer": 1,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "tactical_officer": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "enforce_order",
                "label": "Немедленно разнять конфликтующих и установить порядок силой если необходимо",
                "role_scores": {
                    "security_chief": 3,
                    "tactical_officer": 2,
                    "pilot": 1,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "captain": 0,
                    "xenobiologist": 0,
                },
            },
            {
                "value": "check_resources",
                "label": "Проверить не вызван ли конфликт дефицитом ресурсов и перераспределить запасы",
                "role_scores": {
                    "captain": 3,
                    "chief_engineer": 1,
                    "medical_officer": 1,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "tactical_officer": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "diagnose_cause",
                "label": "Проверить нет ли медицинской или психологической причины — возможно кто-то болен",
                "role_scores": {
                    "medical_officer": 3,
                    "xenobiologist": 1,
                    "science_officer": 1,
                    "chief_engineer": 0,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "tactical_officer": 0,
                    "captain": 0,
                    "pilot": 0,
                },
            },
        ],
    ),
    OnboardingQuestion(
        id=4,
        text="Вы обнаружили инопланетный артефакт неизвестного происхождения. Ваши действия?",
        options=[
            {
                "value": "dissect_device",
                "label": "Аккуратно разобрать артефакт чтобы понять его внутреннее устройство",
                "role_scores": {
                    "chief_engineer": 3,
                    "science_officer": 2,
                    "xenobiologist": 0,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "tactical_officer": 0,
                    "captain": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "contain_sample",
                "label": "Поместить артефакт в карантинную камеру и изучить его биологические свойства",
                "role_scores": {
                    "xenobiologist": 3,
                    "medical_officer": 2,
                    "science_officer": 1,
                    "chief_engineer": 0,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "tactical_officer": 0,
                    "captain": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "secure_artifact",
                "label": "Оцепить зону и обеспечить безопасность при работе с неизвестным объектом",
                "role_scores": {
                    "security_chief": 3,
                    "tactical_officer": 2,
                    "captain": 1,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "signal_analysis",
                "label": "Попытаться расшифровать сигналы артефакта и установить коммуникацию",
                "role_scores": {
                    "communications_officer": 3,
                    "navigator": 1,
                    "science_officer": 1,
                    "chief_engineer": 0,
                    "security_chief": 0,
                    "medical_officer": 0,
                    "tactical_officer": 0,
                    "captain": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "plot_course",
                "label": "Вычислить координаты происхождения артефакта и проложить курс к его источнику",
                "role_scores": {
                    "navigator": 3,
                    "pilot": 2,
                    "science_officer": 1,
                    "chief_engineer": 0,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "medical_officer": 0,
                    "tactical_officer": 0,
                    "captain": 0,
                    "xenobiologist": 0,
                },
            },
        ],
    ),
    OnboardingQuestion(
        id=5,
        text="Корабль получил серьёзные повреждения в бою. Что вы сделаете в первую очередь?",
        options=[
            {
                "value": "emergency_repair",
                "label": "Возглавить ремонтную команду и лично чинить критические системы",
                "role_scores": {
                    "chief_engineer": 3,
                    "captain": 1,
                    "security_chief": 0,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "tactical_officer": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "tactical_retreat",
                "label": "Рассчитать манёвр уклонения и увести корабль из зоны обстрела",
                "role_scores": {
                    "pilot": 3,
                    "navigator": 2,
                    "tactical_officer": 2,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "security_chief": 0,
                    "medical_officer": 0,
                    "captain": 0,
                    "xenobiologist": 0,
                },
            },
            {
                "value": "return_fire",
                "label": "Сосредоточить огонь на слабом месте противника и подавить его орудия",
                "role_scores": {
                    "tactical_officer": 3,
                    "security_chief": 2,
                    "pilot": 1,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "communications_officer": 0,
                    "navigator": 0,
                    "medical_officer": 0,
                    "captain": 0,
                    "xenobiologist": 0,
                },
            },
            {
                "value": "treat_wounded",
                "label": "Организовать сортировку раненых и начать массовую медицинскую помощь",
                "role_scores": {
                    "medical_officer": 3,
                    "communications_officer": 1,
                    "captain": 1,
                    "chief_engineer": 0,
                    "science_officer": 0,
                    "security_chief": 0,
                    "navigator": 0,
                    "tactical_officer": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "call_backup",
                "label": "Отправить экстренный сигнал координатам и запросить подкрепление",
                "role_scores": {
                    "communications_officer": 3,
                    "navigator": 1,
                    "science_officer": 1,
                    "chief_engineer": 0,
                    "security_chief": 0,
                    "medical_officer": 0,
                    "tactical_officer": 0,
                    "captain": 0,
                    "xenobiologist": 0,
                    "pilot": 0,
                },
            },
        ],
    ),
] + build_interleaved_species_gender_questions(LANGUAGE_RU, shuffle_seed=42)


# LLM prompts for dynamic content generation (legacy format — used by main.py)
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
