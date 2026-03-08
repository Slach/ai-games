"""
Telegram Bot for AI Game Master - Rewritten with new architecture

Key Features:
1. Onboarding via API - Questions fetched from game-master-api
2. Multiple Games Support - Track which game each player participates in
3. Polling Mechanism - Periodic checks for updates from API
4. Avatar Display - Show generated avatars in profiles
5. Inline Keyboards - For action selection and onboarding
"""

import os
import logging
import asyncio
import aiohttp
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
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
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "30"))  # seconds between API polls


# ============== FSM States ==============

class OnboardingState(StatesGroup):
    """State machine for onboarding flow"""
    waiting_for_answer = State()
    completed = State()


class GameSessionState(StatesGroup):
    """State machine for game session tracking"""
    in_game = State()


# ============== Global State Storage ==============

# In-memory storage for player sessions (in production, use Redis)
player_sessions: Dict[int, Dict[str, Any]] = {}


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


async def fetch_onboarding_questions() -> list:
    """Fetch onboarding questions from API"""
    try:
        result = await api_request("GET", "/onboarding/questions")
        return result.get("questions", [])
    except Exception as e:
        logger.error(f"Failed to fetch onboarding questions: {e}")
        return []


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


def create_game_info_keyboard(game_id: str) -> InlineKeyboardMarkup:
    """Create keyboard with game info and action buttons"""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="🔄 Refresh",
        callback_data=f"refresh_game:{game_id}"
    ))
    return builder.as_markup()


# ============== Player Session Management ==============

def get_player_session(player_id: int) -> Optional[Dict[str, Any]]:
    """Get player session data"""
    return player_sessions.get(player_id)


def set_player_session(player_id: int, game_id: str, session_data: Dict[str, Any]):
    """Set player session data"""
    player_sessions[player_id] = {
        "game_id": game_id,
        "session_data": session_data,
        "last_poll": datetime.now()
    }


def clear_player_session(player_id: int):
    """Clear player session data"""
    if player_id in player_sessions:
        del player_sessions[player_id]


# ============== Polling Mechanism ==============

async def poll_game_updates():
    """Periodically poll game-master-api for updates"""
    logger.info("Starting polling loop")
    
    while True:
        try:
            # Check all players for updates
            for player_id in list(player_sessions.keys()):
                session = get_player_session(player_id)
                if not session:
                    continue
                
                game_id = session.get("game_id")
                if not game_id:
                    continue
                
                try:
                    # Check for new messages from Game Master/NPCs
                    messages = await api_request("GET", f"/game/messages/{player_id}?limit=5")
                    
                    # Check current day status
                    day_data = await api_request("GET", "/game/current-day")
                    
                    logger.info(f"Polling updates for player {player_id}, game {game_id}")
                    
                except Exception as e:
                    logger.error(f"Error polling updates for player {player_id}: {e}")
            
            # Wait for next poll interval
            await asyncio.sleep(POLLING_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error in polling loop: {e}")
            await asyncio.sleep(60)  # Wait longer on error


# ============== Handlers ==============

async def cmd_start(message: types.Message, state: FSMContext):
    """Handle /start command - Begin onboarding or join existing game"""
    player_id = message.from_user.id
    
    msgs = lang.get_onboarding(BOT_LANGUAGE)
    
    try:
        # Check if player already has a profile
        profile = await api_request("GET", f"/players/{player_id}/profile")
        
        # Player already has a profile - join existing game
        logger.info(f"Player {player_id} returning, showing welcome back message")
        
        avatar_url = profile.get('avatar_description', '')
        avatar_text = f"\n\n🖼️ **Avatar:**\n{avatar_url}" if avatar_url else ""
        
        await message.answer(
            msgs["welcome_back"].format(
                role=profile['role'],
                role_description=profile['role_description'],
                traits=', '.join(profile['personality_traits'])
            ) + avatar_text,
            parse_mode="Markdown",
            reply_markup=create_main_menu_keyboard()
        )
        
        # Set game session
        state_data = await state.get_data()
        game_id = state_data.get("game_id") or f"game_{player_id}"
        set_player_session(player_id, game_id, {"profile": profile})
        
    except Exception:
        # No profile, start onboarding
        logger.info(f"Player {player_id} starting onboarding")
        
        await message.answer(
            msgs["welcome"],
            reply_markup=create_main_menu_keyboard()
        )
        
        # Start onboarding session via API
        try:
            result = await api_request("POST", "/onboarding/start", {"player_id": player_id})
            
            if not result.get("session_id"):
                raise Exception("No session ID returned")
            
            await state.update_data(session_id=result["session_id"])
            
            # Fetch questions from API
            questions = await fetch_onboarding_questions()
            
            if not questions:
                raise Exception("No onboarding questions available")
            
            # Store question index in state
            await state.update_data(question_index=0)
            
            if result.get("question"):
                question = result["question"]
                keyboard = create_onboarding_keyboard(question["options"], question["id"])
                
                msgs = lang.get_onboarding(BOT_LANGUAGE)
                await message.answer(
                    msgs["question_prefix"].format(id=question['id'], text=question['text']),
                    reply_markup=keyboard
                )
                await OnboardingState.waiting_for_answer.set()
            else:
                # No questions, onboarding complete
                profile = result.get("profile", {})
                msgs = lang.get_onboarding(BOT_LANGUAGE)
                await message.answer(
                    msgs["onboarding_complete"].format(
                        role=profile.get('role', 'Crew Member'),
                        role_description=profile.get('role_description', ''),
                        traits='\n- '.join(profile.get('personality_traits', []))
                    ),
                    parse_mode="Markdown",
                    reply_markup=create_main_menu_keyboard()
                )
                await state.clear()
                
        except Exception as e:
            msgs = lang.get_errors(BOT_LANGUAGE)
            logger.error(f"Onboarding error for player {player_id}: {e}")
            await message.answer(msgs["onboarding_error"].format(error=e))


async def cmd_profile(message: types.Message):
    """Show player profile with avatar"""
    player_id = message.from_user.id
    
    msgs = lang.get_profile(BOT_LANGUAGE)
    
    try:
        profile = await api_request("GET", f"/players/{player_id}/profile")
        
        # Format avatar display
        avatar_url = profile.get('avatar_description', '')
        avatar_display = f"\n\n🖼️ **Avatar:**\n{avatar_url}" if avatar_url else ""
        
        await message.answer(
            f"{msgs['title']}\n\n"
            f"{msgs['role'].format(role=profile['role'])}\n\n"
            f"{msgs['description'].format(role_description=profile['role_description'])}\n\n"
            f"{msgs['traits'].format(traits='\n- '.join(profile['personality_traits']))}" + avatar_display,
            parse_mode="Markdown",
            reply_markup=create_main_menu_keyboard()
        )
        
    except Exception as e:
        msgs = lang.get_profile(BOT_LANGUAGE)
        logger.error(f"Profile error for player {player_id}: {e}")
        await message.answer(msgs["no_profile"])


async def cmd_today(message: types.Message):
    """Show current day's game episode with action choices"""
    player_id = message.from_user.id
    
    msgs = lang.get_current_day(BOT_LANGUAGE)
    
    try:
        # Get current day
        day = await api_request("GET", "/game/current-day")
        
        # Create action keyboard if there are actions
        keyboard = None
        if day.get("player_actions"):
            keyboard = create_action_keyboard(day["player_actions"])
        
        # Format story and dialogues
        story_text = day.get('story', '')
        npc_dialogues = day.get("npc_dialogues", [])
        dialogues_text = "\n".join([f"- **{d['npc']}**: {d['dialogue']}" for d in npc_dialogues]) if npc_dialogues else "No NPC dialogues"
        
        actions_text = "\n\n".join([
            f"{i+1}. {a['text']}"
            for i, a in enumerate(day.get("player_actions", []))
        ])
        
        # Check if player has already selected an action
        player_actions = await api_request("GET", f"/game/messages/{player_id}?limit=1")
        action_selected = any(a.get('action_id') for a in player_actions.get('messages', []))
        
        await message.answer(
            msgs["title"].format(day=day['day']) + "\n\n"
            f"{msgs['story'].format(story=story_text)}\n\n"
            f"{msgs['npc_dialogues']}\n{dialogues_text}\n\n"
            f"{msgs['actions'].format(actions=actions_text)}\n\n"
            f"{msgs['select_action']}",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        
    except Exception as e:
        msgs = lang.get_current_day(BOT_LANGUAGE)
        logger.error(f"Today error for player {player_id}: {e}")
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
        response = await api_request("POST", "/game/messages", {
            "player_id": player_id,
            "message": message.text,
            "message_type": "text"
        })
        
        msgs = lang.get_messages(BOT_LANGUAGE)
        
        # Display Game Master response if available
        if response.get("response"):
            await message.answer(
                f"{msgs['game_master_response']}\n\n{response['response']}",
                parse_mode="Markdown"
            )
        else:
            await message.answer(msgs["text_received"])
            
    except Exception as e:
        msgs = lang.get_errors(BOT_LANGUAGE)
        logger.error(f"Text message error for player {player_id}: {e}")
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
        
        if result.get("completed"):
            profile = result.get("profile", {})
            msgs = lang.get_onboarding(BOT_LANGUAGE)
            
            # Display avatar if generated
            avatar_url = profile.get('avatar_description', '')
            avatar_text = f"\n\n🖼️ **Avatar:**\n{avatar_url}" if avatar_url else ""
            
            await callback.message.answer(
                msgs["onboarding_complete"].format(
                    role=profile.get('role', 'Crew Member'),
                    role_description=profile.get('role_description', ''),
                    traits='\n- '.join(profile.get('personality_traits', []))
                ) + avatar_text,
                parse_mode="Markdown",
                reply_markup=create_main_menu_keyboard()
            )
            
            # Clear onboarding state
            await state.clear()
            
        else:
            next_question = result.get("next_question")
            if next_question:
                keyboard = create_onboarding_keyboard(next_question["options"], next_question["id"])
                msgs = lang.get_onboarding(BOT_LANGUAGE)
                await callback.message.answer(
                    msgs["question_prefix"].format(id=next_question['id'], text=next_question['text']),
                    reply_markup=keyboard
                )
        
        await callback.answer()
        
    except Exception as e:
        msgs = lang.get_errors(BOT_LANGUAGE)
        logger.error(f"Onboarding answer error: {e}")
        await callback.message.answer(msgs["error"].format(error=e))
        await callback.answer()


async def action_selection(callback: types.CallbackQuery):
    """Handle player action selection"""
    parts = callback.data.split(":")
    
    if len(parts) != 2:
        await callback.answer("Invalid action format")
        return
    
    action_id = parts[1]
    player_id = callback.from_user.id
    
    msgs = lang.get_actions(BOT_LANGUAGE)
    
    try:
        # Get current day to validate
        day = await api_request("GET", "/game/current-day")
        
        # Submit action
        result = await api_request("POST", "/game/actions", {
            "player_id": player_id,
            "day": day["day"],
            "action_id": action_id,
            "choice": "selected"
        })
        
        await callback.message.answer(
            msgs["recorded"],
            reply_markup=create_main_menu_keyboard()
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Action selection error for player {player_id}: {e}")
        await callback.message.answer(msgs["error"].format(error=e))
        await callback.answer()


async def refresh_game(callback: types.CallbackQuery):
    """Refresh game state from API"""
    parts = callback.data.split(":")
    
    if len(parts) != 2:
        await callback.answer("Invalid format")
        return
    
    game_id = parts[1]
    player_id = callback.from_user.id
    
    try:
        # Get current day
        day = await api_request("GET", "/game/current-day")
        
        # Re-display today's episode
        msgs = lang.get_current_day(BOT_LANGUAGE)
        
        actions_text = "\n\n".join([
            f"{i+1}. {a['text']}"
            for i, a in enumerate(day.get("player_actions", []))
        ])
        
        npc_dialogues = day.get("npc_dialogues", [])
        dialogues_text = "\n".join([f"- **{d['npc']}**: {d['dialogue']}" for d in npc_dialogues]) if npc_dialogues else "No NPC dialogues"
        
        await callback.message.edit_text(
            msgs["title"].format(day=day['day']) + "\n\n"
            f"{msgs['story'].format(story=day['story'])}\n\n"
            f"{msgs['npc_dialogues']}\n{dialogues_text}\n\n"
            f"{msgs['actions'].format(actions=actions_text)}\n\n"
            f"{msgs['select_action']}",
            parse_mode="Markdown",
            reply_markup=create_action_keyboard(day.get("player_actions", []))
        )
        
        await callback.answer("Game refreshed")
        
    except Exception as e:
        logger.error(f"Refresh error for player {player_id}: {e}")
        await callback.answer(f"Error: {str(e)}")


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
    
    # Callback handlers
    dp.callback_query.register(onboarding_answer, F.data.startswith("onboarding_answer:"))
    dp.callback_query.register(action_selection, F.data.startswith("action:"))
    dp.callback_query.register(refresh_game, F.data.startswith("refresh_game:"))
    
    # Start polling loop in background
    asyncio.create_task(poll_game_updates())
    
    logger.info("Starting Telegram Bot")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
