"""
Game Master API - FastAPI service for AI Game Master
"""

import asyncio
import json
import logging
import os
import random
import string
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import uvicorn
from database import (
    GAME_START_MIN_PLAYERS,
    add_game_message,
    clear_game_started,
    create_game,
    create_game_day,
    create_mission,
    create_npc_profile,
    create_onboarding_session,
    create_player_profile,
    delete_all_game_days,
    delete_all_game_messages,
    delete_all_player_actions,
    delete_all_player_briefings,
    delete_game_day,
    delete_game_images,
    delete_mission,
    delete_player_actions_for_day,
    delete_player_briefings_for_day,
    end_game,
    get_all_active_npcs,
    get_all_briefings_for_day,
    get_all_roles,
    get_available_games,
    get_available_roles,
    get_db_connection,
    get_dead_players,
    get_game,
    get_game_day,
    get_game_image_count,
    get_game_messages,
    get_game_state,
    get_game_title,
    get_live_players,
    get_mission,
    get_npc_by_role,
    get_npc_profile,
    get_onboarding_session,
    get_player_actions,
    get_player_briefing,
    get_player_count_in_game,
    get_player_profile,
    get_players_in_game,
    get_players_who_need_to_choose,
    get_random_game_image,
    get_role_by_key,
    init_db,
    is_game_started,
    mark_player_dead,
    record_kick,
    reset_game_state_to_day1,
    reset_roles,
    save_game_image,
    save_player_action,
    save_player_briefing,
    start_game,
    take_role,
    update_briefing_choice,
    update_briefing_chosen_action_url,
    update_game_day_global_circumstances,
    update_game_day_outcome,
    update_game_state,
    update_game_title,
    update_mission_stage_progress,
    update_onboarding_session,
    update_player_profile_last_poll,
)
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from game_master import NPC_TEMPLATES, create_game_master_agent
from image_generator import (
    DEFAULT_LOADING_FALLBACK_URL,
    DEFAULT_SPLASH_FALLBACK_URL,
    create_image_generator,
)
from language import (
    get_gender_type_name,
    get_hybrid_species_name,
    get_species_type_name,
)
from prompts import (
    STATIC_ONBOARDING_QUESTIONS,
    OnboardingQuestion,
    build_interleaved_species_gender_questions,
)
from push_client import push_briefings, push_day_outcome, push_player_chosen_action
from pydantic import BaseModel, TypeAdapter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Track pending action image tasks keyed by (day, game_id) so that
# _analyze_day_outcome can await them before pushing the outcome.
# This ensures action images arrive BEFORE outcome text, not after.
_pending_action_tasks: dict[tuple[int, str], set[asyncio.Task]] = {}


def generate_game_id(length: int = 6) -> str:
    """Generate a unique alphanumeric game ID."""
    while True:
        game_id = "".join(
            random.choices(string.ascii_lowercase + string.digits, k=length)
        )
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
    day: int
    action_id: str
    choice: str


class PollResponse(BaseModel):
    """Response from game polling endpoint"""

    new_game_day: dict[str, Any] | None = None
    pending_actions: list[dict[str, Any]] = []
    messages_from_gm: list[dict[str, Any]] = []
    npc_messages: list[dict[str, Any]] = []
    avatar_url: str | None = None


class StartGameRequest(BaseModel):
    """Request to force-start a game"""

    game_id: str
    language: str = "ru"
    force: bool = True


class KickPlayerRequest(BaseModel):
    """Request to kick a player by role"""

    role_key: str
    reason: str = "Kicked by Game Master"
    game_id: str


class CreateGameRequest(BaseModel):
    """Request to create a new game."""

    name: str = "New Game"
    description: str = ""
    language: str = "ru"


def get_next_question(current_question: int) -> OnboardingQuestion | None:
    """Get the next onboarding question"""
    if current_question >= len(STATIC_ONBOARDING_QUESTIONS):
        return None
    return STATIC_ONBOARDING_QUESTIONS[current_question]


async def generate_dynamic_onboarding_questions(
    language: str = "en",
    game_id: str = "default_game",
) -> list[OnboardingQuestion]:
    """Generate dynamic onboarding questions using LLM with json_schema and enrich with images via ComfyUI."""
    logger.info(
        f"=== Generating dynamic onboarding questions for language: {language} ==="
    )
    start_time = datetime.now()
    questions = STATIC_ONBOARDING_QUESTIONS
    try:
        game_master = create_game_master_agent(language=language)
        logger.info("Game Master agent created successfully")

        raw_questions = game_master.generate_onboarding_questions()
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
                    logger.warning(
                        f"No image_prompt for question {q.id}, skipping image generation"
                    )
                    return None
                url = await image_generator.generate_image(
                    prompt=prompt,
                    filename_prefix=f"onboarding_q_{game_id}_{q.id}",
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
                    logger.warning(
                        f"Image generation failed for question {q.id}: {url_or_err}"
                    )

            img_time = (datetime.now() - image_start).total_seconds()
            success_count = sum(1 for u in image_urls if isinstance(u, str) and u)
            logger.info(
                f"Question images: {success_count}/{len(questions)} generated in {img_time:.2f}s"
            )
        except Exception as img_err:
            logger.warning(f"Question image generation failed entirely: {img_err}")
            # Continue without images - questions are still usable

        return questions

    except Exception as e:
        logger.error(f"Failed to generate dynamic questions, using static: {e}")
        return STATIC_ONBOARDING_QUESTIONS


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
    logger.info(
        f"[OPTION_IMAGES] Generating option images for question {question.id}: {question.text[:50]}..."
    )

    # Determine if this is a species or gender question
    has_species_tags = any(
        opt.get("species_tags") for opt in question.options if opt.get("species_tags")
    )

    tag_type = "species_tags" if has_species_tags else "gender_tags"

    # Build accumulated tags from all previous answers
    accumulated_tags: dict[str, int] = {}
    session_answers = session.get("answers", {})
    session_questions = session.get("questions", [])

    game_master = create_game_master_agent(language=language)

    # Count all tags from already-answered questions
    for qid_str, selected_value in session_answers.items():
        qid = int(qid_str) if not isinstance(qid_str, int) else qid_str
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
        filename_prefix = f"species_{game_id}_{session.get('player_id', 'x')}_{question.id}_{opt_value}"
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

    logger.info(
        f"[OPTION_IMAGES] Generating {len(tasks)} images in parallel via ComfyUI..."
    )
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
            logger.warning(
                f"[OPTION_IMAGES] Image failed for option {opt_value}: {url_or_err}"
            )

    logger.info(f"[OPTION_IMAGES] {success_count}/{len(tasks)} option images generated")
    return question


def generate_player_profile_from_answers(
    player_id: int,
    answers: dict[int, str],
    game_id: str = "default_game",
    language: str = "ru",
    questions: list[dict[str, Any]] | None = None,
    player_name: str = "",
) -> dict[str, Any]:
    """Assign a role from the available ship roles based on accumulated role scores from onboarding answers."""
    available = get_available_roles(game_id, language=language)

    if not available:
        raise ValueError("All crew positions are filled. No roles available.")

    game_master = create_game_master_agent(language=language)

    role_result = game_master.assign_role_from_answers(
        answers, available, questions=questions
    )

    assigned_key = role_result.get("role_key", "")

    role_data = get_role_by_key(assigned_key, language=language, game_id=game_id)
    if not role_data or role_data.get("taken_by") is not None:
        logger.warning(
            f"[ROLE] Suggested taken/invalid role '{assigned_key}', re-assigning from available"
        )
        available = get_available_roles(game_id, language=language)
        if not available:
            raise ValueError("All crew positions are filled while re-assigning.")
        role_result = game_master.assign_role_from_answers(
            answers, available, questions=questions
        )
        assigned_key = role_result.get("role_key", "")
        role_data = get_role_by_key(assigned_key, language=language, game_id=game_id)

    if not role_data:
        role_data = available[0]
        assigned_key = role_data["role_key"]

    taken = take_role(assigned_key, player_id, game_id)
    if not taken:
        logger.warning(
            f"[ROLE] Role {assigned_key} was taken between check and assignment, re-assigning from available"
        )
        available = get_available_roles(game_id, language=language)
        if not available:
            raise ValueError("All crew positions are filled.")
        # Use point-based assignment to pick the best remaining role, not just first
        role_result = game_master.assign_role_from_answers(
            answers, available, questions=questions
        )
        fallback_key = role_result.get("role_key", available[0]["role_key"])
        fallback_taken = take_role(fallback_key, player_id, game_id)
        if not fallback_taken:
            # Ultimate fallback: first available
            role_data = available[0]
            take_role(role_data["role_key"], player_id, game_id)
        else:
            role_data = get_role_by_key(
                fallback_key, language=language, game_id=game_id
            )

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

    logger.info(
        f"[ROLE] Player {player_id} assigned role: {role_data['role_name']} ({assigned_key}), "
        f"scores: {role_result.get('reasoning', '')}"
    )

    # Calculate species and gender from answers
    species_result = game_master.calculate_species_from_answers(
        answers, questions=questions
    )
    gender_result = game_master.calculate_gender_from_answers(
        answers, questions=questions
    )

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

    species_secondary_display = (
        get_species_type_name(species_secondary, language)
        if species_secondary
        else None
    )
    gender_secondary_display = (
        get_gender_type_name(gender_secondary, language) if gender_secondary else None
    )

    # Generate species+gender narrative description via LLM
    species_description = ""
    try:
        species_description = game_master.generate_species_gender_description(
            species_result=species_result,
            gender_result=gender_result,
            role=role_data["role_name"],
        )
        logger.info(
            f"[SPECIES] Description generated for player {player_id}: {species_description}..."
        )
    except Exception as e:
        logger.warning(
            f"[SPECIES] Failed to generate description for player {player_id}: {e}"
        )
        species_description = ""

    logger.info(
        f"[SPECIES] Player {player_id} species={species_primary}, gender={gender_primary}, "
        f"hybrid={species_hybrid}, display={species_display}"
    )

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
    }


# ============== FastAPI App ==============


async def _generate_loading_images():
    """Generate loading images in background at startup."""
    try:
        existing = get_game_image_count("loading")
        total_needed = 5
        if existing >= total_needed:
            logger.info(
                f"[LOADING] {existing} loading images already in DB, skipping gen"
            )
            return

        remaining = total_needed - existing
        logger.info(f"[LOADING] Generating {remaining} loading images (background)...")
        image_generator = create_image_generator()
        urls = await image_generator.generate_loading_images(
            count=remaining, start_index=existing, game_id="default_game"
        )

        saved = 0
        for url in urls:
            if url:
                save_game_image(type="loading", image_url=url)
                saved += 1

        logger.info(f"[LOADING] Background gen: saved {saved}/{remaining} images")
    except Exception as e:
        logger.error(f"[LOADING] Background generation failed: {e}")


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


app = FastAPI(
    title="AI Game Master API",
    description="API for AI-powered cooperative game with Telegram bot interface",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS middleware — allows browser-based clients (Telegram Mini App) to call the API.
# - GAME_MASTER_API_URL: internal Docker URL (for development / self-reference)
# - CORS_ORIGIN: external/public URL for browser frontend (Telegram Mini App)
# Only browsers enforce CORS; backend services (telegram-bot, game-master) don't need it.
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


@app.get("/onboarding/questions")
async def get_onboarding_questions():
    """Get all static onboarding questions (backward compatibility)"""
    return {"questions": [q.model_dump() for q in STATIC_ONBOARDING_QUESTIONS]}


@app.post("/onboarding/start")
async def start_onboarding(request: StartOnboardingRequest):
    """Start a new onboarding session for a player"""
    start_time = datetime.now()
    logger.info("=== START ONBOARDING ===")
    logger.info(
        f"player_id: {request.player_id}, game_id: {request.game_id}, language: {request.language}"
    )

    # Check if player already has a profile
    existing_profile = get_player_profile(request.player_id)

    if existing_profile:
        logger.warning(f"Player {request.player_id} already has a profile")
        raise HTTPException(status_code=400, detail="Player already has a profile")

    # Generate role questions (dynamic or static fallback) with images
    logger.info("Generating dynamic onboarding questions...")
    role_questions = await generate_dynamic_onboarding_questions(
        language=request.language,
        game_id=request.game_id,
    )
    logger.info(f"Generated {len(role_questions)} role questions")

    # Generate shuffle seed for deterministic question/option shuffling
    shuffle_seed = random.randint(0, 2**31 - 1)

    # If dynamic generation returned only role questions (5), append species/gender
    # If it fell back to STATIC_ONBOARDING_QUESTIONS (which already has them), skip
    if len(role_questions) <= 6:
        species_gender_questions = build_interleaved_species_gender_questions(
            language=request.language,
            shuffle_seed=shuffle_seed,
        )
        logger.info(
            f"Adding {len(species_gender_questions)} interleaved species/gender questions "
            f"(seed={shuffle_seed})"
        )
        dynamic_questions = role_questions + species_gender_questions
    else:
        dynamic_questions = role_questions

    for i, q in enumerate(dynamic_questions, start=1):
        q.id = i
    logger.info(f"Total onboarding questions: {len(dynamic_questions)}")

    game_title_data = {}
    try:
        gm = create_game_master_agent(language=request.language)
        game_title_data = gm.generate_game_title()

        # Save game title to database
        if game_title_data.get("title"):
            update_game_title(request.game_id, game_title_data["title"])
            logger.info(f"Game title saved to DB: {game_title_data['title']}")
    except Exception as e:
        logger.warning(f"Game title generation failed: {e}")
        game_title_data = {
            "title": "Звёздный Крейсер «Рассвет»: За горизонтом известного"
            if request.language == "ru"
            else "Star Cruiser «Dawn»: Beyond the Known Horizon",
            "welcome_text": "Кают-компания звёздного корабля мерцает голографическими дисплеями. Экипаж ждёт нового члена. Докажите, что вы достойны места среди звёзд."
            if request.language == "ru"
            else "The starship's mess hall glows with holographic displays. The crew awaits a new member. Prove you are worthy of a place among the stars.",
        }
        # Save fallback title to database
        update_game_title(request.game_id, game_title_data["title"])

    # Generate 3 splash images SYNCHRONOUSLY (blocks until done)
    existing_splash = get_game_image_count("splash", request.game_id)
    if existing_splash < 3:
        title_for_prompt = game_title_data.get("title", "")
        welcome_for_prompt = game_title_data.get("welcome_text", "")

        try:
            logger.info(
                f"[SPLASH] Generating 3 splash images for {title_for_prompt}..."
            )
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
                    save_game_image(
                        type="splash", image_url=url, game_id=request.game_id
                    )
                    saved += 1
            logger.info(f"[SPLASH] Saved {saved}/3 splash images")
        except Exception as e:
            logger.error(f"[SPLASH] Generation failed: {e}")
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
        logger.info(
            f"First question: id={next_question.id}, text={next_question.text}..."
        )

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
async def submit_onboarding_answer(
    session_id: str, answer: OnboardingAnswer, language: str = "en"
):
    """Submit an answer to an onboarding question"""
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Use the language from request or from session if already set
    effective_language = language if language != "en" else session.get("language", "en")
    answers_data = session.get("answers", {})
    game_id = (
        session.get("game_id")
        or answers_data.get(-1)
        or answers_data.get("-1")
        or "default_game"
    )

    answers = session["answers"].copy()
    answers[answer.question_id] = answer.answer
    current_question = session["current_question"] + 1

    session_questions = session.get("questions", [])

    question_adapter = TypeAdapter(list[OnboardingQuestion])
    dynamic_questions = (
        question_adapter.validate_python(session_questions) if session_questions else []
    )

    completed = current_question >= len(dynamic_questions)

    update_onboarding_session(
        session_id, current_question, answers, completed, effective_language
    )

    next_question = None
    if not completed:
        # Get next question from pre-generated list
        remaining_questions = dynamic_questions[current_question:]
        next_question = remaining_questions[0] if remaining_questions else None

        # Generate option images for species/gender questions
        if next_question:
            has_species_tags = any(
                opt.get("species_tags")
                for opt in next_question.options
                if opt.get("species_tags")
            )
            has_gender_tags = any(
                opt.get("gender_tags")
                for opt in next_question.options
                if opt.get("gender_tags")
            )
            if has_species_tags or has_gender_tags:
                # Update session answers in DB before generating images
                update_onboarding_session(
                    session_id, current_question, answers, completed, effective_language
                )
                try:
                    session["answers"] = answers
                    session["current_question"] = current_question
                    next_question = await _generate_option_images_for_question(
                        question=next_question,
                        session=session,
                        language=effective_language,
                        game_id=game_id,
                    )
                except Exception as img_err:
                    logger.warning(
                        f"[OPTION_IMAGES] Generation failed for question {next_question.id}: {img_err}"
                    )

    result = {
        "completed": completed,
        "next_question": next_question.model_dump() if next_question else None,
    }

    if completed:
        profile_answers = {
            k: v for k, v in answers.items() if str(k) not in ("-1", "-2")
        }
        player_name = answers.get(-2) or answers.get("-2", "")
        profile_data = generate_player_profile_from_answers(
            session["player_id"],
            profile_answers,
            game_id=game_id,
            language=effective_language,
            questions=session_questions,
            player_name=player_name,
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
    game_id = (
        session.get("game_id")
        or answers_data.get(-1)
        or answers_data.get("-1")
        or "default_game"
    )

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
        )
        logger.info(f"[AVATAR] LLM prompt for player {player_id}: {avatar_prompt}...")
    except Exception as e:
        logger.warning(
            f"[AVATAR] LLM prompt generation failed for player {player_id}: {e}"
        )

    # Step 2: Use LLM prompt or build fallback
    if not avatar_prompt:
        traits_str = ", ".join(profile.get("personality_traits", []))
        species_desc = profile.get("species_description", "")
        species_type = profile.get("species", "") or ""
        gender_type = profile.get("gender", "") or ""
        avatar_desc = profile.get("avatar_description", "")
        combined_desc = (
            f"{avatar_desc} {species_type} {gender_type} {species_desc}".lower()
        )

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
            "human": (
                f"Sci-fi character portrait of a {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"{avatar_desc} "
                f"Futuristic uniform, cinematic lighting, detailed face, 4K quality. "
                f"Portrait, upper body, space opera aesthetic."
            ),
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
        logger.info(
            f"[AVATAR] Using fallback prompt ({species_cat}) for player {player_id}: {avatar_prompt}..."
        )

    # Step 3: Call ComfyUI to generate the avatar
    try:
        image_generator = create_image_generator()
        logger.info(
            f"[AVATAR] Calling ComfyUI at {image_generator.comfyui_url} for avatar generation"
        )
        avatar_url = await image_generator.generate_avatar_image(
            prompt=avatar_prompt,
            filename_prefix=f"avatar_{game_id}_{player_id}",
        )

        if avatar_url:
            logger.info(f"[AVATAR] URL received for player {player_id}: {avatar_url}")
            update_player_profile_avatar(player_id, avatar_url)
            profile["avatar_url"] = avatar_url
        else:
            logger.warning(f"[AVATAR] ComfyUI returned None for player {player_id}")

    except Exception as e:
        logger.error(
            f"[AVATAR] ComfyUI generation failed for player {player_id}: {type(e).__name__}: {e}"
        )
        logger.error(traceback.format_exc())
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


async def _generate_player_avatar(
    player_id: int, game_id: str, language: str = "en"
) -> str | None:
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
        )
        logger.info(f"[AVATAR] LLM prompt for player {player_id}: {avatar_prompt}...")
    except Exception as e:
        logger.warning(
            f"[AVATAR] LLM prompt generation failed for player {player_id}: {e}"
        )

    # Step 2: Use LLM prompt or build fallback
    if not avatar_prompt:
        traits_str = ", ".join(profile.get("personality_traits", []))
        species_desc = profile.get("species_description", "")
        species_type = profile.get("species", "") or ""
        gender_type = profile.get("gender", "") or ""
        avatar_desc = profile.get("avatar_description", "")
        combined_desc = (
            f"{avatar_desc} {species_type} {gender_type} {species_desc}".lower()
        )

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
            "human": (
                f"Sci-fi character portrait of a {profile['role']} in Star Trek style. "
                f"Personality traits: {traits_str}. "
                f"{avatar_desc} "
                f"Futuristic uniform, cinematic lighting, detailed face, 4K quality. "
                f"Portrait, upper body, space opera aesthetic."
            ),
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
        logger.info(
            f"[AVATAR] Using fallback prompt ({species_cat}) for player {player_id}: {avatar_prompt}..."
        )

    # Step 3: Call ComfyUI to generate the avatar
    avatar_url = None
    try:
        image_generator = create_image_generator()
        logger.info(
            f"[AVATAR] Calling ComfyUI at {image_generator.comfyui_url} for avatar generation"
        )
        avatar_url = await image_generator.generate_avatar_image(
            prompt=avatar_prompt,
            filename_prefix=f"avatar_{game_id}_{player_id}",
        )

        if avatar_url:
            logger.info(f"[AVATAR] URL received for player {player_id}: {avatar_url}")
            update_player_profile_avatar(player_id, avatar_url)
        else:
            logger.warning(f"[AVATAR] ComfyUI returned None for player {player_id}")

    except Exception as e:
        logger.error(
            f"[AVATAR] ComfyUI generation failed for player {player_id}: {type(e).__name__}: {e}"
        )
        logger.error(traceback.format_exc())

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
    session_game_id = (
        session.get("game_id")
        or answers_data.get(-1)
        or answers_data.get("-1")
        or "default_game"
    )

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
        raise HTTPException(
            status_code=400, detail=f"Invalid player ID format: {str(e)}"
        ) from e


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
    return {"game_id": game_id, "started": started, "player_count": player_count}


@app.get("/game/day/{day_num}")
async def get_game_day_endpoint(day_num: int, game_id: str = "default_game"):
    """Get specific day's episode"""
    day = get_game_day(day_num, game_id=game_id)
    if not day:
        raise HTTPException(status_code=404, detail="Day not found")
    return day


@app.get("/game/current-day")
async def get_current_game_day(game_id: str = Query("default_game")):
    """Get current game day

    Game state tracks the NEXT day to generate, so the latest
    completed day is state["day"] - 1. For example:
    - Before any generation: state["day"] = 1, no days exist
    - After day 1 generation: state["day"] = 2, game_day[1] exists
    """
    state = get_game_state(game_id)
    current_day_num = max(1, state["day"] - 1)
    day = get_game_day(current_day_num, game_id=game_id)
    if not day:
        raise HTTPException(status_code=404, detail="No game day generated yet")
    return day


@app.get("/game/poll/{player_id}")
async def poll_game_updates(player_id: int, since: str | None = None):
    """Poll for new game updates (days, actions, messages) since last poll"""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    game_id = profile.get("game_id", "default_game")

    # Get last poll timestamp
    last_poll = since or profile.get("last_poll")

    updates = {
        "new_game_day": None,
        "pending_actions": [],
        "personal_briefing": None,
        "messages_from_gm": [],
        "npc_messages": [],
    }

    try:
        # Check for current day with pending actions
        # Game state tracks NEXT day to generate, so latest completed day is state["day"] - 1
        state = get_game_state(game_id)
        current_day_num = max(1, state["day"] - 1)

        # First, check player_briefings for per-player content
        briefing = get_player_briefing(current_day_num, player_id, game_id=game_id)

        if briefing and briefing.get("choices"):
            # Safety check: only return briefing if game_day record exists
            # (prevents race condition where briefings are saved before game_day)
            day_record = get_game_day(current_day_num, game_id=game_id)
            if day_record is None:
                logger.debug(
                    f"[POLL] Skipping briefing for player {player_id} day {current_day_num}: "
                    "game_day not yet created"
                )
            elif not briefing.get("selected_action_id"):
                # Player hasn't chosen yet — return their briefing
                # Get scene image for this day
                scene_url = get_random_game_image(
                    type="scene", day=current_day_num, game_id=game_id
                )
                # Also fetch NPC dialogues for crew behavior context
                day_record = get_game_day(current_day_num, game_id=game_id)
                crew_dialogues = day_record["crew_dialogues"] if day_record else []
                updates["personal_briefing"] = {
                    "briefing": briefing["briefing"],
                    "choices": briefing["choices"],
                    "chosen_action_url": briefing.get("chosen_action_url"),
                    "briefing_image_url": scene_url,
                    "crew_dialogues": crew_dialogues,
                }
                updates["pending_actions"] = briefing["choices"]
                updates["new_game_day"] = {
                    "day": current_day_num,
                    "briefing": briefing["briefing"],
                    "crew_dialogues": [],
                }
        else:
            # Fall back to legacy game_days player_actions
            day = get_game_day(current_day_num, game_id=game_id)
            if day and day.get("player_actions"):
                player_actions = get_player_actions(player_id, current_day_num)
                if not player_actions:
                    updates["pending_actions"] = day["player_actions"]
                    updates["new_game_day"] = {
                        "day": day["day"],
                        "story": day.get("global_circumstances") or day["story"],
                        "crew_dialogues": day["crew_dialogues"],
                    }

        # Get recent messages from Game Master
        messages = get_game_messages(player_id, limit=10)
        if last_poll:
            messages = [m for m in messages if m.get("timestamp", "") > last_poll]
        updates["messages_from_gm"] = messages

        # Update last poll timestamp
        update_player_profile_last_poll(player_id, datetime.now().isoformat())

    except Exception as e:
        logger.error(f"Poll failed for player {player_id}: {e}")

    return updates


# ============== Player action endpoints ==============


@app.post("/game/actions")
async def submit_player_action(request: PlayerActionRequest):
    """Submit player's action selection"""
    profile = get_player_profile(request.player_id)
    game_id = profile.get("game_id", "default_game") if profile else "default_game"

    # First check if player has a personal briefing (new system)
    briefing = get_player_briefing(request.day, request.player_id, game_id=game_id)

    if briefing and briefing.get("choices"):
        # New system: validate against briefing choices — does NOT require game_day
        # (game_day may not exist yet if briefings were saved before game_day record)
        valid_ids = [c["id"] for c in briefing["choices"]]
        if request.action_id not in valid_ids:
            raise HTTPException(
                status_code=400, detail=f"Invalid action ID. Valid: {valid_ids}"
            )

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
        # Legacy system: validate against game_days.player_actions
        current_day = get_game_day(request.day, game_id=game_id)
        if not current_day:
            raise HTTPException(status_code=404, detail="No active game day")
        valid_actions = [a["id"] for a in current_day.get("player_actions", [])]
        if request.action_id not in valid_actions:
            raise HTTPException(status_code=400, detail="Invalid action ID")

    # Also save to player_actions table for backward compatibility
    result = save_player_action(
        request.player_id, request.day, request.action_id, request.choice
    )

    # ── Generate comic panel for this player's action ────────────────
    # Generates a comic-style image showing the player's character
    # performing the chosen action, using their avatar as reference.
    # Registered in _pending_action_tasks so _analyze_day_outcome can
    # await completion before pushing the outcome.
    action_key = (request.day, game_id)
    action_task = asyncio.create_task(
        _generate_chosen_action_image(
            player_id=request.player_id,
            game_id=game_id,
            day=request.day,
            action_id=request.action_id,
        )
    )
    _pending_action_tasks.setdefault(action_key, set()).add(action_task)
    action_task.add_done_callback(
        lambda _t, k=action_key: _pending_action_tasks.get(k, set()).discard(_t)
    )

    # Check if all real players have now chosen — if so, trigger combined outcome analysis
    try:
        remaining = get_players_who_need_to_choose(request.day, game_id=game_id)
        if not remaining:
            # All players chose — analyze combined outcome
            logger.info(
                f"All players chose for day {request.day}, analyzing combined outcome"
            )
            asyncio.create_task(_analyze_day_outcome(request.day, game_id=game_id))
    except Exception as e:
        logger.warning(f"Combined outcome check failed: {e}")

    return {"status": "accepted", "action": result}


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
        language = (
            "ru"
            if any(c in message for c in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
            else "en"
        )
        game_master = create_game_master_agent(language=language)

        response = game_master.process_player_message(
            player_id=player_id, message=message, player_profile=profile_data
        )

        add_game_message(player_id, response, "text_response")

        return {"status": "processed", "response": response}
    except Exception as e:
        logger.error(f"Failed to generate game master response: {e}")
        return {"status": "received", "error": str(e)}


@app.get("/game/actions/{player_id}/{day}")
async def get_player_actions_endpoint(player_id: int, day: int):
    """Get player actions for a specific day"""
    actions = get_player_actions(player_id, day)
    return {"actions": actions}


@app.get("/game/briefing/{player_id}/{day}")
async def get_player_briefing_endpoint(player_id: int, day: int):
    """Get a player's personal briefing and choices for a specific day"""
    profile = get_player_profile(player_id)
    game_id = profile.get("game_id", "default_game") if profile else "default_game"
    briefing = get_player_briefing(day, player_id, game_id=game_id)
    if not briefing:
        raise HTTPException(status_code=404, detail="No briefing found")
    return {
        "briefing": briefing["briefing"],
        "choices": briefing["choices"],
        "selected_action_id": briefing.get("selected_action_id"),
        "day": briefing["day"],
        "chosen_action_url": briefing.get("chosen_action_url"),
    }


@app.get("/game/current-briefing/{player_id}")
async def get_current_briefing_endpoint(player_id: int):
    """Get a player's current day briefing"""
    profile = get_player_profile(player_id)
    game_id = profile.get("game_id", "default_game") if profile else "default_game"
    state = get_game_state(game_id)
    day = state["day"]
    briefing = get_player_briefing(day, player_id, game_id=game_id)
    if not briefing:
        raise HTTPException(status_code=404, detail="No briefing found for current day")
    return {
        "briefing": briefing["briefing"],
        "choices": briefing["choices"],
        "selected_action_id": briefing.get("selected_action_id"),
        "day": briefing["day"],
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
        logger.info(
            f"[LOADING] No generated loading images, using fallback: {DEFAULT_LOADING_FALLBACK_URL}"
        )
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
        logger.info(
            f"[SPLASH] No generated splash images, using fallback: {DEFAULT_SPLASH_FALLBACK_URL}"
        )
        return {
            "image_url": DEFAULT_SPLASH_FALLBACK_URL,
            "available": 0,
            "fallback": True,
        }
    return {"image_url": url, "available": get_game_image_count("splash", game_id)}


async def _generate_chosen_action_image(
    player_id: int,
    game_id: str,
    day: int,
    action_id: str,
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
        briefing = get_player_briefing(day, player_id, game_id=game_id)
        if not briefing:
            logger.warning(
                f"[ACTION_IMAGE] Briefing not found for {player_id} day {day}"
            )
            return

        # Find chosen action text
        action_text = ""
        for c in briefing.get("choices", []):
            if c.get("id") == action_id:
                action_text = c.get("text", c.get("description", ""))
                break
        if not action_text:
            action_text = action_id

        # Get scene context from game_day
        day_data = get_game_day(day, game_id=game_id)
        global_circ_str = (
            day_data.get("global_circumstances", "{}") if day_data else "{}"
        )
        try:
            global_circ = json.loads(global_circ_str)
        except (json.JSONDecodeError, TypeError):
            global_circ = {}
        setting = (
            global_circ.get("setting", "") or day_data.get("story", "")
            if day_data
            else ""
        )

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
            )
            logger.info(f"[ACTION_IMAGE] LLM prompt for {role}: {prompt[:120]}...")
        except Exception as llm_err:
            logger.warning(
                f"[ACTION_IMAGE] LLM prompt failed for {role}: {llm_err}, "
                f"using fallback prompt"
            )

        if not prompt:
            # Fallback: build prompt via concatenation
            prompt = (
                f"{role} performing action: {action_text}. "
                f"{character_description}. "
                f"Setting: {setting[:200]}. "
                f"Cinematic sci-fi scene, dynamic action in progress, "
                f"dramatic lighting, detailed environment, "
                f"space opera aesthetic, photorealistic quality, 4K."
            )

        # Get player's avatar URL for reference
        avatar_url = profile.get("avatar_url") or None

        image_gen = create_image_generator()
        chosen_action_url = await image_gen.generate_action_image_with_reference(
            prompt=prompt,
            reference_image_url=avatar_url,
            character_description=character_description,
            filename_prefix=f"action_day{day}_{game_id}_p{player_id}",
        )

        if chosen_action_url:
            # Save chosen action URL to the briefing
            if briefing.get("id"):
                update_briefing_chosen_action_url(briefing["id"], chosen_action_url)
                logger.info(
                    f"[ACTION_IMAGE] Saved for player {player_id} "
                    f"day {day}: {chosen_action_url}"
                )

            # Push the action image to the player via telegram-bot
            # (fire-and-forget to avoid blocking the generation loop)
            try:
                await push_player_chosen_action(
                    player_id=player_id,
                    day=day,
                    chosen_action_url=chosen_action_url,
                    game_id=game_id,
                    action_text=action_text,
                )
                logger.info(f"[ACTION_IMAGE] Pushed to player {player_id} day {day}")
            except Exception as push_err:
                logger.warning(
                    f"[ACTION_IMAGE] Failed to push to player {player_id}: {push_err}"
                )
        else:
            logger.warning(
                f"[ACTION_IMAGE] Generation returned None for player {player_id}"
            )
    except Exception as e:
        logger.error(f"[ACTION_IMAGE] Failed to generate: {e}")


def _build_day_summary(combined_outcome_str: str, language: str = "ru") -> str:
    """Build a compact text summary from combined_outcome JSON for cross-day context.

    The LLM receives this summary as 'previous events' when generating the next day.
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
    if ship_status:
        if language == "ru":
            parts.append(f"Состояние корабля: {ship_status}")
        else:
            parts.append(f"Ship status: {ship_status}")

    # Crew morale
    morale = oc.get("crew_morale_change", "")
    if morale:
        if language == "ru":
            parts.append(f"Мораль экипажа: {morale}")
        else:
            parts.append(f"Crew morale: {morale}")

    # Deaths
    dead = oc.get("dead_crew_members", [])
    if dead:
        dead_names = [
            f"{d[0]} ({d[1]})" if isinstance(d, list) and len(d) >= 2 else str(d)
            for d in dead
        ]
        if language == "ru":
            parts.append(f"Погибшие: {', '.join(dead_names)}")
        else:
            parts.append(f"Deceased: {', '.join(dead_names)}")

    # Ship destroyed
    if oc.get("ship_destroyed"):
        if language == "ru":
            parts.append("КОРАБЛЬ УНИЧТОЖЕН")
        else:
            parts.append("SHIP DESTROYED")

    # Next day hook
    hook = oc.get("next_day_hook", "")
    if hook:
        if language == "ru":
            parts.append(f"Зацепка для следующего хода: {hook}")
        else:
            parts.append(f"Next day hook: {hook}")

    return " | ".join(parts) if parts else narrative[:500]


def _build_cumulative_story_summary(
    current_day: int,
    language: str = "ru",
    game_id: str = "default_game",
) -> str:
    """Build a cumulative story summary from ALL previous days.

    Collects combined_outcome from every completed day (1 .. current_day - 1)
    and concatenates them chronologically. This gives the LLM a complete
    picture of the story so far, not just the last turn.

    Args:
        current_day: The upcoming day number (days before this are summarized)
        language: Language for labels ("ru" or "en")
        game_id: Game identifier

    Returns:
        A compact chronological summary string, or empty string if no prior days.
    """
    if current_day <= 1:
        return ""

    summaries = []
    if language == "ru":
        header = "=== ПРЕДЫДУЩИЕ ХОДЫ ==="
        day_label = "Ход"
    else:
        header = "=== PREVIOUS TURNS ==="
        day_label = "Turn"

    for d in range(1, current_day):
        day_record = get_game_day(d, game_id=game_id)
        if not day_record:
            continue

        combined_outcome = day_record.get("combined_outcome", "")
        day_summary = ""
        if combined_outcome:
            day_summary = _build_day_summary(combined_outcome, language=language)
        elif day_record.get("story"):
            day_summary = day_record["story"][:300]

        if day_summary:
            summaries.append(f"{day_label} {d}: {day_summary}")

    if not summaries:
        return ""

    result = header + "\n" + "\n".join(summaries)
    # Truncate to 3000 chars to avoid blowing up the LLM prompt
    if len(result) > 3000:
        result = result[:3000] + "..."

    return result


async def _analyze_day_outcome(
    day: int,
    language: str = "ru",
    game_id: str = "default_game",
):
    """Analyze all decisions for a day (player + NPC) to produce combined outcome.

    Called automatically when all players have submitted their choices,
    or can be triggered manually.
    """
    logger.info(f"[OUTCOME] Analyzing combined outcome for Day {day}")

    try:
        # Get all briefings for this day
        all_briefings = get_all_briefings_for_day(day, game_id)
        if not all_briefings:
            logger.warning(f"[OUTCOME] No briefings found for Day {day}")
            return

        # Get global circumstances
        game_day = get_game_day(day, game_id)
        global_circ_str = (
            game_day.get("global_circumstances", "{}") if game_day else "{}"
        )
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
            logger.warning(f"[OUTCOME] No decisions made yet for Day {day}")
            return

        # Also add NPC decisions from the combined outcome
        # NPC decisions were already analyzed during day generation

        # Build cumulative summary from ALL previous turns for full story context
        previous_summary = _build_cumulative_story_summary(
            current_day=day,
            language=language,
            game_id=game_id,
        )

        # Get mission context for progress tracking
        mission = get_mission(None, game_id)

        # Analyze with LLM
        gm = create_game_master_agent(language=language)
        outcome = gm.analyze_combined_outcome(
            global_circ,
            all_decisions,
            previous_summary,
            mission_context=mission,
        )

        # Save the combined outcome
        update_game_day_outcome(day, json.dumps(outcome, ensure_ascii=False), game_id)
        logger.info(f"[OUTCOME] Combined outcome saved for Day {day}")

        # Update mission progress if provided
        mission_progress = outcome.get("mission_progress", {})
        if mission_progress and mission:
            stage_progress = mission.get("stage_progress", {})
            current_stage = mission.get("current_stage", 0)
            total_stages = mission.get("total_stages", 1)

            for stage_str, points in mission_progress.items():
                stage_key = str(stage_str)
                try:
                    points_int = int(points)
                except (ValueError, TypeError):
                    logger.warning(f"[MISSION] Skipping non-integer points: {points}")
                    continue
                stage_progress[stage_key] = (
                    stage_progress.get(stage_key, 0) + points_int
                )

                # Try to extract a stage number from the key.
                # LLM may return keys like "Stage 1", "Stage_2", "investigation_stage", etc.
                stage_num = None
                try:
                    stage_num = int(stage_key)
                except (ValueError, TypeError):
                    # Extract digits from the key (e.g., "Stage 1" -> 1)
                    digits = "".join(c for c in stage_key if c.isdigit())
                    if digits:
                        stage_num = int(digits)
                    else:
                        # Map well-known stage name prefixes to numbers
                        stage_name_map = {
                            "investigation": 1,
                            "survival": 2,
                            "combat": 3,
                            "negotiation": 4,
                            "escape": 5,
                            "repair": 6,
                        }
                        for prefix, num in stage_name_map.items():
                            if stage_key.startswith(prefix):
                                stage_num = num
                                break

                if stage_num is None:
                    logger.info(
                        f"[MISSION] Skipping unparseable stage key: {stage_key}"
                    )
                    continue

                # Check if current stage is now completed
                if stage_num == current_stage:
                    for obj in mission.get("objectives", []):
                        if obj.get("stage") == stage_num:
                            threshold = obj.get("success_threshold", 5)
                            if stage_progress[stage_key] >= threshold:
                                current_stage = min(current_stage + 1, total_stages)
                                logger.info(f"[MISSION] Stage {stage_num} completed!")
                                break

            completed = current_stage >= total_stages
            update_mission_stage_progress(
                stage_progress,
                current_stage,
                game_id,
                completed,
            )

            if completed:
                logger.info("[MISSION] MISSION COMPLETE! Notifying players...")

        # Handle crew deaths
        dead_crew = outcome.get("dead_crew_members", [])
        for death_entry in dead_crew:
            # death_entry could be [name, role] or [player_id, role]
            if isinstance(death_entry, list) and len(death_entry) >= 2:
                entity_name = death_entry[0]
                entity_role = death_entry[1]
                # Try to find the player by looking up their entity name
                for d in all_decisions:
                    if d.get("name") == entity_name or d.get("role") == entity_role:
                        pid = d.get("player_id")
                        if pid:
                            mark_player_dead(pid, game_id)
                            logger.info(
                                f"[DEATH] Player {pid} ({entity_role}) marked as dead"
                            )
                        break

        # Handle ship destruction
        ship_destroyed = outcome.get("ship_destroyed", False)
        if ship_destroyed:
            end_game("ship_destroyed", game_id)
            logger.warning(f"[SHIP] Ship destroyed! Game over for {game_id}")

        # Also update game state
        state = get_game_state(game_id)
        ship_alive = not ship_destroyed and state.get("ship_alive", True)
        update_game_state(
            state["day"],
            "active" if ship_alive else "ship_destroyed",
            ship_alive=ship_alive,
            game_id=game_id,
        )

        # ── Push outcome to all alive players ──────────────────────
        # Build outcome text from the LLM result
        outcome_text = (
            outcome.get("outcome_narrative", "")
            or outcome.get("narrative", "")
            or outcome.get("summary", "")
            or outcome.get("outcome", "")
        )
        if not outcome_text:
            # Fallback: clean up JSON string for display
            raw = json.dumps(outcome, ensure_ascii=False)
            outcome_text = raw[:500] + ("..." if len(raw) > 500 else "")

        # Build death notices for the push payload
        death_notices = []
        for death_entry in dead_crew:
            if isinstance(death_entry, list) and len(death_entry) >= 2:
                death_notices.append(
                    {"name": str(death_entry[0]), "role": str(death_entry[1])}
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
            outcome_image_url = await image_gen.generate_scene_image(
                prompt=outcome_prompt,
                filename_prefix=f"outcome_day{day}_{game_id}",
            )
            if outcome_image_url:
                save_game_image(
                    type="outcome",
                    image_url=outcome_image_url,
                    game_id=game_id,
                    day=day,
                    prompt=outcome_prompt,
                )
                logger.info(
                    f"[OUTCOME] Outcome image generated for day {day}: {outcome_image_url}"
                )
            else:
                logger.warning(
                    f"[OUTCOME] Outcome image generation returned None for day {day}"
                )
        except Exception as img_err:
            logger.warning(
                f"[OUTCOME] Failed to generate outcome image for day {day}: {img_err}"
            )

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
        action_key = (day, game_id)
        pending = list(_pending_action_tasks.pop(action_key, set()))
        if pending:
            logger.info(
                f"[OUTCOME] Waiting for {len(pending)} action image(s) "
                f"before pushing outcome for day {day}"
            )
            results = await asyncio.gather(*pending, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.warning(f"[OUTCOME] Action image task {i} failed: {r}")

        # Push outcome synchronously so message order is deterministic
        # (outcome arrives BEFORE new day briefings)
        try:
            await push_day_outcome(
                game_id=game_id,
                day=day,
                outcome_text=outcome_text,
                alive_players=alive_players,
                outcome_image_url=outcome_image_url,
                ship_status="destroyed" if ship_destroyed else "alive",
                death_notices=death_notices,
                total_crew_count=total_crew,
                alive_crew_count=alive_crew,
            )
            logger.info(
                f"[OUTCOME] Outcome delivered for day {day} to {len(alive_players)} players"
            )
        except Exception as push_err:
            logger.error(
                f"[OUTCOME] Failed to deliver outcome for day {day}: {push_err}"
            )

    except Exception as e:
        logger.error(f"[OUTCOME] Analysis failed for Day {day}: {e}")
        import traceback

        logger.error(traceback.format_exc())


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
    }

    game = create_game(game_data)
    if not game:
        raise HTTPException(status_code=500, detail="Failed to create game")

    # Generate a title for the new game
    try:
        gm = create_game_master_agent(language=request.language)
        title_data = gm.generate_game_title()
        if title_data.get("title"):
            update_game_title(game_id, title_data["title"])
    except Exception as e:
        logger.warning(f"Title generation for new game {game_id} failed: {e}")

    return {
        "status": "success",
        "game_id": game_id,
        "name": get_game_title(game_id) or request.name,
        "message": f"Game {game_id} created successfully",
    }


def _build_player_briefings_for_push(
    all_briefings: list[dict],
    crew_dialogues: list[dict],
    day_num: int,
    game_id: str = "default_game",
) -> list[dict]:
    """Build per-player briefing dicts for push payload from stored briefings.

    Fetches scene image (if available) from game_images table for this day.
    Also fetches player_name for each real player to include in the payload.
    """
    # Fetch scene image for this day (if generated and saved)
    scene_url = get_random_game_image(type="scene", day=day_num, game_id=game_id)
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


@app.post("/admin/generate-day")
async def generate_daily_episode(
    language: str = "en",
    game_id: str = "default_game",
    previous_actions: list[dict[str, Any]] | None = None,
    previous_summary: str | None = None,
    team_assembly_status: dict[str, Any] | None = None,
):
    """Generate a new daily episode (called by game master scheduler)"""
    state = get_game_state(game_id)
    day_num = state["day"]

    logger.info("=== GENERATE DAY STARTED ===")
    logger.info(f"Day number: {day_num}")
    logger.info(f"Language: {language}")
    logger.info(
        f"Previous actions count: {len(previous_actions) if previous_actions else 0}"
    )

    game_master = create_game_master_agent(language=language)

    player_role = "Crew Member" if language != "ru" else "Член экипажа"
    logger.info(f"Player role: {player_role}")

    # Generate previous day summary from actions for story consistency
    summary = previous_summary or ""
    if not summary and previous_actions:
        action_summaries = []
        for action in previous_actions:
            action_summaries.append(
                f"Day {action.get('day', 0)}: Player chose '{action.get('choice')}'"
            )
        summary = " | ".join(action_summaries)

    story = game_master.generate_daily_story(
        day=day_num,
        previous_summary=summary or state["last_updated"],
        player_role=player_role,
    )

    logger.info("Generating NPC dialogues...")
    dialogues = game_master.generate_crew_dialogues(
        story=story, player_role=player_role
    )

    new_day = {
        "day": day_num,
        "story": story.narrative,
        "crew_dialogues": [
            {"npc": d.npc_name, "dialogue": d.dialogue} for d in dialogues
        ],
        "player_actions": story.decision_points,
        "generated_content": {
            "image": f"/content/day_{day_num}/scene.jpg",
            "comic": f"/content/day_{day_num}/comic.webp",
        },
        "previous_day_summary": summary,
    }

    create_game_day(new_day, game_id)
    update_game_state(day_num + 1, "active", game_id=game_id)

    logger.info("=== GENERATE DAY COMPLETED ===")
    logger.info(f"Story: {story.narrative}...")
    logger.info(f"NPC dialogues: {len(dialogues)}")
    logger.info(f"Player actions: {len(story.decision_points)}")

    return new_day


@app.post("/admin/generate-comic/{player_id}")
async def generate_chosen_action_image(
    player_id: int,
    day: int | None = None,
    game_id: str = "default_game",
):
    """Generate a chosen action image for a player (admin endpoint)."""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    state = get_game_state(game_id)
    game_day = day if day else state["day"]
    day_data = get_game_day(game_day, game_id)
    if not day_data:
        raise HTTPException(status_code=404, detail="Game day not found")

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
            action_text=day_data["story"][:200],
            setting=day_data["story"][:300],
            species_desc=profile.get("species_description", ""),
            species_type=profile.get("species", ""),
        )
    except Exception as e:
        logger.warning(f"[ADMIN] LLM prompt failed: {e}")

    if not prompt:
        prompt = (
            f"{role} performing a critical action during a space mission. "
            f"Story: {day_data['story'][:200]}. "
            f"Character traits: {', '.join(traits)}. "
            f"Dynamic composition, dramatic lighting, detailed environment. "
            f"Cinematic space opera aesthetic, photorealistic quality, 4K."
        )

    chosen_action_url = await image_generator.generate_scene_image(
        prompt=prompt,
        filename_prefix=f"action_day{game_day}_{game_id}_{role.replace(' ', '_')}",
    )

    # Store chosen_action_url in player's briefing for this day (if briefing exists)
    briefing = get_player_briefing(game_day, player_id, game_id=game_id)
    if briefing:
        update_briefing_chosen_action_url(briefing["id"], chosen_action_url)

    return {
        "player_id": player_id,
        "day": game_day,
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
        logger.error(f"[ADMIN] Loading image generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/admin/generate-splash-images")
async def admin_generate_splash_images(game_id: str = "default_game", lang: str = "ru"):
    """Generate 3 splash images for the game using current game title.

    If the game has no title yet, uses a fallback.
    """
    logger.info(f"[ADMIN] Generating splash images for game {game_id}")

    game_title = get_game_title(game_id) or (
        "Звёздный Крейсер «Рассвет»: За горизонтом известного"
        if lang == "ru"
        else "Star Cruiser «Dawn»: Beyond the Known Horizon"
    )
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
        logger.error(f"[ADMIN] Splash image generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/admin/start-game")
async def admin_start_game(request: StartGameRequest):
    """Force-start the game: generate NPCs for missing roles, mark game as started,
    generate the first (or next) game day with per-player briefings."""
    logger.info("=== ADMIN START GAME ===")
    logger.info(f"game_id={request.game_id}, language={request.language}")

    game_id = request.game_id
    language = request.language

    # 1. Get all players in the game
    player_ids = get_players_in_game(game_id)
    real_player_count = len(player_ids)
    logger.info(f"Real players in game: {real_player_count} — {player_ids}")

    if real_player_count == 0:
        raise HTTPException(
            status_code=400, detail="No players have joined the game yet"
        )

    # 2. Get available (unfilled) roles
    available_roles = get_available_roles(game_id, language=language)
    logger.info(
        f"Available (unfilled) roles: {[r['role_key'] for r in available_roles]}"
    )

    # 2.b Re-assign roles to existing players (important after restart reset_roles)
    for pid in player_ids:
        profile = get_player_profile(pid)
        if not profile:
            continue

        player_role = profile.get("role", "")
        player_role_en = profile.get("role_name_en", "")

        for role_data in available_roles:
            if (
                role_data["role_name"] == player_role
                or role_data["role_name_en"] == player_role
                or role_data["role_name_en"] == player_role_en
            ):
                taken = take_role(role_data["role_key"], pid, game_id)
                if taken:
                    logger.info(
                        f"[ROLE] Re-assigned role {role_data['role_key']} to player {pid}"
                    )
                break

    # Refresh available_roles (some may have been re-taken)
    available_roles = get_available_roles(game_id, language=language)
    logger.info(
        f"Available roles after re-assignment: {[r['role_key'] for r in available_roles]}"
    )

    # 3. Create NPCs for all unfilled roles
    npcs_created = []
    gm = create_game_master_agent(language=language)

    for role_data in available_roles:
        role_key = role_data["role_key"]
        role_name = role_data["role_name"]
        npc_key = f"npc_{role_key}_{game_id}"

        # Check if NPC already exists for this role
        existing = get_npc_by_role(role_key, game_id)
        if existing:
            npcs_created.append(existing)
            continue

        npc_data = {
            "npc_key": npc_key,
            "role_key": role_key,
            "npc_name": NPC_TEMPLATES.get(
                role_key.replace("chief_engineer", "engineer")
                .replace("science_officer", "scientist")
                .replace("communications_officer", "communications")
                .replace("security_chief", "security"),
                {},
            ).get("default_name", f"NPC {role_name}"),
            "role": role_name,
            "role_description": role_data.get("role_description", ""),
            "personality_traits": role_data.get("personality_traits", []),
            "species": "Various",
            "gender": "Various",
            "avatar_description": role_data.get("avatar_description", ""),
            "game_id": game_id,
            "is_active": True,
            "replaces_player_id": None,
        }
        npc = create_npc_profile(npc_data)
        if npc:
            npcs_created.append(npc)
            logger.info(f"[NPC] Created NPC {npc_key} for role {role_key}")

    # 4. Mark game as started
    start_game(game_id)

    # 5. Build combined roster (real players + NPCs)
    all_participants = []

    for pid in player_ids:
        profile = get_player_profile(pid)
        if profile:
            all_participants.append(
                {
                    "type": "player",
                    "player_id": pid,
                    "role": profile["role"],
                    "species": profile.get("species"),
                    "personality_traits": profile.get("personality_traits", []),
                    "role_description": profile.get("role_description", ""),
                }
            )

    for npc in npcs_created:
        all_participants.append(
            {
                "type": "npc",
                "npc_key": npc["npc_key"],
                "npc_name": npc.get("npc_name", npc.get("role", "NPC")),
                "role": npc["role"],
                "species": npc.get("species"),
                "personality_traits": npc.get("personality_traits", []),
                "role_description": npc.get("role_description", ""),
            }
        )

    logger.info(
        f"Total participants: {len(all_participants)} ({real_player_count} players + {len(npcs_created)} NPCs)"
    )

    # 6a. Generate NPC avatars (only for NPCs without an existing avatar)
    npc_roles_for_avatar = [
        {
            "role_key": npc.get("role_key", ""),
            "role_name": npc.get("role", npc.get("npc_name", "")),
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
                        filename_prefix=f"npc_{role_key}_{game_id}",
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
                        logger.info(
                            f"[NPC_AVATAR] Generated avatar for {role_key}: {url}"
                        )
        except Exception as e:
            logger.warning(f"[NPC_AVATAR] Batch generation failed: {e}")

    # 6b. Generate mission
    mission_data = gm.generate_mission(all_participants)
    mission_result = create_mission(mission_data, game_id)
    if mission_result:
        logger.info(
            f"[MISSION] Mission created: {mission_result.get('name', '')} ({mission_result.get('total_stages', 0)} stages)"
        )
    else:
        logger.error("[MISSION] Failed to create mission")
        mission_result = {}

    # 6c. Generate bridge image
    try:
        bridge_result = gm.generate_bridge_image_prompt(
            mission_data or {}, all_participants
        )
        bridge_prompt = bridge_result.get("bridge_prompt", "")
        if bridge_prompt:
            image_gen = create_image_generator()
            bridge_url = await image_gen.generate_scene_image(
                prompt=bridge_prompt,
                filename_prefix=f"bridge_{game_id}",
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

    # 7. Generate the game day with the new restructured flow
    state = get_game_state(game_id)
    day_num = state["day"]

    # Build cumulative summary from ALL previous turns, not just the last one
    previous_summary = _build_cumulative_story_summary(
        current_day=day_num,
        language=language,
        game_id=game_id,
    )

    # Step A: Generate global circumstances (with mission context for story consistency)
    global_circ = gm.generate_global_circumstances(
        day=day_num,
        previous_summary=previous_summary,
        player_profiles=all_participants,
        mission_context=mission_data,
    )
    global_narrative = global_circ.get("narrative", "")

    # Save global circumstances
    update_game_day_global_circumstances(
        day_num,
        json.dumps(global_circ, ensure_ascii=False),
        game_id,
    )

    # Step A2: Generate scene image for this turn's briefing
    scene_url = None
    try:
        scene_prompt = (
            f"Sci-fi scene: {global_circ.get('setting', '')}. "
            f"{global_narrative[:500]} "
            f"Cinematic starship interior, crew interacting with holographic displays, "
            f"dramatic lighting from the main viewscreen, Star Trek aesthetic, 4K quality."
        )
        image_gen = create_image_generator()
        scene_url = await image_gen.generate_scene_image(
            prompt=scene_prompt,
            filename_prefix=f"scene_day{day_num}_{game_id}",
        )
        if scene_url:
            save_game_image(
                type="scene",
                image_url=scene_url,
                game_id=game_id,
                day=day_num,
                prompt=scene_prompt,
            )
            logger.info(
                f"[SCENE] Turn scene image saved for day {day_num}: {scene_url}"
            )
    except Exception as e:
        logger.warning(
            f"[SCENE] Failed to generate turn scene image for day {day_num}: {e}"
        )

    # Create game day record EARLY to prevent race condition with polling loop.
    # Poll needs the game_day record to exist before briefings are visible,
    # otherwise the player sees a briefing but cannot submit an action (404).
    # The existing Step E will REPLACE this placeholder via INSERT OR REPLACE.
    early_day = {
        "day": day_num,
        "story": global_narrative,
        "global_circumstances": json.dumps(global_circ, ensure_ascii=False),
        "crew_dialogues": [],
        "player_actions": [],
        "generated_content": {
            "image": f"/content/day_{day_num}/scene.jpg",
        },
        "previous_day_summary": previous_summary,
    }
    create_game_day(early_day, game_id)
    logger.info(f"[DAY] Early game day record created for day {day_num}")

    # Step B: Generate per-player briefings and choices IN PARALLEL
    llm_parallel = int(os.getenv("LLM_PARALLEL", "2"))
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
                )
            except Exception as e:
                logger.error(
                    f"[BRIEFING] Failed to generate briefing for {participant.get('role', '?')}: {e}"
                )
                return None

            briefing = briefing_data.get("briefing", "")
            choices = briefing_data.get("choices", [])
            personal_title = briefing_data.get("personal_title", "")

            if participant["type"] == "npc":
                # NPCs decide immediately without seeing consequences
                npc_profile = get_npc_profile(participant["npc_key"]) or participant
                try:
                    npc_decision = await asyncio.to_thread(
                        gm.generate_npc_choice, choices, npc_profile
                    )
                except Exception as e:
                    logger.error(
                        f"[NPC] Failed to generate choice for {participant.get('npc_key', '?')}: {e}"
                    )
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
                        "day": day_num,
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
                        "day": day_num,
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

    logger.info(
        f"[BRIEFING] Generated {len(all_briefings)}/{len(all_participants)} briefings"
    )

    # ── Generate per-player character images ────────────────────────
    # Each player gets a character-in-scene image showing their avatar
    # in the current setting. Used as the personal briefing image.
    logger.info(
        f"[CHAR_IMAGE] Generating {len([b for b in all_briefings if not b.get('is_npc')])} "
        "per-player character images..."
    )
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
            filename_prefix=f"char_day{day_num}_{game_id}_p{pid}",
        )
        if url:
            save_game_image(
                type="character",
                image_url=url,
                game_id=game_id,
                day=day_num,
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
                logger.info(
                    f"[CHAR_IMAGE] Generated for player {b.get('player_id')}: "
                    f"title='{personal_title[:60]}', url={url_or_err[:80]}"
                )
            elif isinstance(url_or_err, Exception):
                logger.warning(
                    f"[CHAR_IMAGE] Failed for player {b.get('player_id')}: {url_or_err}"
                )

    # Step C: Analyze NPC choices (real players haven't chosen yet)
    npc_decisions = [b for b in all_briefings if b.get("is_npc")]
    if npc_decisions:
        outcome = gm.analyze_combined_outcome(
            global_circ, npc_decisions, previous_summary
        )
        # Save the partial outcome (will be updated when real players choose)
        update_game_day_outcome(
            day_num,
            json.dumps(outcome, ensure_ascii=False),
            game_id,
        )

    # Step D: Build NPC dialogues from global circumstances
    player_role = all_participants[0]["role"] if all_participants else "Crew Member"
    from game_master import GameStory

    dialog_story = GameStory(
        day=day_num,
        setting=global_circ.get("setting", ""),
        conflict=global_circ.get("conflict", ""),
        narrative=global_narrative,
        decision_points=[],
    )
    try:
        dialogues = gm.generate_crew_dialogues(
            story=dialog_story, player_role=player_role
        )
        crew_dialogues_list = [
            {"npc": d.npc_name, "dialogue": d.dialogue} for d in dialogues
        ]
    except Exception as e:
        logger.warning(f"NPC dialogue generation failed: {e}")
        crew_dialogues_list = []

    # Step E: Create the game day record
    new_day = {
        "day": day_num,
        "story": global_narrative,
        "global_circumstances": json.dumps(global_circ, ensure_ascii=False),
        "crew_dialogues": crew_dialogues_list,
        "player_actions": all_briefings[0].get("choices", []) if all_briefings else [],
        "generated_content": {
            "image": f"/content/day_{day_num}/scene.jpg",
        },
        "previous_day_summary": previous_summary,
    }
    create_game_day(new_day, game_id)

    # Advance game state to next day
    update_game_state(day_num + 1, "active", game_id=game_id)

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
    logger.info(
        f"Day: {day_num}, Participants: {len(all_participants)}, NPCs: {len(npcs_created)}"
    )

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
        player_briefings = _build_player_briefings_for_push(
            all_briefings, crew_dialogues_list, day_num, game_id=game_id
        )
        if player_briefings:
            asyncio.create_task(
                push_briefings(
                    game_id=game_id,
                    day=day_num,
                    players_briefings=player_briefings,
                    bridge_url=bridge_url,
                    mission=mission_info,
                    crew_dialogues=crew_dialogues_list,
                    is_first_turn=True,
                    global_narrative=global_narrative,
                )
            )
    except Exception as push_err:
        logger.warning(f"[PUSH] Failed to initiate push: {push_err}")

    return {
        "status": "success",
        "day": day_num,
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
    day: int | None = Query(None),
):
    """Get the bridge image for a game.

    Args:
        game_id: Game identifier
        day: If set, returns the scene image for that day instead of bridge image.
    """
    img_type = "scene" if day is not None else "bridge"
    url = get_random_game_image(type=img_type, game_id=game_id, day=day)
    if not url:
        raise HTTPException(status_code=404, detail=f"No {img_type} image found")
    return {"image_url": url, "game_id": game_id, "type": img_type}


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

    # 1. Find who currently holds this role
    role_data = get_role_by_key(role_key, language="ru", game_id=game_id)
    if not role_data:
        raise HTTPException(status_code=404, detail=f"Role '{role_key}' not found")

    taken_by = role_data.get("taken_by")
    if not taken_by:
        raise HTTPException(
            status_code=400, detail=f"Role '{role_key}' is not taken by any player"
        )

    kicked_player_id = taken_by

    # 2. Load NPC templates for this role
    npc_template = NPC_TEMPLATES.get(
        role_key.replace("chief_engineer", "engineer")
        .replace("science_officer", "scientist")
        .replace("communications_officer", "communications")
        .replace("security_chief", "security"),
        {},
    )
    npc_name = npc_template.get("default_name", f"NPC {role_data['role_name']}")

    # 3. Release the role and create NPC replacement
    reset_roles(game_id)
    # Re-take all other real player roles except the kicked one
    all_players = get_players_in_game(game_id)
    for pid in all_players:
        profile = get_player_profile(pid)
        if profile and pid != kicked_player_id:
            # Re-assign their role
            all_roles = get_all_roles(game_id)
            for r in all_roles:
                if r.get("taken_by") == pid:
                    pass  # Should be restored by the re-take
    # Simpler: just release the kicked player's role
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ship_roles SET taken_by = NULL WHERE role_key = ? AND game_id = ?",
        (role_key, game_id),
    )
    conn.commit()
    conn.close()

    # Create NPC profile
    npc_profile_data = {
        "npc_key": f"npc_{role_key}_{game_id}",
        "role_key": role_key,
        "npc_name": npc_name,
        "role": role_data["role_name"],
        "role_description": role_data.get("role_description", ""),
        "personality_traits": role_data.get("personality_traits", []),
        "species": "Various",
        "gender": "Various",
        "avatar_description": role_data.get("avatar_description", ""),
        "game_id": game_id,
        "is_active": True,
        "replaces_player_id": kicked_player_id,
    }
    npc = create_npc_profile(npc_profile_data)
    if not npc:
        raise HTTPException(status_code=500, detail="Failed to create NPC replacement")

    # Record the kick
    record_kick(kicked_player_id, npc["npc_key"], request.reason)

    # 4. Send notification to the kicked player (via game_messages)
    kick_notification = (
        f"⛔ **Вы были изгнаны с корабля!**\n\n"
        f"Game Master принял решение заменить вас NPC.\n"
        f"**Причина:** {request.reason}\n\n"
        f"Ваш персонаж заменён на {npc_name}.\n"
        f"Спасибо за игру!"
    )
    add_game_message(kicked_player_id, kick_notification, "kick_notification")

    # Also clean up player profile for the kicked player
    conn = get_db_connection()
    cursor = conn.cursor()
    # Remove from game but keep the profile data
    cursor.execute(
        "UPDATE player_profiles SET game_id = NULL WHERE player_id = ?",
        (kicked_player_id,),
    )
    conn.commit()
    conn.close()

    logger.info("=== ADMIN KICK PLAYER COMPLETED ===")
    logger.info(f"Kicked player {kicked_player_id}, replaced with NPC {npc_name}")

    return {
        "status": "success",
        "kicked_player_id": kicked_player_id,
        "role_key": role_key,
        "role_name": role_data["role_name"],
        "npc_key": npc["npc_key"],
        "npc_name": npc_name,
        "reason": request.reason,
    }


@app.get("/admin/list-games")
async def admin_list_games():
    """List all active games with player counts."""
    games = get_available_games()
    result = []
    for game in games:
        game_id = game["game_id"]
        result.append(
            {
                "game_id": game_id,
                "name": get_game_title(game_id) or game.get("name", ""),
                "description": game.get("description", ""),
                "player_count": get_player_count_in_game(game_id),
                "status": game.get("status", "active"),
                "started": is_game_started(game_id),
            }
        )
    return {"games": result}


@app.post("/admin/analyze-day")
async def admin_analyze_day(
    day: int | None = None,
    language: str = "ru",
    game_id: str = "default_game",
):
    """Manually trigger combined outcome analysis for a specific day.

    If day is not specified, uses the current day (day - 1 since game state is pre-advanced).
    """
    if day is None:
        state = get_game_state(game_id)
        day = max(
            1, state["day"] - 1
        )  # Game state is pre-advanced, so current completed day is day-1

    logger.info(f"[ADMIN] Manual outcome analysis for Day {day}")
    await _analyze_day_outcome(day, language=language, game_id=game_id)

    game_day = get_game_day(day, game_id)
    outcome_str = game_day.get("combined_outcome", "{}") if game_day else "{}"
    try:
        outcome = json.loads(outcome_str) if outcome_str else {}
    except (json.JSONDecodeError, TypeError):
        outcome = {}

    return {
        "status": "success",
        "day": day,
        "combined_outcome": outcome,
    }


@app.post("/admin/continue-game")
async def admin_continue_game(
    game_id: str = "default_game",
    language: str = "ru",
    force_resend: bool = False,
):
    """Generate the next turn (day) in the game.

    Generates a new game day with global circumstances, per-player briefings,
    and NPC dialogues. Advances game state by one day.

    This is essentially /admin/start-game but without the NPC/mission
    setup — it continues an already-started game.
    """
    logger.info("=== ADMIN CONTINUE GAME ===")
    logger.info(f"game_id={game_id}, language={language}")

    state = get_game_state(game_id)
    day_num = state["day"]

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
            all_participants.append(
                {
                    "type": "player",
                    "player_id": pid,
                    "role": profile["role"],
                    "species": profile.get("species"),
                    "personality_traits": profile.get("personality_traits", []),
                    "role_description": profile.get("role_description", ""),
                }
            )

    for npc in npcs:
        all_participants.append(
            {
                "type": "npc",
                "npc_key": npc["npc_key"],
                "npc_name": npc.get("npc_name", npc.get("role", "NPC")),
                "role": npc["role"],
                "species": npc.get("species"),
                "personality_traits": npc.get("personality_traits", []),
                "role_description": npc.get("role_description", ""),
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
        current_day=day_num,
        language=language,
        game_id=game_id,
    )

    gm = create_game_master_agent(language=language)

    # Fetch mission data for story consistency
    mission_data = get_mission(None, game_id) or {}

    # Step A: Generate global circumstances (with mission context for story consistency)
    global_circ = gm.generate_global_circumstances(
        day=day_num,
        previous_summary=previous_summary,
        player_profiles=all_participants,
        mission_context=mission_data,
    )

    # Save global circumstances
    update_game_day_global_circumstances(
        day_num,
        json.dumps(global_circ, ensure_ascii=False),
        game_id,
    )

    # Step A2: Generate scene image for this turn's briefing
    try:
        scene_prompt = (
            f"Sci-fi scene: {global_circ.get('setting', '')}. "
            f"{global_circ.get('narrative', '')[:500]} "
            f"Cinematic starship interior, crew interacting with holographic displays, "
            f"dramatic lighting from the main viewscreen, Star Trek aesthetic, 4K quality."
        )
        image_gen = create_image_generator()
        scene_url = await image_gen.generate_scene_image(
            prompt=scene_prompt,
            filename_prefix=f"scene_day{day_num}_{game_id}",
        )
        if scene_url:
            save_game_image(
                type="scene",
                image_url=scene_url,
                game_id=game_id,
                day=day_num,
                prompt=scene_prompt,
            )
            logger.info(
                f"[SCENE] Turn scene image saved for day {day_num}: {scene_url}"
            )
    except Exception as e:
        logger.warning(
            f"[SCENE] Failed to generate turn scene image for day {day_num}: {e}"
        )

    # Create game day record EARLY to prevent race condition with polling loop.
    # The existing Step E will REPLACE this placeholder via INSERT OR REPLACE.
    early_day = {
        "day": day_num,
        "story": global_circ.get("narrative", ""),
        "global_circumstances": json.dumps(global_circ, ensure_ascii=False),
        "crew_dialogues": [],
        "player_actions": [],
        "generated_content": {
            "image": f"/content/day_{day_num}/scene.jpg",
        },
        "previous_day_summary": previous_summary,
    }
    create_game_day(early_day, game_id)
    logger.info(f"[DAY] Early game day record created for day {day_num}")

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
        briefing_data = gm.generate_player_briefing_and_choices(
            global_circ, gm_profile, player_name
        )
        briefing = briefing_data.get("briefing", "")
        choices = briefing_data.get("choices", [])
        personal_title = briefing_data.get("personal_title", "")

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
                    "day": day_num,
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
                    "day": day_num,
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
    logger.info(
        f"[CHAR_IMAGE] Generating {len([b for b in all_briefings if not b.get('is_npc')])} "
        "per-player character images..."
    )
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
            filename_prefix=f"char_day{day_num}_{game_id}_p{pid}",
        )
        if url:
            save_game_image(
                type="character",
                image_url=url,
                game_id=game_id,
                day=day_num,
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
                logger.info(
                    f"[CHAR_IMAGE] Generated for player {b.get('player_id')}: "
                    f"title='{personal_title[:60]}', url={url_or_err[:80]}"
                )
            elif isinstance(url_or_err, Exception):
                logger.warning(
                    f"[CHAR_IMAGE] Failed for player {b.get('player_id')}: {url_or_err}"
                )

    # Step C: Analyze NPC choices
    npc_decisions = [b for b in all_briefings if b.get("is_npc")]
    if npc_decisions:
        outcome = gm.analyze_combined_outcome(
            global_circ, npc_decisions, previous_summary
        )
        update_game_day_outcome(
            day_num, json.dumps(outcome, ensure_ascii=False), game_id
        )

    # Step D: NPC dialogues
    player_role = all_participants[0]["role"] if all_participants else "Crew Member"
    from game_master import GameStory

    dialog_story = GameStory(
        day=day_num,
        setting=global_circ.get("setting", ""),
        conflict=global_circ.get("conflict", ""),
        narrative=global_circ.get("narrative", ""),
        decision_points=[],
    )
    try:
        dialogues = gm.generate_crew_dialogues(
            story=dialog_story, player_role=player_role
        )
        crew_dialogues_list = [
            {"npc": d.npc_name, "dialogue": d.dialogue} for d in dialogues
        ]
    except Exception as e:
        logger.warning(f"NPC dialogue generation failed: {e}")
        crew_dialogues_list = []

    # Step E: Create game day record
    new_day = {
        "day": day_num,
        "story": global_circ.get("narrative", ""),
        "global_circumstances": json.dumps(global_circ, ensure_ascii=False),
        "crew_dialogues": crew_dialogues_list,
        "player_actions": all_briefings[0].get("choices", []) if all_briefings else [],
        "generated_content": {
            "image": f"/content/day_{day_num}/scene.jpg",
        },
        "previous_day_summary": previous_summary,
    }
    create_game_day(new_day, game_id)

    # Advance game state
    update_game_state(day_num + 1, "active", game_id=game_id)

    # ── Push previous day outcome (if applicable) ──────────────
    # Must run BEFORE pushing new day briefings so player sees:
    #   Итоги хода N-1 → Вводная хода N → Ход N + действия
    if day_num > 1:
        await _analyze_day_outcome(
            day=day_num - 1,
            language=language,
            game_id=game_id,
        )

    # ── Push briefings to telegram-bot ─────────────────────────
    try:
        # Build the global intro narrative from global circumstances
        global_narrative = global_circ.get("narrative", "")

        player_briefings = _build_player_briefings_for_push(
            all_briefings, crew_dialogues_list, day_num, game_id=game_id
        )
        if player_briefings:
            asyncio.create_task(
                push_briefings(
                    game_id=game_id,
                    day=day_num,
                    players_briefings=player_briefings,
                    crew_dialogues=crew_dialogues_list,
                    is_first_turn=False,
                    force_resend=force_resend,
                    global_narrative=global_narrative,
                )
            )
    except Exception as push_err:
        logger.warning(f"[PUSH] Failed to initiate push: {push_err}")

    logger.info("=== ADMIN CONTINUE GAME COMPLETED ===")
    logger.info(f"Day {day_num} generated with {len(all_participants)} participants")

    return {
        "status": "success",
        "day": day_num,
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

    Deletes the current day's data (briefings, actions, day record),
    rolls back game state by one day, then regenerates the day.
    """
    logger.info("=== ADMIN REGENERATE TURN ===")
    logger.info(f"game_id={game_id}, language={language}")

    state = get_game_state(game_id)
    current_day = state["day"]
    regenerate_day = max(1, current_day - 1)

    logger.info(f"Regenerating Day {regenerate_day} (current state day={current_day})")

    # Delete current day's data
    deleted_briefings = delete_player_briefings_for_day(regenerate_day, game_id)
    deleted_actions = delete_player_actions_for_day(regenerate_day, game_id)
    deleted_day = delete_game_day(regenerate_day, game_id)

    logger.info(
        f"Deleted: {deleted_briefings} briefings, {deleted_actions} player actions, "
        f"day_record={deleted_day}"
    )

    # Roll back game state to before the deleted day
    reset_game_state_to_day1(game_id)
    # Restore to the correct day (the day being regenerated)
    update_game_state(regenerate_day, "active", game_id=game_id)

    # Now regenerate the day using the continue-game logic
    return await admin_continue_game(
        game_id=game_id, language=language, force_resend=True
    )


@app.post("/admin/restart-game")
async def admin_restart_game(
    game_id: str = "default_game",
    language: str = "ru",
):
    """Reset game state and restart from the first turn.

    Deletes all game days, briefings, actions, messages, mission,
    and game images. Resets game state to day 1, marks game as
    not-started, and keeps player profiles intact.
    """
    logger.info("=== ADMIN RESTART GAME ===")
    logger.info(f"game_id={game_id}, language={language}")

    # Delete all game content
    deleted_days = delete_all_game_days(game_id)
    deleted_briefings = delete_all_player_briefings(game_id)
    deleted_actions = delete_all_player_actions(game_id)
    deleted_messages = delete_all_game_messages(game_id)
    deleted_mission = delete_mission(game_id)
    deleted_images = delete_game_images(game_id)

    logger.info(
        f"Deleted: {deleted_days} days, {deleted_briefings} briefings, "
        f"{deleted_actions} actions, {deleted_messages} messages, "
        f"mission={deleted_mission}, {deleted_images} images"
    )

    # Reset game state to day 1
    reset_game_state_to_day1(game_id)

    # Mark game as not started
    clear_game_started(game_id)

    # Reset ship roles (make all available again)
    reset_roles(game_id)

    logger.info("=== ADMIN RESTART GAME COMPLETED ===")

    return {
        "status": "success",
        "game_id": game_id,
        "deleted_days": deleted_days,
        "deleted_briefings": deleted_briefings,
        "deleted_actions": deleted_actions,
        "deleted_messages": deleted_messages,
        "deleted_mission": deleted_mission,
        "deleted_images": deleted_images,
        "message": f"Game {game_id} has been reset to day 1. All content cleared.",
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
