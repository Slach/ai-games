"""
Game Master Agent - STRANDS-based AI for game orchestration
"""

import os
import logging
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

# Try to import strands, but provide fallback if not available
try:
    from strands import Agent
    from strands.models.openai import OpenAIModel
    STRANDS_AVAILABLE = True
except ImportError:
    STRANDS_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("strands-agents not available, using fallback")

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class GameStory(BaseModel):
    """Generated story for a game day"""
    day: int
    setting: str
    conflict: str
    narrative: str
    decision_points: List[Dict[str, Any]]


class NPCDialogue(BaseModel):
    """NPC reaction to game events"""
    npc_name: str
    npc_role: str
    dialogue: str
    emotion: str


class ContentPrompts(BaseModel):
    """Prompts for content generation"""
    image_prompt: str
    video_prompt: str
    scene_3d_prompt: str
    comic_prompt: str


# NPC role templates - these will be used to generate NPCs based on player's team
NPC_TEMPLATES = {
    "captain": {
        "role": "Captain",
        "personality": "Decisive, caring, responsible for crew safety. Makes tough calls under pressure.",
        "speech_style": "Direct, authoritative but supportive",
        "default_name": "Captain Eva Rodriguez",
    },
    "pilot": {
        "role": "Pilot",
        "personality": "Adventurous, skilled navigator, loves flying through dangerous zones. Quick reflexes and instinctive decisions.",
        "speech_style": "Energetic, uses flying metaphors, confident",
        "default_name": "Pilot Alex 'Ace' Turner",
    },
    "engineer": {
        "role": "Chief Engineer",
        "personality": "Brilliant, pragmatic, fascinated by alien technology. Loves solving technical puzzles.",
        "speech_style": "Technical but accessible, enthusiastic about discoveries",
        "default_name": "Chief Engineer Marcus Chen",
    },
    "communications": {
        "role": "Communications Officer",
        "personality": "Diplomatic, attentive listener, bridge between crew and outsiders. Skilled at de-escalation.",
        "speech_style": "Calm, measured, always considers tone and implication",
        "default_name": "Comm Officer Sarah Williams",
    },
    "scientist": {
        "role": "Science Officer",
        "personality": "Curious, methodical, driven to understand the unknown. Values knowledge above all.",
        "speech_style": "Analytical, thoughtful, asks probing questions",
        "default_name": "Dr. Aisha Patel",
    },
    "security": {
        "role": "Security Chief",
        "personality": "Vigilant, protective, skeptical of unknown threats. Prioritizes crew safety.",
        "speech_style": "Cautious, practical, focused on risks",
        "default_name": "Security Chief Jake Morrison",
    },
}


class GameMasterAgent:
    """
    Game Master agent that orchestrates game narrative, NPC interactions,
    and content generation using STRANDS SDK.
    """

    def __init__(self):
        # Use environment variables with defaults
        self.llm_base_url = os.getenv("LLAMA_CPP_URL", "http://llama.cpp:8090/v1")
        self.pixelle_mcp_url = os.getenv("PIXELLE_MCP_URL", "http://pixelle-mcp:9004/pixelle/mcp")

        self.agent: Optional[Agent] = None
        self.npcs: Dict[str, Dict[str, Any]] = {}

        # Initialize default NPCs
        self._init_default_npcs()

        logger.info(f"GameMasterAgent initialized with LLM: {self.llm_base_url}")
        logger.info(f"Pixelle-MCP URL: {self.pixelle_mcp_url}")

    def _init_default_npcs(self):
        """Initialize default NPCs with distinct personalities"""
        self.npcs = {
            "captain": NPC_TEMPLATES["captain"].copy(),
            "pilot": NPC_TEMPLATES["pilot"].copy(),
            "engineer": NPC_TEMPLATES["engineer"].copy(),
            "communications": NPC_TEMPLATES["communications"].copy(),
        }

    def generate_team_npcs(self, player_role: str) -> Dict[str, Dict[str, Any]]:
        """
        Generate NPC team based on player's role.
        If player is Engineer, other NPCs should complement that role.
        """
        team_npcs = {}

        # Always include captain
        team_npcs["captain"] = NPC_TEMPLATES["captain"].copy()

        # Add complementary roles based on player's position
        role_complements = {
            "Chief Engineer": ["pilot", "communications"],
            "XO (First Officer)": ["engineer", "security"],
            "Science Officer": ["engineer", "pilot"],
            "Security Chief": ["captain", "scientist"],
            "Pilot": ["engineer", "scientist"],
        }

        complementary = role_complements.get(player_role, ["engineer", "scientist"])
        for role_key in complementary:
            if role_key in NPC_TEMPLATES:
                team_npcs[role_key] = NPC_TEMPLATES[role_key].copy()

        return team_npcs

    async def initialize(self):
        """Initialize the agent with LLM connection"""
        if not STRANDS_AVAILABLE:
            logger.warning("STRANDS not available, agent will use fallback")
            return

        try:
            # Initialize OpenAI-compatible model (works with llama.cpp)
            model = OpenAIModel(
                api_base=self.llm_base_url,
                model="default",
                params={
                    "max_tokens": 2000,
                    "temperature": 0.7,
                    "repeat_penalty": 1.1,
                },
            )

            self.agent = Agent(model=model)
            logger.info("Game Master Agent initialized with STRANDS")
        except Exception as e:
            logger.error(f"Failed to initialize STRANDS agent: {e}")
            self.agent = None

    def _generate_story_fallback(self, day: int, previous_summary: str) -> GameStory:
        """Fallback story generation without LLM"""
        scenarios = [
            {
                "setting": "Deep space, near an uncharted nebula",
                "conflict": "Mysterious energy readings threaten ship systems",
                "narrative": f"Day {day}: The crew navigates through a strange nebula when sensors detect anomalous energy patterns. The ship's AI begins reporting unexpected behaviors, and crew members experience vivid dreams of alien landscapes.",
                "decision_points": [
                    {
                        "id": "a1",
                        "text": "Изучить артефакт с помощью сканеров",
                        "consequence": "Обнаружены скрытые паттерны в энергии",
                    },
                    {
                        "id": "a2",
                        "text": "Попробовать установить контакт",
                        "consequence": "Артефакт реагирует на сигнал",
                    },
                    {
                        "id": "a3",
                        "text": "Отойти на безопасное расстояние",
                        "consequence": "Артефакт остаётся неактивным",
                    },
                ],
            },
            {
                "setting": "Orbiting a newly discovered planet",
                "conflict": "Planet shows signs of ancient civilization",
                "narrative": f"Day {day}: A planet in the system reveals ruins of an advanced civilization. The team must decide whether to investigate the ruins or continue the primary mission. Strange symbols on the structures seem to respond to the crew's presence.",
                "decision_points": [
                    {
                        "id": "a1",
                        "text": "Отправить экспедицию на поверхность",
                        "consequence": "Найдены технологии, меняющие понимание истории",
                    },
                    {
                        "id": "a2",
                        "text": "Провести орбитальное сканирование",
                        "consequence": "Обнаружены подземные структуры",
                    },
                    {
                        "id": "a3",
                        "text": "Сообщить командованию и ждать указаний",
                        "consequence": "Получены новые протоколы исследования",
                    },
                ],
            },
            {
                "setting": "Space station emergency",
                "conflict": "Station life support failing, need immediate decision",
                "narrative": f"Day {day}: A nearby space station sends a distress signal - their life support is failing. The crew must decide how to help while managing their own resources. Complicating matters, the station's cargo manifests are classified.",
                "decision_points": [
                    {
                        "id": "a1",
                        "text": "Предоставить максимум ресурсов для спасения",
                        "consequence": "Экипаж спасён, но ресурсы корабля истощены",
                    },
                    {
                        "id": "a2",
                        "text": "Отправить только разведывательную группу",
                        "consequence": "Выяснены истинные причины бедствия",
                    },
                    {
                        "id": "a3",
                        "text": "Сообщить командованию и ждать приказов",
                        "consequence": "Получены противоречивые инструкции",
                    },
                ],
            },
        ]

        scenario = scenarios[(day - 1) % len(scenarios)]

        return GameStory(
            day=day,
            setting=scenario["setting"],
            conflict=scenario["conflict"],
            narrative=scenario["narrative"],
            decision_points=scenario["decision_points"],
        )

    def _generate_npc_dialogue_fallback(
        self, npc: Dict[str, Any], story: GameStory, player_role: str
    ) -> NPCDialogue:
        """Fallback NPC dialogue generation"""
        # Generate role-specific dialogue
        role = npc["role"]
        name = npc.get("name", npc.get("default_name", "Crew Member"))

        dialogues = {
            "Captain": f"{name}: \"Crew, we need to assess this situation carefully. {player_role}, what's your recommendation?\"",
            "Pilot": f"{name}: \"I can get us there fast, but that route is risky. Your call, boss.\"",
            "Chief Engineer": f"{name}: \"The systems are holding, but I'm seeing some unusual readings. This could be interesting... or problematic.\"",
            "Communications Officer": f"{name}: \"I'm picking up faint signals. Let me try to decode them before we make any moves.\"",
            "Science Officer": f"{name}: \"Fascinating! The data suggests patterns we've never observed before. We must investigate further.\"",
            "Security Chief": f"{name}: \"I'm not comfortable with this. We need to consider the risks before proceeding.\"",
        }

        return NPCDialogue(
            npc_name=name,
            npc_role=role,
            dialogue=dialogues.get(role, "The crew discusses the situation."),
            emotion="concerned",
        )

    def _generate_content_prompts_fallback(
        self, story: GameStory, player_role: str
    ) -> ContentPrompts:
        """Fallback content prompt generation"""
        return ContentPrompts(
            image_prompt=f"Sci-fi space scene: {story.setting}. Crew members in futuristic uniforms examining mysterious phenomenon. Cinematic lighting, detailed, 4K.",
            video_prompt=f"Dynamic space scene showing {story.conflict.lower()}. Ship systems flickering, crew responding to emergency. Dramatic camera movements.",
            scene_3d_prompt=f"3D model of a futuristic spaceship bridge with crew stations, holographic displays, and view of space with nebula in background.",
            comic_prompt=f"Comic strip panels: 1) Crew discovers anomaly, 2) NPCs react with concern, 3) Player character makes decision, 4) Consequences unfold. Space opera style.",
        )

    async def generate_daily_story(
        self, day: int, previous_summary: str = "", player_role: str = ""
    ) -> GameStory:
        """Generate daily story using LLM or fallback"""
        if self.agent:
            try:
                prompt = f"""
                Generate a daily episode for a cooperative space exploration game.

                Context:
                - Day: {day}
                - Previous summary: {previous_summary or "First day of mission"}
                - Player role: {player_role or "Crew member"}

                Create a compelling narrative with:
                1. A setting (space location, station, planet)
                2. A central conflict or mystery
                3. 3 decision points for players with visible actions and hidden consequences

                Return JSON with: setting, conflict, narrative, decision_points (array with id, text, consequence)
                """

                response = self.agent(prompt)
                # Parse response as JSON (simplified - in production, use proper JSON mode)
                return self._generate_story_fallback(day, previous_summary)

            except Exception as e:
                logger.error(f"LLM story generation failed: {e}")
                return self._generate_story_fallback(day, previous_summary)
        else:
            return self._generate_story_fallback(day, previous_summary)

    async def generate_npc_dialogues(
        self, story: GameStory, player_role: str
    ) -> List[NPCDialogue]:
        """Generate NPC dialogues for the day"""
        # Generate team based on player role
        team_npcs = self.generate_team_npcs(player_role)
        dialogues = []

        for npc_key, npc in team_npcs.items():
            if self.agent:
                try:
                    prompt = f"""
                    You are {npc.get('name', npc.get('default_name'))}, {npc['role']}.
                    Personality: {npc['personality']}
                    Speech style: {npc['speech_style']}

                    Game context: {story.narrative}
                    Player role: {player_role}

                    Generate a short reaction (1-2 sentences) in character.
                    """

                    response = self.agent(prompt)
                    dialogues.append(
                        NPCDialogue(
                            npc_name=npc.get("name", npc.get("default_name")),
                            npc_role=npc["role"],
                            dialogue=response or f"{npc['role']} considers the situation.",
                            emotion="neutral",
                        )
                    )
                except Exception as e:
                    logger.error(f"NPC dialogue generation failed for {npc_key}: {e}")
                    dialogues.append(self._generate_npc_dialogue_fallback(npc, story, player_role))
            else:
                dialogues.append(self._generate_npc_dialogue_fallback(npc, story, player_role))

        return dialogues

    async def generate_content_prompts(
        self, story: GameStory, dialogues: List[NPCDialogue], player_role: str
    ) -> ContentPrompts:
        """Generate prompts for content generation (image, video, comic)"""
        if self.agent:
            try:
                prompt = f"""
                Generate content prompts for a game day.

                Story: {story.narrative}
                Player role: {player_role}

                Return JSON with: image_prompt, video_prompt, scene_3d_prompt, comic_prompt
                """

                response = self.agent(prompt)
                return self._generate_content_prompts_fallback(story, player_role)
            except Exception as e:
                logger.error(f"Content prompt generation failed: {e}")
                return self._generate_content_prompts_fallback(story, player_role)
        else:
            return self._generate_content_prompts_fallback(story, player_role)

    async def generate_personalized_comic(
        self, story: GameStory, player_profile: Dict[str, Any]
    ) -> str:
        """Generate a personalized comic for the player"""
        # This would call Pixelle-MCP to generate the actual comic
        # For now, return a placeholder

        player_role = player_profile.get("role", "Crew Member")
        traits = player_profile.get("personality_traits", [])

        comic_prompt = f"""
        Generate a comic strip showing the player ({player_role}) in today's story.
        Player traits: {', '.join(traits)}
        Story: {story.narrative}

        Include 4-6 panels showing:
        1. Introduction to the day's situation
        2. NPC interaction
        3. Player making a decision
        4. Immediate consequences
        """

        # TODO: Call Pixelle-MCP API to generate comic
        # async with aiohttp.ClientSession() as session:
        #     async with session.post(f"{self.pixelle_mcp_url}/generate/comic", json={
        #         "prompt": comic_prompt,
        #         "workflow": "comic_generation"
        #     }) as resp:
        #         result = await resp.json()
        #         return result.get("image_url", "")

        return f"/content/comics/day_{story.day}_player_{player_profile.get('player_id', 'unknown')}.webp"

    async def process_player_message(
        self, player_id: int, message: str, player_profile: Dict[str, Any]
    ) -> str:
        """Process a player message and generate Game Master response"""
        if self.agent:
            try:
                player_role = player_profile.get("role", "Crew Member")
                prompt = f"""
                You are the Game Master of a space exploration game.
                Player (role: {player_role}) sent this message:

                "{message}"

                Respond in character as the Game Master, acknowledging their input
                and guiding the narrative forward. Keep it engaging and in the
                Star Trek universe tone.
                """

                response = self.agent(prompt)
                return response or "Game Master received your message."
            except Exception as e:
                logger.error(f"Message processing failed: {e}")
                return "Game Master received your message."
        else:
            return f"Game Master received: \"{message}\". Response will be generated soon."

    async def generate_default_action(
        self, story: GameStory, player_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate a default action when player doesn't choose"""
        # Choose based on player personality
        traits = player_profile.get("personality_traits", [])
        actions = story.decision_points

        # Simple personality-based selection
        if "логичный" in traits or "аналитический" in traits:
            return actions[0] if len(actions) > 0 else actions[0]
        elif "смелый" in traits or "решительный" in traits:
            return actions[1] if len(actions) > 1 else actions[0]
        else:
            return actions[2] if len(actions) > 2 else actions[0]


# Factory function
async def create_game_master_agent() -> GameMasterAgent:
    """Create and initialize Game Master agent"""
    agent = GameMasterAgent()
    await agent.initialize()
    return agent