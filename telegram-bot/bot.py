"""
Telegram Bot for AI Game Master
"""

import os
import logging
import asyncio
import aiohttp
from typing import Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import language as lang
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ============== Configuration ==============

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GAME_MASTER_API_URL = os.getenv("GAME_MASTER_API_URL", "http://game-master-api:8000")
BOT_LANGUAGE = os.getenv("BOT_LANGUAGE", "ru")

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

def create_onboarding_keyboard(options: list, question_id: int) -> InlineKeyboardMarkup:
    """Create inline keyboard for onboarding options"""
    builder = InlineKeyboardBuilder()
    for option in options:
        builder.add(InlineKeyboardButton(
            text=option["label"],
            callback_data=f"onboarding_answer:{question_id}:{option['value']}"
        ))
    builder.adjust(1)  # One button per row
    return builder.as_markup()


def create_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Create main menu keyboard"""
    menu = lang.get_menu(BOT_LANGUAGE)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=menu["start"])],
            [KeyboardButton(text=menu["profile"])],
            [KeyboardButton(text=menu["today"])],
            [KeyboardButton(text=menu["help"])],
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

async def cmd_start(message: types.Message, state: FSMContext):
    """Handle /start command"""
    player_id = message.from_user.id

    # Check if player already has a profile
        profile = await api_request("GET", f"/players/{player_id}/profile")
        # Player already has a profile
        msgs = lang.get_onboarding(BOT_LANGUAGE)
        await message.answer(
            msgs["welcome_back"].format(
                role=profile['role'],
                role_description=profile['role_description'],
                traits=', '.join(profile['personality_traits'])
            ),
            reply_markup=create_main_menu_keyboard()
        )
    except Exception:
        # No profile, start onboarding
        msgs = lang.get_onboarding(BOT_LANGUAGE)
        await message.answer(
            msgs["welcome"],
            reply_markup=create_main_menu_keyboard()
        )

        # Start onboarding session
        try:
            result = await api_request("POST", "/onboarding/start", {"player_id": player_id})
            await state.update_data(session_id=result["session_id"])

            if result["question"]:
                question = result["question"]
                keyboard = create_onboarding_keyboard(question["options"], question["id"])
                msgs = lang.get_onboarding(BOT_LANGUAGE)
                await message.answer(
                    msgs["question_prefix"].format(id=question['id'], text=question['text']),
                    reply_markup=keyboard
                )
                await OnboardingState.waiting_for_answer.set()
        except Exception as e:
            msgs = lang.get_errors(BOT_LANGUAGE)
            await message.answer(msgs["onboarding_error"].format(error=e))


async def cmd_profile(message: types.Message):
    """Show player profile"""
    player_id = message.from_user.id

        profile = await api_request("GET", f"/players/{player_id}/profile")
        msgs = lang.get_profile(BOT_LANGUAGE)
        await message.answer(
            f"{msgs['title']}\n\n"
            f"{msgs['role'].format(role=profile['role'])}\n\n"
            f"{msgs['description'].format(role_description=profile['role_description'])}\n\n"
            f"{msgs['traits'].format(traits='\n- '.join(profile['personality_traits']))}\n\n"
            f"{msgs['visualization'].format(avatar=profile['avatar_description'])}",
            parse_mode="Markdown"
        )
    except Exception:
        msgs = lang.get_profile(BOT_LANGUAGE)
        await message.answer(msgs["no_profile"])


async def cmd_today(message: types.Message):
    """Show current day's game episode"""
        msgs = lang.get_current_day(BOT_LANGUAGE)
        day = await api_request("GET", "/game/current-day")

        # Create action keyboard if there are actions
        keyboard = None
        if day.get("player_actions"):
            keyboard = create_action_keyboard(day["player_actions"])

        actions_text = "\n\n".join([
            f"{i+1}. {a['text']}"
            for i, a in enumerate(day.get("player_actions", []))
        ])

        await message.answer(
            msgs["title"].format(day=day['day']) + "\n\n"
            f"{msgs['story'].format(story=day['story'])}\n\n"
            f"{msgs['npc_dialogues']}\n"
            + "\n".join([f"- {d['npc']}: {d['dialogue']}" for d in day.get("npc_dialogues", [])]) +
            f"\n\n{msgs['actions'].format(actions=actions_text)}\n\n"
            f"{msgs['select_action']}",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        msgs = lang.get_current_day(BOT_LANGUAGE)
        await message.answer(msgs["error"].format(error=e))


async def cmd_help(message: types.Message):
    """Show help information"""
    msgs = lang.get_help(BOT_LANGUAGE)
    await message.answer(
        f"{msgs['title']}\n\n"
        f"{msgs['commands']}\n\n"
        f"{msgs['how_to_play']}",
        parse_mode="Markdown"
    )


async def handle_voice_message(message: types.Message):
    """Handle voice messages"""
    player_id = message.from_user.id

    msgs = lang.get_messages(BOT_LANGUAGE)
    await message.answer(
        msgs["voice_received"]
    )

    # Send message to Game Master API
    try:
        await api_request("POST", "/game/messages", {
            "player_id": player_id,
            "message": "[voice message]",
            "message_type": "voice"
        })
    except Exception as e:
        logger.error(f"Failed to send voice message to API: {e}")


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

        msgs = lang.get_messages(BOT_LANGUAGE)
        await message.answer(
            msgs["text_received"]
        )
    except Exception as e:
        msgs = lang.get_errors(BOT_LANGUAGE)
        await message.answer(msgs["error"].format(error=e))


async def onboarding_answer(callback: types.CallbackQuery, state: FSMContext):
    """Handle onboarding answer selection"""
    parts = callback.data.split(":")
    msgs = lang.get_errors(BOT_LANGUAGE)
    if len(parts) != 3:
        await callback.answer(msgs["invalid_format"])
        return

    question_id = int(parts[1])
    answer_value = parts[2]
    session_id = await state.get_data().get("session_id")

    if not session_id:
        await callback.answer(msgs["session_not_found"])
        return

    try:
        result = await api_request("POST", f"/onboarding/{session_id}/answer", {
            "question_id": question_id,
            "answer": answer_value
        })

        if result["completed"]:
            profile = result["profile"]
            msgs = lang.get_onboarding(BOT_LANGUAGE)
            await callback.message.answer(
                msgs["onboarding_complete"].format(
                    role=profile['role'],
                    role_description=profile['role_description'],
                    traits='\n- '.join(profile['personality_traits'])
                ),
                parse_mode="Markdown",
                reply_markup=create_main_menu_keyboard()
            )
            await state.clear()
        else:
            next_question = result["next_question"]
            keyboard = create_onboarding_keyboard(next_question["options"], next_question["id"])
            msgs = lang.get_onboarding(BOT_LANGUAGE)
            await callback.message.answer(
                msgs["question_prefix"].format(id=next_question['id'], text=next_question['text']),
                reply_markup=keyboard
            )

        await callback.answer()
    except Exception as e:
        msgs = lang.get_errors(BOT_LANGUAGE)
        await callback.message.answer(msgs["error"].format(error=e))
        await callback.answer()


async def action_selection(callback: types.CallbackQuery):
    """Handle player action selection"""
    action_id = callback.data.split(":")[1]
    player_id = callback.from_user.id

        # Get current day to validate
        day = await api_request("GET", "/game/current-day")

        # Submit action
        await api_request("POST", "/game/actions", {
            "player_id": player_id,
            "day": day["day"],
            "action_id": action_id,
            "choice": "selected"
        })

        msgs = lang.get_actions(BOT_LANGUAGE)
        await callback.message.answer(
            msgs["recorded"],
            reply_markup=create_main_menu_keyboard()
        )
        await callback.answer()
    except Exception as e:
        msgs = lang.get_actions(BOT_LANGUAGE)
        await callback.message.answer(msgs["error"].format(error=e))
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