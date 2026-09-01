"""
Language constants for Game Server API
All user-facing strings should be defined here with Russian and English versions
"""

import re

from game_rules import HULL_MAX, SHIELDS_MAX, THREAT_MAX

LANGUAGE_RU = "ru"
LANGUAGE_EN = "en"


# Ship role keys (10 crew positions). Order is canonical — derived into
# SHIP_ROLE_KEYS in database.py and embedded in onboarding schemas.
SHIP_ROLES_KEYS = [
    "captain",
    "chief_engineer",
    "science_officer",
    "communications_officer",
    "security_chief",
    "navigator",
    "medical_officer",
    "tactical_officer",
    "xenobiologist",
    "pilot",
]

# Display names only — role_key is the identity, this is just the localized
# label shown in UI. All other role flavour (description, avatar description,
# personality traits) is generated per-character via LLM at onboarding/NPC
# creation time and stored in player_profiles / npc_profiles.
SHIP_ROLE_NAMES = {
    "captain": {LANGUAGE_RU: "Капитан", LANGUAGE_EN: "Captain"},
    "chief_engineer": {LANGUAGE_RU: "Инженер-механик", LANGUAGE_EN: "Chief Engineer"},
    "science_officer": {LANGUAGE_RU: "Научный офицер", LANGUAGE_EN: "Science Officer"},
    "communications_officer": {LANGUAGE_RU: "Офицер связи", LANGUAGE_EN: "Communications Officer"},
    "security_chief": {LANGUAGE_RU: "Начальник безопасности", LANGUAGE_EN: "Security Chief"},
    "navigator": {LANGUAGE_RU: "Штурман", LANGUAGE_EN: "Navigator"},
    "medical_officer": {LANGUAGE_RU: "Медицинский офицер", LANGUAGE_EN: "Medical Officer"},
    "tactical_officer": {LANGUAGE_RU: "Тактический офицер", LANGUAGE_EN: "Tactical Officer"},
    "xenobiologist": {LANGUAGE_RU: "Ксенобиолог", LANGUAGE_EN: "Xenobiologist"},
    "pilot": {LANGUAGE_RU: "Пилот", LANGUAGE_EN: "Pilot"},
}


# Species/gender type display names
SPECIES_TYPE_NAMES = {
    LANGUAGE_RU: {
        "human": "Человек",
        "humanoid": "Гуманоид",
        "non_humanoid": "Негуманоид",
        "energy": "Энергетическая форма жизни",
        "cybernetic": "Кибернетическая форма жизни",
        "symbiotic": "Симбиотическая форма жизни",
    },
    LANGUAGE_EN: {
        "human": "Human",
        "humanoid": "Humanoid",
        "non_humanoid": "Non-Humanoid",
        "energy": "Energy Being",
        "cybernetic": "Cybernetic Life Form",
        "symbiotic": "Symbiotic Life Form",
    },
}

HYBRID_SPECIES_NAMES = {
    LANGUAGE_RU: {
        "human+humanoid": "Почти человек, но с необычной культурой или физиологией",
        "humanoid+non_humanoid": "Гуманоид с выраженными нечеловеческими чертами",
        "non_humanoid+energy": "Плазменный, кристаллический или газовый организм",
        "energy+cybernetic": "Разум, живущий в энергетической сети",
        "cybernetic+symbiotic": "Кибернетический коллектив или носитель ИИ-симбионта",
        "symbiotic+human": "Человек, соединённый с наследуемым симбионтом",
    },
    LANGUAGE_EN: {
        "human+humanoid": "Nearly human but with unusual culture or physiology",
        "humanoid+non_humanoid": "Humanoid with pronounced non-human traits",
        "non_humanoid+energy": "Plasmic, crystalline, or gaseous organism",
        "energy+cybernetic": "Mind living within an energy network",
        "cybernetic+symbiotic": "Cybernetic collective or AI-symbiont host",
        "symbiotic+human": "Human connected to an inherited symbiont",
    },
}

GENDER_TYPE_NAMES = {
    LANGUAGE_RU: {
        "male": "Мужской",
        "female": "Женский",
        "neutral": "Нейтральный / Бесполый",
        "fluid": "Сменяемый пол",
        "multiple": "Множественный пол",
        "resonance": "Резонансный / энергетический пол",
        "synthetic": "Синтетический / сконструированный пол",
        "symbiotic": "Симбиотический пол",
    },
    LANGUAGE_EN: {
        "male": "Male",
        "female": "Female",
        "neutral": "Neutral / Genderless",
        "fluid": "Fluid Gender",
        "multiple": "Multiple Gender",
        "resonance": "Resonance / Energy Gender",
        "synthetic": "Synthetic / Constructed Gender",
        "symbiotic": "Symbiotic Gender",
    },
}

# Canonical species/gender tags — the dice pool for random character proposals.
SPECIES_TAGS = ["human", "humanoid", "non_humanoid", "energy", "cybernetic", "symbiotic"]
GENDER_TAGS = ["male", "female", "neutral", "fluid", "multiple", "resonance", "synthetic", "symbiotic"]


def get_ship_role_name(role_key: str, language: str) -> str:
    """Get the localized display name of a ship role by role_key.

    Falls back to Russian if the requested language is not defined.
    Returns the role_key itself if the role is unknown.
    """
    names = SHIP_ROLE_NAMES.get(role_key, {})
    return names.get(language, names.get(LANGUAGE_RU, role_key))


def get_ship_role_name_en(role_key: str) -> str:
    """Get the English display name of a ship role by role_key.

    Returns the role_key itself if the role is unknown.
    """
    return SHIP_ROLE_NAMES.get(role_key, {}).get(LANGUAGE_EN, role_key)


def get_species_type_name(species_type: str, language: str) -> str:
    """Get localized species type display name."""
    names = SPECIES_TYPE_NAMES.get(language, SPECIES_TYPE_NAMES[LANGUAGE_RU])
    return names.get(species_type, species_type)


def get_hybrid_species_name(hybrid_key: str, language: str) -> str:
    """Get localized hybrid species description."""
    names = HYBRID_SPECIES_NAMES.get(language, HYBRID_SPECIES_NAMES[LANGUAGE_RU])
    return names.get(hybrid_key, hybrid_key)


def get_gender_type_name(gender_type: str, language: str) -> str:
    """Get localized gender type display name."""
    names = GENDER_TYPE_NAMES.get(language, GENDER_TYPE_NAMES[LANGUAGE_RU])
    return names.get(gender_type, gender_type)


# Game-level strings used across main.py
GAME_STRINGS = {
    LANGUAGE_RU: {
        "game_title_fallback": "Звёздный Крейсер «Рассвет»: За горизонтом известного",
        "welcome_text_fallback": (
            "Кают-компания звёздного корабля мерцает голографическими дисплеями. Экипаж ждёт нового члена. Докажите, что вы достойны места среди звёзд.\n\n"
            "Как можно проиграть:\n"
            "— Угроза растёт каждый ход: на 100 миссия провалена.\n"
            "— Корпус накапливает урон и не чинится сам: 0 — гибель корабля.\n"
            "— Раны копятся: тяжёлая рана плюс любая новая — смерть.\n"
            "— Промедление (авто-действие по таймеру хода) ускоряет рост угрозы.\n"
            "— Экипаж может взбунтоваться от тяжёлых потерь.\n"
            "— Экипаж не пополняется: каждая смерть насовсем.\n\n"
            "Как победить: быстро двигайте миссию, чините корабль, лечите раны — действуйте до срока."
        ),
        "turn_prefix": "Ход {turn} — {title}",
        "turn_prefix_simple": "Ход {turn}",
        "auto_select_notification": ("⏳ *Время хода вышло — зафиксировано ПРОМЕДЛЕНИЕ.*\n\nНерешительность имеет цену: угроза растёт. Game Master выбрал за вас:\n\nВыбрано действие: *{action_text}*\n\n_{rationale}_"),
        "turn_summary": {
            "ship_status": "Состояние корабля: {status}",
            "hull_shields": "Корпус: {hull}, Щиты: {shields}",
            "systems_offline": "Системы отключены: {systems}",
            "crew_morale": "Мораль экипажа: {morale}",
            "deceased": "Погибшие: {names}",
            "injured": "Раненые: {names}",
            "ship_destroyed": "КОРАБЛЬ УНИЧТОЖЕН",
            "next_turn_hook": "Зацепка для следующего хода: {hook}",
        },
        "cumulative_story": {
            "header": "=== ПРЕДЫДУЩИЕ ХОДЫ ===",
            "turn_label": "Ход",
        },
        "gm_fallback": {
            "fallback_title": "{display_name} — {role_label}",
            "fallback_briefing": "{display_name}, ты — {role_label}. Ты оцениваешь ситуацию спокойно и профессионально.",
            "fallback_species": {
                "human": "Ты — человек. Твоё тело биологическое, уязвимое, но полное жизни.",
                "humanoid": "Ты — гуманоид с узнаваемой анатомией, но необычной физиологией.",
                "non_humanoid": "Твоя форма далека от человеческой — панцирь, щупальца или иная необычная биология.",
                "energy": "Ты — энергетическая форма жизни. Твоё сознание существует как устойчивый резонансный узор.",
                "cybernetic": "Ты — кибернетическая форма жизни. Части тебя можно чинить, улучшать и переносить.",
                "symbiotic": 'Ты — симбиотическая форма жизни. Твоё "я" рождается в союзе нескольких существ.',
            },
            "hybrid_format_ru": " В тебе также есть черты: {secondary}",
            "unknown_species_format": "Твой вид — {species_type}.",
            "gender_note": " Твой пол: {gender_type}.",
            "role_note": " Твоя роль на корабле — {role}.",
            "mission_fallback": {
                "name": "Первый контакт",
                "description": "Исследовать неизвестный сигнал в секторе 7-Альфа. Установить контакт с цивилизацией.",
                "short_description": "Исследовать загадочный сигнал в секторе 7-Альфа и установить первый контакт с неизвестной цивилизацией.",
                "stages": [
                    {"name": "Разведка", "description": "Приблизиться к источнику сигнала"},
                    {"name": "Контакт", "description": "Установить коммуникацию"},
                    {"name": "Дипломатия", "description": "Достичь взаимопонимания"},
                ],
            },
            "mission_labels": {
                "stage_label": "Этап",
                "mission_header": "КОНТЕКСТ МИССИИ",
                "mission_sub": "это текущая миссия, её сюжет обязателен для этого дня",
                "name_label": "Название",
                "desc_label": "Описание",
                "stages_header": "Этапы",
                "importance_text": "ВАЖНО: Все обстоятельства дня должны строго соответствовать этой миссии. Не придумывай новый сеттинг — используй сеттинг из описания миссии.",
            },
            "fallback_npc_names": {
                "captain": "Капитан Алексей Старк",
                "pilot": "Пилот Виктор Соколов",
                "chief_engineer": "Инженер Дмитрий Волков",
                "science_officer": "Научный офицер Елена Романова",
                "communications_officer": "Офицер связи Анна Белова",
                "security_chief": "Начальник безопасности Иван Громов",
                "navigator": "Штурман Мария Крылова",
                "medical_officer": "Медик София Павлова",
                "tactical_officer": "Тактик Кирилл Огнев",
                "quartermaster": "Квартирмейстер Пётр Кузнецов",
                "xenobiologist": "Ксенобиолог Алиса Рубинова",
            },
            "fallback_npc_default": "{role_name} экипажа",
        },
        "game_over": {
            "triumph_header": "✨ МИССИЯ ВЫПОЛНЕНА БЕЗУПРЕЧНО — ТРИУМФ!",
            "victory_header": "🏆 МИССИЯ ВЫПОЛНЕНА — ПОБЕДА!",
            "pyrrhic_header": "🔥 МИССИЯ ВЫПОЛНЕНА ЛЮБОЙ ЦЕНОЙ — ПИРРОВА ПОБЕДА",
            "stalemate_header": "⚖️ ЦЕЛЬ НЕ ДОСТИГНУТА — НИЧЬЯ",
            "defeat_header": "💀 КОРАБЛЬ УНИЧТОЖЕН — ПОРАЖЕНИЕ",
            "reason_mission_complete": "Причина конца: миссия выполнена",
            "reason_ship_destroyed": "Причина конца: корабль уничтожен",
            "reason_crew_wiped": "Причина конца: экипаж погиб",
            "reason_overwhelmed": "Причина конца: угроза достигла предела",
            "reason_mutiny": "Причина конца: мятеж экипажа",
            "summary_title": "📊 Итоги миссии",
            "summary_outcome_line": "Исход: {outcome} · Причина: {reason}",
            "summary_stats_line": "Ходов: {turns} · Корпус: {hull}/{hull_max} · Щиты: {shields}/{shields_max} · Угроза: {threat}/{threat_max}",
            "summary_casualties_line": "Погибли: {dead} · Выжили: {alive} из {total}",
            "summary_no_dead": "нет",
            "summary_actions_header": "Действия:",
            "summary_actions_line": "{name} — {actions} (промедления {auto})",
            "summary_reasons": {
                "mission_complete": "миссия выполнена",
                "ship_destroyed": "корабль уничтожен",
                "crew_wiped": "экипаж погиб",
                "overwhelmed": "угроза достигла предела",
                "mutiny": "мятеж экипажа",
            },
            "fallback_triumph": {
                "finale_narrative": "Миссия выполнена безупречно. Корабль цел, экипаж в строю, угроза отражена. Экипаж возвращается домой, и звёзды, кажется, салютуют им.",
                "finale_image_prompt": "A pristine starship gliding triumphantly through calm space, crew celebrating on a spotless bridge, golden light, cinematic lighting, Star Trek aesthetic, 4K quality, epic composition.",
            },
            "fallback_victory": {
                "finale_narrative": "Миссия выполнена. Экипаж возвращается домой, зная, что их смелость и решительность изменили ход истории. Звёзды будут помнить этот день.",
                "finale_image_prompt": "A victorious starship crew standing on the bridge, celebrating their successful mission, triumphant expressions, cinematic lighting, Star Trek aesthetic, 4K quality, epic composition.",
            },
            "fallback_pyrrhic": {
                "finale_narrative": "Миссия выполнена — но цена оказалась невосполнимой. Корабль изранен, кто-то из экипажа не вернётся домой. Победа, которую почти невозможно назвать победой.",
                "finale_image_prompt": "A heavily damaged starship limping home from a completed mission, hull breached and scarred, somber survivors on the bridge, tragic yet epic, cinematic lighting, Star Trek aesthetic, 4K quality, emotional composition.",
            },
            "fallback_stalemate": {
                "finale_narrative": "Цель так и не достигнута, но экипаж уцелел и увёл корабль от гибели. История не окончена — она оборвалась на полуслове, и эта незавершённость останется с ними навсегда.",
                "finale_image_prompt": "A battered starship retreating into deep space, leaving an unfinished objective behind, mood of survival and incompleteness, cinematic lighting, Star Trek aesthetic, 4K quality, epic composition.",
            },
            "fallback_defeat": {
                "finale_narrative": "Корабль погиб в огне и тишине космоса. Но даже в поражении экипаж проявил мужество, достойное легенд. Их история будет рассказана.",
                "finale_image_prompt": "A starship breaking apart in space, dramatic explosion, debris floating in zero gravity, tragic and epic, cinematic lighting, Star Trek aesthetic, 4K quality, emotional composition.",
            },
        },
        "npc_loyalty": {
            "steadfast": "предан",
            "uneasy": "нервничает",
            "on_edge": "на грани",
            "mutinous": "готов взбунтоваться",
        },
    },
    LANGUAGE_EN: {
        "game_title_fallback": "Star Cruiser «Dawn»: Beyond the Known Horizon",
        "welcome_text_fallback": (
            "The starship's mess hall glows with holographic displays. The crew awaits a new member. Prove you are worthy of a place among the stars.\n\n"
            "How you can lose:\n"
            "— Threat rises every turn: at 100 the mission is failed.\n"
            "— Hull damage accumulates and never self-repairs: 0 means the ship is destroyed.\n"
            "— Wounds pile up: a critical wound plus any new one kills.\n"
            "— Hesitation (timer auto-action) accelerates the threat.\n"
            "— The crew can mutiny after heavy losses.\n"
            "— The crew is never replenished: every death is permanent.\n\n"
            "How to win: push the mission fast, repair the ship, treat wounds — act before the clock runs out."
        ),
        "turn_prefix": "Turn {turn} — {title}",
        "turn_prefix_simple": "Turn {turn}",
        "auto_select_notification": ("⏳ *Turn time ran out — DELAY recorded.*\n\nHesitation has a price: the threat grows. The Game Master chose for you:\n\nSelected action: *{action_text}*\n\n_{rationale}_"),
        "turn_summary": {
            "ship_status": "Ship status: {status}",
            "hull_shields": "Hull: {hull}, Shields: {shields}",
            "systems_offline": "Systems offline: {systems}",
            "crew_morale": "Crew morale: {morale}",
            "deceased": "Deceased: {names}",
            "injured": "Injured: {names}",
            "ship_destroyed": "SHIP DESTROYED",
            "next_turn_hook": "Next turn hook: {hook}",
        },
        "cumulative_story": {
            "header": "=== PREVIOUS TURNS ===",
            "turn_label": "Turn",
        },
        "gm_fallback": {
            "fallback_title": "{display_name} — {role_label}",
            "fallback_briefing": "{display_name}, you are the {role_label}. You assess the situation calmly and professionally.",
            "fallback_species": {
                "human": "You are human. Your body is biological, vulnerable, but full of life.",
                "humanoid": "You are a humanoid with recognizable anatomy but unusual physiology.",
                "non_humanoid": "Your form is far from human — a carapace, tentacles, or other unusual biology.",
                "energy": "You are an energy being. Your consciousness exists as a stable resonance pattern.",
                "cybernetic": "You are a cybernetic life form. Parts of you can be repaired, upgraded, and transferred.",
                "symbiotic": 'You are a symbiotic life form. Your "self" is born from the union of several beings.',
            },
            "hybrid_format_en": " You also bear traits of: {secondary}",
            "unknown_species_format": "Your species is {species_type}.",
            "gender_note": " Your gender: {gender_type}.",
            "role_note": " Your role aboard the ship is {role}.",
            "mission_fallback": {
                "name": "First Contact",
                "description": "Investigate an unknown signal in sector 7-Alpha. Establish contact with a civilization.",
                "short_description": "Investigate a mysterious signal in sector 7-Alpha and make first contact with an unknown civilization.",
                "stages": [
                    {"name": "Reconnaissance", "description": "Approach the signal source"},
                    {"name": "Contact", "description": "Establish communication"},
                    {"name": "Diplomacy", "description": "Achieve mutual understanding"},
                ],
            },
            "mission_labels": {
                "stage_label": "Stage",
                "mission_header": "MISSION CONTEXT",
                "mission_sub": "this is the current mission, its story is mandatory for this turn",
                "name_label": "Name",
                "desc_label": "Description",
                "stages_header": "Stages",
                "importance_text": "IMPORTANT: All circumstances MUST be strictly consistent with this mission. Do not invent a new setting — use the setting from the mission description.",
            },
            "fallback_npc_names": {
                "captain": "Captain Eva Rodriguez",
                "pilot": "Pilot Alex 'Ace' Turner",
                "chief_engineer": "Chief Engineer Marcus Chen",
                "science_officer": "Dr. Aisha Patel",
                "communications_officer": "Comm Officer Sarah Williams",
                "security_chief": "Security Chief Jake Morrison",
                "navigator": "Navigator Leo Kim",
                "medical_officer": "Dr. Nina Hart",
                "tactical_officer": "Tactical Officer Rex Vane",
                "quartermaster": "Quartermaster Tessa Cole",
                "xenobiologist": "Dr. Kiran Voss",
            },
            "fallback_npc_default": "The {role_name}",
        },
        "game_over": {
            "triumph_header": "✨ FLAWLESS MISSION — TRIUMPH!",
            "victory_header": "🏆 MISSION COMPLETE — VICTORY!",
            "pyrrhic_header": "🔥 MISSION AT ANY COST — PYRRHIC VICTORY",
            "stalemate_header": "⚖️ OBJECTIVE UNMET — STALEMATE",
            "defeat_header": "💀 SHIP DESTROYED — DEFEAT",
            "reason_mission_complete": "End reason: mission accomplished",
            "reason_ship_destroyed": "End reason: ship destroyed",
            "reason_crew_wiped": "End reason: crew lost",
            "reason_overwhelmed": "End reason: threat reached its peak",
            "reason_mutiny": "End reason: crew mutiny",
            "summary_title": "📊 Mission Summary",
            "summary_outcome_line": "Outcome: {outcome} · Reason: {reason}",
            "summary_stats_line": "Turns: {turns} · Hull: {hull}/{hull_max} · Shields: {shields}/{shields_max} · Threat: {threat}/{threat_max}",
            "summary_casualties_line": "Lost: {dead} · Survived: {alive} of {total}",
            "summary_no_dead": "none",
            "summary_actions_header": "Actions:",
            "summary_actions_line": "{name} — {actions} (delays {auto})",
            "summary_reasons": {
                "mission_complete": "mission accomplished",
                "ship_destroyed": "ship destroyed",
                "crew_wiped": "crew lost",
                "overwhelmed": "threat reached its peak",
                "mutiny": "crew mutiny",
            },
            "fallback_triumph": {
                "finale_narrative": "The mission is accomplished flawlessly. The ship is intact, the crew stands unbroken, the threat repelled. They fly home, and the stars seem to salute them.",
                "finale_image_prompt": "A pristine starship gliding triumphantly through calm space, crew celebrating on a spotless bridge, golden light, cinematic lighting, Star Trek aesthetic, 4K quality, epic composition.",
            },
            "fallback_victory": {
                "finale_narrative": "The mission is accomplished. The crew returns home knowing their courage and resolve changed the course of history. The stars will remember this turn.",
                "finale_image_prompt": "A victorious starship crew standing on the bridge, celebrating their successful mission, triumphant expressions, cinematic lighting, Star Trek aesthetic, 4K quality, epic composition.",
            },
            "fallback_pyrrhic": {
                "finale_narrative": "The mission is accomplished — but the price was irreparable. The ship is broken, and some of the crew will never come home. A victory that can barely be called one.",
                "finale_image_prompt": "A heavily damaged starship limping home from a completed mission, hull breached and scarred, somber survivors on the bridge, tragic yet epic, cinematic lighting, Star Trek aesthetic, 4K quality, emotional composition.",
            },
            "fallback_stalemate": {
                "finale_narrative": "The objective was never reached, yet the crew survived and pulled the ship back from doom. The story is not over — it broke off mid-sentence, and that incompleteness will stay with them forever.",
                "finale_image_prompt": "A battered starship retreating into deep space, leaving an unfinished objective behind, mood of survival and incompleteness, cinematic lighting, Star Trek aesthetic, 4K quality, epic composition.",
            },
            "fallback_defeat": {
                "finale_narrative": "The ship perished in fire and the silence of space. But even in defeat, the crew showed courage worthy of legends. Their story will be told.",
                "finale_image_prompt": "A starship breaking apart in space, dramatic explosion, debris floating in zero gravity, tragic and epic, cinematic lighting, Star Trek aesthetic, 4K quality, emotional composition.",
            },
        },
        "npc_loyalty": {
            "steadfast": "steadfast",
            "uneasy": "uneasy",
            "on_edge": "on edge",
            "mutinous": "ready to mutiny",
        },
    },
}


def get_game_strings(language: str) -> dict:
    """Get game-level localized strings."""
    return GAME_STRINGS.get(language, GAME_STRINGS[LANGUAGE_RU])


def _md_escape(text: str) -> str:
    """Escape Telegram Markdown special characters in dynamic values."""
    return re.sub(r"([_*`\[])", r"\\\1", text)


def format_game_summary(
    language: str,
    *,
    outcome_label: str,
    end_status: str,
    turns: int,
    hull: int,
    shields: int,
    threat: int,
    dead_names: list[str],
    alive_crew: int,
    total_crew: int,
    player_stats: list[dict],
) -> str:
    """Build the compact post-finale mission summary (Telegram Markdown).

    player_stats: [{name, actions, auto_actions}] — one line per player,
    auto_actions being the code-assigned 'delay' (hesitation) count.
    """
    msgs = get_game_strings(language)["game_over"]
    reason = msgs["summary_reasons"].get(end_status, end_status)
    dead = ", ".join(_md_escape(n) for n in dead_names) if dead_names else msgs["summary_no_dead"]
    lines = [
        f"*{msgs['summary_title']}*",
        msgs["summary_outcome_line"].format(outcome=outcome_label, reason=reason),
        msgs["summary_stats_line"].format(
            turns=turns, hull=hull, hull_max=HULL_MAX,
            shields=shields, shields_max=SHIELDS_MAX,
            threat=threat, threat_max=THREAT_MAX,
        ),
        msgs["summary_casualties_line"].format(dead=dead, alive=alive_crew, total=total_crew),
    ]
    if player_stats:
        action_lines = [
            msgs["summary_actions_line"].format(
                name=_md_escape(str(s.get("name", ""))),
                actions=s.get("actions", 0),
                auto=s.get("auto_actions", 0),
            )
            for s in player_stats
        ]
        lines.append(f"*{msgs['summary_actions_header']}*\n" + "\n".join(action_lines))
    return "\n".join(lines)
