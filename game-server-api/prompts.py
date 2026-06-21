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
                    "quartermaster": 1,
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
                    "quartermaster": 0,
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
                    "quartermaster": 0,
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
                    "quartermaster": 0,
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
                    "quartermaster": 1,
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
                    "quartermaster": 0,
                    "xenobiologist": 0,
                },
            },
            {
                "value": "build_shelter",
                "label": "Использовать обломки пород чтобы построить временное укрытие и усилить конструкцию",
                "role_scores": {
                    "chief_engineer": 3,
                    "quartermaster": 2,
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
                    "quartermaster": 2,
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
                    "quartermaster": 0,
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
                    "quartermaster": 0,
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
                    "quartermaster": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "data_driven",
                "label": "Собрать все данные о ситуации и предложить решение на основе анализа фактов",
                "role_scores": {
                    "science_officer": 3,
                    "quartermaster": 1,
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
                    "quartermaster": 0,
                    "xenobiologist": 0,
                },
            },
            {
                "value": "check_resources",
                "label": "Проверить не вызван ли конфликт дефицитом ресурсов и перераспределить запасы",
                "role_scores": {
                    "quartermaster": 3,
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
                    "quartermaster": 0,
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
                    "quartermaster": 0,
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
                    "quartermaster": 0,
                    "pilot": 0,
                },
            },
            {
                "value": "secure_artifact",
                "label": "Оцепить зону и обеспечить безопасность при работе с неизвестным объектом",
                "role_scores": {
                    "security_chief": 3,
                    "tactical_officer": 2,
                    "quartermaster": 1,
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
                    "quartermaster": 0,
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
                    "quartermaster": 0,
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
                    "quartermaster": 1,
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
                    "quartermaster": 0,
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
                    "quartermaster": 0,
                    "xenobiologist": 0,
                },
            },
            {
                "value": "treat_wounded",
                "label": "Организовать сортировку раненых и начать массовую медицинскую помощь",
                "role_scores": {
                    "medical_officer": 3,
                    "communications_officer": 1,
                    "quartermaster": 1,
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
                    "quartermaster": 0,
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
                    "tactical_officer": 0, "quartermaster": 0, "xenobiologist": 0, "pilot": 0
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
                    "tactical_officer": 0, "quartermaster": 0, "xenobiologist": 0, "pilot": 0
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
            "user": (
                "Ответы игрока уже содержат role_scores. "
                "Роль выбирается детерминированно по максимальной сумме очков."
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
            "user": (
                "Player answers already contain role_scores. "
                "Role is selected deterministically by highest point sum."
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
