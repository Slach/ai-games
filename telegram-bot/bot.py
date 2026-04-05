"""
Telegram Bot for AI Game Master - New Architecture

Key Features:
1. Onboarding via API - Questions fetched from game-master-api
2. Multiple Games Support - Track which game each player participates in
3. Polling Mechanism - Periodic polling for updates from API
4. Enhanced Game Flow - Better state management and inline keyboards
5. Avatar Display - Show generated avatars in profiles

Architecture:
- Uses aiogram with FSM for state management
- Maintains existing language support (Russian/English)
- Uses existing language.py for messages
- Proper error handling and logging
- Async HTTP calls to game-master-api
"""

import os
import logging
import asyncio
import aiohttp
from typing import Optional, Dict, Any
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram_sqlite_storage.sqlitestore import SQLStorage
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp_socks import ProxyConnector
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BufferedInputFile

import language as lang

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ============== Configuration ==============

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GAME_MASTER_API_URL = os.getenv("GAME_MASTER_API_URL", "http://game-master-api:8000")
BOT_LANGUAGE = os.getenv("BOT_LANGUAGE", "ru")
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "30"))  # seconds between polls

# Socks5 proxy configuration
# Set to empty string to disable proxy (direct connection)
# For Docker, use host.docker.internal:PORT or proxy IP address
TELEGRAM_SOCKS_PROXY = os.getenv("TELEGRAM_SOCKS_PROXY", "")

# ============== FSM States ==============


class OnboardingState(StatesGroup):
    """State machine for onboarding flow"""

    waiting_for_answer = State()
    completed = State()


class GameSessionState(StatesGroup):
    """State machine for game session tracking"""

    waiting_for_action = State()
    waiting_for_message = State()


# ============== Player State Storage ==============

# In-memory storage for player state (could be replaced with Redis in production)
player_states: Dict[int, Dict[str, Any]] = {}


def get_player_state(player_id: int) -> Dict[str, Any]:
    """Get or create player state"""
    if player_id not in player_states:
        player_states[player_id] = {
            "game_id": None,
            "onboarding_session_id": None,
            "current_question": 0,
            "last_poll": datetime.now(),
            "pending_updates": [],
        }
    return player_states[player_id]


def update_player_state(player_id: int, **kwargs):
    """Update player state with new values"""
    state = get_player_state(player_id)
    state.update(kwargs)


# ============== Helper Functions ==============


def parse_proxy_url(proxy_url: str) -> tuple[str, int, Optional[str], Optional[str]]:
    """Parse socks5 proxy URL into components.

    Expected format: host:port or user:pass@host:port
    Returns: (host, port, username, password)
    """
    # Remove protocol if present
    if proxy_url.startswith("socks5://"):
        proxy_url = proxy_url[9:]
    elif proxy_url.startswith("socks5h://"):
        proxy_url = proxy_url[10:]

    # Extract credentials if present
    username = None
    password = None
    if "@" in proxy_url:
        creds, rest = proxy_url.rsplit("@", 1)
        if ":" in creds:
            username, password = creds.split(":", 1)
        proxy_url = rest

    # Extract host and port
    if ":" in proxy_url:
        host, port = proxy_url.rsplit(":", 1)
        return (host, int(port), username, password)

    return (proxy_url, 9999, username, password)


async def create_aiohttp_session(proxy_url: str = None) -> aiohttp.ClientSession:
    """Create an aiohttp ClientSession with Socks5 proxy support.

    Args:
        proxy_url: Proxy URL in format host:port or user:pass@host:port
                   If None, uses TELEGRAM_SOCKS_PROXY env var

    Returns:
        Configured aiohttp.ClientSession
    """
    if proxy_url is None:
        proxy_url = TELEGRAM_SOCKS_PROXY

    try:
        host, port, username, password = parse_proxy_url(proxy_url)

        connector = ProxyConnector(
            host=host, port=port, username=username or None, password=password or None
        )

        return aiohttp.ClientSession(connector=connector)

    except Exception as e:
        logger.warning(
            f"Failed to configure proxy {proxy_url}: {e}. Using direct connection."
        )
        return aiohttp.ClientSession()


async def api_request(
    method: str,
    endpoint: str,
    data: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout_total: int = 600,
    ignore_codes: tuple = (),
) -> Optional[dict]:
    """Make a request to the Game Master API (direct connection, no proxy)

    Args:
        method: HTTP method
        endpoint: API endpoint path
        data: JSON data for request body
        params: Query parameters
        timeout_total: Total timeout in seconds
        ignore_codes: Tuple of HTTP status codes to ignore (return None instead of raising)

    Returns:
        Response JSON as dict, or None if status code is in ignore_codes
    """
    url = f"{GAME_MASTER_API_URL}{endpoint}"

    # Direct connection - no proxy for internal API calls
    session = aiohttp.ClientSession()

    try:
        async with session.request(
            method,
            url,
            json=data,
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout_total),
        ) as resp:
            if resp.status in ignore_codes:
                return None
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"API error: {resp.status} - {error_text}")
                raise Exception(f"API error: {resp.status} - {error_text}")
            return await resp.json()
    except aiohttp.ClientError as e:
        logger.error(f"HTTP error during API request: {e}")
        raise
    finally:
        await session.close()


def create_bot_session(proxy_url: str = None):
    """Create an AiohttpSession for aiogram Bot with SOCKS5 proxy support.

    Args:
        proxy_url: Proxy URL in format host:port or socks5://host:port
                   or user:pass@host:port. Empty string for direct connection.

    Returns:
        AiohttpSession with SOCKS5 proxy configured (or direct connection)
    """
    if proxy_url is None:
        proxy_url = TELEGRAM_SOCKS_PROXY

    # Empty proxy means direct connection
    if not proxy_url or not proxy_url.strip():
        return AiohttpSession()

    try:
        # Ensure proxy URL has socks5:// prefix
        if not proxy_url.startswith("socks5://") and not proxy_url.startswith(
            "socks5h://"
        ):
            proxy_url = f"socks5://{proxy_url}"

        session = AiohttpSession(proxy=proxy_url)
        logger.info(f"Configured SOCKS5 proxy: {proxy_url}")
        return session

    except Exception as e:
        logger.warning(
            f"Failed to configure proxy {proxy_url}: {e}. Using direct connection."
        )
        return AiohttpSession()


async def fetch_onboarding_questions() -> list:
    """Fetch onboarding questions from API"""
    try:
        result = await api_request("GET", "/onboarding/questions")
        return result.get("questions", [])
    except Exception as e:
        logger.error(f"Failed to fetch onboarding questions: {e}")
        raise


async def check_player_game_status(player_id: int) -> Optional[Dict[str, Any]]:
    """Check if player has an existing game profile"""
    try:
        profile = await api_request("GET", f"/players/{player_id}/profile")
        return profile
    except Exception:
        return None


async def poll_game_updates(player_id: int):
    """Poll for new game updates (days, actions, messages)"""
    state = get_player_state(player_id)

    # Skip polling if player hasn't completed onboarding yet
    if state.get("onboarding_session_id") is not None:
        logger.debug(f"Skipping poll for player {player_id}: onboarding in progress")
        return

    try:
        # Get last poll timestamp from profile
        # If profile doesn't exist (404), skip polling silently
        profile = await api_request(
            "GET", f"/players/{player_id}/profile", ignore_codes=(404,)
        )
        if not profile:
            # Player hasn't completed onboarding yet - skip polling
            return
        last_poll = profile.get("last_poll") if profile else None

        # Poll for updates using the new endpoint
        params = {"last_poll": last_poll} if last_poll is not None else {}
        result = await api_request("GET", f"/game/poll/{player_id}", params=params)

        # Process updates
        if result.get("new_game_day"):
            state["pending_updates"].append(
                {
                    "type": "new_day",
                    "day": result["new_game_day"],
                    "timestamp": datetime.now(),
                }
            )

        if result.get("pending_actions"):
            state["pending_updates"].append(
                {
                    "type": "pending_actions",
                    "actions": result["pending_actions"],
                    "timestamp": datetime.now(),
                }
            )

        # Update last poll time
        state["last_poll"] = datetime.now()

    except Exception as e:
        logger.error(f"Failed to poll game updates for player {player_id}: {e}")


async def _generate_and_send_avatar(player_id: int, session_id: str, bot: Bot):
    """Generate avatar, then send onboarding complete message with avatar, then notify others."""
    try:
        result = await api_request(
            "POST",
            f"/onboarding/{session_id}/complete",
            timeout_total=300,
        )
        avatar_url = result.get("avatar_url")
        profile = result.get("profile", {})
        game_started = result.get("game_started", False)
        game_just_started = result.get("game_just_started", False)
        player_count = result.get("player_count", 1)
        other_player_ids = result.get("other_player_ids", [])

        onboarding_msgs = lang.get_onboarding(BOT_LANGUAGE)

        # Build the onboarding message text
        onboarding_text = onboarding_msgs["onboarding_complete"].format(
            role=profile.get("role", "Crew Member"),
            role_description=profile.get("role_description", ""),
            traits="\n- ".join(profile.get("personality_traits", [])),
        )

        # Add game status message
        if game_started:
            onboarding_text += "\n\n" + onboarding_msgs["game_already_started"]
        else:
            onboarding_text += "\n\n" + onboarding_msgs["game_waiting"]

        # Send message with or without avatar
        if avatar_url:
            logger.info(f"Avatar generated for player {player_id}: {avatar_url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    avatar_url,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        photo_data = await resp.read()

                        photo = BufferedInputFile(photo_data, filename="avatar.png")
                        await bot.send_photo(
                            chat_id=player_id,
                            photo=photo,
                            caption=onboarding_text,
                            parse_mode="Markdown",
                            reply_markup=create_main_menu_keyboard(),
                        )
                    else:
                        logger.warning(f"Failed to download avatar: {resp.status}")
                        await bot.send_message(
                            chat_id=player_id,
                            text=onboarding_text,
                            parse_mode="Markdown",
                            reply_markup=create_main_menu_keyboard(),
                        )
        else:
            logger.info(f"No avatar URL for player {player_id}")
            await bot.send_message(
                chat_id=player_id,
                text=onboarding_text,
                parse_mode="Markdown",
                reply_markup=create_main_menu_keyboard(),
            )

        # If game just started (this player made it >= 3), notify all players
        if game_just_started:
            await _broadcast_game_started(player_id, profile, other_player_ids, bot)
        elif game_started and other_player_ids:
            # Game already started, but notify other players about new member
            await _broadcast_new_player(player_id, profile, other_player_ids, bot)

    except Exception as e:
        logger.error(f"Avatar generation/sending failed for player {player_id}: {e}")
        try:
            onboarding_msgs = lang.get_onboarding(BOT_LANGUAGE)
            # Try to get profile info for fallback message
            try:
                profile = await api_request("GET", f"/players/{player_id}/profile")
                text = onboarding_msgs["onboarding_complete"].format(
                    role=profile.get("role", "Crew Member"),
                    role_description=profile.get("role_description", ""),
                    traits="\n- ".join(profile.get("personality_traits", [])),
                )
            except Exception:
                text = onboarding_msgs["onboarding_complete"].format(
                    role="Crew Member",
                    role_description="",
                    traits="Unknown",
                )
            await bot.send_message(
                chat_id=player_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=create_main_menu_keyboard(),
            )
        except Exception:
            pass


async def _broadcast_new_player(new_player_id: int, profile: dict, other_player_ids: list, bot: Bot):
    """Notify existing players about a new crew member joining."""
    try:
        onboarding_msgs = lang.get_onboarding(BOT_LANGUAGE)
        player_name = str(new_player_id)  # Use player ID as name for now
        notify_text = onboarding_msgs["new_player_joined"].format(
            player_name=player_name,
            role=profile.get("role", "Crew Member"),
            role_description=profile.get("role_description", ""),
        )

        for other_id in other_player_ids:
            try:
                await bot.send_message(
                    chat_id=other_id,
                    text=notify_text,
                    parse_mode="Markdown",
                )
                # Send avatar to other players too
                avatar_url = profile.get("avatar_url")
                if avatar_url:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            avatar_url,
                            timeout=aiohttp.ClientTimeout(total=60),
                        ) as resp:
                            if resp.status == 200:
                                photo_data = await resp.read()
                                photo = BufferedInputFile(photo_data, filename="avatar.png")
                                await bot.send_photo(
                                    chat_id=other_id,
                                    photo=photo,
                                    caption=f"👆 {player_name} — {profile.get('role', '')}",
                                )
            except Exception as e:
                logger.warning(f"Failed to notify player {other_id}: {e}")
    except Exception as e:
        logger.error(f"Broadcast new player failed: {e}")


async def _broadcast_game_started(new_player_id: int, profile: dict, other_player_ids: list, bot: Bot):
    """Notify all players that the game has started (the new player triggered >= 3 players)."""
    try:
        onboarding_msgs = lang.get_onboarding(BOT_LANGUAGE)
        player_name = str(new_player_id)

        # Notify the new player that the game started
        await bot.send_message(
            chat_id=new_player_id,
            text=onboarding_msgs["game_already_started"],
        )

        # Notify existing players that the game is now starting
        for other_id in other_player_ids:
            try:
                await bot.send_message(
                    chat_id=other_id,
                    text=f"🚀 Игра начинается! {player_name} присоединился, и команда в полном составе.\n\n{onboarding_msgs['new_player_joined'].format(player_name=player_name, role=profile.get('role', ''), role_description=profile.get('role_description', ''))}",
                    parse_mode="Markdown",
                )
                # Send avatar to other players
                avatar_url = profile.get("avatar_url")
                if avatar_url:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            avatar_url,
                            timeout=aiohttp.ClientTimeout(total=60),
                        ) as resp:
                            if resp.status == 200:
                                photo_data = await resp.read()
                                photo = BufferedInputFile(photo_data, filename="avatar.png")
                                await bot.send_photo(
                                    chat_id=other_id,
                                    photo=photo,
                                    caption=f"👆 {player_name} — {profile.get('role', '')}",
                                )
            except Exception as e:
                logger.warning(f"Failed to notify player {other_id} about game start: {e}")
    except Exception as e:
        logger.error(f"Broadcast game started failed: {e}")


def wrap_text(text: str, width: int = 35) -> str:
    """Wrap text into multiple lines for Telegram button.

    Telegram inline buttons have limited width. This function splits
    long text into multiple lines at word boundaries.
    """
    words = text.split()
    lines = []
    current_line = []
    current_length = 0

    for word in words:
        if current_length + len(word) + 1 <= width:
            current_line.append(word)
            current_length += len(word) + 1
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_length = len(word)

    if current_line:
        lines.append(" ".join(current_line))

    return "\n".join(lines)


def create_onboarding_keyboard(options: list, question_id: int) -> ReplyKeyboardMarkup:
    """Create reply keyboard for onboarding options.

    Uses ReplyKeyboardMarkup instead of InlineKeyboardMarkup to avoid text truncation.
    Telegram reply keyboards display full text without ellipsis.
    """
    keyboard = []
    for option in options:
        # Use full label text - reply keyboards don't truncate
        button = KeyboardButton(text=option["label"])
        keyboard.append([button])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True,  # Hide keyboard after selection
    )


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
        resize_keyboard=True,
    )


def create_action_keyboard(actions: list) -> InlineKeyboardMarkup:
    """Create inline keyboard for game actions"""
    builder = InlineKeyboardBuilder()
    for action in actions:
        # Wrap long action text to fit in Telegram button width
        text = wrap_text(action["text"], width=35)
        builder.add(
            InlineKeyboardButton(text=text, callback_data=f"action:{action['id']}")
        )
    builder.adjust(1)
    return builder.as_markup()


def create_game_info_keyboard(game_id: str) -> InlineKeyboardMarkup:
    """Create keyboard with game information"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="🔄 Refresh", callback_data=f"refresh_game:{game_id}")
    )
    return builder.as_markup()


# ============== Handlers ==============


async def cmd_start(message: types.Message, state: FSMContext):
    """Handle /start command - Begin onboarding or join existing game"""
    player_id = message.from_user.id

    msgs = lang.get_onboarding(BOT_LANGUAGE)

    # Check if player already has a profile
    try:
        profile = await check_player_game_status(player_id)

        if profile:
            # Player already has a profile - welcome back
            await message.answer(
                msgs["welcome_back"].format(
                    role=profile["role"],
                    role_description=profile["role_description"],
                    traits=", ".join(profile["personality_traits"]),
                ),
                reply_markup=create_main_menu_keyboard(),
            )

            # Update player state with game info
            update_player_state(
                player_id, game_id=profile.get("game_id", "default_game")
            )

        else:
            # Start onboarding session (long timeout for LLM generation)
            try:
                logger.info(
                    f"Starting onboarding for player_id={player_id}, language={BOT_LANGUAGE}"
                )
                result = await api_request(
                    "POST",
                    "/onboarding/start",
                    data={
                        "player_id": player_id,
                        "game_id": "default_game",
                        "language": BOT_LANGUAGE,
                    },
                    timeout_total=600,
                )
                logger.info(f"Onboarding start response: {result}")

                welcome_text = result.get("welcome_message") or msgs["welcome"]
                game_title = result.get("game_title", "")
                if game_title:
                    welcome_text = f"**{game_title}**\n\n{welcome_text}" if welcome_text else f"**{game_title}**"

                await message.answer(
                    welcome_text,
                    parse_mode="Markdown",
                    reply_markup=create_main_menu_keyboard(),
                )

                session_id = result.get("session_id")
                game_id = result.get("game_id", "default_game")

                if not session_id:
                    raise Exception("No session ID returned from API")

                question = result.get("question")
                if not question:
                    raise Exception("No question returned from API")

                await state.update_data(
                    session_id=session_id,
                    game_id=game_id,
                    current_question_id=question["id"],
                    current_options=question["options"],
                )
                update_player_state(
                    player_id, onboarding_session_id=session_id, game_id=game_id
                )

                if question:
                    logger.info(
                        f"First onboarding question: id={question['id']}, text={question['text'][:50]}..."
                    )
                    keyboard = create_onboarding_keyboard(
                        question["options"], question["id"]
                    )
                    await message.answer(
                        msgs["question_prefix"].format(
                            id=question["id"], text=question["text"]
                        ),
                        reply_markup=keyboard,
                    )
                    await state.set_state(OnboardingState.waiting_for_answer)

            except Exception as e:
                logger.error(
                    f"Failed to start onboarding for player {player_id}: {type(e).__name__} - {str(e)}"
                )
                error_msgs = lang.get_errors(BOT_LANGUAGE)
                await message.answer(
                    error_msgs["onboarding_error"].format(error=str(e))
                )

    except Exception as e:
        logger.error(f"Error in /start command for player {player_id}: {e}")
        error_msgs = lang.get_errors(BOT_LANGUAGE)
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def cmd_profile(message: types.Message):
    """Show player profile with avatar"""
    player_id = message.from_user.id

    try:
        profile = await api_request("GET", f"/players/{player_id}/profile")
        msgs = lang.get_profile(BOT_LANGUAGE)

        # Build profile message
        profile_text = f"{msgs['title']}\n\n"
        profile_text += f"{msgs['role'].format(role=profile['role'])}\n\n"
        profile_text += f"{msgs['description'].format(role_description=profile['role_description'])}\n\n"
        profile_text += f"{msgs['traits'].format(traits='\n- '.join(profile['personality_traits']))}\n\n"

        # Add avatar if available (URL or description)
        if profile.get("avatar_url"):
            avatar_link = f"[Avatar Image]({profile['avatar_url']})"
            profile_text += msgs["visualization"].format(avatar=avatar_link)
        elif profile.get("avatar_description"):
            profile_text += msgs["visualization"].format(
                avatar=profile["avatar_description"]
            )

        await message.answer(profile_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Failed to get profile for player {player_id}: {e}")
        msgs = lang.get_profile(BOT_LANGUAGE)
        await message.answer(msgs["no_profile"])


async def cmd_today(message: types.Message):
    """Show current day's game episode"""
    player_id = message.from_user.id

    try:
        msgs = lang.get_current_day(BOT_LANGUAGE)
        day = await api_request("GET", "/game/current-day")

        # Create action keyboard if there are actions to select
        keyboard = None
        if day.get("player_actions"):
            keyboard = create_action_keyboard(day["player_actions"])

        # Build actions text
        actions_text = "\n\n".join(
            [
                f"{i + 1}. {a['text']}"
                for i, a in enumerate(day.get("player_actions", []))
            ]
        )

        # Build NPC dialogues text
        npc_dialogues_text = ""
        if day.get("npc_dialogues"):
            npc_dialogues_text = "\n".join(
                [f"- {d['npc']}: {d['dialogue']}" for d in day["npc_dialogues"]]
            )

        await message.answer(
            msgs["title"].format(day=day["day"]) + "\n\n"
            f"{msgs['story'].format(story=day['story'])}\n\n"
            f"{msgs['npc_dialogues']}\n{npc_dialogues_text}"
            + f"\n\n{msgs['actions'].format(actions=actions_text)}\n\n"
            f"{msgs['select_action']}",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(f"Failed to get current day for player {player_id}: {e}")
        msgs = lang.get_current_day(BOT_LANGUAGE)
        await message.answer(msgs["error"].format(error=str(e)))


async def cmd_help(message: types.Message):
    """Show help information"""
    msgs = lang.get_help(BOT_LANGUAGE)
    
    # Fetch game title dynamically from API
    game_title = "🎮 Game"
    try:
        title_data = await api_request("GET", "/game/title", params={"game_id": "default_game"})
        if title_data and title_data.get("title"):
            game_title = f"🎮 {title_data['title']}"
    except Exception as e:
        logger.warning(f"Failed to fetch game title for help: {e}")
        # Fallback to generic title
        game_title = "🎮 Space Exploration Game"
    
    help_title = f"**{game_title} — Help**"
    
    await message.answer(
        f"{help_title}\n\n{msgs['commands']}\n\n{msgs['how_to_play']}",
        parse_mode="Markdown",
    )


async def handle_voice_message(message: types.Message):
    """Handle voice messages"""
    player_id = message.from_user.id

    msgs = lang.get_messages(BOT_LANGUAGE)
    await message.answer(msgs["voice_received"])

    # Send message to Game Master API
    try:
        await api_request(
            "POST",
            "/game/messages",
            data={
                "player_id": player_id,
                "message": "[voice message]",
                "message_type": "voice",
            },
        )
    except Exception as e:
        logger.error(f"Failed to send voice message to API: {e}")


async def handle_text_message(message: types.Message):
    """Handle regular text messages (chat with Game Master)"""
    player_id = message.from_user.id

    try:
        # Send message to Game Master API
        response = await api_request(
            "POST",
            "/game/messages",
            data={
                "player_id": player_id,
                "message": message.text,
                "message_type": "text",
            },
        )

        msgs = lang.get_messages(BOT_LANGUAGE)

        # If there's a response from Game Master, show it
        if response.get("response"):
            await message.answer(
                f"{msgs['game_master_response']}\n\n{response['response']}",
                parse_mode="Markdown",
            )
        else:
            await message.answer(msgs["text_received"])

    except Exception as e:
        logger.error(f"Failed to send text message to API: {e}")
        msgs = lang.get_messages(BOT_LANGUAGE)
        await message.answer(msgs["error"].format(error=str(e)))


async def onboarding_answer(message: types.Message, state: FSMContext):
    """Handle onboarding answer selection from reply keyboard.

    Matches button text to option value and submits to API.
    """
    answer_text = message.text
    player_id = message.from_user.id
    error_msgs = lang.get_errors(BOT_LANGUAGE)

    logger.info(
        f"Onboarding answer handler called: player={player_id}, text={answer_text}"
    )

    # Get current question data from state
    state_data = await state.get_data()
    session_id = state_data.get("session_id")
    current_question_id = state_data.get("current_question_id")
    current_options = state_data.get("current_options")

    logger.info(
        f"State data: session_id={session_id}, question_id={current_question_id}, options_count={len(current_options) if current_options else 0}"
    )

    if not session_id:
        logger.error(f"No session_id in state for player {player_id}")
        await message.answer(error_msgs["session_not_found"])
        return

    if not current_options:
        logger.error(f"No current_options in state for player {player_id}")
        await message.answer(error_msgs["invalid_format"])
        return

    # Find matching option by button text
    answer_value = None
    for option in current_options:
        if option["label"] == answer_text:
            answer_value = option["value"]
            break

    if not answer_value:
        await message.answer(error_msgs["invalid_format"])
        return

    try:
        logger.info(
            f"Submitting onboarding answer: session_id={session_id}, question_id={current_question_id}, answer={answer_value}"
        )
        result = await api_request(
            "POST",
            f"/onboarding/{session_id}/answer",
            data={"question_id": current_question_id, "answer": answer_value},
            params={"language": BOT_LANGUAGE},
        )
        logger.info(f"Onboarding answer response: completed={result.get('completed')}")

        onboarding_msgs = lang.get_onboarding(BOT_LANGUAGE)

        if result.get("completed"):
            profile = result.get("profile")
            logger.info(
                f"Onboarding completed for player {player_id}: role={profile['role']}"
            )

            try:
                verify_profile = await api_request(
                    "GET", f"/players/{player_id}/profile"
                )
                logger.info(
                    f"Profile verified for player {player_id}: {verify_profile.get('role')}"
                )
            except Exception as verify_error:
                logger.error(
                    f"Profile verification failed for player {player_id}: {verify_error}"
                )

            await state.clear()
            update_player_state(player_id, onboarding_session_id=None)

            # Avatar generation + onboarding message is handled in _generate_and_send_avatar
            asyncio.create_task(
                _generate_and_send_avatar(player_id, session_id, message.bot)
            )

        else:
            next_question = result.get("next_question")
            if next_question:
                # Store next question data in state for matching
                await state.update_data(
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                )
                keyboard = create_onboarding_keyboard(
                    next_question["options"], next_question["id"]
                )
                await message.answer(
                    onboarding_msgs["question_prefix"].format(
                        id=next_question["id"], text=next_question["text"]
                    ),
                    reply_markup=keyboard,
                )

    except Exception as e:
        logger.error(f"Failed to submit onboarding answer: {e}")
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def action_selection(callback: types.CallbackQuery):
    """Handle player action selection"""
    parts = callback.data.split(":")

    if len(parts) != 2:
        await callback.answer(lang.get_errors(BOT_LANGUAGE)["invalid_format"])
        return

    action_id = parts[1]
    player_id = callback.from_user.id

    try:
        # Get current day to validate
        day = await api_request("GET", "/game/current-day")

        # Submit action
        await api_request(
            "POST",
            "/game/actions",
            data={
                "player_id": player_id,
                "day": day["day"],
                "action_id": action_id,
                "choice": "selected",
            },
        )

        msgs = lang.get_actions(BOT_LANGUAGE)
        await callback.message.answer(
            msgs["recorded"], reply_markup=create_main_menu_keyboard()
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Failed to record action for player {player_id}: {e}")
        msgs = lang.get_actions(BOT_LANGUAGE)
        await callback.message.answer(msgs["error"].format(error=str(e)))
        await callback.answer()


async def refresh_game(callback: types.CallbackQuery):
    """Refresh game information"""
    parts = callback.data.split(":")

    if len(parts) != 2:
        await callback.answer(lang.get_errors(BOT_LANGUAGE)["invalid_format"])
        return

    game_id = parts[1]
    player_id = callback.from_user.id

    try:
        # Refresh current day
        day = await api_request("GET", "/game/current-day")

        msgs = lang.get_current_day(BOT_LANGUAGE)

        # Build actions text
        actions_text = "\n\n".join(
            [
                f"{i + 1}. {a['text']}"
                for i, a in enumerate(day.get("player_actions", []))
            ]
        )

        # Build NPC dialogues text
        npc_dialogues_text = ""
        if day.get("npc_dialogues"):
            npc_dialogues_text = "\n".join(
                [f"- {d['npc']}: {d['dialogue']}" for d in day["npc_dialogues"]]
            )

        await callback.message.edit_text(
            msgs["title"].format(day=day["day"]) + "\n\n"
            f"{msgs['story'].format(story=day['story'])}\n\n"
            f"{msgs['npc_dialogues']}\n{npc_dialogues_text}"
            + f"\n\n{msgs['actions'].format(actions=actions_text)}\n\n"
            f"{msgs['select_action']}",
            parse_mode="Markdown",
            reply_markup=create_action_keyboard(day.get("player_actions", [])),
        )

        await callback.answer()

    except Exception as e:
        logger.error(f"Failed to refresh game for player {player_id}: {e}")
        await callback.answer(
            lang.get_messages(BOT_LANGUAGE)["error"].format(error=str(e))
        )


# ============== Polling Loop ==============


async def polling_loop(bot: Bot):
    """Background polling loop for checking updates"""
    logger.info("Starting polling loop")

    while True:
        try:
            # Get all player IDs from state storage
            player_ids = list(player_states.keys())

            for player_id in player_ids:
                state = get_player_state(player_id)

                # Check if enough time has passed since last poll
                if (
                    datetime.now() - state["last_poll"]
                ).total_seconds() >= POLLING_INTERVAL:
                    await poll_game_updates(player_id)

                    # Process pending updates
                    for update in state.get("pending_updates", []):
                        if update["type"] == "new_day":
                            day = update["day"]
                            # Could send notification to player here
                            logger.info(
                                f"New game day available for player {player_id}: Day {day['day']}"
                            )

                    # Clear processed updates
                    state["pending_updates"] = []

            await asyncio.sleep(POLLING_INTERVAL)

        except Exception as e:
            logger.error(f"Error in polling loop: {e}")
            await asyncio.sleep(60)  # Wait before retrying


# ============== Main Entry Point ==============


async def main():
    """Main entry point"""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    # Configure SQLite storage for FSM state persistence
    db_path = os.getenv("AI_FSM_DB", "/app/fsm_storage.db")
    storage = SQLStorage(db_path=db_path, serializing_method="json")

    # Create aiohttp session with Socks5 proxy for Telegram API
    bot_session = create_bot_session()

    # Initialize bot and dispatcher with SQLite storage and proxy session
    bot = Bot(token=BOT_TOKEN, session=bot_session)
    dp = Dispatcher(storage=storage)

    # Register handlers
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_profile, Command("profile"))
    dp.message.register(cmd_today, Command("today"))
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(handle_voice_message, F.content_type == types.ContentType.VOICE)

    # Onboarding answer handler - must be registered BEFORE general text handlers
    dp.message.register(onboarding_answer, OnboardingState.waiting_for_answer)

    # General text message handler (catch-all for non-command messages)
    dp.message.register(handle_text_message, F.text & ~F.command)

    # Callback query handlers
    dp.callback_query.register(action_selection, F.data.startswith("action:"))
    dp.callback_query.register(refresh_game, F.data.startswith("refresh_game:"))

    logger.info("Starting Telegram Bot")

    # Start polling loop in background
    polling_task = asyncio.create_task(polling_loop(bot))

    # Start bot polling
    await dp.start_polling(bot)

    # Clean up
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
