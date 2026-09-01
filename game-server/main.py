"""
Game Server API - FastAPI service for AI Game Server
"""

import asyncio
import json
import logging
import os
import random
import secrets
import string
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import aiohttp
import uvicorn
from database import (
    GAME_START_MIN_PLAYERS,
    GAME_START_MAX_PLAYERS,
    add_game_message,
    adjust_npc_loyalty,
    clear_game_started,
    complete_generation_job,
    count_turn_action_autos,
    create_game,
    create_game_turn,
    create_mission,
    create_npc_profile,
    create_onboarding_session,
    create_player_profile,
    deactivate_replacement_npcs_for_player,
    clear_kicks_for_returning_player,
    delete_all_game_turns,
    delete_all_game_messages,
    delete_all_player_actions,
    delete_all_player_briefings,
    delete_game_turn,
    delete_game_images,
    delete_mission,
    delete_onboarding_sessions_for_player,
    delete_player_actions_for_turn,
    delete_player_briefings_for_turn,
    delete_briefing,
    delete_player_profile,
    end_game,
    fail_generation_job,
    get_active_generation_job,
    get_all_active_npcs,
    get_all_briefings_for_turn,
    get_all_games,
    get_all_npcs,
    get_available_games,
    get_all_roles,
    get_available_roles,
    get_db_connection,
    get_dead_players,
    get_game,
    get_game_action_stats,
    get_game_turn,
    get_game_image_count,
    get_game_language,
    get_game_messages,
    get_game_state,
    get_game_title,
    get_game_welcome_text,
    get_in_progress_generation_jobs,
    get_live_players,
    get_mission,
    get_npc_by_role,
    get_npc_profile,
    get_onboarding_count_in_game,
    get_onboarding_player_ids_in_game,
    get_onboarding_session,
    reserve_onboarding_slot,
    get_player_actions,
    get_player_briefing,
    get_player_count_in_game,
    get_npc_briefing,
    get_player_profile,
    should_reset_profile_for_reonboarding,
    get_players_in_game,
    get_players_who_need_to_choose,
    get_random_game_image,
    get_role_by_key,
    get_role_key_for_player,
    deactivate_npc,
    init_db,
    is_game_started,
    is_player_kicked,
    leave_game,
    mark_player_dead,
    set_player_wound_severity,
    set_npc_wound_severity,
    release_role,
    set_game_language,
    record_kick,
    reset_active_npcs,
    reset_game_state_to_turn1,
    reset_roles,
    save_game_image,
    save_game_title_and_welcome,
    save_player_action,
    save_player_action_stats,
    save_game_finale,
    save_player_briefing,
    start_game,
    start_generation_job,
    take_role,
    update_briefing_choice,
    update_briefing_chosen_action_url,
    update_game_turn_global_circumstances,
    update_game_turn_outcome,
    update_game_state,
    update_game_title,
    update_mission_stage_progress,
    update_onboarding_session,
    update_player_profile_last_poll,
)
from game_rules import HULL_MAX, THREAT_MAX, WOUND_DEAD, _to_int, apply_mission_progress, apply_ship_status, apply_systems_offline, compute_loyalty_change, compute_outcome_type, compute_threat_tick, loyalty_band, mutiny_conditions, resolve_injury, select_npc_role_keys
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from game_server import create_game_server
from game_concept import generate_game_concept, get_game_concept_lock
from image_generator import (
    DEFAULT_LOADING_FALLBACK_URL,
    DEFAULT_SPLASH_FALLBACK_URL,
    create_image_generator,
)
from language import (
    LANGUAGE_EN,
    LANGUAGE_RU,
    format_game_summary,
    get_game_strings,
    GENDER_TAGS,
    get_gender_type_name,
    get_hybrid_species_name,
    get_species_type_name,
    SPECIES_TAGS,
)
from push_client import push_briefings, push_language_changed, push_turn_outcome, push_game_over, push_game_summary, push_gm_notification, push_player_chosen_action, push_player_death, push_turn_reminder
from pydantic import BaseModel

# Configure logging.
# A daily file handler mirrors logs to /app/logs/ so they survive
# container restarts/recreates (docker json-logs are wiped on recreate).
try:
    os.makedirs("/app/logs", exist_ok=True)  # noqa: unchecked-throwing-call-python
except OSError:
    pass
# Configure logging.
# A daily file handler mirrors logs to /app/YYYY-MM-DD.log so they survive
# container restarts/recreates (docker json-logs are wiped on recreate).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"/app/logs/game-server-{datetime.now().strftime('%Y-%m-%d')}.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


class HealthCheckFilter(logging.Filter):
    """Suppress access logs for /health endpoint only."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


# Apply filter to uvicorn access logger to suppress /health noise
uvicorn_access = logging.getLogger("uvicorn.access")
uvicorn_access.addFilter(HealthCheckFilter())


# Track pending action image tasks keyed by (turn, game_id) so that
# _analyze_turn_outcome can await them before pushing the outcome.
# This ensures action images arrive BEFORE outcome text, not after.
_pending_action_tasks: dict[tuple[int, str], set[asyncio.Task]] = {}

# Per-(turn, game_id) async lock to prevent concurrent outcome analysis.
# Guards against the race: auto-action (create_task) vs continue-game (await).
_outcome_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _get_outcome_lock(turn: int, game_id: str) -> asyncio.Lock:
    """Get or create an async lock for (turn, game_id)."""
    key = (turn, game_id)
    if key not in _outcome_locks:
        _outcome_locks[key] = asyncio.Lock()
    return _outcome_locks[key]


def generate_game_id(length: int) -> str:
    """Generate a unique alphanumeric game ID."""
    while True:
        alphabet = string.ascii_lowercase + string.digits
        game_id = "".join(secrets.choice(alphabet) for _ in range(length))
        if not get_game(game_id):
            return game_id


# ============== Pydantic Models ==============


class GameInfo(BaseModel):
    """Game information for available games list"""

    game_id: str
    name: str
    description: str
    player_count: int
    status: str


class JoinGameRequest(BaseModel):
    """Request to join a game"""

    player_id: int
    game_id: str


class StartOnboardingRequest(BaseModel):
    """Request to start onboarding"""

    player_id: int
    game_id: str
    language: str
    player_name: str


class GameMessageRequest(BaseModel):
    """Request to send a game message"""

    player_id: int
    message: str
    message_type: str


class PlayerActionRequest(BaseModel):
    """Request to submit player action"""

    player_id: int
    turn: int
    action_id: str
    choice: str


class RemindTurnRequest(BaseModel):
    """Request to send a turn-deadline reminder to players who haven't chosen.

    level 1 = ~2 hours before the deadline, level 2 = ~30 minutes before.
    """

    level: int


class PollResponse(BaseModel):
    """Response from game polling endpoint"""

    new_game_turn: dict[str, Any] | None
    pending_actions: list[dict[str, Any]]
    messages_from_gm: list[dict[str, Any]]
    npc_messages: list[dict[str, Any]]
    avatar_url: str | None


class StartGameRequest(BaseModel):
    """Request to force-start a game"""

    game_id: str
    language: str
    force: bool
    was_restarted: bool


class KickPlayerRequest(BaseModel):
    """Request to kick a player by role"""

    role_key: str
    reason: str
    game_id: str


class CreateGameRequest(BaseModel):
    """Request to create a new game."""

    name: str
    description: str
    language: str
    schedule: str | None


class SetLanguageRequest(BaseModel):
    """Request to set a game's language."""

    game_id: str
    language: str


# ============== Random character proposals ==============

# Number of rejected proposals ("no" presses) after which the next character
# is generated and assigned to the player automatically.
ONBOARDING_MAX_REROLLS = 3

# Chance that a rolled character is a hybrid of two species.
SPECIES_HYBRID_CHANCE = 0.2

# Template avatar prompts per species key, used when the LLM avatar-prompt
# call fails. The species key is known exactly (no keyword sniffing needed).
_FALLBACK_AVATAR_TEMPLATES = {
    "human": (
        "Sci-fi character portrait of a {role} in Star Trek style. Personality traits: {traits}. "
        "{desc} Futuristic uniform, cinematic lighting, detailed face, 4K quality. "
        "Portrait, upper body, space opera aesthetic."
    ),
    "humanoid": (
        "Sci-fi character portrait of a humanoid {role} in Star Trek style. Personality traits: {traits}. "
        "{desc} Humanoid with subtle alien features, futuristic uniform, cinematic lighting, "
        "detailed face, 4K quality. Portrait, upper body, space opera aesthetic."
    ),
    "non_humanoid": (
        "Alien creature concept art of a non-humanoid {role}. Personality traits: {traits}. {desc} "
        "The creature is NOT a human or humanoid: no two arms ending in hands, no two legs, "
        "no human face or hair, not a bipedal silhouette, no uniform. "
        "Cinematic lighting, 4K quality, detailed alien biology. "
        "Full body or 3/4 view showing the alien physiology."
    ),
    "energy": (
        "Abstract energy-being concept art of an energy being {role}. Personality traits: {traits}. {desc} "
        "Glowing plasma energy form, luminous, ethereal, no solid body, no face, no limbs, "
        "not a human or humanoid. Cinematic lighting, 4K quality. Full body showing the energy form."
    ),
    "cybernetic": (
        "Sci-fi concept art of a cybernetic {role}. Personality traits: {traits}. {desc} "
        "Mechanical body, circuits, synthetic components, cinematic lighting, 4K quality. "
        "Full body or 3/4 view showing cybernetic anatomy."
    ),
    "symbiotic": (
        "Alien creature concept art of a symbiotic being {role}. Personality traits: {traits}. {desc} "
        "Composite organism, multiple life forms in one body, not a single humanoid body, no human face. "
        "Cinematic lighting, 4K quality. Full body view showing the composite nature."
    ),
}


def _fallback_avatar_prompt(role: str, traits: list[str], avatar_desc: str, species_desc: str, species_key: str) -> str:
    template = _FALLBACK_AVATAR_TEMPLATES.get(species_key, _FALLBACK_AVATAR_TEMPLATES["human"])
    desc = " ".join(x for x in (avatar_desc, species_desc) if x)
    return template.format(role=role, traits=", ".join(traits), desc=desc)


def _roll_species_and_gender(past_species: list[str]) -> dict[str, Any]:
    """Roll a random species/gender combination for a character proposal.

    Avoids repeating species already shown to this player while there is
    something else left to pick.
    """
    species_pool = [s for s in SPECIES_TAGS if s not in past_species] or list(SPECIES_TAGS)
    species_primary = random.choice(species_pool)

    species_secondary = ""
    species_hybrid = False
    if random.random() < SPECIES_HYBRID_CHANCE:
        species_secondary = random.choice([s for s in SPECIES_TAGS if s != species_primary])
        species_hybrid = True

    return {
        "species_primary": species_primary,
        "species_secondary": species_secondary,
        "species_hybrid": species_hybrid,
        "gender_primary": random.choice(GENDER_TAGS),
    }


async def _generate_character_proposal(
    player_id: int,
    game_id: str,
    language: str,
    past_roles: list[str],
    past_species: list[str],
) -> dict[str, Any]:
    """Generate one random character proposal.

    Rolls a free role + species + gender, produces all flavour text in a
    single LLM call, then renders the avatar via ComfyUI. Never repeats a
    role/species the player has already rejected while alternatives exist.
    """
    available = get_available_roles(game_id, language=language)
    if not available:
        raise ValueError("All crew positions are filled. No roles available.")

    role_pool = [r for r in available if r["role_key"] not in past_roles] or available
    role_data = random.choice(role_pool)
    assigned_key = role_data["role_key"]

    dice = _roll_species_and_gender(past_species)
    species_primary = dice["species_primary"]
    species_secondary = dice["species_secondary"]
    species_hybrid = dice["species_hybrid"]

    species_display = species_primary
    if species_hybrid:
        hybrid_key = f"{species_primary}+{species_secondary}"
        alt_hybrid = f"{species_secondary}+{species_primary}"
        species_display = get_hybrid_species_name(hybrid_key, language)
        if species_display == hybrid_key:
            species_display = get_hybrid_species_name(alt_hybrid, language)

    species_type_display = get_species_type_name(species_primary, language)
    gender_type_display = get_gender_type_name(dice["gender_primary"], language)
    species_secondary_display = get_species_type_name(species_secondary, language) if species_secondary else None

    gm = create_game_server(language=language)
    flavour = await gm.generate_character_flavour(
        role_key=assigned_key,
        role_name=role_data["role_name"],
        species_display=species_display,
        species_secondary=species_secondary_display,
        species_hybrid=species_hybrid,
        gender_display=gender_type_display,
        gender_secondary=None,
        gender_hybrid=False,
        game_id=game_id,
        player_id=str(player_id),
        turn=None,
    )
    traits = flavour["personality_traits"]

    # Avatar prompt via LLM; the combined description mirrors the pre-proposal
    # avatar pipeline (species type + gender + narrative appearance).
    avatar_prompt = ""
    try:
        parts = [flavour["avatar_description"]]
        if species_type_display:
            parts.append(f"Species type: {species_type_display}")
        # A human gender label is a strong humanoid prior that collapses alien
        # anatomy back into a person, so it is omitted for exotic body plans.
        if gender_type_display and species_primary not in ("non_humanoid", "energy", "symbiotic"):
            parts.append(f"Gender type: {gender_type_display}")
        if flavour["species_description"]:
            parts.append(f"Appearance: {flavour['species_description']}")
        avatar_prompt = await gm.generate_avatar_prompt(
            role=role_data["role_name"],
            traits=traits,
            avatar_description="\n".join(x for x in parts if x),
            species_category=species_primary,
            game_id=game_id,
            player_id=str(player_id),
            turn=None,
            kind="avatar_prompt",
        )
    except Exception as e:
        logger.warning(f"[PROPOSAL] Avatar prompt generation failed for player {player_id}: {e}")

    if not avatar_prompt:
        avatar_prompt = _fallback_avatar_prompt(
            role_data["role_name"], traits, flavour["avatar_description"], flavour["species_description"], species_primary
        )

    avatar_url = None
    try:
        image_generator = create_image_generator()
        avatar_url = await image_generator.generate_avatar_image(
            prompt=avatar_prompt,
            filename_prefix=f"{game_id}/avatar_{player_id}",
            width=768,
            height=1024,
            game_id=game_id,
            player_id=str(player_id),
            turn=None,
            kind="avatar",
        )
    except Exception as e:
        logger.error(f"[PROPOSAL] Avatar ComfyUI generation failed for player {player_id}: {type(e).__name__}: {e}", exc_info=True)

    return {
        "role_key": assigned_key,
        "role": role_data["role_name"],
        "role_name_en": role_data.get("role_name_en", ""),
        "role_description": flavour["role_description"],
        "avatar_description": flavour["avatar_description"],
        "personality_traits": traits,
        "species": species_type_display,
        "gender": gender_type_display,
        "species_description": flavour["species_description"],
        "species_secondary": species_secondary_display,
        "gender_secondary": None,
        "species_primary_key": species_primary,
        "avatar_url": avatar_url,
        "past_roles": past_roles + [assigned_key],
        "past_species": past_species + [species_primary],
    }


def _take_proposed_role(proposal: dict[str, Any], player_id: int, game_id: str, language: str) -> dict[str, Any]:
    """Reserve the proposed role for the player, falling back to any free role
    if it was taken by a concurrently-completing player."""
    assigned_key = proposal.get("role_key", "")
    role_data = get_role_by_key(assigned_key, language=language, game_id=game_id)
    if not role_data or role_data.get("taken_by") is not None:
        available = get_available_roles(game_id, language=language)
        if not available:
            raise ValueError("All crew positions are filled.")
        role_data = available[0]
        assigned_key = role_data["role_key"]

    if not take_role(assigned_key, player_id, game_id):
        available = get_available_roles(game_id, language=language)
        if not available:
            raise ValueError("All crew positions are filled.")
        role_data = available[0]
        take_role(role_data["role_key"], player_id, game_id)

    return role_data


def _profile_from_proposal(
    proposal: dict[str, Any],
    player_id: int,
    player_name: str,
    game_id: str,
    language: str,
) -> dict[str, Any]:
    """Build a player_profiles row from an accepted character proposal."""
    role_data = _take_proposed_role(proposal, player_id, game_id, language)
    logger.info(f"[ROLE] Player {player_id} accepted role: {role_data['role_name']} ({role_data['role_key']})")
    return {
        "player_id": player_id,
        "player_name": player_name,
        "avatar_description": proposal.get("avatar_description", ""),
        "avatar_url": proposal.get("avatar_url"),
        "role": role_data["role_name"],
        "role_name_en": role_data.get("role_name_en", ""),
        "role_description": proposal.get("role_description", ""),
        "personality_traits": proposal.get("personality_traits", []),
        "game_id": game_id,
        "species": proposal.get("species", ""),
        "gender": proposal.get("gender", ""),
        "species_description": proposal.get("species_description", ""),
        "species_secondary": proposal.get("species_secondary"),
        "gender_secondary": proposal.get("gender_secondary"),
        "species_primary_key": proposal.get("species_primary_key", ""),
    }


async def _finalize_onboarding_session(session: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    """Create the player profile from an accepted proposal and run post-join logic.

    Marks the session completed. Returns the /onboarding/{id}/complete payload.
    Safe to call again for an already-created profile (idempotent restore path).
    """
    session_id = session["session_id"]
    player_id = session["player_id"]
    answers_data = session.get("answers", {})
    game_id = session.get("game_id") or answers_data.get(-1) or answers_data.get("-1")
    language = session.get("language", "en")
    player_name = answers_data.get(-2) or answers_data.get("-2", "")

    profile = get_player_profile(player_id)
    if not profile:
        profile_data = _profile_from_proposal(proposal, player_id, player_name, game_id, language)
        create_player_profile(profile_data)
        profile = get_player_profile(player_id)
        if not profile:
            raise HTTPException(status_code=500, detail="Failed to create player profile")
        # If the player is returning (e.g. after /reset), deactivate any active
        # NPC that was created to replace them on a previous role — otherwise it
        # would duplicate them in the team roster and turn generation.
        ghosts = deactivate_replacement_npcs_for_player(player_id, game_id)
        if ghosts:
            logger.info(f"[ONBOARDING] Deactivated {ghosts} ghost NPC(s) for returning player {player_id} in game {game_id}")
        cleared_kicks = clear_kicks_for_returning_player(player_id, game_id)
        if cleared_kicks:
            logger.info(f"[ONBOARDING] Cleared {cleared_kicks} stale kick record(s) for returning player {player_id} in game {game_id}")
        update_onboarding_session(
            session_id,
            session.get("current_question", 0),
            answers_data,
            True,
            language,
            proposal,
        )

    # Check player count and start game if >= GAME_START_MIN_PLAYERS
    player_count = get_player_count_in_game(game_id)
    game_was_started = False
    if player_count >= GAME_START_MIN_PLAYERS:
        game_was_started = start_game(game_id)
        if game_was_started:
            # Register this game with the scheduler
            asyncio.create_task(_register_game_in_scheduler(game_id, None))
            # Auto-start skips _original_start_game, so generate the mission
            # (archetype + objectives) and the bridge image explicitly —
            # otherwise the game runs without them.
            game_lang = get_game_language(game_id)
            asyncio.create_task(_generate_started_game_assets(game_id, game_lang))

    game_started = is_game_started(game_id)

    # If joining an already-running game (this onboarding did not start it),
    # let the player inherit the current turn's NPC briefing so they can
    # participate immediately instead of waiting for the next generated turn.
    if game_started and not game_was_started:
        game_lang = get_game_language(game_id)
        asyncio.create_task(_inherit_npc_briefing_for_player(player_id, game_id, game_lang))

    all_players = get_players_in_game(game_id)
    other_players = [p for p in all_players if p != player_id]

    return {
        "status": "completed",
        "profile": profile,
        "avatar_url": profile.get("avatar_url"),
        "game_started": game_started,
        "game_just_started": game_was_started,
        "player_count": player_count,
        "other_player_ids": other_players,
        "game_title": get_game_title(game_id) or "",
        "language": get_game_language(game_id),
    }


# ============== FastAPI App ==============


async def _generate_loading_images():
    """Generate loading images in background at startup."""
    try:
        existing = get_game_image_count("loading", game_id="all", turn=None)
        total_needed = 5
        if existing >= total_needed:
            logger.info(f"[LOADING] {existing} loading images already in DB, skipping gen")
            return

        remaining = total_needed - existing
        logger.info(f"[LOADING] Generating {remaining} loading images (background)...")
        image_generator = create_image_generator()
        urls = await image_generator.generate_loading_images(count=remaining, start_index=existing, filename_prefix="loading", game_id="all", width=768, height=768)

        saved = 0
        for url in urls:
            if url:
                save_game_image(type="loading", image_url=url, game_id="all", turn=None, prompt="")
                saved += 1

        logger.info(f"[LOADING] Background gen: saved {saved}/{remaining} images")
    except Exception as e:
        logger.error(f"[LOADING] Background generation failed: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    logger.info("Game Server API starting up")
    init_db()
    logger.info("Database initialized and migrations run")

    # Generate loading images in background (non-blocking)
    asyncio.create_task(_generate_loading_images())

    # Resume any generation interrupted by a previous shutdown/crash.
    asyncio.create_task(_resume_interrupted_generations())

    # Backfill mission/archetype (and bridge/backgrounds) for started games that
    # never received them — e.g. the auto-start background task failed or was
    # killed by a restart before it finished.
    asyncio.create_task(_ensure_missions_for_started_games())

    yield
    logger.info("Game Server API shutting down")


GAME_SCHEDULER_URL = os.getenv("GAME_SCHEDULER_URL", "http://game-scheduler:8001")

# Mission generation resilience: every started game MUST end up with a mission
# (archetype). These control the per-call retry inside _generate_started_game_assets.
MISSION_MAX_ATTEMPTS = 3
MISSION_RETRY_DELAY = 2  # seconds, multiplied by attempt number (backoff)


async def _notify_scheduler(action: str, game_id: str) -> None:
    """Fire-and-forget notification to game-scheduler after a turn event."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GAME_SCHEDULER_URL}/scheduler/{action}",
                params={"game_id": game_id},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Scheduler notification '{action}' returned {resp.status}")
                else:
                    logger.info(f"Scheduler notified: {action}")
    except Exception as e:
        logger.warning(f"Failed to notify scheduler ({action}): {e}")


async def _register_game_in_scheduler(game_id: str, schedule: str | None) -> None:
    """Register a game with the scheduler, optionally with a specific schedule.

    When ``schedule`` is omitted the scheduler falls back to its env default.
    """
    try:
        body = {"schedule": schedule} if schedule else None
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GAME_SCHEDULER_URL}/scheduler/register/{game_id}",
                json=body,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    logger.info(f"Game '{game_id}' registered with scheduler")
                else:
                    logger.warning(f"Scheduler register returned {resp.status}")
    except Exception as e:
        logger.warning(f"Failed to register game '{game_id}' with scheduler: {e}", exc_info=True)


app = FastAPI(
    title="AI Game Server API",
    description="API for AI-powered cooperative game with Telegram bot interface",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS middleware — allows browser-based clients (Telegram Mini App) to call the API.
# - GAME_MASTER_API_URL: internal Docker URL (for development / self-reference)
# - CORS_ORIGIN: external/public URL for browser frontend (Telegram Mini App)
# Only browsers enforce CORS; backend services (telegram-bot, game-scheduler) don't need it.
cors_origins = [os.getenv("GAME_MASTER_API_URL", "http://game-server:8000")]
extra_cors = os.getenv("CORS_ORIGIN", "")
if extra_cors:
    cors_origins.append(extra_cors)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============== API Endpoints ==============


@app.get("/")
async def root():
    return {"service": "AI Game Server API", "status": "running", "version": "2.0.0"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# ============== Onboarding endpoints ==============


# Per-player async lock to prevent concurrent background onboarding image tasks
_onboarding_image_locks: dict[str, asyncio.Lock] = {}


def _get_onboarding_image_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _onboarding_image_locks:
        _onboarding_image_locks[session_id] = asyncio.Lock()
    return _onboarding_image_locks[session_id]


async def _background_character_proposal(
    player_id: int,
    game_id: str,
    session_id: str,
    language: str,
    game_title_data: dict,
    needs_splash: bool,
    forced: bool,
) -> None:
    """Background task: generate splash (first proposal only) + a random
    character proposal, persist it, then push the card to the telegram-bot
    via /push/onboarding-ready.

    Acquires a per-session lock so a spam of "no" presses cannot produce
    concurrent generations for one player. When ``forced`` is set (the player
    rejected three proposals), the generated character is assigned
    immediately: the session completes here and the push carries the
    completion payload instead of yes/no buttons.
    """
    lock = _get_onboarding_image_lock(session_id)
    async with lock:
        logger.info(f"[ONBOARDING_BG] Starting background proposal for player {player_id} (session {session_id}, forced={forced})")
        bg_start = datetime.now()

        try:
            session = get_onboarding_session(session_id)
            if not session:
                logger.warning(f"[ONBOARDING_BG] Session {session_id} not found, aborting proposal generation")
                return
            if session.get("completed"):
                logger.info(f"[ONBOARDING_BG] Session {session_id} already completed, skipping stale generation")
                return

            current = session.get("proposal") or {}
            past_roles = list(current.get("past_roles", []))
            past_species = list(current.get("past_species", []))

            # Splash images accompany the FIRST proposal only.
            if needs_splash:
                title_for_prompt = game_title_data.get("title", "")
                welcome_for_prompt = game_title_data.get("welcome_text", "")
                try:
                    logger.info(f"[ONBOARDING_BG] Generating 3 splash images for {title_for_prompt}...")
                    cg = create_image_generator()
                    urls = await cg.generate_splash_images(
                        game_title=title_for_prompt,
                        welcome_text=welcome_for_prompt,
                        count=3,
                        filename_prefix="splash",
                        game_id=game_id,
                        width=1024,
                        height=768,
                    )
                    saved = 0
                    for url in urls:
                        if url:
                            save_game_image(type="splash", image_url=url, game_id=game_id, turn=None, prompt="")
                            saved += 1
                    logger.info(f"[ONBOARDING_BG] Saved {saved}/3 splash images")
                except Exception as e:
                    logger.error(f"[ONBOARDING_BG] Splash generation failed: {e}", exc_info=True)

            proposal = await _generate_character_proposal(
                player_id=player_id,
                game_id=game_id,
                language=language,
                past_roles=past_roles,
                past_species=past_species,
            )

            completion_payload = None
            if forced:
                completion_payload = await _finalize_onboarding_session(
                    get_onboarding_session(session_id) or session,
                    proposal,
                )
            else:
                update_onboarding_session(
                    session_id,
                    session.get("current_question", 0),
                    session.get("answers", {}),
                    False,
                    language,
                    proposal,
                )

            try:
                from push_client import push_onboarding_ready as _push_ready

                success = await _push_ready(
                    player_id=player_id,
                    game_id=game_id,
                    session_id=session_id,
                    proposal=proposal,
                    game_title=game_title_data.get("title", "") if needs_splash else "",
                    welcome_message=game_title_data.get("welcome_text", "") if needs_splash else "",
                    language=language,
                    final=forced,
                    completion=completion_payload,
                )
                logger.info(f"[ONBOARDING_BG] Push proposal {'succeeded' if success else 'FAILED'} for player {player_id}")
            except Exception as e:
                logger.error(f"[ONBOARDING_BG] Push proposal failed for player {player_id}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"[ONBOARDING_BG] Background proposal generation failed for player {player_id}: {e}", exc_info=True)

        bg_time = (datetime.now() - bg_start).total_seconds()
        logger.info(f"[ONBOARDING_BG] Proposal for player {player_id} took {bg_time:.2f} seconds")


@app.post("/onboarding/start")
async def start_onboarding(request: StartOnboardingRequest):
    """Start a new onboarding session for a player.

    Creates the session and kicks off a background task that rolls a random
    character (role + species + gender + flavour + avatar) and pushes the
    proposal card to the bot. Returns immediately.
    """
    start_time = datetime.now()
    logger.info("=== START ONBOARDING ===")
    logger.info(f"player_id: {request.player_id}, game_id: {request.game_id}, language: {request.language}")

    # Check if player already has a profile.
    # Allow re-onboarding when joining a different game or when their previous
    # game has ended. Block re-onboarding into the same still-active game,
    # including for dead/spectator/replaced players (they must not be revived
    # into a game whose current turn was generated without them).
    existing_profile = get_player_profile(request.player_id)

    if existing_profile:
        old_game_id = existing_profile.get("game_id", "")
        allow_reset, reason = should_reset_profile_for_reonboarding(existing_profile, request.game_id)
        if allow_reset:
            logger.info(f"Player {request.player_id} has a profile from {reason} game {old_game_id}. Deleting old profile and allowing re-onboarding.")
            delete_player_profile(request.player_id)
        elif reason == "already_played_same_game":
            logger.info(f"Player {request.player_id} already played (died/replaced) in active game {request.game_id}; re-onboarding blocked.")
            raise HTTPException(status_code=400, detail="Player already played in this game")
        else:
            logger.warning(f"Player {request.player_id} already has an active profile in game {old_game_id}")
            raise HTTPException(status_code=400, detail="Player already has a profile")

    # Check if the game is already full
    current_count = get_player_count_in_game(request.game_id)
    if current_count >= GAME_START_MAX_PLAYERS:
        raise HTTPException(
            status_code=400,
            detail=(f"Game is full ({current_count}/{GAME_START_MAX_PLAYERS} players). No more players can join at this time."),
        )

    # Generate the linked game concept (mission + title + welcome) once per
    # game. Idempotent and concurrency-safe (per-game lock + uq_game_mission
    # index), so this only blocks for the very first player of a game.
    concept = await _generate_game_concept(request.game_id, request.language)
    game_title_data = {
        "title": concept["title"],
        "welcome_text": concept["welcome_text"],
    }

    session = create_onboarding_session(request.player_id, request.language)
    session_id = session["session_id"]

    # The onboarding_sessions table has no game_id column; keep it in the
    # answers payload alongside the player name (same convention as before).
    metadata = {
        -1: request.game_id,
        -2: request.player_name,
    }
    update_onboarding_session(session_id, 0, metadata, False, request.language, None)
    logger.info(f"Onboarding session created: {session_id}")

    gen_time = (datetime.now() - start_time).total_seconds()
    logger.info(f"Fast onboarding start took {gen_time:.2f} seconds")

    needs_splash = get_game_image_count("splash", request.game_id, None) < 3
    asyncio.create_task(
        _background_character_proposal(
            player_id=request.player_id,
            game_id=request.game_id,
            session_id=session_id,
            language=request.language,
            game_title_data=game_title_data,
            needs_splash=needs_splash,
            forced=False,
        )
    )

    logger.info("=== START ONBOARDING COMPLETED (fast return) ===")
    return {
        "session_id": session_id,
        "game_id": request.game_id,
        "game_title": game_title_data.get("title", ""),
        "welcome_message": game_title_data.get("welcome_text", ""),
        "pending_images": True,
    }


@app.post("/onboarding/{session_id}/reroll")
async def reroll_onboarding_character(session_id: str):
    """Reject the current character proposal and generate another one.

    The compare-and-set on current_question (the rejection counter) rejects
    duplicate button presses racing while a proposal is still generating.
    After ONBOARDING_MAX_REROLLS rejections the next character is generated
    and assigned automatically (forced).
    """
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["completed"]:
        raise HTTPException(status_code=400, detail="Onboarding already completed")

    if not session.get("proposal"):
        raise HTTPException(status_code=409, detail="No character proposal to reject yet")

    if not reserve_onboarding_slot(session_id, session["current_question"]):
        logger.info(f"[ONBOARDING] Duplicate reroll race rejected for session={session_id}")
        raise HTTPException(status_code=409, detail="Duplicate reroll request")

    rejections = session["current_question"] + 1
    forced = rejections >= ONBOARDING_MAX_REROLLS

    answers_data = session.get("answers", {})
    game_id = session.get("game_id") or answers_data.get(-1) or answers_data.get("-1")
    if not game_id:
        raise HTTPException(status_code=400, detail="Session has no game_id")
    language = session.get("language", "en")

    logger.info(f"[ONBOARDING] Player {session['player_id']} rejected proposal {rejections}/{ONBOARDING_MAX_REROLLS} (forced={forced})")

    asyncio.create_task(
        _background_character_proposal(
            player_id=session["player_id"],
            game_id=game_id,
            session_id=session_id,
            language=language,
            game_title_data={},
            needs_splash=False,
            forced=forced,
        )
    )

    return {"rejections": rejections, "forced": forced}


async def _generate_game_concept(game_id: str, language: str) -> dict:
    """Thin wrapper over game_concept.generate_game_concept, passing the
    main-configured retry parameters."""
    return await generate_game_concept(
        game_id,
        language,
        max_attempts=MISSION_MAX_ATTEMPTS,
        retry_delay=MISSION_RETRY_DELAY,
    )


async def _generate_background_library(
    game_id: str,
    mission_data: dict,
    all_participants: list[dict],
    gm,
    language: str,
) -> None:
    """Pre-generate empty-location backgrounds for a game (best-effort).

    Generates one background per canonical location type (bridge, engineering,
    sickbay, ...) via Z-Image Turbo, then stores each under
    ``game_images.type = "background_{location}"`` so scene generation can look
    them up by location. Safe to call repeatedly: existing backgrounds are
    skipped. Failures are logged and do not abort the caller.
    """
    from prompts import BACKGROUND_LOCATION_TYPES

    existing = {loc for loc in BACKGROUND_LOCATION_TYPES if get_random_game_image(type=f"background_{loc}", game_id=game_id, turn=None)}
    if existing:
        logger.info("[BACKGROUND] %d/%d backgrounds already exist for game %s, skipping", len(existing), len(BACKGROUND_LOCATION_TYPES), game_id)
        return

    try:
        prompts_by_loc = await gm.generate_background_prompts(mission_data, all_participants, language=language, game_id=game_id, player_id=None, turn=None, kind="background_prompts")
    except Exception:
        logger.error("[BACKGROUND] Prompt generation failed for game %s", game_id, exc_info=True)
        return
    if not prompts_by_loc:
        logger.warning("[BACKGROUND] No prompts returned for game %s", game_id)
        return

    image_gen = create_image_generator()
    for loc, prompt in prompts_by_loc.items():
        try:
            url = await image_gen.generate_background_image(prompt=prompt, location_type=loc, game_id=game_id, width=1024, height=576)
            if url:
                save_game_image(type=f"background_{loc}", image_url=url, prompt=prompt, game_id=game_id, turn=None)
                logger.info("[BACKGROUND] Generated %s for game %s: %s", loc, game_id, url)
        except Exception:
            logger.error("[BACKGROUND] Failed to generate %s for game %s", loc, game_id, exc_info=True)


async def _generate_started_game_assets(game_id: str, language: str) -> None:
    """Generate and persist bridge image + background library for an
    auto-started game.

    The mission (and title/welcome) is produced earlier, by
    ``_generate_game_concept`` at onboarding start. This function only fills
    in the remaining per-game visuals that depend on the crew composition,
    which is only known once the game starts. Guarded by the per-game concept
    lock so the startup sweep cannot race an in-flight call. Safe to call
    repeatedly: each asset is generated only when missing.
    """
    lock = get_game_concept_lock(game_id)
    async with lock:
        try:
            all_participants = []
            for pid in get_players_in_game(game_id):
                p = get_player_profile(pid)
                if p:
                    all_participants.append(
                        {
                            "type": "player",
                            "player_id": pid,
                            "player_name": p.get("player_name", "") or "",
                            "role": p["role"],
                            "species": p.get("species", ""),
                            "personality_traits": p.get("personality_traits", []),
                            "role_description": p.get("role_description", ""),
                            "avatar_description": _extract_avatar_prompt(p.get("avatar_description", "") or ""),
                            "species_description": p.get("species_description", "") or "",
                        }
                    )
            for npc in get_all_active_npcs(game_id):
                all_participants.append(
                    {
                        "type": "npc",
                        "npc_key": npc["npc_key"],
                        "npc_name": npc.get("npc_name", npc.get("role", "NPC")),
                        "role": npc["role"],
                        "species": npc.get("species", ""),
                        "personality_traits": npc.get("personality_traits", []),
                        "role_description": npc.get("role_description", ""),
                        "avatar_description": _extract_avatar_prompt(npc.get("avatar_description", "") or ""),
                    }
                )
            if not all_participants:
                return
            gm = create_game_server(language=language)

            # Mission is created at game concept time; backfill it here only for
            # legacy games that predate the concept pipeline. We already hold
            # the per-game concept lock here, so generate the mission directly
            # (without re-entering _generate_game_concept, which would deadlock
            # on the same non-reentrant lock).
            mission_data = get_mission(None, game_id=game_id)
            if not mission_data:
                try:
                    mission_data = await gm.generate_mission(game_id=game_id, player_id=None, turn=None, kind="mission")
                    create_mission(mission_data, game_id)
                    logger.info("[MISSION] Backfilled mission for legacy game %s: %s", game_id, mission_data.get("name", ""))
                except Exception:
                    logger.error("[MISSION] Backfill failed for legacy game %s", game_id, exc_info=True)
                mission_data = get_mission(None, game_id=game_id)

            # Pre-generate empty-location backgrounds (used as backdrops for Qwen-Image-Edit scene compositing)
            try:
                await _generate_background_library(game_id, mission_data or {}, all_participants, gm, language)
            except Exception:
                logger.error(f"[BACKGROUND] Library generation failed for auto-started game {game_id}", exc_info=True)

            # Bridge image (needs the mission for crew positioning context)
            if not get_random_game_image(type="bridge", game_id=game_id, turn=None):
                try:
                    bridge_result = await gm.generate_bridge_image_prompt(mission_data or {}, all_participants, game_id=game_id, player_id=None, turn=None, kind="bridge_image_prompt")
                    bridge_prompt = bridge_result.get("bridge_prompt", "")
                    if bridge_prompt:
                        image_gen = create_image_generator()
                        bridge_url = await image_gen.generate_scene_image(prompt=bridge_prompt, filename_prefix=f"{game_id}/bridge", width=1024, height=1024, game_id=game_id, player_id=None, turn=None, kind="bridge")
                        if bridge_url:
                            save_game_image(type="bridge", image_url=bridge_url, prompt=bridge_prompt, game_id=game_id, turn=None)
                            logger.info(f"[BRIDGE] Generated bridge image for auto-started game {game_id}: {bridge_url}")
                except Exception:
                    logger.error(f"[BRIDGE] Failed to generate bridge image for auto-started game {game_id}", exc_info=True)
        except Exception:
            logger.error(f"[MISSION] Failed to generate mission for auto-started game {game_id}", exc_info=True)


async def _ensure_missions_for_started_games() -> None:
    """Backfill missing mission/archetype (and other start assets) for started games.

    Runs at startup. The onboarding auto-start path generates the mission + bridge
    image + backgrounds in a fire-and-forget background task; if that task failed
    or was killed by a restart, the game ends up started-but-mission-less and the
    archetype never appears in /gm_list. This sweep re-runs
    _generate_started_game_assets for any such game. That function is idempotent
    (each asset is only generated when missing), so repeated runs are safe.
    """
    try:
        games = get_all_games()
        orphaned = []
        for game in games:
            if not game.get("started"):
                continue
            if game.get("status") != "active":
                continue
            game_id = game["game_id"]
            if get_mission(None, game_id=game_id) is None:
                orphaned.append(game_id)
        if not orphaned:
            return
        logger.info(f"[MISSION] Startup sweep: {len(orphaned)} started game(s) missing a mission, regenerating: {orphaned}")
        for game_id in orphaned:
            language = get_game_language(game_id)
            try:
                await _generate_started_game_assets(game_id, language)
            except Exception:
                logger.error(f"[MISSION] Startup sweep failed to generate mission for game {game_id}", exc_info=True)
    except Exception:
        logger.error("[MISSION] Startup sweep for missing missions failed", exc_info=True)


@app.post("/onboarding/{session_id}/complete")
async def complete_onboarding(session_id: str):
    """Accept the proposed character and finish onboarding.

    Builds the player profile from the stored proposal (the avatar was already
    generated with it), runs post-join logic (game start, briefing inherit)
    and returns the completion payload. Idempotent: calling it for an already
    completed session just re-derives the payload.
    """
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    proposal = session.get("proposal")
    if not proposal:
        raise HTTPException(status_code=400, detail="No character proposal yet")

    answers_data = session.get("answers", {})
    game_id = session.get("game_id") or answers_data.get(-1) or answers_data.get("-1")
    if not game_id:
        raise HTTPException(status_code=400, detail="Session has no game_id")

    return await _finalize_onboarding_session(session, proposal)


def update_player_profile_avatar(player_id: int, avatar_url: str) -> bool:
    """Update player profile with avatar URL"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE player_profiles SET avatar_url = ? WHERE player_id = ?""",
        (avatar_url, player_id),
    )

    conn.commit()
    conn.close()
    return True


async def _generate_player_avatar(player_id: int, game_id: str, language: str) -> str | None:
    """Generate avatar for an existing player. Returns avatar_url or None."""
    profile = get_player_profile(player_id)
    if not profile:
        logger.warning(f"[AVATAR] Player {player_id} not found, cannot generate avatar")
        return None

    # Step 1: Generate avatar prompt (LLM) with fallback to template
    avatar_prompt = ""
    try:
        game_server = create_game_server(language=language)

        species_desc = profile.get("species_description") or ""
        species_type = profile.get("species", "") or ""
        gender_type = profile.get("gender", "") or ""
        species_primary = profile.get("species_primary_key") or ""
        if species_desc or species_type or gender_type:
            parts = [profile.get("avatar_description", "")]
            if species_type:
                parts.append(f"Species type: {species_type}")
            # A human gender label (Male/Female) is a strong humanoid prior that
            # collapses alien anatomy back into a person, so for non-humanoid,
            # energy, and symbiotic beings we let the LLM invent a fitting
            # non-human biological identity instead of passing the gender through.
            if gender_type and species_primary not in ("non_humanoid", "energy", "symbiotic"):
                parts.append(f"Gender type: {gender_type}")
            if species_desc:
                parts.append(f"Appearance: {species_desc}")
            avatar_description_combined = "\n".join(parts)
        else:
            avatar_description_combined = profile.get("avatar_description", "")

        avatar_prompt = await game_server.generate_avatar_prompt(
            role=profile["role"],
            traits=profile["personality_traits"],
            avatar_description=avatar_description_combined,
            species_category=profile.get("species_primary_key") or "",
            game_id=game_id,
            player_id=str(player_id),
            turn=None,
            kind="avatar_prompt",
        )
        logger.info(f"[AVATAR] LLM prompt for player {player_id}: {avatar_prompt}...")
    except Exception as e:
        logger.warning(f"[AVATAR] LLM prompt generation failed for player {player_id}: {e}")

    # Step 2: Use LLM prompt or build fallback
    if not avatar_prompt:
        traits_str = ", ".join(profile.get("personality_traits", []))
        species_desc = profile.get("species_description", "")
        species_type = profile.get("species", "") or ""
        gender_type = profile.get("gender", "") or ""
        avatar_desc = profile.get("avatar_description", "")
        combined_desc = f"{avatar_desc} {species_type} {gender_type} {species_desc}".lower()

        species_cat = "human"
        cat_keywords = {
            "energy": [
                "energy being",
                "энергетическ",
                "plasma",
                "energy field",
                "gaseous",
                "frequency",
                "resonance",
                "light being",
            ],
            "cybernetic": [
                "cybernetic",
                "кибернетическ",
                "robotic",
                "mechanical",
                "synthetic",
                "machine",
                "android",
                "cyborg",
                "digital",
            ],
            "symbiotic": [
                "symbiotic",
                "симбиотическ",
                "symbiont",
                "composite",
                "multiple beings",
                "host",
                "union",
                "collective",
            ],
            "non_humanoid": [
                "non_humanoid",
                "негуманоид",
                "tentacle",
                "carapace",
                "exoskeleton",
                "crystalline",
                "кристаллическ",
                "щупальц",
                "панцирь",
                "экзоскелет",
                "бесформенн",
                "amorphous",
                "alien anatomy",
                "multiple limb",
            ],
            "humanoid": ["humanoid", "гуманоид"],
        }
        for cat, keywords in cat_keywords.items():
            if any(kw in combined_desc for kw in keywords):
                species_cat = cat
                break

        fallback_templates = {
            "human": (f"Sci-fi character portrait of a {profile['role']} in Star Trek style. Personality traits: {traits_str}. {avatar_desc} Futuristic uniform, cinematic lighting, detailed face, 4K quality. Portrait, upper body, space opera aesthetic."),
            "humanoid": (
                f"Sci-fi character portrait of a humanoid {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"{avatar_desc} "
                f"{species_desc} "
                f"Humanoid with subtle alien features, futuristic uniform, "
                f"cinematic lighting, detailed face, 4K quality. "
                f"Portrait, upper body, space opera aesthetic."
            ),
            "non_humanoid": (
                f"Alien creature concept art of a non-humanoid {profile['role']}. "
                f"Personality traits: {traits_str}. "
                f"Creature form: {avatar_desc} "
                f"{species_desc} "
                f"The creature is NOT a human or humanoid: no two arms ending in hands, "
                f"no two legs, no human face or hair, not a bipedal silhouette, no uniform. "
                f"Cinematic lighting, 4K quality, detailed alien biology. "
                f"Full body or 3/4 view showing the alien physiology."
            ),
            "energy": (
                f"Abstract energy-being concept art of an energy being {profile['role']}. "
                f"Personality traits: {traits_str}. "
                f"Form: {avatar_desc} "
                f"{species_desc} "
                f"Glowing plasma energy form, luminous, ethereal, no solid body, "
                f"no face, no limbs, not a human or humanoid. "
                f"Cinematic lighting, 4K quality. "
                f"Full body showing the energy form."
            ),
            "cybernetic": (
                f"Sci-fi concept art of a cybernetic {profile['role']}. "
                f"Personality traits: {traits_str}. "
                f"Form: {avatar_desc} "
                f"{species_desc} "
                f"Mechanical body, circuits, synthetic components, "
                f"cinematic lighting, 4K quality. "
                f"Full body or 3/4 view showing cybernetic anatomy."
            ),
            "symbiotic": (
                f"Alien creature concept art of a symbiotic being {profile['role']}. "
                f"Personality traits: {traits_str}. "
                f"Form: {avatar_desc} "
                f"{species_desc} "
                f"Composite organism, multiple life forms in one body, "
                f"not a single humanoid body, no human face. "
                f"Cinematic lighting, 4K quality. "
                f"Full body view showing the composite nature."
            ),
        }
        avatar_prompt = fallback_templates.get(species_cat, fallback_templates["human"])
        logger.info(f"[AVATAR] Using fallback prompt ({species_cat}) for player {player_id}: {avatar_prompt}...")

    # Step 3: Call ComfyUI to generate the avatar
    avatar_url = None
    try:
        image_generator = create_image_generator()
        logger.info(f"[AVATAR] Calling ComfyUI at {image_generator.comfyui_url} for avatar generation")
        avatar_url = await image_generator.generate_avatar_image(
            prompt=avatar_prompt,
            filename_prefix=f"{game_id}/avatar_{player_id}",
            width=768,
            height=1024,
            game_id=game_id,
            player_id=str(player_id),
            turn=None,
            kind="avatar",
        )

        if avatar_url:
            logger.info(f"[AVATAR] URL received for player {player_id}: {avatar_url}")
            update_player_profile_avatar(player_id, avatar_url)
        else:
            logger.warning(f"[AVATAR] ComfyUI returned None for player {player_id}")

    except Exception as e:
        logger.error(f"[AVATAR] ComfyUI generation failed for player {player_id}: {type(e).__name__}: {e}", exc_info=True)

    return avatar_url


@app.post("/players/{player_id}/generate-avatar")
async def generate_player_avatar_endpoint(player_id: int):
    """Generate avatar for an existing player who doesn't have one yet"""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    if profile.get("avatar_url"):
        return {"status": "already_exists", "avatar_url": profile["avatar_url"]}

    game_id = profile.get("game_id")
    if not game_id:
        raise HTTPException(status_code=400, detail="Player is not in any game")
    language = get_game_language(game_id)
    avatar_url = await _generate_player_avatar(player_id, game_id, language)

    if avatar_url:
        return {"status": "generated", "avatar_url": avatar_url}
    else:
        return {"status": "failed", "avatar_url": None}


@app.get("/onboarding/{session_id}")
async def get_onboarding_status(session_id: str):
    """Get onboarding session status: the pending character proposal (if any)."""
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    answers_data = session.get("answers", {})
    session_game_id = session.get("game_id") or answers_data.get(-1) or answers_data.get("-1")
    if not session_game_id:
        raise HTTPException(status_code=400, detail="Session has no game_id")

    return {
        "session_id": session["session_id"],
        "game_id": session_game_id,
        "rejections": session["current_question"],
        "completed": session["completed"],
        "proposal": session.get("proposal"),
    }


# ============== Player profile endpoints ==============


@app.get("/players/{player_id}/profile")
async def get_player_profile_endpoint(player_id: int):
    """Get player profile"""
    try:
        profile = get_player_profile(player_id)
        if not profile:
            raise HTTPException(
                status_code=404,
                detail="Player profile not found. Complete onboarding first.",
            )
        return profile
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid player ID format: {str(e)}") from e


# ============== Game state endpoints ==============


@app.get("/game/title")
async def get_game_title_endpoint(game_id: str):
    """Get game title"""
    title = get_game_title(game_id)
    if not title:
        raise HTTPException(status_code=404, detail="Game title not found")
    return {"game_id": game_id, "title": title}


@app.get("/game/state")
async def get_game_state_endpoint(game_id: str):
    """Get current game state"""
    return get_game_state(game_id)


@app.get("/game/finale")
async def get_game_finale_endpoint(game_id: str):
    """Get the game-over finale narrative (if game ended).

    Returns 404 if the game is still active or no finale was generated.
    """
    state = get_game_state(game_id)
    if state["status"] == "active":
        raise HTTPException(status_code=404, detail="Game is still active")
    if not state.get("finale_narrative"):
        raise HTTPException(status_code=404, detail="No finale generated yet")
    return {
        "game_id": game_id,
        "status": state["status"],
        "finale_narrative": state["finale_narrative"],
        "finale_outcome_type": state["finale_outcome_type"],
        "finale_image_url": state["finale_image_url"],
    }


@app.get("/game/started")
async def get_game_started_endpoint(game_id: str):
    """Check if game has started (>= 3 players joined)"""
    started = is_game_started(game_id)
    player_count = get_player_count_in_game(game_id)
    language = get_game_language(game_id)
    return {"game_id": game_id, "started": started, "player_count": player_count, "language": language}


@app.get("/game/status")
async def get_game_status_endpoint(game_id: str):
    """Get game status: players, NPCs, their current choices, alive/dead."""
    state = get_game_state(game_id)
    title = get_game_title(game_id) or ""

    current_turn_num = max(1, state["turn"] - 1)

    # Real players
    player_ids = get_players_in_game(game_id)
    players_list = []
    for pid in player_ids:
        p = get_player_profile(pid)
        if not p:
            continue
        # Check if they have a pending choice for the current turn
        briefing = get_player_briefing(current_turn_num, pid, game_id)
        has_chosen = briefing is not None and briefing.get("selected_action_id") is not None
        chosen_action_text = ""
        if briefing and briefing.get("selected_action_id"):
            for c in briefing.get("choices", []):
                if c.get("id") == briefing["selected_action_id"]:
                    chosen_action_text = c.get("text", c.get("description", ""))
                    break

        players_list.append(
            {
                "player_id": pid,
                "player_name": p.get("player_name", "") or str(pid),
                "role": p.get("role", ""),
                "species": p.get("species", ""),
                "is_dead": bool(p.get("is_dead", False)),
                "is_spectator": bool(p.get("is_spectator", False)),
                "has_chosen": has_chosen,
                "chosen_action": chosen_action_text,
            }
        )

    # NPCs — include both active and inactive (dead) NPCs
    npcs_list = []
    for npc in get_all_npcs(game_id):
        npc_key = npc["npc_key"]
        all_briefings = get_all_briefings_for_turn(current_turn_num, game_id)
        chosen_action_text = ""
        for b in all_briefings:
            if b.get("npc_key") == npc_key and b.get("selected_action_id"):
                for c in b.get("choices", []):
                    if c.get("id") == b["selected_action_id"]:
                        chosen_action_text = c.get("text", c.get("description", ""))
                        break
                break

        npcs_list.append(
            {
                "npc_key": npc_key,
                "npc_name": npc.get("npc_name", npc_key),
                "role": npc.get("role", ""),
                "replaces_player_id": npc.get("replaces_player_id"),
                "chosen_action_text": chosen_action_text,
                "is_dead": not npc.get("is_active", True),
            }
        )

    mission = get_mission(None, game_id=game_id)
    return {
        "game_id": game_id,
        "title": title,
        "turn": state["turn"],
        "current_turn": current_turn_num,
        "status": state["status"],
        "ship_alive": state["ship_alive"],
        "hull_integrity": state["hull_integrity"],
        "shields": state["shields"],
        "game_started": is_game_started(game_id),
        "mission_name": mission.get("name", "") if mission else "",
        "archetype": mission.get("archetype", "") if mission else "",
        "player_count": len(players_list),
        "alive_count": sum(1 for pl in players_list if not pl["is_dead"]),
        "npc_count": len(npcs_list),
        "npc_alive_count": sum(1 for n in npcs_list if not n["is_dead"]),
        "players": players_list,
        "npcs": npcs_list,
    }


@app.get("/game/turn/{turn_num}")
async def get_game_turn_endpoint(turn_num: int, game_id: str):
    """Get specific turn's episode"""
    turn_data = get_game_turn(turn_num, game_id)
    if not turn_data:
        raise HTTPException(status_code=404, detail="Turn not found")
    return turn_data


@app.get("/game/current-turn")
async def get_current_game_turn(game_id: str):
    """Get current game turn

    Game state tracks the NEXT turn to generate, so the latest
    completed turn is state["turn"] - 1. For example:
    - Before any generation: state["turn"] = 1, no turns exist
    - After turn 1 generation: state["turn"] = 2, game_turn[1] exists
    """
    state = get_game_state(game_id)
    current_turn_num = max(1, state["turn"] - 1)
    turn_data = get_game_turn(current_turn_num, game_id)
    if not turn_data:
        raise HTTPException(status_code=404, detail="No game turn generated yet")
    return turn_data


@app.get("/games/current-turns")
async def get_all_current_turns():
    """Get current turn for every ACTIVE game.

    Returns a dict {game_id: current_turn} where current_turn is the
    latest completed turn (game_state.turn - 1, minimum 1).

    Ended games (mission_complete / ship_destroyed / crew_wiped / ...) are
    excluded: the telegram-bot uses this map to retry failed push_queue
    deliveries for the *current* turn on startup (reset_failed_for_current_turn).
    Including ended games here resurrected stale failed rows from a dead epoch
    on every bot restart and re-delivered them (e.g. итоги хода 10 of a game
    that had already ended).
    """
    games = get_all_games()
    result: dict[str, int] = {}
    for g in games:
        state = get_game_state(g["game_id"])
        if state.get("status") != "active":
            continue
        result[g["game_id"]] = max(1, state["turn"] - 1)
    return result


@app.get("/game/poll/{player_id}")
async def poll_game_updates(player_id: int, since: str | None):
    """Poll for new game updates (turns, actions, messages) since last poll"""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    game_id = profile.get("game_id")
    if not game_id:
        raise HTTPException(status_code=400, detail="Player is not in any game")

    # Get last poll timestamp
    last_poll = since or profile.get("last_poll")

    updates = {
        "new_game_turn": None,
        "pending_actions": [],
        "personal_briefing": None,
        "messages_from_gm": [],
        "npc_messages": [],
    }

    try:
        # Check for current turn with pending actions
        # Game state tracks NEXT turn to generate, so latest completed turn is state["turn"] - 1
        state = get_game_state(game_id)
        current_turn_num = max(1, state["turn"] - 1)

        # First, check player_briefings for per-player content
        briefing = get_player_briefing(current_turn_num, player_id, game_id)

        if briefing and briefing.get("choices"):
            # Safety check: only return briefing if game_turn record exists
            # (prevents race condition where briefings are saved before game_turn)
            turn_record = get_game_turn(current_turn_num, game_id)
            if turn_record is None:
                logger.debug(f"[POLL] Skipping briefing for player {player_id} turn {current_turn_num}: game_turn not yet created")
            elif not briefing.get("selected_action_id"):
                # Player hasn't chosen yet — return their briefing
                # Get scene image for this turn
                scene_url = get_random_game_image(type="scene", game_id=game_id, turn=current_turn_num)
                # Also fetch NPC dialogues for crew behavior context
                turn_record = get_game_turn(current_turn_num, game_id)
                crew_dialogues = turn_record["crew_dialogues"] if turn_record else []
                updates["personal_briefing"] = {
                    "briefing": briefing["briefing"],
                    "choices": briefing["choices"],
                    "chosen_action_url": briefing.get("chosen_action_url"),
                    "briefing_image_url": scene_url,
                    "crew_dialogues": crew_dialogues,
                }
                updates["pending_actions"] = briefing["choices"]
                updates["new_game_turn"] = {
                    "turn": current_turn_num,
                    "briefing": briefing["briefing"],
                    "crew_dialogues": [],
                }
        else:
            # Fall back to legacy game_turns player_actions
            turn_data = get_game_turn(current_turn_num, game_id)
            if turn_data and turn_data.get("player_actions"):
                player_actions = get_player_actions(player_id, current_turn_num)
                if not player_actions:
                    updates["pending_actions"] = turn_data["player_actions"]
                    updates["new_game_turn"] = {
                        "turn": turn_data["turn"],
                        "story": turn_data.get("global_circumstances") or turn_data["story"],
                        "crew_dialogues": turn_data["crew_dialogues"],
                    }

        # Get recent messages from Game Master
        messages = get_game_messages(player_id, limit=10)
        if last_poll:
            messages = [m for m in messages if m.get("timestamp", "") > last_poll]
        updates["messages_from_gm"] = messages

        # Update last poll timestamp
        update_player_profile_last_poll(player_id, datetime.now().isoformat())

    except Exception as e:
        logger.error(f"Poll failed for player {player_id}: {e}", exc_info=True)

    return updates


# ============== Player action endpoints ==============


@app.post("/game/actions")
async def submit_player_action(request: PlayerActionRequest):
    """Submit player's action selection"""
    profile = get_player_profile(request.player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")
    if profile.get("is_dead") or profile.get("is_spectator"):
        raise HTTPException(status_code=403, detail="Dead/spectator players cannot submit actions")
    game_id = profile.get("game_id")
    if not game_id:
        raise HTTPException(status_code=400, detail="Player is not in any game")
    briefing = get_player_briefing(request.turn, request.player_id, game_id)

    if briefing and briefing.get("choices"):
        # New system: validate against briefing choices — does NOT require game_turn
        # (game_turn may not exist yet if briefings were saved before game_turn record)
        valid_ids = [c["id"] for c in briefing["choices"]]
        if request.action_id not in valid_ids:
            raise HTTPException(status_code=400, detail=f"Invalid action ID. Valid: {valid_ids}")

        # Find the consequence for the chosen action
        chosen_consequence = ""
        chosen_consequence_kind = ""
        chosen_action_text = ""
        for c in briefing["choices"]:
            if c.get("id") == request.action_id:
                chosen_consequence = c.get("consequence", "")
                chosen_consequence_kind = c.get("consequence_kind", "")
                chosen_action_text = c.get("text", "")
                break

        # Update the briefing with the player's choice
        update_briefing_choice(
            briefing_id=briefing["id"],
            selected_action_id=request.action_id,
            choice_rationale="selected by player",
            consequence_result={"consequence": chosen_consequence, "consequence_kind": chosen_consequence_kind},
        )

        # Append to per-action analytics log with hull snapshot
        try:
            stats_hull = get_game_state(game_id)["hull_integrity"]
            save_player_action_stats(
                game_id=game_id,
                player_id=request.player_id,
                turn=request.turn,
                action_id=request.action_id,
                action_text=chosen_action_text,
                consequence_kind=chosen_consequence_kind,
                hull_integrity=stats_hull,
            )
        except Exception:
            logger.warning("player_action_stats save failed", exc_info=True)
    else:
        # Legacy system: validate against game_turns.player_actions
        current_turn = get_game_turn(request.turn, game_id)
        if not current_turn:
            raise HTTPException(status_code=404, detail="No active game turn")
        valid_actions = [a["id"] for a in current_turn.get("player_actions", [])]
        if request.action_id not in valid_actions:
            raise HTTPException(status_code=400, detail="Invalid action ID")

    # Also save to player_actions table for backward compatibility
    result = save_player_action(request.player_id, request.turn, request.action_id, request.choice, None)

    # ── Generate comic panel for this player's action ────────────────
    # Generates a comic-style image showing the player's character
    # performing the chosen action, using their avatar as reference.
    # Registered in _pending_action_tasks so _analyze_turn_outcome can
    # await completion before pushing the outcome.
    action_key = (request.turn, game_id)
    game_lang = get_game_language(game_id)
    action_task = asyncio.create_task(
        _generate_chosen_action_image(
            player_id=request.player_id,
            game_id=game_id,
            turn=request.turn,
            action_id=request.action_id,
            language=game_lang,
        )
    )
    _pending_action_tasks.setdefault(action_key, set()).add(action_task)
    action_task.add_done_callback(lambda _t, k=action_key: _pending_action_tasks.get(k, set()).discard(_t))

    # Check if all real players have now chosen — if so, trigger combined outcome analysis
    try:
        remaining = get_players_who_need_to_choose(request.turn, game_id)
        if not remaining:
            # All players chose — analyze combined outcome
            logger.info(f"All players chose for turn {request.turn}, analyzing combined outcome")
            asyncio.create_task(_analyze_turn_outcome(request.turn, language=game_lang, game_id=game_id, force=False))
    except Exception as e:
        logger.warning(f"Combined outcome check failed: {e}")

    return {"status": "accepted", "action": result}


@app.post("/game/auto-action/{player_id}/{turn}")
async def auto_select_action(
    player_id: int,
    turn: int,
    language: str,
    *,
    game_id: str,
):
    """Auto-select an action for a player who hasn't chosen in time.

    Uses LLM with global circumstances + personal briefing + player profile
    to make an in-character choice. Notifies the player about the auto-selection.
    """
    # Use game's stored language — the caller may not know it
    language = get_game_language(game_id) or language
    logger.info(f"[AUTO_ACTION] Auto-selecting action for player {player_id}, turn {turn}")

    # Dead/spectator players never act. Without this guard the scheduler's
    # auto-select would submit a choice on behalf of a player who died on an
    # earlier turn (and may even have a leftover briefing from the race that
    # generated a turn before their death was applied), keeping them in the
    # "needs to choose" roster forever.
    profile = get_player_profile(player_id)
    if profile and (profile.get("is_dead") or profile.get("is_spectator")):
        logger.info(f"[AUTO_ACTION] Player {player_id} is dead/spectator, skipping auto-select")
        return {"status": "skipped_dead"}

    # 1. Get player's briefing with choices
    briefing = get_player_briefing(turn, player_id, game_id)
    if not briefing:
        raise HTTPException(
            status_code=404,
            detail=f"No briefing for player {player_id} turn {turn}",
        )

    if briefing.get("selected_action_id"):
        logger.info(f"[AUTO_ACTION] Player {player_id} already chose {briefing['selected_action_id']}, skipping")
        return {
            "status": "already_chosen",
            "action_id": briefing["selected_action_id"],
        }

    choices = briefing.get("choices", [])
    if not choices:
        raise HTTPException(
            status_code=400,
            detail=f"No choices available for player {player_id} turn {turn}",
        )

    # 2. Player profile already fetched above (is_dead guard).
    if not profile:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

    # 3. Get global circumstances
    game_turn = get_game_turn(turn, game_id)
    global_circ = {}
    if game_turn:
        gc_str = game_turn.get("global_circumstances", "{}")
        try:
            global_circ = json.loads(gc_str) if isinstance(gc_str, str) else gc_str
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Failed to parse global_circumstances: {e}", exc_info=True)

    # 4. Generate LLM choice
    gm = create_game_server(language=language)
    player_name = profile.get("player_name", "") or ""
    decision = await gm.generate_player_auto_choice(
        choices=choices, player_profile=profile, personal_briefing=briefing.get("briefing", ""), global_circumstances=global_circ, player_name=player_name, game_id=game_id, player_id=str(player_id), turn=turn, kind="player_auto_choice"
    )

    action_id = decision.get("action_id", "")
    rationale = decision.get("rationale", "Auto-selected by Game Server")

    if not action_id:
        raise HTTPException(status_code=500, detail="LLM returned no valid action")

    # Fallback delay decision carries a synthetic choice — append it to the
    # briefing's choices so every consumer (turn outcome, action image,
    # notification) resolves the selected id as usual.
    fallback_choice = decision.get("choice")
    if fallback_choice and not any(c.get("id") == fallback_choice.get("id") for c in choices):
        choices = [*choices, fallback_choice]

    # 5. Submit the action (same flow as submit_player_action)
    chosen_consequence = ""
    chosen_consequence_kind = ""
    chosen_action_text = ""
    for c in choices:
        if c.get("id") == action_id:
            chosen_consequence = c.get("consequence", "")
            chosen_consequence_kind = c.get("consequence_kind", "")
            chosen_action_text = c.get("text", "")
            break

    update_briefing_choice(
        briefing_id=briefing["id"],
        selected_action_id=action_id,
        choice_rationale=rationale,
        consequence_result={"consequence": chosen_consequence, "consequence_kind": chosen_consequence_kind},
        choices=choices,
    )

    # Append to per-action analytics log with hull snapshot
    try:
        stats_hull = get_game_state(game_id)["hull_integrity"]
        save_player_action_stats(
            game_id=game_id,
            player_id=player_id,
            turn=turn,
            action_id=action_id,
            action_text=chosen_action_text,
            consequence_kind=chosen_consequence_kind,
            hull_integrity=stats_hull,
        )
    except Exception:
        logger.warning("player_action_stats save failed (auto)", exc_info=True)

    save_player_action(
        player_id=player_id,
        turn=turn,
        action_id=action_id,
        choice="auto_selected",
        consequence_result=None,
    )

    # ── Generate comic panel for this player's action ────────────────
    # Mirrors the manual /game/actions path so auto-selected actions get
    # the same chosen_action_url, appear in the outcome album, and get
    # pushed to the player. Registered in _pending_action_tasks before the
    # remaining-check below so _analyze_turn_outcome (if triggered now)
    # awaits it before pushing the outcome.
    action_key = (turn, game_id)
    action_task = asyncio.create_task(
        _generate_chosen_action_image(
            player_id=player_id,
            game_id=game_id,
            turn=turn,
            action_id=action_id,
            language=language,
        )
    )
    _pending_action_tasks.setdefault(action_key, set()).add(action_task)
    action_task.add_done_callback(lambda _t, k=action_key: _pending_action_tasks.get(k, set()).discard(_t))

    # 6. Notify player about auto-selection
    action_text = ""
    for c in choices:
        if c.get("id") == action_id:
            action_text = c.get("text", c.get("description", ""))
            break

    gs = get_game_strings(language)
    notification = gs["auto_select_notification"].format(action_text=action_text, rationale=rationale)

    add_game_message(
        player_id=player_id,
        message=notification,
        message_type="auto_selection",
    )

    # 7. Check if all players have now chosen
    try:
        remaining = get_players_who_need_to_choose(turn, game_id=game_id)
        if not remaining:
            logger.info(f"All players chose for turn {turn} (after auto-select), analyzing combined outcome")
            asyncio.create_task(_analyze_turn_outcome(turn, language=language, game_id=game_id, force=False))
    except Exception as e:
        logger.warning(f"Combined outcome check after auto-select failed: {e}")

    logger.info(f"[AUTO_ACTION] Auto-selected '{action_id}' for player {player_id} turn {turn}: {action_text[:60]}...")

    return {
        "status": "selected",
        "action_id": action_id,
        "action_text": action_text,
        "rationale": rationale,
    }


@app.get("/game/turn-deadline/{game_id}")
async def get_turn_deadline(game_id: str):
    """Get the deadline of the current (playable) turn.

    The playable turn is game_state.turn - 1: generation advances the state
    to turn N+1 while players still act on turn N. deadline is an ISO
    datetime string (UTC) or null when unknown (e.g. first turn).
    Used by game-scheduler to fire T-2h/T-30m reminders.
    """
    state = get_game_state(game_id)
    turn = max(1, state["turn"] - 1)
    turn_data = get_game_turn(turn, game_id)
    deadline = turn_data.get("deadline") if turn_data else None
    return {"game_id": game_id, "turn": turn, "deadline": deadline}


@app.post("/game/remind-turn/{game_id}/{turn}")
async def remind_turn(game_id: str, turn: int, request: RemindTurnRequest):
    """Send a turn-deadline reminder to players who haven't chosen an action.

    Called by game-scheduler at T-2h (level 1) and T-30m (level 2) before the
    deadline stored on the turn. "Haven't chosen" is the same definition the
    auto-action flow uses: a player briefing with no selected_action_id.
    """
    if request.level not in (1, 2):
        raise HTTPException(status_code=400, detail="level must be 1 (T-2h) or 2 (T-30m)")

    language = get_game_language(game_id)
    pending = get_players_who_need_to_choose(turn, game_id)
    player_ids = [b["player_id"] for b in pending if b.get("player_id") is not None]

    logger.info(f"[REMIND] Turn {turn} reminder level {request.level} for game {game_id}: {len(player_ids)} player(s) pending")

    if player_ids:
        asyncio.create_task(
            push_turn_reminder(
                game_id=game_id,
                turn=turn,
                level=request.level,
                player_ids=player_ids,
                language=language,
            )
        )

    return {"status": "ok", "turn": turn, "level": request.level, "reminded": len(player_ids), "player_ids": player_ids}


# ============== Message endpoints ==============


def _build_game_message_context(game_id: str, player_id: int, profile_data: dict[str, Any]) -> dict[str, Any]:
    """Build rich game context for the Game Master LLM prompt.

    Gathers game title, mission info, current turn state, previous turn
    outcome, global circumstances, and crew composition.
    """
    ctx: dict[str, Any] = {}

    # Game title
    title = get_game_title(game_id)
    if title:
        ctx["game_title"] = title

    # Mission
    mission = get_mission(None, game_id=game_id)
    if mission:
        ctx["mission_name"] = mission.get("name", "")
        ctx["mission_description"] = mission.get("description", "")
        objectives = mission.get("objectives", [])
        if objectives:
            obj_lines = []
            for o in objectives:
                if isinstance(o, dict):
                    obj_lines.append(f"— Этап {o.get('stage', '?')}: {o.get('name', '')} — {o.get('description', '')}")
                else:
                    obj_lines.append(f"— {o}")
            ctx["mission_objectives"] = "\n".join(obj_lines)

    # Game state
    state = get_game_state(game_id)
    turn_num = state.get("turn", 1)
    ctx["turn"] = turn_num

    # Previous turn data (current turn - 1, since game_state.turn is the NEXT turn to generate)
    current_turn = max(1, turn_num - 1)
    if current_turn >= 1:
        prev_turn = get_game_turn(current_turn, game_id=game_id)
        if prev_turn:
            # Use combined_outcome if available, else previous_turn_summary
            outcome = prev_turn.get("combined_outcome") or prev_turn.get("previous_turn_summary", "")
            if outcome:
                ctx["previous_turn_summary"] = outcome
            gc_setting = prev_turn.get("global_circumstances", "")
            if gc_setting:
                ctx["global_circumstances_setting"] = gc_setting

    # Crew context — players + NPCs
    crew_lines: list[str] = []
    player_ids = get_players_in_game(game_id)
    for pid in player_ids:
        p = get_player_profile(pid)
        if p and not p.get("is_dead") and not p.get("is_spectator"):
            name = p.get("player_name", "") or str(pid)
            role = p.get("role", "Crew Member")
            marker = " ← ВЫ" if pid == player_id else ""
            crew_lines.append(f"— {name} ({role}){marker}")
    npcs = get_all_active_npcs(game_id)
    for npc in npcs:
        crew_lines.append(f"— {npc.get('npc_name', 'NPC')} ({npc.get('role', 'Crew')}) [NPC]")
    if crew_lines:
        ctx["crew_context"] = "\n".join(crew_lines)

    return ctx


@app.post("/game/messages")
async def submit_game_message(request: GameMessageRequest):
    """Submit a message to the game master and get response"""
    player_id = request.player_id
    message = request.message

    add_game_message(player_id, message, request.message_type)

    # Get player profile
    profile = get_player_profile(player_id)
    if not profile:
        profile_data: dict[str, Any] = {
            "role": "Crew Member",
            "personality_traits": [],
            "player_name": "",
            "player_id": player_id,
        }
    else:
        profile_data = {
            "role": profile["role"],
            "personality_traits": profile.get("personality_traits", []),
            "player_name": profile.get("player_name", ""),
            "player_id": player_id,
        }

    # Build game context for the LLM prompt
    game_id = profile.get("game_id") if profile else None
    if not game_id:
        raise HTTPException(status_code=400, detail="Player is not in any game")
    game_context = _build_game_message_context(game_id, player_id, profile_data)

    # Generate response from game master
    try:
        # Use the game's stored language, not character-based detection
        language = get_game_language(game_id) if profile else "en"
        game_server = create_game_server(language=language)

        response = await game_server.process_player_message(player_id=player_id, message=message, player_profile=profile_data, game_context=game_context, game_id=game_id, turn=None, kind="player_message")

        add_game_message(player_id, response, "text_response")

        return {"status": "processed", "response": response}
    except Exception as e:
        logger.error(f"Failed to generate game master response: {e}", exc_info=True)
        return {"status": "received", "error": str(e)}


@app.get("/game/actions/{player_id}/{turn}")
async def get_player_actions_endpoint(player_id: int, turn: int):
    """Get player actions for a specific turn"""
    actions = get_player_actions(player_id, turn)
    return {"actions": actions}


@app.get("/game/briefing/{player_id}/{turn}")
async def get_player_briefing_endpoint(player_id: int, turn: int):
    """Get a player's personal briefing and choices for a specific turn"""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")
    game_id = profile.get("game_id")
    if not game_id:
        raise HTTPException(status_code=400, detail="Player is not in any game")
    briefing = get_player_briefing(turn, player_id, game_id=game_id)
    if not briefing:
        raise HTTPException(status_code=404, detail="No briefing found")
    # Scene image for this turn
    briefing_image_url = get_random_game_image(type="scene", game_id=game_id, turn=turn)
    return {
        "briefing": briefing["briefing"],
        "choices": briefing["choices"],
        "selected_action_id": briefing.get("selected_action_id"),
        "turn": briefing["turn"],
        "chosen_action_url": briefing.get("chosen_action_url"),
        "briefing_image_url": briefing_image_url,
        "avatar_url": profile.get("avatar_url") if profile else None,
    }


@app.get("/game/current-briefing/{player_id}")
async def get_current_briefing_endpoint(player_id: int):
    """Get a player's current turn briefing"""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")
    game_id = profile.get("game_id")
    if not game_id:
        raise HTTPException(status_code=400, detail="Player is not in any game")
    state = get_game_state(game_id)
    turn_num = state["turn"]
    briefing = get_player_briefing(turn_num, player_id, game_id=game_id)
    if not briefing:
        raise HTTPException(status_code=404, detail="No briefing found for current turn")
    return {
        "briefing": briefing["briefing"],
        "choices": briefing["choices"],
        "selected_action_id": briefing.get("selected_action_id"),
        "turn": briefing["turn"],
        "chosen_action_url": briefing.get("chosen_action_url"),
    }


@app.get("/players")
async def get_all_players(game_id: str):
    """Get all players in the current game"""
    players = get_players_in_game(game_id)
    return [{"player_id": pid, "game_id": game_id} for pid in players]


@app.get("/game/messages/{player_id}")
async def get_game_messages_endpoint(player_id: int, limit: int):
    """Get player's message history"""
    messages = get_game_messages(player_id, limit)
    return {"messages": messages}


# ============== Content / Image endpoints ==============


@app.get("/content/loading-image")
async def get_loading_image(game_id: str):
    """Get a random loading screen image URL.

    Falls back to a manually-placed default image in ComfyUI output
    if no AI-generated loading images are available yet.
    """
    # Loading images are a global pool under game_id="all" (generated at
    # startup, not per-game), so look them up there regardless of game_id.
    url = get_random_game_image(type="loading", game_id="all", turn=None)
    if not url:
        logger.info(f"[LOADING] No generated loading images, using fallback: {DEFAULT_LOADING_FALLBACK_URL}")
        return {
            "image_url": DEFAULT_LOADING_FALLBACK_URL,
            "available": 0,
            "fallback": True,
        }
    return {"image_url": url, "available": get_game_image_count("loading", "all", None)}


@app.get("/content/splash-image")
async def get_splash_image(game_id: str):
    """Get a random splash image URL for the game.

    Falls back to a manually-placed default image in ComfyUI output
    if no AI-generated splash images are available yet.
    """
    url = get_random_game_image(type="splash", game_id=game_id, turn=None)
    if not url:
        logger.info(f"[SPLASH] No generated splash images, using fallback: {DEFAULT_SPLASH_FALLBACK_URL}")
        return {
            "image_url": DEFAULT_SPLASH_FALLBACK_URL,
            "available": 0,
            "fallback": True,
        }
    return {"image_url": url, "available": get_game_image_count("splash", game_id, None)}


async def _generate_chosen_action_image(
    player_id: int,
    game_id: str,
    turn: int,
    action_id: str,
    language: str,
):
    """Generate an image showing the player's chosen action.

    Uses LLM to craft a prompt in the same style as avatar prompts,
    with the player's avatar as visual reference for character consistency.
    Runs as fire-and-forget background task.
    """
    try:
        profile = get_player_profile(player_id)
        if not profile:
            logger.warning(f"[ACTION_IMAGE] Player {player_id} not found, skipping")
            return

        # Get the briefing to find the action text
        briefing = get_player_briefing(turn, player_id, game_id=game_id)
        if not briefing:
            logger.warning(f"[ACTION_IMAGE] Briefing not found for {player_id} turn {turn}")
            return

        # Find chosen action text
        action_text = ""
        for c in briefing.get("choices", []):
            if c.get("id") == action_id:
                action_text = c.get("text", c.get("description", ""))
                break
        if not action_text:
            action_text = action_id

        # Get scene context from game_turn
        turn_data = get_game_turn(turn, game_id=game_id)
        global_circ_str = turn_data.get("global_circumstances", "{}") if turn_data else "{}"
        try:
            global_circ = json.loads(global_circ_str)
        except (json.JSONDecodeError, TypeError):
            global_circ = {}
        setting = global_circ.get("setting", "") or turn_data.get("story", "") if turn_data else ""

        # Build character appearance description
        role = profile.get("role", "Crew Member")
        species = profile.get("species", "")
        species_desc = profile.get("species_description", "")
        avatar_desc = profile.get("avatar_description", "")
        traits = profile.get("personality_traits", [])

        # SHORT character visual reference (1 sentence max for image gen fallback).
        # Avoid the role title ("Scientific Officer") — it biases text-to-image
        # toward a human in uniform. Species only.
        character_description = species if species and species not in ("Unknown", "Неизвестно") else ""

        # Generate Qwen-Image-Edit instruction via LLM (refers to the avatar as
        # "Picture 1" and the best-matching background as "Picture 2"), with a
        # plain-text fallback if the LLM call fails.
        conflict = global_circ.get("conflict", "")
        scene_context = f"Setting: {setting}. Situation: {conflict}" if conflict else f"Setting: {setting}"
        gm = None
        try:
            gm = create_game_server(language=language)
            scene = await gm.generate_scene_instruction(
                action_text=action_text,
                species_desc=species_desc or species,
                language=language,
                background_location=None,
                scene_context=scene_context,
                species_category=profile.get("species_primary_key") or "",
                game_id=game_id,
                player_id=str(player_id),
                turn=turn,
                kind="player_action",
            )
            instruction = scene.get("instruction", "")
            bg_location = scene.get("background_location")
            logger.info(f"[ACTION_IMAGE] Scene instruction for {role}: {instruction[:120]}...")
        except Exception as llm_err:
            logger.warning(f"[ACTION_IMAGE] Scene instruction failed for {role}: {llm_err}, using fallback")
            instruction = ""
            bg_location = None

        if not instruction:
            instruction = f"Place the character from Picture 1 performing this action: {action_text}. Cinematic sci-fi scene, dramatic lighting, detailed environment, space opera aesthetic, photorealistic, 4K."

        # Look up a pre-generated background for the chosen location (if any)
        background_url = None
        if bg_location:
            background_url = get_random_game_image(type=f"background_{bg_location}", game_id=game_id, turn=None)

        # Get player's avatar URL for reference
        avatar_url = profile.get("avatar_url") or None

        image_gen = create_image_generator()
        chosen_action_url = await image_gen.generate_character_in_scene(
            instruction_prompt=instruction,
            character_avatar_url=avatar_url,
            background_url=background_url,
            character_description=character_description,
            filename_prefix=f"{game_id}/action_turn{turn}_p{player_id}",
            width=1024,
            height=1024,
            game_id=game_id,
            player_id=str(player_id),
            turn=turn,
            kind="player_action",
            species_category=profile.get("species_primary_key") or "",
        )

        if chosen_action_url:
            # Save chosen action URL to the briefing
            if briefing.get("id"):
                update_briefing_chosen_action_url(briefing["id"], chosen_action_url)
                logger.info(f"[ACTION_IMAGE] Saved for player {player_id} turn {turn}: {chosen_action_url}")

            # Push the action image to the player via telegram-bot
            # (fire-and-forget to avoid blocking the generation loop)
            try:
                await push_player_chosen_action(
                    player_id=player_id,
                    turn=turn,
                    chosen_action_url=chosen_action_url,
                    game_id=game_id,
                    action_text=action_text,
                    language=language,
                )
                logger.info(f"[ACTION_IMAGE] Pushed to player {player_id} turn {turn}")
            except Exception as push_err:
                logger.warning(f"[ACTION_IMAGE] Failed to push to player {player_id}: {push_err}")
        else:
            logger.warning(f"[ACTION_IMAGE] Generation returned None for player {player_id}")
    except Exception as e:
        logger.error(f"[ACTION_IMAGE] Failed to generate: {e}", exc_info=True)


async def _generate_death_image(
    player_id: int,
    game_id: str,
    turn: int,
    death_narrative: str,
    outcome_narrative: str,
    language: str,
) -> str | None:
    """Generate an image depicting the player's death this turn.

    Uses the dead character's avatar as a visual reference (so the image shows
    THAT character dying, not a generic figure) composed into a scene built from
    the personal death narrative. Mirrors _generate_chosen_action_image: an LLM
    crafts a Qwen-Image-Edit instruction, then generate_character_in_scene
    renders it with the avatar as "Picture 1".

    Returns the image URL, or None on failure (the death notice is still pushed
    without an image).
    """
    try:
        profile = get_player_profile(player_id)
        if not profile:
            logger.warning(f"[DEATH_IMAGE] Player {player_id} not found, skipping")
            return None

        # Scene context: the personal death narrative is the most specific cue;
        # fall back to the shared outcome narrative if absent.
        action_text = death_narrative or outcome_narrative or ""

        # Turn setting/conflict for background selection.
        turn_data = get_game_turn(turn, game_id=game_id)
        global_circ_str = turn_data.get("global_circumstances", "{}") if turn_data else "{}"
        try:
            global_circ = json.loads(global_circ_str)
        except (json.JSONDecodeError, TypeError):
            global_circ = {}
        setting = global_circ.get("setting", "") or (turn_data.get("story", "") if turn_data else "")
        conflict = global_circ.get("conflict", "")
        scene_context = f"Setting: {setting}. Situation: {conflict}" if conflict else f"Setting: {setting}"

        species = profile.get("species", "")
        species_desc = profile.get("species_description", "")
        character_description = species if species and species not in ("Unknown", "Неизвестно") else ""

        gm = create_game_server(language=language)
        instruction = ""
        bg_location = None
        try:
            scene = await gm.generate_scene_instruction(
                action_text=action_text,
                species_desc=species_desc or species,
                language=language,
                background_location=None,
                scene_context=scene_context,
                species_category=profile.get("species_primary_key") or "",
                game_id=game_id,
                player_id=str(player_id),
                turn=turn,
                kind="player_death",
            )
            instruction = scene.get("instruction", "")
            bg_location = scene.get("background_location")
        except Exception as llm_err:
            logger.warning(f"[DEATH_IMAGE] Scene instruction failed for {player_id}: {llm_err}, using fallback")

        if not instruction:
            instruction = (
                f"Show the character from Picture 1 in their final moment: {action_text}. "
                f"Dramatic, somber cinematic sci-fi scene, dramatic lighting, "
                f"space opera aesthetic, photorealistic, 4K."
            )

        background_url = None
        if bg_location:
            background_url = get_random_game_image(type=f"background_{bg_location}", game_id=game_id, turn=None)

        avatar_url = profile.get("avatar_url") or None
        image_gen = create_image_generator()
        death_image_url = await image_gen.generate_character_in_scene(
            instruction_prompt=instruction,
            character_avatar_url=avatar_url,
            background_url=background_url,
            character_description=character_description,
            filename_prefix=f"{game_id}/death_turn{turn}_p{player_id}",
            width=1024,
            height=1024,
            game_id=game_id,
            player_id=str(player_id),
            turn=turn,
            kind="player_death",
            species_category=profile.get("species_primary_key") or "",
        )
        if death_image_url:
            logger.info(f"[DEATH_IMAGE] Generated for player {player_id} turn {turn}: {death_image_url}")
        else:
            logger.warning(f"[DEATH_IMAGE] Generation returned None for player {player_id}")
        return death_image_url
    except Exception as e:
        logger.error(f"[DEATH_IMAGE] Failed to generate for player {player_id}: {e}", exc_info=True)
        return None


async def _inherit_npc_briefing_for_player(player_id: int, game_id: str, language: str) -> None:
    """Let a late-joining player inherit the current turn's NPC briefing.

    When a player completes onboarding into an already-running game and takes a
    role held by an NPC, the NPC's briefing for the current turn is cloned into
    the player's slot (with the auto-choice cleared, so the player chooses
    themselves) and the original NPC row is removed so the turn outcome
    resolves only the player's decision.
    """
    try:
        role_key = get_role_key_for_player(player_id, game_id)
        if not role_key:
            return
        npc_key = f"npc_{role_key}_{game_id}"

        # Game state tracks the NEXT turn to generate; the latest completed turn
        # is the one a player joining now would expect to participate in.
        current_turn = max(1, get_game_state(game_id)["turn"] - 1)
        npc_briefing = get_npc_briefing(current_turn, npc_key, game_id)
        if not npc_briefing:
            return

        save_player_briefing(
            {
                "turn": current_turn,
                "player_id": player_id,
                "npc_key": None,
                "is_npc": False,
                "briefing": npc_briefing["briefing"],
                "choices": npc_briefing.get("choices", []),
                "selected_action_id": None,
                "choice_rationale": "",
                "consequence_result": {},
                "chosen_action_url": None,
                "personal_title": npc_briefing.get("personal_title", ""),
                "image_prompt": npc_briefing.get("image_prompt", ""),
            },
            game_id,
        )
        delete_briefing(current_turn, npc_key, game_id)
        logger.info(
            f"[INHERIT] player {player_id} inherited turn {current_turn} from {npc_key} in game {game_id}"
        )
        # The player's chosen-action image is generated when they submit their
        # choice via /game/actions (reusing the existing pipeline), so nothing
        # more to do here — the player polls, sees the inherited briefing, and
        # picks an action like any other player.
    except Exception as e:
        logger.error(f"[INHERIT] Failed for player {player_id} game {game_id}: {e}", exc_info=True)


async def _generate_npc_chosen_action_image(
    npc_key: str,
    game_id: str,
    turn: int,
    action_id: str,
):
    """Generate an image showing the NPC's chosen action.

    Similar to _generate_chosen_action_image but uses NPC profiles.
    Runs as fire-and-forget background task.
    """
    try:
        npc_profile = get_npc_profile(npc_key)
        if not npc_profile:
            logger.warning(f"[NPC_ACTION_IMAGE] NPC {npc_key} not found, skipping")
            return

        # Get the briefing to find the action text
        # NPC briefings have player_id = None and npc_key set
        all_briefings = get_all_briefings_for_turn(turn, game_id)
        briefing = None
        for b in all_briefings:
            if b.get("npc_key") == npc_key:
                briefing = b
                break
        if not briefing:
            logger.warning(f"[NPC_ACTION_IMAGE] Briefing not found for {npc_key} turn {turn}")
            return

        # Find chosen action text
        action_text = ""
        for c in briefing.get("choices", []):
            if c.get("id") == action_id:
                action_text = c.get("text", c.get("description", ""))
                break
        if not action_text:
            action_text = action_id

        # Get scene context from game_turn
        turn_data = get_game_turn(turn, game_id)
        global_circ_str = turn_data.get("global_circumstances", "{}") if turn_data else "{}"
        try:
            global_circ = json.loads(global_circ_str)
        except (json.JSONDecodeError, TypeError):
            global_circ = {}
        setting = global_circ.get("setting", "") or turn_data.get("story", "") if turn_data else ""

        # Build character appearance description from NPC profile
        role = npc_profile.get("role", "Crew Member")
        npc_name = npc_profile.get("npc_name", npc_key)
        traits = npc_profile.get("personality_traits", [])
        avatar_desc = npc_profile.get("avatar_description", "")

        # Extract avatar URL from avatar_description field (format: "avatar_url=<url>;...")
        avatar_url = None
        if avatar_desc.startswith("avatar_url="):
            url_part = avatar_desc.split(";")[0]
            avatar_url = url_part.replace("avatar_url=", "", 1)
            # Remove prompt part for description
            avatar_desc_clean = avatar_desc.split(";", 1)[1] if ";" in avatar_desc else ""
        else:
            avatar_desc_clean = avatar_desc

        character_description = f"{npc_name}, the {role}"

        # Generate Qwen-Image-Edit instruction via LLM
        npc_species = npc_profile.get("species", "") or ""
        instruction = ""
        bg_location = None
        conflict = global_circ.get("conflict", "")
        scene_context = f"Setting: {setting}. Situation: {conflict}" if conflict else f"Setting: {setting}"
        try:
            game_lang = get_game_language(game_id)
            gm = create_game_server(language=game_lang)
            scene = await gm.generate_scene_instruction(
                action_text=action_text,
                species_desc=npc_species,
                language=game_lang,
                background_location=None,
                scene_context=scene_context,
                species_category=npc_profile.get("species", "") or "",
                game_id=game_id,
                player_id=npc_key,
                turn=turn,
                kind="npc_action",
            )
            instruction = scene.get("instruction", "")
            bg_location = scene.get("background_location")
        except Exception as llm_err:
            logger.warning(f"[NPC_ACTION_IMAGE] Scene instruction failed for {npc_name}: {llm_err}")

        if not instruction:
            instruction = f"Place the character from Picture 1 performing this action: {action_text}. Cinematic sci-fi scene, dramatic lighting, detailed environment, space opera aesthetic, photorealistic, 4K."

        background_url = None
        if bg_location:
            background_url = get_random_game_image(type=f"background_{bg_location}", game_id=game_id, turn=None)

        image_gen = create_image_generator()
        chosen_action_url = await image_gen.generate_character_in_scene(
            instruction_prompt=instruction,
            character_avatar_url=avatar_url,
            background_url=background_url,
            character_description=character_description,
            filename_prefix=f"{game_id}/action_turn{turn}_{npc_key}",
            width=1024,
            height=1024,
            game_id=game_id,
            player_id=None,
            turn=turn,
            kind="npc_action",
            species_category=npc_profile.get("species", "") or "",
        )

        if chosen_action_url:
            # Save chosen action URL to the briefing
            if briefing.get("id"):
                update_briefing_chosen_action_url(briefing["id"], chosen_action_url)
                logger.info(f"[NPC_ACTION_IMAGE] Saved for NPC {npc_name} turn {turn}: {chosen_action_url}")
        else:
            logger.warning(f"[NPC_ACTION_IMAGE] Generation returned None for {npc_name}")
    except Exception as e:
        logger.error(f"[NPC_ACTION_IMAGE] Failed to generate: {e}", exc_info=True)


def _build_turn_summary(combined_outcome_str: str, language: str) -> str:
    """Build a compact text summary from combined_outcome JSON for cross-turn context.

    The LLM receives this summary as 'previous events' when generating the next turn.
    Extracts key fields rather than passing raw JSON to save tokens and improve focus.
    """
    if not combined_outcome_str:
        return ""
    try:
        oc = json.loads(combined_outcome_str)
    except (json.JSONDecodeError, TypeError):
        # Not JSON — might be a plain text summary already
        return str(combined_outcome_str)[:2000]

    parts = []

    # Narrative summary (first ~400 chars for compactness)
    narrative = oc.get("outcome_narrative", "")
    if narrative:
        parts.append(narrative[:400])

    # Ship status
    ship_status = oc.get("ship_status_change", "")
    gs = get_game_strings(language)
    ds = gs["turn_summary"]
    if ship_status:
        parts.append(ds["ship_status"].format(status=ship_status))

    # Ship hull integrity
    hull = oc.get("ship_hull_integrity")
    shields = oc.get("ship_shields")
    if hull is not None or shields is not None:
        hull_str = f"{hull}%" if hull is not None else "?"
        shields_str = f"{shields}%" if shields is not None else "?"
        parts.append(ds["hull_shields"].format(hull=hull_str, shields=shields_str))

    # Ship systems offline
    offline = oc.get("ship_systems_offline", [])
    if offline:
        systems_str = ", ".join(offline)
        parts.append(ds["systems_offline"].format(systems=systems_str))

    # Crew morale
    morale = oc.get("crew_morale_change", "")
    if morale:
        parts.append(ds["crew_morale"].format(morale=morale))

    # Deaths — new outcomes address victims as {"entity_id": ...}; older
    # stored outcomes use legacy [name, role] lists.
    dead = oc.get("dead_crew_members", [])
    if dead:
        dead_names = []
        for d in dead:
            if isinstance(d, dict):
                dead_names.append(str(d.get("entity_id", d)))
            elif isinstance(d, list) and len(d) >= 2:
                dead_names.append(f"{d[0]} ({d[1]})")
            else:
                dead_names.append(str(d))
        parts.append(ds["deceased"].format(names=", ".join(dead_names)))

    # Injured — entity_id objects (new) or [name, role, severity] lists (legacy)
    injured = oc.get("crew_injured", [])
    if injured:
        injured_names = []
        for i_entry in injured:
            if isinstance(i_entry, dict):
                severity = i_entry.get("severity") or "unknown"
                injured_names.append(f"{i_entry.get('entity_id', i_entry)} ({severity})")
            elif isinstance(i_entry, list) and len(i_entry) >= 2:
                i_severity = i_entry[2] if len(i_entry) >= 3 else "unknown"
                injured_names.append(f"{i_entry[0]} ({i_severity})")
            else:
                injured_names.append(str(i_entry))
        parts.append(ds["injured"].format(names=", ".join(injured_names)))

    # Ship destroyed
    if oc.get("ship_destroyed"):
        parts.append(ds["ship_destroyed"])

    # Next turn hook
    hook = oc.get("next_turn_hook", "")
    if hook:
        parts.append(ds["next_turn_hook"].format(hook=hook))

    return " | ".join(parts) if parts else narrative[:500]


def _build_cumulative_story_summary(
    current_turn: int,
    language: str,
    *,
    game_id: str,
) -> str:
    """Build a cumulative story summary from ALL previous turns.

    Collects combined_outcome from every completed turn (1 .. current_turn - 1)
    and concatenates them chronologically. This gives the LLM a complete
    picture of the story so far, not just the last turn.

    Args:
        current_turn: The upcoming turn number (turns before this are summarized)
        language: Language for labels ("ru" or "en")
        game_id: Game identifier

    Returns:
        A compact chronological summary string, or empty string if no prior turns.
    """
    if current_turn <= 1:
        return ""

    summaries = []
    gs = get_game_strings(language)
    cs = gs["cumulative_story"]
    header = cs["header"]
    turn_label = cs["turn_label"]

    for d in range(1, current_turn):
        turn_record = get_game_turn(d, game_id)
        if not turn_record:
            continue

        combined_outcome = turn_record.get("combined_outcome", "")
        turn_summary = ""
        if combined_outcome:
            turn_summary = _build_turn_summary(combined_outcome, language=language)
        elif turn_record.get("story"):
            turn_summary = turn_record["story"][:300]

        if turn_summary:
            summaries.append(f"{turn_label} {d}: {turn_summary}")

    if not summaries:
        return ""

    result = header + "\n" + "\n".join(summaries)
    # Truncate to 3000 chars to avoid blowing up the LLM prompt
    if len(result) > 3000:
        result = result[:3000] + "..."

    return result


def _parse_entity_id(entity_id: Any) -> tuple[int | None, str | None]:
    """Split a stable entity_id ("p<player_id>" / "n<npc_key>") into its parts.

    Returns (player_id, npc_key) — exactly one of them is set.
    """
    if not isinstance(entity_id, str):
        return None, None
    if entity_id.startswith("p") and entity_id[1:].isdigit():
        return int(entity_id[1:]), None
    if entity_id.startswith("n") and len(entity_id) > 1:
        return None, entity_id[1:]
    return None, None


def _resolve_outcome_entity(
    entry: Any,
    crew_roster: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve one dead_crew_members / crew_injured / crew_healed entry to a
    roster record.

    Expected format (per COMBINED_OUTCOME_SCHEMA): an object addressed by the
    stable entity_id from the crew roster — "p<player_id>" for players,
    "n<npc_key>" for NPCs. A legacy [name, role, ...] list is a schema
    violation from the LLM: it is logged and then matched by an EXACT roster
    name only (the role is never compared — role matching once deactivated
    the wrong NPC). Graceful degradation at the external-API boundary.
    """
    if isinstance(entry, dict):
        entity_id = entry.get("entity_id")
        if entity_id is not None:
            for r in crew_roster:
                if r.get("entity_id") == entity_id:
                    return r
        logger.warning(f"[OUTCOME] Unknown entity_id {entity_id!r} in outcome entry, skipping: {entry}", stack_info=True)
        return None
    if isinstance(entry, list) and entry:
        logger.warning(f"[OUTCOME] Legacy [name, role] outcome entry (schema requires entity_id objects), falling back to exact name match: {entry}", stack_info=True)
        for r in crew_roster:
            if r.get("name") == entry[0]:
                return r
        logger.warning(f"[OUTCOME] Legacy outcome entry name {str(entry[0])!r} not found in roster, skipping", stack_info=True)
        return None
    logger.warning(f"[OUTCOME] Malformed outcome entry, skipping: {entry}", stack_info=True)
    return None


def _apply_crew_injuries(
    outcome: dict[str, Any],
    crew_roster: list[dict[str, Any]],
    *,
    game_id: str,
) -> tuple[list[dict[str, str]], set[int]]:
    """Apply the turn's crew_injured entries through the wound-escalation rules.

    The incoming severity from the LLM is resolved against the STORED
    severity via game_rules.resolve_injury (result = max on the ladder,
    never below the incoming wound). A critically wounded character who
    takes ANY new wound dies mechanically: players are marked dead and
    collected in newly_dead (for the one-time death notice push), NPCs
    are deactivated. Returns (injury_notices, newly_dead); notices are
    built only for wounds that were actually persisted, never for deaths
    (those surface via the death-notice roster).
    """
    injury_notices: list[dict[str, str]] = []
    newly_dead: set[int] = set()
    for injury_entry in outcome.get("crew_injured", []):
        resolved = _resolve_outcome_entity(injury_entry, crew_roster)
        if not resolved:
            continue
        if isinstance(injury_entry, dict):
            severity = injury_entry.get("severity") or "minor"
        else:
            severity = injury_entry[2] if len(injury_entry) >= 3 else "minor"
        pid, npc_key = _parse_entity_id(resolved.get("entity_id"))
        if pid:
            profile = get_player_profile(pid) or {}
            new_severity = resolve_injury(profile.get("wound_severity"), severity)
            if new_severity == WOUND_DEAD:
                mark_player_dead(pid, game_id)
                newly_dead.add(pid)
                logger.info(f"[WOUND] Player {pid} died of accumulated wounds (critical + new injury)")
                continue
            set_player_wound_severity(pid, game_id, new_severity)
            logger.info(f"[INJURY] Player {pid} now {new_severity}")
        elif npc_key:
            npc_profile = get_npc_profile(npc_key) or {}
            new_severity = resolve_injury(npc_profile.get("wound_severity"), severity)
            if new_severity == WOUND_DEAD:
                deactivate_npc(npc_key)
                logger.info(f"[WOUND] NPC {npc_key} died of accumulated wounds (critical + new injury)")
                continue
            set_npc_wound_severity(npc_key, new_severity)
            logger.info(f"[INJURY] NPC {npc_key} now {new_severity}")
        else:
            continue
        injury_notices.append(
            {
                "name": str(resolved.get("name", "")),
                "role": str(resolved.get("role", "")),
                "severity": str(severity),
            }
        )
    return injury_notices, newly_dead


async def _analyze_turn_outcome(
    turn: int,
    language: str,
    *,
    game_id: str,
    force: bool,
):
    """Analyze all decisions for a turn (player + NPC) to produce combined outcome.

    Called automatically when all players have submitted their choices (from
    /game/action or /game/auto-action), or triggered manually via
    /admin/analyze-turn with force=True.

    Guarded by per-(turn, game_id) async lock + DB idempotency check to prevent
    duplicate LLM calls when auto-action and continue-game race.
    """
    logger.info(f"[OUTCOME] Analyzing combined outcome for Turn {turn} (force={force})")

    lock = _get_outcome_lock(turn, game_id)
    async with lock:
        # Idempotency: if outcome already computed, skip (unless forced)
        if not force:
            existing = get_game_turn(turn, game_id)
            if existing and existing.get("combined_outcome", "").strip():
                logger.info(f"[OUTCOME] Turn {turn} already has combined outcome, skipping")
                return

        try:
            # Get all briefings for this turn
            all_briefings = get_all_briefings_for_turn(turn, game_id)
            if not all_briefings:
                logger.warning(f"[OUTCOME] No briefings found for Turn {turn}")
                return

            # Get global circumstances
            game_turn = get_game_turn(turn, game_id)
            global_circ_str = game_turn.get("global_circumstances", "{}") if game_turn else "{}"
            try:
                global_circ = json.loads(global_circ_str)
            except (json.JSONDecodeError, TypeError):
                global_circ = {}

            # Build decisions list (name, role, action, consequence, rationale)
            all_decisions = []
            for b in all_briefings:
                selected_id = b.get("selected_action_id")
                if not selected_id:
                    continue

                choices = b.get("choices", [])
                action_text = ""
                consequence = ""
                consequence_kind = ""
                for c in choices:
                    if c.get("id") == selected_id:
                        action_text = c.get("text", "")
                        consequence = c.get("consequence", "")
                        consequence_kind = c.get("consequence_kind", "")
                        break

                cr = b.get("consequence_result", {})
                if isinstance(cr, str):
                    try:
                        cr = json.loads(cr)
                    except (json.JSONDecodeError, TypeError):
                        cr = {}

                # Look up role from profile or NPC
                player_id = b.get("player_id")
                npc_key = b.get("npc_key")
                role_name = ""
                entity_name = "?"

                if player_id:
                    p = get_player_profile(player_id)
                    if p:
                        role_name = p.get("role", "")
                        entity_name = p.get("player_name", "") or str(player_id)
                elif npc_key:
                    n = get_npc_profile(npc_key)
                    if n:
                        role_name = n.get("role", "")
                        entity_name = n.get("npc_name", npc_key)

                all_decisions.append(
                    {
                        "player_id": player_id,
                        "npc_key": npc_key,
                        "entity_id": f"p{player_id}" if player_id else (f"n{npc_key}" if npc_key else None),
                        "name": entity_name,
                        "role": role_name,
                        "action_id": selected_id,
                        "action_text": action_text,
                        "consequence": cr.get("consequence") or consequence,
                        "consequence_kind": cr.get("consequence_kind") or consequence_kind,
                        "rationale": b.get("choice_rationale", ""),
                    }
                )

            if not all_decisions:
                logger.warning(f"[OUTCOME] No decisions made yet for Turn {turn}")
                return

            # Also add NPC decisions from the combined outcome
            # NPC decisions were already analyzed during turn generation

            # Build cumulative summary from ALL previous turns for full story context
            previous_summary = _build_cumulative_story_summary(
                current_turn=turn,
                language=language,
                game_id=game_id,
            )

            # Get mission context for progress tracking
            mission = get_mission(None, game_id=game_id)

            # Build full crew roster from all briefings — prevents LLM from inventing members.
            # entity_id ("p<player_id>" / "n<npc_key>") is the stable address the LLM
            # MUST use in dead_crew_members / crew_injured / crew_healed.
            crew_roster = []
            for b in all_briefings:
                player_id = b.get("player_id")
                npc_key = b.get("npc_key")
                entity_id = f"p{player_id}" if player_id else (f"n{npc_key}" if npc_key else None)
                role_name = "?"
                entity_name = "?"
                is_dead = False
                wound_severity = None
                if player_id:
                    p = get_player_profile(player_id)
                    if p:
                        role_name = p.get("role", "?")
                        entity_name = p.get("player_name", "") or str(player_id)
                        is_dead = bool(p.get("is_dead", False))
                        wound_severity = p.get("wound_severity")
                elif npc_key:
                    n = get_npc_profile(npc_key)
                    if n:
                        role_name = n.get("role", "?")
                        entity_name = n.get("npc_name", npc_key)
                        is_dead = not n.get("is_active", True)
                        wound_severity = n.get("wound_severity")
                crew_roster.append({"entity_id": entity_id, "name": entity_name, "role": role_name, "is_dead": is_dead, "wound_severity": wound_severity})

            # Analyze with LLM. Ship status is persistent code-owned state:
            # the LLM receives the current hull/shields/systems_offline and
            # returns only per-turn deltas against them.
            gm = create_game_server(language=language)
            ship_state = get_game_state(game_id)
            ship_status = {
                "hull_integrity": ship_state["hull_integrity"],
                "shields": ship_state["shields"],
                "systems_offline": ship_state["systems_offline"],
            }
            outcome = await gm.analyze_combined_outcome(global_circ, all_decisions, previous_summary, mission_context=mission, crew_roster=crew_roster, ship_status=ship_status, threat_level=ship_state["threat_level"], game_id=game_id, player_id=None, turn=turn, kind="combined_outcome")

            # Detect and retry fallback outcomes (bland narrative, empty progress).
            # This happens when the LLM JSON can't be parsed and we got the generic
            # fallback dict. Retry once with a fresh GM to avoid stale state issues.
            narrative = outcome.get("outcome_narrative", "")
            is_fallback = not outcome.get("mission_progress") and ("passed without major incident" in narrative or "without major incident" in narrative)
            # Detect schema violation: some models obey the JSON schema but ALSO
            # dump the structured fields (ship_status_change, mission_progress,
            # personal_outcomes, ...) as plain text INSIDE outcome_narrative,
            # after the real narrative. That bloats the field to >4096 chars and
            # breaks Telegram delivery. The model should return a clean narrative
            # only — retry when it leaks structured fields into the narrative.
            is_schema_leak = "ship_status_change:" in narrative
            if is_fallback or is_schema_leak:
                if is_schema_leak:
                    logger.warning("[OUTCOME] outcome_narrative leaked structured fields (%d chars), retrying for a clean schema...", len(narrative))
                else:
                    logger.warning("[OUTCOME] Got fallback outcome, retrying once...")
                try:
                    retry_gm = create_game_server(language=language)
                    outcome = await retry_gm.analyze_combined_outcome(
                        global_circ,
                        all_decisions,
                        previous_summary,
                        mission_context=mission,
                        crew_roster=crew_roster,
                        ship_status=ship_status,
                        threat_level=ship_state["threat_level"],
                        game_id=game_id,
                        player_id=None,
                        turn=turn,
                        kind="combined_outcome",
                    )
                    retry_narrative = outcome.get("outcome_narrative", "")
                    retry_leak = "ship_status_change:" in retry_narrative
                    if retry_leak:
                        logger.error("[OUTCOME] Retry still leaked structured fields into outcome_narrative — giving up")
                    elif not outcome.get("mission_progress") and "without major incident" in retry_narrative:
                        logger.error("[OUTCOME] Retry also returned fallback — giving up")
                    else:
                        logger.info("[OUTCOME] Retry succeeded")
                except Exception as retry_err:
                    logger.error(f"[OUTCOME] Retry failed: {retry_err}", exc_info=True)

            # NOTE: combined_outcome is persisted AFTER all turn effects
            # (deaths, end_game, ship state) are applied below. The presence of
            # combined_outcome in the DB is the "turn fully closed" signal that
            # continue-game waits on before generating the next turn — so it must
            # not be written until mark_player_dead / end_game have run.

            # Apply mission progress through the rules layer (P0+P1):
            # normalizes objectives, accumulates with regression caps,
            # and computes completion from real thresholds (fixes defect B/C).
            mission_progress = outcome.get("mission_progress", [])
            mission_completed = False
            mission_stagnant = False
            if mission:
                progress_before = sum(mission.get("stage_progress", {}).values())
                updated_mission = apply_mission_progress(mission, mission_progress)
                update_mission_stage_progress(
                    updated_mission["stage_progress"],
                    updated_mission["current_stage"],
                    game_id=game_id,
                    completed=updated_mission["completed"],
                )
                for stage_key, pts in updated_mission["stage_progress"].items():
                    logger.info(f"[MISSION] Stage {stage_key} progress now {pts}")
                if updated_mission["completed"]:
                    mission_completed = True
                    end_game("mission_complete", game_id=game_id)
                    logger.info("[MISSION] MISSION COMPLETE! Game ended.")
                # Doom clock input: the turn is stagnant when total stage
                # progress did not grow (regression counts as stagnant too).
                mission_stagnant = sum(updated_mission["stage_progress"].values()) <= progress_before
                mission = updated_mission

            # ========== Ship status: persistent state + LLM deltas ==========
            # hull/shields/systems_offline live in game_state; the LLM outcome
            # carries only per-turn changes. Apply them through the rules layer
            # (clamped to [0,100]). Destruction is decided by code: hull <= 0.
            state = get_game_state(game_id)
            ship_hull, ship_shields = apply_ship_status(
                state["hull_integrity"],
                state["shields"],
                outcome.get("ship_hull_change", 0),
                outcome.get("ship_shields_change", 0),
            )
            ship_systems_offline = apply_systems_offline(
                state["systems_offline"],
                outcome.get("systems_taken_offline", []),
                outcome.get("systems_restored", []),
            )
            ship_destroyed = ship_hull <= 0

            # Record the resulting absolute ship status in the outcome JSON so
            # stored turn summaries (and the push below) read real end-of-turn
            # values instead of LLM deltas.
            outcome["ship_hull_integrity"] = ship_hull
            outcome["ship_shields"] = ship_shields
            outcome["ship_systems_offline"] = ship_systems_offline
            outcome["ship_destroyed"] = ship_destroyed
            outcome_json = json.dumps(outcome, ensure_ascii=False)

            logger.info(f"[SHIP] Turn {turn}: hull={ship_hull}%, shields={ship_shields}%, systems_offline={ship_systems_offline}, destroyed={ship_destroyed}")

            # Handle crew injuries — escalate the stored severity through
            # game_rules.resolve_injury: the result is max(current, incoming)
            # on the ladder, and a critically wounded character who takes any
            # new wound DIES (mark_player_dead / deactivate_npc — the death
            # notice push reads newly_dead below). Entries are addressed
            # by entity_id from the crew roster; legacy [name, role, severity]
            # lists fall back to an exact roster-name match (see
            # _resolve_outcome_entity). Notices are built inside the helper
            # so a push notice exists exactly when the wound was persisted.
            injury_notices, newly_dead = _apply_crew_injuries(outcome, crew_roster, game_id=game_id)

            # Handle crew healing — only when the Medical Officer treated wounds.
            # crew_healed entries are {"entity_id", "new_severity"} where
            # new_severity is the improved step ('healthy' = fully healed →
            # stored as NULL). Healed NPCs feed the loyalty rules below.
            healed_npc_count = 0
            for heal_entry in outcome.get("crew_healed", []):
                resolved = _resolve_outcome_entity(heal_entry, crew_roster)
                if not resolved:
                    continue
                if isinstance(heal_entry, dict):
                    new_severity = heal_entry.get("new_severity") or "healthy"
                else:
                    new_severity = heal_entry[2] if len(heal_entry) >= 3 else "healthy"
                stored = None if new_severity in (None, "", "healthy") else new_severity
                pid, npc_key = _parse_entity_id(resolved.get("entity_id"))
                if pid:
                    set_player_wound_severity(pid, game_id, stored)
                    logger.info(f"[HEAL] Player {pid} wound now {stored or 'healthy'}")
                elif npc_key:
                    set_npc_wound_severity(npc_key, stored)
                    healed_npc_count += 1
                    logger.info(f"[HEAL] NPC {npc_key} wound now {stored or 'healthy'}")

            # Handle crew deaths — entries are {"entity_id", "cause"} addressed
            # by the stable roster id (legacy [name, role] falls back to an
            # exact roster-name match). Player ids who died on THIS turn —
            # here or via wound escalation above — are collected in newly_dead
            # for a one-time death notice. NPCs are deactivated and never
            # receive a push.
            dead_crew = outcome.get("dead_crew_members", [])
            for death_entry in dead_crew:
                resolved = _resolve_outcome_entity(death_entry, crew_roster)
                if not resolved:
                    continue
                pid, npc_key = _parse_entity_id(resolved.get("entity_id"))
                if pid:
                    mark_player_dead(pid, game_id)
                    newly_dead.add(pid)
                    logger.info(f"[DEATH] Player {pid} marked as dead")
                elif npc_key:
                    deactivate_npc(npc_key)
                    logger.info(f"[DEATH] NPC {npc_key} deactivated")

            # Handle ship destruction — decided by code (hull <= 0), not the LLM
            if ship_destroyed:
                end_game("ship_destroyed", game_id=game_id)
                logger.warning(f"[SHIP] Ship destroyed! Game over for {game_id}")

            # Handle crew wiped — all crew members dead
            live_players = get_live_players(game_id)
            active_npcs = get_all_active_npcs(game_id)
            crew_wiped = len(live_players) == 0 and len(active_npcs) == 0
            if crew_wiped and not ship_destroyed:
                end_game("crew_wiped", game_id=game_id)
                logger.warning(f"[CREW] All crew dead! Game over for {game_id}")

            # Persist the ship status and advance the doom clock, unless the
            # game has already ended — end_game() already set the correct
            # status (mission_complete, ship_destroyed, etc.).
            game_already_ended = mission_completed or ship_destroyed or crew_wiped

            # ── NPC loyalty: code-owned morale, applied to every active NPC ──
            # Loyalty drops from this turn's losses (deaths, hull damage,
            # mission regression) and recovers slightly from heals and
            # mission progress (game_rules.compute_loyalty_change). Two
            # active NPCs at loyalty <= MUTINY_LOYALTY_THRESHOLD means open
            # mutiny — a defeat path of its own.
            mutiny_happened = False
            if not game_already_ended:
                # Deaths this turn: players are collected in newly_dead (both
                # dead_crew_members and wound escalation), NPCs are deactivated
                # in the same handlers — count them as the roster drop.
                npcs_alive_before = sum(
                    1 for r in crew_roster if str(r["entity_id"] or "").startswith("n") and not r["is_dead"]
                )
                deaths_count = len(newly_dead) + max(0, npcs_alive_before - len(active_npcs))
                hull_damage = max(0, -_to_int(outcome.get("ship_hull_change", 0), 0))
                mission_delta = sum(_to_int(e.get("points", 0), 0) for e in mission_progress if isinstance(e, dict))
                change = compute_loyalty_change(
                    deaths_count=deaths_count,
                    hull_damage=hull_damage,
                    mission_delta=mission_delta,
                    healed_count=healed_npc_count,
                )
                new_loyalties = [adjust_npc_loyalty(n["npc_key"], change) for n in active_npcs]
                logger.info(
                    f"[LOYALTY] Turn {turn}: change={change} (deaths={deaths_count}, "
                    f"hull_dmg={hull_damage}, mission={mission_delta}, healed={healed_npc_count})"
                )
                if mutiny_conditions(new_loyalties):
                    mutiny_happened = True
                    game_already_ended = True
                    end_game("mutiny", game_id=game_id)
                    logger.warning(f"[LOYALTY] Crew mutiny on {game_id}! Game over")

            threat_overwhelmed = False
            new_threat = state["threat_level"]
            if not game_already_ended:
                # Doom clock: threat grows by CODE every turn (never by the
                # LLM), accelerated by auto-selected actions (hesitation),
                # a critically damaged hull and mission stagnation.
                auto_total, auto_count = count_turn_action_autos(turn, game_id=game_id)
                auto_ratio = (auto_count / auto_total) if auto_total > 0 else 0.0
                new_threat = compute_threat_tick(
                    state["threat_level"],
                    auto_ratio=auto_ratio,
                    hull_ratio=ship_hull / HULL_MAX,
                    mission_stagnant=mission_stagnant,
                )
                update_game_state(
                    state["turn"],
                    "active",
                    ship_alive=True,
                    hull_integrity=ship_hull,
                    shields=ship_shields,
                    systems_offline=ship_systems_offline,
                    threat_level=new_threat,
                    game_id=game_id,
                )
                logger.info(
                    f"[THREAT] Turn {turn}: threat={new_threat} (+{new_threat - state['threat_level']}, "
                    f"auto={auto_ratio:.0%}, stagnant={mission_stagnant})"
                )
                if new_threat >= THREAT_MAX:
                    threat_overwhelmed = True
                    end_game("overwhelmed", game_id=game_id)
                    logger.warning(f"[THREAT] Threat reached {new_threat}/{THREAT_MAX}! Game over for {game_id}")
            else:
                # Terminal turn: still persist the final ship state so
                # /game/status reflects the ship the crew actually ended
                # with — end_game() only flips status/ship_alive. Status and
                # ship_alive are re-read AFTER end_game so we never overwrite
                # the terminal values it just wrote.
                ended_state = get_game_state(game_id)
                update_game_state(
                    state["turn"],
                    ended_state.get("status", "active"),
                    ship_alive=ended_state.get("ship_alive", True),
                    hull_integrity=ship_hull,
                    shields=ship_shields,
                    systems_offline=ship_systems_offline,
                    game_id=game_id,
                )

            # Log ship systems offline
            if ship_systems_offline:
                logger.info(f"[SHIP] Systems offline: {', '.join(ship_systems_offline)}")

            # Persist combined_outcome now that ALL turn effects (deaths,
            # end_game, ship status) are applied. This is the
            # "turn fully closed" signal: continue-game blocks until the
            # previous turn's combined_outcome exists, so a death resolved on
            # turn N is visible in player_profiles before turn N+1 starts.
            update_game_turn_outcome(turn, outcome_json, game_id)
            logger.info(f"[OUTCOME] Combined outcome saved for Turn {turn}")

            # ── Push outcome to all alive players ──────────────────────
            # Build outcome text from the LLM result
            outcome_text = outcome.get("outcome_narrative", "") or outcome.get("narrative", "") or outcome.get("summary", "") or outcome.get("outcome", "")
            if not outcome_text:
                # Fallback: clean up JSON string for display
                raw = json.dumps(outcome, ensure_ascii=False)
                outcome_text = raw[:500] + ("..." if len(raw) > 500 else "")

            # Build death notices as a persistent roster from DB state — dead
            # players plus dead (deactivated) NPCs — so losses remain visible
            # every turn, the same way injuries do. Previously this derived
            # notices only from the current turn's LLM output (dead_crew), so a
            # death surfaced on the turn it happened and then disappeared.
            all_players_total = get_players_in_game(game_id)
            all_npcs_total = get_all_npcs(game_id)
            player_ids = set(all_players_total)
            death_notices = []
            for dead_pid in get_dead_players(game_id):
                p = get_player_profile(dead_pid)
                if p:
                    death_notices.append(
                        {
                            "name": p.get("player_name") or str(dead_pid),
                            "role": p.get("role", ""),
                        }
                    )
            # An NPC whose replaces_player_id still matches a player registered
            # in the game holds the same seat as that player (counted once via
            # the player's row), so skip it to avoid duplicate notices.
            for n in all_npcs_total:
                if n.get("is_active"):
                    continue
                if n.get("replaces_player_id") in player_ids:
                    continue
                death_notices.append(
                    {
                        "name": n.get("npc_name", ""),
                        "role": n.get("role", ""),
                    }
                )

            # ── Generate outcome scene image ──────────────────────────
            outcome_image_url = None
            try:
                outcome_narrative = outcome.get("outcome_narrative", "")
                ship_status_str = outcome.get("ship_status_change", "")
                crew_morale_str = outcome.get("crew_morale_change", "")
                # Build a prompt from the outcome narrative
                outcome_prompt = (
                    f"Sci-fi cinematic scene illustrating the aftermath of events. "
                    f"{outcome_narrative[:600]} "
                    f"Ship status: {ship_status_str[:200]}. "
                    f"Crew morale: {crew_morale_str[:200]}. "
                    f"Dramatic lighting, starship interior or exterior, "
                    f"Star Trek aesthetic, 4K quality, cinematic composition."
                )
                image_gen = create_image_generator()
                outcome_image_url = await image_gen.generate_scene_image(prompt=outcome_prompt, filename_prefix=f"{game_id}/outcome_turn{turn}", width=1024, height=1024, game_id=game_id, player_id=None, turn=turn, kind="outcome")
                if outcome_image_url:
                    save_game_image(
                        type="outcome",
                        image_url=outcome_image_url,
                        game_id=game_id,
                        turn=turn,
                        prompt=outcome_prompt,
                    )
                    logger.info(f"[OUTCOME] Outcome image generated for turn {turn}: {outcome_image_url}")
                else:
                    logger.warning(f"[OUTCOME] Outcome image generation returned None for turn {turn}")
            except Exception as img_err:
                logger.warning(f"[OUTCOME] Failed to generate outcome image for turn {turn}: {img_err}")

            # Outcome recipients = ALL players in the game. Dead players become
            # spectators: they no longer act (excluded from generation and
            # briefings) but keep receiving turn outcomes so they can follow the
            # story. Previously the push went to alive players only, so a player
            # killed on turn N never learned what happened — not even their own
            # death. The alive-crew count (computed below via get_live_players)
            # is unaffected.
            outcome_recipients = get_players_in_game(game_id)

            # Compute crew counts for outcome display.
            # Total crew = all players + all NPCs ever in this game (dead/inactive
            # included), so the denominator stays stable across the whole game.
            # Previously this used len(all_briefings), which only reflects crew who
            # got a briefing THIS turn — dead members vanish from briefings, so the
            # denominator shrank ("9 из 9" instead of "9 из 10").
            # all_players_total / all_npcs_total / player_ids are already fetched
            # above for the death-notice roster; reuse them here.
            # An NPC with replaces_player_id set holds the same seat as a player
            # still registered in the game (e.g. a player who replaced that NPC, or
            # a dead player replaced by the NPC). The player's row already counts
            # the seat, so exclude such NPCs to avoid double-counting it.
            distinct_npc_total = [n for n in all_npcs_total if n.get("replaces_player_id") not in player_ids]
            total_crew = len(all_players_total) + len(distinct_npc_total)
            # Alive = live players + active NPCs (deaths already persisted above).
            alive_crew = len(get_live_players(game_id)) + len(get_all_active_npcs(game_id))

            # ── Await pending action image tasks ───────────────────────
            # Ensures action images (showing the consequences of player
            # actions) arrive BEFORE the outcome text, not after.
            action_key = (turn, game_id)
            pending = list(_pending_action_tasks.pop(action_key, set()))
            if pending:
                logger.info(f"[OUTCOME] Waiting for {len(pending)} action image(s) before pushing outcome for turn {turn}")
                results = await asyncio.gather(*pending, return_exceptions=True)
                for i, r in enumerate(results):
                    if isinstance(r, Exception):
                        logger.warning(f"[OUTCOME] Action image task {i} failed: {r}")

            # ── Recover action images lost to a container restart ──────
            # _pending_action_tasks is in-memory: if the container was
            # restarted between a player choosing and the image finishing,
            # the task is gone and chosen_action_url stays None forever.
            # Catch that here by regenerating any briefing that has a
            # selected action but no image yet.
            all_briefings_pre = get_all_briefings_for_turn(turn, game_id) or all_briefings
            missing = [
                b for b in all_briefings_pre
                if b.get("selected_action_id") and not b.get("chosen_action_url")
            ]
            if missing:
                logger.warning(
                    f"[OUTCOME] {len(missing)} briefing(s) with a selected action but no "
                    f"chosen_action_url for turn {turn} — regenerating (likely lost to a restart)"
                )
                recover_tasks = []
                for b in missing:
                    action_id = b["selected_action_id"]
                    if b.get("player_id"):
                        recover_tasks.append(
                            _generate_chosen_action_image(
                                player_id=b["player_id"],
                                game_id=game_id,
                                turn=turn,
                                action_id=action_id,
                                language=language,
                            )
                        )
                    elif b.get("npc_key"):
                        recover_tasks.append(
                            _generate_npc_chosen_action_image(
                                npc_key=b["npc_key"],
                                game_id=game_id,
                                turn=turn,
                                action_id=action_id,
                            )
                        )
                if recover_tasks:
                    rec_results = await asyncio.gather(*recover_tasks, return_exceptions=True)
                    for i, r in enumerate(rec_results):
                        if isinstance(r, Exception):
                            logger.warning(f"[OUTCOME] Action image recovery task {i} failed: {r}")

            # ── Build action images array with captions ────────────────
            # After awaiting all pending/recovered tasks, briefings now have chosen_action_url populated.
            # Format: 'Ход X — Имя — Роль — Действие'
            action_images = []
            all_briefings_fresh = get_all_briefings_for_turn(turn, game_id) or all_briefings
            gs = get_game_strings(language)
            caption_prefix = gs["turn_prefix_simple"].format(turn=turn)

            for b in all_briefings_fresh:
                action_url = b.get("chosen_action_url")
                if not action_url:
                    continue

                # Determine entity name and role
                player_id = b.get("player_id")
                npc_key = b.get("npc_key")

                if player_id:
                    p = get_player_profile(player_id)
                    if p:
                        entity_name = p.get("player_name", "") or str(player_id)
                        role_name = p.get("role", "")
                    else:
                        entity_name = str(player_id)
                        role_name = b.get("role", "")
                elif npc_key:
                    n = get_npc_profile(npc_key)
                    if n:
                        entity_name = n.get("npc_name", npc_key)
                        role_name = n.get("role", "")
                    else:
                        entity_name = npc_key
                        role_name = b.get("role", "")
                else:
                    continue

                # Find action text
                selected_id = b.get("selected_action_id")
                action_text = ""
                for c in b.get("choices", []):
                    if c.get("id") == selected_id:
                        action_text = c.get("text", c.get("description", ""))
                        break
                if not action_text:
                    action_text = selected_id or ""

                # Truncate action text for caption (max 60 chars)
                short_action = action_text[:57] + "..." if len(action_text) > 60 else action_text

                caption = f"{caption_prefix} — {entity_name} — {role_name} — {short_action}"
                action_images.append(
                    {
                        "image_url": action_url,
                        "caption": caption,
                        "player_id": player_id,
                        "npc_key": npc_key,
                    }
                )

            # ── Build injury notices for push ───────────────────────────
            # injury_notices were built where the wounds were persisted
            # (see the crew_injured handling above), so every notice maps to
            # an actually-applied wound with roster name/role resolved from
            # the entity_id.

            # ── Build personal outcomes for push ────────────────────────
            personal_outcomes = outcome.get("personal_outcomes", [])

            # ── One-time death notice to players who died this turn ────
            # The main outcome push goes to alive players only, so a just-killed
            # player would otherwise learn nothing. Match each newly-dead player
            # to their personal_outcomes entry (by name, then role) to surface
            # the cause of death, and push it directly to that player.
            for pid in newly_dead:
                p = get_player_profile(pid) or {}
                pname = p.get("player_name", "") or str(pid)
                prole = p.get("role", "")
                death_text = ""
                for po in personal_outcomes:
                    if po.get("character_name") == pname or po.get("role") == prole:
                        death_text = po.get("outcome_text", "")
                        break
                # Dramatic per-character death notice (title + narrative) instead
                # of the canned "You died in the line of duty!" line.
                death_notice = await gm.generate_death_notice(
                    language=language,
                    character_name=pname,
                    role=prole,
                    death_narrative=death_text,
                    outcome_narrative=outcome_text,
                    game_id=game_id,
                    player_id=str(pid),
                    turn=turn,
                    kind="death_notice",
                )
                death_title = death_notice.get("title", "") or ""
                death_narrative = death_notice.get("narrative", "") or death_text
                # Generate a death scene image (character avatar as reference).
                # Done before the push so the image URL rides along; a None URL
                # means the notice is still delivered, just without an image.
                death_image_url = await _generate_death_image(
                    player_id=pid,
                    game_id=game_id,
                    turn=turn,
                    death_narrative=death_narrative,
                    outcome_narrative=outcome_text,
                    language=language,
                )
                try:
                    await push_player_death(
                        player_id=pid,
                        turn=turn,
                        game_id=game_id,
                        death_title=death_title,
                        death_narrative=death_narrative,
                        outcome_narrative=outcome_text,
                        death_image_url=death_image_url,
                        character_name=pname,
                        role=prole,
                        language=language,
                    )
                    logger.info(f"[DEATH] Death notice pushed to player {pid} for turn {turn}")
                except Exception:
                    logger.error(f"[DEATH] Failed to push death notice to player {pid}", exc_info=True)

            # Enrich mission_progress deltas with stage names + cumulative progress
            # so the push can show "Этап N: <name> (progress/threshold)" instead of
            # a bare "этап N". mission already holds updated stage_progress here
            # (apply_mission_progress ran above).
            mission_stages_recap: list[dict] = []
            if mission:
                objectives = mission.get("objectives", [])
                stage_progress = mission.get("stage_progress", {})
                for obj in objectives:
                    stage = obj.get("stage")
                    threshold = obj.get("success_threshold", 0)
                    progress = stage_progress.get(str(stage), 0)
                    mission_stages_recap.append(
                        {
                            "stage": stage,
                            "name": obj.get("name", ""),
                            "progress": progress,
                            "threshold": threshold,
                            "completed": progress >= threshold,
                        }
                    )
                obj_by_stage = {o.get("stage"): o for o in objectives}
                mission_progress = [
                    {
                        "stage": entry.get("stage"),
                        "points": entry.get("points", 0),
                        "name": obj_by_stage.get(entry.get("stage"), {}).get("name", ""),
                    }
                    for entry in mission_progress
                ]

            # Push outcome synchronously so message order is deterministic
            # (outcome arrives BEFORE new turn briefings)
            try:
                await push_turn_outcome(
                    game_id=game_id,
                    turn=turn,
                    outcome_text=outcome_text,
                    alive_players=outcome_recipients,
                    outcome_image_url=outcome_image_url,
                    ship_status="destroyed" if ship_destroyed else "alive",
                    mission_progress=mission_progress,
                    mission_stages_recap=mission_stages_recap,
                    death_notices=death_notices,
                    injury_notices=injury_notices,
                    personal_outcomes=personal_outcomes,
                    action_images=action_images,
                    language=language,
                    ship_hull_integrity=ship_hull,
                    ship_shields=ship_shields,
                    ship_systems_offline=ship_systems_offline,
                    threat_level=new_threat,
                    total_crew_count=total_crew,
                    alive_crew_count=alive_crew,
                )
                logger.info(f"[OUTCOME] Outcome delivered for turn {turn} to {len(outcome_recipients)} players")
            except Exception as push_err:
                logger.error(f"[OUTCOME] Failed to deliver outcome for turn {turn}: {push_err}", exc_info=True)

            # ── Game Over: generate and deliver finale ──────────────────
            game_ended = mission_completed or ship_destroyed or crew_wiped or threat_overwhelmed or mutiny_happened
            if game_ended:
                try:
                    # Outcome matrix: the verdict is computed by CODE from the
                    # end-state; the LLM only writes it up in the matching tone.
                    mission_progress_ratio = 0.0
                    if mission:
                        threshold_total = sum(o.get("success_threshold", 0) for o in mission.get("objectives", []))
                        if threshold_total > 0:
                            mission_progress_ratio = sum(mission.get("stage_progress", {}).values()) / threshold_total
                    alive_crew_ratio = (alive_crew / total_crew) if total_crew > 0 else 1.0
                    outcome_type = compute_outcome_type(
                        mission_completed=mission_completed,
                        mission_progress_ratio=mission_progress_ratio,
                        hull_ratio=ship_hull / HULL_MAX,
                        alive_crew_ratio=alive_crew_ratio,
                        threat_level=new_threat,
                        ship_destroyed=ship_destroyed,
                        crew_wiped=crew_wiped,
                    )
                    logger.info(f"[GAME_OVER] Game ended: {outcome_type}, generating finale...")

                    # Build mission summary (dry facts) for the LLM prompt
                    stage_word = "Этап" if language == LANGUAGE_RU else "Stage"
                    mission_summary_parts = []
                    if mission:
                        for obj in mission.get("objectives", []):
                            stage = obj.get("stage", "?")
                            name = obj.get("name", "")
                            progress = mission.get("stage_progress", {}).get(str(stage), 0)
                            threshold = obj.get("success_threshold", "?")
                            done = "✓" if progress >= threshold else "✗"
                            mission_summary_parts.append(f"{done} {stage_word} {stage}: {name} ({progress}/{threshold})")
                    mission_summary = "\n".join(mission_summary_parts) if mission_summary_parts else "No mission data"

                    # outcome_type is the machine token ("triumph"/"victory"/"pyrrhic"/
                    # "stalemate"/"defeat") used for fallback lookup and the rules-verdict
                    # line; outcome_label is the human-readable header shown to the LLM
                    # alongside that token. Mixing them once routed a victory to the
                    # defeat fallback.
                    gs = get_game_strings(language)
                    go_msgs = gs.get("game_over", {})
                    outcome_label = go_msgs.get(f"{outcome_type}_header", outcome_type)

                    # Why the game ended (terminal status → localized facts line).
                    # Priority mirrors the DB write order: ship_destroyed wins
                    # over crew_wiped when both hit in one turn (end_game skips
                    # the crew_wiped write if the ship already went down), and
                    # a mutiny matters more than the clock.
                    if mutiny_happened:
                        end_status = "mutiny"
                    elif ship_destroyed:
                        end_status = "ship_destroyed"
                    elif crew_wiped:
                        end_status = "crew_wiped"
                    elif threat_overwhelmed:
                        end_status = "overwhelmed"
                    else:
                        end_status = "mission_complete"
                    end_reason = go_msgs[f"reason_{end_status}"]

                    gm = create_game_server(language=language)
                    game_over = await gm.generate_game_over_outcome(
                        outcome_type=outcome_type, outcome_label=outcome_label, outcome_narrative=outcome_text[:2000], mission_summary=mission_summary,
                        end_reason=end_reason,
                        hull=ship_hull, shields=ship_shields, threat=new_threat,
                        dead_crew_count=total_crew - alive_crew, alive_crew_count=alive_crew, turns_played=turn,
                        game_id=game_id, player_id=None, turn=turn, kind="game_over_outcome"
                    )

                    finale_narrative = game_over.get("finale_narrative", "")
                    finale_image_prompt = game_over.get("finale_image_prompt", "")

                    # Generate finale image via ComfyUI
                    finale_image_url = None
                    if finale_image_prompt:
                        try:
                            image_gen = create_image_generator()
                            finale_image_url = await image_gen.generate_scene_image(
                                prompt=finale_image_prompt,
                                filename_prefix=f"{game_id}/finale_{outcome_type}",
                                width=1024,
                                height=1024,
                                game_id=game_id,
                                player_id=None,
                                turn=turn,
                                kind="finale",
                            )
                            if finale_image_url:
                                save_game_image(
                                    type="finale",
                                    image_url=finale_image_url,
                                    game_id=game_id,
                                    turn=turn,
                                    prompt=finale_image_prompt,
                                )
                                logger.info(f"[GAME_OVER] Finale image generated: {finale_image_url}")
                        except Exception as img_err:
                            logger.warning(f"[GAME_OVER] Failed to generate finale image: {img_err}")

                    # Build available games list (excluding this finished game)
                    all_games = get_available_games()
                    available_games = []
                    for game in all_games:
                        if game["game_id"] == game_id:
                            continue
                        gid = game["game_id"]
                        available_games.append(
                            {
                                "game_id": gid,
                                "name": get_game_title(gid) or game.get("name", ""),
                                "player_count": get_player_count_in_game(gid),
                                "language": get_game_language(gid),
                            }
                        )

                    await push_game_over(
                        game_id=game_id,
                        finale_narrative=finale_narrative or outcome_text[:1000],
                        finale_image_url=finale_image_url,
                        outcome_type=outcome_type,
                        alive_players=outcome_recipients,
                        available_games=available_games,
                        language=language,
                    )
                    logger.info(f"[GAME_OVER] Finale delivered to {len(outcome_recipients)} players: {outcome_type}")

                    # Persist finale so /turn can replay it later
                    save_game_finale(
                        game_id=game_id,
                        finale_narrative=finale_narrative or outcome_text[:2000],
                        finale_outcome_type=outcome_type,
                        finale_image_url=finale_image_url or "",
                    )

                    # ── Mission summary: compact stats right after the finale ──
                    action_stats = get_game_action_stats(game_id=game_id)
                    stats_players = []
                    for p in action_stats["players"]:
                        stats_profile = get_player_profile(p["player_id"])
                        stats_players.append(
                            {
                                "name": (stats_profile or {}).get("player_name") or str(p["player_id"]),
                                "actions": p["actions"],
                                "auto_actions": p["auto_actions"],
                            }
                        )
                    summary_text = format_game_summary(
                        language,
                        outcome_label=outcome_label,
                        end_status=end_status,
                        turns=turn,
                        hull=ship_hull,
                        shields=ship_shields,
                        threat=new_threat,
                        dead_names=[n.get("name", "") for n in death_notices],
                        alive_crew=alive_crew,
                        total_crew=total_crew,
                        player_stats=stats_players,
                    )
                    await push_game_summary(
                        game_id=game_id,
                        text=summary_text,
                        player_ids=outcome_recipients,
                    )
                    logger.info(f"[GAME_OVER] Summary delivered to {len(outcome_recipients)} players")
                except Exception as go_err:
                    logger.error(f"[GAME_OVER] Finale generation/delivery failed: {go_err}", exc_info=True)

        except Exception as e:
            logger.error(f"[OUTCOME] Analysis failed for Turn {turn}: {e}", exc_info=True)


# ============== Admin endpoints ==============


@app.post("/admin/create-game")
async def admin_create_game(request: CreateGameRequest):
    """Create a new game with a generated game_id."""
    game_id = generate_game_id(6)

    game_data = {
        "game_id": game_id,
        "name": request.name,
        "description": request.description,
        "setting": "starship",
        "status": "active",
        "max_players": 10,
        "language": request.language,
    }

    game = create_game(game_data)
    if not game:
        raise HTTPException(status_code=500, detail="Failed to create game")

    # Register the game with the scheduler using the schedule chosen at
    # creation (falls back to the scheduler env default when not provided).
    if request.schedule:
        await _register_game_in_scheduler(game_id, request.schedule)

    # Generate the linked game concept (mission + title + welcome) once, at
    # game creation. The title tagline and welcome are derived from the
    # mission so they stay consistent; subsequent onboardings reuse them.
    try:
        concept = await _generate_game_concept(game_id, request.language)
        if concept["mission"]:
            await _generate_started_game_assets(game_id, request.language)
    except Exception:
        logger.warning("Game concept generation for new game %s failed", game_id, exc_info=True)

    return {
        "status": "success",
        "game_id": game_id,
        "name": get_game_title(game_id) or request.name,
        "language": request.language,
        "message": f"Game {game_id} created successfully",
    }


# Tracks in-flight language changes so a repeated /gm_lang for the same game
# is answered "already in progress" instead of stacking a second regeneration.
_language_changes_in_flight: set[str] = set()
# Keeps strong references to background language-change tasks so the GC does
# not cancel them before they finish.
_language_change_tasks: set = set()


@app.post("/admin/set-language")
async def admin_set_language(request: SetLanguageRequest):
    """Set the language for a game and regenerate its title, mission, splash and
    bridge image in the background. Blocked once the game has started (turns
    reference the mission stages, so regenerating would desync the story).

    Returns immediately with status "accepted" (or "in_progress" if a change is
    already running for this game); the GM is notified of completion via
    /push/gm-notification."""
    game = get_game(request.game_id)
    if not game:
        raise HTTPException(status_code=404, detail=f"Game {request.game_id} not found")

    if request.language not in ("ru", "en"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid language '{request.language}'. Supported: ru, en",
        )

    if is_game_started(request.game_id):
        raise HTTPException(
            status_code=409,
            detail=f"Game {request.game_id} already started; cannot change language",
        )

    if request.game_id in _language_changes_in_flight:
        return {"status": "in_progress", "game_id": request.game_id, "language": request.language}

    set_game_language(request.game_id, request.language)
    logger.info(f"Language for game {request.game_id} set to '{request.language}'")

    _language_changes_in_flight.add(request.game_id)
    task = asyncio.create_task(_run_language_change(request.game_id, request.language))
    _language_change_tasks.add(task)
    task.add_done_callback(_language_change_tasks.discard)

    return {"status": "accepted", "game_id": request.game_id, "language": request.language}


async def _run_language_change(game_id: str, language: str) -> None:
    """Background task: regenerate mission/title/splash/bridge after a language
    change, notify players, then push a completion (or error) notification to the
    GM. Always clears the in-flight flag for this game when done."""
    new_title = ""
    new_mission_name = ""
    try:
        gm = create_game_server(language=language)

        # Build participant list from live players + active NPCs (used only for the
        # crew-aware bridge image; mission/title/splash are plot-driven).
        all_participants = []
        try:
            for pid in get_live_players(game_id):
                profile = get_player_profile(pid)
                if profile:
                    avatar_desc = _extract_avatar_prompt(profile.get("avatar_description", "") or "")
                    all_participants.append(
                        {
                            "type": "player",
                            "player_id": pid,
                            "player_name": profile.get("player_name", "") or "",
                            "role": profile["role"],
                            "species": profile.get("species", ""),
                            "personality_traits": profile.get("personality_traits", []),
                            "role_description": profile.get("role_description", ""),
                            "avatar_description": avatar_desc,
                            "species_description": profile.get("species_description", "") or "",
                        }
                    )
            for npc in get_all_active_npcs(game_id):
                avatar_desc = _extract_avatar_prompt(npc.get("avatar_description", "") or "")
                all_participants.append(
                    {
                        "type": "npc",
                        "npc_key": npc["npc_key"],
                        "npc_name": npc.get("npc_name", npc.get("role", "NPC")),
                        "role": npc["role"],
                        "species": npc.get("species", ""),
                        "personality_traits": npc.get("personality_traits", []),
                        "role_description": npc.get("role_description", ""),
                        "avatar_description": avatar_desc,
                    }
                )
        except Exception:
            logger.warning("Failed to build participants for %s", game_id, exc_info=True)

        # Mission + title + splash are plot-driven: regenerate regardless of
        # whether participants exist yet (before the game starts nobody may have
        # finished onboarding, but the concept must match the new language).
        # Old mission and images (splash/bridge/background_*) are removed first.
        delete_mission(game_id)
        delete_game_images(game_id)

        new_welcome = ""
        mission_data: dict | None = None
        try:
            mission_data = await gm.generate_mission(game_id=game_id, player_id=None, turn=None, kind="mission")
            mission_result = create_mission(mission_data, game_id)
            if mission_result:
                new_mission_name = mission_result.get("name", "")
                logger.info(f"Regenerated mission in {language}: {new_mission_name}")
                try:
                    title_data = await gm.generate_game_title(game_id=game_id, player_id=None, turn=None, kind="game_title", mission_context=mission_result)
                    new_title = title_data.get("title", "")
                    new_welcome = title_data.get("welcome_text", "")
                    if new_title:
                        save_game_title_and_welcome(game_id, new_title, new_welcome)
                        logger.info(f"Regenerated game title in {language}: {new_title}")
                except Exception:
                    logger.warning("Failed to regenerate game title for %s", game_id, exc_info=True)
        except Exception:
            logger.warning("Failed to regenerate mission for %s", game_id, exc_info=True)

        # Regenerate splash images (deleted above) from the new title/welcome.
        if new_title:
            try:
                logger.info(f"[SPLASH] Regenerating 3 splash images for {game_id} ({language})")
                image_gen = create_image_generator()
                urls = await image_gen.generate_splash_images(
                    game_title=new_title,
                    welcome_text=new_welcome,
                    count=3,
                    filename_prefix="splash",
                    game_id=game_id,
                    width=1024,
                    height=768,
                )
                saved = sum(1 for url in urls if url and save_game_image(type="splash", image_url=url, game_id=game_id, turn=None, prompt=""))
                logger.info(f"[SPLASH] Saved {saved}/3 splash images for {game_id}")
            except Exception:
                logger.error(f"[SPLASH] Regeneration failed for {game_id}", exc_info=True)

        # Bridge image is crew-aware: only regenerate once participants exist.
        if all_participants:
            try:
                bridge_result = await gm.generate_bridge_image_prompt(mission_data or {}, all_participants, game_id=game_id, player_id=None, turn=None, kind="bridge_image_prompt")
                bridge_prompt = bridge_result.get("bridge_prompt", "")
                if bridge_prompt:
                    image_gen = create_image_generator()
                    bridge_url = await image_gen.generate_scene_image(
                        prompt=bridge_prompt,
                        filename_prefix=f"{game_id}/bridge",
                        width=1024,
                        height=1024,
                        game_id=game_id,
                        player_id=None,
                        turn=None,
                        kind="bridge",
                    )
                    if bridge_url:
                        save_game_image(
                            type="bridge",
                            image_url=bridge_url,
                            game_id=game_id,
                            turn=None,
                            prompt=bridge_prompt,
                        )
                        logger.info(f"Regenerated bridge image: {bridge_url}")
            except Exception as e:
                logger.warning(f"Failed to regenerate bridge image: {e}")

        # Notify players who started or finished onboarding that the language
        # changed (best-effort, fire-and-forget).
        try:
            player_ids = list(dict.fromkeys(get_onboarding_player_ids_in_game(game_id) + get_live_players(game_id)))
            if player_ids:
                asyncio.create_task(push_language_changed(game_id, player_ids, language))
                logger.info(f"[PUSH] Queued language-changed notification to {len(player_ids)} player(s) for {game_id}")
        except Exception:
            logger.warning("Failed to queue language-changed push for %s", game_id, exc_info=True)

        logger.info(f"[LANGUAGE] Change completed for game {game_id} -> {language}, title='{new_title}'")
        await push_gm_notification(
            game_id=game_id,
            turn=0,
            status="language_changed",
            error="",
            players=0,
            npcs=0,
            language=language,
            title=new_title,
            mission_name=new_mission_name,
        )
    except Exception as e:
        logger.error(f"[LANGUAGE] Change failed for game {game_id}: {e}", exc_info=True)
        await push_gm_notification(
            game_id=game_id,
            turn=0,
            status="language_changed_error",
            error=str(e),
            players=0,
            npcs=0,
            language=language,
        )
    finally:
        _language_changes_in_flight.discard(game_id)


def _build_player_briefings_for_push(
    all_briefings: list[dict],
    crew_dialogues: list[dict],
    turn_num: int,
    game_id: str,
) -> list[dict]:
    """Build per-player briefing dicts for push payload from stored briefings.

    Fetches scene image (if available) from game_images table for this turn.
    Also fetches player_name for each real player to include in the payload.
    """
    # Fetch scene image for this turn (if generated and saved)
    scene_url = get_random_game_image(type="scene", game_id=game_id, turn=turn_num)
    players_data = []
    for b in all_briefings:
        if b.get("is_npc"):
            continue  # Only send to real players
        player_id = b.get("player_id")
        if not player_id:
            continue
        # Skip kicked players — they are out of the game
        if is_player_kicked(player_id, game_id):
            continue
        # Get player_name from profile
        p = get_player_profile(player_id)
        player_name = (p.get("player_name", "") or "") if p else ""
        # Get personal_title from briefing (LLM-generated) or build fallback
        personal_title = b.get("personal_title", "")
        players_data.append(
            {
                "player_id": player_id,
                "player_name": player_name,
                "personal_title": personal_title,
                "role": b.get("role", ""),
                "briefing": b.get("briefing", ""),
                "choices": b.get("choices", []),
                "chosen_action_url": b.get("chosen_action_url"),
                "scene_url": scene_url,
                "character_image_url": b.get("character_image_url"),
            }
        )
    return players_data


@app.post("/admin/generate-comic/{player_id}")
async def generate_chosen_action_image(
    player_id: int,
    turn: int | None,
    *,
    game_id: str,
):
    """Generate a chosen action image for a player (admin endpoint)."""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    state = get_game_state(game_id)
    turn_num_val = turn if turn else state["turn"]
    turn_data = get_game_turn(turn_num_val, game_id)
    if not turn_data:
        raise HTTPException(status_code=404, detail="Game turn not found")

    image_generator = create_image_generator()
    role = profile["role"]
    traits = profile["personality_traits"]
    # Generate prompt via LLM if game_server is available
    prompt = ""
    try:
        game_lang = get_game_language(game_id)
        gm = create_game_server(language=game_lang)
        prompt = await gm.generate_chosen_action_prompt(
            role=role,
            traits=traits,
            avatar_description=profile.get("avatar_description", ""),
            action_text=turn_data["story"][:200],
            setting=turn_data["story"][:300],
            species_desc=profile.get("species_description", ""),
            species_type=profile.get("species", ""),
            species_category=profile.get("species_primary_key") or "",
            game_id=game_id,
            player_id=str(player_id),
            turn=turn_num_val,
            kind="chosen_action_prompt",
        )
    except Exception as e:
        logger.warning(f"[ADMIN] LLM prompt failed: {e}")

    if not prompt:
        prompt = (
            f"{role} performing a critical action during a space mission. "
            f"Story: {turn_data['story'][:200]}. "
            f"Character traits: {', '.join(traits)}. "
            f"Dynamic composition, dramatic lighting, detailed environment. "
            f"Cinematic space opera aesthetic, photorealistic quality, 4K."
        )

    chosen_action_url = await image_generator.generate_scene_image(
        prompt=prompt, filename_prefix=f"{game_id}/action_turn{turn_num_val}_p{player_id}", width=1024, height=1024, game_id=game_id, player_id=str(player_id), turn=turn_num_val, kind="player_action"
    )

    # Store chosen_action_url in player's briefing for this turn (if briefing exists)
    briefing = get_player_briefing(turn_num_val, player_id, game_id)
    if briefing:
        update_briefing_chosen_action_url(briefing["id"], chosen_action_url)

    return {
        "player_id": player_id,
        "turn": turn_num_val,
        "chosen_action_url": chosen_action_url,
        "role": profile["role"],
    }


@app.post("/admin/generate-loading-images")
async def admin_generate_loading_images(count: int, *, game_id: str):
    """Manually trigger generation of loading screen images."""
    logger.info(f"[ADMIN] Generating {count} loading images for game {game_id}")

    try:
        image_generator = create_image_generator()
        urls = await image_generator.generate_loading_images(
            count=count,
            start_index=0,
            filename_prefix="loading",
            game_id=game_id,
            width=768,
            height=768,
        )

        saved = 0
        for url in urls:
            if url:
                save_game_image(type="loading", image_url=url, game_id=game_id, turn=None, prompt="")
                saved += 1

        return {
            "status": "success",
            "requested": count,
            "generated": len(urls),
            "saved": saved,
            "total_in_db": get_game_image_count("loading", game_id, None),
        }
    except Exception as e:
        logger.error(f"[ADMIN] Loading image generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/admin/generate-splash-images")
async def admin_generate_splash_images(game_id: str, language: str):
    """Generate 3 splash images for the game using current game title.

    If the game has no title yet, uses a fallback.
    """
    logger.info(f"[ADMIN] Generating splash images for game {game_id}")

    gs = get_game_strings(language)
    game_title = get_game_title(game_id) or gs["game_title_fallback"]
    welcome_text = "Космический корабль в глубинах неизведанного космоса."

    try:
        image_generator = create_image_generator()
        urls = await image_generator.generate_splash_images(
            game_title=game_title,
            welcome_text=welcome_text,
            count=3,
            filename_prefix="splash",
            game_id=game_id,
            width=1024,
            height=768,
        )

        saved = 0
        for url in urls:
            if url:
                save_game_image(type="splash", image_url=url, game_id=game_id, turn=None, prompt="")
                saved += 1

        return {
            "status": "success",
            "requested": 3,
            "generated": len(urls),
            "saved": saved,
            "total_in_db": get_game_image_count("splash", game_id, None),
        }
    except Exception as e:
        logger.error(f"[ADMIN] Splash image generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


# Species and gender options for NPC randomization
_NPC_SPECIES_OPTIONS = ["human", "humanoid", "non_humanoid", "cybernetic"]
_NPC_GENDER_OPTIONS = {
    "ru": {
        "male": "Мужской",
        "female": "Женский",
        "neutral": "Нейтральный",
        "fluid": "Сменяемый",
        "synthetic": "Синтетический",
    },
    "en": {
        "male": "Male",
        "female": "Female",
        "neutral": "Neutral",
        "fluid": "Fluid",
        "synthetic": "Synthetic",
    },
}


def _extract_avatar_prompt(avatar_description: str) -> str:
    """Extract the text prompt from an avatar_description field.

    The field may contain 'avatar_url=<url>;<prompt>' after avatar generation.
    Strip the URL prefix and return just the prompt.
    """
    if not avatar_description:
        return ""
    if avatar_description.startswith("avatar_url="):
        parts = avatar_description.split(";", 1)
        return parts[1] if len(parts) > 1 else ""
    return avatar_description


def _extract_avatar_url(avatar_description: str) -> str | None:
    """Extract the image URL from an avatar_description field.

    NPCs store avatar URLs as 'avatar_url=<url>;<prompt>'. Players store
    avatar_url directly in a separate column. This function extracts the URL
    from the combined format.
    """
    if not avatar_description:
        return None
    if avatar_description.startswith("avatar_url="):
        # Format: avatar_url=https://example.com/img.png;description text
        parts = avatar_description.split(";", 1)
        return parts[0].replace("avatar_url=", "", 1)
    return None


def _get_crew_members(game_id: str) -> list[dict[str, Any]]:
    """Get all crew members (players + NPCs) for dialogue generation.

    Returns a list of dicts with 'name' and 'role' keys, plus optional
    'personality_traits' and 'species'. Used by generate_crew_dialogues.
    """
    crew: list[dict[str, Any]] = []

    # Add real players (dead players don't get crew dialogues)
    for pid in get_live_players(game_id):
        p = get_player_profile(pid)
        if not p:
            continue
        crew.append(
            {
                "name": p.get("player_name", "") or p.get("role", "Crew"),
                "role": p.get("role", "Crew Member"),
                "species": p.get("species", ""),
                "personality_traits": p.get("personality_traits", []),
            }
        )

    # Add NPCs
    for npc in get_all_active_npcs(game_id):
        crew.append(
            {
                "name": npc.get("npc_name", "") or npc.get("role", "NPC"),
                "role": npc.get("role", "NPC"),
                "species": npc.get("species", ""),
                "personality_traits": npc.get("personality_traits", []),
            }
        )

    return crew


def _random_npc_species() -> str:
    """Pick a random species key for NPC generation."""
    return secrets.choice(_NPC_SPECIES_OPTIONS)


def _random_npc_gender(language: str) -> str:
    """Pick a random localized gender display name for NPC.

    Returns a display name (e.g. "Мужской" or "Male") rather than a key.
    """
    lang_key = LANGUAGE_RU if language == LANGUAGE_RU else LANGUAGE_EN
    gender_key = secrets.choice(list(_NPC_GENDER_OPTIONS[lang_key].keys()))
    return _NPC_GENDER_OPTIONS[lang_key][gender_key]


async def _run_generation_with_job(
    game_id: str,
    turn: int,
    job_type: str,
    coro,
    *,
    resume_job_id: int | None,
):
    """Run a generation coroutine under a generation-job lock.

    Normal launch (resume_job_id is None): refuse to start if a generation is
    already in progress for the game (lock), then record a new in_progress job.
    Resume launch (resume_job_id set, used by the startup sweep): reuse the
    existing in_progress job. The job is marked done on success or failed on
    error. Returns the coroutine's result, or None if skipped due to the lock.
    """
    if resume_job_id is not None:
        job_id = resume_job_id
        logger.info(f"[GEN_JOB] Resuming generation job {job_id} for {game_id} turn {turn}")
    else:
        active = get_active_generation_job(game_id)
        if active:
            logger.warning(f"[GEN_JOB] Generation already in progress for {game_id} (job {active['id']} turn {active['turn']}), skipping new {job_type}")
            return None
        job = start_generation_job(game_id, turn, job_type)
        job_id = job["id"]
        logger.info(f"[GEN_JOB] Started {job_type} job {job_id} for {game_id} turn {turn}")
    try:
        result = await coro
        complete_generation_job(job_id)
        logger.info(f"[GEN_JOB] Completed job {job_id} for {game_id}")
        return result
    except Exception as e:
        fail_generation_job(job_id, str(e))
        logger.error(f"[GEN_JOB] Failed job {job_id} for {game_id}: {e}", exc_info=True)
        raise


async def _resume_interrupted_generations() -> None:
    """Re-launch generation jobs left in_progress by a previous shutdown/crash.

    Runs at startup. Relies on the per-step idempotency guards inside
    _original_start_game / _original_continue_game to skip already-completed
    work (mission, bridge, global circumstances, scene image, per-participant
    briefings) and only finish what remains. Briefings are pushed to players by
    the generation functions themselves once they complete.
    """
    try:
        jobs = get_in_progress_generation_jobs()
        if not jobs:
            return
        logger.info(f"[GEN_JOB] Found {len(jobs)} interrupted generation job(s); resuming")
        for job in jobs:
            game_id = job["game_id"]
            turn = job["turn"]
            job_type = job["job_type"]
            language = get_game_language(game_id)
            logger.info(f"[GEN_JOB] Resuming {job_type} job {job['id']} for {game_id} turn {turn}")
            if job_type == "start":
                req = StartGameRequest(game_id=game_id, language=language, force=True, was_restarted=False)
                asyncio.create_task(_run_generation_with_job(game_id, turn, "start", _original_start_game(req), resume_job_id=job["id"]))
            elif job_type == "continue":
                asyncio.create_task(
                    _run_generation_with_job(
                        game_id,
                        turn,
                        "continue",
                        _original_continue_game(game_id=game_id, language=language, force_resend=False),
                        resume_job_id=job["id"],
                    )
                )
    except Exception:
        logger.error("[GEN_JOB] Failed to resume interrupted generations", exc_info=True)


async def _background_start_wrapper(request: StartGameRequest, turn_num: int):
    """Run start-game in background, notify GM on completion."""
    try:
        result = await _run_generation_with_job(request.game_id, turn_num, "start", _original_start_game(request), resume_job_id=None)
        if result and result.get("status") == "success":
            await _notify_scheduler("reset", game_id=request.game_id)
            await push_gm_notification(
                game_id=request.game_id,
                turn=turn_num,
                status="success",
                error="",
                players=result.get("player_count", 0),
                npcs=result.get("npc_count", 0),
                language=request.language,
            )
    except Exception as e:
        logger.error(f"[BACKGROUND] Start game failed for {request.game_id}: {e}", exc_info=True)
        await push_gm_notification(
            game_id=request.game_id,
            turn=turn_num,
            status="error",
            error=str(e),
            players=0,
            npcs=0,
            language=request.language,
        )


@app.post("/admin/start-game")
async def admin_start_game(request: StartGameRequest):
    """Force-start the game in background.

    Validates prerequisites, starts background generation,
    returns immediately. GM gets push notification when done.
    """
    # Use game's stored language if available
    request.language = get_game_language(request.game_id) or request.language
    logger.info("=== ADMIN START GAME (async) ===")
    logger.info(f"game_id={request.game_id}, language={request.language}")

    game_id = request.game_id

    # Validate: game must have players
    player_ids = get_players_in_game(game_id)
    if len(player_ids) == 0:
        raise HTTPException(status_code=400, detail="No players have joined the game yet")

    state = get_game_state(game_id)
    turn_num = state["turn"]

    # Start background generation
    asyncio.create_task(_background_start_wrapper(request, turn_num))

    logger.info(f"Background game start for {game_id}, current turn={turn_num}")

    return {
        "status": "accepted",
        "turn": turn_num,
        "player_count": len(player_ids),
        "message": f"Game start for {game_id} accepted. You will be notified when ready.",
    }


async def _original_start_game(request: StartGameRequest):
    """Original start-game logic (runs in background)."""
    logger.info("=== ADMIN START GAME ===")
    logger.info(f"game_id={request.game_id}, language={request.language}")

    game_id = request.game_id
    language = request.language

    # 1. Get all players in the game
    player_ids = get_players_in_game(game_id)
    real_player_count = len(player_ids)
    logger.info(f"Real players in game: {real_player_count} — {player_ids}")

    if real_player_count == 0:
        raise HTTPException(status_code=400, detail="No players have joined the game yet")

    # 2. Get available (unfilled) roles
    available_roles = get_available_roles(game_id, language=language)
    logger.info(f"Available (unfilled) roles: {[r['role_key'] for r in available_roles]}")

    # 2.b Re-assign roles to existing players (important after restart reset_roles)
    for pid in player_ids:
        profile = get_player_profile(pid)
        if not profile:
            continue

        player_role = profile.get("role", "")
        player_role_en = profile.get("role_name_en", "")

        for role_data in available_roles:
            if role_data["role_name"] == player_role or role_data["role_name_en"] == player_role or role_data["role_name_en"] == player_role_en:
                taken = take_role(role_data["role_key"], pid, game_id)
                if taken:
                    logger.info(f"[ROLE] Re-assigned role {role_data['role_key']} to player {pid}")
                break

    # Refresh available_roles (some may have been re-taken)
    available_roles = get_available_roles(game_id, language=language)
    logger.info(f"Available roles after re-assignment: {[r['role_key'] for r in available_roles]}")

    # 3. Create NPCs for unfilled roles — capped at NPC_COUNT seats by the
    # rules layer (keeps crew_wiped reachable and turn generation lean).
    # A seat vacated later (kick/reset/death) stays empty: departing players
    # are no longer replaced by NPCs.
    npc_role_keys = set(select_npc_role_keys([r["role_key"] for r in available_roles]))
    available_roles = [r for r in available_roles if r["role_key"] in npc_role_keys]
    npcs_created = []
    gm = create_game_server(language=language)
    _npc_turn = get_game_state(game_id)["turn"]

    # Collect names to avoid: player names + existing NPC names being reused
    avoid_names: set[str] = set()
    for pid in player_ids:
        p = get_player_profile(pid)
        if p and p.get("player_name"):
            avoid_names.add(p["player_name"])

    for role_data in available_roles:
        role_key = role_data["role_key"]
        role_name = role_data["role_name"]
        npc_key = f"npc_{role_key}_{game_id}"

        # Check if NPC already exists for this role
        existing = get_npc_by_role(role_key, game_id)
        if existing:
            npcs_created.append(existing)
            if existing.get("npc_name"):
                avoid_names.add(existing["npc_name"])
            continue

        # Randomize species and gender for this NPC
        npc_species = _random_npc_species()
        npc_gender = _random_npc_gender(language)

        # Generate per-character role flavour via LLM (replaces static
        # SHIP_ROLES_I18N). Done before name generation so the name can
        # match the generated personality/visual description.
        npc_flavour = await gm.generate_role_flavour(
            role_key=role_key,
            role_name=role_name,
            species_display=npc_species,
            gender_display=npc_gender,
            traits=[],
            game_id=game_id,
            player_id=None,
            turn=_npc_turn,
            kind=f"npc_role_flavour_{role_key}",
        )

        # Generate creative name via LLM (with fallback), avoid duplicates
        npc_name_attempt = await gm.generate_npc_name(
            role_key=role_key,
            role_name=role_name,
            species=npc_species,
            gender=npc_gender,
            avatar_description=npc_flavour["avatar_description"],
            personality_traits=npc_flavour["personality_traits"],
            avoid_names=avoid_names,
            game_id=game_id,
            player_id=None,
            turn=_npc_turn,
            kind=f"npc_name_{role_key}",
        )
        # If LLM returned a name WITH role prefix (e.g. "Инженер Дмитрий Волков"),
        # strip it — the role is already shown separately in UI
        if npc_name_attempt:
            # Remove leading role prefix if present (e.g. "Инженер " → "")
            for prefix in [f"{role_name} ", f"{role_data.get('role_name_en', '')} "]:
                if npc_name_attempt.startswith(prefix):
                    npc_name_attempt = npc_name_attempt[len(prefix) :]
                    break
            avoid_names.add(npc_name_attempt)

        npc_data = {
            "npc_key": npc_key,
            "role_key": role_key,
            "npc_name": npc_name_attempt,
            "role": role_name,
            "role_description": npc_flavour["role_description"],
            "personality_traits": npc_flavour["personality_traits"],
            "species": npc_species,
            "gender": npc_gender,
            "avatar_description": npc_flavour["avatar_description"],
            "game_id": game_id,
            "is_active": True,
        }
        npc = create_npc_profile(npc_data)
        if npc:
            npcs_created.append(npc)
            logger.info(f"[NPC] Created NPC {npc_key} for role {role_key}: {npc_name_attempt} ({npc_species}, {npc_gender})")

    # 4. Mark game as started
    start_game(game_id)

    # 5. Build combined roster (real players + NPCs)
    all_participants = []

    for pid in player_ids:
        profile = get_player_profile(pid)
        if profile:
            avatar_desc = _extract_avatar_prompt(profile.get("avatar_description", "") or "")
            all_participants.append(
                {
                    "type": "player",
                    "player_id": pid,
                    "player_name": profile.get("player_name", "") or "",
                    "role": profile["role"],
                    "species": profile.get("species", ""),
                    "personality_traits": profile.get("personality_traits", []),
                    "role_description": profile.get("role_description", ""),
                    "avatar_description": avatar_desc,
                    "species_description": profile.get("species_description", "") or "",
                }
            )

    for npc in npcs_created:
        avatar_desc = _extract_avatar_prompt(npc.get("avatar_description", "") or "")
        all_participants.append(
            {
                "type": "npc",
                "npc_key": npc["npc_key"],
                "npc_name": npc.get("npc_name", npc.get("role", "NPC")),
                "role": npc["role"],
                "species": npc.get("species", ""),
                "personality_traits": npc.get("personality_traits", []),
                "role_description": npc.get("role_description", ""),
                "avatar_description": avatar_desc,
            }
        )

    logger.info(f"Total participants: {len(all_participants)} ({real_player_count} players + {len(npcs_created)} NPCs)")

    # 6a. Generate NPC avatars (only for NPCs without an existing avatar)
    npc_roles_for_avatar = [
        {
            "role_key": npc.get("role_key", ""),
            "role_name": npc.get("role", npc.get("npc_name", "")),
            "species": npc.get("species", "random"),
            "gender": npc.get("gender", "random"),
            "avatar_description": npc.get("avatar_description", ""),
            "personality_traits": npc.get("personality_traits", []),
        }
        for npc in npcs_created
        if not npc.get("avatar_description", "").startswith("avatar_url=")
    ]
    if npc_roles_for_avatar:
        try:
            image_gen = create_image_generator()
            avatar_prompts = await gm.generate_npc_avatar_prompts(npc_roles_for_avatar, game_id=game_id, player_id=None, turn=_npc_turn, kind="npc_avatar_prompts")
            for prompt_entry in avatar_prompts:
                role_key = prompt_entry.get("role_key", "")
                prompt = prompt_entry.get("prompt", "")
                if role_key and prompt:
                    url = await image_gen.generate_avatar_image(prompt=prompt, filename_prefix=f"{game_id}/avatar_{role_key}", width=768, height=1024, game_id=game_id, player_id=None, turn=None, kind=f"npc_avatar_{role_key}")
                    if url:
                        # Update NPC profile with avatar URL
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE npc_profiles SET avatar_description = ? WHERE role_key = ? AND game_id = ?",
                            (f"avatar_url={url};{prompt}", role_key, game_id),
                        )
                        conn.commit()
                        conn.close()
                        logger.info(f"[NPC_AVATAR] Generated avatar for {role_key}: {url}")
        except Exception as e:
            logger.warning(f"[NPC_AVATAR] Batch generation failed: {e}")

    # 6b. Generate mission + title + welcome via the linked concept pipeline
    # (resume: reuse if already created). After a restart the mission is gone,
    # so this regenerates both the mission AND a title tied to it — keeping the
    # game name consistent with its mission instead of reusing a stale title.
    mission_data = get_mission(None, game_id=game_id)
    if mission_data:
        logger.info(f"[MISSION] Resume: reusing existing mission '{mission_data.get('name', '')}'")
        mission_result = mission_data
    else:
        concept = await _generate_game_concept(game_id, language)
        mission_result = concept.get("mission") or {}
        mission_data = mission_result
        if mission_result.get("name"):
            logger.info(f"[MISSION] Mission created: {mission_result.get('name', '')} ({mission_result.get('total_stages', 0)} stages), title='{concept.get('title', '')}'")
        else:
            logger.error("[MISSION] Failed to create mission", stack_info=True)

    # 6b.5 Pre-generate empty-location backgrounds (best-effort)
    try:
        await _generate_background_library(game_id, mission_data or {}, all_participants, gm, language)
    except Exception:
        logger.error("[BACKGROUND] Library generation failed for game %s", game_id, exc_info=True)

    # 6c. Generate bridge image (resume: skip if already generated)
    try:
        if get_random_game_image(type="bridge", game_id=game_id, turn=None):
            logger.info("[BRIDGE] Resume: bridge image already exists, skipping")
        else:
            bridge_result = await gm.generate_bridge_image_prompt(mission_data or {}, all_participants, game_id=game_id, player_id=None, turn=_npc_turn, kind="bridge_image_prompt")
            bridge_prompt = bridge_result.get("bridge_prompt", "")
            if bridge_prompt:
                image_gen = create_image_generator()
                bridge_url = await image_gen.generate_scene_image(prompt=bridge_prompt, filename_prefix=f"{game_id}/bridge", width=1024, height=1024, game_id=game_id, player_id=None, turn=None, kind="bridge")
                if bridge_url:
                    save_game_image(
                        type="bridge",
                        image_url=bridge_url,
                        game_id=game_id,
                        turn=None,
                        prompt=bridge_prompt,
                    )
                    logger.info(f"[BRIDGE] Bridge image saved: {bridge_url}")
    except Exception as e:
        logger.warning(f"[BRIDGE] Image generation failed: {e}")

    # 7. Generate the game turn with the new restructured flow
    state = get_game_state(game_id)
    turn_num = state["turn"]
    turn_threat = state["threat_level"]
    turn_ship_status = {"hull_integrity": state["hull_integrity"], "shields": state["shields"]}

    # Build cumulative summary from ALL previous turns, not just the last one
    previous_summary = _build_cumulative_story_summary(
        current_turn=turn_num,
        language=language,
        game_id=game_id,
    )

    # Step A: Generate global circumstances (resume: reuse if already stored)
    global_circ = None
    _existing_turn = get_game_turn(turn_num, game_id)
    if _existing_turn and _existing_turn.get("global_circumstances"):
        try:
            global_circ = json.loads(_existing_turn["global_circumstances"])
            logger.info(f"[TURN] Resume: reusing global circumstances for turn {turn_num}")
        except (json.JSONDecodeError, TypeError):
            global_circ = None
    if global_circ is None:
        global_circ = await gm.generate_global_circumstances(turn=turn_num, previous_summary=previous_summary, player_profiles=all_participants, mission_context=mission_data, game_id=game_id, player_id=None, kind="global_circumstances", threat_level=turn_threat)
    global_narrative = global_circ.get("narrative", "")

    # Save global circumstances
    update_game_turn_global_circumstances(
        turn_num,
        json.dumps(global_circ, ensure_ascii=False),
        game_id,
    )

    # Step A2: Generate scene image for this turn's briefing (resume: skip if exists)
    scene_url = get_random_game_image(type="scene", game_id=game_id, turn=turn_num)
    if scene_url:
        logger.info(f"[SCENE] Resume: scene image already exists for turn {turn_num}, skipping")
    else:
        try:
            # Prefer LLM-generated scene_prompt
            scene_prompt = global_circ.get("scene_prompt", "")
            if not scene_prompt:
                # Fallback: build from setting + narrative
                scene_prompt = (
                    f"Sci-fi scene: {global_circ.get('setting', '')}. {global_narrative[:500]} Cinematic starship interior, crew interacting with holographic displays, dramatic lighting from the main viewscreen, Star Trek aesthetic, 4K quality."
                )
            # Remove [avatar: ...] markers before sending to image gen
            import re

            scene_prompt_clean = re.sub(r"\[avatar:\s*\w+\]", "", scene_prompt).strip()
            image_gen = create_image_generator()
            scene_url = await image_gen.generate_scene_image(prompt=scene_prompt_clean, filename_prefix=f"{game_id}/scene_turn{turn_num}", width=1024, height=1024, game_id=game_id, player_id=None, turn=None, kind="scene")
            if scene_url:
                save_game_image(
                    type="scene",
                    image_url=scene_url,
                    game_id=game_id,
                    turn=turn_num,
                    prompt=scene_prompt_clean,
                )
                logger.info(f"[SCENE] Turn scene image saved for turn {turn_num}: {scene_url}")
        except Exception as e:
            logger.warning(f"[SCENE] Failed to generate turn scene image for turn {turn_num}: {e}")

    # Create game turn record EARLY to prevent race condition with polling loop.
    # Poll needs the game_turn record to exist before briefings are visible,
    # otherwise the player sees a briefing but cannot submit an action (404).
    # The existing Step E will REPLACE this placeholder via INSERT OR REPLACE.
    early_turn = {
        "turn": turn_num,
        "story": global_narrative,
        "global_circumstances": json.dumps(global_circ, ensure_ascii=False),
        "crew_dialogues": [],
        "player_actions": [],
        "generated_content": {
            "image": f"/content/turn_{turn_num}/scene.jpg",
        },
        "previous_turn_summary": previous_summary,
    }
    create_game_turn(early_turn, game_id)
    logger.info(f"[TURN] Early game turn record created for turn {turn_num}")

    # Step B: Generate per-player briefings and choices IN PARALLEL
    try:
        llm_parallel = int(os.getenv("LLM_PARALLEL", "2"))
    except (ValueError, TypeError):
        llm_parallel = 2
    sem = asyncio.Semaphore(llm_parallel)

    # Resume support: map existing briefings for this turn (from an interrupted
    # run) by participant key so completed briefings are reused, not regenerated.
    existing_by_key: dict[str, dict[str, Any]] = {}
    for _b in get_all_briefings_for_turn(turn_num, game_id):
        if _b.get("is_npc") and _b.get("npc_key"):
            existing_by_key[f"npc:{_b['npc_key']}"] = _b
        elif _b.get("player_id"):
            existing_by_key[f"player:{_b['player_id']}"] = _b

    async def _process_participant(
        participant: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Generate briefing for one participant (player or NPC) under semaphore."""
        async with sem:
            # Resume: reuse an existing briefing from an interrupted run
            if participant["type"] == "npc":
                _resume_key = f"npc:{participant['npc_key']}"
            else:
                _resume_key = f"player:{participant['player_id']}"
            _existing = existing_by_key.get(_resume_key)
            if _existing is not None:
                logger.info(f"[BRIEFING] Resume: reusing existing briefing for {_resume_key}")
                if participant["type"] == "npc":
                    return {
                        **_existing,
                        "name": participant.get("npc_name", participant["npc_key"]),
                        "role": participant["role"],
                        "action_text": next(
                            (c["text"] for c in _existing.get("choices", []) if c.get("id") == _existing.get("selected_action_id")),
                            "",
                        ),
                    }
                return {
                    **_existing,
                    "name": str(participant["player_id"]),
                    "role": participant["role"],
                    "personal_title": _existing.get("personal_title", ""),
                }
            player_id = participant.get("player_id")
            # Get player_name and wound severity from profile if available
            player_name = ""
            wound_severity = None
            if player_id:
                p = get_player_profile(player_id)
                if p:
                    player_name = p.get("player_name", "") or ""
                    wound_severity = p.get("wound_severity")
            elif participant.get("type") == "npc":
                player_name = participant.get("npc_name", "") or ""
                n = get_npc_profile(participant["npc_key"])
                if n:
                    wound_severity = n.get("wound_severity")

            gm_profile = {
                "player_id": player_id,
                "npc_key": participant.get("npc_key"),
                "role": participant["role"],
                "personality_traits": participant.get("personality_traits", []),
                "role_description": participant.get("role_description", ""),
                "species": participant.get("species", ""),
                "species_description": participant.get("species_description", ""),
                "wound_severity": wound_severity,
            }
            try:
                # Fresh GameServer per participant isolates logging context.
                _gs = create_game_server(language=language)
                briefing_data = await _gs.generate_player_briefing_and_choices(
                    global_circ,
                    gm_profile,
                    player_name,
                    turn_num,
                    game_id=game_id,
                    player_id=str(player_id) if player_id else participant.get("npc_key", ""),
                    kind="player_briefing",
                    threat_level=turn_threat,
                    ship_status=turn_ship_status,
                )
            except Exception as e:
                logger.error(f"[BRIEFING] Failed to generate briefing for {participant.get('role', '?')}: {e}", exc_info=True)
                return None

            briefing = briefing_data.get("briefing", "")
            choices = briefing_data.get("choices", [])
            personal_title = briefing_data.get("personal_title", "")
            if personal_title and turn_num:
                gs = get_game_strings(language)
                personal_title = gs["turn_prefix_simple"].format(turn=turn_num) + f" — {personal_title}"

            if participant["type"] == "npc":
                npc_profile = get_npc_profile(participant["npc_key"]) or participant
                try:
                    _npc_gs = create_game_server(language=language)
                    npc_decision = await _npc_gs.generate_npc_choice(
                        choices,
                        npc_profile,
                        game_id=game_id,
                        player_id=participant.get("npc_key", ""),
                        turn=turn_num,
                        kind="npc_choice",
                        loyalty=npc_profile.get("loyalty", 70),
                    )
                except Exception as e:
                    logger.error(f"[NPC] Failed to generate choice for {participant.get('npc_key', '?')}: {e}", exc_info=True)
                    return None

                selected_id = npc_decision.get("action_id", "")
                rationale = npc_decision.get("rationale", "")

                # Fallback delay decision carries a synthetic choice — append
                # it so the selected id resolves for every consumer.
                fallback_choice = npc_decision.get("choice")
                if fallback_choice and not any(c.get("id") == fallback_choice.get("id") for c in choices):
                    choices = [*choices, fallback_choice]

                # Find the consequence for the chosen action
                chosen_consequence = ""
                chosen_consequence_kind = ""
                for c in choices:
                    if c.get("id") == selected_id:
                        chosen_consequence = c.get("consequence", "")
                        chosen_consequence_kind = c.get("consequence_kind", "")
                        break

                saved = save_player_briefing(
                    {
                        "turn": turn_num,
                        "player_id": None,
                        "npc_key": participant["npc_key"],
                        "is_npc": True,
                        "briefing": briefing,
                        "choices": choices,
                        "selected_action_id": selected_id,
                        "choice_rationale": rationale,
                        "consequence_result": {"consequence": chosen_consequence, "consequence_kind": chosen_consequence_kind},
                    },
                    game_id,
                )
                if saved:
                    # ── Generate NPC action image ────────────────────────
                    npc_action_key = (turn_num, game_id)
                    npc_action_task = asyncio.create_task(
                        _generate_npc_chosen_action_image(
                            npc_key=participant["npc_key"],
                            game_id=game_id,
                            turn=turn_num,
                            action_id=selected_id,
                        )
                    )
                    _pending_action_tasks.setdefault(npc_action_key, set()).add(npc_action_task)
                    npc_action_task.add_done_callback(lambda _t, k=npc_action_key: _pending_action_tasks.get(k, set()).discard(_t))
                    return {
                        **saved,
                        "name": participant.get("npc_name", participant["npc_key"]),
                        "role": participant["role"],
                        "action_text": next(
                            (c["text"] for c in choices if c.get("id") == selected_id),
                            "",
                        ),
                    }
            else:
                # Real players — save briefing without choice (they'll choose later)
                saved = save_player_briefing(
                    {
                        "turn": turn_num,
                        "player_id": participant["player_id"],
                        "npc_key": None,
                        "is_npc": False,
                        "briefing": briefing,
                        "choices": choices,
                        "selected_action_id": None,
                        "choice_rationale": "",
                        "consequence_result": {},
                        "personal_title": personal_title,
                    },
                    game_id,
                )
                if saved:
                    return {
                        **saved,
                        "name": str(participant["player_id"]),
                        "role": participant["role"],
                        "personal_title": personal_title,
                    }
            return None

    # Run all participant briefings in parallel with semaphore limiting concurrency
    tasks = [_process_participant(p) for p in all_participants]
    results = await asyncio.gather(*tasks)
    all_briefings = [r for r in results if r]

    logger.info(f"[BRIEFING] Generated {len(all_briefings)}/{len(all_participants)} briefings")

    # ── Generate per-player character images ────────────────────────
    # Each player gets a character-in-scene image showing their avatar
    # in the current setting. Used as the personal briefing image.
    logger.info(f"[CHAR_IMAGE] Generating {len([b for b in all_briefings if not b.get('is_npc')])} per-player character images...")
    player_briefings = [b for b in all_briefings if not b.get("is_npc")]

    async def _generate_char_image(b: dict) -> str | None:
        """Generate a character image for a real player in the current setting.

        Uses LLM-based species-aware prompt generation for non-human characters,
        with fallback to string concatenation if LLM fails.
        """
        pid = b.get("player_id")
        if not pid:
            return None
        profile = get_player_profile(pid)
        if not profile:
            return None

        role = profile.get("role", "Crew Member")
        player_name = profile.get("player_name", "") or role
        traits = profile.get("personality_traits", [])
        avatar_desc = profile.get("avatar_description", "") or ""
        species_desc = profile.get("species_description", "") or ""
        species_type = profile.get("species", "") or ""
        gender_type = profile.get("gender", "") or ""
        setting = global_circ.get("setting", "ship interior")

        # Qwen-Image-Edit instruction for placing this character in the scene.
        # image_prompt is the LLM-generated visual-only scene description
        # (pose/action/species, no name/role) for Qwen-Image-Edit conditioning.
        char_action = b.get("image_prompt", "") or f"reacting to the situation in {setting[:120]}"
        instruction = ""
        bg_location = None
        try:
            scene = await gm.generate_scene_instruction(
                action_text=char_action,
                species_desc=species_desc or species_type,
                language=language,
                background_location=bg_location,
                scene_context=f"Setting: {setting}",
                species_category=profile.get("species_primary_key") or "",
                game_id=game_id,
                player_id=str(pid),
                turn=turn_num,
                kind="character_scene",
            )
            instruction = scene.get("instruction", "")
            bg_location = scene.get("background_location")
        except Exception as e:
            logger.warning(f"[CHAR_IMAGE] Scene instruction failed for {role}: {e}")

        if not instruction:
            instruction = f"Place the character from Picture 1 in the scene. {char_action}. Cinematic sci-fi portrait, upper body, dynamic lighting, 4K quality."

        background_url = None
        if bg_location:
            background_url = get_random_game_image(type=f"background_{bg_location}", game_id=game_id, turn=None)

        image_gen = create_image_generator()
        avatar_url = profile.get("avatar_url") or None
        # Avoid starting with the role title ("Scientific Officer") — it biases
        # text-to-image toward a human in uniform. Lead with species only.
        character_description = ""
        if species_type and species_type not in ("Unknown", "Неизвестно"):
            character_description = species_type
        if species_desc:
            character_description = f"{character_description}. {species_desc[:200]}" if character_description else species_desc[:200]

        url = await image_gen.generate_character_in_scene(
            instruction_prompt=instruction,
            character_avatar_url=avatar_url,
            background_url=background_url,
            character_description=character_description,
            filename_prefix=f"{game_id}/char_turn{turn_num}_p{pid}",
            width=1024,
            height=1024,
            game_id=game_id,
            player_id=str(pid),
            turn=turn_num,
            kind="character_scene",
            species_category=profile.get("species_primary_key") or "",
        )
        if url:
            save_game_image(
                type="character",
                image_url=url,
                game_id=game_id,
                turn=turn_num,
                prompt=instruction,
            )
        return url

    char_tasks = [_generate_char_image(b) for b in player_briefings]
    if char_tasks:
        char_urls = await asyncio.gather(*char_tasks, return_exceptions=True)
        for b, url_or_err in zip(player_briefings, char_urls, strict=False):
            if isinstance(url_or_err, str) and url_or_err:
                b["character_image_url"] = url_or_err
                personal_title = b.get("personal_title", "")
                logger.info(f"[CHAR_IMAGE] Generated for player {b.get('player_id')}: title='{personal_title[:60]}', url={url_or_err[:80]}")
            elif isinstance(url_or_err, Exception):
                logger.warning(f"[CHAR_IMAGE] Failed for player {b.get('player_id')}: {url_or_err}")

    # NPC dialogues
    player_role = all_participants[0]["role"] if all_participants else "Crew Member"
    from game_server import GameStory

    dialog_story = GameStory(
        turn=turn_num,
        setting=global_circ.get("setting", ""),
        conflict=global_circ.get("conflict", ""),
        narrative=global_narrative,
        decision_points=[],
    )
    try:
        dialogues = await gm.generate_crew_dialogues(
            story=dialog_story,
            player_role=player_role,
            crew_members=_get_crew_members(game_id),
            game_id=game_id,
            player_id=None,
            turn=turn_num,
            kind="crew_dialogue",
        )
        crew_dialogues_list = [{"npc": d.npc_name, "dialogue": d.dialogue} for d in dialogues]
    except Exception as e:
        logger.warning(f"NPC dialogue generation failed: {e}")
        crew_dialogues_list = []

    # Step E: Create the game turn record
    new_turn = {
        "turn": turn_num,
        "story": global_narrative,
        "global_circumstances": json.dumps(global_circ, ensure_ascii=False),
        "crew_dialogues": crew_dialogues_list,
        "player_actions": all_briefings[0].get("choices", []) if all_briefings else [],
        "generated_content": {
            "image": f"/content/turn_{turn_num}/scene.jpg",
        },
        "previous_turn_summary": previous_summary,
    }
    create_game_turn(new_turn, game_id)

    # Advance game state to next turn. Ship status (hull/shields/systems)
    # is persistent and deliberately not reset here.
    update_game_state(turn_num + 1, "active", ship_alive=True, game_id=game_id)

    # Build per-player briefing response
    briefings_for_response = []
    for b in all_briefings:
        briefings_for_response.append(
            {
                "player_id": b.get("player_id"),
                "npc_key": b.get("npc_key"),
                "is_npc": b.get("is_npc", False),
                "name": b.get("name", ""),
                "role": b.get("role", ""),
                "briefing": b.get("briefing", ""),
                "choices": b.get("choices", []),
                "selected_action_id": b.get("selected_action_id"),
                "choice_rationale": b.get("choice_rationale", ""),
            }
        )

    logger.info("=== ADMIN START GAME COMPLETED ===")
    logger.info(f"Turn: {turn_num}, Participants: {len(all_participants)}, NPCs: {len(npcs_created)}")

    # Get bridge image URL if generated
    bridge_url = get_random_game_image(type="bridge", game_id=game_id, turn=None)

    # Build mission info
    mission_info = {}
    if mission_data:
        mission_info = {
            "name": mission_data.get("name", ""),
            "description": mission_data.get("description", ""),
            "stages": len(mission_data.get("objectives", [])),
        }

    # ── Push briefings to telegram-bot ─────────────────────────
    try:
        player_briefings = _build_player_briefings_for_push(all_briefings, crew_dialogues_list, turn_num, game_id)
        if player_briefings:
            asyncio.create_task(
                push_briefings(
                    game_id=game_id,
                    turn=turn_num,
                    players_briefings=player_briefings,
                    bridge_url=bridge_url,
                    mission=mission_info,
                    crew_dialogues=crew_dialogues_list,
                    is_first_turn=True,
                    force_resend=False,
                    global_narrative=global_narrative,
                    was_restarted=request.was_restarted,
                    language=language,
                )
            )
    except Exception as push_err:
        logger.warning(f"[PUSH] Failed to initiate push: {push_err}")

    return {
        "status": "success",
        "turn": turn_num,
        "player_count": real_player_count,
        "npc_count": len(npcs_created),
        "total_participants": len(all_participants),
        "global_circumstances": global_circ,
        "briefings": briefings_for_response,
        "crew_dialogues": crew_dialogues_list,
        "mission": mission_info,
        "bridge_image_url": bridge_url,
    }


@app.get("/game/mission")
async def get_mission_endpoint(game_id: str):
    """Get the current mission for a game."""
    mission = get_mission(None, game_id=game_id)
    if not mission:
        raise HTTPException(status_code=404, detail="No mission found for this game")
    return mission


@app.get("/game/bridge-image")
async def get_bridge_image_endpoint(
    game_id: str,
):
    """Get the bridge image for a game."""
    url = get_random_game_image(type="bridge", game_id=game_id, turn=None)
    if not url:
        raise HTTPException(status_code=404, detail="No bridge image found")
    return {"image_url": url, "game_id": game_id, "type": "bridge"}


@app.get("/game/scene-image")
async def get_scene_image_endpoint(
    game_id: str,
    turn: int,
):
    """Get the scene image for a specific turn of a game."""
    url = get_random_game_image(type="scene", game_id=game_id, turn=turn)
    if not url:
        raise HTTPException(status_code=404, detail=f"No scene image found for turn {turn}")
    return {"image_url": url, "game_id": game_id, "turn": turn, "type": "scene"}


@app.get("/game/team")
async def get_team_endpoint(game_id: str):
    """Get the full team roster with avatar URLs and status.

    Returns all participants (players + NPCs) without distinguishing
    which is which. Each entry has: name, role, species, gender,
    avatar_url, and is_dead status.
    """
    team: list[dict[str, Any]] = []

    # Add real players
    player_ids = get_players_in_game(game_id)
    for pid in player_ids:
        profile = get_player_profile(pid)
        if not profile:
            continue
        avatar_url = profile.get("avatar_url") or None
        team.append(
            {
                "name": profile.get("player_name", "") or profile.get("role", "Crew Member"),
                "role": profile.get("role", "Crew Member"),
                "species": profile.get("species", "Unknown"),
                "gender": profile.get("gender", "Unknown"),
                "avatar_url": avatar_url,
                "is_dead": bool(profile.get("is_dead", False)),
            }
        )

    # Add NPCs — include both active and dead (killed in story).
    # Exclude inactive NPCs whose role is now taken by a real player
    # (checks ship_roles.taken_by to also handle legacy data), and also exclude
    # NPCs whose replaces_player_id is a player registered in this game — the
    # NPC holds the same seat as that player (a player who displaced the NPC by
    # taking its role, or legacy replacement NPCs of a returned player), so
    # listing both would duplicate the seat in the roster.
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT n.* FROM npc_profiles n LEFT JOIN ship_roles sr ON sr.role_key = n.role_key AND sr.game_id = n.game_id WHERE n.game_id = ? AND (n.is_active = 1 OR sr.taken_by IS NULL) AND COALESCE(n.replaces_player_id, -1) NOT IN (SELECT player_id FROM player_profiles p WHERE p.game_id = n.game_id) ORDER BY n.created_at",
        (game_id,),
    )
    npc_rows = cursor.fetchall()
    conn.close()
    loyalty_words = get_game_strings(get_game_language(game_id)).get("npc_loyalty", {})
    for row in npc_rows:
        avatar_desc = row["avatar_description"] or ""
        avatar_url = _extract_avatar_url(avatar_desc)
        npc_name = row["npc_name"] or ""
        npc_role = row["role"] or "NPC"
        # Translate raw species/gender keys to display names
        raw_species = row["species"] or ""
        raw_gender = row["gender"] or ""
        npc_species = get_species_type_name(raw_species, "ru") if raw_species else "Unknown"
        npc_gender = get_gender_type_name(raw_gender, "ru") if raw_gender else "Unknown"
        # sqlite3.Row has no .get() — check column existence via try/except
        try:
            is_active = bool(row["is_active"])
        except KeyError:
            is_active = True
        entry = {
            "name": npc_name or npc_role,
            "role": npc_role,
            "species": npc_species,
            "gender": npc_gender,
            "avatar_url": avatar_url,
            "is_dead": not is_active,
        }
        # Telegraph NPC morale to the players: active crew members show
        # their loyalty band so an approaching mutiny is visible in advance.
        if is_active and row["loyalty"] is not None:
            band = loyalty_band(row["loyalty"])
            entry["loyalty_band"] = band
            entry["loyalty_status"] = loyalty_words.get(band, band)
        team.append(entry)

    return {"game_id": game_id, "members": team, "count": len(team)}


@app.post("/player/{player_id}/die")
async def mark_player_dead_endpoint(player_id: int, game_id: str):
    """Mark a player as dead (crew member died in the story)."""
    result = mark_player_dead(player_id, game_id)
    if not result:
        raise HTTPException(status_code=404, detail="Player not found")
    return {
        "status": "ok",
        "player_id": player_id,
        "is_dead": True,
        "is_spectator": True,
    }


@app.get("/players/{game_id}/spectators")
async def get_spectator_ids_endpoint(game_id: str):
    """Get IDs of dead players (spectators) in a game."""
    dead = get_dead_players(game_id)
    return {"spectator_ids": dead, "count": len(dead)}


@app.get("/players/{game_id}/live")
async def get_live_player_ids_endpoint(game_id: str):
    """Get IDs of live players in a game."""
    live = get_live_players(game_id)
    return {"live_player_ids": live, "count": len(live)}


@app.post("/admin/kick-player")
async def admin_kick_player(request: KickPlayerRequest):
    """Kick a player by role and notify the kicked player.

    The kicked player receives a message about being removed from the game.
    Their seat stays empty — no NPC replacement is created.
    """
    logger.info("=== ADMIN KICK PLAYER ===")
    logger.info(f"role_key={request.role_key}, reason={request.reason}")

    game_id = request.game_id
    role_key = request.role_key

    # Find who currently holds this role
    role_data = get_role_by_key(role_key, language="ru", game_id=game_id)
    if not role_data:
        raise HTTPException(status_code=404, detail=f"Role '{role_key}' not found")
    taken_by = role_data.get("taken_by")
    if not taken_by:
        raise HTTPException(status_code=400, detail=f"Role '{role_key}' is not taken by any player")
    kicked_player_id = taken_by

    # Vacate the seat — it stays empty until a new player takes the role.
    release_role(role_key, game_id)
    record_kick(kicked_player_id, request.reason, game_id=game_id)

    # Notify the kicked player (via game_messages)
    kick_notification = f"⛔ *Вы были изгнаны с корабля!*\n\nGame Master принял решение удалить вас из игры.\n*Причина:* {request.reason}\n\nВаше место в экипаже осталось пустым.\nСпасибо за игру!"
    add_game_message(kicked_player_id, kick_notification, "kick_notification")

    # Remove from game but keep the profile data
    leave_game(kicked_player_id)

    logger.info("=== ADMIN KICK PLAYER COMPLETED ===")
    logger.info(f"Kicked player {kicked_player_id} from role {role_key} in game {game_id}; seat left empty")

    return {
        "status": "success",
        "kicked_player_id": kicked_player_id,
        "role_key": role_key,
        "role_name": role_data["role_name"],
        "reason": request.reason,
    }


class AutoKickBlockedRequest(BaseModel):
    """Request to auto-kick a player who blocked the bot."""

    player_id: int
    reason: str


@app.post("/admin/auto-kick-blocked")
async def admin_auto_kick_blocked(request: AutoKickBlockedRequest):
    """Auto-kick a player who blocked the bot. Called by telegram-bot push server.

    Looks up the player's game and role and removes them from the game; their
    seat stays empty. Idempotent: returns success if the player is already
    kicked/not in a game.
    """
    logger.info("=== AUTO KICK BLOCKED ===")
    logger.info(f"player_id={request.player_id}, reason={request.reason}")

    profile = get_player_profile(request.player_id)
    if not profile:
        logger.info(f"Player {request.player_id} has no profile, nothing to kick")
        return {"status": "no_profile", "player_id": request.player_id}

    game_id = profile.get("game_id", "") or ""
    if not game_id:
        logger.info(f"Player {request.player_id} is not in any game")
        return {"status": "not_in_game", "player_id": request.player_id}

    # Find which role this player holds in the game
    role_key = None
    all_roles = get_all_roles(game_id, language="en")
    for r in all_roles:
        if r.get("taken_by") == request.player_id:
            role_key = r.get("role_key", "")
            break

    if not role_key:
        logger.info(f"Player {request.player_id} holds no role in game {game_id}")
        # Still clear game_id so they don't keep failing pushes
        leave_game(request.player_id)
        return {"status": "no_role", "player_id": request.player_id, "game_id": game_id}

    # Vacate the seat — it stays empty, no NPC replacement.
    release_role(role_key, game_id)
    record_kick(request.player_id, request.reason, game_id=game_id)
    leave_game(request.player_id)

    # No notification — user blocked the bot, they won't see it anyway.

    logger.info("=== AUTO KICK BLOCKED COMPLETED ===")
    logger.info(f"Kicked blocked player {request.player_id} from game {game_id} (role {role_key}); seat left empty")

    return {
        "status": "kicked",
        "kicked_player_id": request.player_id,
        "game_id": game_id,
        "role_key": role_key,
        "reason": request.reason,
    }


class ResetPlayerRequest(BaseModel):
    """Request to reset a player's game participation (self-service /reset)."""

    player_id: int


@app.post("/admin/reset-player")
async def admin_reset_player(request: ResetPlayerRequest):
    """Reset a player's participation: vacate their seat, then wipe their
    profile and onboarding answers so they can start over from scratch.
    """
    logger.info("=== ADMIN RESET PLAYER ===")
    logger.info(f"player_id={request.player_id}")

    player_id = request.player_id
    profile = get_player_profile(player_id)
    game_id = profile.get("game_id") if profile else None

    # If the player is mid-onboarding, they have no profile yet — only an
    # onboarding session. There is no seat to vacate and no
    # game_id to report, but we must still wipe the session so /reset can
    # get them out of the onboarding flow.
    if not profile:
        sessions_deleted = delete_onboarding_sessions_for_player(player_id)
        logger.info(f"=== ADMIN RESET PLAYER COMPLETED === player_id={player_id}, no profile (onboarding-only), sessions_deleted={sessions_deleted}")
        return {
            "status": "success",
            "player_id": player_id,
            "game_id": None,
            "role_released": None,
            "profile_deleted": False,
            "sessions_deleted": sessions_deleted,
        }

    if game_id is None:
        raise HTTPException(status_code=400, detail="Player has no associated game")

    # Vacate the player's seat if they currently hold a role — it stays
    # empty, no NPC replacement. The kick record keeps them out of briefing
    # pushes for this game until they re-onboard.
    role_key = get_role_key_for_player(player_id, game_id)
    if role_key:
        release_role(role_key, game_id)
        record_kick(player_id, "Player reset", game_id=game_id)

    # Wipe the player's data so they can start a fresh onboarding.
    profile_deleted = delete_player_profile(player_id)
    sessions_deleted = delete_onboarding_sessions_for_player(player_id)

    logger.info(f"=== ADMIN RESET PLAYER COMPLETED === player_id={player_id}, game_id={game_id}, role_released={role_key}, profile_deleted={profile_deleted}, sessions_deleted={sessions_deleted}")

    return {
        "status": "success",
        "player_id": player_id,
        "game_id": game_id,
        "role_released": role_key,
        "profile_deleted": profile_deleted,
        "sessions_deleted": sessions_deleted,
    }


@app.get("/admin/list-games")
async def admin_list_games(include_ended: bool):
    """List games with player counts."""
    games = get_all_games() if include_ended else get_available_games()
    result = []
    for game in games:
        game_id = game["game_id"]
        onboarding_count = get_onboarding_count_in_game(game_id)
        current_turn = 0
        finale_outcome_type = ""
        if is_game_started(game_id):
            state = get_game_state(game_id)
            current_turn = state.get("turn", 0)
            finale_outcome_type = state.get("finale_outcome_type", "")
        mission = get_mission(None, game_id=game_id)
        result.append(
            {
                "game_id": game_id,
                "name": get_game_title(game_id) or game.get("name", ""),
                "description": game.get("description", ""),
                "player_count": get_player_count_in_game(game_id),
                "onboarding_count": onboarding_count,
                "status": game.get("status", "active"),
                "started": is_game_started(game_id),
                "language": get_game_language(game_id),
                "current_turn": current_turn,
                "archetype": mission.get("archetype", "") if mission else "",
                "finale_outcome_type": finale_outcome_type,
            }
        )
    return {"games": result}


@app.get("/admin/win-rate")
async def admin_win_rate():
    """Telemetry over all ended games: outcome-token counts, win share, averages.

    win_share = (triumph + victory + pyrrhic) / ended games. avg_auto_ratio is
    the mean of per-game auto/total action ratios from player_action_stats
    (auto = the code-assigned 'delay' hesitation kind).
    """
    counts = {"triumph": 0, "victory": 0, "pyrrhic": 0, "stalemate": 0, "defeat": 0}
    turns_list: list[int] = []
    auto_ratios: list[float] = []
    for game in get_all_games():
        if game.get("status") != "ended":
            continue
        game_id = game["game_id"]
        state = get_game_state(game_id)
        outcome_type = state.get("finale_outcome_type") or "unknown"
        counts[outcome_type] = counts.get(outcome_type, 0) + 1
        turns_list.append(int(state.get("turn") or 0))
        stats = get_game_action_stats(game_id=game_id)
        if stats["total_actions"] > 0:
            auto_ratios.append(stats["auto_actions"] / stats["total_actions"])
        else:
            auto_ratios.append(0.0)
    total_ended = len(turns_list)
    wins = counts["triumph"] + counts["victory"] + counts["pyrrhic"]
    return {
        "total_ended": total_ended,
        "counts": counts,
        "win_share": round(wins / total_ended, 3) if total_ended else 0.0,
        "avg_turns": round(sum(turns_list) / total_ended, 1) if total_ended else 0.0,
        "avg_auto_ratio": round(sum(auto_ratios) / total_ended, 3) if total_ended else 0.0,
    }


@app.post("/admin/analyze-turn")
async def admin_analyze_turn(
    language: str,
    *,
    game_id: str,
    turn: int | None,
):
    """Manually trigger combined outcome analysis for a specific turn.

    If turn is not specified, uses the current turn (turn - 1 since game state is pre-advanced).
    """
    if turn is None:
        state = get_game_state(game_id)
        turn_num = max(1, state["turn"] - 1)  # Game state is pre-advanced, so current completed turn is turn-1
    else:
        turn_num = turn

    logger.info(f"[ADMIN] Manual outcome analysis for Turn {turn_num}")
    await _analyze_turn_outcome(turn_num, language=language, game_id=game_id, force=True)

    game_turn = get_game_turn(turn_num, game_id)
    outcome_str = game_turn.get("combined_outcome", "{}") if game_turn else "{}"
    try:
        outcome = json.loads(outcome_str) if outcome_str else {}
    except (json.JSONDecodeError, TypeError):
        outcome = {}

    return {
        "status": "success",
        "turn": turn_num,
        "combined_outcome": outcome,
    }


async def _background_continue_wrapper(
    game_id: str,
    language: str,
    force_resend: bool,
    turn_num: int,
    deadline: str | None,
):
    """Run continue-game in background, notify GM on completion."""
    try:
        result = await _run_generation_with_job(
            game_id,
            turn_num,
            "continue",
            _original_continue_game(
                game_id=game_id,
                language=language,
                force_resend=force_resend,
                deadline=deadline,
            ),
            resume_job_id=None,
        )
        if result and result.get("status") == "success":
            await _notify_scheduler("reset", game_id=game_id)
            await push_gm_notification(
                game_id=game_id,
                turn=turn_num,
                status="success",
                error="",
                players=result.get("players", 0),
                npcs=result.get("npcs", 0),
                language=language,
            )
        elif result and result.get("status") == "game_ended":
            logger.info(f"[BACKGROUND] Game {game_id} ended during turn {turn_num} generation; not notifying scheduler")
            await push_gm_notification(
                game_id=game_id,
                turn=turn_num,
                status="game_ended",
                error="",
                players=result.get("players", 0),
                npcs=result.get("npcs", 0),
                language=language,
            )
    except Exception as e:
        logger.error(f"[BACKGROUND] Continue game failed for {game_id}: {e}", exc_info=True)
        await push_gm_notification(
            game_id=game_id,
            turn=turn_num,
            status="error",
            error=str(e),
            players=0,
            npcs=0,
            language=language,
        )


@app.post("/admin/continue-game")
async def admin_continue_game(
    game_id: str,
    language: str,
    force_resend: bool,
    deadline: str | None = None,
):
    """Generate the next turn in the game.

    Starts background generation and returns immediately.
    GM will receive a push notification via Telegram when done.

    deadline: ISO datetime (UTC) when the scheduler fires the NEXT
    generation — i.e. when this new turn closes. Stored on the turn
    and shown to players so the auto-action "delay" is fair.
    """
    # Use game's stored language if available
    language = get_game_language(game_id) or language
    logger.info("=== ADMIN CONTINUE GAME ===")
    logger.info(f"game_id={game_id}, language={language}, deadline={deadline}")

    state = get_game_state(game_id)
    turn_num = state["turn"]

    # Check game is active
    if state["status"] != "active" or not state["ship_alive"]:
        raise HTTPException(
            status_code=400,
            detail="Game is not active (ship destroyed or status is not 'active')",
        )

    # Start background generation task
    asyncio.create_task(
        _background_continue_wrapper(
            game_id=game_id,
            language=language,
            force_resend=force_resend,
            turn_num=turn_num,
            deadline=deadline,
        )
    )

    logger.info(f"Background turn generation started for turn {turn_num}")

    return {
        "status": "accepted",
        "turn": turn_num,
        "message": f"Turn generation started for turn {turn_num}. You'll be notified when complete.",
    }


async def _original_continue_game(
    game_id: str,
    language: str,
    force_resend: bool,
    deadline: str | None = None,
):
    """Original continue-game logic (runs in background)."""
    logger.info("=== ADMIN CONTINUE GAME ===")
    logger.info(f"game_id={game_id}, language={language}")

    state = get_game_state(game_id)
    turn_num = state["turn"]
    logger.info("=== ADMIN CONTINUE GAME ===")
    logger.info(f"game_id={game_id}, language={language}")

    state = get_game_state(game_id)
    turn_num = state["turn"]

    # Check game is active
    if state["status"] != "active" or not state["ship_alive"]:
        raise HTTPException(
            status_code=400,
            detail="Game is not active (ship destroyed or status is not 'active')",
        )

    # ── Close the previous turn BEFORE generating the next one ──────────
    # The previous turn's outcome (deaths, ship state, end_game) must be
    # applied to player_profiles BEFORE we read them to build the roster for
    # turn N+1. Otherwise a player who died on turn N is still is_dead=0 here,
    # so they get a briefing + action choices + crew-dialogue lines on turn N+1
    # — after their death notice. This also auto-selects actions for players
    # who never responded on turn N (they get a consequence in the outcome),
    # then computes the combined outcome. If that ends the game, abort before
    # generating anything.
    if turn_num > 1:
        prev_turn = turn_num - 1
        missing = get_players_who_need_to_choose(prev_turn, game_id=game_id)
        for b in missing:
            pid = b.get("player_id")
            if pid is None:
                continue
            try:
                await auto_select_action(player_id=pid, turn=prev_turn, language=language, game_id=game_id)
            except Exception:
                logger.warning(f"[AUTO_ACTION] Auto-select failed for player {pid} turn {prev_turn}", exc_info=True)

        await _analyze_turn_outcome(
            turn=prev_turn,
            language=language,
            game_id=game_id,
            force=False,
        )

        post_state = get_game_state(game_id)
        if post_state["status"] != "active" or not post_state["ship_alive"]:
            logger.info(f"[CONTINUE] Game ended after analyzing turn {prev_turn} (status={post_state['status']}); not generating turn {turn_num}")
            return {
                "status": "game_ended",
                "turn": turn_num,
                "total_participants": 0,
                "players": 0,
                "npcs": 0,
                "crew_dialogues": [],
            }

    # Get all participants (players + NPCs)
    player_ids = get_players_in_game(game_id)
    npcs = get_all_active_npcs(game_id)

    all_participants = []

    for pid in player_ids:
        profile = get_player_profile(pid)
        if profile and not profile.get("is_dead", False):
            avatar_desc = _extract_avatar_prompt(profile.get("avatar_description", "") or "")
            all_participants.append(
                {
                    "type": "player",
                    "player_id": pid,
                    "player_name": profile.get("player_name", "") or "",
                    "role": profile["role"],
                    "species": profile.get("species", ""),
                    "personality_traits": profile.get("personality_traits", []),
                    "role_description": profile.get("role_description", ""),
                    "avatar_description": avatar_desc,
                    "species_description": profile.get("species_description", "") or "",
                }
            )

    for npc in npcs:
        avatar_desc = _extract_avatar_prompt(npc.get("avatar_description", "") or "")
        all_participants.append(
            {
                "type": "npc",
                "npc_key": npc["npc_key"],
                "npc_name": npc.get("npc_name", npc.get("role", "NPC")),
                "role": npc["role"],
                "species": npc.get("species", ""),
                "personality_traits": npc.get("personality_traits", []),
                "role_description": npc.get("role_description", ""),
                "avatar_description": avatar_desc,
            }
        )

    if not all_participants:
        raise HTTPException(
            status_code=400,
            detail="No active participants (players or NPCs) in the game",
        )

    logger.info(f"Participants: {len(all_participants)}")

    # Build cumulative summary from ALL previous turns, not just the last one
    previous_summary = _build_cumulative_story_summary(
        current_turn=turn_num,
        language=language,
        game_id=game_id,
    )

    gm = create_game_server(language=language)

    # Fetch mission data for story consistency
    mission_data = get_mission(None, game_id=game_id) or {}

    # Doom clock for this turn's prompts — read AFTER the previous turn was
    # closed above (its outcome analysis may have advanced the threat).
    turn_state = get_game_state(game_id)
    turn_threat = turn_state["threat_level"]
    turn_ship_status = {"hull_integrity": turn_state["hull_integrity"], "shields": turn_state["shields"]}

    # Step A: Generate global circumstances (resume: reuse if already stored)
    global_circ = None
    _existing_turn = get_game_turn(turn_num, game_id)
    if _existing_turn and _existing_turn.get("global_circumstances"):
        try:
            global_circ = json.loads(_existing_turn["global_circumstances"])
            logger.info(f"[TURN] Resume: reusing global circumstances for turn {turn_num}")
        except (json.JSONDecodeError, TypeError):
            global_circ = None
    # Resume: the caller (e.g. interrupted-job recovery) may not know the
    # deadline this turn was generated with — keep the stored one.
    if deadline is None and _existing_turn and _existing_turn.get("deadline"):
        deadline = _existing_turn["deadline"]
    if global_circ is None:
        global_circ = await gm.generate_global_circumstances(
            turn=turn_num,
            previous_summary=previous_summary,
            player_profiles=all_participants,
            mission_context=mission_data,
            game_id=game_id,
            player_id=None,
            kind="global_circumstances",
            threat_level=turn_threat,
        )

    # Save global circumstances
    update_game_turn_global_circumstances(
        turn_num,
        json.dumps(global_circ, ensure_ascii=False),
        game_id,
    )

    # ── Generate the cohesive crew dialogue scene ────────────────
    # Done BEFORE per-player briefings so the conversation can influence
    # each player's personal action choices. The dialogue text (and the
    # player's own lines, if they were a speaker) is fed into the briefing
    # prompt as crew_dialogue_context.
    crew_speakers_pool = [
        {
            "type": p["type"],
            "player_id": p.get("player_id"),
            "npc_key": p.get("npc_key"),
            "name": p.get("player_name") or p.get("npc_name") or p.get("role", "Crew"),
            "role": p.get("role", "Crew Member"),
            "species": p.get("species", ""),
            "personality_traits": p.get("personality_traits", []),
        }
        for p in all_participants
    ]
    narrative_text = global_circ.get("narrative", "")
    try:
        crew_dialogues_list, crew_lines_by_key = await gm.generate_crew_scene_dialogue(
            narrative_text,
            crew_speakers_pool,
            game_id=game_id,
            player_id=None,
            turn=turn_num,
            kind="crew_dialogue",
        )
    except Exception:
        logger.error("[CREW_DIALOG] generation failed", exc_info=True)
        crew_dialogues_list, crew_lines_by_key = [], {}
    logger.info(f"[CREW_DIALOG] {len(crew_dialogues_list)} lines for turn {turn_num}; speakers with lines: {list(crew_lines_by_key)}")

    # Step A2: Generate scene image for this turn's briefing (resume: skip if exists)
    scene_url = get_random_game_image(type="scene", game_id=game_id, turn=turn_num)
    if scene_url:
        logger.info(f"[SCENE] Resume: scene image already exists for turn {turn_num}, skipping")
    else:
        try:
            # Prefer LLM-generated scene_prompt
            scene_prompt = global_circ.get("scene_prompt", "")
            if not scene_prompt:
                # Fallback: build from setting + narrative
                scene_prompt = (
                    f"Sci-fi scene: {global_circ.get('setting', '')}. "
                    f"{global_circ.get('narrative', '')[:500]} "
                    f"Cinematic starship interior, crew interacting with holographic displays, "
                    f"dramatic lighting from the main viewscreen, Star Trek aesthetic, 4K quality."
                )
            # Remove [avatar: ...] markers before sending to image gen
            import re

            scene_prompt_clean = re.sub(r"\[avatar:\s*\w+\]", "", scene_prompt).strip()
            image_gen = create_image_generator()
            scene_url = await image_gen.generate_scene_image(prompt=scene_prompt_clean, filename_prefix=f"{game_id}/scene_turn{turn_num}", width=1024, height=1024, game_id=game_id, player_id=None, turn=None, kind="scene")
            if scene_url:
                save_game_image(
                    type="scene",
                    image_url=scene_url,
                    game_id=game_id,
                    turn=turn_num,
                    prompt=scene_prompt_clean,
                )
                logger.info(f"[SCENE] Turn scene image saved for turn {turn_num}: {scene_url}")
        except Exception as e:
            logger.warning(f"[SCENE] Failed to generate turn scene image for turn {turn_num}: {e}")

    # Create game turn record EARLY to prevent race condition with polling loop.
    # The existing Step E will REPLACE this placeholder via INSERT OR REPLACE.
    early_turn = {
        "turn": turn_num,
        "story": global_circ.get("narrative", ""),
        "global_circumstances": json.dumps(global_circ, ensure_ascii=False),
        "crew_dialogues": [],
        "player_actions": [],
        "generated_content": {
            "image": f"/content/turn_{turn_num}/scene.jpg",
        },
        "previous_turn_summary": previous_summary,
        "deadline": deadline,
    }
    create_game_turn(early_turn, game_id)
    logger.info(f"[TURN] Early game turn record created for turn {turn_num}")

    # Step B: Generate per-player briefings
    all_briefings = []

    # Shared crew-dialogue context: the full conversation text, fed into every
    # participant's briefing so their action choices reflect what was discussed.
    crew_dialogue_shared_context = ""
    if crew_dialogues_list:
        if language == LANGUAGE_RU:
            joined = "\n".join(f"— {d['npc']}: {d['dialogue']}" for d in crew_dialogues_list)
            crew_dialogue_shared_context = (
                "\n\nКонтекст обсуждения экипажа (только что состоялся разговор):\n"
                f"{joined}\n"
                "Учти этот разговор при создании брифинга и вариантов действий.\n"
            )
        else:
            joined = "\n".join(f"— {d['npc']}: {d['dialogue']}" for d in crew_dialogues_list)
            crew_dialogue_shared_context = (
                "\n\nCrew discussion context (a conversation just took place):\n"
                f"{joined}\n"
                "Take this conversation into account when building the briefing and action choices.\n"
            )

    # Resume support: reuse existing briefings from an interrupted run.
    existing_by_key: dict[str, dict[str, Any]] = {}
    for _b in get_all_briefings_for_turn(turn_num, game_id):
        if _b.get("is_npc") and _b.get("npc_key"):
            existing_by_key[f"npc:{_b['npc_key']}"] = _b
        elif _b.get("player_id"):
            existing_by_key[f"player:{_b['player_id']}"] = _b
    for participant in all_participants:
        # Resume: reuse an existing briefing instead of regenerating
        if participant["type"] == "npc":
            _resume_key = f"npc:{participant['npc_key']}"
        else:
            _resume_key = f"player:{participant['player_id']}"
        _existing = existing_by_key.get(_resume_key)
        if _existing is not None:
            logger.info(f"[BRIEFING] Resume: reusing existing briefing for {_resume_key}")
            if participant["type"] == "npc":
                all_briefings.append(
                    {
                        **_existing,
                        "name": participant.get("npc_name", participant["npc_key"]),
                        "role": participant["role"],
                        "action_text": next(
                            (c["text"] for c in _existing.get("choices", []) if c.get("id") == _existing.get("selected_action_id")),
                            "",
                        ),
                    }
                )
            else:
                all_briefings.append(
                    {
                        **_existing,
                        "name": str(participant["player_id"]),
                        "role": participant["role"],
                        "personal_title": _existing.get("personal_title", ""),
                    }
                )
            continue
        gm_profile = {
            "player_id": participant.get("player_id"),
            "npc_key": participant.get("npc_key"),
            "role": participant["role"],
            "personality_traits": participant.get("personality_traits", []),
            "role_description": participant.get("role_description", ""),
            "species": participant.get("species", ""),
            "species_description": participant.get("species_description", ""),
        }
        player_name = ""
        if participant["type"] == "player" and participant.get("player_id"):
            p = get_player_profile(participant["player_id"])
            if p:
                player_name = p.get("player_name", "") or ""
        elif participant["type"] == "npc":
            player_name = participant.get("npc_name", "") or ""

        # Personal crew-dialogue context: if this participant spoke in the
        # crew dialogue, remind them of their own lines so their action
        # choices stay consistent with what they said.
        crew_dialogue_context = crew_dialogue_shared_context
        _pkey = f"player:{participant['player_id']}" if participant.get("player_id") else (
            f"npc:{participant['npc_key']}" if participant.get("npc_key") else None
        )
        my_lines = crew_lines_by_key.get(_pkey) if _pkey else None
        if my_lines:
            mine = "; ".join(my_lines)
            if language == LANGUAGE_RU:
                crew_dialogue_context = (
                    crew_dialogue_shared_context
                    + f"\nТы уже высказался в этом разговоре: \"{mine}\". "
                    "Твои варианты действий должны быть согласованы с этой позицией.\n"
                )
            else:
                crew_dialogue_context = (
                    crew_dialogue_shared_context
                    + f"\nYou already spoke in this conversation: \"{mine}\". "
                    "Your action choices must be consistent with this stance.\n"
                )

        briefing_data = await gm.generate_player_briefing_and_choices(
            global_circ,
            gm_profile,
            player_name,
            turn_num,
            game_id=game_id,
            player_id=str(participant["player_id"]) if participant.get("player_id") else participant.get("npc_key", ""),
            kind="player_briefing",
            crew_dialogue_context=crew_dialogue_context or None,
            threat_level=turn_threat,
            ship_status=turn_ship_status,
        )
        briefing = briefing_data.get("briefing", "")
        choices = briefing_data.get("choices", [])
        personal_title = briefing_data.get("personal_title", "")
        if personal_title and turn_num:
            gs = get_game_strings(language)
            personal_title = gs["turn_prefix_simple"].format(turn=turn_num) + f" — {personal_title}"

        if participant["type"] == "npc":
            npc_profile = get_npc_profile(participant["npc_key"]) or participant
            npc_decision = await gm.generate_npc_choice(
                choices,
                npc_profile,
                game_id=game_id,
                player_id=participant.get("npc_key", ""),
                turn=turn_num,
                kind="npc_choice",
                crew_dialogue_context=crew_dialogue_context or None,
                loyalty=npc_profile.get("loyalty", 70),
            )
            selected_id = npc_decision.get("action_id", "")
            rationale = npc_decision.get("rationale", "")

            # Fallback delay decision carries a synthetic choice — append
            # it so the selected id resolves for every consumer.
            fallback_choice = npc_decision.get("choice")
            if fallback_choice and not any(c.get("id") == fallback_choice.get("id") for c in choices):
                choices = [*choices, fallback_choice]

            chosen_consequence = ""
            chosen_consequence_kind = ""
            for c in choices:
                if c.get("id") == selected_id:
                    chosen_consequence = c.get("consequence", "")
                    chosen_consequence_kind = c.get("consequence_kind", "")
                    break

            saved = save_player_briefing(
                {
                    "turn": turn_num,
                    "player_id": None,
                    "npc_key": participant["npc_key"],
                    "is_npc": True,
                    "briefing": briefing,
                    "choices": choices,
                    "selected_action_id": selected_id,
                    "choice_rationale": rationale,
                    "consequence_result": {"consequence": chosen_consequence, "consequence_kind": chosen_consequence_kind},
                },
                game_id,
            )
            if saved:
                # ── Generate NPC action image ────────────────────────────
                npc_action_key = (turn_num, game_id)
                npc_action_task = asyncio.create_task(
                    _generate_npc_chosen_action_image(
                        npc_key=participant["npc_key"],
                        game_id=game_id,
                        turn=turn_num,
                        action_id=selected_id,
                    )
                )
                _pending_action_tasks.setdefault(npc_action_key, set()).add(npc_action_task)
                npc_action_task.add_done_callback(lambda _t, k=npc_action_key: _pending_action_tasks.get(k, set()).discard(_t))
                all_briefings.append(
                    {
                        **saved,
                        "name": participant.get("npc_name", participant["npc_key"]),
                        "role": participant["role"],
                        "action_text": next(
                            (c["text"] for c in choices if c.get("id") == selected_id),
                            "",
                        ),
                    }
                )
        else:
            saved = save_player_briefing(
                {
                    "turn": turn_num,
                    "player_id": participant["player_id"],
                    "npc_key": None,
                    "is_npc": False,
                    "briefing": briefing,
                    "choices": choices,
                    "selected_action_id": None,
                    "choice_rationale": "",
                    "consequence_result": {},
                    "personal_title": personal_title,
                },
                game_id,
            )
            if saved:
                all_briefings.append(
                    {
                        **saved,
                        "name": str(participant["player_id"]),
                        "role": participant["role"],
                        "personal_title": personal_title,
                    }
                )

    # ── Generate per-player character images (live players only) ────
    logger.info(f"[CHAR_IMAGE] Generating {len([b for b in all_briefings if not b.get('is_npc')])} per-player character images...")
    player_briefings = [b for b in all_briefings if not b.get("is_npc")]

    async def _generate_char_image(b: dict) -> str | None:
        """Generate a character image for a real player in the current setting.

        Uses LLM-based species-aware prompt generation for non-human characters,
        with fallback to string concatenation if LLM fails.
        """
        pid = b.get("player_id")
        if not pid:
            return None
        profile = get_player_profile(pid)
        if not profile:
            return None

        role = profile.get("role", "Crew Member")
        player_name = profile.get("player_name", "") or role
        traits = profile.get("personality_traits", [])
        avatar_desc = profile.get("avatar_description", "") or ""
        species_desc = profile.get("species_description", "") or ""
        species_type = profile.get("species", "") or ""
        gender_type = profile.get("gender", "") or ""
        setting = global_circ.get("setting", "ship interior")

        # Qwen-Image-Edit instruction for placing this character in the scene.
        # image_prompt is the LLM-generated visual-only description (no name/role).
        char_action = b.get("image_prompt", "") or f"reacting to the situation in {setting[:120]}"
        instruction = ""
        bg_location = None
        try:
            scene = await gm.generate_scene_instruction(
                action_text=char_action,
                species_desc=species_desc or species_type,
                language=language,
                background_location=None,
                scene_context=f"Setting: {setting}",
                species_category=profile.get("species_primary_key") or "",
                game_id=game_id,
                player_id=str(pid),
                turn=turn_num,
                kind="character_scene",
            )
            instruction = scene.get("instruction", "")
            bg_location = scene.get("background_location")
        except Exception as e:
            logger.warning(f"[CHAR_IMAGE] Scene instruction failed for {role}: {e}")

        if not instruction:
            instruction = f"Place the character from Picture 1 in the scene. {char_action}. Cinematic sci-fi portrait, upper body, dynamic lighting, 4K quality."

        background_url = None
        if bg_location:
            background_url = get_random_game_image(type=f"background_{bg_location}", game_id=game_id, turn=None)

        image_gen = create_image_generator()
        avatar_url = profile.get("avatar_url") or None
        # Avoid starting with the role title ("Scientific Officer") — it biases
        # text-to-image toward a human in uniform. Lead with species only.
        character_description = ""
        if species_type and species_type not in ("Unknown", "Неизвестно"):
            character_description = species_type
        if species_desc:
            character_description = f"{character_description}. {species_desc[:200]}" if character_description else species_desc[:200]

        url = await image_gen.generate_character_in_scene(
            instruction_prompt=instruction,
            character_avatar_url=avatar_url,
            background_url=background_url,
            character_description=character_description,
            filename_prefix=f"{game_id}/char_turn{turn_num}_p{pid}",
            width=1024,
            height=1024,
            game_id=game_id,
            player_id=str(pid),
            turn=turn_num,
            kind="character_scene",
            species_category=profile.get("species_primary_key") or "",
        )
        if url:
            save_game_image(
                type="character",
                image_url=url,
                game_id=game_id,
                turn=turn_num,
                prompt=instruction,
            )
        return url

    char_tasks = [_generate_char_image(b) for b in player_briefings]
    if char_tasks:
        char_urls = await asyncio.gather(*char_tasks, return_exceptions=True)
        for b, url_or_err in zip(player_briefings, char_urls, strict=False):
            if isinstance(url_or_err, str) and url_or_err:
                b["character_image_url"] = url_or_err
                personal_title = b.get("personal_title", "")
                logger.info(f"[CHAR_IMAGE] Generated for player {b.get('player_id')}: title='{personal_title[:60]}', url={url_or_err[:80]}")
            elif isinstance(url_or_err, Exception):
                logger.warning(f"[CHAR_IMAGE] Failed for player {b.get('player_id')}: {url_or_err}")

    # crew_dialogues_list + crew_lines_by_key are generated above, before the
    # per-player briefing loop, so the dialogue can influence each briefing.

    # Step E: Create game turn record
    new_turn = {
        "turn": turn_num,
        "story": global_circ.get("narrative", ""),
        "global_circumstances": json.dumps(global_circ, ensure_ascii=False),
        "crew_dialogues": crew_dialogues_list,
        "player_actions": all_briefings[0].get("choices", []) if all_briefings else [],
        "generated_content": {
            "image": f"/content/turn_{turn_num}/scene.jpg",
        },
        "previous_turn_summary": previous_summary,
        "deadline": deadline,
    }
    create_game_turn(new_turn, game_id)

    # Advance game state — but only if the previous turn didn't already end
    # the game (race: the last player's action can trigger _analyze_turn_outcome
    # → end_game before the scheduler fires continue-game). Without this guard
    # we'd clobber end_game's status='mission_complete'/'ship_destroyed' back
    # to 'active', resurrecting a dead game and letting turn N+1 be pushed.
    pre_state = get_game_state(game_id)
    if pre_state["status"] == "active" and pre_state["ship_alive"]:
        update_game_state(turn_num + 1, "active", ship_alive=True, game_id=game_id)
    else:
        logger.info(f"[CONTINUE] Game already ended (status={pre_state['status']}) before advancing to turn {turn_num + 1}; skipping state update")

    # ── Push briefings to telegram-bot ─────────────────────────
    try:
        # Build the global intro narrative from global circumstances
        global_narrative = global_circ.get("narrative", "")

        player_briefings = _build_player_briefings_for_push(all_briefings, crew_dialogues_list, turn_num, game_id)
        if player_briefings:
            asyncio.create_task(
                push_briefings(
                    game_id=game_id,
                    turn=turn_num,
                    players_briefings=player_briefings,
                    bridge_url=None,
                    mission=None,
                    crew_dialogues=crew_dialogues_list,
                    is_first_turn=False,
                    force_resend=force_resend,
                    global_narrative=global_narrative,
                    was_restarted=False,
                    language=language,
                    deadline=deadline,
                )
            )
    except Exception as push_err:
        logger.warning(f"[PUSH] Failed to initiate push: {push_err}")

    logger.info("=== ADMIN CONTINUE GAME COMPLETED ===")
    logger.info(f"Turn {turn_num} generated with {len(all_participants)} participants")

    return {
        "status": "success",
        "turn": turn_num,
        "total_participants": len(all_participants),
        "players": len(player_ids),
        "npcs": len(npcs),
        "crew_dialogues": crew_dialogues_list,
    }


@app.post("/admin/regenerate-turn")
async def admin_regenerate_turn(
    game_id: str,
    language: str,
):
    """Regenerate the current turn with state reset.

    Deletes the current turn's data (briefings, actions, turn record),
    rolls back game state by one turn, then regenerates the turn.
    """
    logger.info("=== ADMIN REGENERATE TURN ===")
    logger.info(f"game_id={game_id}, language={language}")

    state = get_game_state(game_id)
    current_turn = state["turn"]
    regenerate_turn = max(1, current_turn - 1)

    logger.info(f"Regenerating Turn {regenerate_turn} (current state turn={current_turn})")

    # Preserve the turn's deadline across regeneration — the deletion below
    # wipes the game_turns row that stores it.
    existing_turn = get_game_turn(regenerate_turn, game_id)
    preserved_deadline = existing_turn.get("deadline") if existing_turn else None

    # Delete current turn's data
    deleted_briefings = delete_player_briefings_for_turn(regenerate_turn, game_id)
    deleted_actions = delete_player_actions_for_turn(regenerate_turn, game_id)
    deleted_turn = delete_game_turn(regenerate_turn, game_id)

    logger.info(f"Deleted: {deleted_briefings} briefings, {deleted_actions} player actions, turn_record={deleted_turn}")

    # Roll back game state to before the deleted turn
    reset_game_state_to_turn1(game_id)
    # Restore to the correct turn (the turn being regenerated)
    update_game_state(regenerate_turn, "active", ship_alive=True, game_id=game_id)

    # Now regenerate the turn using the continue-game logic
    # admin_continue_game now starts background processing and returns immediately
    await admin_continue_game(game_id=game_id, language=language, force_resend=True, deadline=preserved_deadline)

    logger.info(f"Background regeneration started for Turn {regenerate_turn}")

    return {
        "status": "accepted",
        "turn": regenerate_turn,
        "message": f"Regeneration started for turn {regenerate_turn}. You will be notified when complete.",
        "deleted": {
            "briefings": deleted_briefings,
            "actions": deleted_actions,
            "turn_record": bool(deleted_turn),
        },
    }


@app.post("/admin/restart-game")
async def admin_restart_game(
    game_id: str,
    language: str,
):
    """Reset game state and restart from the first turn.

    Deletes all game turns, briefings, actions, messages, mission,
    and game images. Resets game state to turn 1, marks game as
    not-started, and keeps player profiles intact.
    """
    # Use game's stored language if available
    language = get_game_language(game_id) or language
    logger.info("=== ADMIN RESTART GAME ===")
    logger.info(f"game_id={game_id}, language={language}")

    # Delete all game content
    deleted_turns = delete_all_game_turns(game_id)
    deleted_briefings = delete_all_player_briefings(game_id)
    deleted_actions = delete_all_player_actions(game_id)
    deleted_messages = delete_all_game_messages(game_id)
    deleted_mission = delete_mission(game_id)
    deleted_images = delete_game_images(game_id)

    logger.info(f"Deleted: {deleted_turns} turns, {deleted_briefings} briefings, {deleted_actions} actions, {deleted_messages} messages, mission={deleted_mission}, {deleted_images} images")

    # Reset game state to turn 1
    reset_game_state_to_turn1(game_id)

    # Mark game as not started
    clear_game_started(game_id)

    # Reset ship roles (make all available again)
    reset_roles(game_id)

    # Deactivate all NPCs so fresh ones are generated with unique names
    reset_active_npcs(game_id)

    logger.info("=== ADMIN RESTART GAME COMPLETED ===")

    # Re-register with scheduler (reactivates if previously ended)
    asyncio.create_task(_register_game_in_scheduler(game_id, None))

    return {
        "status": "success",
        "game_id": game_id,
        "deleted_turns": deleted_turns,
        "deleted_briefings": deleted_briefings,
        "deleted_actions": deleted_actions,
        "deleted_messages": deleted_messages,
        "deleted_mission": deleted_mission,
        "deleted_images": deleted_images,
        "message": f"Game {game_id} has been reset to turn 1. All content cleared.",
    }


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    try:
        port = int(os.getenv("PORT", "8000"))
    except (ValueError, TypeError):
        port = 8000
    uvicorn.run(app, host=host, port=port)
