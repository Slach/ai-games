"""
Telegram Bot for AI Game Master
"""

import os
import logging
import asyncio
import aiohttp
from typing import Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ============== Configuration ==============

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GAME_MASTER_API_URL = os.getenv("GAME_MASTER_API_URL", "http://game-master-api:8000")


# ============== FSM States ==============

class OnboardingState(StatesGroup):
    """State machine for onboarding flow"""
    waiting_for_answer = State()
    completed = State()


# ============== Helper Functions ==============

async def api_request(method: str, endpoint: str, data: Optional[dict] = None) -> dict:
    """Make a request to the Game Master API"""
    url = f"{GAME_MASTER_API_URL}{endpoint}"
    async with aiohttp.ClientSession() as session:
        async with session.request(method, url, json=data) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"API error: {resp.status} - {error_text}")
                raise Exception(f"API error: {resp.status}")
            return await resp.json()


# ============== Keyboard Builders ==============

def create_onboarding_keyboard(options: list) -> InlineKeyboardMarkup:
    """Create inline keyboard for onboarding options"""
    builder = InlineKeyboardBuilder()
    for option in options:
        builder.add(InlineKeyboardButton(
            text=option["label"],
            callback_data=f"onboarding_answer:{option['value']}"
        ))
    builder.adjust(1)  # One button per row
    return builder.as_markup()


def create_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Create main menu keyboard"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/start")],
            [KeyboardButton(text="/profile")],
            [KeyboardButton(text="/today")],
            [KeyboardButton(text="/help")],
        ],
        resize_keyboard=True
    )


def create_action_keyboard(actions: list) -> InlineKeyboardMarkup:
    """Create inline keyboard for game actions"""
    builder = InlineKeyboardBuilder()
    for action in actions:
        builder.add(InlineKeyboardButton(
            text=action["text"],
            callback_data=f"action:{action['id']}"
        ))
    builder.adjust(1)
    return builder.as_markup()


# ============== Handlers ==============

@aiogram.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Handle /start command"""
    player_id = message.from_user.id

    # Check if player already has a profile
    try:
        profile = await api_request("GET", f"/players/{player_id}/profile")
        # Player already has a profile
        await message.answer(
            f"Добро пожаловать назад, {profile['role']}!\n\n"
            f"Ваша роль: {profile['role_description']}\n"
            f"Характеристики: {', '.join(profile['personality_traits'])}\n\n"
            f"Используйте /today для просмотра текущего дня игры.",
            reply_markup=create_main_menu_keyboard()
        )
    except Exception:
        # No profile, start onboarding
        await message.answer(
            "Добро пожаловать в AI Game Master!\n\n"
            "Вы присоединяетесь к экипажу звездного корабля.\n"
            "Давайте определим вашу роль через несколько вопросов.\n\n"
            "Отвечайте на вопросы, выбирая один из вариантов.",
            reply_markup=create_main_menu_keyboard()
        )

        # Start onboarding session
        try:
            result = await api_request("POST", f"/onboarding/start", {"player_id": player_id})
            await state.update_data(session_id=result["session_id"])

            if result["question"]:
                question = result["question"]
                keyboard = create_onboarding_keyboard(question["options"])
                await message.answer(
                    f"Вопрос {question['id']}:\n\n{question['text']}",
                    reply_markup=keyboard
                )
                await OnboardingState.waiting_for_answer.set()
        except Exception as e:
            await message.answer(f"Произошла ошибка при запуске: {e}")


@aiogram.message(Command("profile"))
async def cmd_profile(message: types.Message):
    """Show player profile"""
    player_id = message.from_user.id

    try:
        profile = await api_request("GET", f"/players/{player_id}/profile")
        await message.answer(
            f"👤 **Ваш профиль**\n\n"
            f"**Роль:** {profile['role']}\n\n"
            f"{profile['role_description']}\n\n"
            f"**Характеристики:**\n- {'\n- '.join(profile['personality_traits'])}\n\n"
            f"**Визуализация:** {profile['avatar_description']}",
            parse_mode="Markdown"
        )
    except Exception:
        await message.answer(
            "У вас ещё нет профиля. Пройдите онбординг с помощью /start"
        )


@aiogram.message(Command("today"))
async def cmd_today(message: types.Message):
    """Show current day's game episode"""
    try:
        day = await api_request("GET", "/game/current-day")

        actions_text = "\n\n".join([
            f"{i+1}. {a['text']}"
            for i, a in enumerate(day.get("player_actions", []))
        ])

        await message.answer(
            f"📅 **День {day['day']}**\n\n"
            f"*Сюжет:*\n{day['story']}\n\n"
            f"*NPC диалоги:*\n"
            + "\n".join([f"- {d['npc']}: {d['dialogue']}" for d in day.get("npc_dialogues", [])]) +
            f"\n\n*Ваши действия:*\n{actions_text}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"Не удалось получить информацию о текущем дне: {e}")


@aiogram.message(Command("help"))
async def cmd_help(message: types.Message):
    """Show help information"""
    await message.answer(
        "🎮 **AI Game Master - Помощь**\n\n"
        "**Команды:**\n"
        "/start - Начать или продолжить игру\n"
        "/profile - Показать ваш профиль\n"
        "/today - Текущий день игры\n"
        "/help - Эта справка\n\n"
        "**Как играть:**\n"
        "1. Каждый день генерируется новый сюжет\n"
        "2. Вы выбираете действия из предложенных вариантов\n"
        "3. Ваши решения влияют на развитие истории\n"
        "4. Вы можете общаться с Game Master в любое время\n\n"
        "Напишите сообщение для общения с Game Master.",
        parse_mode="Markdown"
    )


@aiogram.message(F.content_type == types.ContentType.VOICE)
async def handle_voice_message(message: types.Message):
    """Handle voice messages"""
    player_id = message.from_user.id

    # Download voice file
    voice = message.voice
    file = await bot.get_file(voice.file_id)
    # TODO: Download file and send to speech-to-text service

    await message.answer(
        "Спасибо за голосовое сообщение!\n"
        "Game Master получил ваше сообщение и ответит скоро."
    )

    # TODO: Send message to Game Master API
    # await api_request("POST", "/game/messages", {
    #     "player_id": player_id,
    #     "message": "voice:message_id",
    #     "message_type": "voice"
    # })


@aiogram.message(F.text & ~F.command)
async def handle_text_message(message: types.Message):
    """Handle regular text messages (chat with Game Master)"""
    player_id = message.from_user.id

    try:
        # Send message to Game Master API
        await api_request("POST", "/game/messages", {
            "player_id": player_id,
            "message": message.text,
            "message_type": "text"
        })

        await message.answer(
            "Game Master получил ваше сообщение.\n"
            "Ответ будет сгенерирован в ближайшее время."
        )
    except Exception as e:
        await message.answer(f"Произошла ошибка: {e}")


@aiogram.callback_query(F.data.startswith("onboarding_answer:"))
async def onboarding_answer(callback: types.CallbackQuery, state: FSMContext):
    """Handle onboarding answer selection"""
    data = callback.data.split(":")[1]
    session_id = await state.get_data().get("session_id")

    try:
        # Extract question_id from callback data (we need to store it)
        # For now, get current question from API
        status = await api_request("GET", f"/onboarding/{session_id}")
        question_id = status["current_question"] + 1  # 1-indexed

        result = await api_request("POST", f"/onboarding/{session_id}/answer", {
            "question_id": question_id,
            "answer": data
        })

        if result["completed"]:
            profile = result["profile"]
            await callback.message.answer(
                f"🎉 Онбординг завершён!\n\n"
                f"Ваша роль: **{profile['role']}**\n\n"
                f"{profile['role_description']}\n\n"
                f"**Характеристики:**\n- {'\n- '.join(profile['personality_traits'])}\n\n"
                f"Добро пожаловать на борт!\n\n"
                f"Используйте /today для просмотра текущего дня игры.",
                parse_mode="Markdown",
                reply_markup=create_main_menu_keyboard()
            )
            await state.clear()
        else:
            next_question = result["next_question"]
            keyboard = create_onboarding_keyboard(next_question["options"])
            await callback.message.answer(
                f"Вопрос {next_question['id']}:\n\n{next_question['text']}",
                reply_markup=keyboard
            )

        await callback.answer()
    except Exception as e:
        await callback.message.answer(f"Произошла ошибка: {e}")
        await callback.answer()


@aiogram.callback_query(F.data.startswith("action:"))
async def action_selection(callback: types.CallbackQuery):
    """Handle player action selection"""
    action_id = callback.data.split(":")[1]
    player_id = callback.from_user.id

    try:
        # Get current day to validate
        day = await api_request("GET", "/game/current-day")

        # Submit action
        await api_request("POST", "/game/actions", {
            "player_id": player_id,
            "day": day["day"],
            "action_id": action_id,
            "choice": "selected"
        })

        await callback.message.answer(
            f"Ваш выбор записан!\n\n"
            f"Game Master обработает ваше решение и обновит сюжет.\n\n"
            f"Вы можете продолжить общение с Game Master или подождать следующего дня."
        )
        await callback.answer()
    except Exception as e:
        await callback.message.answer(f"Произошла ошибка при записи выбора: {e}")
        await callback.answer()


# ============== Main Entry Point ==============

async def main():
    """Main entry point"""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    # Initialize bot and dispatcher
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Register handlers
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_profile, Command("profile"))
    dp.message.register(cmd_today, Command("today"))
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(handle_voice_message, F.content_type == types.ContentType.VOICE)
    dp.message.register(handle_text_message, F.text & ~F.command)
    dp.callback_query.register(onboarding_answer, F.data.startswith("onboarding_answer:"))
    dp.callback_query.register(action_selection, F.data.startswith("action:"))

    logger.info("Starting Telegram Bot")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())