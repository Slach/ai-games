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
