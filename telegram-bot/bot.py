"""
Telegram Bot for AI Game Master - New Architecture

Key Features:
1. Onboarding via API - Questions fetched from game-server-api
2. Multiple Games Support - Track which game each player participates in
3. Polling Mechanism - Periodic polling for updates from API
4. Enhanced Game Flow - Better state management and inline keyboards
5. Avatar Display - Show generated avatars in profiles

Architecture:
- Uses aiogram with FSM for state management
- Maintains existing language support (Russian/English)
- Uses existing language.py for messages
- Proper error handling and logging
- Async HTTP calls to game-server-api
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any

import aiohttp
import language as lang
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    ReactionTypeEmoji,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram_sqlite_storage.sqlitestore import SQLStorage
from aiohttp_socks import ProxyConnector
from player_store import (
    get_all_player_ids,
    get_player_state,
    update_player_state,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram parse_mode='Markdown' (legacy).

    Escapes: _ * ` [ ] ( ) ~ > # + - = | { } . !
    These are treated as format entities by Telegram MarkdownV2.
    For legacy Markdown (parse_mode='Markdown'), the dangerous chars are: _ * ` [
    We escape all of them to be safe across both modes.
    """
    special_chars = r"_*`["
    return re.sub(f"([{re.escape(special_chars)}])", r"\\\1", text)


# ============== Configuration ==============

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GAME_MASTER_API_URL = os.getenv("GAME_MASTER_API_URL", "http://game-server-api:8000")
BOT_LANGUAGE = os.getenv("BOT_LANGUAGE", "ru")
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "30"))  # seconds between polls
BOT_USERNAME: str | None = None

# Game Master Telegram user ID — only this user can send GM commands
GAME_MASTER_ID = int(os.getenv("TELEGRAM_BOT_GAME_MASTER_ID", "0"))

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


class GameSelectionState(StatesGroup):
    """State machine for game selection before onboarding"""

    waiting_for_game_selection = State()


# ============== Player State Storage ==============

# Persistent SQLite-backed player state storage.
# Replaced the old in-memory dict. See player_store.py for implementation.
# Exposes the same get_player_state / update_player_state API.
# Survives bot restarts so the polling loop and onboarding
# flow can resume where they left off.


# ============== Helper Functions ==============


def parse_proxy_url(proxy_url: str) -> tuple[str, int, str | None, str | None]:
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


async def create_aiohttp_session(
    proxy_url: str | None = None,
) -> aiohttp.ClientSession:
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
    data: dict | None = None,
    params: dict | None = None,
    timeout_total: int = 600,
    ignore_codes: tuple = (),
) -> dict | None:
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


def create_bot_session(proxy_url: str | None = None):
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


async def send_image_from_api_url(
    bot_or_message: types.Message,
    image_url: str,
    caption: str = "",
    reply_markup=None,
) -> bool:
    """Fetch an image from a URL (from game-server-api) and send as photo.

    Returns True if sent successfully, False otherwise.
    """
    if not image_url:
        return False
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(
                image_url,
                timeout=aiohttp.ClientTimeout(total=30),
            )
            if resp.status == 200:
                photo_data = await resp.read()
                photo = BufferedInputFile(photo_data, filename="image.png")
                if caption:
                    await bot_or_message.answer_photo(
                        photo=photo,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_markup=reply_markup,
                    )
                else:
                    await bot_or_message.answer_photo(photo=photo)
                return True
            else:
                logger.warning(f"Failed to download image: {resp.status}")
    except Exception as e:
        logger.warning(f"Failed to send image from URL: {e}")
    return False


async def send_random_loading_image(
    message: types.Message, caption_key: str = "loading_caption"
) -> bool:
    """Fetch and send a random loading image from the API with a caption.

    Args:
        message: Telegram message context
        caption_key: Key in IMAGES dict for the caption text (default: "loading_caption")

    Returns True if sent, False otherwise.
    """
    try:
        result = await api_request("GET", "/content/loading-image")
        image_url = result.get("image_url") if result else None
        if image_url:
            caption = lang.get_images(BOT_LANGUAGE)[caption_key]
            return await send_image_from_api_url(message, image_url, caption=caption)
    except Exception as e:
        logger.warning(f"Failed to get/send loading image: {e}")
    return False


async def send_random_splash_image(
    message: types.Message, caption: str = "", reply_markup=None
) -> bool:
    """Fetch and send a random splash image from the API with optional caption.

    Args:
        message: Telegram message context
        caption: Caption text (e.g., game description) to include with the image
        reply_markup: Optional keyboard to show with the image

    Returns True if sent, False otherwise.
    """
    try:
        result = await api_request("GET", "/content/splash-image")
        image_url = result.get("image_url") if result else None
        if image_url:
            return await send_image_from_api_url(
                message, image_url, caption=caption, reply_markup=reply_markup
            )
    except Exception as e:
        logger.warning(f"Failed to get/send splash image: {e}")
    return False


async def send_question_with_image(
    bot_or_message: types.Message,
    question: dict,
    keyboard: InlineKeyboardMarkup,
    language: str = BOT_LANGUAGE,
) -> str:
    """Send a question to the player, optionally with an image.

    If the question has an image_url, sends it as a photo with caption.
    Otherwise sends plain text.
    Returns the question text that was displayed.
    """
    image_url = question.get("image_url")
    options = question.get("options", [])
    # Separate each option with a blank line for readability
    options_text = "\n\n".join(
        [f"{i + 1}. {opt['label']}" for i, opt in enumerate(options)]
    )
    question_text = lang.get_onboarding(language)["question_prefix"].format(
        id=question["id"], text=question["text"]
    )
    if options_text:
        question_text += f"\n\n---\n\n{options_text}"

    if image_url:
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.get(
                    image_url,
                    timeout=aiohttp.ClientTimeout(total=30),
                )
                if resp.status == 200:
                    photo_data = await resp.read()
                    photo = BufferedInputFile(
                        photo_data, filename=f"q_{question['id']}.png"
                    )
                    await bot_or_message.answer_photo(
                        photo=photo,
                        caption=question_text,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                    return question_text
                else:
                    logger.warning(f"Failed to download question image: {resp.status}")
        except Exception as e:
            logger.warning(f"Failed to send question image: {e}")

    # Check for option-level images (species/gender questions)
    has_option_images = any(opt.get("image_url") for opt in options)

    if has_option_images:
        # Download all option images and send as a media group
        media_group = []
        for i, opt in enumerate(options):
            opt_url = opt.get("image_url")
            if not opt_url:
                continue
            try:
                async with aiohttp.ClientSession() as session:
                    resp = await session.get(
                        opt_url,
                        timeout=aiohttp.ClientTimeout(total=30),
                    )
                    if resp.status == 200:
                        photo_data = await resp.read()
                        photo = BufferedInputFile(
                            photo_data, filename=f"opt_{question['id']}_{i}.png"
                        )
                        caption = f"{i + 1}. {opt['label']}"
                        media_group.append(
                            InputMediaPhoto(
                                media=photo,
                                caption=caption,
                                parse_mode="Markdown",
                            )
                        )
                    else:
                        logger.warning(
                            f"Failed to download option image {i}: {resp.status}"
                        )
            except Exception as e:
                logger.warning(f"Failed to download option image {i}: {e}")

        if media_group:
            try:
                await bot_or_message.answer_media_group(media=media_group)
            except Exception as e:
                logger.warning(f"Failed to send option media group: {e}")

        # Send question text + inline keyboard as separate message
        await bot_or_message.answer(
            question_text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return question_text

    # No image or download failed: send text only
    await bot_or_message.answer(
        question_text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return question_text


async def fetch_onboarding_questions() -> list:
    """Fetch onboarding questions from API"""
    try:
        result = await api_request("GET", "/onboarding/questions")
        if result is None:
            return []
        return result.get("questions", [])
    except Exception as e:
        logger.error(f"Failed to fetch onboarding questions: {e}")
        raise


async def check_player_game_status(player_id: int) -> dict[str, Any] | None:
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
        if result is None:
            return

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

        # Update last poll time and persist to DB
        state["last_poll"] = datetime.now()
        update_player_state(
            player_id,
            pending_updates=state["pending_updates"],
            last_poll=state["last_poll"],
        )

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
        if result is None:
            logger.error(
                f"Onboarding completion returned no result for player {player_id}"
            )
            return
        avatar_url = result.get("avatar_url")
        profile = result.get("profile", {})
        game_started = result.get("game_started", False)
        game_just_started = result.get("game_just_started", False)
        other_player_ids = result.get("other_player_ids", [])

        onboarding_msgs = lang.get_onboarding(BOT_LANGUAGE)

        # Format species/gender with hybrid display
        species_primary = profile.get("species", "Unknown") or "Unknown"
        species_secondary = profile.get("species_secondary")
        gender_primary = profile.get("gender", "Unknown") or "Unknown"
        gender_secondary = profile.get("gender_secondary")

        if species_secondary:
            species_display = (
                f"Гибрид: {species_primary} + {species_secondary}"
                if BOT_LANGUAGE == "ru"
                else f"Hybrid: {species_primary} + {species_secondary}"
            )
        else:
            species_display = species_primary

        if gender_secondary:
            gender_display = (
                f"Гибрид: {gender_primary} + {gender_secondary}"
                if BOT_LANGUAGE == "ru"
                else f"Hybrid: {gender_primary} + {gender_secondary}"
            )
        else:
            gender_display = gender_primary

        # Build the onboarding message text
        onboarding_text = onboarding_msgs["onboarding_complete"].format(
            role=escape_markdown(profile.get("role", "Crew Member")),
            role_description=escape_markdown(profile.get("role_description", "")),
            species=escape_markdown(species_display),
            gender=escape_markdown(gender_display),
            traits=escape_markdown("\n- ".join(profile.get("personality_traits", []))),
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
                resp = await session.get(
                    avatar_url,
                    timeout=aiohttp.ClientTimeout(total=60),
                )
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

        # Send invite link if bot username and game ID are available
        if BOT_USERNAME:
            game_id = profile.get("game_id", "")
            if game_id:
                invite_url = f"https://t.me/{BOT_USERNAME}?start=game={game_id}"
                # Escape the URL for Markdown to handle underscores in bot username
                invite_text = (
                    onboarding_msgs["invite_title"]
                    + "\n\n"
                    + onboarding_msgs["invite_message"].format(
                        invite_url=escape_markdown(invite_url)
                    )
                )

                invite_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=onboarding_msgs["invite_button"],
                                url=invite_url,  # Real URL (no escaping needed for button)
                            )
                        ]
                    ]
                )

                try:
                    await bot.send_message(
                        chat_id=player_id,
                        text=invite_text,
                        parse_mode="Markdown",
                        reply_markup=invite_keyboard,
                    )
                    logger.info(f"Sent invite link to player {player_id}: {invite_url}")
                except Exception as e:
                    logger.warning(f"Failed to send invite to player {player_id}: {e}")

        # If game just started (this player made it >= 3), notify all players
        try:
            if game_just_started:
                await _broadcast_game_started(player_id, profile, other_player_ids, bot)
            elif game_started and other_player_ids:
                # Game already started, but notify other players about new member
                await _broadcast_new_player(player_id, profile, other_player_ids, bot)
        except Exception as e:
            logger.warning(f"Failed to broadcast for player {player_id}: {e}")

    except Exception as e:
        logger.error(f"Avatar generation/sending failed for player {player_id}: {e}")
        try:
            onboarding_msgs = lang.get_onboarding(BOT_LANGUAGE)
            # Try to get profile info for fallback message
            try:
                profile = await api_request("GET", f"/players/{player_id}/profile")
                if profile is None:
                    profile = {}
                text = onboarding_msgs["onboarding_complete"].format(
                    role=escape_markdown(profile.get("role", "Crew Member")),
                    role_description=escape_markdown(
                        profile.get("role_description", "")
                    ),
                    species=escape_markdown(profile.get("species", "Unknown")),
                    gender=escape_markdown(profile.get("gender", "Unknown")),
                    traits=escape_markdown(
                        "\n- ".join(profile.get("personality_traits", []))
                    ),
                )
            except Exception:
                text = onboarding_msgs["onboarding_complete"].format(
                    role="Crew Member",
                    role_description="",
                    species="Unknown",
                    gender="Unknown",
                    traits="Unknown",
                )
            try:
                await bot.send_message(
                    chat_id=player_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=create_main_menu_keyboard(),
                )
            except Exception:
                # Fallback: send without Markdown if parsing fails
                plain_text = re.sub(r"[*_\[\]()`]", "", text)
                await bot.send_message(
                    chat_id=player_id,
                    text=plain_text,
                    reply_markup=create_main_menu_keyboard(),
                )
        except Exception:
            pass


async def _broadcast_new_player(
    new_player_id: int, profile: dict, other_player_ids: list, bot: Bot
):
    """Notify existing players about a new crew member joining."""
    try:
        onboarding_msgs = lang.get_onboarding(BOT_LANGUAGE)
        player_name = str(new_player_id)  # Use player ID as name for now
        notify_text = onboarding_msgs["new_player_joined"].format(
            player_name=player_name,
            role=escape_markdown(profile.get("role", "Crew Member")),
            role_description=escape_markdown(profile.get("role_description", "")),
        )

        for other_id in other_player_ids:
            try:
                await bot.send_message(
                    chat_id=other_id,
                    text=notify_text,
                    parse_mode="Markdown",
                )
                avatar_url = profile.get("avatar_url")
                await _send_avatar_to_player(
                    bot, other_id, avatar_url, player_name, profile
                )
            except Exception as e:
                logger.warning(f"Failed to notify player {other_id}: {e}")
    except Exception as e:
        logger.error(f"Broadcast new player failed: {e}")


async def _send_avatar_to_player(
    bot: Bot, chat_id: int, avatar_url: str | None, player_name: str, profile: dict
):
    """Fetch a player's avatar from its URL and send it as a photo to the given chat."""
    if not avatar_url:
        return
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(
                avatar_url,
                timeout=aiohttp.ClientTimeout(total=60),
            )
            if resp.status == 200:
                photo_data = await resp.read()
                photo = BufferedInputFile(photo_data, filename="avatar.png")
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=f"👆 {player_name} — {profile.get('role', '')}",
                )
    except Exception as e:
        logger.warning(f"Failed to send avatar to {chat_id}: {e}")


async def _broadcast_game_started(
    new_player_id: int, profile: dict, other_player_ids: list, bot: Bot
):
    """Notify all players that the game has started (the new player triggered >= 3 players)."""
    try:
        onboarding_msgs = lang.get_onboarding(BOT_LANGUAGE)
        player_name = str(new_player_id)

        # Notify existing players that the game is now starting
        # Note: new player already received game_already_started in their onboarding message
        for other_id in other_player_ids:
            try:
                await bot.send_message(
                    chat_id=other_id,
                    text=onboarding_msgs["game_starting_broadcast"].format(
                        player_name=player_name,
                        role=escape_markdown(profile.get("role", "")),
                        role_description=escape_markdown(
                            profile.get("role_description", "")
                        ),
                    ),
                    parse_mode="Markdown",
                )
                avatar_url = profile.get("avatar_url")
                await _send_avatar_to_player(
                    bot, other_id, avatar_url, player_name, profile
                )
            except Exception as e:
                logger.warning(
                    f"Failed to notify player {other_id} about game start: {e}"
                )
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


def create_onboarding_keyboard(options: list, question_id: int) -> InlineKeyboardMarkup:
    """Create inline keyboard for onboarding options.

    Buttons show numbers [1] [2] [3] etc. attached to the message
    itself — unlike ReplyKeyboardMarkup, these CANNOT be dismissed
    by the user, ensuring they always have a way to answer.
    """
    builder = InlineKeyboardBuilder()
    for idx in range(len(options)):
        builder.add(
            InlineKeyboardButton(
                text=f"[{idx + 1}]",
                callback_data=f"onb_ans:{question_id}:{idx}",
            )
        )
    builder.adjust(len(options))  # one row, Telegram wraps if too wide

    logger.info(
        f"Created onboarding inline keyboard for question_id={question_id} with {len(options)} buttons"
    )

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
        resize_keyboard=True,
    )


def create_action_keyboard(actions: list) -> InlineKeyboardMarkup:
    """Create inline keyboard for game actions

    Buttons show numbers [1] [2] [3] etc. instead of full action text.
    Full action text is displayed in the message as a numbered list.
    """
    builder = InlineKeyboardBuilder()
    for idx, action in enumerate(actions, start=1):
        builder.add(
            InlineKeyboardButton(
                text=f"[{idx}]", callback_data=f"action:{action['id']}"
            )
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


def parse_game_id_from_start_command(text: str) -> str | None:
    """Extract game_id from `/start game=...` command payload."""
    if not text:
        return None

    text_parts = text.split()
    if len(text_parts) <= 1:
        return None

    for part in text_parts[1:]:
        if part.startswith("game="):
            game_id = part[5:].strip()
            return game_id or None

    return None


async def create_new_game(player_id: int) -> str:
    """Create a new game and return its game_id."""
    result = await api_request(
        "POST",
        "/admin/create-game",
        data={"name": f"Game by {player_id}"},
    )
    game_id = result.get("game_id") if result else None
    if not game_id:
        raise Exception("No game_id returned from /admin/create-game")
    return game_id


async def show_game_selection(message: types.Message, state: FSMContext):
    """Show available games or option to create a new one."""
    msgs = lang.get_onboarding(BOT_LANGUAGE)

    try:
        result = await api_request("GET", "/admin/list-games")
        games = result.get("games", []) if result else []

        keyboard = []
        for game in games:
            game_id = game.get("game_id")
            if not game_id:
                continue

            name = game.get("title") or game.get("name") or game_id
            player_count = game.get("player_count", 0)
            started = "🚀" if game.get("started") else "⏳"
            btn_text = f"{started} {name} ({player_count} players)"
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=btn_text,
                        callback_data=f"select_game:{game_id}",
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=msgs["new_game"],
                    callback_data="select_game:new",
                )
            ]
        )

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        await message.answer(
            msgs["select_game"],
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
        await state.set_state(GameSelectionState.waiting_for_game_selection)

    except Exception as e:
        logger.error(f"Failed to fetch games list: {e}")
        error_msgs = lang.get_errors(BOT_LANGUAGE)
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def start_onboarding_flow(
    message: types.Message,
    state: FSMContext,
    player_id: int,
    game_id: str,
):
    """Start onboarding flow with a specific game_id."""
    msgs = lang.get_onboarding(BOT_LANGUAGE)

    try:
        logger.info(
            f"Starting onboarding for player_id={player_id}, game_id={game_id}, language={BOT_LANGUAGE}"
        )
        result = await api_request(
            "POST",
            "/onboarding/start",
            data={
                "player_id": player_id,
                "game_id": game_id,
                "language": BOT_LANGUAGE,
            },
            timeout_total=600,
        )
        logger.info(f"Onboarding start response: {result}")
        if result is None:
            raise Exception("No response from API when starting onboarding")

        welcome_text = result.get("welcome_message") or msgs["welcome"]
        game_title = result.get("game_title", "")
        if game_title:
            welcome_text = (
                f"**{game_title}**\n\n{welcome_text}"
                if welcome_text
                else f"**{game_title}**"
            )

        # Send splash image with game description as caption
        # NOTE: No main menu keyboard during onboarding — buttons show only after completion
        splash_sent = await send_random_splash_image(message, welcome_text)
        if not splash_sent:
            # Fallback: send text-only if no splash image available
            await message.answer(
                welcome_text,
                parse_mode="Markdown",
            )

        session_id = result.get("session_id")
        resolved_game_id = result.get("game_id", game_id)

        if not session_id:
            raise Exception("No session ID returned from API")

        question = result.get("question")
        if not question:
            raise Exception("No question returned from API")

        await state.update_data(
            session_id=session_id,
            game_id=resolved_game_id,
            current_question_id=question["id"],
            current_options=question["options"],
        )
        update_player_state(
            player_id,
            onboarding_session_id=session_id,
            game_id=resolved_game_id,
            current_question_id=question["id"],
            current_options=question["options"],
        )

        logger.info(
            f"First onboarding question: id={question['id']}, text={question['text']}..."
        )
        logger.info(
            f"Question options: {[opt['label'] for opt in question['options']]}"
        )
        if question.get("image_url"):
            logger.info(f"Question has image: {question['image_url']}")

        keyboard = create_onboarding_keyboard(question["options"], question["id"])
        await send_question_with_image(message, question, keyboard, BOT_LANGUAGE)
        await state.set_state(OnboardingState.waiting_for_answer)

    except Exception as e:
        logger.error(
            f"Failed to start onboarding for player {player_id}: {type(e).__name__} - {str(e)}"
        )
        error_msgs = lang.get_errors(BOT_LANGUAGE)
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def game_selection_callback(callback: types.CallbackQuery, state: FSMContext):
    """Handle game selection callback and continue onboarding."""
    await callback.answer()

    data = callback.data or ""
    if not data.startswith("select_game:"):
        return

    player_id = callback.from_user.id
    game_id_or_new = data.split(":", 1)[1]
    message = callback.message

    if not isinstance(message, types.Message):
        logger.warning(
            f"Callback message not accessible for player {player_id}, data={data}"
        )
        return

    try:
        if game_id_or_new == "new":
            game_id = await create_new_game(player_id)
        else:
            game_id = game_id_or_new

        if not game_id:
            raise Exception("No game_id selected")

        # Remove selection keyboard to avoid duplicate taps
        from contextlib import suppress

        with suppress(Exception):
            await message.edit_reply_markup(reply_markup=None)

        await send_random_loading_image(message)
        await start_onboarding_flow(message, state, player_id, game_id)

    except Exception as e:
        logger.error(f"Failed to process game selection for player {player_id}: {e}")
        error_msgs = lang.get_errors(BOT_LANGUAGE)
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def cmd_start(message: types.Message, state: FSMContext):
    """Handle /start command - Begin onboarding or join existing game"""
    assert message.from_user is not None
    player_id = message.from_user.id

    msgs = lang.get_onboarding(BOT_LANGUAGE)

    # Check if player already has an active onboarding session in memory
    player_state = get_player_state(player_id)
    if player_state.get("onboarding_session_id"):
        logger.info(
            f"Player {player_id} already has active onboarding session: {player_state['onboarding_session_id']}"
        )
        current_options = player_state.get("current_options", [])
        if current_options:
            keyboard = create_onboarding_keyboard(
                current_options, player_state.get("current_question_id", 1)
            )
        else:
            # No options stored — remove any reply keyboard
            keyboard = types.ReplyKeyboardRemove()
        await message.answer(
            msgs["already_in_onboarding"],
            reply_markup=keyboard,
        )
        return

    game_id = parse_game_id_from_start_command(message.text or "")
    if game_id:
        logger.info(f"Player {player_id} started with game_id={game_id} from deep link")

    # Check if player already has a profile
    try:
        profile = await check_player_game_status(player_id)

        if profile:
            # Check if player is dead (spectator)
            if profile.get("is_dead") or profile.get("is_spectator"):
                spectator_msgs = lang.get_spectator(BOT_LANGUAGE)
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔄 Начать заново / Start Over",
                                callback_data="select_game:new",
                            )
                        ],
                    ]
                )
                await message.answer(
                    spectator_msgs["still_watching"],
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
                return

            # Player already has a profile - welcome back
            await send_random_loading_image(message)
            await message.answer(
                msgs["welcome_back"].format(
                    role=profile["role"],
                    role_description=profile["role_description"],
                    traits=", ".join(profile["personality_traits"]),
                ),
                reply_markup=create_main_menu_keyboard(),
            )

            # Send invite link for existing player
            if BOT_USERNAME:
                game_id = profile.get("game_id", "")
                if game_id:
                    invite_url = f"https://t.me/{BOT_USERNAME}?start=game={game_id}"
                    invite_text = (
                        msgs.get("invite_title", "")
                        + "\n\n"
                        + msgs.get("invite_message", "").format(
                            invite_url=escape_markdown(invite_url)
                        )
                    )
                    try:
                        await message.answer(invite_text, parse_mode="Markdown")
                    except Exception as e:
                        logger.warning(
                            f"Failed to send invite to player {player_id}: {e}"
                        )

            # Update player state with game info
            update_player_state(
                player_id, game_id=profile.get("game_id", "default_game")
            )

        else:
            if not game_id:
                await show_game_selection(message, state)
                return

            await send_random_loading_image(message)
            await start_onboarding_flow(message, state, player_id, game_id)

    except Exception as e:
        logger.error(f"Error in /start command for player {player_id}: {e}")
        error_msgs = lang.get_errors(BOT_LANGUAGE)
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def cmd_profile(message: types.Message):
    """Show player profile with avatar"""
    assert message.from_user is not None
    player_id = message.from_user.id

    try:
        profile = await api_request("GET", f"/players/{player_id}/profile")
        if profile is None:
            msgs = lang.get_profile(BOT_LANGUAGE)
            await message.answer(msgs["no_profile"])
            return
        msgs = lang.get_profile(BOT_LANGUAGE)

        # Build profile message with hybrid display support
        species_primary = profile.get("species", "Unknown") or "Unknown"
        species_secondary = profile.get("species_secondary")
        gender_primary = profile.get("gender", "Unknown") or "Unknown"
        gender_secondary = profile.get("gender_secondary")

        if species_secondary:
            species_display = (
                f"Гибрид: {species_primary} + {species_secondary}"
                if BOT_LANGUAGE == "ru"
                else f"Hybrid: {species_primary} + {species_secondary}"
            )
        else:
            species_display = species_primary

        if gender_secondary:
            gender_display = (
                f"Гибрид: {gender_primary} + {gender_secondary}"
                if BOT_LANGUAGE == "ru"
                else f"Hybrid: {gender_primary} + {gender_secondary}"
            )
        else:
            gender_display = gender_primary

        profile_text = f"{msgs['title']}\n\n"
        profile_text += f"{msgs['role'].format(role=profile['role'])}\n"
        profile_text += f"{msgs['species'].format(species=species_display)}\n"
        profile_text += f"{msgs['gender'].format(gender=gender_display)}\n\n"
        profile_text += f"{msgs['description'].format(role_description=profile['role_description'])}\n\n"
        profile_text += f"{msgs['traits'].format(traits='\n- '.join(profile['personality_traits']))}"

        # Send avatar as photo if available, otherwise fall back to text
        avatar_url = profile.get("avatar_url")
        avatar_description = profile.get("avatar_description")

        if avatar_url:
            logger.info(
                f"Fetching avatar for profile of player {player_id}: {avatar_url}"
            )
            try:
                async with aiohttp.ClientSession() as session:
                    resp = await session.get(
                        avatar_url,
                        timeout=aiohttp.ClientTimeout(total=60),
                    )
                    if resp.status == 200:
                        photo_data = await resp.read()
                        photo = BufferedInputFile(photo_data, filename="avatar.png")
                        if message.bot:
                            await message.bot.send_photo(
                                chat_id=player_id,
                                photo=photo,
                                caption=profile_text,
                                parse_mode="Markdown",
                            )
                        return
                    else:
                        logger.warning(
                            f"Failed to download avatar for profile: {resp.status}"
                        )
            except Exception as avatar_err:
                logger.warning(f"Error downloading avatar for profile: {avatar_err}")

            # Fallback: avatar URL exists but download failed — show description if available
            if avatar_description:
                profile_text += "\n\n" + msgs["visualization"].format(
                    avatar=avatar_description
                )
        elif avatar_description:
            profile_text += "\n\n" + msgs["visualization"].format(
                avatar=avatar_description
            )

        await message.answer(profile_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Failed to get profile for player {player_id}: {e}")
        msgs = lang.get_profile(BOT_LANGUAGE)
        await message.answer(msgs["no_profile"])


async def cmd_today(message: types.Message):
    """Show current day's game episode"""
    assert message.from_user is not None
    player_id = message.from_user.id

    try:
        msgs = lang.get_current_day(BOT_LANGUAGE)

        # First try to get personal briefing (new system)
        briefing = None
        try:
            state = await api_request("GET", "/game/state")
            current_day_num = state.get("day", 1) if state else 1
            briefing = await api_request(
                "GET",
                f"/game/briefing/{player_id}/{current_day_num}",
                ignore_codes=(404,),
            )
        except Exception:
            pass

        if briefing and briefing.get("choices"):
            # New system: show personal briefing
            choices = briefing.get("choices", [])
            keyboard = create_action_keyboard(choices) if choices else None

            actions_text = "\n\n".join(
                [f"{i + 1} - {a['text']}" for i, a in enumerate(choices)]
            )

            await message.answer(
                msgs["title"].format(day=briefing["day"])
                + "\n\n"
                + msgs["briefing_header"].format(briefing=briefing["briefing"])
                + "\n\n"
                + msgs["actions"].format(actions=actions_text)
                + "\n\n"
                + msgs["select_action"],
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        else:
            # Legacy system: show global story
            day = await api_request("GET", "/game/current-day")
            if day is None:
                await message.answer(lang.get_errors(BOT_LANGUAGE)["api_error"])
                return

            # Create action keyboard if there are actions to select
            keyboard = None
            if day.get("player_actions"):
                keyboard = create_action_keyboard(day["player_actions"])

            # Build actions text
            actions_text = "\n\n".join(
                [
                    f"{i + 1} - {a['text']}"
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


async def cmd_bridge(message: types.Message):
    """Show the current bridge image and mission info."""
    assert message.from_user is not None
    player_id = message.from_user.id

    try:
        # Get mission info
        from contextlib import suppress

        mission = None
        with suppress(Exception):
            mission = await api_request("GET", "/game/mission", ignore_codes=(404,))

        # Get bridge image
        bridge = None
        with suppress(Exception):
            bridge = await api_request("GET", "/game/bridge-image", ignore_codes=(404,))

        bridge_msgs = lang.get_bridge(BOT_LANGUAGE)

        if bridge and bridge.get("image_url"):
            caption = bridge_msgs["title"]
            if mission:
                caption += "\n\n" + bridge_msgs["mission_header"].format(
                    name=mission.get("name", "")
                )
                caption += "\n\n" + bridge_msgs["mission_desc"].format(
                    description=mission.get("description", "")
                )
            await send_image_from_api_url(message, bridge["image_url"], caption=caption)
        else:
            await message.answer(bridge_msgs["error"])
    except Exception as e:
        logger.error(f"Failed to get bridge image for player {player_id}: {e}")
        await message.answer(str(e))


async def cmd_help(message: types.Message):
    """Show help information"""
    assert message.from_user is not None
    player_id = message.from_user.id

    msgs = lang.get_help(BOT_LANGUAGE)

    # Fetch game title dynamically from API
    game_title = "🎮 Game"
    try:
        title_data = await api_request(
            "GET", "/game/title", params={"game_id": "default_game"}
        )
        if title_data and title_data.get("title"):
            game_title = f"🎮 {title_data['title']}"
    except Exception as e:
        logger.warning(f"Failed to fetch game title for help: {e}")
        # Fallback to generic title
        game_title = "🎮 Space Exploration Game"

    help_title = f"**{game_title} — Help**"

    # Only show GM commands if the requesting user is the configured Game Master
    is_gm = GAME_MASTER_ID > 0 and player_id == GAME_MASTER_ID

    if is_gm:
        # Include GM commands in help text
        help_text = f"{help_title}\n\n{msgs['commands']}\n\n{msgs['how_to_play']}"
    else:
        # Show only regular commands (no /gm_* commands)
        if BOT_LANGUAGE == "ru":
            commands_only = (
                "**Команды:**\n"
                "/start - Начать или продолжить игру\n"
                "/profile - Показать ваш профиль\n"
                "/today - Текущий ход игры\n"
                "/bridge - Картинка рубки и миссия\n"
                "/help - Эта справка"
            )
        else:
            commands_only = (
                "**Commands:**\n"
                "/start - Start or continue the game\n"
                "/profile - Show your profile\n"
                "/today - Current game turn\n"
                "/bridge - Bridge image and mission\n"
                "/help - This help"
            )
        help_text = f"{help_title}\n\n{commands_only}\n\n{msgs['how_to_play']}"

    await message.answer(
        help_text,
        parse_mode="Markdown",
    )


async def cmd_gm_start_game(message: types.Message):
    """GM command: Force start a game by ID.
    Usage: /gm_start_game <game_id>
    Only executable by the configured Game Master user.
    """
    assert message.from_user is not None
    player_id = message.from_user.id
    gm_msgs = lang.get_gm_commands(BOT_LANGUAGE)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        logger.warning(f"Unauthorized /gm_start_game attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(gm_msgs["start_game_usage"])
        return

    game_id = parts[1].strip()
    if not game_id:
        await message.answer(gm_msgs["start_game_usage"])
        return

    await message.answer(
        gm_msgs["starting_game"].format(game_id=game_id), parse_mode="Markdown"
    )

    try:
        result = await api_request(
            "POST",
            "/admin/start-game",
            data={"game_id": game_id, "language": BOT_LANGUAGE},
            timeout_total=600,
        )
        if result and result.get("status") == "success":
            day_num = result.get("day", 1)
            player_count = result.get("player_count", 0)
            npc_count = result.get("npc_count", 0)
            msg = gm_msgs["game_started"].format(
                game_id=game_id,
                day_num=day_num,
                player_count=player_count,
                npc_count=npc_count,
            )
            if result.get("briefings"):
                msg += gm_msgs["briefings_sent"]
            await message.answer(msg, parse_mode="Markdown")
        else:
            await message.answer(gm_msgs["start_game_error"].format(error=result))
    except Exception as e:
        logger.error(f"Failed to start game {game_id}: {e}")
        await message.answer(gm_msgs["start_game_error"].format(error=e))


async def cmd_gm_kick(message: types.Message):
    """GM command: Kick a player by role and replace with NPC.

    Usage: /gm_kick <game_id> <role_key> [reason]
    The kicked player receives a notification about being removed.
    Only executable by the configured Game Master user.
    """
    assert message.from_user is not None
    player_id = message.from_user.id
    gm_msgs = lang.get_gm_commands(BOT_LANGUAGE)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        logger.warning(f"Unauthorized /gm_kick attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    # Parse args: /gm_kick <game_id> <role_key> [reason]
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(gm_msgs["kick_usage"])
        return

    game_id = parts[1].strip()
    role_key = parts[2].strip()
    reason = parts[3].strip() if len(parts) > 3 else gm_msgs["default_kick_reason"]

    if not game_id or not role_key:
        await message.answer(gm_msgs["kick_usage"])
        return

    await message.answer(
        gm_msgs["kicking_player"].format(role_key=role_key, game_id=game_id),
        parse_mode="Markdown",
    )

    try:
        result = await api_request(
            "POST",
            "/admin/kick-player",
            data={"role_key": role_key, "reason": reason, "game_id": game_id},
            timeout_total=120,
        )
        if result and result.get("status") == "success":
            kicked_id = result.get("kicked_player_id")
            npc_name = result.get("npc_name", "NPC")
            msg = gm_msgs["player_kicked"].format(
                game_id=game_id,
                kicked_id=kicked_id,
                npc_name=npc_name,
                reason=reason,
            )
            await message.answer(msg, parse_mode="Markdown")
        else:
            error_detail = (
                result.get("detail", gm_msgs["unknown_error"])
                if result
                else gm_msgs["no_api_response"]
            )
            await message.answer(gm_msgs["kick_error"].format(error=error_detail))
    except Exception as e:
        logger.error(f"Failed to kick player: {e}")
        await message.answer(gm_msgs["kick_error"].format(error=e))


async def cmd_gm_list_games(message: types.Message):
    """GM command: List available games.

    Usage: /gm_list_games
    Only executable by the configured Game Master user.
    """
    assert message.from_user is not None
    player_id = message.from_user.id
    gm_msgs = lang.get_gm_commands(BOT_LANGUAGE)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        logger.warning(f"Unauthorized /gm_list_games attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    try:
        result = await api_request("GET", "/admin/list-games")
        games = result.get("games", []) if result else []

        if not games:
            await message.answer(gm_msgs["no_games"], parse_mode="Markdown")
            return

        lines = [gm_msgs["games_list_header"], ""]
        for idx, game in enumerate(games, start=1):
            game_id = game.get("game_id", "unknown")
            title = (
                game.get("title") or game.get("name") or gm_msgs["default_game_title"]
            )
            player_count = game.get("player_count", 0)
            status = game.get("status") or (
                "started" if game.get("started") else "waiting"
            )
            status_icon = "🚀" if status == "started" else "⏳"
            lines.append(
                gm_msgs["games_list_entry"].format(
                    idx=idx,
                    game_id=game_id,
                    title=title,
                    player_count=player_count,
                    status_icon=status_icon,
                    status=status,
                )
            )

        await message.answer("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to list games: {e}")
        await message.answer(gm_msgs["list_games_error"].format(error=e))


async def cmd_gm_continue_game(message: types.Message):
    """GM command: Generate the next turn for a game.

    Usage: /gm_continue_game <game_id>
    Only executable by the configured Game Master user.
    """
    assert message.from_user is not None
    player_id = message.from_user.id
    gm_msgs = lang.get_gm_commands(BOT_LANGUAGE)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        logger.warning(f"Unauthorized /gm_continue_game attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(gm_msgs["continue_game_usage"])
        return

    game_id = parts[1].strip()
    if not game_id:
        await message.answer(gm_msgs["continue_game_usage"])
        return

    await message.answer(
        gm_msgs["continuing_game"].format(game_id=game_id), parse_mode="Markdown"
    )

    try:
        result = await api_request(
            "POST",
            "/admin/continue-game",
            params={"game_id": game_id, "language": BOT_LANGUAGE},
            timeout_total=600,
        )
        if result and result.get("status") == "success":
            day_num = result.get("day", 1)
            players = result.get("players", 0)
            npcs = result.get("npcs", 0)
            total = result.get("total_participants", 0)
            msg = gm_msgs["game_continued"].format(
                day_num=day_num,
                players=players,
                npcs=npcs,
                total=total,
            )
            await message.answer(msg, parse_mode="Markdown")
        else:
            await message.answer(gm_msgs["continue_game_error"].format(error=result))
    except Exception as e:
        logger.error(f"Failed to continue game {game_id}: {e}")
        await message.answer(gm_msgs["continue_game_error"].format(error=e))


async def cmd_gm_regenerate_turn(message: types.Message):
    """GM command: Regenerate the current turn with state reset.

    Deletes the current day's data and regenerates it fresh.
    Usage: /gm_regenerate_turn <game_id>
    Only executable by the configured Game Master user.
    """
    assert message.from_user is not None
    player_id = message.from_user.id
    gm_msgs = lang.get_gm_commands(BOT_LANGUAGE)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        logger.warning(f"Unauthorized /gm_regenerate_turn attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(gm_msgs["regenerate_turn_usage"])
        return

    game_id = parts[1].strip()
    if not game_id:
        await message.answer(gm_msgs["regenerate_turn_usage"])
        return

    await message.answer(
        gm_msgs["regenerating_turn"].format(game_id=game_id),
        parse_mode="Markdown",
    )

    try:
        result = await api_request(
            "POST",
            "/admin/regenerate-turn",
            params={"game_id": game_id, "language": BOT_LANGUAGE},
            timeout_total=600,
        )
        if result and result.get("status") == "success":
            day_num = result.get("day", 1)
            players = result.get("players", 0)
            npcs = result.get("npcs", 0)
            msg = gm_msgs["turn_regenerated"].format(
                day_num=day_num,
                players=players,
                npcs=npcs,
            )
            await message.answer(msg, parse_mode="Markdown")
        else:
            await message.answer(gm_msgs["regenerate_turn_error"].format(error=result))
    except Exception as e:
        logger.error(f"Failed to regenerate turn for game {game_id}: {e}")
        await message.answer(gm_msgs["regenerate_turn_error"].format(error=e))


async def cmd_gm_restart_game(message: types.Message):
    """GM command: Reset game state and restart from turn 1.

    Shows a confirmation prompt first. Use /gm_restart_game_confirm to proceed.
    Usage: /gm_restart_game <game_id>
    Only executable by the configured Game Master user.
    """
    assert message.from_user is not None
    player_id = message.from_user.id
    gm_msgs = lang.get_gm_commands(BOT_LANGUAGE)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        logger.warning(f"Unauthorized /gm_restart_game attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(gm_msgs["restart_game_usage"])
        return

    game_id = parts[1].strip()
    if not game_id:
        await message.answer(gm_msgs["restart_game_usage"])
        return

    # Show confirmation prompt
    await message.answer(
        gm_msgs["confirm_restart"].format(game_id=game_id),
        parse_mode="Markdown",
    )


async def cmd_gm_restart_game_confirm(message: types.Message):
    """GM command: Confirm and execute game restart.

    Usage: /gm_restart_game_confirm <game_id>
    Only executable by the configured Game Master user.
    """
    assert message.from_user is not None
    player_id = message.from_user.id
    gm_msgs = lang.get_gm_commands(BOT_LANGUAGE)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        logger.warning(
            f"Unauthorized /gm_restart_game_confirm attempt by user {player_id}"
        )
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(gm_msgs["need_confirm"])
        return

    game_id = parts[1].strip()
    if not game_id:
        await message.answer(gm_msgs["restart_game_usage"])
        return

    await message.answer(
        gm_msgs["restarting_game"].format(game_id=game_id),
        parse_mode="Markdown",
    )

    try:
        result = await api_request(
            "POST",
            "/admin/restart-game",
            params={"game_id": game_id, "language": BOT_LANGUAGE},
            timeout_total=120,
        )
        if result and result.get("status") == "success":
            msg = gm_msgs["game_restarted"].format(
                game_id=game_id,
                deleted_days=result.get("deleted_days", 0),
                deleted_briefings=result.get("deleted_briefings", 0),
                deleted_actions=result.get("deleted_actions", 0),
                deleted_messages=result.get("deleted_messages", 0),
                deleted_mission=result.get("deleted_mission", False),
            )
            await message.answer(msg, parse_mode="Markdown")
        else:
            await message.answer(gm_msgs["restart_game_error"].format(error=result))
    except Exception as e:
        logger.error(f"Failed to restart game {game_id}: {e}")
        await message.answer(gm_msgs["restart_game_error"].format(error=e))


async def handle_voice_message(message: types.Message):
    """Handle voice messages"""
    assert message.from_user is not None
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
    assert message.from_user is not None
    player_id = message.from_user.id

    try:
        # Send message to Game Master API
        text_content = message.text
        if text_content is None:
            await message.answer(lang.get_messages(BOT_LANGUAGE)["text_received"])
            return

        response = await api_request(
            "POST",
            "/game/messages",
            data={
                "player_id": player_id,
                "message": text_content,
                "message_type": "text",
            },
        )

        msgs = lang.get_messages(BOT_LANGUAGE)

        # If there's a response from Game Master, show it
        if response and response.get("response"):
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


async def handle_onboarding_inline_answer(
    callback: types.CallbackQuery, state: FSMContext
):
    """Handle onboarding answer selection from inline keyboard buttons.

    Callback data format: onb_ans:<question_id>:<option_index>
    The option_index is used to look up the answer value from current_options in state.
    """
    await callback.answer()
    assert callback.data is not None
    assert callback.message is not None
    msg: types.Message = callback.message  # type: ignore[assignment]

    parts = callback.data.split(":")
    if len(parts) != 3:
        await msg.answer(lang.get_errors(BOT_LANGUAGE)["invalid_format"])
        return

    _, question_id_str, option_idx_str = parts
    option_idx = int(option_idx_str)
    player_id = callback.from_user.id
    error_msgs = lang.get_errors(BOT_LANGUAGE)

    # Get current question data from state
    state_data = await state.get_data()
    session_id = state_data.get("session_id")
    current_question_id = state_data.get("current_question_id")
    current_options = state_data.get("current_options")

    logger.info(
        f"Inline onboarding answer: player={player_id}, question_id={question_id_str}, option_idx={option_idx}, "
        f"session_id={session_id}"
    )

    if not session_id:
        logger.error(f"No session_id in state for player {player_id}")
        await msg.answer(error_msgs["session_not_found"])
        return

    if not current_options or option_idx < 0 or option_idx >= len(current_options):
        logger.error(
            f"Invalid option_idx {option_idx} for {len(current_options) if current_options else 0} options"
        )
        await msg.answer(error_msgs["invalid_format"])
        return

    answer_value = current_options[option_idx]["value"]
    matched_label = current_options[option_idx]["label"]
    logger.info(f"Matched option: idx={option_idx}, label='{matched_label}'")

    # Set reaction to show processing
    if callback.bot is not None:
        try:
            await callback.bot.set_message_reaction(
                chat_id=player_id,
                message_id=msg.message_id,
                reaction=[ReactionTypeEmoji(emoji="\U0001f440")],  # 👀 eyes
                is_big=False,
            )
        except Exception as reaction_err:
            logger.warning(
                f"Failed to set reaction for player {player_id}: {reaction_err}"
            )

    try:
        logger.info(
            f"Submitting onboarding answer (inline): session_id={session_id}, "
            f"question_id={current_question_id}, answer_value='{answer_value}'"
        )
        result = await api_request(
            "POST",
            f"/onboarding/{session_id}/answer",
            data={"question_id": current_question_id, "answer": answer_value},
            params={"language": BOT_LANGUAGE},
        )
        logger.info(f"Onboarding answer response: {result}")

        if result is None:
            raise Exception("No response from API when submitting onboarding answer")

        # Answer processed successfully — update reaction to checkmark
        if callback.bot is None:
            logger.warning(
                f"callback.bot is None for player {player_id}, cannot set reaction"
            )
        else:
            try:
                await callback.bot.set_message_reaction(
                    chat_id=player_id,
                    message_id=msg.message_id,
                    reaction=[ReactionTypeEmoji(emoji="\U0001f44d")],  # 👍 thumbs up
                    is_big=False,
                )
            except Exception as reaction_err:
                logger.warning(
                    f"Failed to update reaction for player {player_id}: {reaction_err}"
                )

        if result.get("completed"):
            profile = result.get("profile") or {}
            logger.info(
                f"Onboarding completed for player {player_id}: role={profile.get('role', 'Unknown')}"
            )

            try:
                verify_profile = await api_request(
                    "GET", f"/players/{player_id}/profile"
                )
                logger.info(
                    f"Profile verified for player {player_id}: {verify_profile.get('role') if verify_profile else 'Unknown'}"
                )
            except Exception as verify_error:
                logger.error(
                    f"Profile verification failed for player {player_id}: {verify_error}"
                )

            await state.clear()
            update_player_state(
                player_id,
                onboarding_session_id=None,
                current_question_id=None,
                current_options=None,
            )

            # Show loading image while profile/avatar is being generated
            await send_random_loading_image(msg, caption_key="processing_caption")

            # Avatar generation + onboarding message
            if msg.bot is None:
                logger.error(
                    f"message.bot is None for player {player_id}, cannot generate avatar"
                )
            else:
                asyncio.create_task(
                    _generate_and_send_avatar(player_id, session_id, msg.bot)
                )
        else:
            next_question = result.get("next_question")
            if next_question:
                logger.info(
                    f"Next onboarding question (inline): id={next_question['id']}, text={next_question['text']}..."
                )
                await state.update_data(
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                )
                update_player_state(
                    player_id,
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                )
                keyboard = create_onboarding_keyboard(
                    next_question["options"], next_question["id"]
                )
                await send_question_with_image(
                    msg, next_question, keyboard, BOT_LANGUAGE
                )

    except Exception as e:
        logger.error(f"Failed to submit onboarding answer (inline): {e}")
        await callback.message.answer(
            error_msgs["onboarding_error"].format(error=str(e))
        )


async def onboarding_answer(message: types.Message, state: FSMContext):
    """Handle onboarding answer selection from reply keyboard.

    Buttons show [1], [2], [3] etc. The number is extracted and used
    as an index into current_options to find the matching option value.
    """
    assert message.from_user is not None
    assert message.text is not None
    answer_text = message.text
    player_id = message.from_user.id
    error_msgs = lang.get_errors(BOT_LANGUAGE)

    logger.info(
        f"Onboarding answer handler called: player={player_id}, text='{answer_text}'"
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
        logger.error(
            f"No current_options in state for player {player_id}, state_data={state_data}"
        )
        await message.answer(error_msgs["invalid_format"])
        return

    # Match by numeric index from button text (e.g., "[1]" or "1")
    answer_value = None
    matched_label = None
    match = re.match(r"^\[?(\d+)\]?$", answer_text.strip())
    if match:
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(current_options):
            answer_value = current_options[idx]["value"]
            matched_label = current_options[idx]["label"]
            logger.info(f"Numeric match: idx={idx}, label='{matched_label}'")

    if not answer_value:
        logger.warning(
            f"No matching option found! Player text: '{answer_text}', "
            f"Available options: {[opt['label'] for opt in current_options]}"
        )
        await message.answer(error_msgs["invalid_format"])
        return

    # Set reaction to show processing
    if message.bot is not None:
        try:
            await message.bot.set_message_reaction(
                chat_id=player_id,
                message_id=message.message_id,
                reaction=[ReactionTypeEmoji(emoji="\U0001f440")],  # 👀 eyes
                is_big=False,
            )
        except Exception as reaction_err:
            logger.warning(
                f"Failed to set reaction for player {player_id}: {reaction_err}"
            )

    try:
        logger.info(
            f"Submitting onboarding answer: session_id={session_id}, question_id={current_question_id}, "
            f"matched_label='{matched_label}', answer_value='{answer_value}'"
        )
        result = await api_request(
            "POST",
            f"/onboarding/{session_id}/answer",
            data={"question_id": current_question_id, "answer": answer_value},
            params={"language": BOT_LANGUAGE},
        )
        logger.info(f"Onboarding answer response: {result}")

        if result is None:
            raise Exception("No response from API when submitting onboarding answer")

        # Answer processed successfully — update reaction to checkmark
        if message.bot is not None:
            try:
                await message.bot.set_message_reaction(
                    chat_id=player_id,
                    message_id=message.message_id,
                    reaction=[ReactionTypeEmoji(emoji="\U0001f44d")],  # 👍 thumbs up
                    is_big=False,
                )
            except Exception as reaction_err:
                logger.warning(
                    f"Failed to update reaction for player {player_id}: {reaction_err}"
                )

        if result.get("completed"):
            profile = result.get("profile") or {}
            logger.info(
                f"Onboarding completed for player {player_id}: role={profile.get('role', 'Unknown')}"
            )

            try:
                verify_profile = await api_request(
                    "GET", f"/players/{player_id}/profile"
                )
                logger.info(
                    f"Profile verified for player {player_id}: {verify_profile.get('role') if verify_profile else 'Unknown'}"
                )
            except Exception as verify_error:
                logger.error(
                    f"Profile verification failed for player {player_id}: {verify_error}"
                )

            await state.clear()
            update_player_state(
                player_id,
                onboarding_session_id=None,
                current_question_id=None,
                current_options=None,
            )

            # Show loading image while profile/avatar is being generated
            await send_random_loading_image(message, caption_key="processing_caption")

            # Avatar generation + onboarding message is handled in _generate_and_send_avatar
            if message.bot is None:
                logger.error(
                    f"message.bot is None for player {player_id}, cannot generate avatar"
                )
            else:
                asyncio.create_task(
                    _generate_and_send_avatar(player_id, session_id, message.bot)
                )

        else:
            next_question = result.get("next_question")
            if next_question:
                logger.info(
                    f"Next onboarding question: id={next_question['id']}, text={next_question['text']}..."
                )
                logger.info(
                    f"Next question options: {[opt['label'] for opt in next_question['options']]}"
                )
                if next_question.get("image_url"):
                    logger.info(
                        f"Next question has image: {next_question['image_url']}"
                    )
                # Store next question data in state for matching
                logger.info(
                    f"Storing next question in state: question_id={next_question['id']}, "
                    f"options={[opt['label'] for opt in next_question['options']]}"
                )
                await state.update_data(
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                )
                update_player_state(
                    player_id,
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                )
                keyboard = create_onboarding_keyboard(
                    next_question["options"], next_question["id"]
                )
                await send_question_with_image(
                    message, next_question, keyboard, BOT_LANGUAGE
                )

    except Exception as e:
        logger.error(f"Failed to submit onboarding answer: {e}")
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def action_selection(callback: types.CallbackQuery):
    """Handle player action selection"""
    if callback.data is None:
        await callback.answer(lang.get_errors(BOT_LANGUAGE)["invalid_format"])
        return
    parts = callback.data.split(":")

    if len(parts) != 2:
        await callback.answer(lang.get_errors(BOT_LANGUAGE)["invalid_format"])
        return

    action_id = parts[1]
    player_id = callback.from_user.id

    try:
        # Get current day to validate
        day = await api_request("GET", "/game/current-day")
        if day is None:
            raise Exception("No current day data from API")

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
        if callback.message:
            await callback.message.answer(
                msgs["recorded"], reply_markup=create_main_menu_keyboard()
            )
        await callback.answer()

    except Exception as e:
        logger.error(f"Failed to record action for player {player_id}: {e}")
        msgs = lang.get_actions(BOT_LANGUAGE)
        if callback.message:
            await callback.message.answer(msgs["error"].format(error=str(e)))
        await callback.answer()


async def refresh_game(callback: types.CallbackQuery):
    """Refresh game information"""
    if callback.data is None:
        await callback.answer(lang.get_errors(BOT_LANGUAGE)["invalid_format"])
        return
    parts = callback.data.split(":")

    if len(parts) != 2:
        await callback.answer(lang.get_errors(BOT_LANGUAGE)["invalid_format"])
        return

    player_id = callback.from_user.id

    try:
        # Refresh current day
        day = await api_request("GET", "/game/current-day")
        if day is None:
            raise Exception("No current day data from API")

        msgs = lang.get_current_day(BOT_LANGUAGE)

        # Build actions text
        actions_text = "\n\n".join(
            [
                f"{i + 1} - {a['text']}"
                for i, a in enumerate(day.get("player_actions", []))
            ]
        )

        # Build NPC dialogues text
        npc_dialogues_text = ""
        if day.get("npc_dialogues"):
            npc_dialogues_text = "\n".join(
                [f"- {d['npc']}: {d['dialogue']}" for d in day["npc_dialogues"]]
            )

        if isinstance(callback.message, types.Message):
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
            player_ids = get_all_player_ids()

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
                    update_player_state(player_id, pending_updates=[])

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

    # Detect bot username at startup (used for deep-link/share flows)
    global BOT_USERNAME
    try:
        bot_me = await bot.get_me()
        BOT_USERNAME = bot_me.username
        logger.info(f"Bot username: {BOT_USERNAME}")
    except Exception as e:
        logger.warning(f"Failed to fetch bot username: {e}")

    # Register handlers
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_profile, Command("profile"))
    dp.message.register(cmd_today, Command("today"))
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_bridge, Command("bridge"))
    dp.message.register(cmd_gm_start_game, Command("gm_start_game"))
    dp.message.register(cmd_gm_kick, Command("gm_kick"))
    dp.message.register(cmd_gm_list_games, Command("gm_list_games"))
    dp.message.register(cmd_gm_continue_game, Command("gm_continue_game"))
    dp.message.register(cmd_gm_regenerate_turn, Command("gm_regenerate_turn"))
    dp.message.register(cmd_gm_restart_game, Command("gm_restart_game"))
    dp.message.register(cmd_gm_restart_game_confirm, Command("gm_restart_game_confirm"))
    dp.message.register(handle_voice_message, F.content_type == types.ContentType.VOICE)

    # Onboarding answer handler - must be registered BEFORE general text handlers
    # Onboarding answer handler - inline keyboard callback
    dp.callback_query.register(
        handle_onboarding_inline_answer, F.data.startswith("onb_ans:")
    )
    # Fallback for manually typed text answers (if user types instead of pressing button)
    dp.message.register(onboarding_answer, OnboardingState.waiting_for_answer)

    # General text message handler (catch-all for non-command messages)
    dp.message.register(handle_text_message, F.text & ~F.command)

    # Callback query handlers
    dp.callback_query.register(
        game_selection_callback, F.data.startswith("select_game:")
    )
    dp.callback_query.register(action_selection, F.data.startswith("action:"))
    dp.callback_query.register(refresh_game, F.data.startswith("refresh_game:"))

    logger.info("Starting Telegram Bot")

    # Start polling loop in background
    polling_task = asyncio.create_task(polling_loop(bot))

    # Start bot polling
    await dp.start_polling(bot)

    # Clean up
    polling_task.cancel()
    from contextlib import suppress

    with suppress(asyncio.CancelledError):
        await polling_task


if __name__ == "__main__":
    asyncio.run(main())
