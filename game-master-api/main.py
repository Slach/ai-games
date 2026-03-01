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


# ============== Onboarding Questions ==============

ONBOARDING_QUESTIONS = [
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
    if current_question >= len(ONBOARDING_QUESTIONS):
        return None
    return ONBOARDING_QUESTIONS[current_question]


def generate_player_profile_from_answers(player_id: int, answers: Dict[int, str]) -> Dict[str, Any]:
    """Generate player profile based on onboarding answers"""
    role_mapping = {
        "technical": {
            "role": "Chief Engineer",
            "description": "Вы отвечаете за техническое состояние корабля. Ваша способность быстро находить решения в критических ситуациях спасает экипаж.",
            "avatar": "Техничный специалист в инженерном костюме, с инструментами и голографическими дисплеями вокруг",
            "traits": ["технический", "практичный", "решительный"],
        },
        "diplomatic": {
            "role": "XO (First Officer)",
            "description": "Вы координируете действия экипажа и ведёте переговоры с внешними контактами. Ваше умение находить общий язык решает исход кризисов.",
            "avatar": "Офицер связи в форменной униформе, с коммуникатором и уверенным взглядом",
            "traits": ["коммуникабельный", "стратегический", "эмпатичный"],
        },
        "exploration": {
            "role": "Science Officer",
            "description": "Вы исследуете неизвестное и анализируете данные. Ваша способность видеть закономерности открывает новые возможности.",
            "avatar": "Учёный в лабораторном халате, с сканером и научными приборами",
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
        "avatar_description": profile_data["avatar"],
        "role": profile_data["role"],
        "role_description": profile_data["description"],
        "personality_traits": traits,
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
    """Get all onboarding questions"""
    return {"questions": [q.model_dump() for q in ONBOARDING_QUESTIONS]}


@app.post("/onboarding/start")
async def start_onboarding(player_id: int):
    """Start a new onboarding session for a player"""
    session = create_onboarding_session(player_id)
    next_question = get_next_question(0)
    return {
        "session_id": session["session_id"],
        "question": next_question.model_dump() if next_question else None,
    }


@app.post("/onboarding/{session_id}/answer")
async def submit_onboarding_answer(session_id: str, answer: OnboardingAnswer):
    """Submit an answer to an onboarding question"""
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    answers = session["answers"].copy()
    answers[answer.question_id] = answer.answer
    current_question = session["current_question"] + 1

    completed = current_question >= len(ONBOARDING_QUESTIONS)
    if completed:
        profile_data = generate_player_profile_from_answers(session["player_id"], answers)
        create_player_profile(profile_data)

    update_onboarding_session(session_id, current_question, answers, completed)

    next_question = get_next_question(current_question) if not completed else None

    result = {
        "completed": completed,
        "next_question": next_question.model_dump() if next_question else None,
    }

    if completed:
        result["profile"] = profile_data

    return result


@app.get("/onboarding/{session_id}")
async def get_onboarding_status(session_id: str):
    """Get onboarding session status"""
    session = get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    next_question = get_next_question(session["current_question"]) if not session["completed"] else None

    return {
        "session_id": session["session_id"],
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
    return profile


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


@app.get("/game/messages/{player_id}")
async def get_game_messages_endpoint(player_id: int, limit: int = 10):
    """Get player's message history"""
    messages = get_game_messages(player_id, limit)
    return {"messages": messages}


# Admin endpoints
@app.post("/admin/generate-day")
async def generate_daily_episode(language: str = "en"):
    """Generate a new daily episode (called by game master scheduler)"""
    state = get_game_state()
    day_num = state["day"]

    logger.info(f"=== GENERATE DAY STARTED ===")
    logger.info(f"Day number: {day_num}")
    logger.info(f"Language: {language}")

    game_master = await create_game_master_agent(language=language)

    player_role = "Crew Member" if language != "ru" else "Член экипажа"
    logger.info(f"Player role: {player_role}")
    story = await game_master.generate_daily_story(
        day=day_num,
        previous_summary=state["last_updated"],
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