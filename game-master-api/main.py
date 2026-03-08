"""
Game Master API - FastAPI service for AI Game Master
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

from game_master import GameMasterAgent, create_game_master_agent
from comic_generator import ComicGenerator, create_comic_generator
from database import (
    init_db,
    create_onboarding_session,
    get_onboarding_session,
    update_onboarding_session,
    create_player_profile,
    get_player_profile,
    create_game_day,
    get_game_day,
    save_player_action,
    add_game_message,
    get_game_messages,
    get_game_state,
    update_game_state,
    create_game,
    get_game,
    join_game,
    leave_game,
    get_available_games,
    get_db_connection,
    get_player_actions,
    get_players_in_game,
    update_player_profile_last_poll,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ============== Pydantic Models ==============

class OnboardingQuestion(BaseModel):
    """A single onboarding question"""
    id: int
    text: str
    options: List[Dict[str, str]]


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


class PollResponse(BaseModel):
    """Response from game polling endpoint"""
    new_game_day: Optional[Dict[str, Any]] = None
    pending_actions: List[Dict[str, Any]] = []
    messages_from_gm: List[Dict[str, Any]] = []
    npc_messages: List[Dict[str, Any]] = []
    avatar_url: Optional[str] = None


# ============== Onboarding Questions ==============

STATIC_ONBOARDING_QUESTIONS = [
    OnboardingQuestion(
        id=1,
        text="Корабль обнаружил неизвестный сигнал. Ваши действия?",
        options=[
            {"value": "cautious", "label": "Изучить сигнал с осторожностью, отправить разведывательный зонд"},
            {"value": "bold", "label": "Немедленно подойти ближе и попробовать установить контакт"},
        ]
    ),
    OnboardingQuestion(
        id=2,
        text="Важный член экипажа предлагает рискованный план. Что вы сделаете?",
        options=[
            {"value": "supportive", "label": "Поддержать коллегу и помочь в реализации"},
            {"value": "analytical", "label": "Тщательно проанализировать план на предмет слабых мест"},
        ]
    ),
    OnboardingQuestion(
        id=3,
        text="Экипаж столкнулся с моральной дилеммой. Как вы поступите?",
        options=[
            {"value": "empathetic", "label": "Выслушать всех и найти решение, которое учтёт чувства людей"},
            {"value": "logical", "label": "Принять решение на основе логики и пользы для миссии"},
        ]
    ),
    OnboardingQuestion(
        id=4,
        text="Ваша специализация на корабле?",
        options=[
            {"value": "technical", "label": "Технические системы, инженерия, технологии"},
            {"value": "diplomatic", "label": "Коммуникация, переговоры, координация"},
            {"value": "exploration", "label": "Исследование, наука, анализ"},
        ]
    ),
    OnboardingQuestion(
        id=5,
        text="Как вы предпочитаете решать конфликты?",
        options=[
            {"value": "collaborative", "label": "Обсуждение и поиск компромисса"},
            {"value": "decisive", "label": "Быстрое принятие решения и действие"},
        ]
    ),
]


def get_next_question(current_question: int) -> Optional[OnboardingQuestion]:
    """Get the next onboarding question"""
    if current_question >= len(STATIC_ONBOARDING_QUESTIONS):
        return None
    return STATIC_ONBOARDING_QUESTIONS[current_question]


async def generate_dynamic_onboarding_questions(language: str = "en") -> List[OnboardingQuestion]:
    """Generate 2-3 dynamic onboarding questions based on game setting"""
    try:
        game_master = await create_game_master_agent(language=language)
        
        prompt = """
Generate 2-3 onboarding questions for a space exploration game.
Questions should be about "what would you do in this situation" or "A or B preference".
Questions help determine player role and personality traits.

Return ONLY valid JSON with this structure:
[
    {
        "id": 1,
        "text": "question text",
        "options": [
            {"value": "option_value_1", "label": "Option 1 display text"},
            {"value": "option_value_2", "label": "Option 2 display text"}
        ]
    }
]
"""
        
        response = game_master.agent(prompt)
        response_str = str(response)
        
        # Try to parse JSON
        import json
        import re
        
        try:
            questions = json.loads(response_str)
        except json.JSONDecodeError:
            # Try to extract JSON block
            json_match = re.search(r'\[.*\]', response_str, re.DOTALL)
            if json_match:
                questions = json.loads(json_match.group())
            else:
                raise ValueError("Failed to parse JSON from LLM response")
        
        # Convert to OnboardingQuestion objects
        result = []
        for i, q in enumerate(questions, start=1):
            result.append(OnboardingQuestion(
                id=i,
                text=q.get("text", f"Question {i}"),
                options=q.get("options", [])
            ))
        
        return result if result else STATIC_ONBOARDING_QUESTIONS[:3]
        
    except Exception as e:
        logger.error(f"Failed to generate dynamic questions, using static: {e}")
        return STATIC_ONBOARDING_QUESTIONS[:3]


def generate_player_profile_from_answers(player_id: int, answers: Dict[int, str], game_id: str = "default_game") -> Dict[str, Any]:
    """Generate player profile based on onboarding answers"""
    role_mapping = {
        "technical": {
            "role": "Chief Engineer",
            "description": "Вы отвечаете за техническое состояние корабля. Ваша способность быстро находить решения в критических ситуациях спасает экипаж.",
            "avatar_description": "Техничный специалист в инженерном костюме, с инструментами и голографическими дисплеями вокруг",
            "traits": ["технический", "практичный", "решительный"],
        },
        "diplomatic": {
            "role": "XO (First Officer)",
            "description": "Вы координируете действия экипажа и ведёте переговоры с внешними контактами. Ваше умение находить общий язык решает исход кризисов.",
            "avatar_description": "Офицер связи в форменной униформе, с коммуникатором и уверенным взглядом",
            "traits": ["коммуникабельный", "стратегический", "эмпатичный"],
        },
        "exploration": {
            "role": "Science Officer",
            "description": "Вы исследуете неизвестное и анализируете данные. Ваша способность видеть закономерности открывает новые возможности.",
            "avatar_description": "Учёный в лабораторном халате, с сканером и научными приборами",
            "traits": ["аналитический", "любопытный", "методичный"],
        },
    }

    specialization = answers.get(4, "technical")
    profile_data = role_mapping.get(specialization, role_mapping["technical"])

    traits = profile_data["traits"].copy()

    if answers.get(1) == "cautious":
        traits.append("осторожный")
    elif answers.get(1) == "bold":
        traits.append("смелый")

    if answers.get(3) == "empathetic":
        traits.append("эмпатичный")
    elif answers.get(3) == "logical":
        traits.append("логичный")

    return {
        "player_id": player_id,
        "avatar_description": profile_data["avatar_description"],
        "role": profile_data["role"],
        "role_description": profile_data["description"],
        "personality_traits": traits,
        "game_id": game_id,
    }


# ============== FastAPI App ==============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    logger.info("Game Master API starting up")
    init_db()
    logger.info("Database initialized")
    yield
    logger.info("Game Master API shutting down")


app = FastAPI(
    title="AI Game Master API",
    description="API for AI-powered cooperative game with Telegram bot interface",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============== API Endpoints ==============

@app.get("/")
async def root():
    return {"service": "AI Game Master API", "status": "running"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# Onboarding endpoints
@app.get("/onboarding/questions")
async def get_onboarding_questions():
    """Get all static onboarding questions (backward compatibility)"""
    return {"questions": [q.model_dump() for q in STATIC_ONBOARDING_QUESTIONS]}


@app.get("/onboarding/questions/generate")
async def generate_static_onboarding_questions(game_id: str = "default_game"):
    """Generate 2-3 static onboarding questions based on game setting (fallback)"""
    game = get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    # Generate static questions based on game setting
    dynamic_questions = [
        OnboardingQuestion(
            id=101,
            text=f"In the {game['setting']}, you encounter an unknown signal. How do you respond?",
            options=[
                {"value": "cautious", "label": "Investigate cautiously with sensors"},
                {"value": "bold", "label": "Approach directly and attempt contact"},
            ]
        ),
        OnboardingQuestion(
            id=102,
            text=f"As a crew member in {game['name']}, what is your priority?",
            options=[
                {"value": "safety", "label": "Crew safety above all else"},
                {"value": "discovery", "label": "Knowledge and discovery"},
                {"value": "mission", "label": "Complete the mission objectives"},
            ]
        ),
        OnboardingQuestion(
            id=103,
            text=f"When faced with a moral dilemma in {game['setting']}, you would:",
            options=[
                {"value": "empathetic", "label": "Consider everyone's feelings"},
                {"value": "logical", "label": "Follow logic and rules"},
            ]
        ),
    ]

    return {"questions": [q.model_dump() for q in dynamic_questions]}


@app.post("/onboarding/start")
async def start_onboarding(player_id: int, game_id: str = "default_game", language: str = "en"):
    """Start a new onboarding session for a player"""
    # Check if player already has a profile
    existing_profile = get_player_profile(player_id)
    
    if existing_profile:
        raise HTTPException(status_code=400, detail="Player already has a profile")
    
    session = create_onboarding_session(player_id)
    
    # Get dynamic questions for the game
    dynamic_questions_result = await generate_dynamic_onboarding_questions(language=language)
    next_question = dynamic_questions_result["questions"][0] if dynamic_questions_result.get("questions") else None
    
    return {
        "session_id": session["session_id"],
        "game_id": game_id,
        "question": next_question.model_dump() if next_question else None,
    }


@app.post("/onboarding/{session_id}/answer")
async def submit_onboarding_answer(session_id: str, answer: OnboardingAnswer, language: str = "en"):
    """Submit an answer to an onboarding question"""
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    answers = session["answers"].copy()
    answers[answer.question_id] = answer.answer
    current_question = session["current_question"] + 1

    # Check if all questions answered (3 dynamic questions)
    completed = current_question >= 3
    
    update_onboarding_session(session_id, current_question, answers, completed)

    next_question = None
    if not completed:
        # Get next dynamic question
        game_id = session.get("game_id", "default_game")
        dynamic_questions_result = await generate_dynamic_onboarding_questions(language=language)
        remaining_questions = dynamic_questions_result["questions"][current_question:]
        next_question = remaining_questions[0] if remaining_questions else None

    result = {
        "completed": completed,
        "next_question": next_question.model_dump() if next_question else None,
    }

    if completed:
        # Generate profile from answers
        profile_data = generate_player_profile_from_answers(session["player_id"], answers)
        profile_data["game_id"] = session.get("game_id", "default_game")
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
    
    # Get player profile
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    # Generate avatar using comic_generator
    try:
        comic_generator = create_comic_generator()
        
        avatar_url = await comic_generator.generate_character_image(
            character_name=profile["role"],
            role=profile["role"],
            traits=profile["personality_traits"],
            scene_description=profile.get("avatar_description", "")
        )
        
        # Update profile with avatar URL
        if avatar_url:
            update_player_profile_avatar(player_id, avatar_url)
            profile["avatar_url"] = avatar_url
            
    except Exception as e:
        logger.error(f"Avatar generation failed: {e}")
        # Continue without avatar URL

    return {
        "status": "completed",
        "profile": profile,
        "avatar_url": profile.get("avatar_url")
    }


def update_player_profile_avatar(player_id: int, avatar_url: str) -> bool:
    """Update player profile with avatar URL"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE player_profiles SET avatar_url = ? WHERE player_id = ?""",
        (avatar_url, player_id)
    )

    conn.commit()
    conn.close()
    return True


@app.get("/onboarding/{session_id}")
async def get_onboarding_status(session_id: str, language: str = "en"):
    """Get onboarding session status"""
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    next_question = None
    if not session["completed"]:
        game_id = session.get("game_id", "default_game")
        dynamic_questions_result = await generate_dynamic_onboarding_questions(language=language)
        remaining_questions = dynamic_questions_result["questions"][session["current_question"]:]
        next_question = remaining_questions[0] if remaining_questions else None

    return {
        "session_id": session["session_id"],
        "game_id": session.get("game_id", "default_game"),
        "current_question": session["current_question"],
        "completed": session["completed"],
        "next_question": next_question.model_dump() if next_question else None,
    }


# Player profile endpoints
@app.get("/players/{player_id}/profile")
async def get_player_profile_endpoint(player_id: int):
    """Get player profile"""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found. Complete onboarding first.")
    
    # Return profile with avatar_url and game_id
    return {
        **profile,
        "avatar_url": profile.get("avatar_url"),
        "game_id": profile.get("game_id"),
    }


# Game state endpoints
@app.get("/game/state")
async def get_game_state_endpoint():
    """Get current game state"""
    return get_game_state()


@app.get("/game/day/{day_num}")
async def get_game_day_endpoint(day_num: int):
    """Get specific day's episode"""
    day = get_game_day(day_num)
    if not day:
        raise HTTPException(status_code=404, detail="Day not found")
    return day


@app.get("/game/current-day")
async def get_current_game_day():
    """Get current game day"""
    state = get_game_state()
    day = get_game_day(state["day"])
    if not day:
        raise HTTPException(status_code=404, detail="No game day generated yet")
    return day


@app.get("/game/poll/{player_id}")
async def poll_game_updates(player_id: int, since: Optional[str] = None):
    """Poll for new game updates (days, actions, messages) since last poll"""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    # Get last poll timestamp
    last_poll = since or profile.get("last_poll")
    
    updates = {
        "new_game_day": None,
        "pending_actions": [],
        "messages_from_gm": [],
        "npc_messages": []
    }

    try:
        # Check for current day with pending actions
        state = get_game_state()
        day = get_game_day(state["day"])
        
        if day and day.get("player_actions"):
            # Check if player has already selected action
            player_actions = get_player_actions(player_id, day["day"])
            if not player_actions:
                updates["pending_actions"] = day["player_actions"]
                updates["new_game_day"] = {
                    "day": day["day"],
                    "story": day["story"],
                    "npc_dialogues": day["npc_dialogues"]
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


# Player action endpoints
@app.post("/game/actions")
async def submit_player_action(
    player_id: int,
    day: int,
    action_id: str,
    choice: str
):
    """Submit player's action selection"""
    current_day = get_game_day(day)
    if not current_day:
        raise HTTPException(status_code=404, detail="No active game day")

    valid_actions = [a["id"] for a in current_day["player_actions"]]
    if action_id not in valid_actions:
        raise HTTPException(status_code=400, detail="Invalid action ID")

    result = save_player_action(player_id, day, action_id, choice)
    return {"status": "accepted", "action": result}


# Message endpoints
@app.post("/game/messages")
async def submit_game_message(player_id: int, message: str, message_type: str = "text"):
    """Submit a message to the game master and get response"""
    add_game_message(player_id, message, message_type)

    # Get player profile
    profile = get_player_profile(player_id)
    if not profile:
        profile_data = {
            "role": "Crew Member",
            "personality_traits": [],
            "player_id": player_id
        }
    else:
        profile_data = {
            "role": profile["role"],
            "personality_traits": profile["personality_traits"],
            "player_id": player_id
        }

    # Generate response from game master
    try:
        language = "ru" if any(c in message for c in 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя') else "en"
        game_master = await create_game_master_agent(language=language)

        response = await game_master.process_player_message(
            player_id=player_id,
            message=message,
            player_profile=profile_data
        )

        add_game_message(player_id, response, "text_response")

        return {
            "status": "processed",
            "response": response
        }
    except Exception as e:
        logger.error(f"Failed to generate game master response: {e}")
        return {
            "status": "received",
            "error": str(e)
        }


@app.get("/game/actions/{player_id}/{day}")
async def get_player_actions_endpoint(player_id: int, day: int):
    """Get player actions for a specific day"""
    actions = get_player_actions(player_id, day)
    return {"actions": actions}


@app.get("/players")
async def get_all_players():
    """Get all players in the current game"""
    # Get default game players
    game_id = "default_game"
    players = get_players_in_game(game_id)
    return [{"player_id": pid, "game_id": game_id} for pid in players]


@app.get("/game/messages/{player_id}")
async def get_game_messages_endpoint(player_id: int, limit: int = 10):
    """Get player's message history"""
    messages = get_game_messages(player_id, limit)
    return {"messages": messages}


# Admin endpoints
@app.post("/admin/generate-day")
async def generate_daily_episode(language: str = "en", previous_actions: list = None, team_assembly_status: dict = None):
    """Generate a new daily episode (called by game master scheduler)"""
    state = get_game_state()
    day_num = state["day"]

    logger.info(f"=== GENERATE DAY STARTED ===")
    logger.info(f"Day number: {day_num}")
    logger.info(f"Language: {language}")
    logger.info(f"Previous actions count: {len(previous_actions) if previous_actions else 0}")
    logger.info(f"Team assembly status: {team_assembly_status}")

    game_master = await create_game_master_agent(language=language)

    player_role = "Crew Member" if language != "ru" else "Член экипажа"
    logger.info(f"Player role: {player_role}")
    
    # Generate previous day summary from actions for story consistency
    previous_summary = ""
    if previous_actions:
        action_summaries = []
        for action in previous_actions:
            action_summaries.append(f"Day {action.get('day', 0)}: Player chose '{action.get('choice')}'")
        previous_summary = " | ".join(action_summaries)
    
    story = await game_master.generate_daily_story(
        day=day_num,
        previous_summary=previous_summary or state["last_updated"],
        player_role=player_role
    )

    logger.info(f"Generating NPC dialogues...")
    dialogues = await game_master.generate_npc_dialogues(
        story=story,
        player_role=player_role
    )

    new_day = {
        "day": day_num,
        "story": story.narrative,
        "npc_dialogues": [{"npc": d.npc_name, "dialogue": d.dialogue} for d in dialogues],
        "player_actions": story.decision_points,
        "generated_content": {
            "image": f"/content/day_{day_num}/scene.jpg",
            "comic": f"/content/day_{day_num}/comic.webp",
        },
        "previous_day_summary": previous_summary,
    }

    create_game_day(new_day)
    update_game_state(day_num + 1, "active")

    logger.info(f"=== GENERATE DAY COMPLETED ===")
    logger.info(f"Story: {story.narrative[:100]}...")
    logger.info(f"NPC dialogues: {len(dialogues)}")
    logger.info(f"Player actions: {len(story.decision_points)}")

    return new_day


@app.post("/admin/generate-comic/{player_id}")
async def generate_personalized_comic(player_id: int, day: Optional[int] = None):
    """Generate a personalized comic for a player"""
    profile = get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    state = get_game_state()
    game_day = day if day else state["day"]
    day_data = get_game_day(game_day)
    if not day_data:
        raise HTTPException(status_code=404, detail="Game day not found")

    comic_generator = create_comic_generator()
    comic_url = await comic_generator.generate_personalized_comic(
        day=game_day,
        story=day_data["story"],
        player_profile={
            "role": profile["role"],
            "personality_traits": profile["personality_traits"],
            "player_id": player_id,
        },
        npc_dialogues=day_data["npc_dialogues"],
    )

    return {
        "player_id": player_id,
        "day": game_day,
        "comic_url": comic_url,
        "role": profile["role"],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# Add aiohttp import at module level for avatar generation
import aiohttp
