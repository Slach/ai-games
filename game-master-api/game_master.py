"""
Game Master Agent - Direct OpenAI API for game orchestration

Uses openai client with json_schema response_format for all LLM calls.
Compatible with llama.cpp / vLLM / any OpenAI-compatible endpoint.
"""

import os
import logging
import json
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from openai import OpenAI
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ============== Pydantic Models ==============


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


class OnboardingQuestions(BaseModel):
    """Structured onboarding questions"""

    questions: List[Dict[str, Any]]


# ============== NPC Role Templates ==============

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


# ============== JSON Schema Definitions ==============

STORY_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "daily_story",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "setting": {
                    "type": "string",
                    "description": "Description of the space location, station or planet",
                },
                "conflict": {
                    "type": "string",
                    "description": "The central problem or mystery",
                },
                "narrative": {
                    "type": "string",
                    "description": "The story description for the day",
                },
                "decision_points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "text": {
                                "type": "string",
                                "description": "Action description visible to player",
                            },
                            "consequence": {
                                "type": "string",
                                "description": "Hidden consequence result",
                            },
                        },
                        "required": ["id", "text", "consequence"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["setting", "conflict", "narrative", "decision_points"],
            "additionalProperties": False,
        },
    },
}

ONBOARDING_QUESTIONS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "onboarding_questions",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": "Question text about what would you do",
                            },
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "value": {
                                            "type": "string",
                                            "description": "Short value identifier",
                                        },
                                        "label": {
                                            "type": "string",
                                            "description": "Full display text for this option",
                                        },
                                    },
                                    "required": ["value", "label"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["text", "options"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["questions"],
            "additionalProperties": False,
        },
    },
}

NPC_DIALOGUE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "npc_dialogue",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "dialogue": {
                    "type": "string",
                    "description": "NPC reaction in character, 1-2 sentences",
                },
                "emotion": {
                    "type": "string",
                    "description": "Emotional tone: neutral, concerned, excited, worried, determined",
                },
            },
            "required": ["dialogue", "emotion"],
            "additionalProperties": False,
        },
    },
}

CONTENT_PROMPTS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "content_prompts",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "image_prompt": {"type": "string"},
                "video_prompt": {"type": "string"},
                "scene_3d_prompt": {"type": "string"},
                "comic_prompt": {"type": "string"},
            },
            "required": [
                "image_prompt",
                "video_prompt",
                "scene_3d_prompt",
                "comic_prompt",
            ],
            "additionalProperties": False,
        },
    },
}

PLAYER_MESSAGE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "gm_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "response": {
                    "type": "string",
                    "description": "Game Master response to the player message",
                }
            },
            "required": ["response"],
            "additionalProperties": False,
        },
    },
}

AVATAR_PROMPT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "avatar_prompt",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "avatar_prompt": {
                    "type": "string",
                    "description": "Detailed image generation prompt for the player's character avatar",
                }
            },
            "required": ["avatar_prompt"],
            "additionalProperties": False,
        },
    },
}


# ============== Game Master Agent ==============


class GameMasterAgent:
    """
    Game Master agent using direct OpenAI API calls with json_schema
    structured outputs for all LLM interactions.
    """

    def __init__(self, language: str = "en"):
        self.llm_base_url = os.getenv("LLM_URL", "http://llama.cpp:8090/v1")
        self.llm_api_key = os.getenv("LLM_API_KEY", "placeholder-key-for-llama-cpp")
        self.llm_model = os.getenv("LLM_MODEL", "unsloth/Qwen3.5-27B")
        self.language = language
        self.npcs: Dict[str, Dict[str, Any]] = {}

        self.client = OpenAI(
            api_key=self.llm_api_key,
            base_url=self.llm_base_url,
        )

        self._init_default_npcs()
        logger.info(
            f"GameMasterAgent initialized: model={self.llm_model}, language={language}"
        )

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

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Dict[str, Any],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """
        Call LLM with json_schema structured output.

        Falls back to plain text + JSON extraction if the endpoint
        does not support response_format (e.g. older llama.cpp).
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            # Try structured output first
            response = self.client.chat.completions.create(
                model=self.llm_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_schema,
            )
            content = response.choices[0].message.content
            return json.loads(content)

        except Exception as e:
            logger.warning(
                f"Structured output failed ({e}), falling back to plain JSON extraction"
            )

            # Fallback: ask for JSON in plain text, then parse
            json_instruction = (
                "\n\nIMPORTANT: Return ONLY valid JSON. No markdown, no code blocks, no explanation. "
                "Pure JSON only."
            )
            messages[1]["content"] = user_prompt + json_instruction

            response = self.client.chat.completions.create(
                model=self.llm_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content.strip()

            # Clean and parse
            content = self._strip_json_block(content)
            return json.loads(content)

    def _call_llm_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Call LLM and return raw text response (for free-form text)."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self.client.chat.completions.create(
            model=self.llm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()

    @staticmethod
    def _strip_json_block(text: str) -> str:
        """Remove markdown code blocks and extract JSON."""
        import re

        # Remove markdown code blocks
        cleaned = re.sub(r"```(?:json)?\s*", "", text)
        cleaned = re.sub(r"\s*```", "", cleaned)

        # Try to find JSON object or array
        for pattern in [r"\{.*\}", r"\[.*\]"]:
            match = re.search(pattern, cleaned, re.DOTALL)
            if match:
                return match.group()

        return cleaned.strip()

    # ============== Onboarding ==============

    def generate_onboarding_questions(self) -> List[Dict[str, Any]]:
        """Generate dynamic onboarding questions using LLM with json_schema."""
        logger.info(f"Generating onboarding questions, language: {self.language}")

        if self.language == "ru":
            system = "Ты — дизайнер игр. Генерируй вопросы для онбординга в космической игре."
            user = (
                "Сгенерируй 3 вопроса для онбординга в игре про космические исследования. "
                "Каждый вопрос — это ситуация с выбором из 2-3 вариантов. "
                "Вопросы помогают определить роль игрока (инженер, офицер связи, учёный) "
                "и черты его личности (осторожный/смелый, логичный/эмпатичный и т.д.). "
                "Все тексты на русском языке."
            )
        else:
            system = "You are a game designer. Generate onboarding questions for a space exploration game."
            user = (
                "Generate 3 onboarding questions for a space exploration game. "
                "Each question is a scenario with 2-3 choices. "
                "Questions help determine player role (engineer, communications officer, scientist) "
                "and personality traits (cautious/bold, logical/empathetic, etc). "
                "All text in English."
            )

        result = self._call_llm(
            system_prompt=system,
            user_prompt=user,
            response_schema=ONBOARDING_QUESTIONS_SCHEMA,
        )

        questions = result.get("questions", [])
        # Add sequential IDs
        for i, q in enumerate(questions, start=1):
            q["id"] = i

        logger.info(f"Generated {len(questions)} onboarding questions")
        return questions

    # ============== Daily Story ==============

    def generate_daily_story(
        self, day: int, previous_summary: str = "", player_role: str = ""
    ) -> GameStory:
        """Generate daily story using LLM with json_schema."""
        logger.info(
            f"[STORY] Starting story generation for Day {day}, language: {self.language}"
        )

        if self.language == "ru":
            system = (
                "Ты — Game Master космической исследовательской игры в стиле Star Trek. "
                "Создаёшь увлекательные ежедневные эпизоды с конфликтами и выбором."
            )
            player_role_display = player_role or "Член экипажа"
            user = (
                f"День: {day}\n"
                f"Предыдущий день: {previous_summary or 'Первый день миссии'}\n"
                f"Роль игрока: {player_role_display}\n\n"
                "Создай эпизод с:\n"
                "1. Место действия (космос, станция, планета)\n"
                "2. Центральный конфликт или тайна\n"
                "3. 3 точки выбора для игрока с действиями и скрытыми последствиями\n\n"
                "Всё на русском языке."
            )
        else:
            system = (
                "You are a Game Master for a Star Trek-style space exploration game. "
                "Create compelling daily episodes with conflicts and player choices."
            )
            player_role_display = player_role or "Crew member"
            user = (
                f"Day: {day}\n"
                f"Previous day: {previous_summary or 'First day of mission'}\n"
                f"Player role: {player_role_display}\n\n"
                "Create an episode with:\n"
                "1. A setting (space location, station, planet)\n"
                "2. A central conflict or mystery\n"
                "3. 3 decision points for the player with visible actions and hidden consequences\n"
            )

        parsed = self._call_llm(
            system_prompt=system,
            user_prompt=user,
            response_schema=STORY_SCHEMA,
            max_tokens=4096,
        )

        story = GameStory(
            day=day,
            setting=parsed.get("setting", ""),
            conflict=parsed.get("conflict", ""),
            narrative=parsed.get("narrative", ""),
            decision_points=parsed.get("decision_points", []),
        )
        logger.info(
            f"[STORY] Story generated: setting='{story.setting[:50]}...', {len(story.decision_points)} actions"
        )
        return story

    # ============== NPC Dialogues ==============

    def generate_npc_dialogues(
        self, story: GameStory, player_role: str
    ) -> List[NPCDialogue]:
        """Generate NPC dialogues for the day."""
        logger.info(
            f"[NPC] Starting NPC dialogue generation, language: {self.language}"
        )
        team_npcs = self.generate_team_npcs(player_role)
        dialogues = []

        if self.language == "ru":
            lang_note = "Отвечай на русском."
            player_role_display = player_role or "Член экипажа"
        else:
            lang_note = "Respond in English."
            player_role_display = player_role or "Crew member"

        for npc_key, npc in team_npcs.items():
            try:
                npc_name = npc.get("name", npc.get("default_name", "Unknown"))
                logger.info(f"[NPC] Generating dialogue for {npc_name} ({npc_key})")

                system = (
                    f"You are {npc_name}, {npc['role']}.\n"
                    f"Personality: {npc['personality']}\n"
                    f"Speech style: {npc['speech_style']}\n"
                    f"{lang_note}"
                )
                user = (
                    f"Game context: {story.narrative}\n"
                    f"Player role: {player_role_display}\n\n"
                    f"Generate a short in-character reaction (1-2 sentences)."
                )

                parsed = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=NPC_DIALOGUE_SCHEMA,
                    temperature=0.8,
                    max_tokens=256,
                )

                dialogues.append(
                    NPCDialogue(
                        npc_name=npc_name,
                        npc_role=npc["role"],
                        dialogue=parsed.get("dialogue", ""),
                        emotion=parsed.get("emotion", "neutral"),
                    )
                )
            except Exception as e:
                logger.error(f"[NPC] Dialogue generation failed for {npc_key}: {e}")
                raise

        logger.info(f"[NPC] Generated {len(dialogues)} NPC dialogues")
        return dialogues

    # ============== Content Prompts ==============

    def generate_content_prompts(
        self, story: GameStory, dialogues: List[NPCDialogue], player_role: str
    ) -> ContentPrompts:
        """Generate prompts for content generation (image, video, comic)."""
        logger.info(
            f"[CONTENT] Starting content prompt generation, language: {self.language}"
        )

        if self.language == "ru":
            lang_note = "Промпты пиши на английском (для генерации изображений)."
        else:
            lang_note = "Write prompts in English for image generation."

        system = "You are an AI art prompt engineer. Generate detailed, high-quality prompts for image/video generation."
        user = (
            f"Story: {story.narrative}\n"
            f"Player role: {player_role}\n\n"
            f"Generate content prompts for image, video, 3D scene, and comic strip.\n"
            f"{lang_note}"
        )

        parsed = self._call_llm(
            system_prompt=system,
            user_prompt=user,
            response_schema=CONTENT_PROMPTS_SCHEMA,
            max_tokens=2048,
        )

        prompts = ContentPrompts(
            image_prompt=parsed.get("image_prompt", ""),
            video_prompt=parsed.get("video_prompt", ""),
            scene_3d_prompt=parsed.get("scene_3d_prompt", ""),
            comic_prompt=parsed.get("comic_prompt", ""),
        )
        logger.info("[CONTENT] Content prompts generated")
        return prompts

    # ============== Player Message ==============

    def process_player_message(
        self, player_id: int, message: str, player_profile: Dict[str, Any]
    ) -> str:
        """Process a player message and generate Game Master response."""
        player_role = player_profile.get("role", "Crew Member")

        if self.language == "ru":
            system = (
                "Ты — Game Master космической исследовательской игры в стиле Star Trek. "
                "Отвечай в стиле Game Master, направляя叙事. "
                "Будь увлекательным и атмосферным."
            )
        else:
            system = (
                "You are the Game Master of a Star Trek-style space exploration game. "
                "Respond in character as the Game Master, guiding the narrative forward. "
                "Keep it engaging and atmospheric."
            )

        user = (
            f"Player (role: {player_role}) sent this message:\n\n"
            f'"{message}"\n\n'
            "Respond in character as Game Master."
        )

        try:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=PLAYER_MESSAGE_SCHEMA,
                max_tokens=1024,
            )
            return parsed.get("response", "Game Master received your message.")
        except Exception as e:
            logger.error(f"Message processing failed: {e}")
            # Fallback to text-only call
            return self._call_llm_text(system, user)

    # ============== Avatar Prompt ==============

    def generate_avatar_prompt(
        self, role: str, traits: List[str], avatar_description: str
    ) -> str:
        """Generate an image prompt for player avatar using LLM with json_schema."""
        logger.info(f"[AVATAR] Generating avatar prompt for role: {role}")

        system = (
            "You are an expert AI art prompt engineer specializing in sci-fi character portraits. "
            "Generate detailed, cinematic-quality image prompts for character avatars."
        )
        user = (
            f"Generate an image prompt for a Star Trek-style character avatar.\n"
            f"Role: {role}\n"
            f"Personality traits: {', '.join(traits)}\n"
            f"Character description: {avatar_description}\n\n"
            "The prompt should describe:\n"
            "- Character appearance (face, expression, uniform details)\n"
            "- Cinematic lighting and composition\n"
            "- Sci-fi/space opera aesthetic\n"
            "- High quality, 4K, detailed\n"
            "- Portrait style, upper body\n"
            "Write the prompt in English."
        )

        parsed = self._call_llm(
            system_prompt=system,
            user_prompt=user,
            response_schema=AVATAR_PROMPT_SCHEMA,
            max_tokens=1024,
        )

        avatar_prompt = parsed.get("avatar_prompt", "")
        logger.info(f"[AVATAR] Avatar prompt generated: {avatar_prompt[:100]}...")
        return avatar_prompt

    # ============== Default Action ==============

    def generate_default_action(
        self, story: GameStory, player_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate a default action when player doesn't choose"""
        traits = player_profile.get("personality_traits", [])
        actions = story.decision_points

        if (
            "логичный" in traits
            or "аналитический" in traits
            or "logical" in traits
            or "analytical" in traits
        ):
            return actions[0] if actions else {}
        elif (
            "смелый" in traits
            or "решительный" in traits
            or "bold" in traits
            or "decisive" in traits
        ):
            return actions[1] if len(actions) > 1 else (actions[0] if actions else {})
        else:
            return actions[2] if len(actions) > 2 else (actions[0] if actions else {})


# ============== Factory Function ==============


def create_game_master_agent(language: str = "en") -> GameMasterAgent:
    """Create and initialize Game Master agent.

    Args:
        language: Language for content generation ("en" or "ru")
    """
    return GameMasterAgent(language=language)
