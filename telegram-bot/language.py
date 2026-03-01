"""
Language constants for Telegram Bot
All user-facing strings should be defined here with Russian and English versions
"""

LANGUAGE_RU = "ru"
LANGUAGE_EN = "en"

# Welcome and onboarding messages
ONBOARDING = {
    LANGUAGE_RU: {
        "welcome": "Добро пожаловать в AI Game Master!\n\nВы присоединяетесь к экипажу звездного корабля.\nДавайте определим вашу роль через несколько вопросов.\n\nОтвечайте на вопросы, выбирая один из вариантов.",
        "welcome_back": "Добро пожаловать назад, {role}!\n\nВаша роль: {role_description}\nХарактеристики: {traits}\n\nИспользуйте /today для просмотра текущего дня игры.",
        "question_prefix": "Вопрос {id}:\n\n{text}",
        "onboarding_complete": "🎉 Онбординг завершён!\n\nВаша роль: **{role}**\n\n{role_description}\n\n**Характеристики:**\n- {traits}\n\nДобро пожаловать на борт!\n\nИспользуйте /today для просмотра текущего дня игры.",
    },
    LANGUAGE_EN: {
        "welcome": "Welcome to AI Game Master!\n\nYou are joining the crew of a starship.\nLet's determine your role through a few questions.\n\nAnswer the questions by choosing one of the options.",
        "welcome_back": "Welcome back, {role}!\n\nYour role: {role_description}\nTraits: {traits}\n\nUse /today to view the current game day.",
        "question_prefix": "Question {id}:\n\n{text}",
        "onboarding_complete": "🎉 Onboarding completed!\n\nYour role: **{role}**\n\n{role_description}\n\n**Traits:**\n- {traits}\n\nWelcome aboard!\n\nUse /today to view the current game day.",
    },
}

# Commands help
HELP = {
    LANGUAGE_RU: {
        "title": "🎮 **AI Game Master - Помощь**",
        "commands": "**Команды:**\n/start - Начать или продолжить игру\n/profile - Показать ваш профиль\n/today - Текущий день игры\n/help - Эта справка",
        "how_to_play": "**Как играть:**\n1. Каждый день генерируется новый сюжет\n2. Вы выбираете действия из предложенных вариантов\n3. Ваши решения влияют на развитие истории\n4. Вы можете общаться с Game Master в любое время\n\nНапишите сообщение для общения с Game Master.",
    },
    LANGUAGE_EN: {
        "title": "🎮 **AI Game Master - Help**",
        "commands": "**Commands:**\n/start - Start or continue the game\n/profile - Show your profile\n/today - Current game day\n/help - This help",
        "how_to_play": "**How to play:**\n1. A new story is generated every day\n2. You choose actions from the suggested options\n3. Your decisions affect the story development\n4. You can communicate with the Game Master at any time\n\nWrite a message to communicate with the Game Master.",
    },
}

# Profile messages
PROFILE = {
    LANGUAGE_RU: {
        "title": "👤 **Ваш профиль**",
        "role": "**Роль:** {role}",
        "description": "{role_description}",
        "traits": "**Характеристики:**\n- {traits}",
        "visualization": "**Визуализация:** {avatar}",
        "no_profile": "У вас ещё нет профиля. Пройдите онбординг с помощью /start",
    },
    LANGUAGE_EN: {
        "title": "👤 **Your Profile**",
        "role": "**Role:** {role}",
        "description": "{role_description}",
        "traits": "**Traits:**\n- {traits}",
        "visualization": "**Visualization:** {avatar}",
        "no_profile": "You don't have a profile yet. Complete onboarding with /start",
    },
}

# Current day messages
CURRENT_DAY = {
    LANGUAGE_RU: {
        "title": "📅 **День {day}**",
        "story": "*Сюжет:*\n{story}",
        "npc_dialogues": "*NPC диалоги:*",
        "actions": "*Ваши действия:*\n{actions}",
        "select_action": "Выберите действие ниже:",
        "error": "Не удалось получить информацию о текущем дне: {error}",
    },
    LANGUAGE_EN: {
        "title": "📅 **Day {day}**",
        "story": "*Story:*\n{story}",
        "npc_dialogues": "*NPC dialogues:*",
        "actions": "*Your actions:*\n{actions}",
        "select_action": "Select an action below:",
        "error": "Could not get information about the current day: {error}",
    },
}

# Action selection
ACTIONS = {
    LANGUAGE_RU: {
        "recorded": "Ваш выбор записан!\n\nGame Master обработает ваше решение и обновит сюжет.\n\nВы можете продолжить общение с Game Master или подождать следующего дня.",
        "error": "Произошла ошибка при записи выбора: {error}",
    },
    LANGUAGE_EN: {
        "recorded": "Your choice has been recorded!\n\nThe Game Master will process your decision and update the plot.\n\nYou can continue communicating with the Game Master or wait for the next day.",
        "error": "An error occurred while recording the choice: {error}",
    },
}

# Message handling
MESSAGES = {
    LANGUAGE_RU: {
        "voice_received": "Спасибо за голосовое сообщение!\nGame Master получил ваше сообщение.\nПримечание: голосовые сообщения пока не поддерживают преобразование в текст.",
        "text_received": "Game Master получил ваше сообщение.\nОтвет будет сгенерирован в ближайшее время.",
        "error": "Произошла ошибка: {error}",
        "game_master_response": "Game Master получил ваше сообщение.",
    },
    LANGUAGE_EN: {
        "voice_received": "Thank you for the voice message!\nThe Game Master has received your message.\nNote: Voice messages do not support text-to-speech conversion yet.",
        "text_received": "The Game Master has received your message.\nThe response will be generated shortly.",
        "error": "An error occurred: {error}",
        "game_master_response": "Game Master received your message.",
    },
}

# Error messages
ERRORS = {
    LANGUAGE_RU: {
        "invalid_format": "Неверный формат ответа",
        "session_not_found": "Сессия не найдена. Пожалуйста, начните заново с /start",
        "onboarding_error": "Произошла ошибка при запуске: {error}",
    },
    LANGUAGE_EN: {
        "invalid_format": "Invalid answer format",
        "session_not_found": "Session not found. Please start again with /start",
        "onboarding_error": "An error occurred during startup: {error}",
    },
}

# Menu labels
MENU = {
    LANGUAGE_RU: {
        "start": "/start",
        "profile": "/profile",
        "today": "/today",
        "help": "/help",
    },
    LANGUAGE_EN: {
        "start": "/start",
        "profile": "/profile",
        "today": "/today",
        "help": "/help",
    },
}


def get_onboarding(language: str = LANGUAGE_RU):
    """Get onboarding messages for a specific language"""
    return ONBOARDING.get(language, ONBOARDING[LANGUAGE_RU])


def get_help(language: str = LANGUAGE_RU):
    """Get help messages for a specific language"""
    return HELP.get(language, HELP[LANGUAGE_RU])


def get_profile(language: str = LANGUAGE_RU):
    """Get profile messages for a specific language"""
    return PROFILE.get(language, PROFILE[LANGUAGE_RU])


def get_current_day(language: str = LANGUAGE_RU):
    """Get current day messages for a specific language"""
    return CURRENT_DAY.get(language, CURRENT_DAY[LANGUAGE_RU])


def get_actions(language: str = LANGUAGE_RU):
    """Get action messages for a specific language"""
    return ACTIONS.get(language, ACTIONS[LANGUAGE_RU])


def get_messages(language: str = LANGUAGE_RU):
    """Get message handling strings for a specific language"""
    return MESSAGES.get(language, MESSAGES[LANGUAGE_RU])


def get_errors(language: str = LANGUAGE_RU):
    """Get error messages for a specific language"""
    return ERRORS.get(language, ERRORS[LANGUAGE_RU])


def get_menu(language: str = LANGUAGE_RU):
    """Get menu labels for a specific language"""
    return MENU.get(language, MENU[LANGUAGE_RU])
