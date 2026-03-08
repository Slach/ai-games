"""
Game Master Agent - STRANDS-based AI for game orchestration
"""

import os
import logging
import json
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

# Try to import strands
try:
    from strands import Agent
    from strands.models.openai import OpenAIModel
    STRANDS_AVAILABLE = True
except ImportError:
    STRANDS_AVAILABLE = False

from pydantic import BaseModel

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


# NPC role templates
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

    def __init__(self, language: str = "en"):
        self.llm_base_url = os.getenv("LLM_URL", "http://llama.cpp:8090/v1")
        self.pixelle_mcp_url = os.getenv("PIXELLE_MCP_URL", "http://pixelle-mcp:9004/pixelle/mcp")
        self.language = language

        self.agent: Optional[Agent] = None
        self.npcs: Dict[str, Dict[str, Any]] = {}

        self._init_default_npcs()

        logger.info(f"GameMasterAgent initialized with LLM: {self.llm_base_url}, language: {language}")
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
        """Generate NPC team based on player's role"""
        team_npcs = {"captain": NPC_TEMPLATES["captain"].copy()}

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
            logger.error("STRANDS not available - LLM generation will not work")
            return

        try:
            # For llama.cpp compatibility, we need to use the correct OpenAI-compatible config
            # The api_key is required by the OpenAI client but can be any value for local llama.cpp
            # Note: STRANDS SDK requires api_key and base_url to be passed via client_args,
            # not as direct model configuration parameters (see strands/models/openai.py)
            model = OpenAIModel(
                model_id=os.getenv("LLM_MODEL", "unsloth/Qwen3.5-27B"),
                client_args={
                    "api_key": os.getenv("LLM_API_KEY", "placeholder-key-for-llama-cpp"),
                    "base_url": self.llm_base_url,
                },
                params={
                    "max_tokens": 2000,
                    "temperature": 0.7,
                },
            )
            self.agent = Agent(model=model)
            logger.info("Game Master Agent initialized with STRANDS")
        except Exception as e:
            logger.error(f"Failed to initialize STRANDS agent: {e}")
            self.agent = None

    def _parse_json_from_response(self, response: str) -> Optional[Dict]:
        """Try to extract JSON from LLM response"""
        try:
            # Try direct JSON parse first
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try to find JSON block
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        return None

    async def generate_daily_story(
        self, day: int, previous_summary: str = "", player_role: str = ""
    ) -> GameStory:
        """Generate daily story using LLM"""
        logger.info(f"[STORY] Starting story generation for Day {day}, language: {self.language}")

        if self.language == "ru":
            lang_directive = "IMPORTANT: Respond entirely in RUSSIAN. All narrative, actions, and consequences must be in Russian language."
            player_role_display = player_role or "Член экипажа"
        else:
            lang_directive = "IMPORTANT: Respond entirely in ENGLISH. All narrative, actions, and consequences must be in English language."
            player_role_display = player_role or "Crew member"

        if not self.agent:
            logger.error("[STORY] LLM agent not available - cannot generate story")
            raise RuntimeError("LLM agent not available - cannot generate story")

        logger.info(f"[STORY] Sending prompt to LLM")
        try:
            prompt = f"""
Generate a daily episode for a cooperative space exploration game.

Context:
- Day: {day}
- Previous summary: {previous_summary or "First day of mission"}
- Player role: {player_role_display}

Create a compelling narrative with:
1. A setting (space location, station, planet)
2. A central conflict or mystery
3. 3 decision points for players with visible actions and hidden consequences

Return ONLY valid JSON with this structure:
{{
    "setting": "description of the location",
    "conflict": "the central problem",
    "narrative": "the story description",
    "decision_points": [
        {{"id": "a1", "text": "action 1", "consequence": "result 1"}},
        {{"id": "a2", "text": "action 2", "consequence": "result 2"}},
        {{"id": "a3", "text": "action 3", "consequence": "result 3"}}
    ]
}}

{lang_directive}
"""

            logger.debug(f"[STORY] Prompt sent to LLM, waiting for response...")
            response = self.agent(prompt)
            response_str = str(response)
            logger.info(f"[STORY] LLM response received ({len(response_str)} chars)")
            logger.debug(f"[STORY] Raw response: {response_str[:500]}...")

            parsed = self._parse_json_from_response(response_str)
            if parsed:
                logger.info(f"[STORY] JSON parsed successfully")
                story = GameStory(
                    day=day,
                    setting=parsed.get("setting", ""),
                    conflict=parsed.get("conflict", ""),
                    narrative=parsed.get("narrative", ""),
                    decision_points=parsed.get("decision_points", []),
                )
                logger.info(f"[STORY] Story generated: setting='{story.setting[:50]}...', conflict='{story.conflict[:50]}...'")
                logger.info(f"[STORY] Decision points: {len(story.decision_points)} actions")
                return story

            logger.error(f"[STORY] Failed to parse JSON from LLM response")
            raise ValueError("Failed to parse JSON from LLM response")

        except Exception as e:
            logger.error(f"[STORY] LLM story generation failed: {e}")
            raise

    async def generate_npc_dialogues(
        self, story: GameStory, player_role: str
    ) -> List[NPCDialogue]:
        """Generate NPC dialogues for the day"""
        logger.info(f"[NPC] Starting NPC dialogue generation, language: {self.language}")
        team_npcs = self.generate_team_npcs(player_role)
        dialogues = []

        if self.language == "ru":
            lang_directive = "Respond in RUSSIAN."
            player_role_display = player_role or "Член экипажа"
        else:
            lang_directive = "Respond in ENGLISH."
            player_role_display = player_role or "Crew member"

        if not self.agent:
            logger.error("[NPC] LLM agent not available - cannot generate NPC dialogues")
            raise RuntimeError("LLM agent not available - cannot generate NPC dialogues")

        logger.info(f"[NPC] Generating dialogues for {len(team_npcs)} NPCs: {list(team_npcs.keys())}")

        for npc_key, npc in team_npcs.items():
            try:
                npc_name = npc.get('name', npc.get('default_name', 'Unknown'))
                logger.info(f"[NPC] Generating dialogue for {npc_name} ({npc_key})")

                prompt = f"""
You are {npc_name}, {npc['role']}.
Personality: {npc['personality']}
Speech style: {npc['speech_style']}

Game context: {story.narrative}
Player role: {player_role_display}

Generate a short reaction (1-2 sentences) in character.
{lang_directive}
"""

                response = self.agent(prompt)
                response_str = str(response).strip()
                logger.info(f"[NPC] {npc_key}: Response received ({len(response_str)} chars)")
                logger.debug(f"[NPC] {npc_key}: '{response_str[:100]}...'")

                dialogues.append(
                    NPCDialogue(
                        npc_name=npc_name,
                        npc_role=npc["role"],
                        dialogue=response_str,
                        emotion="neutral",
                    )
                )
            except Exception as e:
                logger.error(f"[NPC] Dialogue generation failed for {npc_key}: {e}")
                raise

        logger.info(f"[NPC] Generated {len(dialogues)} NPC dialogues successfully")
        return dialogues

    async def generate_content_prompts(
        self, story: GameStory, dialogues: List[NPCDialogue], player_role: str
    ) -> ContentPrompts:
        """Generate prompts for content generation (image, video, comic)"""
        logger.info(f"[CONTENT] Starting content prompt generation, language: {self.language}")

        if self.language == "ru":
            lang_directive = "Respond in RUSSIAN."
        else:
            lang_directive = "Respond in ENGLISH."

        if not self.agent:
            logger.error("[CONTENT] LLM agent not available - cannot generate content prompts")
            raise RuntimeError("LLM agent not available - cannot generate content prompts")

        try:
            prompt = f"""
Generate content prompts for a game day.

Story: {story.narrative}
Player role: {player_role}

Return ONLY valid JSON with this structure:
{{
    "image_prompt": "description for image generation",
    "video_prompt": "description for video generation",
    "scene_3d_prompt": "description for 3D scene",
    "comic_prompt": "description for comic strip"
}}

{lang_directive}
"""

            logger.info(f"[CONTENT] Sending prompt to LLM")
            response = self.agent(prompt)
            response_str = str(response)
            logger.info(f"[CONTENT] LLM response received ({len(response_str)} chars)")

            parsed = self._parse_json_from_response(response_str)
            if parsed:
                prompts = ContentPrompts(
                    image_prompt=parsed.get("image_prompt", ""),
                    video_prompt=parsed.get("video_prompt", ""),
                    scene_3d_prompt=parsed.get("scene_3d_prompt", ""),
                    comic_prompt=parsed.get("comic_prompt", ""),
                )
                logger.info(f"[CONTENT] Content prompts generated successfully")
                return prompts

            logger.error(f"[CONTENT] Failed to parse JSON from LLM response")
            raise ValueError("Failed to parse JSON from LLM response")

        except Exception as e:
            logger.error(f"[CONTENT] Content prompt generation failed: {e}")
            raise

    async def generate_personalized_comic(
        self, story: GameStory, player_profile: Dict[str, Any]
    ) -> str:
        """Generate a personalized comic for the player"""
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

        return f"/content/comics/day_{story.day}_player_{player_profile.get('player_id', 'unknown')}.webp"

    async def process_player_message(
        self, player_id: int, message: str, player_profile: Dict[str, Any]
    ) -> str:
        """Process a player message and generate Game Master response"""
        if not self.agent:
            raise RuntimeError("LLM agent not available")

        try:
            player_role = player_profile.get("role", "Crew Member")

            if self.language == "ru":
                lang_directive = "Respond in RUSSIAN."
            else:
                lang_directive = "Respond in ENGLISH."

            prompt = f"""
You are the Game Master of a space exploration game.
Player (role: {player_role}) sent this message:

"{message}"

Respond in character as the Game Master, acknowledging their input
and guiding the narrative forward. Keep it engaging and in the
Star Trek universe tone.
{lang_directive}
"""

            response = self.agent(prompt)
            return str(response) if response else "Game Master received your message."
        except Exception as e:
            logger.error(f"Message processing failed: {e}")
            raise

    async def generate_default_action(
        self, story: GameStory, player_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate a default action when player doesn't choose"""
        traits = player_profile.get("personality_traits", [])
        actions = story.decision_points

        if "логичный" in traits or "аналитический" in traits:
            return actions[0] if len(actions) > 0 else actions[0]
        elif "смелый" in traits or "решительный" in traits:
            return actions[1] if len(actions) > 1 else actions[0]
        else:
            return actions[2] if len(actions) > 2 else actions[0]


# Factory function
async def create_game_master_agent(language: str = "en") -> GameMasterAgent:
    """Create and initialize Game Master agent

    Args:
        language: Language for content generation ("en" or "ru")
    """
    agent = GameMasterAgent(language=language)
    await agent.initialize()
    return agent