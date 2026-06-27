"""
Game Master API - FastAPI service for AI Game Master
"""

import asyncio
import json
import logging
import os
import random
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
    clear_game_started,
    create_game,
    create_game_turn,
    create_mission,
    create_npc_profile,
    create_onboarding_session,
    create_player_profile,
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
    delete_player_profile,
    end_game,
    get_all_active_npcs,
    get_all_briefings_for_turn,
    get_all_npcs,
    get_available_games,
    get_available_roles,
    get_db_connection,
    get_dead_players,
    get_game,
    get_game_turn,
    get_game_image_count,
    get_game_language,
    get_game_messages,
    get_game_state,
    get_game_title,
    get_game_welcome_text,
    get_live_players,
    get_mission,
    get_npc_by_role,
    get_npc_profile,
    get_onboarding_count_in_game,
    get_onboarding_session,
    get_player_actions,
    get_player_briefing,
    get_player_count_in_game,
    get_player_profile,
    get_players_in_game,
    get_players_who_need_to_choose,
    get_random_game_image,
    get_role_by_key,
    get_role_key_for_player,
    get_underrepresented_roles,
    deactivate_npc,
    init_db,
    is_game_started,
    mark_player_dead,
    release_role,
    set_game_language,
    record_kick,
    reset_active_npcs,
    reset_game_state_to_turn1,
    reset_roles,
    save_game_image,
    save_game_title_and_welcome,
    save_player_action,
    save_player_briefing,
    set_last_death_turn,
    start_game,
    take_role,
    update_briefing_choice,
    update_briefing_chosen_action_url,
    update_game_turn_global_circumstances,
    update_game_turn_outcome,
    update_game_state,
    update_game_title,
    update_mission_stage_progress,
    update_onboarding_role_scores,
    update_onboarding_session,
    update_player_profile_last_poll,
)
from game_rules import apply_mission_progress, apply_death_limits
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from game_master import create_game_master_agent
from image_generator import (
    DEFAULT_LOADING_FALLBACK_URL,
    DEFAULT_SPLASH_FALLBACK_URL,
    create_image_generator,
)
from language import (
    LANGUAGE_EN,
    LANGUAGE_RU,
    get_dimension_tag_field,
    get_dimension_tags,
    get_game_strings,
    get_gender_type_name,
    get_hybrid_species_name,
    get_species_type_name,
)
from prompts import (
    OnboardingQuestion,
)
from push_client import push_briefings, push_turn_outcome, push_game_over, push_gm_notification, push_player_chosen_action
from pydantic import BaseModel, Field, TypeAdapter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
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


def generate_game_id(length: int = 6) -> str:
    """Generate a unique alphanumeric game ID."""
    while True:
        game_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=length))
        if not get_game(game_id):
            return game_id


# ============== Pydantic Models ==============


class OnboardingAnswer(BaseModel):
    """Player's answer to an onboarding question"""

    question_id: int
    answer: str


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
    language: str = "en"
    player_name: str = ""


class GameMessageRequest(BaseModel):
    """Request to send a game message"""

    player_id: int
    message: str
    message_type: str = "text"


class PlayerActionRequest(BaseModel):
    """Request to submit player action"""

    player_id: int
    turn: int
    action_id: str
    choice: str


class PollResponse(BaseModel):
    """Response from game polling endpoint"""

    new_game_turn: dict[str, Any] | None = None
    pending_actions: list[dict[str, Any]] = Field(default_factory=list)
    messages_from_gm: list[dict[str, Any]] = Field(default_factory=list)
    npc_messages: list[dict[str, Any]] = Field(default_factory=list)
    avatar_url: str | None = None


class StartGameRequest(BaseModel):
    """Request to force-start a game"""

    game_id: str
    language: str = "ru"
    force: bool = True
    was_restarted: bool = False


class KickPlayerRequest(BaseModel):
    """Request to kick a player by role"""

    role_key: str
    reason: str = "Kicked by Game Master"
    game_id: str
    language: str = "ru"


class CreateGameRequest(BaseModel):
    """Request to create a new game."""

    name: str = "New Game"
    description: str = ""
    language: str = "ru"


class SetLanguageRequest(BaseModel):
    """Request to set a game's language."""

    game_id: str
    language: str = "ru"


# Dynamic species/gender onboarding: up to SPECIES_GENDER_QUESTIONS_TOTAL questions
# generated one-at-a-time by the LLM as the player answers, in a fixed alternating
# species/gender sequence (S/G/S/G/S = 3 species + 2 gender). The question text and
# option labels are LLM-authored; the canonical tags are assigned by us, so the
# existing tag-counting determination logic stays reliable.
SPECIES_GENDER_QUESTIONS_TOTAL = 5
SPECIES_GENDER_DIMENSIONS = ("species", "gender", "species", "gender", "species")


def _question_has_sg_tags(question: OnboardingQuestion) -> bool:
    """True if a question's options carry species_tags or gender_tags."""
    return any(opt.get("species_tags") or opt.get("gender_tags") for opt in question.options)


async def generate_dynamic_onboarding_questions(
    language: str = "en",
    game_id: str = "default_game",
) -> list[OnboardingQuestion]:
    """Generate dynamic onboarding questions using LLM with json_schema and enrich with images via ComfyUI."""
    logger.info(f"=== Generating dynamic onboarding questions for language: {language} ===")
    start_time = datetime.now()
    questions: list[OnboardingQuestion] = []
    try:
        game_master = create_game_master_agent(language=language)
        logger.info("Game Master agent created successfully")

        # Query underrepresented roles from recent onboarding history
        try:
            from language import SHIP_ROLES_I18N

            underrepresented = get_underrepresented_roles(game_id, n_last=10)
            if underrepresented:
                # Take bottom 3-4 roles
                target_roles = underrepresented[:4]
                role_names = []
                for rk in target_roles:
                    i18n = SHIP_ROLES_I18N.get(rk, {})
                    name = i18n.get(language, {}).get("role_name", rk)
                    role_names.append(f"{name} ({rk})")
                hint = ", ".join(role_names)
                logger.info(f"Underrepresented roles: {hint}")
            else:
                hint = ""
                logger.info("No underrepresented role history available")
        except Exception as e:
            logger.warning(f"Failed to query underrepresented roles: {e}")
            hint = ""

        raw_questions = game_master.generate_onboarding_questions(
            underrepresented_hint=hint,
        )
        logger.info(f"LLM returned {len(raw_questions)} questions")

        if raw_questions:
            result = []
            for i, q in enumerate(raw_questions):
                result.append(
                    OnboardingQuestion(
                        id=q.get("id", i + 1),
                        text=q.get("text", f"Question {i + 1}"),
                        options=q.get("options", []),
                        image_prompt=q.get("image_prompt"),
                    )
                )
            if result:
                questions = result
        else:
            logger.warning("No questions returned, using static fallback")

        gen_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"Question generation took {gen_time:.2f} seconds")

        # Generate images for each question in parallel via ComfyUI
        logger.info("=== Generating images for onboarding questions ===")
        image_start = datetime.now()
        try:
            image_generator = create_image_generator()

            async def _generate_question_image(q: OnboardingQuestion) -> str | None:
                """Generate image for a single question using LLM-generated image_prompt."""
                prompt = q.image_prompt
                if not prompt:
                    logger.warning(f"No image_prompt for question {q.id}, skipping image generation")
                    return None
                url = await image_generator.generate_image(
                    prompt=prompt,
                    filename_prefix=f"{game_id}/onboarding_q_{q.id}",
                    width=768,
                    height=768,
                )
                return url

            tasks = [_generate_question_image(q) for q in questions]
            image_urls = await asyncio.gather(*tasks, return_exceptions=True)

            for q, url_or_err in zip(questions, image_urls, strict=False):
                if isinstance(url_or_err, str) and url_or_err:
                    q.image_url = url_or_err
                elif isinstance(url_or_err, Exception):
                    logger.warning(f"Image generation failed for question {q.id}: {url_or_err}")

            img_time = (datetime.now() - image_start).total_seconds()
            success_count = sum(1 for u in image_urls if isinstance(u, str) and u)
            logger.info(f"Question images: {success_count}/{len(questions)} generated in {img_time:.2f}s")
        except Exception as img_err:
            logger.warning(f"Question image generation failed entirely: {img_err}")
            # Continue without images - questions are still usable

        return questions

    except Exception as e:
        logger.error(f"Failed to generate dynamic onboarding questions: {e}", exc_info=True)
        raise


async def _generate_option_images_for_question(
    question: OnboardingQuestion,
    session: dict[str, Any],
    language: str,
    game_id: str,
) -> OnboardingQuestion:
    """Generate one image per answer option for a species/gender question.

    Uses LLM to create short creative prompts, then generates images
    in parallel via ComfyUI (bounded by COMFYUI_IMAGE_CONCURRENCY semaphore).

    Each option image shows cumulative visual effect of all previous
    species/gender choices + this option's specific trait.

    Args:
        question: The next question to generate option images for
        session: The onboarding session (contains all answers so far)
        language: Language code (ru/en)
        game_id: Game identifier

    Returns:
        Question with image_url attached to each option (or None if generation failed)
    """
    logger.info(f"[OPTION_IMAGES] Generating option images for question {question.id}: {question.text[:50]}...")

    # Determine if this is a species or gender question
    has_species_tags = any(opt.get("species_tags") for opt in question.options if opt.get("species_tags"))

    tag_type = "species_tags" if has_species_tags else "gender_tags"

    # Build accumulated tags from all previous answers
    accumulated_tags: dict[str, int] = {}
    session_answers = session.get("answers", {})
    session_questions = session.get("questions", [])

    game_master = create_game_master_agent(language=language)

    # Count all tags from already-answered questions
    for qid_str, selected_value in session_answers.items():
        try:
            qid = int(qid_str) if not isinstance(qid_str, int) else qid_str
        except (ValueError, TypeError):
            logger.warning("Invalid question id in answers: %r", qid_str)
            continue
        if qid < 0:
            continue  # Skip metadata entries (game_id stored as -1)
        # Find the question in session questions
        for sq in session_questions:
            if sq.get("id") == qid:
                for opt in sq.get("options", []):
                    if opt.get("value") == selected_value:
                        for tag in opt.get(tag_type, []):
                            accumulated_tags[tag] = accumulated_tags.get(tag, 0) + 1
                        break
                break

    # Generate LLM prompts for each option
    prompts_dict = game_master.generate_species_option_prompts(
        question=question.model_dump(),
        accumulated_tags=accumulated_tags,
        tag_type=tag_type,
    )

    if not prompts_dict:
        logger.warning("[OPTION_IMAGES] No prompts generated, skipping images")
        return question

    # Generate images in parallel
    image_generator = create_image_generator()
    tasks = []
    option_values = []

    for opt in question.options:
        opt_value = opt.get("value", "")
        prompt = prompts_dict.get(opt_value, "")
        if not prompt:
            continue
        filename_prefix = f"{game_id}/species_{session.get('player_id', 'x')}_{question.id}_{opt_value}"
        tasks.append(
            image_generator.generate_image(
                prompt=prompt,
                filename_prefix=filename_prefix,
                width=512,
                height=512,
            )
        )
        option_values.append(opt_value)

    if not tasks:
        return question

    logger.info(f"[OPTION_IMAGES] Generating {len(tasks)} images in parallel via ComfyUI...")
    urls = await asyncio.gather(*tasks, return_exceptions=True)

    # Attach URLs back to options
    success_count = 0
    for opt_value, url_or_err in zip(option_values, urls, strict=False):
        if isinstance(url_or_err, str) and url_or_err:
            for opt in question.options:
                if opt.get("value") == opt_value:
                    opt["image_url"] = url_or_err
                    success_count += 1
                    break
        elif isinstance(url_or_err, Exception):
            logger.warning(f"[OPTION_IMAGES] Image failed for option {opt_value}: {url_or_err}")

    logger.info(f"[OPTION_IMAGES] {success_count}/{len(tasks)} option images generated")
    return question


async def generate_dynamic_species_gender_question(
    dimension: str,
    sg_step: int,
    session: dict[str, Any],
    language: str,
    game_id: str,
    existing_questions: list[OnboardingQuestion],
) -> OnboardingQuestion:
    """Generate ONE dynamic species/gender question and render its option images.

    The LLM authors only the question text + one label per canonical tag; we attach
    the tags ourselves (one option per canonical tag), then shuffle options
    deterministically by the session seed and generate a per-option ComfyUI image
    reflecting the cumulative traits chosen so far.
    """
    tag_field = get_dimension_tag_field(dimension)
    tags = get_dimension_tags(dimension)

    session_answers = {k: v for k, v in session.get("answers", {}).items() if str(k) not in ("-1", "-2", "-3")}
    session_questions = session.get("questions", [])

    game_master = create_game_master_agent(language=language)
    accumulated = game_master._count_tags_from_answers(session_answers, tag_field, session_questions)
    generated = game_master.generate_dynamic_species_gender_question(dimension, sg_step, accumulated)

    prefix = "s" if dimension == "species" else "g"
    options = [
        {
            "value": f"{prefix}{sg_step}_{tag}",
            "label": generated["labels"].get(tag, tag),
            "role_scores": {},
            tag_field: [tag],
        }
        for tag in tags
    ]

    rng = random.Random(session.get("shuffle_seed", 0) + sg_step)
    rng.shuffle(options)

    question = OnboardingQuestion(id=len(existing_questions) + 1, text=generated["text"], options=options)

    try:
        question = await _generate_option_images_for_question(
            question=question,
            session=session,
            language=language,
            game_id=game_id,
        )
    except Exception as img_err:
        logger.warning(f"[SG_Q] Option image generation failed for {dimension} step {sg_step}: {img_err}")

    return question


def generate_player_profile_from_answers(
    player_id: int,
    answers: dict[int, str],
    game_id: str = "default_game",
    language: str = "ru",
    questions: list[dict[str, Any]] | None = None,
    player_name: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Assign a role from the available ship roles based on accumulated role scores from onboarding answers."""
    available = get_available_roles(game_id, language=language)

    if not available:
        raise ValueError("All crew positions are filled. No roles available.")

    game_master = create_game_master_agent(language=language)

    role_result = game_master.assign_role_from_answers(answers, available, questions=questions)

    assigned_key = role_result.get("role_key", "")

    role_data = get_role_by_key(assigned_key, language=language, game_id=game_id)
    if not role_data or role_data.get("taken_by") is not None:
        logger.warning(f"[ROLE] Suggested taken/invalid role '{assigned_key}', re-assigning from available")
        available = get_available_roles(game_id, language=language)
        if not available:
            raise ValueError("All crew positions are filled while re-assigning.")
        role_result = game_master.assign_role_from_answers(answers, available, questions=questions)
        assigned_key = role_result.get("role_key", "")
        role_data = get_role_by_key(assigned_key, language=language, game_id=game_id)

    if not role_data:
        role_data = available[0]
        assigned_key = role_data["role_key"]

    taken = take_role(assigned_key, player_id, game_id)
    if not taken:
        logger.warning(f"[ROLE] Role {assigned_key} was taken between check and assignment, re-assigning from available")
        available = get_available_roles(game_id, language=language)
        if not available:
            raise ValueError("All crew positions are filled.")
        # Use point-based assignment to pick the best remaining role, not just first
        role_result = game_master.assign_role_from_answers(answers, available, questions=questions)
        fallback_key = role_result.get("role_key", available[0]["role_key"])
        fallback_taken = take_role(fallback_key, player_id, game_id)
        if not fallback_taken:
            # Ultimate fallback: first available
            role_data = available[0]
            take_role(role_data["role_key"], player_id, game_id)
        else:
            role_data = get_role_by_key(fallback_key, language=language, game_id=game_id)

    if not role_data:
        raise ValueError("Could not resolve an available role for player.")

    traits = role_data["personality_traits"].copy()
    for ans in answers.values():
        if ans in ("cautious", "caution"):
            traits.append("осторожный")
        elif ans in ("bold", "aggressive"):
            traits.append("смелый")
        elif ans == "empathetic":
            traits.append("эмпатичный")
        elif ans == "logical":
            traits.append("логичный")
    traits = list(dict.fromkeys(traits))

    logger.info(f"[ROLE] Player {player_id} assigned role: {role_data['role_name']} ({assigned_key}), scores: {role_result.get('reasoning', '')}")

    # Calculate species and gender from answers
    species_result = game_master.calculate_species_from_answers(answers, questions=questions)
    gender_result = game_master.calculate_gender_from_answers(answers, questions=questions)

    species_primary = species_result.get("primary", "")
    species_hybrid = species_result.get("hybrid", False)
    species_secondary = species_result.get("secondary", "")
    gender_primary = gender_result.get("primary", "")

    species_display = species_primary
    if species_hybrid and species_secondary:
        hybrid_key = f"{species_primary}+{species_secondary}"
        alt_hybrid = f"{species_secondary}+{species_primary}"
        species_display = get_hybrid_species_name(hybrid_key, language)
        if species_display == hybrid_key:
            species_display = get_hybrid_species_name(alt_hybrid, language)

    # Get secondary display names for hybrid display
    gender_secondary = gender_result.get("secondary", "")

    species_type_display = get_species_type_name(species_primary, language)
    gender_type_display = get_gender_type_name(gender_primary, language)

    species_secondary_display = get_species_type_name(species_secondary, language) if species_secondary else None
    gender_secondary_display = get_gender_type_name(gender_secondary, language) if gender_secondary else None

    # Generate species+gender narrative description via LLM
    species_description = ""
    try:
        species_description = game_master.generate_species_gender_description(
            species_result=species_result,
            gender_result=gender_result,
            role=role_data["role_name"],
        )
        logger.info(f"[SPECIES] Description generated for player {player_id}: {species_description}...")
    except Exception as e:
        logger.warning(f"[SPECIES] Failed to generate description for player {player_id}: {e}")
        species_description = ""

    logger.info(f"[SPECIES] Player {player_id} species={species_primary}, gender={gender_primary}, hybrid={species_hybrid}, display={species_display}")

    return {
        "player_id": player_id,
        "player_name": player_name,
        "avatar_description": role_data["avatar_description"],
        "role": role_data["role_name"],
        "role_name_en": role_data["role_name_en"],
        "role_description": role_data["role_description"],
        "personality_traits": traits,
        "game_id": game_id,
        "species": species_type_display,
        "gender": gender_type_display,
        "species_description": species_description,
        "species_secondary": species_secondary_display,
        "gender_secondary": gender_secondary_display,
        "species_primary_key": species_primary,
    }

    # Save role_score_history for underrepresented role tracking
    if session_id:
        role_points = role_result.get("role_points", {})
        if role_points:
            try:
                update_onboarding_role_scores(session_id, role_points)
                logger.info(f"[ROLE] Saved role_score_history for session {session_id}: {dict(sorted(role_points.items(), key=lambda x: x[1], reverse=True)[:5])}")
            except Exception as e:
                logger.warning(f"[ROLE] Failed to save role_score_history for session {session_id}: {e}")


# ============== FastAPI App ==============


async def _generate_loading_images():
    """Generate loading images in background at startup."""
    try:
        existing = get_game_image_count("loading")
        total_needed = 5
        if existing >= total_needed:
            logger.info(f"[LOADING] {existing} loading images already in DB, skipping gen")
            return

        remaining = total_needed - existing
        logger.info(f"[LOADING] Generating {remaining} loading images (background)...")
        image_generator = create_image_generator()
        urls = await image_generator.generate_loading_images(count=remaining, start_index=existing, game_id="default_game")

        saved = 0
        for url in urls:
            if url:
                save_game_image(type="loading", image_url=url)
                saved += 1

        logger.info(f"[LOADING] Background gen: saved {saved}/{remaining} images")
    except Exception as e:
        logger.error(f"[LOADING] Background generation failed: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    logger.info("Game Master API starting up")
    init_db()
    logger.info("Database initialized and migrations run")

    # Generate loading images in background (non-blocking)
    asyncio.create_task(_generate_loading_images())

    yield
    logger.info("Game Master API shutting down")


GAME_SCHEDULER_URL = os.getenv("GAME_SCHEDULER_URL", "http://game-scheduler:8001")


async def _notify_scheduler(action: str) -> None:
    """Fire-and-forget notification to game-scheduler after a turn event."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GAME_SCHEDULER_URL}/scheduler/{action}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Scheduler notification '{action}' returned {resp.status}")
                else:
                    logger.info(f"Scheduler notified: {action}")
    except Exception as e:
        logger.warning(f"Failed to notify scheduler ({action}): {e}")


app = FastAPI(
    title="AI Game Master API",
    description="API for AI-powered cooperative game with Telegram bot interface",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS middleware — allows browser-based clients (Telegram Mini App) to call the API.
# - GAME_MASTER_API_URL: internal Docker URL (for development / self-reference)
# - CORS_ORIGIN: external/public URL for browser frontend (Telegram Mini App)
# Only browsers enforce CORS; backend services (telegram-bot, game-scheduler) don't need it.
cors_origins = [os.getenv("GAME_MASTER_API_URL", "http://game-server-api:8000")]
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
    return {"service": "AI Game Master API", "status": "running", "version": "2.0.0"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# ============== Onboarding endpoints ==============


@app.post("/onboarding/start")
async def start_onboarding(request: StartOnboardingRequest):
    """Start a new onboarding session for a player"""
    start_time = datetime.now()
    logger.info("=== START ONBOARDING ===")
    logger.info(f"player_id: {request.player_id}, game_id: {request.game_id}, language: {request.language}")

    # Check if player already has a profile
    existing_profile = get_player_profile(request.player_id)

    if existing_profile:
        logger.warning(f"Player {request.player_id} already has a profile")
        raise HTTPException(status_code=400, detail="Player already has a profile")

    # Check if the game is already full
    current_count = get_player_count_in_game(request.game_id)
    if current_count >= GAME_START_MAX_PLAYERS:
        raise HTTPException(
            status_code=400,
            detail=(f"Game is full ({current_count}/{GAME_START_MAX_PLAYERS} players). No more players can join at this time."),
        )

    # Generate role questions (dynamic or static fallback) with images
    logger.info("Generating dynamic onboarding questions...")
    role_questions = await generate_dynamic_onboarding_questions(
        language=request.language,
        game_id=request.game_id,
    )
    logger.info(f"Generated {len(role_questions)} role questions")

    # Generate shuffle seed for deterministic question/option shuffling
    shuffle_seed = random.randint(0, 2**31 - 1)

    # Species/gender questions are NOT pre-generated here. They are produced
    # one-at-a-time by the LLM during /onboarding/{session_id}/answer (fixed
    # alternating S/G/S/G/S sequence, capped at SPECIES_GENDER_QUESTIONS_TOTAL).
    dynamic_questions = role_questions

    for i, q in enumerate(dynamic_questions, start=1):
        q.id = i
    logger.info(f"Total onboarding questions: {len(dynamic_questions)} role (+ up to {SPECIES_GENDER_QUESTIONS_TOTAL} dynamic species/gender)")

    # Reuse the title + welcome generated once at game creation (they describe the
    # shared ship and must be identical for every player). Only generate+persist
    # when missing (e.g. legacy games created before this existed).
    existing_title = get_game_title(request.game_id)
    existing_welcome = get_game_welcome_text(request.game_id)
    game_title_data = {}
    if existing_title:
        logger.info(f"Reusing existing game title: {existing_title}")
        gs = get_game_strings(request.language)
        game_title_data = {
            "title": existing_title,
            "welcome_text": existing_welcome or gs["welcome_text_fallback"],
        }
    else:
        try:
            gm = create_game_master_agent(language=request.language)
            game_title_data = gm.generate_game_title()

            if game_title_data.get("title"):
                save_game_title_and_welcome(
                    request.game_id,
                    game_title_data["title"],
                    game_title_data.get("welcome_text", ""),
                )
                logger.info(f"Game title saved to DB: {game_title_data['title']}")
        except Exception as e:
            logger.warning(f"Game title generation failed: {e}")
            gs = get_game_strings(request.language)
            game_title_data = {
                "title": gs["game_title_fallback"],
                "welcome_text": gs["welcome_text_fallback"],
            }
            # Save fallback title to database
            update_game_title(request.game_id, game_title_data["title"])

    # Generate 3 splash images SYNCHRONOUSLY (blocks until done)
    existing_splash = get_game_image_count("splash", request.game_id)
    if existing_splash < 3:
        title_for_prompt = game_title_data.get("title", "")
        welcome_for_prompt = game_title_data.get("welcome_text", "")

        try:
            logger.info(f"[SPLASH] Generating 3 splash images for {title_for_prompt}...")
            cg = create_image_generator()
            urls = await cg.generate_splash_images(
                game_title=title_for_prompt,
                welcome_text=welcome_for_prompt,
                count=3,
                game_id=request.game_id,
            )
            saved = 0
            for url in urls:
                if url:
                    save_game_image(type="splash", image_url=url, game_id=request.game_id)
                    saved += 1
            logger.info(f"[SPLASH] Saved {saved}/3 splash images")
        except Exception as e:
            logger.error(f"[SPLASH] Generation failed: {e}", exc_info=True)
    else:
        logger.info(f"[SPLASH] {existing_splash} splash images already in DB, skipping")

    # Create session with pre-generated questions and shuffle_seed
    session = create_onboarding_session(
        request.player_id,
        request.language,
        questions=[q.model_dump() for q in dynamic_questions],
        shuffle_seed=shuffle_seed,
    )
    # onboarding_sessions table does not persist game_id yet; keep them in answers payload
    metadata = {
        -1: request.game_id,  # store game_id as metadata
        -2: request.player_name,  # store player_name as metadata
    }
    update_onboarding_session(
        session["session_id"],
        0,
        metadata,
        False,
        request.language,
    )
    logger.info(f"Onboarding session created: {session['session_id']}")

    # Log generation time
    gen_time = (datetime.now() - start_time).total_seconds()
    logger.info(f"Total onboarding start took {gen_time:.2f} seconds")

    next_question = dynamic_questions[0] if dynamic_questions else None
    if next_question:
        logger.info(f"First question: id={next_question.id}, text={next_question.text}...")

    result = {
        "session_id": session["session_id"],
        "game_id": request.game_id,
        "question": next_question.model_dump() if next_question else None,
        "game_title": game_title_data.get("title", ""),
        "welcome_message": game_title_data.get("welcome_text", ""),
    }
    logger.info("=== START ONBOARDING COMPLETED ===")
    return result


@app.post("/onboarding/{session_id}/answer")
async def submit_onboarding_answer(session_id: str, answer: OnboardingAnswer, language: str = "en"):
    """Submit an answer to an onboarding question"""
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Use the language from request or from session if already set
    effective_language = language if language != "en" else session.get("language", "en")
    answers_data = session.get("answers", {})
    game_id = session.get("game_id") or answers_data.get(-1) or answers_data.get("-1") or "default_game"

    answers = session["answers"].copy()
    answers[answer.question_id] = answer.answer
    current_question = session["current_question"] + 1

    session_questions = session.get("questions", [])

    question_adapter = TypeAdapter(list[OnboardingQuestion])
    dynamic_questions = question_adapter.validate_python(session_questions) if session_questions else []

    # Role questions are the tag-less ones; species/gender questions carry tags.
    role_count = sum(1 for q in dynamic_questions if not _question_has_sg_tags(q))
    total_questions = role_count + SPECIES_GENDER_QUESTIONS_TOTAL
    completed = current_question >= total_questions

    next_question = None
    questions_changed = False
    if not completed:
        if current_question < len(dynamic_questions):
            # Question already exists (a role question, or a species/gender question
            # built in a previous step with its option images already attached).
            next_question = dynamic_questions[current_question]
        else:
            # Dynamic species/gender phase: build the next question on demand via LLM.
            sg_step = current_question - role_count + 1  # 1-based within the S/G sequence
            dimension = SPECIES_GENDER_DIMENSIONS[sg_step - 1]
            session["answers"] = answers
            session["current_question"] = current_question
            try:
                next_question = await generate_dynamic_species_gender_question(
                    dimension=dimension,
                    sg_step=sg_step,
                    session=session,
                    language=effective_language,
                    game_id=game_id,
                    existing_questions=dynamic_questions,
                )
            except Exception as gen_err:
                logger.warning(f"[SG_Q] Dynamic {dimension} question generation failed: {gen_err}")
                next_question = None
            if next_question is not None:
                dynamic_questions.append(next_question)
                questions_changed = True

    # Persist progress (and the newly generated question, if any).
    update_onboarding_session(
        session_id,
        current_question,
        answers,
        completed,
        effective_language,
        questions=[q.model_dump() for q in dynamic_questions] if questions_changed else None,
    )

    result = {
        "completed": completed,
        "next_question": next_question.model_dump() if next_question else None,
    }

    if completed:
        profile_answers = {k: v for k, v in answers.items() if str(k) not in ("-1", "-2")}
        player_name = answers.get(-2) or answers.get("-2", "")
        profile_data = generate_player_profile_from_answers(
            session["player_id"],
            profile_answers,
            game_id=game_id,
            language=effective_language,
            questions=session_questions,
            player_name=player_name,
            session_id=session_id,
        )
        create_player_profile(profile_data)
        result["profile"] = profile_data

    return result


@app.post("/onboarding/{session_id}/complete")
async def complete_onboarding(session_id: str):
    """Complete onboarding and trigger avatar generation"""
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session["completed"]:
        raise HTTPException(status_code=400, detail="Onboarding not completed yet")

    player_id = session["player_id"]
    answers_data = session.get("answers", {})
    game_id = session.get("game_id") or answers_data.get(-1) or answers_data.get("-1") or "default_game"

    # Get player profile
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    # Generate avatar using ComfyUI directly
    # Step 1: Generate avatar prompt (LLM) with fallback to template
    avatar_prompt = ""
    try:
        game_master = create_game_master_agent(language=session.get("language", "en"))

        species_desc = profile.get("species_description") or ""
        species_type = profile.get("species", "") or ""
        gender_type = profile.get("gender", "") or ""
        if species_desc or species_type or gender_type:
            parts = [profile.get("avatar_description", "")]
            if species_type:
                parts.append(f"Species type: {species_type}")
            if gender_type:
                parts.append(f"Gender type: {gender_type}")
            if species_desc:
                parts.append(f"Appearance: {species_desc}")
            avatar_description_combined = "\n".join(parts)
        else:
            avatar_description_combined = profile.get("avatar_description", "")

        avatar_prompt = game_master.generate_avatar_prompt(
            role=profile["role"],
            traits=profile["personality_traits"],
            avatar_description=avatar_description_combined,
            species_category=profile.get("species_primary_key") or "",
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

        # Detect species category from available text
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
                f"Sci-fi artwork of a non-humanoid {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"Character form: {avatar_desc} "
                f"{species_desc} "
                f"Cinematic lighting, 4K quality, space opera aesthetic. "
                f"Full body or 3/4 view showing the alien physiology."
            ),
            "energy": (
                f"Sci-fi artwork of an energy being {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"Form: {avatar_desc} "
                f"{species_desc} "
                f"Glowing plasma energy form, luminous, ethereal, "
                f"cinematic lighting, 4K quality, space opera aesthetic. "
                f"Full body showing the energy form."
            ),
            "cybernetic": (
                f"Sci-fi artwork of a cybernetic {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"Form: {avatar_desc} "
                f"{species_desc} "
                f"Mechanical body, circuits, synthetic components, "
                f"cinematic lighting, 4K quality, space opera aesthetic. "
                f"Full body or 3/4 view showing cybernetic anatomy."
            ),
            "symbiotic": (
                f"Sci-fi artwork of a symbiotic being {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"Form: {avatar_desc} "
                f"{species_desc} "
                f"Composite organism, multiple life forms in one body, "
                f"cinematic lighting, 4K quality, space opera aesthetic. "
                f"Full body view showing the composite nature."
            ),
        }
        avatar_prompt = fallback_templates.get(species_cat, fallback_templates["human"])
        logger.info(f"[AVATAR] Using fallback prompt ({species_cat}) for player {player_id}: {avatar_prompt}...")

    # Step 3: Call ComfyUI to generate the avatar
    try:
        image_generator = create_image_generator()
        logger.info(f"[AVATAR] Calling ComfyUI at {image_generator.comfyui_url} for avatar generation")
        avatar_url = await image_generator.generate_avatar_image(
            prompt=avatar_prompt,
            filename_prefix=f"{game_id}/avatar_{player_id}",
        )

        if avatar_url:
            logger.info(f"[AVATAR] URL received for player {player_id}: {avatar_url}")
            update_player_profile_avatar(player_id, avatar_url)
            profile["avatar_url"] = avatar_url
        else:
            logger.warning(f"[AVATAR] ComfyUI returned None for player {player_id}")

    except Exception as e:
        logger.error(f"[AVATAR] ComfyUI generation failed for player {player_id}: {type(e).__name__}: {e}", exc_info=True)
        # Continue without avatar URL

    # Check player count and start game if >= GAME_START_MIN_PLAYERS
    player_count = get_player_count_in_game(game_id)
    game_was_started = False
    if player_count >= GAME_START_MIN_PLAYERS:
        game_was_started = start_game(game_id)

    game_started = is_game_started(game_id)

    # Get all other players in the game (for notification)
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
        "language": get_game_language(game_id),
    }


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


async def _generate_player_avatar(player_id: int, game_id: str, language: str = "en") -> str | None:
    """Generate avatar for an existing player. Returns avatar_url or None."""
    profile = get_player_profile(player_id)
    if not profile:
        logger.warning(f"[AVATAR] Player {player_id} not found, cannot generate avatar")
        return None

    # Step 1: Generate avatar prompt (LLM) with fallback to template
    avatar_prompt = ""
    try:
        game_master = create_game_master_agent(language=language)

        species_desc = profile.get("species_description") or ""
        species_type = profile.get("species", "") or ""
        gender_type = profile.get("gender", "") or ""
        if species_desc or species_type or gender_type:
            parts = [profile.get("avatar_description", "")]
            if species_type:
                parts.append(f"Species type: {species_type}")
            if gender_type:
                parts.append(f"Gender type: {gender_type}")
            if species_desc:
                parts.append(f"Appearance: {species_desc}")
            avatar_description_combined = "\n".join(parts)
        else:
            avatar_description_combined = profile.get("avatar_description", "")

        avatar_prompt = game_master.generate_avatar_prompt(
            role=profile["role"],
            traits=profile["personality_traits"],
            avatar_description=avatar_description_combined,
            species_category=profile.get("species_primary_key") or "",
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
                f"Sci-fi artwork of a non-humanoid {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"Character form: {avatar_desc} "
                f"{species_desc} "
                f"Cinematic lighting, 4K quality, space opera aesthetic. "
                f"Full body or 3/4 view showing the alien physiology."
            ),
            "energy": (
                f"Sci-fi artwork of an energy being {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"Form: {avatar_desc} "
                f"{species_desc} "
                f"Glowing plasma energy form, luminous, ethereal, "
                f"cinematic lighting, 4K quality, space opera aesthetic. "
                f"Full body showing the energy form."
            ),
            "cybernetic": (
                f"Sci-fi artwork of a cybernetic {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"Form: {avatar_desc} "
                f"{species_desc} "
                f"Mechanical body, circuits, synthetic components, "
                f"cinematic lighting, 4K quality, space opera aesthetic. "
                f"Full body or 3/4 view showing cybernetic anatomy."
            ),
            "symbiotic": (
                f"Sci-fi artwork of a symbiotic being {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"Form: {avatar_desc} "
                f"{species_desc} "
                f"Composite organism, multiple life forms in one body, "
                f"cinematic lighting, 4K quality, space opera aesthetic. "
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

    game_id = profile.get("game_id") or "default_game"
    avatar_url = await _generate_player_avatar(player_id, game_id)

    if avatar_url:
        return {"status": "generated", "avatar_url": avatar_url}
    else:
        return {"status": "failed", "avatar_url": None}


@app.get("/onboarding/{session_id}")
async def get_onboarding_status(session_id: str, language: str = "en"):
    """Get onboarding session status"""
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    next_question = None
    if not session["completed"]:
        # Get questions from session (pre-generated, no need to regenerate)
        session_questions = session.get("questions", [])
        if session_questions:
            question_adapter = TypeAdapter(list[OnboardingQuestion])
            dynamic_questions = question_adapter.validate_python(session_questions)
            remaining_questions = dynamic_questions[session["current_question"] :]
            next_question = remaining_questions[0] if remaining_questions else None

    answers_data = session.get("answers", {})
    session_game_id = session.get("game_id") or answers_data.get(-1) or answers_data.get("-1") or "default_game"

    return {
        "session_id": session["session_id"],
        "game_id": session_game_id,
        "current_question": session["current_question"],
        "completed": session["completed"],
        "next_question": next_question.model_dump() if next_question else None,
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
async def get_game_title_endpoint(game_id: str = "default_game"):
    """Get game title"""
    title = get_game_title(game_id)
    if not title:
        raise HTTPException(status_code=404, detail="Game title not found")
    return {"game_id": game_id, "title": title}


@app.get("/game/state")
async def get_game_state_endpoint(game_id: str = "default_game"):
    """Get current game state"""
    return get_game_state(game_id)


@app.get("/game/started")
async def get_game_started_endpoint(game_id: str = "default_game"):
    """Check if game has started (>= 3 players joined)"""
    started = is_game_started(game_id)
    player_count = get_player_count_in_game(game_id)
    language = get_game_language(game_id)
    return {"game_id": game_id, "started": started, "player_count": player_count, "language": language}


@app.get("/game/status")
async def get_game_status_endpoint(game_id: str = "default_game"):
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
        briefing = get_player_briefing(current_turn_num, pid, game_id=game_id)
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
                "has_chosen": has_chosen,
                "chosen_action": chosen_action_text,
            }
        )

    # NPCs — include both active and inactive (dead) NPCs
    npcs_list = []
    for npc in get_all_npcs(game_id):
        npc_key = npc["npc_key"]
        all_briefings = get_all_briefings_for_turn(current_turn_num, game_id=game_id)
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

    return {
        "game_id": game_id,
        "title": title,
        "turn": state["turn"],
        "current_turn": current_turn_num,
        "status": state["status"],
        "ship_alive": state["ship_alive"],
        "crew_health": state["crew_health"],
        "game_started": is_game_started(game_id),
        "player_count": len(players_list),
        "alive_count": sum(1 for pl in players_list if not pl["is_dead"]),
        "npc_count": len(npcs_list),
        "npc_alive_count": sum(1 for n in npcs_list if not n["is_dead"]),
        "players": players_list,
        "npcs": npcs_list,
    }


@app.get("/game/turn/{turn_num}")
async def get_game_turn_endpoint(turn_num: int, game_id: str = "default_game"):
    """Get specific turn's episode"""
    turn_data = get_game_turn(turn_num, game_id=game_id)
    if not turn_data:
        raise HTTPException(status_code=404, detail="Turn not found")
    return turn_data


@app.get("/game/current-turn")
async def get_current_game_turn(game_id: str = Query("default_game")):
    """Get current game turn

    Game state tracks the NEXT turn to generate, so the latest
    completed turn is state["turn"] - 1. For example:
    - Before any generation: state["turn"] = 1, no turns exist
    - After turn 1 generation: state["turn"] = 2, game_turn[1] exists
    """
    state = get_game_state(game_id)
    current_turn_num = max(1, state["turn"] - 1)
    turn_data = get_game_turn(current_turn_num, game_id=game_id)
    if not turn_data:
        raise HTTPException(status_code=404, detail="No game turn generated yet")
    return turn_data


@app.get("/game/poll/{player_id}")
async def poll_game_updates(player_id: int, since: str | None = None):
    """Poll for new game updates (turns, actions, messages) since last poll"""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    game_id = profile.get("game_id", "default_game")

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
        briefing = get_player_briefing(current_turn_num, player_id, game_id=game_id)

        if briefing and briefing.get("choices"):
            # Safety check: only return briefing if game_turn record exists
            # (prevents race condition where briefings are saved before game_turn)
            turn_record = get_game_turn(current_turn_num, game_id=game_id)
            if turn_record is None:
                logger.debug(f"[POLL] Skipping briefing for player {player_id} turn {current_turn_num}: game_turn not yet created")
            elif not briefing.get("selected_action_id"):
                # Player hasn't chosen yet — return their briefing
                # Get scene image for this turn
                scene_url = get_random_game_image(type="scene", turn=current_turn_num, game_id=game_id)
                # Also fetch NPC dialogues for crew behavior context
                turn_record = get_game_turn(current_turn_num, game_id=game_id)
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
            turn_data = get_game_turn(current_turn_num, game_id=game_id)
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
    game_id = profile.get("game_id", "default_game") if profile else "default_game"

    # First check if player has a personal briefing (new system)
    briefing = get_player_briefing(request.turn, request.player_id, game_id=game_id)

    if briefing and briefing.get("choices"):
        # New system: validate against briefing choices — does NOT require game_turn
        # (game_turn may not exist yet if briefings were saved before game_turn record)
        valid_ids = [c["id"] for c in briefing["choices"]]
        if request.action_id not in valid_ids:
            raise HTTPException(status_code=400, detail=f"Invalid action ID. Valid: {valid_ids}")

        # Find the consequence for the chosen action
        chosen_consequence = ""
        for c in briefing["choices"]:
            if c.get("id") == request.action_id:
                chosen_consequence = c.get("consequence", "")
                break

        # Update the briefing with the player's choice
        update_briefing_choice(
            briefing_id=briefing["id"],
            selected_action_id=request.action_id,
            choice_rationale="selected by player",
            consequence_result={"consequence": chosen_consequence},
        )
    else:
        # Legacy system: validate against game_turns.player_actions
        current_turn = get_game_turn(request.turn, game_id=game_id)
        if not current_turn:
            raise HTTPException(status_code=404, detail="No active game turn")
        valid_actions = [a["id"] for a in current_turn.get("player_actions", [])]
        if request.action_id not in valid_actions:
            raise HTTPException(status_code=400, detail="Invalid action ID")

    # Also save to player_actions table for backward compatibility
    result = save_player_action(request.player_id, request.turn, request.action_id, request.choice)

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
        remaining = get_players_who_need_to_choose(request.turn, game_id=game_id)
        if not remaining:
            # All players chose — analyze combined outcome
            logger.info(f"All players chose for turn {request.turn}, analyzing combined outcome")
            asyncio.create_task(_analyze_turn_outcome(request.turn, game_id=game_id))
    except Exception as e:
        logger.warning(f"Combined outcome check failed: {e}")

    return {"status": "accepted", "action": result}


@app.post("/game/auto-action/{player_id}/{turn}")
async def auto_select_action(
    player_id: int,
    turn: int,
    language: str = "en",
    game_id: str = "default_game",
):
    """Auto-select an action for a player who hasn't chosen in time.

    Uses LLM with global circumstances + personal briefing + player profile
    to make an in-character choice. Notifies the player about the auto-selection.
    """
    # Use game's stored language — the caller may not know it
    language = get_game_language(game_id) or language
    logger.info(f"[AUTO_ACTION] Auto-selecting action for player {player_id}, turn {turn}")

    # 1. Get player's briefing with choices
    briefing = get_player_briefing(turn, player_id, game_id=game_id)
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

    # 2. Get player profile
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

    # 3. Get global circumstances
    game_turn = get_game_turn(turn, game_id=game_id)
    global_circ = {}
    if game_turn:
        gc_str = game_turn.get("global_circumstances", "{}")
        try:
            global_circ = json.loads(gc_str) if isinstance(gc_str, str) else gc_str
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Failed to parse global_circumstances: {e}", exc_info=True)

    # 4. Generate LLM choice
    gm = create_game_master_agent(language=language)
    player_name = profile.get("player_name", "") or ""
    decision = gm.generate_player_auto_choice(
        choices=choices,
        player_profile=profile,
        personal_briefing=briefing.get("briefing", ""),
        global_circumstances=global_circ,
        player_name=player_name,
    )

    action_id = decision.get("action_id", "")
    rationale = decision.get("rationale", "Auto-selected by Game Master")

    if not action_id:
        raise HTTPException(status_code=500, detail="LLM returned no valid action")

    # 5. Submit the action (same flow as submit_player_action)
    chosen_consequence = ""
    for c in choices:
        if c.get("id") == action_id:
            chosen_consequence = c.get("consequence", "")
            break

    update_briefing_choice(
        briefing_id=briefing["id"],
        selected_action_id=action_id,
        choice_rationale=rationale,
        consequence_result={"consequence": chosen_consequence},
    )

    save_player_action(
        player_id=player_id,
        turn=turn,
        action_id=action_id,
        choice="auto_selected",
    )

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
            asyncio.create_task(_analyze_turn_outcome(turn, game_id=game_id))
    except Exception as e:
        logger.warning(f"Combined outcome check after auto-select failed: {e}")

    logger.info(f"[AUTO_ACTION] Auto-selected '{action_id}' for player {player_id} turn {turn}: {action_text[:60]}...")

    return {
        "status": "selected",
        "action_id": action_id,
        "action_text": action_text,
        "rationale": rationale,
    }


# ============== Message endpoints ==============


@app.post("/game/messages")
async def submit_game_message(request: GameMessageRequest):
    """Submit a message to the game master and get response"""
    player_id = request.player_id
    message = request.message

    add_game_message(player_id, message, request.message_type)

    # Get player profile
    profile = get_player_profile(player_id)
    if not profile:
        profile_data = {
            "role": "Crew Member",
            "personality_traits": [],
            "player_id": player_id,
        }
    else:
        profile_data = {
            "role": profile["role"],
            "personality_traits": profile["personality_traits"],
            "player_id": player_id,
        }

    # Generate response from game master
    try:
        language = "ru" if any(c in message for c in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя") else "en"
        game_master = create_game_master_agent(language=language)

        response = game_master.process_player_message(player_id=player_id, message=message, player_profile=profile_data)

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
    game_id = profile.get("game_id", "default_game") if profile else "default_game"
    briefing = get_player_briefing(turn, player_id, game_id=game_id)
    if not briefing:
        raise HTTPException(status_code=404, detail="No briefing found")
    return {
        "briefing": briefing["briefing"],
        "choices": briefing["choices"],
        "selected_action_id": briefing.get("selected_action_id"),
        "turn": briefing["turn"],
        "chosen_action_url": briefing.get("chosen_action_url"),
    }


@app.get("/game/current-briefing/{player_id}")
async def get_current_briefing_endpoint(player_id: int):
    """Get a player's current turn briefing"""
    profile = get_player_profile(player_id)
    game_id = profile.get("game_id", "default_game") if profile else "default_game"
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
async def get_all_players(game_id: str = "default_game"):
    """Get all players in the current game"""
    players = get_players_in_game(game_id)
    return [{"player_id": pid, "game_id": game_id} for pid in players]


@app.get("/game/messages/{player_id}")
async def get_game_messages_endpoint(player_id: int, limit: int = 10):
    """Get player's message history"""
    messages = get_game_messages(player_id, limit)
    return {"messages": messages}


# ============== Content / Image endpoints ==============


@app.get("/content/loading-image")
async def get_loading_image(game_id: str = "default_game"):
    """Get a random loading screen image URL.

    Falls back to a manually-placed default image in ComfyUI output
    if no AI-generated loading images are available yet.
    """
    url = get_random_game_image(type="loading", game_id=game_id)
    if not url:
        logger.info(f"[LOADING] No generated loading images, using fallback: {DEFAULT_LOADING_FALLBACK_URL}")
        return {
            "image_url": DEFAULT_LOADING_FALLBACK_URL,
            "available": 0,
            "fallback": True,
        }
    return {"image_url": url, "available": get_game_image_count("loading", game_id)}


@app.get("/content/splash-image")
async def get_splash_image(game_id: str = "default_game"):
    """Get a random splash image URL for the game.

    Falls back to a manually-placed default image in ComfyUI output
    if no AI-generated splash images are available yet.
    """
    url = get_random_game_image(type="splash", game_id=game_id)
    if not url:
        logger.info(f"[SPLASH] No generated splash images, using fallback: {DEFAULT_SPLASH_FALLBACK_URL}")
        return {
            "image_url": DEFAULT_SPLASH_FALLBACK_URL,
            "available": 0,
            "fallback": True,
        }
    return {"image_url": url, "available": get_game_image_count("splash", game_id)}


async def _generate_chosen_action_image(
    player_id: int,
    game_id: str,
    turn: int,
    action_id: str,
    language: str = "ru",
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

        # SHORT character visual reference (1 sentence max for image gen fallback)
        character_description = role
        if species and species not in ("Unknown", "Неизвестно"):
            character_description += f", {species}"

        # Generate prompt via LLM (same style as avatar prompt),
        # with fallback to string concatenation
        prompt = ""
        try:
            gm = create_game_master_agent(language="en")
            prompt = gm.generate_chosen_action_prompt(
                role=role,
                traits=traits,
                avatar_description=avatar_desc,
                action_text=action_text,
                setting=setting,
                species_desc=species_desc,
                species_type=species,
                species_category=profile.get("species_primary_key") or "",
            )
            logger.info(f"[ACTION_IMAGE] LLM prompt for {role}: {prompt[:120]}...")
        except Exception as llm_err:
            logger.warning(f"[ACTION_IMAGE] LLM prompt failed for {role}: {llm_err}, using fallback prompt")

        if not prompt:
            # Fallback: build prompt via concatenation
            prompt = (
                f"{role} performing action: {action_text}. {character_description}. Setting: {setting[:200]}. Cinematic sci-fi scene, dynamic action in progress, dramatic lighting, detailed environment, space opera aesthetic, photorealistic quality, 4K."
            )

        # Get player's avatar URL for reference
        avatar_url = profile.get("avatar_url") or None

        image_gen = create_image_generator()
        chosen_action_url = await image_gen.generate_action_image_with_reference(
            prompt=prompt,
            reference_image_url=avatar_url,
            character_description=character_description,
            filename_prefix=f"{game_id}/action_turn{turn}_p{player_id}",
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
        turn_data = get_game_turn(turn, game_id=game_id)
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

        # Generate prompt via LLM
        prompt = ""
        try:
            gm = create_game_master_agent(language="en")
            prompt = gm.generate_chosen_action_prompt(
                role=role,
                traits=traits,
                avatar_description=avatar_desc_clean,
                action_text=action_text,
                setting=setting,
                species_category=npc_profile.get("species_primary_key") or "",
            )
        except Exception as llm_err:
            logger.warning(f"[NPC_ACTION_IMAGE] LLM prompt failed for {npc_name}: {llm_err}")

        if not prompt:
            prompt = f"{npc_name} ({role}) performing action: {action_text}. Setting: {setting[:200]}. Cinematic sci-fi scene, dynamic action in progress, dramatic lighting, detailed environment, space opera aesthetic, photorealistic quality, 4K."

        image_gen = create_image_generator()
        chosen_action_url = await image_gen.generate_action_image_with_reference(
            prompt=prompt,
            reference_image_url=avatar_url,
            character_description=character_description,
            filename_prefix=f"{game_id}/action_turn{turn}_{npc_key}",
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


def _build_turn_summary(combined_outcome_str: str, language: str = "ru") -> str:
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
    ds = gs["day_summary"]
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

    # Deaths
    dead = oc.get("dead_crew_members", [])
    if dead:
        dead_names = [f"{d[0]} ({d[1]})" if isinstance(d, list) and len(d) >= 2 else str(d) for d in dead]
        parts.append(ds["deceased"].format(names=", ".join(dead_names)))

    # Injured
    injured = oc.get("crew_injured", [])
    if injured:
        injured_names = []
        for i_entry in injured:
            if isinstance(i_entry, list) and len(i_entry) >= 2:
                i_name = i_entry[0]
                i_severity = i_entry[2] if len(i_entry) >= 3 else "unknown"
                injured_names.append(f"{i_name} ({i_severity})")
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
    language: str = "ru",
    game_id: str = "default_game",
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
    day_label = cs["day_label"]

    for d in range(1, current_turn):
        turn_record = get_game_turn(d, game_id=game_id)
        if not turn_record:
            continue

        combined_outcome = turn_record.get("combined_outcome", "")
        turn_summary = ""
        if combined_outcome:
            turn_summary = _build_turn_summary(combined_outcome, language=language)
        elif turn_record.get("story"):
            turn_summary = turn_record["story"][:300]

        if turn_summary:
            summaries.append(f"{day_label} {d}: {turn_summary}")

    if not summaries:
        return ""

    result = header + "\n" + "\n".join(summaries)
    # Truncate to 3000 chars to avoid blowing up the LLM prompt
    if len(result) > 3000:
        result = result[:3000] + "..."

    return result


async def _analyze_turn_outcome(
    turn: int,
    language: str = "ru",
    game_id: str = "default_game",
):
    """Analyze all decisions for a turn (player + NPC) to produce combined outcome.

    Called automatically when all players have submitted their choices,
    or can be triggered manually.
    """
    logger.info(f"[OUTCOME] Analyzing combined outcome for Turn {turn}")

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
            for c in choices:
                if c.get("id") == selected_id:
                    action_text = c.get("text", "")
                    consequence = c.get("consequence", "")
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
                    entity_name = str(player_id)
            elif npc_key:
                n = get_npc_profile(npc_key)
                if n:
                    role_name = n.get("role", "")
                    entity_name = n.get("npc_name", npc_key)

            all_decisions.append(
                {
                    "player_id": player_id,
                    "npc_key": npc_key,
                    "name": entity_name,
                    "role": role_name,
                    "action_id": selected_id,
                    "action_text": action_text,
                    "consequence": cr.get("consequence") or consequence,
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
        mission = get_mission(None, game_id)

        # Build full crew roster from all briefings — prevents LLM from inventing members
        crew_roster = []
        for b in all_briefings:
            player_id = b.get("player_id")
            npc_key = b.get("npc_key")
            role_name = "?"
            entity_name = "?"
            is_dead = False
            if player_id:
                p = get_player_profile(player_id)
                if p:
                    role_name = p.get("role", "?")
                    entity_name = p.get("player_name", "") or str(player_id)
                    is_dead = bool(p.get("is_dead", False))
            elif npc_key:
                n = get_npc_profile(npc_key)
                if n:
                    role_name = n.get("role", "?")
                    entity_name = n.get("npc_name", npc_key)
                    is_dead = not n.get("is_active", True)
            crew_roster.append({"name": entity_name, "role": role_name, "is_dead": is_dead})

        # Analyze with LLM
        gm = create_game_master_agent(language=language)
        outcome = gm.analyze_combined_outcome(
            global_circ,
            all_decisions,
            previous_summary,
            mission_context=mission,
            crew_roster=crew_roster,
        )

        # Save the combined outcome
        update_game_turn_outcome(turn, json.dumps(outcome, ensure_ascii=False), game_id)
        logger.info(f"[OUTCOME] Combined outcome saved for Turn {turn}")

        # Apply mission progress through the rules layer (P0+P1):
        # normalizes objectives, accumulates with regression caps + tempo floor,
        # and computes completion from real thresholds (fixes defect B/C).
        mission_progress = outcome.get("mission_progress", [])
        mission_completed = False
        if mission:
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
                end_game("mission_complete", game_id)
                logger.info("[MISSION] MISSION COMPLETE! Game ended.")
            mission = updated_mission

        # Rate-limit crew deaths through the rules layer (P3):
        # at most one death per DEATH_COOLDOWN_TURNS, never below min_alive;
        # excess proposed deaths are demoted to critical injuries.
        state = get_game_state(game_id)
        alive_count = sum(1 for r in crew_roster if not r.get("is_dead"))
        outcome, new_last_death_turn = apply_death_limits(
            outcome,
            turn=turn,
            last_death_turn=int(state.get("last_death_turn", 0) or 0),
            alive_count=alive_count,
        )
        if new_last_death_turn != int(state.get("last_death_turn", 0) or 0):
            set_last_death_turn(game_id, new_last_death_turn)
            logger.info(f"[DEATH] Cooldown window starts at turn {new_last_death_turn}")

        # ========== Process ship damage from new structured fields ==========
        ship_hull = outcome.get("ship_hull_integrity", 100)
        ship_shields = outcome.get("ship_shields", 100)
        ship_systems_offline = outcome.get("ship_systems_offline", [])
        ship_destroyed = outcome.get("ship_destroyed", False)

        # Compute crew_health from hull (hull=0 → crew_health=0, hull=100 → crew_health=100)
        # Shields also contribute to survival chances
        crew_health = max(0, min(100, int(ship_hull * 0.7 + ship_shields * 0.3)))

        logger.info(f"[SHIP] Turn {turn}: hull={ship_hull}%, shields={ship_shields}%, systems_offline={ship_systems_offline}, destroyed={ship_destroyed}")

        # Handle crew injuries (new structured field)
        crew_injured = outcome.get("crew_injured", [])
        for injury_entry in crew_injured:
            if isinstance(injury_entry, list) and len(injury_entry) >= 2:
                injured_name = injury_entry[0]
                injured_role = injury_entry[1]
                severity = injury_entry[2] if len(injury_entry) >= 3 else "unknown"
                # Try to find the player and log their injury
                for d in all_decisions:
                    if d.get("name") == injured_name or d.get("role") == injured_role:
                        pid = d.get("player_id")
                        if pid:
                            logger.info(f"[INJURY] Player {pid} ({injured_role}) injured: {severity}")
                            # Critical injuries → player becomes spectator for this turn
                            if severity == "critical":
                                logger.info(f"[INJURY] Player {pid} critically injured, out of action")
                        break

        # Handle crew deaths
        dead_crew = outcome.get("dead_crew_members", [])
        for death_entry in dead_crew:
            # death_entry could be [name, role]
            if isinstance(death_entry, list) and len(death_entry) >= 2:
                entity_name = death_entry[0]
                entity_role = death_entry[1]
                found = False
                # Try to find the player by looking up their entity name
                for d in all_decisions:
                    if d.get("name") == entity_name or d.get("role") == entity_role:
                        pid = d.get("player_id")
                        if pid:
                            mark_player_dead(pid, game_id)
                            logger.info(f"[DEATH] Player {pid} ({entity_role}) marked as dead")
                            found = True
                        break
                # If not a player, try to deactivate the NPC
                if not found:
                    for d in all_decisions:
                        if d.get("name") == entity_name or d.get("role") == entity_role:
                            npc_key = d.get("npc_key")
                            if npc_key:
                                deactivate_npc(npc_key)
                                logger.info(f"[DEATH] NPC {npc_key} ({entity_role}) deactivated")
                                break

        # Handle ship destruction
        if ship_destroyed:
            end_game("ship_destroyed", game_id)
            logger.warning(f"[SHIP] Ship destroyed! Game over for {game_id}")

        # Handle crew wiped — all crew members dead
        live_players = get_live_players(game_id)
        active_npcs = get_all_active_npcs(game_id)
        crew_wiped = len(live_players) == 0 and len(active_npcs) == 0
        if crew_wiped and not ship_destroyed:
            end_game("crew_wiped", game_id)
            logger.warning(f"[CREW] All crew dead! Game over for {game_id}")

        # Also update game state with computed crew_health
        state = get_game_state(game_id)
        ship_alive = not ship_destroyed and state.get("ship_alive", True)
        update_game_state(
            state["turn"],
            "active" if ship_alive else "ship_destroyed",
            ship_alive=ship_alive,
            crew_health=crew_health,
            game_id=game_id,
        )

        # Log ship systems offline
        if ship_systems_offline:
            logger.info(f"[SHIP] Systems offline: {', '.join(ship_systems_offline)}")

        # ── Push outcome to all alive players ──────────────────────
        # Build outcome text from the LLM result
        outcome_text = outcome.get("outcome_narrative", "") or outcome.get("narrative", "") or outcome.get("summary", "") or outcome.get("outcome", "")
        if not outcome_text:
            # Fallback: clean up JSON string for display
            raw = json.dumps(outcome, ensure_ascii=False)
            outcome_text = raw[:500] + ("..." if len(raw) > 500 else "")

        # Build death notices for the push payload
        # Resolve actual NPC/player names from profiles instead of using LLM-generated names
        death_notices = []
        for death_entry in dead_crew:
            if isinstance(death_entry, list) and len(death_entry) >= 2:
                llm_name = str(death_entry[0])
                llm_role = str(death_entry[1])
                real_name = llm_name
                # Try to find the real NPC/player name from briefings
                for b in all_briefings:
                    # Check NPC
                    if b.get("npc_key"):
                        n = get_npc_profile(b["npc_key"])
                        if n and (n.get("role") == llm_role or n.get("npc_name") == llm_name):
                            real_name = n.get("npc_name", llm_name)
                            break
                    # Check player
                    if b.get("player_id"):
                        p = get_player_profile(b["player_id"])
                        if p and (p.get("role") == llm_role or p.get("player_name") == llm_name):
                            real_name = p.get("player_name", "") or str(b["player_id"])
                            break
                death_notices.append({"name": real_name, "role": llm_role})

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
            outcome_image_url = await image_gen.generate_scene_image(
                prompt=outcome_prompt,
                filename_prefix=f"{game_id}/outcome_turn{turn}",
            )
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

        # Get alive players
        try:
            alive_players = get_live_players(game_id)
        except Exception:
            alive_players = get_players_in_game(game_id)

        # Compute crew counts for outcome display
        total_crew = len(all_briefings)  # all participants (players + NPCs)
        dead_this_turn = len(dead_crew)
        alive_crew = total_crew - dead_this_turn

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

        # ── Build action images array with captions ────────────────
        # After awaiting all pending tasks, briefings now have chosen_action_url populated.
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
        injury_notices = []
        crew_injured_list = outcome.get("crew_injured", [])
        for injury_entry in crew_injured_list:
            if isinstance(injury_entry, list) and len(injury_entry) >= 2:
                injury_notices.append(
                    {
                        "name": str(injury_entry[0]),
                        "role": str(injury_entry[1]),
                        "severity": str(injury_entry[2]) if len(injury_entry) >= 3 else "unknown",
                    }
                )

        # ── Build personal outcomes for push ────────────────────────
        personal_outcomes = outcome.get("personal_outcomes", [])

        # Push outcome synchronously so message order is deterministic
        # (outcome arrives BEFORE new turn briefings)
        try:
            await push_turn_outcome(
                game_id=game_id,
                turn=turn,
                outcome_text=outcome_text,
                alive_players=alive_players,
                outcome_image_url=outcome_image_url,
                ship_status="destroyed" if ship_destroyed else "alive",
                mission_progress=mission_progress,
                death_notices=death_notices,
                injury_notices=injury_notices,
                personal_outcomes=personal_outcomes,
                action_images=action_images,
                language=language,
                ship_hull_integrity=ship_hull,
                ship_shields=ship_shields,
                ship_systems_offline=ship_systems_offline,
                total_crew_count=total_crew,
                alive_crew_count=alive_crew,
            )
            logger.info(f"[OUTCOME] Outcome delivered for turn {turn} to {len(alive_players)} players")
        except Exception as push_err:
            logger.error(f"[OUTCOME] Failed to deliver outcome for turn {turn}: {push_err}", exc_info=True)

        # ── Game Over: generate and deliver finale ──────────────────
        game_ended = mission_completed or ship_destroyed or crew_wiped
        if game_ended:
            try:
                outcome_type = "victory" if mission_completed else "defeat"
                logger.info(f"[GAME_OVER] Game ended: {outcome_type}, generating finale...")

                # Build mission summary for the LLM prompt
                mission_summary_parts = []
                if mission:
                    for obj in mission.get("objectives", []):
                        stage = obj.get("stage", "?")
                        name = obj.get("name", "")
                        progress = mission.get("stage_progress", {}).get(str(stage), 0)
                        threshold = obj.get("success_threshold", "?")
                        done = "✓" if progress >= threshold else "✗"
                        mission_summary_parts.append(f"{done} Этап {stage}: {name} ({progress}/{threshold})")
                mission_summary = "\n".join(mission_summary_parts) if mission_summary_parts else "No mission data"

                # Get outcome_type label for LLM
                gs = get_game_strings(language)
                go_msgs = gs.get("game_over", {})
                outcome_label = go_msgs.get("victory_header") if mission_completed else go_msgs.get("defeat_header", outcome_type)

                gm = create_game_master_agent(language=language)
                game_over = gm.generate_game_over_outcome(
                    outcome_type=outcome_label,
                    outcome_narrative=outcome_text[:2000],
                    mission_summary=mission_summary,
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
                    alive_players=alive_players,
                    available_games=available_games,
                    language=language,
                )
                logger.info(f"[GAME_OVER] Finale delivered to {len(alive_players)} players: {outcome_type}")
            except Exception as go_err:
                logger.error(f"[GAME_OVER] Finale generation/delivery failed: {go_err}", exc_info=True)

    except Exception as e:
        logger.error(f"[OUTCOME] Analysis failed for Turn {turn}: {e}", exc_info=True)


# ============== Admin endpoints ==============


@app.post("/admin/create-game")
async def admin_create_game(request: CreateGameRequest):
    """Create a new game with a generated game_id."""
    game_id = generate_game_id()

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

    # Generate and persist title + welcome text once, at game creation.
    # These describe the ship shared by all players and must stay stable across onboardings.
    try:
        gm = create_game_master_agent(language=request.language)
        title_data = gm.generate_game_title()
        if title_data.get("title"):
            save_game_title_and_welcome(
                game_id,
                title_data["title"],
                title_data.get("welcome_text", ""),
            )
    except Exception as e:
        logger.warning(f"Title generation for new game {game_id} failed: {e}")

    return {
        "status": "success",
        "game_id": game_id,
        "name": get_game_title(game_id) or request.name,
        "language": request.language,
        "message": f"Game {game_id} created successfully",
    }


@app.post("/admin/set-language")
async def admin_set_language(request: SetLanguageRequest):
    """Set the language for a game and regenerate its title, mission and bridge image."""
    game = get_game(request.game_id)
    if not game:
        raise HTTPException(status_code=404, detail=f"Game {request.game_id} not found")

    if request.language not in ("ru", "en"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid language '{request.language}'. Supported: ru, en",
        )

    set_game_language(request.game_id, request.language)
    logger.info(f"Language for game {request.game_id} set to '{request.language}'")

    gm = create_game_master_agent(language=request.language)

    # Regenerate game title in the new language
    new_title = ""
    new_welcome = ""
    try:
        title_data = gm.generate_game_title()
        new_title = title_data.get("title", "")
        new_welcome = title_data.get("welcome_text", "")
        if new_title:
            save_game_title_and_welcome(request.game_id, new_title, new_welcome)
            logger.info(f"Regenerated game title in {request.language}: {new_title}")
    except Exception as e:
        logger.warning(f"Failed to regenerate game title for {request.game_id}: {e}")

    # Regenerate mission and bridge image
    new_mission_name = ""
    try:
        # Build participant list from live players + active NPCs
        all_participants = []
        for pid in get_live_players(request.game_id):
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
        for npc in get_all_active_npcs(request.game_id):
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

        if all_participants:
            # Delete old mission and bridge image
            delete_mission(request.game_id)
            delete_game_images(request.game_id)

            # Generate new mission
            mission_data = gm.generate_mission(all_participants)
            mission_result = create_mission(mission_data, request.game_id)
            if mission_result:
                new_mission_name = mission_result.get("name", "")
                logger.info(f"Regenerated mission in {request.language}: {new_mission_name}")

                # Generate new bridge image
                try:
                    bridge_result = gm.generate_bridge_image_prompt(mission_data or {}, all_participants)
                    bridge_prompt = bridge_result.get("bridge_prompt", "")
                    if bridge_prompt:
                        image_gen = create_image_generator()
                        bridge_url = await image_gen.generate_scene_image(
                            prompt=bridge_prompt,
                            filename_prefix=f"{request.game_id}/bridge",
                        )
                        if bridge_url:
                            save_game_image(
                                type="bridge",
                                image_url=bridge_url,
                                game_id=request.game_id,
                                prompt=bridge_prompt,
                            )
                            logger.info(f"Regenerated bridge image: {bridge_url}")
                except Exception as e:
                    logger.warning(f"Failed to regenerate bridge image: {e}")
    except Exception as e:
        logger.warning(f"Failed to regenerate mission for {request.game_id}: {e}")

    return {
        "status": "success",
        "game_id": request.game_id,
        "language": request.language,
        "title": new_title,
        "mission_name": new_mission_name or None,
        "message": f"Game language set to {request.language}, title and mission regenerated",
    }


def _build_player_briefings_for_push(
    all_briefings: list[dict],
    crew_dialogues: list[dict],
    turn_num: int,
    game_id: str = "default_game",
) -> list[dict]:
    """Build per-player briefing dicts for push payload from stored briefings.

    Fetches scene image (if available) from game_images table for this turn.
    Also fetches player_name for each real player to include in the payload.
    """
    # Fetch scene image for this turn (if generated and saved)
    scene_url = get_random_game_image(type="scene", turn=turn_num, game_id=game_id)
    players_data = []
    for b in all_briefings:
        if b.get("is_npc"):
            continue  # Only send to real players
        player_id = b.get("player_id")
        if not player_id:
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


@app.post("/admin/generate-turn")
async def generate_turn_episode(
    language: str = "en",
    game_id: str = "default_game",
    previous_actions: list[dict[str, Any]] | None = None,
    previous_summary: str | None = None,
    team_assembly_status: dict[str, Any] | None = None,
):
    """Generate a new turn episode (called by game scheduler)"""
    state = get_game_state(game_id)
    turn_num = state["turn"]

    # Use game's stored language — the caller may not know it
    language = get_game_language(game_id) or language

    logger.info("=== GENERATE TURN STARTED ===")
    logger.info(f"Turn number: {turn_num}")
    logger.info(f"Language: {language}")
    logger.info(f"Previous actions count: {len(previous_actions) if previous_actions else 0}")

    game_master = create_game_master_agent(language=language)

    player_role = "Crew Member" if language != "ru" else "Член экипажа"
    logger.info(f"Player role: {player_role}")

    # Generate previous turn summary from actions for story consistency
    summary = previous_summary or ""
    if not summary and previous_actions:
        action_summaries = []
        for action in previous_actions:
            action_summaries.append(f"Turn {action.get('turn', 0)}: Player chose '{action.get('choice')}'")
        summary = " | ".join(action_summaries)

    story = game_master.generate_turn_story(
        turn=turn_num,
        previous_summary=summary or state["last_updated"],
        player_role=player_role,
    )

    logger.info("Generating NPC dialogues...")
    dialogues = game_master.generate_crew_dialogues(
        story=story,
        player_role=player_role,
        crew_members=_get_crew_members(game_id),
    )

    new_turn = {
        "turn": turn_num,
        "story": story.narrative,
        "crew_dialogues": [{"npc": d.npc_name, "dialogue": d.dialogue} for d in dialogues],
        "player_actions": story.decision_points,
        "generated_content": {
            "image": f"/content/turn_{turn_num}/scene.jpg",
            "comic": f"/content/turn_{turn_num}/comic.webp",
        },
        "previous_turn_summary": summary,
    }

    create_game_turn(new_turn, game_id)
    update_game_state(turn_num + 1, "active", game_id=game_id)

    logger.info("=== GENERATE TURN COMPLETED ===")
    logger.info(f"Story: {story.narrative}...")
    logger.info(f"NPC dialogues: {len(dialogues)}")
    logger.info(f"Player actions: {len(story.decision_points)}")

    return new_turn


@app.post("/admin/generate-comic/{player_id}")
async def generate_chosen_action_image(
    player_id: int,
    turn: int | None = None,
    game_id: str = "default_game",
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
    # Generate prompt via LLM if game_master is available
    prompt = ""
    try:
        gm = create_game_master_agent(language="en")
        prompt = gm.generate_chosen_action_prompt(
            role=role,
            traits=traits,
            avatar_description=profile.get("avatar_description", ""),
            action_text=turn_data["story"][:200],
            setting=turn_data["story"][:300],
            species_desc=profile.get("species_description", ""),
            species_type=profile.get("species", ""),
            species_category=profile.get("species_primary_key") or "",
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
        prompt=prompt,
        filename_prefix=f"{game_id}/action_turn{turn_num_val}_p{player_id}",
    )

    # Store chosen_action_url in player's briefing for this turn (if briefing exists)
    briefing = get_player_briefing(turn_num_val, player_id, game_id=game_id)
    if briefing:
        update_briefing_chosen_action_url(briefing["id"], chosen_action_url)

    return {
        "player_id": player_id,
        "turn": turn_num_val,
        "chosen_action_url": chosen_action_url,
        "role": profile["role"],
    }


@app.post("/admin/generate-loading-images")
async def admin_generate_loading_images(count: int = 10, game_id: str = "default_game"):
    """Manually trigger generation of loading screen images."""
    logger.info(f"[ADMIN] Generating {count} loading images for game {game_id}")

    try:
        image_generator = create_image_generator()
        urls = await image_generator.generate_loading_images(
            count=count,
            game_id=game_id,
        )

        saved = 0
        for url in urls:
            if url:
                save_game_image(type="loading", image_url=url, game_id=game_id)
                saved += 1

        return {
            "status": "success",
            "requested": count,
            "generated": len(urls),
            "saved": saved,
            "total_in_db": get_game_image_count("loading", game_id),
        }
    except Exception as e:
        logger.error(f"[ADMIN] Loading image generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/admin/generate-splash-images")
async def admin_generate_splash_images(game_id: str = "default_game", lang: str = "ru"):
    """Generate 3 splash images for the game using current game title.

    If the game has no title yet, uses a fallback.
    """
    logger.info(f"[ADMIN] Generating splash images for game {game_id}")

    gs = get_game_strings(lang)
    game_title = get_game_title(game_id) or gs["game_title_fallback"]
    welcome_text = "Космический корабль в глубинах неизведанного космоса."

    try:
        image_generator = create_image_generator()
        urls = await image_generator.generate_splash_images(
            game_title=game_title,
            welcome_text=welcome_text,
            count=3,
            game_id=game_id,
        )

        saved = 0
        for url in urls:
            if url:
                save_game_image(type="splash", image_url=url, game_id=game_id)
                saved += 1

        return {
            "status": "success",
            "requested": 3,
            "generated": len(urls),
            "saved": saved,
            "total_in_db": get_game_image_count("splash", game_id),
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

    # Add real players
    for pid in get_players_in_game(game_id):
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
    return random.choice(_NPC_SPECIES_OPTIONS)


def _random_npc_gender(language: str = "ru") -> str:
    """Pick a random localized gender display name for NPC.

    Returns a display name (e.g. "Мужской" or "Male") rather than a key.
    """
    lang_key = LANGUAGE_RU if language == LANGUAGE_RU else LANGUAGE_EN
    gender_key = random.choice(list(_NPC_GENDER_OPTIONS[lang_key].keys()))
    return _NPC_GENDER_OPTIONS[lang_key][gender_key]


async def _background_start_wrapper(request: StartGameRequest, turn_num: int):
    """Run start-game in background, notify GM on completion."""
    try:
        result = await _original_start_game(request)
        if result and result.get("status") == "success":
            await _notify_scheduler("reset")
            await push_gm_notification(
                game_id=request.game_id,
                turn=turn_num,
                status="success",
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

    # 3. Create NPCs for all unfilled roles
    npcs_created = []
    gm = create_game_master_agent(language=language)

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

        # Generate creative name via LLM (with fallback), avoid duplicates
        npc_name_attempt = gm.generate_npc_name(
            role_key=role_key,
            role_name=role_name,
            species=npc_species,
            gender=npc_gender,
            avatar_description=role_data.get("avatar_description", ""),
            personality_traits=role_data.get("personality_traits", []),
            avoid_names=avoid_names,
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
            "role_description": role_data.get("role_description", ""),
            "personality_traits": role_data.get("personality_traits", []),
            "species": npc_species,
            "gender": npc_gender,
            "avatar_description": role_data.get("avatar_description", ""),
            "game_id": game_id,
            "is_active": True,
            "replaces_player_id": None,
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
            avatar_prompts = gm.generate_npc_avatar_prompts(npc_roles_for_avatar)
            for prompt_entry in avatar_prompts:
                role_key = prompt_entry.get("role_key", "")
                prompt = prompt_entry.get("prompt", "")
                if role_key and prompt:
                    url = await image_gen.generate_avatar_image(
                        prompt=prompt,
                        filename_prefix=f"{game_id}/avatar_{role_key}",
                    )
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

    # 6b. Generate mission
    mission_data = gm.generate_mission(all_participants)
    mission_result = create_mission(mission_data, game_id)
    if mission_result:
        logger.info(f"[MISSION] Mission created: {mission_result.get('name', '')} ({mission_result.get('total_stages', 0)} stages)")
    else:
        logger.error("[MISSION] Failed to create mission", stack_info=True)
        mission_result = {}

    # 6c. Generate bridge image
    try:
        bridge_result = gm.generate_bridge_image_prompt(mission_data or {}, all_participants)
        bridge_prompt = bridge_result.get("bridge_prompt", "")
        if bridge_prompt:
            image_gen = create_image_generator()
            bridge_url = await image_gen.generate_scene_image(
                prompt=bridge_prompt,
                filename_prefix=f"{game_id}/bridge",
            )
            if bridge_url:
                save_game_image(
                    type="bridge",
                    image_url=bridge_url,
                    game_id=game_id,
                    prompt=bridge_prompt,
                )
                logger.info(f"[BRIDGE] Bridge image saved: {bridge_url}")
    except Exception as e:
        logger.warning(f"[BRIDGE] Image generation failed: {e}")

    # 7. Generate the game turn with the new restructured flow
    state = get_game_state(game_id)
    turn_num = state["turn"]

    # Build cumulative summary from ALL previous turns, not just the last one
    previous_summary = _build_cumulative_story_summary(
        current_turn=turn_num,
        language=language,
        game_id=game_id,
    )

    # Step A: Generate global circumstances (with mission context for story consistency)
    global_circ = gm.generate_global_circumstances(
        turn=turn_num,
        previous_summary=previous_summary,
        player_profiles=all_participants,
        mission_context=mission_data,
    )
    global_narrative = global_circ.get("narrative", "")

    # Save global circumstances
    update_game_turn_global_circumstances(
        turn_num,
        json.dumps(global_circ, ensure_ascii=False),
        game_id,
    )

    # Step A2: Generate scene image for this turn's briefing
    # Uses LLM-generated scene_prompt if available (from global_circ), otherwise falls back to hardcoded prompt
    scene_url = None
    try:
        # Prefer LLM-generated scene_prompt
        scene_prompt = global_circ.get("scene_prompt", "")
        if not scene_prompt:
            # Fallback: build from setting + narrative
            scene_prompt = f"Sci-fi scene: {global_circ.get('setting', '')}. {global_narrative[:500]} Cinematic starship interior, crew interacting with holographic displays, dramatic lighting from the main viewscreen, Star Trek aesthetic, 4K quality."
        # Remove [avatar: ...] markers before sending to image gen
        import re

        scene_prompt_clean = re.sub(r"\[avatar:\s*\w+\]", "", scene_prompt).strip()
        image_gen = create_image_generator()
        scene_url = await image_gen.generate_scene_image(
            prompt=scene_prompt_clean,
            filename_prefix=f"{game_id}/scene_turn{turn_num}",
        )
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

    async def _process_participant(
        participant: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Generate briefing for one participant (player or NPC) under semaphore."""
        async with sem:
            player_id = participant.get("player_id")
            # Get player_name from profile if available
            player_name = ""
            if player_id:
                p = get_player_profile(player_id)
                if p:
                    player_name = p.get("player_name", "") or ""
            elif participant.get("type") == "npc":
                player_name = participant.get("npc_name", "") or ""

            gm_profile = {
                "player_id": player_id,
                "npc_key": participant.get("npc_key"),
                "role": participant["role"],
                "personality_traits": participant.get("personality_traits", []),
                "role_description": participant.get("role_description", ""),
            }
            try:
                # LLM call — run in thread pool to avoid blocking the event loop
                briefing_data = await asyncio.to_thread(
                    gm.generate_player_briefing_and_choices,
                    global_circ,
                    gm_profile,
                    player_name,
                    turn_num,
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
                # NPCs decide immediately without seeing consequences
                npc_profile = get_npc_profile(participant["npc_key"]) or participant
                try:
                    npc_decision = await asyncio.to_thread(gm.generate_npc_choice, choices, npc_profile)
                except Exception as e:
                    logger.error(f"[NPC] Failed to generate choice for {participant.get('npc_key', '?')}: {e}", exc_info=True)
                    return None

                selected_id = npc_decision.get("action_id", "")
                rationale = npc_decision.get("rationale", "")

                # Find the consequence for the chosen action
                chosen_consequence = ""
                for c in choices:
                    if c.get("id") == selected_id:
                        chosen_consequence = c.get("consequence", "")
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
                        "consequence_result": {"consequence": chosen_consequence},
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

        # Try LLM-based species-aware prompt generation
        prompt = ""
        try:
            # Build combined description (same as onboarding avatar flow)
            parts = [avatar_desc]
            if species_type and species_type not in ("Unknown", "Неизвестно"):
                parts.append(f"Species type: {species_type}")
            if gender_type and gender_type not in ("Unknown", "Неизвестно"):
                parts.append(f"Gender type: {gender_type}")
            if species_desc:
                parts.append(f"Appearance: {species_desc}")
            avatar_description_combined = "\n".join(parts)

            prompt = await asyncio.to_thread(
                gm.generate_avatar_prompt,
                role=role,
                traits=traits,
                avatar_description=avatar_description_combined,
                species_category=profile.get("species_primary_key") or "",
            )
        except Exception as e:
            logger.warning(f"[CHAR_IMAGE] LLM prompt generation failed for {role}: {e}")

        if not prompt:
            # Fallback: hardcoded prompt
            prompt = (
                f"Sci-fi character portrait of {player_name}, the {role}, "
                f"placed in the current environment: {setting[:200]}. "
                f"Character appearance: {avatar_desc[:200]}. "
                f"{species_desc[:150]}"
                f"Personality: {', '.join(traits) if traits else 'professional'}. "
                f"The character is reacting to the situation around them. "
                f"Cinematic sci-fi portrait, upper body, dynamic lighting, "
                f"detailed uniform, 4K quality, Star Trek aesthetic."
            )

        image_gen = create_image_generator()
        avatar_url = profile.get("avatar_url") or None
        character_description = role
        if species_type and species_type not in ("Unknown", "Неизвестно"):
            character_description += f", {species_type}"
        if species_desc:
            character_description += f". {species_desc[:200]}"

        url = await image_gen.generate_action_image_with_reference(
            prompt=prompt,
            reference_image_url=avatar_url,
            character_description=character_description,
            filename_prefix=f"{game_id}/char_turn{turn_num}_p{pid}",
        )
        if url:
            save_game_image(
                type="character",
                image_url=url,
                game_id=game_id,
                turn=turn_num,
                prompt=prompt,
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
    from game_master import GameStory

    dialog_story = GameStory(
        turn=turn_num,
        setting=global_circ.get("setting", ""),
        conflict=global_circ.get("conflict", ""),
        narrative=global_narrative,
        decision_points=[],
    )
    try:
        dialogues = gm.generate_crew_dialogues(
            story=dialog_story,
            player_role=player_role,
            crew_members=_get_crew_members(game_id),
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

    # Advance game state to next turn
    update_game_state(turn_num + 1, "active", game_id=game_id)

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
    bridge_url = get_random_game_image(type="bridge", game_id=game_id)

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
        player_briefings = _build_player_briefings_for_push(all_briefings, crew_dialogues_list, turn_num, game_id=game_id)
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
async def get_mission_endpoint(game_id: str = "default_game"):
    """Get the current mission for a game."""
    mission = get_mission(None, game_id)
    if not mission:
        raise HTTPException(status_code=404, detail="No mission found for this game")
    return mission


@app.get("/game/bridge-image")
async def get_bridge_image_endpoint(
    game_id: str = "default_game",
    turn: int | None = Query(None),
):
    """Get the bridge image for a game.

    Args:
        game_id: Game identifier
        turn: If set, returns the scene image for that turn instead of bridge image.
    """
    img_type = "scene" if turn is not None else "bridge"
    url = get_random_game_image(type=img_type, game_id=game_id, turn=turn)
    if not url:
        raise HTTPException(status_code=404, detail=f"No {img_type} image found")
    return {"image_url": url, "game_id": game_id, "type": img_type}


@app.get("/game/team")
async def get_team_endpoint(game_id: str = "default_game"):
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
    # (checks ship_roles.taken_by to also handle legacy data).
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT n.* FROM npc_profiles n LEFT JOIN ship_roles sr ON sr.role_key = n.role_key AND sr.game_id = n.game_id WHERE n.game_id = ? AND (n.is_active = 1 OR sr.taken_by IS NULL) ORDER BY n.created_at",
        (game_id,),
    )
    npc_rows = cursor.fetchall()
    conn.close()
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
        team.append(
            {
                "name": npc_name or npc_role,
                "role": npc_role,
                "species": npc_species,
                "gender": npc_gender,
                "avatar_url": avatar_url,
                "is_dead": not is_active,
            }
        )

    return {"game_id": game_id, "members": team, "count": len(team)}


@app.post("/player/{player_id}/die")
async def mark_player_dead_endpoint(player_id: int, game_id: str = "default_game"):
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
async def get_spectator_ids_endpoint(game_id: str = "default_game"):
    """Get IDs of dead players (spectators) in a game."""
    dead = get_dead_players(game_id)
    return {"spectator_ids": dead, "count": len(dead)}


@app.get("/players/{game_id}/live")
async def get_live_player_ids_endpoint(game_id: str = "default_game"):
    """Get IDs of live players in a game."""
    live = get_live_players(game_id)
    return {"live_player_ids": live, "count": len(live)}


def _replace_player_with_npc(
    player_id: int,
    role_key: str,
    game_id: str,
    reason: str,
    language: str = "ru",
) -> dict[str, Any]:
    """Replace a player with an NPC that takes over their role.

    Core of the kick/reset flow: builds an NPC preserving the player's name,
    species, gender and appearance, releases ONLY that role (not every role in
    the game), creates the NPC profile and records the kick. Does NOT touch the
    player profile itself — the caller decides whether to NULL its game_id (kick)
    or delete it outright (reset).

    Returns {"role_name", "npc_key", "npc_name"}. Raises HTTPException on failure.
    """
    role_data = get_role_by_key(role_key, language="ru", game_id=game_id)
    if not role_data:
        raise HTTPException(status_code=404, detail=f"Role '{role_key}' not found")

    kicked_profile = get_player_profile(player_id)
    npc_name = ((kicked_profile.get("player_name", "") or "") if kicked_profile else role_data["role_name"]) or role_data["role_name"]
    npc_traits = kicked_profile.get("personality_traits", []) if kicked_profile else role_data.get("personality_traits", [])
    npc_species = (kicked_profile.get("species", "") or "") if kicked_profile else ""
    npc_gender = (kicked_profile.get("gender", "") or "") if kicked_profile else ""
    if not npc_species:
        npc_species = _random_npc_species()
    if not npc_gender:
        npc_gender = _random_npc_gender(language)
    npc_avatar_desc = kicked_profile.get("avatar_description", "") if kicked_profile else role_data.get("avatar_description", "")
    npc_role_description = kicked_profile.get("role_description", "") if kicked_profile else role_data.get("role_description", "")

    # Release ONLY the target role (reset_roles() here nuked every assignment).
    release_role(role_key, game_id)

    npc = create_npc_profile(
        {
            "npc_key": f"npc_{role_key}_{game_id}",
            "role_key": role_key,
            "npc_name": npc_name,
            "role": role_data["role_name"],
            "role_description": npc_role_description,
            "personality_traits": npc_traits,
            "species": npc_species,
            "gender": npc_gender,
            "avatar_description": npc_avatar_desc,
            "game_id": game_id,
            "is_active": True,
            "replaces_player_id": player_id,
        }
    )
    if not npc:
        raise HTTPException(status_code=500, detail="Failed to create NPC replacement")

    record_kick(player_id, npc["npc_key"], reason)
    logger.info(f"[REPLACE] Player {player_id} replaced by NPC {npc_name} for role {role_key} in game {game_id}")
    return {
        "role_name": role_data["role_name"],
        "npc_key": npc["npc_key"],
        "npc_name": npc_name,
        "player_avatar_url": kicked_profile.get("avatar_url") if kicked_profile else None,
        "player_avatar_desc": kicked_profile.get("avatar_description", "") if kicked_profile else "",
    }


async def _generate_replacement_npc_avatar(
    npc_key: str,
    role_key: str,
    role_name: str,
    player_avatar_url: str | None,
    player_avatar_desc: str,
    game_id: str,
) -> None:
    """Generate an NPC avatar using the replaced player's avatar as reference.

    Uses img2img (denoise=0.4) to adapt the player's avatar into an NPC portrait
    that preserves the character's appearance. Falls back to text-to-image if
    the player had no avatar or img2img fails.
    """
    prompt = _extract_avatar_prompt(player_avatar_desc)
    if not prompt:
        prompt = f"Sci-fi character portrait of {role_name}. Cinematic lighting, detailed uniform, 4K quality, space opera aesthetic."

    try:
        image_gen = create_image_generator()

        if player_avatar_url:
            # Try img2img with player's avatar as reference (low denoise to preserve appearance)
            url = await image_gen.generate_action_image_with_reference(
                prompt=prompt,
                reference_image_url=player_avatar_url,
                character_description=role_name,
                filename_prefix=f"{game_id}/avatar_{role_key}",
                width=768,
                height=1024,
                denoise=0.4,
            )
        else:
            # Fallback: text-to-image
            url = await image_gen.generate_avatar_image(
                prompt=prompt,
                filename_prefix=f"{game_id}/avatar_{role_key}",
            )

        if url:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE npc_profiles SET avatar_description = ? WHERE npc_key = ?",
                (f"avatar_url={url};{prompt}", npc_key),
            )
            conn.commit()
            conn.close()
            logger.info(f"[NPC_AVATAR] Generated replacement avatar for {npc_key}: {url}")
        else:
            logger.warning(f"[NPC_AVATAR] Failed to generate avatar for {npc_key}")
    except Exception as e:
        logger.warning(f"[NPC_AVATAR] Avatar generation failed for {npc_key}: {e}")


@app.post("/admin/kick-player")
async def admin_kick_player(request: KickPlayerRequest):
    """Kick a player by role, replace with NPC, and notify the kicked player.

    The kicked player receives a message about being removed from the game.
    The NPC takes over the role with LLM-based decisions.
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

    replaced = _replace_player_with_npc(
        player_id=kicked_player_id,
        role_key=role_key,
        game_id=game_id,
        reason=request.reason,
        language=request.language,
    )

    # Generate NPC avatar using kicked player's avatar as reference
    await _generate_replacement_npc_avatar(
        npc_key=replaced["npc_key"],
        role_key=role_key,
        role_name=replaced["role_name"],
        player_avatar_url=replaced.get("player_avatar_url"),
        player_avatar_desc=replaced.get("player_avatar_desc", ""),
        game_id=game_id,
    )

    # Notify the kicked player (via game_messages)
    kick_notification = f"⛔ **Вы были изгнаны с корабля!**\n\nGame Master принял решение заменить вас NPC.\n**Причина:** {request.reason}\n\nВаш персонаж заменён на {replaced['npc_name']}.\nСпасибо за игру!"
    add_game_message(kicked_player_id, kick_notification, "kick_notification")

    # Remove from game but keep the profile data
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE player_profiles SET game_id = NULL WHERE player_id = ?",
        (kicked_player_id,),
    )
    conn.commit()
    conn.close()

    logger.info("=== ADMIN KICK PLAYER COMPLETED ===")
    logger.info(f"Kicked player {kicked_player_id}, replaced with NPC {replaced['npc_name']}")

    return {
        "status": "success",
        "kicked_player_id": kicked_player_id,
        "role_key": role_key,
        "role_name": replaced["role_name"],
        "npc_key": replaced["npc_key"],
        "npc_name": replaced["npc_name"],
        "reason": request.reason,
    }


class ResetPlayerRequest(BaseModel):
    """Request to reset a player's game participation (self-service /reset)."""

    player_id: int
    language: str = "ru"


@app.post("/admin/reset-player")
async def admin_reset_player(request: ResetPlayerRequest):
    """Reset a player's participation: replace them with an NPC, then wipe their
    profile and onboarding answers so they can start over from scratch.
    """
    logger.info("=== ADMIN RESET PLAYER ===")
    logger.info(f"player_id={request.player_id}")

    player_id = request.player_id
    profile = get_player_profile(player_id)
    game_id = (profile.get("game_id") if profile else None) or "default_game"

    # Replace the player with an NPC if they currently hold a role.
    npc_replaced = None
    role_key = get_role_key_for_player(player_id, game_id)
    if role_key:
        npc_replaced = _replace_player_with_npc(
            player_id=player_id,
            role_key=role_key,
            game_id=game_id,
            reason="Player reset",
            language=request.language,
        )

    # Wipe the player's data so they can start a fresh onboarding.
    profile_deleted = delete_player_profile(player_id)
    sessions_deleted = delete_onboarding_sessions_for_player(player_id)

    logger.info(f"=== ADMIN RESET PLAYER COMPLETED === player_id={player_id}, game_id={game_id}, role_replaced={role_key}, profile_deleted={profile_deleted}, sessions_deleted={sessions_deleted}")

    return {
        "status": "success",
        "player_id": player_id,
        "game_id": game_id,
        "role_replaced": role_key,
        "npc_name": npc_replaced["npc_name"] if npc_replaced else None,
        "profile_deleted": profile_deleted,
        "sessions_deleted": sessions_deleted,
    }


@app.get("/admin/list-games")
async def admin_list_games():
    """List all active games with player counts."""
    games = get_available_games()
    result = []
    for game in games:
        game_id = game["game_id"]
        onboarding_count = get_onboarding_count_in_game(game_id)
        current_turn = 0
        if is_game_started(game_id):
            state = get_game_state(game_id)
            current_turn = state.get("turn", 0)
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
            }
        )
    return {"games": result}


@app.post("/admin/analyze-turn")
async def admin_analyze_turn(
    turn: int | None = None,
    language: str = "ru",
    game_id: str = "default_game",
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
    await _analyze_turn_outcome(turn_num, language=language, game_id=game_id)

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
):
    """Run continue-game in background, notify GM on completion."""
    try:
        result = await _original_continue_game(
            game_id=game_id,
            language=language,
            force_resend=force_resend,
        )
        if result and result.get("status") == "success":
            await _notify_scheduler("reset")
            await push_gm_notification(
                game_id=game_id,
                turn=turn_num,
                status="success",
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
            language=language,
        )


@app.post("/admin/continue-game")
async def admin_continue_game(
    game_id: str = "default_game",
    language: str = "ru",
    force_resend: bool = False,
):
    """Generate the next turn in the game.

    Starts background generation and returns immediately.
    GM will receive a push notification via Telegram when done.
    """
    # Use game's stored language if available
    language = get_game_language(game_id) or language
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

    # Start background generation task
    asyncio.create_task(
        _background_continue_wrapper(
            game_id=game_id,
            language=language,
            force_resend=force_resend,
            turn_num=turn_num,
        )
    )

    logger.info(f"Background turn generation started for turn {turn_num}")

    return {
        "status": "accepted",
        "turn": turn_num,
        "message": f"Turn generation started for turn {turn_num}. You'll be notified when complete.",
    }


async def _original_continue_game(
    game_id: str = "default_game",
    language: str = "ru",
    force_resend: bool = False,
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

    gm = create_game_master_agent(language=language)

    # Fetch mission data for story consistency
    mission_data = get_mission(None, game_id) or {}

    # Step A: Generate global circumstances (with mission context for story consistency)
    global_circ = gm.generate_global_circumstances(
        turn=turn_num,
        previous_summary=previous_summary,
        player_profiles=all_participants,
        mission_context=mission_data,
    )

    # Save global circumstances
    update_game_turn_global_circumstances(
        turn_num,
        json.dumps(global_circ, ensure_ascii=False),
        game_id,
    )

    # Step A2: Generate scene image for this turn's briefing
    # Uses LLM-generated scene_prompt if available (from global_circ), otherwise falls back to hardcoded prompt
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
        scene_url = await image_gen.generate_scene_image(
            prompt=scene_prompt_clean,
            filename_prefix=f"{game_id}/scene_turn{turn_num}",
        )
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
    }
    create_game_turn(early_turn, game_id)
    logger.info(f"[TURN] Early game turn record created for turn {turn_num}")

    # Step B: Generate per-player briefings
    all_briefings = []
    for participant in all_participants:
        gm_profile = {
            "player_id": participant.get("player_id"),
            "npc_key": participant.get("npc_key"),
            "role": participant["role"],
            "personality_traits": participant.get("personality_traits", []),
            "role_description": participant.get("role_description", ""),
        }
        player_name = ""
        if participant["type"] == "player" and participant.get("player_id"):
            p = get_player_profile(participant["player_id"])
            if p:
                player_name = p.get("player_name", "") or ""
        elif participant["type"] == "npc":
            player_name = participant.get("npc_name", "") or ""
        briefing_data = gm.generate_player_briefing_and_choices(global_circ, gm_profile, player_name, turn_num)
        briefing = briefing_data.get("briefing", "")
        choices = briefing_data.get("choices", [])
        personal_title = briefing_data.get("personal_title", "")
        if personal_title and turn_num:
            gs = get_game_strings(language)
            personal_title = gs["turn_prefix_simple"].format(turn=turn_num) + f" — {personal_title}"

        if participant["type"] == "npc":
            npc_profile = get_npc_profile(participant["npc_key"]) or participant
            npc_decision = gm.generate_npc_choice(choices, npc_profile)
            selected_id = npc_decision.get("action_id", "")
            rationale = npc_decision.get("rationale", "")

            chosen_consequence = ""
            for c in choices:
                if c.get("id") == selected_id:
                    chosen_consequence = c.get("consequence", "")
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
                    "consequence_result": {"consequence": chosen_consequence},
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

        # Try LLM-based species-aware prompt generation
        prompt = ""
        try:
            # Build combined description (same as onboarding avatar flow)
            parts = [avatar_desc]
            if species_type and species_type not in ("Unknown", "Неизвестно"):
                parts.append(f"Species type: {species_type}")
            if gender_type and gender_type not in ("Unknown", "Неизвестно"):
                parts.append(f"Gender type: {gender_type}")
            if species_desc:
                parts.append(f"Appearance: {species_desc}")
            avatar_description_combined = "\n".join(parts)

            prompt = gm.generate_avatar_prompt(
                role=role,
                traits=traits,
                avatar_description=avatar_description_combined,
                species_category=profile.get("species_primary_key") or "",
            )
        except Exception as e:
            logger.warning(f"[CHAR_IMAGE] LLM prompt generation failed for {role}: {e}")

        if not prompt:
            # Fallback: hardcoded prompt
            prompt = (
                f"Sci-fi character portrait of {player_name}, the {role}, "
                f"placed in the current environment: {setting[:200]}. "
                f"Character appearance: {avatar_desc[:200]}. "
                f"{species_desc[:150]}"
                f"Personality: {', '.join(traits) if traits else 'professional'}. "
                f"The character is reacting to the situation around them. "
                f"Cinematic sci-fi portrait, upper body, dynamic lighting, "
                f"detailed uniform, 4K quality, Star Trek aesthetic."
            )

        image_gen = create_image_generator()
        avatar_url = profile.get("avatar_url") or None
        character_description = role
        if species_type and species_type not in ("Unknown", "Неизвестно"):
            character_description += f", {species_type}"
        if species_desc:
            character_description += f". {species_desc[:200]}"

        url = await image_gen.generate_action_image_with_reference(
            prompt=prompt,
            reference_image_url=avatar_url,
            character_description=character_description,
            filename_prefix=f"{game_id}/char_turn{turn_num}_p{pid}",
        )
        if url:
            save_game_image(
                type="character",
                image_url=url,
                game_id=game_id,
                turn=turn_num,
                prompt=prompt,
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
    from game_master import GameStory

    dialog_story = GameStory(
        turn=turn_num,
        setting=global_circ.get("setting", ""),
        conflict=global_circ.get("conflict", ""),
        narrative=global_circ.get("narrative", ""),
        decision_points=[],
    )
    try:
        dialogues = gm.generate_crew_dialogues(
            story=dialog_story,
            player_role=player_role,
            crew_members=_get_crew_members(game_id),
        )
        crew_dialogues_list = [{"npc": d.npc_name, "dialogue": d.dialogue} for d in dialogues]
    except Exception as e:
        logger.warning(f"NPC dialogue generation failed: {e}")
        crew_dialogues_list = []

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
    }
    create_game_turn(new_turn, game_id)

    # Advance game state
    update_game_state(turn_num + 1, "active", game_id=game_id)

    # ── Push previous turn outcome (if applicable) ──────────────
    # Must run BEFORE pushing new turn briefings so player sees:
    #   Итоги хода N-1 → Вводная хода N → Ход N + действия
    if turn_num > 1:
        await _analyze_turn_outcome(
            turn=turn_num - 1,
            language=language,
            game_id=game_id,
        )

    # ── Push briefings to telegram-bot ─────────────────────────
    try:
        # Build the global intro narrative from global circumstances
        global_narrative = global_circ.get("narrative", "")

        player_briefings = _build_player_briefings_for_push(all_briefings, crew_dialogues_list, turn_num, game_id=game_id)
        if player_briefings:
            asyncio.create_task(
                push_briefings(
                    game_id=game_id,
                    turn=turn_num,
                    players_briefings=player_briefings,
                    crew_dialogues=crew_dialogues_list,
                    is_first_turn=False,
                    force_resend=force_resend,
                    global_narrative=global_narrative,
                    language=language,
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
    game_id: str = "default_game",
    language: str = "ru",
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

    # Delete current turn's data
    deleted_briefings = delete_player_briefings_for_turn(regenerate_turn, game_id)
    deleted_actions = delete_player_actions_for_turn(regenerate_turn, game_id)
    deleted_turn = delete_game_turn(regenerate_turn, game_id)

    logger.info(f"Deleted: {deleted_briefings} briefings, {deleted_actions} player actions, turn_record={deleted_turn}")

    # Roll back game state to before the deleted turn
    reset_game_state_to_turn1(game_id)
    # Restore to the correct turn (the turn being regenerated)
    update_game_state(regenerate_turn, "active", game_id=game_id)

    # Now regenerate the turn using the continue-game logic
    # admin_continue_game now starts background processing and returns immediately
    await admin_continue_game(game_id=game_id, language=language, force_resend=True)

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
    game_id: str = "default_game",
    language: str = "ru",
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

    asyncio.create_task(_notify_scheduler("reset"))

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
