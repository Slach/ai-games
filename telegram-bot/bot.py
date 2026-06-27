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
from typing import Any
from urllib.parse import quote

import aiohttp
import language as lang
from retry import call_with_retry
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject, CommandStart
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
from aiogram.utils.deep_linking import create_start_link, decode_payload
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram_sqlite_storage.sqlitestore import SQLStorage
from aiohttp_socks import ProxyConnector
from player_store import (
    delete_player_state,
    get_all_briefing_turns,
    get_player_state,
    record_reference,
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
GAME_SCHEDULER_URL = os.getenv("GAME_SCHEDULER_URL", "http://game-scheduler:8001")

DEFAULT_LANGUAGE = "en"

BOT_USERNAME: str | None = None

# Game Master Telegram user ID — only this user can send GM commands
try:
    GAME_MASTER_ID = int(os.getenv("TELEGRAM_BOT_GAME_MASTER_ID", "0"))
except (ValueError, TypeError):
    logger.warning("Invalid TELEGRAM_BOT_GAME_MASTER_ID value, defaulting to 0")
    GAME_MASTER_ID = 0

# Socks5 proxy configuration
# Set to empty string to disable proxy (direct connection)
# For Docker, use host.docker.internal:PORT or proxy IP address
TELEGRAM_SOCKS_PROXY = os.getenv("TELEGRAM_SOCKS_PROXY", "")

# ============== FSM States ==============


class OnboardingState(StatesGroup):
    """State machine for onboarding flow"""

    waiting_for_name = State()
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


# Track last briefing turn sent per player to avoid duplicate messages on poll.
# Persisted to player_states.last_briefing_turn_sent so it survives bot restarts.
_last_sent_briefing_turn: dict[int, int] = {}


def _mark_briefing_sent(player_id: int, turn_num: int) -> None:
    """Record that a briefing was sent — updates both in-memory cache and DB."""
    _last_sent_briefing_turn[player_id] = turn_num
    update_player_state(player_id, last_briefing_turn_sent=turn_num)


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
        host, port_str = proxy_url.rsplit(":", 1)
        try:
            port = int(port_str)
        except (ValueError, TypeError):
            logger.warning("Invalid proxy port %r, using default 9999", port_str)
            return (host, 9999, username, password)
        return (host, port, username, password)

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

        connector = ProxyConnector(host=host, port=port, username=username or None, password=password or None)

        return aiohttp.ClientSession(connector=connector)

    except Exception as e:
        logger.warning(f"Failed to configure proxy {proxy_url}: {e}. Using direct connection.")
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
                logger.error(f"API error: {resp.status} - {error_text}", stack_info=True)
                raise Exception(f"API error: {resp.status} - {error_text}")
            return await resp.json()
    except aiohttp.ClientError as e:
        logger.error(f"HTTP error during API request: {e}", exc_info=True)
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
        if not proxy_url.startswith("socks5://") and not proxy_url.startswith("socks5h://"):
            proxy_url = f"socks5://{proxy_url}"

        session = AiohttpSession(proxy=proxy_url)
        logger.info(f"Configured SOCKS5 proxy: {proxy_url}")
        return session

    except Exception as e:
        logger.warning(f"Failed to configure proxy {proxy_url}: {e}. Using direct connection.")
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


def get_player_language(player_id: int) -> str:
    """Get the player's chosen language from persistent state."""
    state = get_player_state(player_id)
    return state.get("language", DEFAULT_LANGUAGE)


async def get_game_language(game_id: str, fallback: str = DEFAULT_LANGUAGE) -> str:
    """Get the game's stored language from the server.

    Falls back to the provided fallback (or DEFAULT_LANGUAGE) when the
    server can't be reached or the game doesn't exist yet.
    """
    try:
        result = await api_request("GET", "/game/started", params={"game_id": game_id})
        if result and result.get("language"):
            return result["language"]
    except Exception as e:
        logger.warning(f"Failed to get language for game {game_id}: {e}")
    return fallback


async def send_random_loading_image(message: types.Message, caption_key: str = "loading_caption", language: str = DEFAULT_LANGUAGE) -> bool:
    """Fetch and send a random loading image from the API with a caption.

    Args:
        message: Telegram message context
        caption_key: Key in IMAGES dict for the caption text (default: "loading_caption")
        language: Language code (default: DEFAULT_LANGUAGE)

    Returns True if sent, False otherwise.
    """
    try:
        result = await api_request("GET", "/content/loading-image")
        image_url = result.get("image_url") if result else None
        if image_url:
            caption = lang.get_images(language)[caption_key]
            return await send_image_from_api_url(message, image_url, caption=caption)
    except Exception as e:
        logger.warning(f"Failed to get/send loading image: {e}")
    return False


async def send_random_splash_image(message: types.Message, caption: str = "", reply_markup=None, game_id: str | None = None) -> bool:
    """Fetch and send a random splash image from the API with optional caption.

    Args:
        message: Telegram message context
        caption: Caption text (e.g., game description) to include with the image
        reply_markup: Optional keyboard to show with the image
        game_id: Game ID to scope the splash image (None = default_game)

    Returns True if sent, False otherwise.
    """
    try:
        params = {"game_id": game_id} if game_id else None
        result = await api_request("GET", "/content/splash-image", params=params)
        image_url = result.get("image_url") if result else None
        if image_url:
            return await send_image_from_api_url(message, image_url, caption=caption, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Failed to get/send splash image: {e}")
    return False


async def send_question_with_image(
    bot_or_message: types.Message,
    question: dict,
    keyboard: InlineKeyboardMarkup,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """Send a question to the player, optionally with an image.

    If the question has an image_url, sends it as a photo with caption.
    Otherwise sends plain text.
    Returns the question text that was displayed.
    """
    image_url = question.get("image_url")
    options = question.get("options", [])

    # Use label for display when available (species/gender questions), fall back to value
    def _option_display(opt: dict, idx: int) -> str:
        return f"{idx + 1}. {escape_markdown(opt.get('label', opt['value']))}"

    options_text = "\n\n".join([_option_display(opt, i) for i, opt in enumerate(options)])
    question_text = lang.get_onboarding(language)["question_prefix"].format(id=question["id"], text=escape_markdown(question["text"]))
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
                    photo = BufferedInputFile(photo_data, filename=f"q_{question['id']}.png")
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
                        photo = BufferedInputFile(photo_data, filename=f"opt_{question['id']}_{i}.png")
                        caption = f"{i + 1}. {escape_markdown(opt.get('label', opt['value']))}"
                        media_group.append(
                            InputMediaPhoto(
                                media=photo,
                                caption=caption,
                                parse_mode="Markdown",
                            )
                        )
                    else:
                        logger.warning(f"Failed to download option image {i}: {resp.status}")
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


async def check_player_game_status(player_id: int) -> dict[str, Any] | None:
    """Check if player has an existing game profile"""
    try:
        profile = await api_request("GET", f"/players/{player_id}/profile")
        return profile
    except Exception:
        return None


def _build_share_text(msgs: dict, game_title: str = "") -> str:
    """Build pre-filled text for t.me/share/url?text= parameter."""
    base = msgs.get("share_text", "Join the game")
    if game_title:
        # Strip any existing guillemets from the title to avoid double-wrapping
        # e.g. incoming «Title» wrapped again produces ««Title»»
        clean_title = game_title.strip("«»")
        return f"{base} «{clean_title}»!"
    return f"{base}!"


async def _generate_and_send_avatar(player_id: int, session_id: str, bot: Bot):
    """Generate avatar, then send onboarding complete message with avatar, then notify others."""
    try:
        result = await api_request(
            "POST",
            f"/onboarding/{session_id}/complete",
            timeout_total=300,
        )
        if result is None:
            logger.error(f"Onboarding completion returned no result for player {player_id}", stack_info=True)
            return
        avatar_url = result.get("avatar_url")
        profile = result.get("profile", {})
        game_started = result.get("game_started", False)
        game_just_started = result.get("game_just_started", False)
        other_player_ids = result.get("other_player_ids", [])
        game_title = result.get("game_title", "")
        game_language = result.get("language", DEFAULT_LANGUAGE)

        onboarding_msgs = lang.get_onboarding(game_language)

        # Format species/gender with hybrid display
        species_primary = profile.get("species", "Unknown") or "Unknown"
        species_secondary = profile.get("species_secondary")
        gender_primary = profile.get("gender", "Unknown") or "Unknown"
        gender_secondary = profile.get("gender_secondary")

        profile_msgs = lang.get_profile(game_language)
        if species_secondary:
            species_display = profile_msgs["hybrid_species"].format(primary=species_primary, secondary=species_secondary)
        else:
            species_display = species_primary

        if gender_secondary:
            gender_display = profile_msgs["hybrid_gender"].format(primary=gender_primary, secondary=gender_secondary)
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
                invite_url = await create_start_link(bot, f"{game_id}:{player_id}", encode=True)
                share_text = _build_share_text(onboarding_msgs, game_title)
                share_url = f"https://t.me/share/url?url={quote(invite_url, safe='')}&text={quote(share_text, safe='')}"
                # Escape the URL for Markdown to handle underscores in bot username
                invite_text = onboarding_msgs["invite_title"] + "\n\n" + onboarding_msgs["invite_message"].format(invite_url=escape_markdown(invite_url))

                invite_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=onboarding_msgs["invite_button"],
                                url=share_url,
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
        logger.error(f"Avatar generation/sending failed for player {player_id}: {e}", exc_info=True)
        try:
            onboarding_msgs = lang.get_onboarding(get_player_language(player_id))
            # Try to get profile info for fallback message
            try:
                profile = await api_request("GET", f"/players/{player_id}/profile")
                if profile is None:
                    profile = {}
                text = onboarding_msgs["onboarding_complete"].format(
                    role=escape_markdown(profile.get("role", "Crew Member")),
                    role_description=escape_markdown(profile.get("role_description", "")),
                    species=escape_markdown(profile.get("species", "Unknown")),
                    gender=escape_markdown(profile.get("gender", "Unknown")),
                    traits=escape_markdown("\n- ".join(profile.get("personality_traits", []))),
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
            logger.error(f"Failed to send fallback message to player {player_id}", exc_info=True)


async def _broadcast_new_player(new_player_id: int, profile: dict, other_player_ids: list, bot: Bot):
    """Notify existing players about a new crew member joining."""
    try:
        player_name = profile.get("player_name", "") or str(new_player_id)

        for other_id in other_player_ids:
            try:
                msgs = lang.get_onboarding(get_player_language(other_id))
                notify_text = msgs["new_player_joined"].format(
                    player_name=player_name,
                    role=escape_markdown(profile.get("role", "Crew Member")),
                    role_description=escape_markdown(profile.get("role_description", "")),
                )
                await bot.send_message(
                    chat_id=other_id,
                    text=notify_text,
                    parse_mode="Markdown",
                )
                avatar_url = profile.get("avatar_url")
                await _send_avatar_to_player(bot, other_id, avatar_url, player_name, profile)
            except Exception as e:
                logger.warning(f"Failed to notify player {other_id}: {e}")
    except Exception as e:
        logger.error(f"Broadcast new player failed: {e}", exc_info=True)


async def _send_avatar_to_player(bot: Bot, chat_id: int, avatar_url: str | None, player_name: str, profile: dict):
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


async def _broadcast_game_started(new_player_id: int, profile: dict, other_player_ids: list[int], bot: Bot):
    """Notify all players that the game has started (the new player triggered >= 3 players).

    Also sends bridge image + mission info to all existing players.
    """
    try:
        player_name = profile.get("player_name", "") or str(new_player_id)

        # Fetch bridge image and mission info once for all players
        mission = None
        bridge = None
        try:
            mission = await api_request("GET", "/game/mission", ignore_codes=(404,))
        except Exception as e:
            logger.error(f"Failed to fetch mission: {e}", exc_info=True)
        try:
            bridge = await api_request("GET", "/game/bridge-image", ignore_codes=(404,))
        except Exception as e:
            logger.error(f"Failed to fetch bridge image: {e}", exc_info=True)

        # Notify ALL players (new + existing) that the game has started
        # Send to existing players AND the new player who triggered start
        all_recipients = other_player_ids + [new_player_id]
        for other_id in all_recipients:
            try:
                player_lang = get_player_language(other_id)
                onboarding_msgs = lang.get_onboarding(player_lang)
                bridge_msgs = lang.get_bridge(player_lang)
                # Only send text notification to existing players (new player
                # already got onboarding-complete message)
                if other_id != new_player_id:
                    await bot.send_message(
                        chat_id=other_id,
                        text=onboarding_msgs["game_starting_broadcast"].format(
                            player_name=player_name,
                            role=escape_markdown(profile.get("role", "")),
                            role_description=escape_markdown(profile.get("role_description", "")),
                        ),
                        parse_mode="Markdown",
                    )
                    avatar_url = profile.get("avatar_url")
                    await _send_avatar_to_player(bot, other_id, avatar_url, player_name, profile)
                # Send bridge image with mission info to all
                if bridge and bridge.get("image_url"):
                    caption = bridge_msgs["title"]
                    if mission:
                        caption += "\n\n" + bridge_msgs["mission_header"].format(name=mission.get("name", ""))
                        caption += "\n\n" + bridge_msgs["mission_desc"].format(description=mission.get("description", ""))
                    # Fetch and send bridge photo directly to this chat
                    try:
                        async with (
                            aiohttp.ClientSession() as session,
                            session.get(
                                bridge["image_url"],
                                timeout=aiohttp.ClientTimeout(total=30),
                            ) as img_resp,
                        ):
                            if img_resp.status == 200:
                                photo_data = await img_resp.read()
                                photo = BufferedInputFile(photo_data, filename="bridge.png")
                                await bot.send_photo(
                                    chat_id=other_id,
                                    photo=photo,
                                    caption=caption,
                                    parse_mode="Markdown",
                                )
                    except Exception as e:
                        logger.warning(f"Failed to send bridge image to player {other_id}: {e}")
            except Exception as e:
                logger.warning(f"Failed to notify player {other_id} about game start: {e}")
    except Exception as e:
        logger.error(f"Broadcast game started failed: {e}", exc_info=True)


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


def create_onboarding_keyboard(options: list, question_id: int, selected_index: int | None = None) -> InlineKeyboardMarkup:
    """Create inline keyboard for onboarding options.

    Buttons show numbers [1] [2] [3] etc. attached to the message
    itself — unlike ReplyKeyboardMarkup, these CANNOT be dismissed
    by the user, ensuring they always have a way to answer.

    If selected_index is provided, that button gets a ✅ prefix
    to visually indicate the player's choice.
    """
    builder = InlineKeyboardBuilder()
    for idx in range(len(options)):
        if selected_index == idx:
            text = f"✅ [{idx + 1}]"
        else:
            text = f"[{idx + 1}]"
        builder.add(
            InlineKeyboardButton(
                text=text,
                callback_data=f"onb_ans:{question_id}:{idx}",
            )
        )
    builder.adjust(len(options))  # one row, Telegram wraps if too wide

    logger.info(f"Created onboarding inline keyboard for question_id={question_id} with {len(options)} buttons")

    return builder.as_markup()


def create_main_menu_keyboard(language: str = DEFAULT_LANGUAGE) -> ReplyKeyboardMarkup:
    """Create compact main menu keyboard with horizontal button layout"""
    menu = lang.get_menu(language)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=menu["start"]), KeyboardButton(text=menu["profile"]), KeyboardButton(text=menu["turn"])],
            [KeyboardButton(text=menu["team"]), KeyboardButton(text=menu["invite"]), KeyboardButton(text=menu["help"])],
        ],
        resize_keyboard=True,
    )


def create_action_keyboard(actions: list, selected_action_id: str | None = None) -> InlineKeyboardMarkup:
    """Create inline keyboard for game actions

    Buttons show numbers [1] [2] [3] etc. instead of full action text.
    Full action text is displayed in the message as a numbered list.
    Arranged in a single row for maximum compactness.

    If selected_action_id is provided, that button gets a ✅ prefix
    to visually indicate the player's choice.
    """
    builder = InlineKeyboardBuilder()
    for idx, action in enumerate(actions, start=1):
        if action["id"] == selected_action_id:
            text = f"✅ [{idx}]"
        else:
            text = f"[{idx}]"
        builder.add(InlineKeyboardButton(text=text, callback_data=f"action:{action['id']}"))
    # All actions in a single row for maximum compactness
    builder.adjust(len(actions))
    return builder.as_markup()


def create_game_info_keyboard(game_id: str) -> InlineKeyboardMarkup:
    """Create keyboard with game information"""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🔄 Refresh", callback_data=f"refresh_game:{game_id}"))
    return builder.as_markup()


# ============== Handlers ==============


async def create_new_game(player_id: int, language: str = "ru") -> tuple[str, str]:
    """Create a new game with the given language.

    Returns (game_id, game_name).
    """
    result = await api_request(
        "POST",
        "/admin/create-game",
        data={"name": f"Game by {player_id}", "language": language},
    )
    if not result:
        raise Exception("No response from /admin/create-game")
    game_id = result.get("game_id")
    if not game_id:
        raise Exception("No game_id returned from /admin/create-game")
    game_name = result.get("name", "") or game_id
    return game_id, game_name


async def show_player_language_selection(message: types.Message, state: FSMContext):
    """Show language selection for the player before showing game list."""
    lang_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{lang.HELLO['ru']} {lang.get_language_flag('ru')}",
                    callback_data="player_lang:ru",
                ),
                InlineKeyboardButton(
                    text=f"{lang.HELLO['en']} {lang.get_language_flag('en')}",
                    callback_data="player_lang:en",
                ),
            ],
        ]
    )
    await message.answer(
        "> " * 5 + "🌐" + " <" * 5 + "\n\n",
        reply_markup=lang_keyboard,
    )
    await state.set_state(GameSelectionState.waiting_for_game_selection)


async def player_language_selection_callback(callback: types.CallbackQuery, state: FSMContext):
    """Handle player's language choice, then show game list."""
    await callback.answer()

    data = callback.data or ""
    if not data.startswith("player_lang:"):
        return

    lang_code = data.split(":", 1)[1]
    if lang_code not in ("ru", "en"):
        return

    player_id = callback.from_user.id
    message = callback.message

    if not isinstance(message, types.Message):
        return

    # Store player's language preference
    await state.update_data(player_language=lang_code)
    update_player_state(player_id, language=lang_code)

    # Remove language keyboard
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.error(f"Failed to remove language keyboard: {e}", exc_info=True)

    # Confirm language selection
    onboarding_msgs = lang.get_onboarding(lang_code)
    lang_flag = lang.get_language_flag(lang_code)
    lang_name = lang.get_language_name(lang_code, lang_code)
    await message.answer(
        onboarding_msgs["language_confirmation"].format(language=lang_name, flag=lang_flag),
        parse_mode="Markdown",
    )

    # Now show game list in chosen language
    await show_game_selection(message, state, language=lang_code)


async def show_game_selection(message: types.Message, state: FSMContext, language: str = ""):
    """Show available games or option to create a new one."""
    effective_lang = language or DEFAULT_LANGUAGE
    msgs = lang.get_onboarding(effective_lang)

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
            game_lang_flag = lang.get_language_flag(game.get("language", "ru"))
            btn_text = f"{game_lang_flag} {started} {name} ({player_count})"
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
        logger.error(f"Failed to fetch games list: {e}", exc_info=True)
        error_msgs = lang.get_errors(effective_lang)
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def start_onboarding_flow(
    message: types.Message,
    state: FSMContext,
    player_id: int,
    game_id: str,
    player_name: str = "",
    language: str = "",
):
    """Start onboarding flow with a specific game_id and optional player_name.

    Uses game language from state if set, otherwise falls back to DEFAULT_LANGUAGE.
    """
    effective_language = language or DEFAULT_LANGUAGE
    msgs = lang.get_onboarding(effective_language)

    try:
        logger.info(f"Starting onboarding for player_id={player_id}, game_id={game_id}, player_name={player_name}, language={effective_language}")
        result = await api_request(
            "POST",
            "/onboarding/start",
            data={
                "player_id": player_id,
                "game_id": game_id,
                "player_name": player_name,
                "language": effective_language,
            },
            timeout_total=600,
        )
        logger.info(f"Onboarding start response: {result}")
        if result is None:
            raise Exception("No response from API when starting onboarding")

        welcome_text = result.get("welcome_message") or msgs["welcome"]
        game_title = result.get("game_title", "")
        if game_title:
            welcome_text = f"**{game_title}**\n\n{welcome_text}" if welcome_text else f"**{game_title}**"

        # Send splash image with game description as caption
        # NOTE: No main menu keyboard during onboarding — buttons show only after completion
        splash_sent = await send_random_splash_image(message, welcome_text, game_id=game_id)
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
            current_question_text=question["text"],
            current_question_image_url=question.get("image_url"),
        )

        logger.info(f"First onboarding question: id={question['id']}, text={question['text']}...")
        logger.info(f"Question options: {[opt['value'] for opt in question['options']]}")
        if question.get("image_url"):
            logger.info(f"Question has image: {question['image_url']}")

        keyboard = create_onboarding_keyboard(question["options"], question["id"])
        await send_question_with_image(message, question, keyboard, effective_language)
        await state.set_state(OnboardingState.waiting_for_answer)

    except Exception as e:
        logger.error(f"Failed to start onboarding for player {player_id}: {type(e).__name__} - {str(e)}", exc_info=True)
        error_msgs = lang.get_errors(effective_language)
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def game_selection_callback(callback: types.CallbackQuery, state: FSMContext):
    """Handle game selection callback.

    For "new" games: create with player's stored language.
    For existing games: ask for player name, using the game's language.
    """
    await callback.answer()

    data = callback.data or ""
    if not data.startswith("select_game:"):
        return

    player_id = callback.from_user.id
    game_id_or_new = data.split(":", 1)[1]
    message = callback.message

    if not isinstance(message, types.Message):
        logger.warning(f"Callback message not accessible for player {player_id}, data={data}")
        return

    # Read player's stored language preference
    state_data = await state.get_data()
    player_lang = state_data.get("player_language", DEFAULT_LANGUAGE)

    # Remove selection keyboard to avoid duplicate taps
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.error(f"Failed to remove selection keyboard: {e}", exc_info=True)

    try:
        if game_id_or_new == "new":
            # Create game with player's language
            game_id, game_name = await create_new_game(player_id, language=player_lang)

            if not game_id:
                raise Exception("No game_id returned from create_new_game")

            onboarding_msgs = lang.get_onboarding(player_lang)

            # Show game name confirmation
            await message.answer(
                f"🎮 **{game_name}**",
                parse_mode="Markdown",
            )

            # Ask for player name
            await message.answer(
                onboarding_msgs["name_question"],
                parse_mode="Markdown",
            )

            await state.update_data(
                game_id=game_id,
                game_language=player_lang,
            )
            await state.set_state(OnboardingState.waiting_for_name)
        else:
            game_id = game_id_or_new

            if not game_id:
                raise Exception("No game_id selected")

            # Fetch game language and name from API
            game_lang = player_lang
            game_name = ""
            try:
                result = await api_request("GET", "/admin/list-games")
                games = result.get("games", []) if result else []
                for g in games:
                    if g.get("game_id") == game_id:
                        game_lang = g.get("language", player_lang)
                        game_name = g.get("name", "")
                        break
            except Exception as e:
                logger.warning(f"Failed to fetch language for game {game_id}: {e}")

            # Show which game the player is joining
            onboarding_msgs = lang.get_onboarding(game_lang)
            if game_name:
                await message.answer(
                    onboarding_msgs["selected_game"].format(game_name=game_name),
                    parse_mode="Markdown",
                )

            # Ask for player name in game's language
            await message.answer(
                onboarding_msgs["name_question"],
                parse_mode="Markdown",
            )

            await state.update_data(
                game_id=game_id,
                game_language=game_lang,
            )
            await state.set_state(OnboardingState.waiting_for_name)

    except Exception as e:
        logger.error(f"Failed to process game selection for player {player_id}: {e}", exc_info=True)
        error_msgs = lang.get_errors(player_lang)
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def handle_onboarding_name(message: types.Message, state: FSMContext):
    """Handle player name input during onboarding."""
    if message.from_user is None:
        return
    player_id = message.from_user.id

    player_name = message.text.strip() if message.text else ""
    if not player_name or len(player_name) < 1 or len(player_name) > 50:
        onboarding_msgs = lang.get_onboarding(get_player_language(player_id))
        await message.answer(onboarding_msgs["name_length_error"])
        return

    logger.info(f"Player {player_id} entered name: {player_name}")

    # Store name in FSM data and proceed to onboarding
    data = await state.get_data()
    game_id = data.get("game_id", "default_game")
    game_language = data.get("game_language", "")
    effective_lang = game_language or DEFAULT_LANGUAGE

    # Confirm player's name in chosen language
    onboarding_msgs = lang.get_onboarding(effective_lang)
    await message.answer(
        onboarding_msgs["game_name_confirmation"].format(name=player_name),
        parse_mode="Markdown",
    )

    await send_random_loading_image(message, language=effective_lang)

    await state.update_data(player_name=player_name)

    # Proceed to the actual onboarding flow with game language if set
    await start_onboarding_flow(message, state, player_id, game_id, player_name, language=game_language)


async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext):
    """Handle /start command - Begin onboarding or join existing game"""
    if message.from_user is None:
        return
    player_id = message.from_user.id

    player_lang = get_player_language(player_id)
    msgs = lang.get_onboarding(player_lang)

    # Check if player already has an active onboarding session in memory
    player_state = get_player_state(player_id)
    session_id = player_state.get("onboarding_session_id")
    if session_id:
        logger.info(f"Player {player_id} already has active onboarding session: {session_id}")
        current_options = player_state.get("current_options", [])
        current_question_text = player_state.get("current_question_text")
        current_question_image_url = player_state.get("current_question_image_url")
        current_question_id = player_state.get("current_question_id", 1)

        if current_options and current_question_text:
            # Re-send the current question with image and inline keyboard
            keyboard = create_onboarding_keyboard(current_options, current_question_id)
            question = {
                "id": current_question_id,
                "text": current_question_text,
                "options": current_options,
                "image_url": current_question_image_url,
            }
            await send_question_with_image(message, question, keyboard, player_lang)
            await state.set_state(OnboardingState.waiting_for_answer)
        else:
            # No cached question data — fetch current question from API
            try:
                session_data = await api_request(
                    "GET",
                    f"/onboarding/{session_id}",
                    params={"language": player_lang},
                    ignore_codes=(404,),
                )
            except Exception as e:
                logger.error(f"Failed to fetch onboarding session {session_id}: {e}", exc_info=True)
                session_data = None

            if session_data and session_data.get("completed"):
                # Session already completed but player never got completion flow.
                # Trigger avatar generation and completion.
                logger.info(f"Session {session_id} already completed for player {player_id}, triggering completion")
                await message.answer(
                    msgs["onboarding_complete_restored"],
                    parse_mode="Markdown",
                )
                await state.clear()
                update_player_state(
                    player_id,
                    onboarding_session_id=None,
                    current_question_id=None,
                    current_options=None,
                )
                if message.bot is not None:
                    asyncio.create_task(_generate_and_send_avatar(player_id, session_id, message.bot))
                return

            if session_data and session_data.get("next_question"):
                next_question = session_data["next_question"]
                logger.info(f"Restored onboarding question from API: id={next_question['id']}")
                await state.update_data(
                    session_id=session_id,
                    game_id=session_data.get("game_id", "default_game"),
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                )
                update_player_state(
                    player_id,
                    onboarding_session_id=session_id,
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                    current_question_text=next_question["text"],
                    current_question_image_url=next_question.get("image_url"),
                )
                keyboard = create_onboarding_keyboard(next_question["options"], next_question["id"])
                await send_question_with_image(message, next_question, keyboard, player_lang)
                await state.set_state(OnboardingState.waiting_for_answer)
            else:
                # Nothing useful came back — guide the player to reset
                logger.error(f"Stale onboarding session {session_id} for player {player_id}: no question data available", exc_info=True)
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=msgs["clear_session_button"],
                                callback_data=f"onb_clear:{session_id}",
                            )
                        ]
                    ]
                )
                await message.answer(
                    msgs["stale_onboarding_session"],
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
        return

    game_id, referrer_id = None, None
    if command.args:
        try:
            payload = decode_payload(command.args)
            if payload and ":" in payload:
                game_id, referrer_id_str = payload.split(":", 1)
                referrer_id = int(referrer_id_str) if referrer_id_str.isdigit() else None
            elif payload:
                game_id = payload
        except Exception as e:
            logger.warning(f"Failed to decode start payload: {e}")

    if game_id:
        logger.info(f"Player {player_id} started with game_id={game_id} from deep link")
        if referrer_id:
            logger.info(f"Referrer detected: {referrer_id} invited {player_id} into game {game_id}")
            # Persist the referral (deduplicated; self-referrals ignored).
            try:
                if record_reference(player_id, referrer_id, game_id):
                    logger.info(f"Recorded new reference: referrer={referrer_id} -> referred={player_id} game={game_id}")
            except Exception as ref_err:
                logger.warning(f"Failed to record reference for {player_id}: {ref_err}")

    # Check if player already has a profile
    try:
        profile = await check_player_game_status(player_id)

        if profile:
            # Check if player is dead (spectator)
            if profile.get("is_dead") or profile.get("is_spectator"):
                spectator_msgs = lang.get_spectator(player_lang)
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=spectator_msgs["start_over_button"],
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
            await send_random_loading_image(message, language=player_lang)

            welcome_text = lang.get_onboarding(player_lang)["welcome_back"].format(
                role=profile["role"],
                role_description=profile["role_description"],
                traits=", ".join(profile["personality_traits"]),
            )

            # Try to send avatar as photo, fall back to text-only
            avatar_url = profile.get("avatar_url")
            avatar_sent = False

            if avatar_url:
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
                                    caption=welcome_text,
                                    parse_mode="Markdown",
                                    reply_markup=create_main_menu_keyboard(),
                                )
                                avatar_sent = True
                except Exception as avatar_err:
                    logger.warning(f"Failed to send welcome_back avatar for {player_id}: {avatar_err}")

            if not avatar_sent:
                await message.answer(
                    welcome_text,
                    parse_mode="Markdown",
                    reply_markup=create_main_menu_keyboard(),
                )

            # Send invite link for existing player
            if BOT_USERNAME:
                game_id = profile.get("game_id", "")
                if game_id and message.bot is not None:
                    # Fetch game title for share text
                    game_title = ""
                    try:
                        title_data = await api_request("GET", "/game/title", params={"game_id": game_id})
                        if title_data and title_data.get("title"):
                            game_title = title_data["title"]
                    except Exception as e:
                        logger.error("Failed to fetch game title for invite", exc_info=e)

                    # Use game's language for invite, not player's language
                    game_lang = await get_game_language(game_id, fallback=player_lang)
                    invite_msgs = lang.get_onboarding(game_lang)

                    invite_url = await create_start_link(message.bot, f"{game_id}:{player_id}", encode=True)
                    share_url = f"https://t.me/share/url?url={quote(invite_url, safe='')}&text={quote(_build_share_text(invite_msgs, game_title), safe='')}"
                    invite_text = invite_msgs["invite_title"] + "\n\n" + invite_msgs["invite_message"].format(invite_url=escape_markdown(invite_url))
                    invite_keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=invite_msgs["invite_button"],
                                    url=share_url,
                                )
                            ]
                        ]
                    )
                    try:
                        await message.answer(
                            invite_text,
                            parse_mode="Markdown",
                            reply_markup=invite_keyboard,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send invite to player {player_id}: {e}")

            # Update player state with game info
            update_player_state(player_id, game_id=profile.get("game_id", "default_game"))

        else:
            if game_id:
                # Deep link to a specific game — fetch its language
                game_lang = DEFAULT_LANGUAGE
                try:
                    result = await api_request("GET", "/admin/list-games")
                    games = result.get("games", []) if result else []
                    for g in games:
                        if g.get("game_id") == game_id:
                            game_lang = g.get("language", DEFAULT_LANGUAGE)
                            break
                except Exception as e:
                    logger.warning(f"Failed to fetch language for game {game_id}: {e}")

                await send_random_loading_image(message, language=game_lang)
                await start_onboarding_flow(message, state, player_id, game_id, language=game_lang)
            else:
                # New player — ask for language preference first
                await show_player_language_selection(message, state)

    except Exception as e:
        logger.error(f"Error in /start command for player {player_id}: {e}", exc_info=True)
        error_msgs = lang.get_errors(player_lang)
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def cmd_profile(message: types.Message):
    """Show player profile with avatar"""
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    try:
        profile = await api_request("GET", f"/players/{player_id}/profile")
        if profile is None:
            msgs = lang.get_profile(player_lang)
            await message.answer(msgs["no_profile"])
            return
        msgs = lang.get_profile(player_lang)

        # Build profile message with hybrid display support
        species_primary = profile.get("species", "Unknown") or "Unknown"
        species_secondary = profile.get("species_secondary")
        gender_primary = profile.get("gender", "Unknown") or "Unknown"
        gender_secondary = profile.get("gender_secondary")

        if species_secondary:
            species_display = msgs["hybrid_species"].format(primary=species_primary, secondary=species_secondary)
        else:
            species_display = species_primary

        if gender_secondary:
            gender_display = msgs["hybrid_gender"].format(primary=gender_primary, secondary=gender_secondary)
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
            logger.info(f"Fetching avatar for profile of player {player_id}: {avatar_url}")
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
                        logger.warning(f"Failed to download avatar for profile: {resp.status}")
            except Exception as avatar_err:
                logger.warning(f"Error downloading avatar for profile: {avatar_err}")

            # Fallback: avatar URL exists but download failed — show description if available
            if avatar_description:
                profile_text += "\n\n" + msgs["visualization"].format(avatar=avatar_description)
        elif avatar_description:
            profile_text += "\n\n" + msgs["visualization"].format(avatar=avatar_description)

        await message.answer(profile_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Failed to get profile for player {player_id}: {e}", exc_info=True)
        msgs = lang.get_profile(player_lang)
        await message.answer(msgs["no_profile"])


async def cmd_turn(message: types.Message):
    """Show current turn's game episode"""
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    try:
        msgs = lang.get_current_turn(player_lang)

        # First try to get personal briefing (new system)
        briefing = None
        current_turn_num = 1
        try:
            state = await api_request("GET", "/game/state")
            current_turn_num = state.get("turn", 1) if state else 1
            briefing = await api_request(
                "GET",
                f"/game/briefing/{player_id}/{current_turn_num}",
                ignore_codes=(404,),
            )
        except Exception:
            logger.error(
                f"Failed to fetch briefing for player {player_id} turn {current_turn_num}",
                exc_info=True,
            )

        if briefing and briefing.get("choices"):
            # New system: show personal briefing
            choices = briefing.get("choices", [])
            keyboard = create_action_keyboard(choices) if choices else None

            actions_text = "\n\n".join([f"{i + 1} - {a['text']}" for i, a in enumerate(choices)])

            # Fetch NPC dialogues for crew behavior context
            crew_dialogues_today = []
            try:
                day_data = await api_request(
                    "GET",
                    f"/game/turn/{current_turn_num}",
                    params={"game_id": briefing.get("game_id", "default_game")},
                    ignore_codes=(404,),
                )
                if day_data:
                    crew_dialogues_today = day_data.get("crew_dialogues", [])
            except Exception:
                logger.error(
                    "Failed to fetch turn data, continuing without crew dialogues",
                    exc_info=True,
                )

            crew_behavior_text = ""
            if crew_dialogues_today:
                dialogue_lines = []
                for d in crew_dialogues_today:
                    line = f"*{d.get('npc', 'NPC')}*: {d.get('dialogue', '')}"
                    dialogue_lines.append(line)
                crew_separator = "\n---\n"
                crew_behavior_text = f"\n\n{msgs['crew_dialogues']}\n{crew_separator.join(dialogue_lines)}"

            # Send action image first, if available
            chosen_action_url = briefing.get("chosen_action_url")
            if chosen_action_url:
                try:
                    async with aiohttp.ClientSession() as session:
                        resp = await session.get(
                            chosen_action_url,
                            timeout=aiohttp.ClientTimeout(total=30),
                        )
                        if resp.status == 200:
                            photo_data = await resp.read()
                            photo = BufferedInputFile(photo_data, filename="action.png")
                            await message.answer_photo(
                                photo=photo,
                            )
                            logger.info(f"[TURN] Sent action image for player {player_id}, turn {briefing['turn']}")
                        else:
                            logger.warning(f"[TURN] Failed to download action image: {resp.status}")
                except Exception as e:
                    logger.warning(f"[TURN] Failed to send action image: {e}")

            await message.answer(
                msgs["briefing_header"].format(briefing=briefing["briefing"]) + crew_behavior_text + "\n\n" + msgs["actions"].format(actions=actions_text) + "\n\n" + msgs["select_action"],
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        else:
            # Legacy system: show global story
            turn_data = await api_request("GET", "/game/current-turn")
            if turn_data is None:
                await message.answer(lang.get_errors(player_lang)["api_error"])
                return

            # Create action keyboard if there are actions to select
            keyboard = None
            if turn_data.get("player_actions"):
                keyboard = create_action_keyboard(turn_data["player_actions"])

            # Build actions text
            actions_text = "\n\n".join([f"{i + 1} - {a['text']}" for i, a in enumerate(turn_data.get("player_actions", []))])

            # Build crew behavior text from NPC dialogues (integrated, no separate section)
            crew_behavior_text = ""
            if turn_data.get("crew_dialogues"):
                dialogue_lines = []
                for d in turn_data["crew_dialogues"]:
                    line = f"*{d['npc']}*: {d['dialogue']}"
                    dialogue_lines.append(line)
                crew_separator = "\n---\n"
                crew_behavior_text = f"\n\n{msgs['crew_dialogues']}\n{crew_separator.join(dialogue_lines)}"

            await message.answer(
                msgs["title"].format(turn=turn_data["turn"]) + f"\n\n{msgs['story'].format(story=turn_data['story'])}" + crew_behavior_text + f"\n\n{msgs['actions'].format(actions=actions_text)}\n\n{msgs['select_action']}",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )

    except Exception as e:
        logger.error(f"Failed to get current turn for player {player_id}: {e}", exc_info=True)
        msgs = lang.get_current_turn(player_lang)
        await message.answer(msgs["error"].format(error=str(e)))


async def cmd_bridge(message: types.Message):
    """Show the current bridge image and mission info."""
    if message.from_user is None:
        return
    player_id = message.from_user.id

    try:
        # Get mission info
        mission = None
        try:
            mission = await api_request("GET", "/game/mission", ignore_codes=(404,))
        except Exception as e:
            logger.error(f"Failed to fetch mission: {e}", exc_info=True)

        # Get bridge image
        bridge = None
        try:
            bridge = await api_request("GET", "/game/bridge-image", ignore_codes=(404,))
        except Exception as e:
            logger.error(f"Failed to fetch bridge image: {e}", exc_info=True)

        # Use the game's language for bridge captions
        bridge_lang = await get_game_language("default_game", fallback=get_player_language(player_id))
        bridge_msgs = lang.get_bridge(bridge_lang)

        if bridge and bridge.get("image_url"):
            caption = bridge_msgs["title"]
            if mission:
                caption += "\n\n" + bridge_msgs["mission_header"].format(name=mission.get("name", ""))
                caption += "\n\n" + bridge_msgs["mission_desc"].format(description=mission.get("description", ""))
            await send_image_from_api_url(message, bridge["image_url"], caption=caption)
        else:
            await message.answer(bridge_msgs["error"])
    except Exception as e:
        logger.error(f"Failed to get bridge image for player {player_id}: {e}", exc_info=True)
        await message.answer(str(e))


async def cmd_team(message: types.Message):
    """Show the full crew roster with avatars"""
    if message.from_user is None:
        return
    player_id = message.from_user.id
    msgs = lang.get_team(get_player_language(player_id))

    try:
        # Get player profile to find game_id
        profile = await api_request("GET", f"/players/{player_id}/profile", ignore_codes=(404,))
        if not profile:
            await message.answer(msgs["no_team"])
            return

        game_id = profile.get("game_id", "")
        if not game_id:
            await message.answer(msgs["no_team"])
            return

        # Fetch team data
        team_data = await api_request("GET", "/game/team", params={"game_id": game_id})
        if not team_data or not team_data.get("members"):
            await message.answer(msgs["no_team"])
            return

        members = team_data["members"]

        # Build roster text — list all members without distinguishing NPC/player
        roster_lines = []
        for m in members:
            name = m.get("name", "?")
            role = m.get("role", "?")
            species = m.get("species", "?") or "?"
            gender = m.get("gender", "?") or "?"
            if m.get("is_dead"):
                roster_lines.append(msgs["entry_dead"].format(name=name, role=role, species=species, gender=gender))
            else:
                roster_lines.append(msgs["entry"].format(name=name, role=role, species=species, gender=gender))

        roster_text = msgs["roster"].format(details="\n".join(roster_lines))

        # Send header
        await message.answer(
            msgs["header"].format(count=len(members)),
            parse_mode="Markdown",
        )

        # Download and send avatar images as media group
        media_group = []
        for m in members:
            avatar_url = m.get("avatar_url")
            if not avatar_url:
                continue

            name = m.get("name", "?")
            role = m.get("role", "?")
            species = m.get("species", "?") or "?"
            gender = m.get("gender", "?") or "?"

            try:
                async with aiohttp.ClientSession() as session:
                    resp = await session.get(
                        avatar_url,
                        timeout=aiohttp.ClientTimeout(total=30),
                    )
                    if resp.status == 200:
                        photo_data = await resp.read()
                        photo = BufferedInputFile(photo_data, filename=f"team_{name}.png")
                        caption = f"{name} — {role} | {species} | {gender}"
                        media_group.append(
                            InputMediaPhoto(
                                media=photo,
                                caption=caption,
                            )
                        )
                    else:
                        logger.warning(f"[TEAM] Failed to download avatar for {name}: {resp.status}")
            except Exception as e:
                logger.warning(f"[TEAM] Error downloading avatar for {name}: {e}")

        # Send media group (up to 10 photos per album)
        if media_group:
            # Telegram limits media groups to 10 items
            for i in range(0, len(media_group), 10):
                batch = media_group[i : i + 10]
                await call_with_retry(
                    lambda: message.answer_media_group(media=batch),
                )

        # Send roster text after images
        await call_with_retry(
            lambda: message.answer(
                roster_text,
                parse_mode="Markdown",
            ),
        )

    except Exception as e:
        logger.error(f"[TEAM] Failed for player {player_id}: {e}", exc_info=True)
        await message.answer(msgs["api_error"])


async def cmd_invite(message: types.Message):
    """Send invite: photo (bridge/splash) + text with deep link."""
    if message.from_user is None:
        return
    player_id = message.from_user.id

    try:
        profile = await api_request("GET", f"/players/{player_id}/profile", ignore_codes=(404,))
        if not profile:
            await message.answer(
                lang.get_profile(get_player_language(player_id))["no_profile"],
                reply_markup=create_main_menu_keyboard(),
            )
            return

        game_id = profile.get("game_id", "")
        if not game_id:
            await message.answer(
                "No active game found. Use /start to join a game.",
                reply_markup=create_main_menu_keyboard(),
            )
            return

        game_lang = await get_game_language(game_id, fallback=get_player_language(player_id))
        msgs = lang.get_onboarding(game_lang)

        if not BOT_USERNAME or message.bot is None:
            await message.answer(
                "Bot username is not available. Invite links cannot be generated at this moment.",
                reply_markup=create_main_menu_keyboard(),
            )
            return

        # Fetch game title
        game_title = ""
        try:
            title_data = await api_request("GET", "/game/title", params={"game_id": game_id})
            if title_data and title_data.get("title"):
                game_title = title_data["title"]
        except Exception as e:
            logger.error("Failed to fetch game title for invite", exc_info=e)

        # Fetch mission and bridge image
        mission = None
        bridge = None
        try:
            mission = await api_request("GET", "/game/mission", params={"game_id": game_id}, ignore_codes=(404,))
        except Exception as e:
            logger.error(f"Failed to fetch mission for invite: {e}", exc_info=True)
        try:
            bridge = await api_request("GET", "/game/bridge-image", params={"game_id": game_id}, ignore_codes=(404,))
        except Exception as e:
            logger.error(f"Failed to fetch bridge image for invite: {e}", exc_info=True)

        invite_url = await create_start_link(message.bot, f"{game_id}:{player_id}", encode=True)

        if mission and bridge and bridge.get("image_url"):
            # ── Mission exists: bridge photo with mission caption ──
            mission_name = mission.get("name", "")
            # Use short_description from DB if available; fall back to truncating full description
            short_desc = mission.get("short_description", "") or mission.get("description", "")[:500]
            clean_title = game_title.strip("«»")
            caption = msgs.get(
                "invite_mission_caption",
                "I invite you to the game «{game_title}»!\n\n🚀 Mission «{mission_name}»\n\n{mission_description}\n\n{invite_url}",
            ).format(
                game_title=escape_markdown(clean_title),
                mission_name=escape_markdown(mission_name),
                mission_description=escape_markdown(short_desc),
                invite_url=escape_markdown(invite_url),
            )
            sent = await send_image_from_api_url(message, bridge["image_url"], caption=caption)
            if not sent:
                await message.answer(caption, parse_mode="Markdown")
        else:
            # ── No mission: splash image with game title ──
            clean_title = game_title.strip("«»")
            caption = msgs.get(
                "invite_no_mission_caption",
                "I invite you to the game «{game_title}»!\n\n{invite_url}",
            ).format(
                game_title=escape_markdown(clean_title),
                invite_url=escape_markdown(invite_url),
            )
            splash_sent = await send_random_splash_image(message, caption=caption, game_id=game_id)
            if not splash_sent:
                await message.answer(caption, parse_mode="Markdown")

        # ── Second message: forward instruction + deep link ──
        forward_text = msgs.get(
            "invite_forward",
            "👆 Forward the message above to a friend!\n\nOr send them this link:\n{invite_url}",
        ).format(
            invite_url=escape_markdown(invite_url),
        )
        await message.answer(forward_text, parse_mode="Markdown")

        logger.info(f"Sent invite to player {player_id}")

    except Exception as e:
        logger.error(f"Failed to send invite for player {player_id}: {e}", exc_info=True)
        await message.answer(
            "Failed to generate invite link. Please try again later.",
            reply_markup=create_main_menu_keyboard(),
        )


async def cmd_reset(message: types.Message, state: FSMContext):
    """Handle /reset — leave the current game (replaced by NPC) and start over.

    Confirms before performing the irreversible reset: the player's role is taken
    over by an NPC, their profile and onboarding answers are wiped, then they are
    sent back to language selection.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    reset_msgs = lang.get_reset(get_player_language(player_id))

    # Only offer reset when there is something to wipe (a profile or an in-flight
    # onboarding session).
    profile = await check_player_game_status(player_id)
    has_onboarding = bool(get_player_state(player_id).get("onboarding_session_id"))
    if not profile and not has_onboarding:
        await message.answer(reset_msgs["nothing_to_reset"])
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=reset_msgs["confirm_yes"],
                    callback_data="reset_confirm:yes",
                ),
                InlineKeyboardButton(
                    text=reset_msgs["confirm_no"],
                    callback_data="reset_confirm:no",
                ),
            ],
        ]
    )
    await message.answer(
        reset_msgs["confirm"],
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def reset_confirm_callback(callback: types.CallbackQuery, state: FSMContext):
    """Handle the /reset confirmation inline buttons (yes/no)."""
    if callback.from_user is None:
        await callback.answer()
        return
    player_id = callback.from_user.id
    player_lang = get_player_language(player_id)
    reset_msgs = lang.get_reset(player_lang)

    message = callback.message
    if not isinstance(message, types.Message):
        await callback.answer()
        return

    # Acknowledge callback immediately to prevent query ID expiration
    await callback.answer()

    # Remove the confirmation keyboard regardless of the choice
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.error(f"Failed to remove confirmation keyboard: {e}", exc_info=True)

    if not (callback.data or "").endswith(":yes"):
        await message.answer(reset_msgs["cancelled"])
        return

    await message.answer(reset_msgs["resetting"])

    try:
        result = await api_request(
            "POST",
            "/admin/reset-player",
            data={"player_id": player_id, "language": player_lang},
            timeout_total=120,
        )
    except Exception as e:
        logger.error(f"[RESET] API call failed for player {player_id}: {e}", exc_info=True)
        await message.answer(reset_msgs["error"].format(error=e))
        return

    if not result or result.get("status") != "success":
        error_detail = (result or {}).get("detail", "unknown error") if result else "no API response"
        await message.answer(reset_msgs["error"].format(error=error_detail))
        return

    # Clear the local briefing-dedup cache so a fresh game can deliver briefings.
    try:
        _last_sent_briefing_turn.pop(player_id, None)
    except KeyError:
        pass
    try:
        update_player_state(player_id, last_briefing_turn_sent=None)
    except Exception as e:
        logger.error(f"Failed to clear briefing turn for player {player_id}: {e}", exc_info=True)

    # Wipe FSM + business state, then restart from language selection.
    await state.clear()
    delete_player_state(player_id)

    npc_name = result.get("npc_name")
    npc_part = reset_msgs["success_npc_part"].format(npc_name=npc_name) if npc_name else ""
    await message.answer(
        reset_msgs["success"].format(npc_part=npc_part),
        parse_mode="Markdown",
    )
    await show_player_language_selection(message, state)


async def cmd_help(message: types.Message):
    """Show help information"""
    if message.from_user is None:
        return
    player_id = message.from_user.id

    msgs = lang.get_help(get_player_language(player_id))

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

    # Build help text from components
    parts = [game_title]
    parts.append("")
    parts.append(msgs["regular_commands"])
    if GAME_MASTER_ID > 0 and player_id == GAME_MASTER_ID:
        parts.append("")
        parts.append(msgs["gm_commands"])
    parts.append("")
    parts.append(msgs["how_to_play"])
    help_text = "\n".join(parts)

    await message.answer(
        help_text,
    )


async def cmd_gm_start(message: types.Message):
    """GM command: Force start a game by ID.
    Usage: /gm_start <game_id>
    Only executable by the configured Game Master user.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        gm_msgs = lang.get_gm_commands(player_lang)
        logger.warning(f"Unauthorized /gm_start attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        gm_msgs = lang.get_gm_commands(player_lang)
        await message.answer(gm_msgs["start_game_usage"])
        return

    game_id = parts[1].strip()
    if not game_id:
        gm_msgs = lang.get_gm_commands(player_lang)
        await message.answer(gm_msgs["start_game_usage"])
        return

    # Use the game's language if available, fall back to the player's
    game_lang = await get_game_language(game_id, fallback=player_lang)
    gm_msgs = lang.get_gm_commands(game_lang)

    await message.answer(
        gm_msgs["game_generation_started"].format(game_id=game_id),
        parse_mode="Markdown",
    )

    try:
        result = await api_request(
            "POST",
            "/admin/start-game",
            data={"game_id": game_id, "language": game_lang},
            timeout_total=60,
        )
        if result and result.get("status") == "accepted":
            logger.info(f"Start game {game_id} accepted by server")
        else:
            logger.warning(f"Start game {game_id}: unexpected response {result}")
            await message.answer(gm_msgs["start_game_error"].format(error=result))
    except Exception as e:
        logger.error(f"Failed to start start-game request for {game_id}: {e}", exc_info=True)
        await message.answer(
            gm_msgs["start_game_failed"].format(error=e),
        )


async def cmd_gm_kick(message: types.Message):
    """GM command: Kick a player by role and replace with NPC.

    Usage: /gm_kick <game_id> <role_key> [reason]
    The kicked player receives a notification about being removed.
    Only executable by the configured Game Master user.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        gm_msgs = lang.get_gm_commands(player_lang)
        logger.warning(f"Unauthorized /gm_kick attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    # Parse args: /gm_kick <game_id> <role_key> [reason]
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        gm_msgs = lang.get_gm_commands(player_lang)
        await message.answer(gm_msgs["kick_usage"])
        return

    game_id = parts[1].strip()
    role_key = parts[2].strip()
    game_lang = await get_game_language(game_id, fallback=player_lang)
    gm_msgs = lang.get_gm_commands(game_lang)

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
            error_detail = result.get("detail", gm_msgs["unknown_error"]) if result else gm_msgs["no_api_response"]
            await message.answer(gm_msgs["kick_error"].format(error=error_detail))
    except Exception as e:
        logger.error(f"Failed to kick player: {e}", exc_info=True)
        await message.answer(gm_msgs["kick_error"].format(error=e))


async def cmd_gm_list(message: types.Message):
    """GM command: List available games.

    Usage: /gm_list
    Only executable by the configured Game Master user.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    gm_msgs = lang.get_gm_commands(get_player_language(player_id))

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        logger.warning(f"Unauthorized /gm_list attempt by user {player_id}")
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
            title = game.get("title") or game.get("name") or gm_msgs["default_game_title"]
            player_count = game.get("player_count", 0)
            onboarding_count = game.get("onboarding_count", 0)
            turn = game.get("current_turn", 0)
            started = game.get("started", False)
            status = "started" if started else "waiting"
            status_icon = "🚀" if started else "⏳"
            lang_flag = lang.get_language_flag(game.get("language", "ru"))
            lines.append(
                gm_msgs["games_list_entry"].format(
                    idx=idx,
                    game_id=game_id,
                    title=title,
                    turn=turn,
                    player_count=player_count,
                    onboarding_count=onboarding_count,
                    status_icon=status_icon,
                    status=status,
                )
                + f" {lang_flag}"
            )

        # Append scheduler status
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{GAME_SCHEDULER_URL}/scheduler/status",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        sched = await resp.json()
                        lines.append("")
                        if sched.get("mode") == "paused":
                            lines.append(gm_msgs["scheduler_paused"])
                        elif sched.get("next_run_at"):
                            lines.append(gm_msgs["next_turn_auto"].format(time=sched["next_run_at"]))
        except Exception:
            pass

        await message.answer("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to list games: {e}", exc_info=True)
        await message.answer(gm_msgs["list_games_error"].format(error=e))


async def cmd_gm_pause(message: types.Message):
    """GM command: Toggle scheduler pause/resume.

    Usage: /gm_pause
    Only executable by the configured Game Master user.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        gm_msgs = lang.get_gm_commands(player_lang)
        logger.warning(f"Unauthorized /gm_pause attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    gm_msgs = lang.get_gm_commands(player_lang)

    try:
        # First, check current state
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{GAME_SCHEDULER_URL}/scheduler/status",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    await message.answer(gm_msgs["scheduler_unavailable"])
                    return
                sched = await resp.json()

            # Toggle: if paused -> resume, else -> pause
            if sched.get("mode") == "paused":
                async with session.post(
                    f"{GAME_SCHEDULER_URL}/scheduler/resume",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        await message.answer(
                            gm_msgs["pause_toggled"].format(state="resumed"),
                            parse_mode="Markdown",
                        )
                    else:
                        await message.answer(
                            gm_msgs["pause_error"].format(error=f"HTTP {resp.status}"),
                        )
            else:
                async with session.post(
                    f"{GAME_SCHEDULER_URL}/scheduler/pause",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        await message.answer(
                            gm_msgs["pause_toggled"].format(state="paused"),
                            parse_mode="Markdown",
                        )
                    else:
                        await message.answer(
                            gm_msgs["pause_error"].format(error=f"HTTP {resp.status}"),
                        )
    except Exception as e:
        logger.error(f"Failed to toggle scheduler: {e}", exc_info=True)
        await message.answer(gm_msgs["pause_error"].format(error=e))


async def cmd_gm_continue(message: types.Message):
    """GM command: Generate the next turn for a game.

    Usage: /gm_continue <game_id>
    Only executable by the configured Game Master user.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        gm_msgs = lang.get_gm_commands(player_lang)
        logger.warning(f"Unauthorized /gm_continue attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        gm_msgs = lang.get_gm_commands(player_lang)
        await message.answer(gm_msgs["continue_game_usage"])
        return

    game_id = parts[1].strip()
    if not game_id:
        gm_msgs = lang.get_gm_commands(player_lang)
        await message.answer(gm_msgs["continue_game_usage"])
        return

    # Use the game's language if available, fall back to the player's
    game_lang = await get_game_language(game_id, fallback=player_lang)
    gm_msgs = lang.get_gm_commands(game_lang)

    await message.answer(
        gm_msgs["turn_generation_started"].format(game_id=game_id),
        parse_mode="Markdown",
    )

    try:
        result = await api_request(
            "POST",
            "/admin/continue-game",
            params={"game_id": game_id, "language": game_lang},
            timeout_total=60,
        )
        if result and result.get("status") == "accepted":
            turn_num = result.get("turn", 1)
            logger.info(f"Continue game {game_id} accepted by server for turn {turn_num}")
        else:
            logger.warning(f"Continue game {game_id}: unexpected response {result}")
            await message.answer(gm_msgs["continue_game_error"].format(error=result))
    except Exception as e:
        logger.error(f"Failed to start continue-game request for {game_id}: {e}", exc_info=True)
        await message.answer(
            gm_msgs["continue_game_failed"].format(error=e),
        )


async def cmd_gm_turn(message: types.Message):
    """GM command: Regenerate the current turn with state reset.

    Deletes the current turn's data and regenerates it fresh.
    Usage: /gm_turn <game_id>
    Only executable by the configured Game Master user.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        gm_msgs = lang.get_gm_commands(player_lang)
        logger.warning(f"Unauthorized /gm_turn attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        gm_msgs = lang.get_gm_commands(player_lang)
        await message.answer(gm_msgs["regenerate_turn_usage"])
        return

    game_id = parts[1].strip()
    if not game_id:
        gm_msgs = lang.get_gm_commands(player_lang)
        await message.answer(gm_msgs["regenerate_turn_usage"])
        return

    # Use the game's language if available, fall back to the player's
    game_lang = await get_game_language(game_id, fallback=player_lang)
    gm_msgs = lang.get_gm_commands(game_lang)

    await message.answer(
        gm_msgs["turn_regeneration_started"].format(game_id=game_id),
        parse_mode="Markdown",
    )

    try:
        result = await api_request(
            "POST",
            "/admin/regenerate-turn",
            params={"game_id": game_id, "language": game_lang},
            timeout_total=60,
        )
        if result and result.get("status") == "accepted":
            turn_num = result.get("turn", 1)
            logger.info(f"Regenerate turn for game {game_id} accepted by server for turn {turn_num}")
        else:
            logger.warning(f"Regenerate turn {game_id}: unexpected response {result}")
            await message.answer(gm_msgs["regenerate_turn_error"].format(error=result))
    except Exception as e:
        logger.error(f"Failed to start regenerate-turn request for {game_id}: {e}", exc_info=True)
        await message.answer(
            gm_msgs["regenerate_turn_failed"].format(error=e),
        )


async def cmd_gm_restart(message: types.Message):
    """GM command: Reset game state and restart from turn 1.

    Immediately restarts the game, deleting all content.
    Usage: /gm_restart <game_id>
    Only executable by the configured Game Master user.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        gm_msgs = lang.get_gm_commands(player_lang)
        logger.warning(f"Unauthorized /gm_restart attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        gm_msgs = lang.get_gm_commands(player_lang)
        await message.answer(gm_msgs["restart_game_usage"])
        return

    game_id = parts[1].strip()
    if not game_id:
        gm_msgs = lang.get_gm_commands(player_lang)
        await message.answer(gm_msgs["restart_game_usage"])
        return

    # Use the game's language if available, fall back to the player's
    game_lang = await get_game_language(game_id, fallback=player_lang)
    gm_msgs = lang.get_gm_commands(game_lang)

    await message.answer(
        gm_msgs["restarting_game"].format(game_id=game_id),
        parse_mode="Markdown",
    )

    try:
        # Step 1: Reset game state
        result = await api_request(
            "POST",
            "/admin/restart-game",
            params={"game_id": game_id, "language": game_lang},
            timeout_total=120,
        )
        if not result or result.get("status") != "success":
            await message.answer(gm_msgs["restart_game_error"].format(error=result))
            return

        # Reset dedup cache for all players in this game — otherwise push_server
        # will skip delivery because _last_sent_briefing_turn still has the old turn 1
        # from the previous game session (loaded from persistent DB at startup).
        try:
            players_result = await api_request("GET", "/players", params={"game_id": game_id})
            if players_result:
                for p in players_result:
                    if isinstance(p, dict):
                        pid = p.get("player_id")
                        if pid is not None and pid in _last_sent_briefing_turn:
                            del _last_sent_briefing_turn[pid]
                            update_player_state(pid, last_briefing_turn_sent=None)
                            logger.info(f"[DEDUP] Reset briefing cache for player {pid} (game restart)")
        except Exception as e:
            logger.warning(f"Failed to reset dedup cache for restart: {e}")

        # Step 2: Start background game generation (async, returns immediately)
        start_result = await api_request(
            "POST",
            "/admin/start-game",
            data={
                "game_id": game_id,
                "language": game_lang,
                "was_restarted": True,
            },
            timeout_total=60,
        )
        if start_result and start_result.get("status") == "accepted":
            logger.info(f"Restart game {game_id}: reset complete, background generation started")
            deleted_turns = result.get("deleted_turns", 0)
            deleted_briefings = result.get("deleted_briefings", 0)
            deleted_actions = result.get("deleted_actions", 0)
            await message.answer(
                gm_msgs["restart_cleanup_done"].format(
                    game_id=game_id,
                    deleted_turns=deleted_turns,
                    deleted_briefings=deleted_briefings,
                    deleted_actions=deleted_actions,
                ),
                parse_mode="Markdown",
            )
        else:
            await message.answer(gm_msgs["restart_game_error"].format(error=start_result))
    except Exception as e:
        logger.error(f"Failed to restart game {game_id}: {e}", exc_info=True)
        await message.answer(gm_msgs["restart_game_error"].format(error=e))


async def cmd_gm_status(message: types.Message):
    """GM command: Show game status (players, NPCs, their choices).

    Usage: /gm_status <game_id>
    No images — text-only overview.
    Only executable by the configured Game Master user.

    If the status message exceeds Telegram's character limit (~4000 safe),
    it is split into 3 parts: header, players, NPCs.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        gm_msgs = lang.get_gm_commands(player_lang)
        logger.warning(f"Unauthorized /gm_status attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        gm_msgs = lang.get_gm_commands(player_lang)
        await message.answer(gm_msgs["status_usage"])
        return

    game_id = parts[1].strip()
    game_lang = await get_game_language(game_id, fallback=player_lang)
    gm_msgs = lang.get_gm_commands(game_lang)
    await message.answer(gm_msgs["status_loading"].format(game_id=game_id), parse_mode="Markdown")

    try:
        result = await api_request("GET", "/game/status", params={"game_id": game_id})
        if not result:
            await message.answer(gm_msgs["status_error"].format(error="No response"))
            return

        # Build header
        ship = gm_msgs["ship_intact"] if result.get("ship_alive") else gm_msgs["ship_destroyed"]
        status_label = result.get("status", "?")
        header = gm_msgs["status_header"].format(
            game_id=game_id,
            turn=result.get("current_turn", result.get("turn", 1)),
            status=status_label,
            ship=ship,
            player_count=result.get("player_count", 0),
            alive_count=result.get("alive_count", 0),
            npc_count=result.get("npc_count", 0),
            npc_alive_count=result.get("npc_alive_count", 0),
        )

        # Build players section
        players_text = ""
        players = result.get("players", [])
        if players:
            players_parts = []
            for p in players:
                icon = "☠" if p.get("is_dead") else ("✅" if p.get("has_chosen") else "⏳")
                action = p.get("chosen_action", "") or gm_msgs["waiting_label"]
                name = p.get("player_name", "") or str(p.get("player_id", "?"))
                players_parts.append(
                    gm_msgs["status_player_entry"].format(
                        icon=icon,
                        player_id=p.get("player_id", "?"),
                        name=name,
                        role=p.get("role", "?"),
                        action=action,
                    )
                )
            players_text = gm_msgs["status_players_header"] + "\n" + "\n\n".join(players_parts)
        else:
            players_text = gm_msgs["status_no_players"]

        # Build NPCs section
        npcs_text = ""
        npcs = result.get("npcs", [])
        if npcs:
            npcs_parts = []
            for n in npcs:
                action = n.get("chosen_action_text", "") or gm_msgs["no_data_label"]
                icon = "☠" if n.get("is_dead") else ("✅" if action != gm_msgs["no_data_label"] else "⏳")
                npcs_parts.append(
                    gm_msgs["status_npc_entry"].format(
                        icon=icon,
                        name=n.get("npc_name", "?"),
                        role=n.get("role", "?"),
                        action=action,
                    )
                )
            npcs_text = gm_msgs["status_npcs_header"] + "\n" + "\n\n".join(npcs_parts)
        else:
            npcs_text = gm_msgs["status_no_npcs"]

        # Combine full message
        full_message = header + "\n" + players_text + "\n\n" + npcs_text

        # Fetch scheduler status
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{GAME_SCHEDULER_URL}/scheduler/status",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        sched = await resp.json()
                        if sched.get("mode") == "paused":
                            full_message += "\n\n" + gm_msgs["scheduler_paused"]
                        elif sched.get("next_run_at"):
                            full_message += "\n\n" + gm_msgs["next_turn_at"].format(time=sched["next_run_at"])
        except Exception:
            pass  # Scheduler unavailable, omit the line

        # Telegram's limit is 4096 chars; use 3950 as safe threshold
        MAX_STATUS_LEN = 3950

        if len(full_message) <= MAX_STATUS_LEN:
            await message.answer(full_message, parse_mode="Markdown")
        else:
            # Split into 3 parts
            # Part 1: header
            await message.answer(header, parse_mode="Markdown")
            # Part 2: players
            await message.answer(players_text, parse_mode="Markdown")
            # Part 3: NPCs
            await message.answer(npcs_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Failed to get game status for {game_id}: {e}", exc_info=True)
        await message.answer(gm_msgs["status_error"].format(error=e))


async def cmd_gm_lang(message: types.Message):
    """GM command: Set the language for a game and regenerate its title.

    Usage: /gm_lang <game_id> <ru|en>
    Only executable by the configured Game Master user.
    """
    if message.from_user is None:
        return
    player_id = message.from_user.id
    gm_msgs = lang.get_gm_commands(get_player_language(player_id))

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        logger.warning(f"Unauthorized /gm_lang attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(gm_msgs["set_language_usage"])
        return

    game_id = parts[1].strip()
    lang_code = parts[2].strip().lower()

    if lang_code not in ("ru", "en"):
        await message.answer(gm_msgs["set_language_invalid"])
        return

    await message.answer(
        gm_msgs["set_language_progress"].format(lang_code=lang_code, game_id=game_id),
        parse_mode="Markdown",
    )

    try:
        result = await api_request(
            "POST",
            "/admin/set-language",
            data={"game_id": game_id, "language": lang_code},
            timeout_total=120,
        )
        if result and result.get("status") == "success":
            new_title = result.get("title", "")
            new_mission = result.get("mission_name") or ""
            success_text = gm_msgs["set_language_success"].format(game_id=game_id, lang_code=lang_code, title=new_title)
            if new_mission:
                success_text += f"\n🎯 New mission: **{new_mission}**"
            await message.answer(success_text, parse_mode="Markdown")
        else:
            detail = result.get("detail", gm_msgs["unknown_error"]) if result else gm_msgs["no_api_response"]
            await message.answer(gm_msgs["set_language_error"].format(detail=detail))
    except Exception as e:
        logger.error(f"Failed to set language for game {game_id}: {e}", exc_info=True)
        await message.answer(gm_msgs["set_language_error"].format(detail=e))


async def handle_voice_message(message: types.Message):
    """Handle voice messages"""
    if message.from_user is None:
        return
    player_id = message.from_user.id

    msgs = lang.get_messages(get_player_language(player_id))
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
        logger.error(f"Failed to send voice message to API: {e}", exc_info=True)


async def handle_text_message(message: types.Message):
    """Handle regular text messages (chat with Game Master)"""
    if message.from_user is None:
        return
    player_id = message.from_user.id
    player_lang = get_player_language(player_id)

    try:
        # Send message to Game Master API
        text_content = message.text
        if text_content is None:
            await message.answer(lang.get_messages(player_lang)["text_received"])
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

        msgs = lang.get_messages(player_lang)

        # If there's a response from Game Master, show it (plain text —
        # LLM output is not valid Telegram Markdown and would break parsing)
        if response and response.get("response"):
            await message.answer(
                f"{msgs['game_master_response']}\n\n{response['response']}",
            )
        else:
            await message.answer(msgs["text_received"])

    except Exception as e:
        logger.error(f"Failed to send text message to API: {e}", exc_info=True)
        msgs = lang.get_messages(player_lang)
        await message.answer(msgs["error"].format(error=str(e)))


async def handle_onboarding_inline_answer(callback: types.CallbackQuery, state: FSMContext):
    """Handle onboarding answer selection from inline keyboard buttons.

    Callback data format: onb_ans:<question_id>:<option_index>
    The option_index is used to look up the answer value from current_options in state.
    """
    await callback.answer()
    if callback.data is None:
        logger.error("handle_onboarding_inline_answer: callback.data is None", stack_info=True)
        return
    if callback.message is None:
        logger.error("handle_onboarding_inline_answer: callback.message is None", stack_info=True)
        return
    msg: types.Message = callback.message  # type: ignore[assignment]
    player_id = callback.from_user.id
    player_lang = get_player_language(player_id)

    parts = callback.data.split(":")
    if len(parts) != 3:
        await msg.answer(lang.get_errors(player_lang)["invalid_format"])
        return

    _, question_id_str, option_idx_str = parts
    try:
        option_idx = int(option_idx_str)
        callback_question_id = int(question_id_str)
    except (ValueError, TypeError):
        logger.warning("Invalid callback data from player %d: %r", player_id, callback.data)
        await msg.answer(lang.get_errors(player_lang)["invalid_format"])
        return
    error_msgs = lang.get_errors(player_lang)

    # Get current question data from state
    state_data = await state.get_data()
    session_id = state_data.get("session_id")
    current_question_id = state_data.get("current_question_id")
    current_options = state_data.get("current_options")

    logger.info(f"Inline onboarding answer: player={player_id}, callback_question_id={callback_question_id}, option_idx={option_idx}, session_id={session_id}")

    # Ignore stale button presses from old messages
    if current_question_id is not None and callback_question_id != current_question_id:
        logger.warning(f"Stale keyboard press: callback_question_id={callback_question_id} != current_question_id={current_question_id}, ignoring")
        error_msgs = lang.get_errors(player_lang)
        await callback.answer(error_msgs["stale_question"], show_alert=False)
        return

    if not session_id:
        logger.error(f"No session_id in state for player {player_id}", exc_info=True)
        await msg.answer(error_msgs["session_not_found"])
        return

    if not current_options or option_idx < 0 or option_idx >= len(current_options):
        logger.error(f"Invalid option_idx {option_idx} for {len(current_options) if current_options else 0} options", exc_info=True)
        await msg.answer(error_msgs["invalid_format"])
        return

    answer_value = current_options[option_idx]["value"]
    logger.info(f"Matched option: idx={option_idx}, value='{answer_value}'")

    # Set reaction to show processing
    if callback.bot is not None:
        try:
            await callback.bot.set_message_reaction(
                chat_id=player_id,
                message_id=msg.message_id,
                reaction=[ReactionTypeEmoji(emoji="👀")],  # 👀 eyes
                is_big=False,
            )
        except Exception as reaction_err:
            logger.warning(f"Failed to set reaction for player {player_id}: {reaction_err}")

    # Immediately update the inline keyboard to show ✅ on the selected option
    try:
        updated_keyboard = create_onboarding_keyboard(current_options, callback_question_id, selected_index=option_idx)
        await msg.edit_reply_markup(reply_markup=updated_keyboard)
    except Exception as kb_err:
        logger.warning(f"Failed to update onboarding keyboard for player {player_id}: {kb_err}")

    try:
        logger.info(f"Submitting onboarding answer (inline): session_id={session_id}, question_id={current_question_id}, answer_value='{answer_value}'")
        result = await api_request(
            "POST",
            f"/onboarding/{session_id}/answer",
            data={"question_id": current_question_id, "answer": answer_value},
            params={"language": DEFAULT_LANGUAGE},
        )
        logger.info(f"Onboarding answer response: {result}")

        if result is None:
            raise Exception("No response from API when submitting onboarding answer")

        # Answer processed successfully — update reaction to checkmark
        if callback.bot is None:
            logger.warning(f"callback.bot is None for player {player_id}, cannot set reaction")
        else:
            try:
                await callback.bot.set_message_reaction(
                    chat_id=player_id,
                    message_id=msg.message_id,
                    reaction=[ReactionTypeEmoji(emoji="👍")],  # 👍 thumbs up
                    is_big=False,
                )
            except Exception as reaction_err:
                logger.warning(f"Failed to update reaction for player {player_id}: {reaction_err}")

        if result.get("completed"):
            profile = result.get("profile") or {}
            logger.info(f"Onboarding completed for player {player_id}: role={profile.get('role', 'Unknown')}")

            try:
                verify_profile = await api_request("GET", f"/players/{player_id}/profile")
                logger.info(f"Profile verified for player {player_id}: {verify_profile.get('role') if verify_profile else 'Unknown'}")
            except Exception as verify_error:
                logger.error(f"Profile verification failed for player {player_id}: {verify_error}", exc_info=True)

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
                logger.error(f"message.bot is None for player {player_id}, cannot generate avatar", exc_info=True)
            else:
                asyncio.create_task(_generate_and_send_avatar(player_id, session_id, msg.bot))
        else:
            next_question = result.get("next_question")
            if next_question:
                logger.info(f"Next onboarding question (inline): id={next_question['id']}, text={next_question['text']}...")
                await state.update_data(
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                )
                update_player_state(
                    player_id,
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                    current_question_text=next_question["text"],
                    current_question_image_url=next_question.get("image_url"),
                )
                keyboard = create_onboarding_keyboard(next_question["options"], next_question["id"])
                await send_question_with_image(msg, next_question, keyboard, player_lang)

    except Exception as e:
        logger.error(f"Failed to submit onboarding answer (inline): {e}", exc_info=True)
        await callback.message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def clear_onboarding_callback(callback: types.CallbackQuery, state: FSMContext):
    """Clear a stale onboarding session and restart the flow.

    Called when the user presses the "Clear and Start Over" button
    after getting a stale_onboarding_session message.
    """
    await callback.answer()
    if callback.data is None:
        logger.error("clear_onboarding_callback: callback.data is None", stack_info=True)
        return
    if callback.message is None:
        logger.error("clear_onboarding_callback: callback.message is None", stack_info=True)
        return
    if not isinstance(callback.message, types.Message):
        logger.warning("Callback message is inaccessible for clear_onboarding")
        return
    msg: types.Message = callback.message
    player_id = callback.from_user.id

    parts = callback.data.split(":")
    if len(parts) != 2:
        return

    stale_session_id = parts[1]
    logger.info(f"Clearing stale onboarding session {stale_session_id} for player {player_id}")

    # Clear local player state
    update_player_state(
        player_id,
        onboarding_session_id=None,
        current_question_id=None,
        current_options=None,
        current_question_text=None,
        current_question_image_url=None,
    )

    # Remove the inline keyboard from the stale message
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.warning(f"Failed to remove keyboard: {e}")

    # Restart from language selection
    await state.clear()
    await show_player_language_selection(msg, state)


async def onboarding_answer(message: types.Message, state: FSMContext):
    """Handle onboarding answer selection from reply keyboard.

    Buttons show [1], [2], [3] etc. The number is extracted and used
    as an index into current_options to find the matching option value.
    """
    if message.from_user is None:
        logger.error("onboarding_answer: message.from_user is None", stack_info=True)
        return
    if message.text is None:
        logger.error("onboarding_answer: message.text is None", stack_info=True)
        return
    answer_text = message.text
    player_id = message.from_user.id
    error_msgs = lang.get_errors(get_player_language(player_id))

    logger.info(f"Onboarding answer handler called: player={player_id}, text='{answer_text}'")

    # Get current question data from state
    state_data = await state.get_data()
    session_id = state_data.get("session_id")
    current_question_id = state_data.get("current_question_id")
    current_options = state_data.get("current_options")

    logger.info(f"State data: session_id={session_id}, question_id={current_question_id}, options_count={len(current_options) if current_options else 0}")

    if not session_id:
        logger.error(f"No session_id in state for player {player_id}", stack_info=True)
        await message.answer(error_msgs["session_not_found"])
        return

    if not current_options:
        logger.error(f"No current_options in state for player {player_id}, state_data={state_data}", stack_info=True)
        await message.answer(error_msgs["invalid_format"])
        return

    # Match by numeric index from button text (e.g., "[1]" or "1")
    answer_value = None
    match = re.match(r"^(\[?)(\d+)(\]?)$", answer_text.strip())
    if match:
        try:
            idx = int(match.group(2)) - 1
        except (ValueError, TypeError):
            logger.debug("Failed to parse onboarding answer index from %r", answer_text)
            idx = -1
        if 0 <= idx < len(current_options):
            answer_value = current_options[idx]["value"]
            logger.info(f"Numeric match: idx={idx}, value='{answer_value}'")

    if not answer_value:
        logger.warning(f"No matching option found! Player text: '{answer_text}', Available options: {[opt['value'] for opt in current_options]}")
        await message.answer(error_msgs["invalid_format"])
        return

    # Set reaction to show processing
    if message.bot is not None:
        try:
            await message.bot.set_message_reaction(
                chat_id=player_id,
                message_id=message.message_id,
                reaction=[ReactionTypeEmoji(emoji="👀")],  # 👀 eyes
                is_big=False,
            )
        except Exception as reaction_err:
            logger.warning(f"Failed to set reaction for player {player_id}: {reaction_err}")

    try:
        logger.info(f"Submitting onboarding answer: session_id={session_id}, question_id={current_question_id}, answer_value='{answer_value}'")
        result = await api_request(
            "POST",
            f"/onboarding/{session_id}/answer",
            data={"question_id": current_question_id, "answer": answer_value},
            params={"language": DEFAULT_LANGUAGE},
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
                    reaction=[ReactionTypeEmoji(emoji="👍")],  # 👍 thumbs up
                    is_big=False,
                )
            except Exception as reaction_err:
                logger.warning(f"Failed to update reaction for player {player_id}: {reaction_err}")

        if result.get("completed"):
            profile = result.get("profile") or {}
            logger.info(f"Onboarding completed for player {player_id}: role={profile.get('role', 'Unknown')}")

            try:
                verify_profile = await api_request("GET", f"/players/{player_id}/profile")
                logger.info(f"Profile verified for player {player_id}: {verify_profile.get('role') if verify_profile else 'Unknown'}")
            except Exception as verify_error:
                logger.error(f"Profile verification failed for player {player_id}: {verify_error}", exc_info=True)

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
                logger.error(f"message.bot is None for player {player_id}, cannot generate avatar", exc_info=True)
            else:
                asyncio.create_task(_generate_and_send_avatar(player_id, session_id, message.bot))

        else:
            next_question = result.get("next_question")
            if next_question:
                logger.info(f"Next onboarding question: id={next_question['id']}, text={next_question['text']}...")
                logger.info(f"Next question options: {[opt['value'] for opt in next_question['options']]}")
                if next_question.get("image_url"):
                    logger.info(f"Next question has image: {next_question['image_url']}")
                # Store next question data in state for matching
                logger.info(f"Storing next question in state: question_id={next_question['id']}, options={[opt['value'] for opt in next_question['options']]}")
                await state.update_data(
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                )
                update_player_state(
                    player_id,
                    current_question_id=next_question["id"],
                    current_options=next_question["options"],
                    current_question_text=next_question["text"],
                    current_question_image_url=next_question.get("image_url"),
                )
                keyboard = create_onboarding_keyboard(next_question["options"], next_question["id"])
                await send_question_with_image(message, next_question, keyboard, get_player_language(player_id))

    except Exception as e:
        logger.error(f"Failed to submit onboarding answer: {e}", exc_info=True)
        await message.answer(error_msgs["onboarding_error"].format(error=str(e)))


async def action_selection(callback: types.CallbackQuery):
    """Handle player action selection"""
    player_id = callback.from_user.id
    player_lang = get_player_language(player_id)
    if callback.data is None:
        await callback.answer(lang.get_errors(player_lang)["invalid_format"])
        return
    parts = callback.data.split(":")

    if len(parts) != 2:
        await callback.answer(lang.get_errors(player_lang)["invalid_format"])
        return

    action_id = parts[1]

    # Acknowledge callback immediately to prevent query ID expiration
    await callback.answer()

    try:
        # Get current day to validate
        turn_data = await api_request("GET", "/game/current-turn")
        if turn_data is None:
            raise Exception("No current turn data from API")

        # Submit action
        await api_request(
            "POST",
            "/game/actions",
            data={
                "player_id": player_id,
                "turn": turn_data["turn"],
                "action_id": action_id,
                "choice": "selected",
            },
        )

        # Mark the selected action on the inline keyboard with ✅
        actions_list = turn_data.get("player_actions", [])
        if actions_list and isinstance(callback.message, types.Message):
            try:
                updated_keyboard = create_action_keyboard(actions_list, selected_action_id=action_id)
                await callback.message.edit_reply_markup(reply_markup=updated_keyboard)
            except Exception as kb_err:
                logger.warning(f"Failed to update action keyboard for player {player_id}: {kb_err}")

        # Use the game's language for confirmation messages
        action_lang = await get_game_language("default_game", fallback=player_lang)
        msgs = lang.get_actions(action_lang)
        if callback.message:
            await callback.message.answer(msgs["recorded"], reply_markup=create_main_menu_keyboard())

    except Exception as e:
        logger.error(f"Failed to record action for player {player_id}: {e}", exc_info=True)
        action_lang = player_lang
        try:
            action_lang = await get_game_language("default_game", fallback=player_lang)
        except Exception:
            pass
        msgs = lang.get_actions(action_lang)
        if callback.message:
            await callback.message.answer(msgs["error"].format(error=str(e)))


async def refresh_game(callback: types.CallbackQuery):
    """Refresh game information"""
    player_id = callback.from_user.id
    player_lang = get_player_language(player_id)
    if callback.data is None:
        await callback.answer(lang.get_errors(player_lang)["invalid_format"])
        return
    parts = callback.data.split(":")

    if len(parts) != 2:
        await callback.answer(lang.get_errors(player_lang)["invalid_format"])
        return

    # Acknowledge callback immediately to prevent query ID expiration
    await callback.answer()

    try:
        # Refresh current day
        turn_data = await api_request("GET", "/game/current-turn")
        if turn_data is None:
            raise Exception("No current turn data from API")

        msgs = lang.get_current_turn(player_lang)

        # Build actions text
        actions_text = "\n\n".join([f"{i + 1} - {a['text']}" for i, a in enumerate(turn_data.get("player_actions", []))])

        # Build NPC dialogues text
        crew_dialogues_text = ""
        if turn_data.get("crew_dialogues"):
            crew_dialogues_text = "\n".join([f"- {d['npc']}: {d['dialogue']}" for d in turn_data["crew_dialogues"]])

        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(
                msgs["title"].format(turn=turn_data["turn"]) + f"\n\n{msgs['story'].format(story=turn_data['story'])}\n\n{msgs['crew_dialogues']}\n{crew_dialogues_text}" + f"\n\n{msgs['actions'].format(actions=actions_text)}\n\n{msgs['select_action']}",
                parse_mode="Markdown",
                reply_markup=create_action_keyboard(turn_data.get("player_actions", [])),
            )

    except Exception as e:
        logger.error(f"Failed to refresh game for player {player_id}: {e}", exc_info=True)
        if isinstance(callback.message, types.Message):
            await callback.message.answer(lang.get_messages(player_lang)["error"].format(error=str(e)))


# ============== Polling Loop ==============


async def main():
    """Main entry point"""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set", stack_info=True)
        return

    # Configure SQLite storage for FSM state persistence
    db_path = os.getenv("AI_FSM_DB", "/app/fsm_storage.db")
    storage = SQLStorage(db_path=db_path, serializing_method="json")

    # Create aiohttp session with Socks5 proxy for Telegram API
    bot_session = create_bot_session()

    # Initialize bot and dispatcher with SQLite storage and proxy session
    bot = Bot(token=BOT_TOKEN, session=bot_session)
    dp = Dispatcher(storage=storage)

    # Detect bot username at startup (used for deep-link/share flows).
    # Retry on transient proxy timeouts — if this fails permanently the
    # bot can still handle messages but /invite and deep-links break.
    global BOT_USERNAME
    try:
        from retry import call_with_retry

        bot_me = await call_with_retry(lambda: bot.get_me(), max_retries=3)
        BOT_USERNAME = bot_me.username
        logger.info(f"Bot username: {BOT_USERNAME}")
    except Exception as e:
        logger.warning(f"Failed to fetch bot username after retries: {e}")

    # Register handlers
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_start, CommandStart(deep_link=True))
    dp.message.register(cmd_profile, Command("profile"))
    dp.message.register(cmd_turn, Command("turn"))
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_bridge, Command("bridge"))
    dp.message.register(cmd_team, Command("team"))
    dp.message.register(cmd_invite, Command("invite"))
    dp.message.register(cmd_reset, Command("reset"))
    dp.message.register(cmd_gm_start, Command("gm_start"))
    dp.message.register(cmd_gm_kick, Command("gm_kick"))
    dp.message.register(cmd_gm_list, Command("gm_list"))
    dp.message.register(cmd_gm_pause, Command("gm_pause"))
    dp.message.register(cmd_gm_continue, Command("gm_continue"))
    dp.message.register(cmd_gm_turn, Command("gm_turn"))
    dp.message.register(cmd_gm_restart, Command("gm_restart"))
    dp.message.register(cmd_gm_status, Command("gm_status"))
    dp.message.register(cmd_gm_lang, Command("gm_lang"))
    dp.message.register(handle_voice_message, F.content_type == types.ContentType.VOICE)

    # Onboarding name input handler — before general text handlers
    dp.message.register(handle_onboarding_name, OnboardingState.waiting_for_name)

    # Onboarding answer handler - inline keyboard callback
    dp.callback_query.register(handle_onboarding_inline_answer, F.data.startswith("onb_ans:"))
    # Fallback for manually typed text answers (if user types instead of pressing button)
    dp.message.register(onboarding_answer, OnboardingState.waiting_for_answer)

    # General text message handler (catch-all for non-command messages)
    dp.message.register(handle_text_message, F.text & ~F.command)

    # Callback query handlers
    dp.callback_query.register(game_selection_callback, F.data.startswith("select_game:"))
    dp.callback_query.register(player_language_selection_callback, F.data.startswith("player_lang:"))
    dp.callback_query.register(action_selection, F.data.startswith("action:"))
    dp.callback_query.register(refresh_game, F.data.startswith("refresh_game:"))
    dp.callback_query.register(reset_confirm_callback, F.data.startswith("reset_confirm:"))
    dp.callback_query.register(clear_onboarding_callback, F.data.startswith("onb_clear:"))

    # Load last_briefing_turn_sent from DB so dedup survives bot restarts
    global _last_sent_briefing_turn
    _last_sent_briefing_turn = get_all_briefing_turns()
    logger.info(
        "Loaded _last_sent_briefing_turn for %d player(s) from persistent storage",
        len(_last_sent_briefing_turn),
    )

    logger.info("Starting Telegram Bot")

    # Start push HTTP server (replaces old polling loop)
    from push_server import start_push_server

    push_runner = await start_push_server(
        bot=bot,
        language=DEFAULT_LANGUAGE,
        last_sent_briefing_turn=_last_sent_briefing_turn,
        mark_sent_fn=_mark_briefing_sent,
        create_keyboard_fn=create_action_keyboard,
    )

    # Start bot polling (aiogram).
    # Delete webhook without dropping pending updates so messages that
    # arrived while the bot was restarting (e.g. docker restart, crash)
    # are still delivered via the polling loop.
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)

    # Clean up push server
    await push_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
