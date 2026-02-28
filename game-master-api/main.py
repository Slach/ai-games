"""
Game Master API - FastAPI service for AI Game Master
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

from game_master import GameMasterAgent, create_game_master_agent
from comic_generator import ComicGenerator, create_comic_generator

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
    options: List[Dict[str, str]]  # [{"value": "a", "label": "..."}, ...]


class OnboardingAnswer(BaseModel):
    """Player's answer to an onboarding question"""
    question_id: int
    answer: str


class OnboardingSession(BaseModel):
    """Onboarding session state"""
    session_id: str
    player_id: Optional[int] = None
    current_question: int = 0
    answers: Dict[int, str] = {}
    completed: bool = False
    created_at: datetime = Field(default_factory=datetime.now)


class PlayerProfile(BaseModel):
    """Player profile after onboarding"""
    player_id: int
    avatar_description: str
    role: str
    role_description: str
    personality_traits: List[str]
    created_at: datetime = Field(default_factory=datetime.now)


class GameDay(BaseModel):
    """Daily game episode"""
    day: int
    story: str
    npc_dialogues: List[Dict[str, str]]
    player_actions: List[Dict[str, Any]]  # Visible actions + hidden consequences
    generated_content: Dict[str, str]  # Images, videos, etc.
    teaser: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class PlayerAction(BaseModel):
    """Player's action selection"""
    player_id: int
    day: int
    action_id: str
    choice: str


class GameMessage(BaseModel):
    """Message between player and game master"""
    player_id: int
    message: str
    message_type: str = "text"  # "text" or "voice"
    timestamp: datetime = Field(default_factory=datetime.now)


class GameState(BaseModel):
    """Current game state"""
    day: int
    status: str  # "active", "completed", "paused"
    last_updated: datetime = Field(default_factory=datetime.now)


# ============== In-Memory Storage (replace with DB later) ==============

class GameStorage:
    """Simple in-memory storage for development"""

    def __init__(self):
        self.onboarding_sessions: Dict[str, OnboardingSession] = {}
        self.player_profiles: Dict[int, PlayerProfile] = {}
        self.game_days: Dict[int, GameDay] = {}
        self.player_actions: List[PlayerAction] = []
        self.game_messages: List[GameMessage] = []
        self.game_state = GameState(day=1, status="active")

        # Onboarding questions - behavioral test
        self.onboarding_questions = [
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

    def get_onboarding_questions(self) -> List[OnboardingQuestion]:
        return self.onboarding_questions

    def create_onboarding_session(self, player_id: int) -> OnboardingSession:
        session_id = f"onboarding_{player_id}_{datetime.now().timestamp()}"
        session = OnboardingSession(session_id=session_id, player_id=player_id)
        self.onboarding_sessions[session_id] = session
        return session

    def get_onboarding_session(self, session_id: str) -> Optional[OnboardingSession]:
        return self.onboarding_sessions.get(session_id)

    def submit_onboarding_answer(self, session_id: str, answer: OnboardingAnswer) -> Optional[OnboardingSession]:
        session = self.onboarding_sessions.get(session_id)
        if not session:
            return None

        session.answers[answer.question_id] = answer.answer
        session.current_question += 1

        # Check if all questions answered
        if session.current_question >= len(self.onboarding_questions):
            session.completed = True
            # Generate player profile based on answers
            self._generate_player_profile(session)

        return session

    def get_next_onboarding_question(self, session_id: str) -> Optional[OnboardingQuestion]:
        session = self.onboarding_sessions.get(session_id)
        if not session or session.current_question >= len(self.onboarding_questions):
            return None
        return self.onboarding_questions[session.current_question]

    def _generate_player_profile(self, session: OnboardingSession) -> PlayerProfile:
        """Generate player profile based on onboarding answers"""
        answers = session.answers

        # Determine role based on specialization question
        specialization = answers.get(4, "technical")

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

        profile_data = role_mapping.get(specialization, role_mapping["technical"])

        # Add personality traits based on other answers
        traits = profile_data["traits"].copy()

        if answers.get(1) == "cautious":
            traits.append("осторожный")
        elif answers.get(1) == "bold":
            traits.append("смелый")

        if answers.get(3) == "empathetic":
            traits.append("эмпатичный")
        elif answers.get(3) == "logical":
            traits.append("логичный")

        profile = PlayerProfile(
            player_id=session.player_id,
            avatar_description=profile_data["avatar"],
            role=profile_data["role"],
            role_description=profile_data["description"],
            personality_traits=traits,
        )

        self.player_profiles[session.player_id] = profile
        return profile

    def get_player_profile(self, player_id: int) -> Optional[PlayerProfile]:
        return self.player_profiles.get(player_id)

    def get_current_game_day(self) -> Optional[GameDay]:
        day_num = self.game_state.day
        return self.game_days.get(day_num)

    def save_player_action(self, action: PlayerAction) -> bool:
        self.player_actions.append(action)
        return True

    def add_game_message(self, message: GameMessage) -> bool:
        self.game_messages.append(message)
        return True

    def get_game_messages(self, player_id: int, limit: int = 10) -> List[GameMessage]:
        player_messages = [m for m in self.game_messages if m.player_id == player_id]
        return player_messages[-limit:]


# Global storage instance
storage = GameStorage()


# ============== FastAPI App ==============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    logger.info("Game Master API starting up")
    # Initialize game master agent if needed
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
    return {"questions": storage.get_onboarding_questions()}


@app.post("/onboarding/start")
async def start_onboarding(player_id: int):
    """Start a new onboarding session for a player"""
    session = storage.create_onboarding_session(player_id)
    next_question = storage.get_next_onboarding_question(session.session_id)
    return {
        "session_id": session.session_id,
        "question": next_question.model_dump() if next_question else None,
    }


@app.post("/onboarding/{session_id}/answer")
async def submit_onboarding_answer(session_id: str, answer: OnboardingAnswer):
    """Submit an answer to an onboarding question"""
    session = storage.submit_onboarding_answer(session_id, answer)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    next_question = storage.get_next_onboarding_question(session_id)

    result = {
        "completed": session.completed,
        "next_question": next_question.model_dump() if next_question else None,
    }

    if session.completed:
        profile = storage.get_player_profile(session.player_id)
        result["profile"] = profile.model_dump() if profile else None

    return result


@app.get("/onboarding/{session_id}")
async def get_onboarding_status(session_id: str):
    """Get onboarding session status"""
    session = storage.get_onboarding_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    next_question = storage.get_next_onboarding_question(session_id)

    return {
        "session_id": session.session_id,
        "current_question": session.current_question,
        "completed": session.completed,
        "next_question": next_question.model_dump() if next_question else None,
    }


# Player profile endpoints
@app.get("/players/{player_id}/profile")
async def get_player_profile(player_id: int):
    """Get player profile"""
    profile = storage.get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found. Complete onboarding first.")
    return profile.model_dump()


# Game state endpoints
@app.get("/game/state")
async def get_game_state():
    """Get current game state"""
    return storage.game_state.model_dump()


@app.get("/game/day/{day_num}")
async def get_game_day(day_num: int):
    """Get specific day's episode"""
    day = storage.game_days.get(day_num)
    if not day:
        raise HTTPException(status_code=404, detail="Day not found")
    return day.model_dump()


@app.get("/game/current-day")
async def get_current_game_day():
    """Get current game day"""
    day = storage.get_current_game_day()
    if not day:
        raise HTTPException(status_code=404, detail="No game day generated yet")
    return day.model_dump()


# Player action endpoints
@app.post("/game/actions")
async def submit_player_action(action: PlayerAction):
    """Submit player's action selection"""
    # Validate action exists for this day
    current_day = storage.get_current_game_day()
    if not current_day:
        raise HTTPException(status_code=404, detail="No active game day")

    # Check if action_id is valid for this day
    valid_actions = [a["id"] for a in current_day.player_actions]
    if action.action_id not in valid_actions:
        raise HTTPException(status_code=400, detail="Invalid action ID")

    storage.save_player_action(action)
    return {"status": "accepted", "action": action.model_dump()}


# Message endpoints
@app.post("/game/messages")
async def submit_game_message(message: GameMessage):
    """Submit a message to the game master"""
    storage.add_game_message(message)
    # TODO: Generate response from game master
    return {"status": "received", "message": message.model_dump()}


@app.get("/game/messages/{player_id}")
async def get_game_messages(player_id: int, limit: int = 10):
    """Get player's message history"""
    messages = storage.get_game_messages(player_id, limit)
    return {"messages": [m.model_dump() for m in messages]}


# Admin endpoints (for game master internal use)
@app.post("/admin/generate-day")
async def generate_daily_episode(language: str = "en"):
    """Generate a new daily episode (called by game master scheduler)

    Args:
        language: Language for content generation ("en" or "ru")
    """
    day_num = storage.game_state.day

    logger.info(f"=== GENERATE DAY STARTED ===")
    logger.info(f"Day number: {day_num}")
    logger.info(f"Language: {language}")

    logger.info(f"Creating GameMasterAgent with language={language}")
    # Create and initialize GameMasterAgent with language
    game_master = await create_game_master_agent(language=language)

    # Get current day's story
    player_role = "Crew Member" if language != "ru" else "Член экипажа"
    logger.info(f"Player role: {player_role}")
    story = await game_master.generate_daily_story(
        day=day_num,
        previous_summary=storage.game_state.last_updated.isoformat(),
        player_role=player_role
    )

    # Generate NPC dialogues
    logger.info(f"Generating NPC dialogues...")
    dialogues = await game_master.generate_npc_dialogues(
        story=story,
        player_role=player_role
    )

    new_day = GameDay(
        day=day_num,
        story=story.narrative,
        npc_dialogues=[{"npc": d.npc_name, "dialogue": d.dialogue} for d in dialogues],
        player_actions=story.decision_points,
        generated_content={
            "image": f"/content/day_{day_num}/scene.jpg",
            "comic": f"/content/day_{day_num}/comic.webp",
        },
        created_at=datetime.now(),
    )

    storage.game_days[day_num] = new_day
    storage.game_state.day += 1
    storage.game_state.last_updated = datetime.now()

    logger.info(f"=== GENERATE DAY COMPLETED ===")
    logger.info(f"Story: {story.narrative[:100]}...")
    logger.info(f"NPC dialogues: {len(dialogues)}")
    logger.info(f"Player actions: {len(story.decision_points)}")

    return new_day.model_dump()


@app.post("/admin/generate-comic/{player_id}")
async def generate_personalized_comic(player_id: int, day: Optional[int] = None):
    """Generate a personalized comic for a player"""
    # Get player profile
    profile = storage.get_player_profile(player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Player profile not found")

    # Get game day
    game_day = day if day else storage.game_state.day
    day_data = storage.game_days.get(game_day)
    if not day_data:
        raise HTTPException(status_code=404, detail="Game day not found")

    # Generate comic
    comic_generator = create_comic_generator()
    comic_url = await comic_generator.generate_personalized_comic(
        day=game_day,
        story=day_data.story,
        player_profile={
            "role": profile.role,
            "personality_traits": profile.personality_traits,
            "player_id": player_id,
        },
        npc_dialogues=day_data.npc_dialogues,
    )

    return {
        "player_id": player_id,
        "day": game_day,
        "comic_url": comic_url,
        "role": profile.role,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)