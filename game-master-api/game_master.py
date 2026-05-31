"""
Game Master Agent - Direct OpenAI API for game orchestration

Uses openai client with json_schema response_format for all LLM calls.
Compatible with llama.cpp / vLLM / any OpenAI-compatible endpoint.
"""

import os
import logging
import json
import uuid
import re
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

# Onboarding configuration from environment
ONBOARDING_QUESTIONS_COUNT = int(os.getenv("ONBOARDING_QUESTIONS_COUNT", "5"))
ONBOARDING_OPTIONS_COUNT = int(os.getenv("ONBOARDING_OPTIONS_COUNT", "5"))

# All valid ship role keys used for role_scores in onboarding options
SHIP_ROLE_KEYS = [
    "chief_engineer",
    "science_officer",
    "communications_officer",
    "security_chief",
    "navigator",
    "medical_officer",
    "tactical_officer",
    "quartermaster",
    "xenobiologist",
    "pilot",
]


def _build_onboarding_questions_schema() -> dict:
    """Build JSON schema for onboarding questions with configurable counts."""
    role_score_properties = {key: {"type": "integer"} for key in SHIP_ROLE_KEYS}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "onboarding_questions",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "minItems": ONBOARDING_QUESTIONS_COUNT,
                        "maxItems": ONBOARDING_QUESTIONS_COUNT,
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "Question text about what would you do",
                                },
                                "options": {
                                    "type": "array",
                                    "minItems": ONBOARDING_OPTIONS_COUNT,
                                    "maxItems": ONBOARDING_OPTIONS_COUNT,
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
                                            "role_scores": {
                                                "type": "object",
                                                "description": "Points awarded to each role when this option is selected. Keys are role_key strings, values are integers 0-3.",
                                                "properties": role_score_properties,
                                                "required": SHIP_ROLE_KEYS,
                                                "additionalProperties": False,
                                            },
                                        },
                                        "required": ["value", "label", "role_scores"],
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


ONBOARDING_QUESTIONS_SCHEMA = _build_onboarding_questions_schema()

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

ROLE_ASSIGNMENT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "role_assignment",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "role_key": {
                    "type": "string",
                    "description": "The role_key of the best matching role from the available roles list",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation of why this role matches the player's answers",
                },
            },
            "required": ["role_key", "reasoning"],
            "additionalProperties": False,
        },
    },
}

SPECIES_GENDER_DESC_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "species_gender_description",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "species_description": {
                    "type": "string",
                    "description": "A vivid narrative description of the character's species/race and what it means for their identity",
                },
                "gender_description": {
                    "type": "string",
                    "description": "A vivid narrative description of the character's gender/reproductive form and how it shapes their experience",
                },
                "combined_description": {
                    "type": "string",
                    "description": "A combined 2-3 sentence narrative blending species and gender into one cohesive character concept",
                },
            },
            "required": ["species_description", "gender_description", "combined_description"],
            "additionalProperties": False,
        },
    },
}

GAME_TITLE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "game_title",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "A creative game title (ship name + mission tagline)",
                },
                "welcome_text": {
                    "type": "string",
                    "description": "An atmospheric welcome message in the starship setting",
                },
            },
            "required": ["title", "welcome_text"],
            "additionalProperties": False,
        },
    },
}

NPC_CHOICE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "npc_choice",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": "The ID of the selected action/choice",
                },
                "rationale": {
                    "type": "string",
                    "description": "In-character reasoning for why this NPC chose this action, based on their personality and role (2-3 sentences)",
                },
            },
            "required": ["action_id", "rationale"],
            "additionalProperties": False,
        },
    },
}

GLOBAL_CIRCUMSTANCES_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "global_circumstances",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "setting": {
                    "type": "string",
                    "description": "Description of the current space location, station or planet",
                },
                "conflict": {
                    "type": "string",
                    "description": "The central problem or mystery",
                },
                "narrative": {
                    "type": "string",
                    "description": "The shared story description for the day from the GM's perspective",
                },
                "key_events": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-5 key events happening in the background that all characters can perceive",
                },
            },
            "required": ["setting", "conflict", "narrative", "key_events"],
            "additionalProperties": False,
        },
    },
}

PLAYER_BRIEFING_CHOICES_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "player_briefing",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "briefing": {
                    "type": "string",
                    "description": "Personal narrative for this specific player — what they see, hear, and feel from their unique perspective",
                },
                "choices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "text": {
                                "type": "string",
                                "description": "Action description visible to the player",
                            },
                            "consequence": {
                                "type": "string",
                                "description": "Hidden consequence result — NOT visible to the player when making the choice",
                            },
                        },
                        "required": ["id", "text", "consequence"],
                        "additionalProperties": False,
                    },
                    "minItems": 3,
                    "maxItems": 4,
                    "description": "3-4 decision points with actions and hidden consequences",
                },
            },
            "required": ["briefing", "choices"],
            "additionalProperties": False,
        },
    },
}

COMBINED_OUTCOME_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "combined_outcome",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "outcome_narrative": {
                    "type": "string",
                    "description": "A coherent narrative describing what actually happened as a result of all choices made",
                },
                "ship_status_change": {
                    "type": "string",
                    "description": "How the ship's condition changed (e.g. 'hull damage repaired', 'shields depleted')",
                },
                "crew_morale_change": {
                    "type": "string",
                    "description": "How crew morale shifted",
                },
                "next_day_hook": {
                    "type": "string",
                    "description": "A teaser or hook for the next day's story",
                },
            },
            "required": ["outcome_narrative", "ship_status_change", "crew_morale_change", "next_day_hook"],
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
        self.llm_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "32768"))
        self.llm_max_avatar_tokens = int(os.getenv("LLM_MAX_AVATAR_TOKENS", "4096"))
        self.language = language
        self.npcs: Dict[str, Dict[str, Any]] = {}

        self.client = OpenAI(
            api_key=self.llm_api_key,
            base_url=self.llm_base_url,
        )

        self._init_default_npcs()
        logger.info(
            f"GameMasterAgent initialized: model={self.llm_model}, "
            f"language={language}, max_tokens={self.llm_max_tokens}"
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
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Call LLM with json_schema structured output.

        Falls back to plain text + JSON extraction if the endpoint
        does not support response_format (e.g. older llama.cpp).
        """
        if max_tokens is None:
            max_tokens = self.llm_max_tokens
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        response = None
        try:
            # Try structured output first
            response = self.client.chat.completions.create(
                model=self.llm_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_schema,
                extra_body=extra_body,
            )
            content = response.choices[0].message.content
            finish_reason = response.choices[0].finish_reason
            if content is None:
                raise ValueError(
                    f"LLM returned content=None. Finish reason: {finish_reason}. "
                    f"Usage: {response.usage}"
                )
            return json.loads(content)

        except Exception as e:
            logger.warning(
                f"Structured output failed ({e}), falling back to plain JSON extraction"
            )
            # Log raw response from the first attempt if available
            if response is not None:
                try:
                    first_content = response.choices[0].message.content
                    logger.warning(
                        f"Raw LLM response on first attempt:\n"
                        f"type(content)={type(first_content).__name__}\n"
                        f"content={repr(first_content)}\n"
                        f"finish_reason={response.choices[0].finish_reason}"
                    )
                except Exception as log_err:
                    logger.warning(f"Could not log raw response: {log_err}")

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
                extra_body=extra_body,
            )
            content = response.choices[0].message.content
            finish_reason = response.choices[0].finish_reason
            if content is None or content.strip() == "":
                raise ValueError(
                    f"LLM returned empty content on fallback call. "
                    f"Finish reason: {finish_reason}. "
                    f"Raw response:\n{str(response)}"
                )

            content = content.strip()
            # Clean and parse
            content = self._strip_json_block(content)
            try:
                return json.loads(content)
            except json.JSONDecodeError as parse_err:
                logger.error(
                    f"Fallback JSON parse failed: {parse_err}\n"
                    f"Raw content:\n{content}\n"
                    f"Finish reason: {finish_reason}"
                )
                raise

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
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return response.choices[0].message.content.strip()

    @staticmethod
    def _strip_json_block(text: str) -> str:
        """Remove markdown code blocks and extract JSON."""
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

        questions_count = ONBOARDING_QUESTIONS_COUNT
        options_count = ONBOARDING_OPTIONS_COUNT
        role_keys_str = ", ".join(SHIP_ROLE_KEYS)

        if self.language == "ru":
            system = "Ты — дизайнер игр. Генерируешь вопросы для онбординга в космической игре."
            user = (
                f"Сгенерируй {questions_count} вопросов для онбординга в игре про космический экипаж звездного корабля. "
                f"Каждый вопрос — это конкретная ситуация на корабле или во время миссии с выбором из {options_count} вариантов ДЕЙСТВИЙ. "
                "ВАЖНО: Каждый вариант ответа должен описывать КОНКРЕТНОЕ ДЕЙСТВИЕ, которое игрок совершает в этой ситуации. "
                "ПРИМЕР правильных вариантов: 'Бежать в машинное отделение и попытаться починить варп-двигатель', "
                "'Активировать аварийные щиты и вызвать подкрепление'. "
                "НЕПРАВИЛЬНО: 'Инженер — технический специалист', 'Учёный – смелый, ищущий прорыв'. "
                "НЕПРАВИЛЬНО: 'A', 'B', 'C' — метки вариантов должны быть ПОЛНЫМИ описаниями действий! "
                "Никогда не указывайте название роли или тип личности в вариантах ответа — только действия. "
                "Каждый вариант (label) должен быть развёрнутым предложением минимум из 5-7 слов, описывающим конкретное действие. "
                "КРИТИЧНО: Все варианты ответа в одном вопросе должны быть РАЗЛИЧНЫМИ и описывать РАЗНЫЕ действия. "
                "Не допускай одинаковых или очень похожих вариантов — каждый должен представлять уникальный подход. "
                "Вопросы должны покрывать разные аспекты: реакция на опасность, работа с техникой, взаимодействие с экипажем, "
                "исследование неизвестного, принятие решений в кризисе. "
                "Все тексты на русском языке.\n\n"
                "КРИТИЧНО: Каждый вариант ответа (option) должен содержать поле role_scores — это объект с очками для ролей. "
                f"Доступные роли (ключи): {role_keys_str}. "
                "Каждому варианту назначь от 1 до 3 ролей, которым это действие больше всего подходит, с очками от 1 до 3. "
                "Остальным ролям поставь 0. Очки отражают насколько выбранное действие характерно для данной роли. "
                "ПРИМЕР role_scores для действия 'Починить варп-двигатель': "
                '{"chief_engineer": 3, "science_officer": 1, "tactical_officer": 0, "communications_officer": 0, '
                '"security_chief": 0, "navigator": 0, "medical_officer": 0, "quartermaster": 0, "xenobiologist": 0, "pilot": 1}. '
                "ВАЖНО: В каждом вопросе варианты должны давать очки РАЗНЫМ ролям — чтобы каждый вопрос помогал отличать игроков."
            )
        else:
            system = "You are a game designer. Generate onboarding questions for a space exploration game."
            user = (
                f"Generate {questions_count} onboarding questions for a starship crew game. "
                f"Each question is a specific situation aboard a ship or during a mission with {options_count} ACTION choices. "
                "CRITICAL: Each option must describe a SPECIFIC ACTION the player would take in this situation. "
                "CORRECT example: 'Run to engineering and try to repair the warp drive', "
                "'Activate emergency shields and call for backup'. "
                "INCORRECT: 'Engineer - technical specialist', 'Scientist - bold, seeking breakthrough'. "
                "INCORRECT: 'A', 'B', 'C' — option labels must be FULL action descriptions, NOT single letters! "
                "NEVER include role names or personality types in options — only actions. "
                "Each option label must be a detailed sentence of at least 5-7 words describing a specific action. "
                "CRITICAL: All options within a question MUST BE DISTINCT and describe DIFFERENT actions. "
                "Do NOT generate duplicate or very similar options — each must represent a unique approach. "
                "Questions should cover: reaction to danger, working with technology, crew interaction, "
                "exploring the unknown, crisis decision-making. "
                "All text in English.\n\n"
                "CRITICAL: Each option must contain a role_scores field — an object with points for each role. "
                f"Available roles (keys): {role_keys_str}. "
                "For each option, assign 1-3 roles that best match this action, with points from 1 to 3. "
                "Set 0 for all other roles. Points reflect how characteristic this action is for the given role. "
                "EXAMPLE role_scores for action 'Repair the warp drive': "
                '{"chief_engineer": 3, "science_officer": 1, "tactical_officer": 0, "communications_officer": 0, '
                '"security_chief": 0, "navigator": 0, "medical_officer": 0, "quartermaster": 0, "xenobiologist": 0, "pilot": 1}. '
                "IMPORTANT: Within each question, options should give points to DIFFERENT roles — so each question helps distinguish players."
            )

        # NOTE: This is a token-heavy generation (5 questions × 5 options × 10 role_scores).
        # Thinking is disabled globally via chat_template_kwargs in _call_llm.
        # max_tokens defaults to LLM_MAX_TOKENS env var (32768).
        result = self._call_llm(
            system_prompt=system,
            user_prompt=user,
            response_schema=ONBOARDING_QUESTIONS_SCHEMA,
        )

        questions = result.get("questions", [])
        
        # Validate and fix duplicate options within each question
        for q in questions:
            options = q.get("options", [])
            seen_labels = set()
            unique_options = []
            for opt in options:
                label = opt.get("label", "")
                # Skip duplicate labels
                if label in seen_labels:
                    continue
                # Skip overly short labels (single letters, "A", "B", etc.)
                if len(label.strip()) < 5:
                    logger.warning(f"Skipping short option label: '{label}' in question: {q.get('text', '')[:50]}")
                    continue
                seen_labels.add(label)
                unique_options.append(opt)

            # If we filtered out too many, keep original options
            if len(unique_options) < 2 and len(options) >= 2:
                logger.warning(f"Question had invalid options, using original: {q.get('text', '')[:50]}")
                unique_options = options

            q["options"] = unique_options
        
        for i, q in enumerate(questions, start=1):
            q["id"] = i

        logger.info(f"Generated {len(questions)} onboarding questions")
        return questions

    def assign_role_from_answers(
        self,
        answers: Dict[int, str],
        available_roles: List[Dict[str, Any]],
        questions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Assign role based on accumulated points from onboarding answers.

        Each answer option contains role_scores (dict of role_key -> points).
        We sum points per role across all answers and pick the available role
        with the highest total score. No LLM call needed — fully deterministic.

        Args:
            answers: Dict mapping question_id -> selected option label text
            available_roles: List of role dicts with at least role_key
            questions: List of question dicts with options containing role_scores

        Returns:
            Dict with role_key and reasoning (score breakdown).
        """
        logger.info(
            f"[ROLE] Assigning role from {len(answers)} answers, "
            f"{len(available_roles)} roles available, "
            f"questions provided: {questions is not None}"
        )

        if not available_roles:
            raise ValueError("No roles available")

        # Build role scores from answer selections
        role_points: Dict[str, int] = {key: 0 for key in SHIP_ROLE_KEYS}

        if questions:
            # Build lookup: question_id -> question data
            question_map = {q.get("id"): q for q in questions}

            for question_id, selected_label in answers.items():
                # Answers dict keys are strings after json.loads from DB (SQLite JSON stores all keys as strings)
                qid = int(question_id) if not isinstance(question_id, int) else question_id
                q_data = question_map.get(qid)
                if not q_data:
                    logger.warning(f"[ROLE] Question {question_id} (type={type(question_id).__name__}) not found in session data")
                    continue

                # Find the selected option by matching label
                selected_option = None
                for opt in q_data.get("options", []):
                    if opt.get("label") == selected_label or opt.get("value") == selected_label:
                        selected_option = opt
                        break

                if not selected_option:
                    logger.warning(
                        f"[ROLE] Answer '{selected_label[:50]}' not found in options for Q{question_id}"
                    )
                    continue

                # Add role_scores from the selected option
                scores = selected_option.get("role_scores", {})
                for role_key, points in scores.items():
                    if role_key in role_points:
                        role_points[role_key] += int(points)

        # Sort available roles by their accumulated points (descending)
        available_keys = {r["role_key"] for r in available_roles}
        scored_available = [
            (key, role_points.get(key, 0))
            for key in sorted(role_points.keys(), key=lambda k: role_points[k], reverse=True)
            if key in available_keys
        ]

        if not scored_available:
            # Fallback: pick first available
            best_key = available_roles[0]["role_key"]
            logger.warning(f"[ROLE] No scored roles available, falling back to {best_key}")
        else:
            best_key, best_score = scored_available[0]

        # Build reasoning string
        top_roles = sorted(role_points.items(), key=lambda x: x[1], reverse=True)[:5]
        reasoning = "Points: " + ", ".join(f"{k}={v}" for k, v in top_roles)

        logger.info(
            f"[ROLE] Point-based assignment: role_key={best_key}, {reasoning}"
        )

        return {"role_key": best_key, "reasoning": reasoning}

    def generate_game_title(self) -> Dict[str, str]:
        """Generate a creative game title and welcome message."""
        logger.info("[TITLE] Generating game title")

        if self.language == "ru":
            system = "Ты — креативный писатель-фантаст. Придумываешь названия и описания для космических приключений."
            user = (
                "Придумай название для игры про экипаж звездного корабля и приветственное сообщение. "
                "Название должно быть в формате: название корабля + подзаголовок миссии. "
                "Пример стиля: «Звёздный Крейсер Аврора: За горизонтом известного». "
                "Приветствие должно быть атмосферным — будто игрок заходит на борт корабля. "
                "Все тексты на русском языке."
            )
        else:
            system = "You are a creative sci-fi writer. You create titles and descriptions for space adventures."
            user = (
                "Create a title for a starship crew game and a welcome message. "
                "Title format: ship name + mission tagline. "
                "Example style: 'Star Cruiser Aurora: Beyond the Known Horizon'. "
                "The welcome should be atmospheric — as if the player is stepping aboard the ship. "
                "All text in English."
            )

        result = self._call_llm(
            system_prompt=system,
            user_prompt=user,
            response_schema=GAME_TITLE_SCHEMA,
            temperature=0.9,
        )

        logger.info(f"[TITLE] Generated: {result.get('title', '')}")
        return result

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

    def _detect_species_category(self, text: str) -> str:
        """Detect species category from avatar/species description text.

        Returns one of: 'human', 'humanoid', 'non_humanoid', 'energy',
                        'cybernetic', 'symbiotic'
        Matches both English and Russian species type names and common descriptors.
        """
        t = text.lower()

        # Species type keywords ordered by specificity (more specific first)
        categories = [
            ("energy", [
                "energy being", "энергетическая", "energy being", "plasma",
                "energy field", "gaseous", "frequency", "resonance", "light being",
                "energy pattern", "field of energy", "electromagnetic",
            ]),
            ("cybernetic", [
                "cybernetic", "кибернетическая", "robotic", "mechanical",
                "synthetic", "machine", "android", "construct", "digital",
                "cyborg", "prosthetic", "circuit", "processor",
            ]),
            ("symbiotic", [
                "symbiotic", "симбиотическая", "symbiont", "composite",
                "multiple beings", "host", "union", "collective",
                "союз существ", "коллектив", "симбионт",
            ]),
            ("non_humanoid", [
                "non_humanoid", "негуманоид", "tentacle", "carapace",
                "exoskeleton", "crystalline", "no face", "no head",
                "slime", "amorphous", "щупальца", "панцирь",
                "экзоскелет", "кристаллический", "бесформенный",
                "without face", "without head", "no humanoid form",
            ]),
            ("humanoid", [
                "humanoid", "гуманоид", "humanoid with",
            ]),
        ]

        for category, keywords in categories:
            if any(kw in t for kw in keywords):
                return category

        return "human"

    def _species_prompt_instructions(self, category: str) -> dict:
        """Return (focus_instructions, framing) for a given species category."""
        prompts = {
            "human": {
                "intro": "character avatar",
                "appearance": "- Character appearance (face, expression, uniform details)",
                "framing": "- Portrait style, upper body",
            },
            "humanoid": {
                "intro": "humanoid alien character avatar",
                "appearance": "- Character appearance: humanoid anatomy with subtle alien features "
                              "(unusual skin/hair/eye color, distinct ears/ridges, etc.)",
                "framing": "- Portrait style, upper body",
            },
            "non_humanoid": {
                "intro": "non-humanoid alien character",
                "appearance": "- The character's ACTUAL physical form from the description — "
                              "alien anatomy (tentacles, carapace, exoskeleton, multiple limbs, etc.)\n"
                              "- Do NOT add human features (face, hair, eyes) unless explicitly described",
                "framing": "- Full body or 3/4 view showing the alien physiology",
            },
            "energy": {
                "intro": "energy being character",
                "appearance": "- The character's form as a being of energy, plasma, or light — "
                              "no solid physical body\n"
                              "- Describe the visual signature: glow, frequency patterns, luminosity, \
spatial presence\n"
                              "- Do NOT add human features or solid anatomy unless explicitly described",
                "framing": "- Full body showing the energy form in its environment",
            },
            "cybernetic": {
                "intro": "cybernetic / synthetic character",
                "appearance": "- The character's mechanical/cybernetic body — "
                              "metal, circuits, synthetic components, digital displays\n"
                              "- If part-organic, highlight the blend of biological and mechanical\n"
                              "- Describe the technological aesthetic of their form",
                "framing": "- Full body or 3/4 view showing the mechanical/cybernetic anatomy",
            },
            "symbiotic": {
                "intro": "symbiotic / composite character",
                "appearance": "- The character as a composite of multiple organisms or entities — "
                              "describe how the different parts coexist in one form\n"
                              "- Highlight the hybrid nature: textures, connections, shared biology\n"
                              "- Do NOT default to a single humanoid body unless described that way",
                "framing": "- Full body view showing the composite/symbiotic nature",
            },
        }
        return prompts.get(category, prompts["human"])

    def generate_avatar_prompt(
        self, role: str, traits: List[str], avatar_description: str
    ) -> str:
        """Generate an image prompt for player avatar using LLM with json_schema."""
        logger.info(f"[AVATAR] Generating avatar prompt for role: {role}")

        species_cat = self._detect_species_category(avatar_description)
        logger.info(f"[AVATAR] Detected species category: {species_cat}")
        instr = self._species_prompt_instructions(species_cat)

        system = (
            "You are an expert AI art prompt engineer specializing in sci-fi character portraits. "
            "Generate detailed, cinematic-quality image prompts for character avatars.\n\n"
            "CRITICAL RULE: The character description below is the DEFINITIVE source for the "
            "character's appearance. If it describes an alien, non-humanoid, energy, cybernetic, "
            "or symbiotic being — describe their ACTUAL form, NOT human anatomy.\n"
            "Never default to \"face, hair, eyes, upper body\" for non-human characters."
        )

        user = (
            f"Generate an image prompt for a Star Trek-style {instr['intro']}.\n"
            f"Role: {role}\n"
            f"Personality traits: {', '.join(traits)}\n"
            f"Character description (definitive source): {avatar_description}\n\n"
            "The prompt should describe:\n"
            f"{instr['appearance']}\n"
            "- Environment setting (ship interior, lab, planet surface, etc.)\n"
            "- Cinematic lighting and composition appropriate to the character\n"
            "- Sci-fi/space opera aesthetic\n"
            "- High quality, 4K, detailed\n"
            f"{instr['framing']}\n"
            "Write the prompt in English."
        )

        parsed = self._call_llm(
            system_prompt=system,
            user_prompt=user,
            response_schema=AVATAR_PROMPT_SCHEMA,
            max_tokens=self.llm_max_avatar_tokens,
        )

        avatar_prompt = parsed.get("avatar_prompt", "")
        logger.info(f"[AVATAR] Avatar prompt generated ({species_cat}): {avatar_prompt[:100]}...")
        return avatar_prompt

    # ============== Species and Gender ==============

    @staticmethod
    def calculate_species_from_answers(
        answers: Dict[int, str],
        questions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Calculate species type by counting species_tags across answers.

        Returns dict with primary species, secondary (for tie/hybrid), and hybrid flag.
        """
        if not questions:
            return {"primary": "", "secondary": "", "hybrid": False}

        question_map = {q.get("id"): q for q in questions}
        tag_counts: Dict[str, int] = {}

        for question_id, selected_value in answers.items():
            qid = int(question_id) if not isinstance(question_id, int) else question_id
            q_data = question_map.get(qid)
            if not q_data:
                continue
            selected_option = None
            for opt in q_data.get("options", []):
                if opt.get("value") == selected_value or opt.get("label") == selected_value:
                    selected_option = opt
                    break
            if not selected_option:
                continue
            tags = selected_option.get("species_tags", [])
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        if not tag_counts:
            return {"primary": "", "secondary": "", "hybrid": False}

        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        primary = sorted_tags[0][0]
        primary_count = sorted_tags[0][1]
        secondary = ""
        hybrid = False
        if len(sorted_tags) > 1 and sorted_tags[1][1] == primary_count:
            secondary = sorted_tags[1][0]
            hybrid = True

        return {"primary": primary, "secondary": secondary, "hybrid": hybrid}

    @staticmethod
    def calculate_gender_from_answers(
        answers: Dict[int, str],
        questions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Calculate gender type by counting gender_tags across answers."""
        if not questions:
            return {"primary": "", "secondary": "", "hybrid": False}

        question_map = {q.get("id"): q for q in questions}
        tag_counts: Dict[str, int] = {}

        for question_id, selected_value in answers.items():
            qid = int(question_id) if not isinstance(question_id, int) else question_id
            q_data = question_map.get(qid)
            if not q_data:
                continue
            selected_option = None
            for opt in q_data.get("options", []):
                if opt.get("value") == selected_value or opt.get("label") == selected_value:
                    selected_option = opt
                    break
            if not selected_option:
                continue
            tags = selected_option.get("gender_tags", [])
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        if not tag_counts:
            return {"primary": "", "secondary": "", "hybrid": False}

        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        primary = sorted_tags[0][0]
        return {"primary": primary, "secondary": "", "hybrid": False}

    def generate_species_gender_description(
        self,
        species_result: Dict[str, Any],
        gender_result: Dict[str, Any],
        role: str,
    ) -> str:
        """Generate a vivid narrative description of the player's species and gender using LLM."""
        logger.info(f"[SPECIES] Generating species+gender description for role: {role}")

        species_display = species_result.get("primary", "unknown")
        gender_display = gender_result.get("primary", "undefined")
        hybrid = species_result.get("hybrid", False)
        secondary = species_result.get("secondary", "")

        if self.language == "ru":
            species_note = (
                f"Тип расы: {species_display}"
                + (f" (гибрид с {secondary})" if hybrid else "")
                + f"\nТип пола: {gender_display}"
            )
            system = (
                "Ты — креативный писатель-фантаст, создающий описания инопланетных персонажей. "
                "Опиши, как выглядят и ощущают себя существа такого типа. Будь атмосферным и детальным."
            )
            user = (
                f"Создай яркое нарративное описание персонажа для космической игры Star Trek.\n\n"
                f"Роль: {role}\n"
                f"{species_note}\n\n"
                f"Опиши:\n"
                f"1. Как выглядит и ощущает себя это существо (внешность, физиология, текстура, свечение и т.д.)\n"
                f"2. Как пол/форма размножения проявляется в их культуре и самовосприятии\n"
                f"3. Единый образ — как расовые и половые черты сливаются в одну личность\n\n"
                f"Текст на русском языке, 3-5 предложений, атмосферный и кинематографичный."
            )
        else:
            species_note = (
                f"Species type: {species_display}"
                + (f" (hybrid with {secondary})" if hybrid else "")
                + f"\nGender type: {gender_display}"
            )
            system = (
                "You are a creative sci-fi writer crafting descriptions of alien characters. "
                "Describe how beings of this type look and feel. Be atmospheric and detailed."
            )
            user = (
                f"Create a vivid narrative description of a character for a Star Trek-style space game.\n\n"
                f"Role: {role}\n"
                f"{species_note}\n\n"
                f"Describe:\n"
                f"1. How this being looks and feels (appearance, physiology, texture, glow, etc.)\n"
                f"2. How their gender/reproductive form manifests in their culture and self-perception\n"
                f"3. A unified image — how species and gender traits merge into one personality\n\n"
                f"Text in English, 3-5 sentences, atmospheric and cinematic."
            )

        try:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=SPECIES_GENDER_DESC_SCHEMA,
                temperature=0.8,
                max_tokens=1024,
            )
            combined = parsed.get("combined_description", "")
            logger.info(f"[SPECIES] Description generated: {combined[:100]}...")
            return combined
        except Exception as e:
            logger.warning(f"[SPECIES] LLM description failed, using fallback: {e}")
            return self._fallback_species_gender_description(species_display, gender_display, hybrid, secondary, role)

    def _fallback_species_gender_description(
        self,
        species_type: str,
        gender_type: str,
        hybrid: bool,
        secondary: str,
        role: str,
    ) -> str:
        """Generate a fallback template-based species+gender description when LLM fails."""
        if self.language == "ru":
            species_map = {
                "human": "Ты — человек. Твоё тело биологическое, уязвимое, но полное жизни.",
                "humanoid": "Ты — гуманоид с узнаваемой анатомией, но необычной физиологией.",
                "non_humanoid": "Твоя форма далека от человеческой — панцирь, щупальца или иная необычная биология.",
                "energy": "Ты — энергетическая форма жизни. Твоё сознание существует как устойчивый резонансный узор.",
                "cybernetic": "Ты — кибернетическая форма жизни. Части тебя можно чинить, улучшать и переносить.",
                "symbiotic": "Ты — симбиотическая форма жизни. Твоё \"я\" рождается в союзе нескольких существ.",
            }
            if hybrid and secondary and species_type in species_map and secondary in species_map:
                base = f"{species_map.get(species_type, species_type)} В тебе также есть черты: {species_map.get(secondary, secondary).lower()}"
            else:
                base = species_map.get(species_type, f"Твой вид — {species_type}.")
            gender_note = f" Твой пол: {gender_type}."
            return f"{base}{gender_note} Твоя роль на корабле — {role}."
        else:
            species_map = {
                "human": "You are human. Your body is biological, vulnerable, but full of life.",
                "humanoid": "You are a humanoid with recognizable anatomy but unusual physiology.",
                "non_humanoid": "Your form is far from human — a carapace, tentacles, or other unusual biology.",
                "energy": "You are an energy being. Your consciousness exists as a stable resonance pattern.",
                "cybernetic": "You are a cybernetic life form. Parts of you can be repaired, upgraded, and transferred.",
                "symbiotic": "You are a symbiotic life form. Your \"self\" is born from the union of several beings.",
            }
            if hybrid and secondary and species_type in species_map and secondary in species_map:
                base = f"{species_map.get(species_type, species_type)} You also bear traits of: {species_map.get(secondary, secondary)}."
            else:
                base = species_map.get(species_type, f"Your species is {species_type}.")
            gender_note = f" Your gender: {gender_type}."
            return f"{base}{gender_note} Your role aboard the ship is {role}."

    # ============== NPC Decision Making (LLM-based, no consequences visible) ==============

    def generate_npc_choice(
        self, choices: List[Dict[str, Any]], npc_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """NPC makes a choice using LLM without seeing the consequences.

        The NPC only sees the action text IDs and descriptions — no consequences.
        This ensures NPC decisions are role-played in-character.
        """
        logger.info(f"[NPC] Generating choice for NPC {npc_profile.get('npc_name', 'Unknown')}")

        npc_name = npc_profile.get("npc_name", "Unknown")
        npc_role = npc_profile.get("role", "Crew Member")
        traits = npc_profile.get("personality_traits", [])

        # Strip consequences from choices before passing to NPC
        clean_choices = []
        for c in choices:
            clean_choices.append({
                "id": c.get("id", ""),
                "text": c.get("text", ""),
            })

        # Build the choice text for the NPC
        choices_text = "\n".join([f"  [{c['id']}] {c['text']}" for c in clean_choices])

        if self.language == "ru":
            system = (
                f"Ты — {npc_name}, {npc_role} на космическом корабле. "
                f"Твой характер: {', '.join(traits) if isinstance(traits, list) else traits}. "
                f"Ты видишь ТОЛЬКО описания действий без последствий. "
                f"Сделай выбор на основе своей личности и роли."
            )
            user = (
                f"Текущая ситуация на корабле требует твоего решения.\n\n"
                f"Доступные действия:\n{choices_text}\n\n"
                f"Выбери одно действие, которое лучше всего соответствует твоему характеру и роли. "
                f"Ты не знаешь последствий — действуй интуитивно."
            )
        else:
            system = (
                f"You are {npc_name}, {npc_role} aboard a starship. "
                f"Your personality: {', '.join(traits) if isinstance(traits, list) else traits}. "
                f"You see ONLY action descriptions with no consequences. "
                f"Make a choice based on your personality and role."
            )
            user = (
                f"The current situation requires your decision.\n\n"
                f"Available actions:\n{choices_text}\n\n"
                f"Choose the action that best matches your character and role. "
                f"You don't know the consequences — act on instinct."
            )

        try:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=NPC_CHOICE_SCHEMA,
                temperature=0.8,
                max_tokens=512,
            )
            action_id = parsed.get("action_id", "")
            rationale = parsed.get("rationale", "")

            # Validate the choice is among available options
            valid_ids = [c.get("id") for c in choices]
            if action_id not in valid_ids:
                logger.warning(
                    f"[NPC] LLM returned invalid choice '{action_id}' for {npc_name}, "
                    f"falling back to first available"
                )
                action_id = valid_ids[0] if valid_ids else ""
                rationale = "Fallback: first available action"

            logger.info(f"[NPC] {npc_name} chose '{action_id}': {rationale[:60]}...")
            return {"action_id": action_id, "rationale": rationale}

        except Exception as e:
            logger.error(f"[NPC] LLM choice failed for {npc_name}: {e}")
            # Fallback: pick first action
            action_id = choices[0].get("id", "") if choices else ""
            return {"action_id": action_id, "rationale": "Fallback: system default"}

    # ============== Restructured Game Day Generation ==============

    def generate_global_circumstances(
        self,
        day: int,
        previous_summary: str = "",
        player_profiles: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate the shared global circumstances for a game day.

        This is the first step — creates the setting, conflict, and key events
        that all players and NPCs will experience from their own perspectives.
        """
        logger.info(f"[DAY] Generating global circumstances for Day {day}")

        player_descriptions = ""
        if player_profiles:
            player_lines = []
            for p in player_profiles:
                pid = p.get("player_id") or p.get("npc_key", "?")
                role = p.get("role", "Crew Member")
                player_lines.append(f"  - {pid}: {role}")
            player_descriptions = "\n".join(player_lines)

        if self.language == "ru":
            system = (
                "Ты — Game Master космической игры в стиле Star Trek. "
                "Создаёшь ОБЩИЕ обстоятельства дня — ситуацию, которая происходит на корабле или вокруг него. "
                "Эти обстоятельства едины для всех членов экипажа."
            )
            user = (
                f"День: {day}\n"
                f"Предыдущие события: {previous_summary or 'Первый день миссии'}\n"
                f"Экипаж:\n{player_descriptions or '  Экипаж формируется'}\n\n"
                "Создай общие обстоятельства дня:\n"
                "1. Место действия — где находится корабль (звездная система, станция, явление космоса)\n"
                "2. Конфликт — центральная проблема или тайна\n"
                "3. Нарратив — описание ситуации от лица GM (2-3 абзаца)\n"
                "4. Ключевые события — 3-5 фоновых событий, которые могут заметить все\n\n"
                "Всё на русском языке."
            )
        else:
            system = (
                "You are a Game Master for a Star Trek-style space exploration game. "
                "Create SHARED circumstances for the day — the situation unfolding on or around the ship. "
                "These circumstances are common to all crew members."
            )
            user = (
                f"Day: {day}\n"
                f"Previous events: {previous_summary or 'First day of mission'}\n"
                f"Crew:\n{player_descriptions or '  Crew forming'}\n\n"
                "Create shared circumstances for the day:\n"
                "1. Setting — where the ship is located\n"
                "2. Conflict — central problem or mystery\n"
                "3. Narrative — GM voice description (2-3 paragraphs)\n"
                "4. Key events — 3-5 background events everyone can perceive\n"
            )

        try:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=GLOBAL_CIRCUMSTANCES_SCHEMA,
                max_tokens=4096,
            )
            logger.info(f"[DAY] Global circumstances generated: setting='{str(parsed.get('setting', ''))[:60]}...'")
            return parsed
        except Exception as e:
            logger.error(f"[DAY] Global circumstances generation failed: {e}")
            return {
                "setting": "Unknown space region",
                "conflict": "Routine operations",
                "narrative": "The ship continues its mission.",
                "key_events": ["Normal operations underway"],
            }

    def generate_player_briefing_and_choices(
        self,
        global_circumstances: Dict[str, Any],
        player_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate a personal briefing and unique choices for a specific player
        based on the shared global circumstances.

        Each player gets:
        - A personal briefing (their unique perspective on the situation)
        - 3-4 choices with visible descriptions and hidden consequences
        """
        player_id = player_profile.get("player_id") or player_profile.get("npc_key", "?")
        player_role = player_profile.get("role", "Crew Member")
        traits = player_profile.get("personality_traits", [])
        logger.info(f"[DAY] Generating briefing for {player_id} ({player_role})")

        setting = global_circumstances.get("setting", "")
        conflict = global_circumstances.get("conflict", "")
        narrative = global_circumstances.get("narrative", "")
        key_events = global_circumstances.get("key_events", [])

        key_events_text = "\n".join([f"  - {e}" for e in key_events])

        if self.language == "ru":
            system = (
                "Ты — Game Master космической игры. Создаёшь ПЕРСОНАЛЬНУЮ вводную для игрока, "
                "основываясь на общих обстоятельствах дня. "
                "Каждый игрок видит ситуацию со своей уникальной точки зрения."
            )
            user = (
                f"Общие обстоятельства дня:\n"
                f"Локация: {setting}\n"
                f"Конфликт: {conflict}\n"
                f"Общий нарратив: {narrative}\n\n"
                f"Ключевые события:\n{key_events_text}\n\n"
                f"Персонаж:\n"
                f"  Роль: {player_role}\n"
                f"  Характер: {', '.join(traits) if isinstance(traits, list) else str(traits)}\n\n"
                "Создай:\n"
                "1. Персональную вводную (briefing) — что этот конкретный персонаж видит, слышит, чувствует. "
                "Как его роль и характер влияют на восприятие ситуации. (2-3 предложения)\n"
                "2. 3-4 варианта действий с последствиями — каждое действие должно быть логичным "
                "для этой роли, а последствия — скрытыми от игрока. "
                "Последствия не должны быть очевидны из текста действия!\n\n"
                "Всё на русском языке."
            )
        else:
            system = (
                "You are a Game Master. You create PERSONAL briefings for each player "
                "based on the shared global circumstances. "
                "Each player sees the situation from their unique perspective."
            )
            user = (
                f"Global circumstances:\n"
                f"Setting: {setting}\n"
                f"Conflict: {conflict}\n"
                f"Narrative: {narrative}\n\n"
                f"Key events:\n{key_events_text}\n\n"
                f"Character:\n"
                f"  Role: {player_role}\n"
                f"  Traits: {', '.join(traits) if isinstance(traits, list) else str(traits)}\n\n"
                "Create:\n"
                "1. Personal briefing — what this specific character sees, hears, feels. "
                "How their role and traits color their perception. (2-3 sentences)\n"
                "2. 3-4 action choices with consequences — each action should be logical "
                "for this role, with consequences hidden from the player. "
                "Consequences should not be obvious from the action text!\n"
            )

        try:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=PLAYER_BRIEFING_CHOICES_SCHEMA,
                max_tokens=4096,
            )
            logger.info(f"[DAY] Briefing generated for {player_id}")
            return parsed
        except Exception as e:
            logger.error(f"[DAY] Briefing generation failed for {player_id}: {e}")
            return {
                "briefing": f"As {player_role}, you assess the situation calmly.",
                "choices": [
                    {"id": "a1", "text": "Proceed with standard protocol", "consequence": "No significant change"},
                    {"id": "a2", "text": "Consult with colleagues", "consequence": "Gather more information"},
                    {"id": "a3", "text": "Wait and observe", "consequence": "Situation develops without your input"},
                ],
            }

    def analyze_combined_outcome(
        self,
        global_circumstances: Dict[str, Any],
        all_decisions: List[Dict[str, Any]],
        previous_summary: str = "",
    ) -> Dict[str, Any]:
        """Analyze all player and NPC choices together with their hidden consequences
        to produce a coherent combined outcome narrative.

        This is the third step — after all choices are made, the LLM
        synthesizes the results into a consistent story.
        """
        logger.info(f"[DAY] Analyzing combined outcome from {len(all_decisions)} decisions")

        decisions_text = ""
        for i, d in enumerate(all_decisions, 1):
            name = d.get("name", d.get("player_id", d.get("npc_key", f"Character {i}")))
            role = d.get("role", "")
            action = d.get("action_id", "")
            action_text = d.get("action_text", "")
            consequence = d.get("consequence", "")
            rationale = d.get("rationale", "")
            decisions_text += (
                f"\n--- Decision {i} ---\n"
                f"Character: {name} ({role})\n"
                f"Chose: {action_text} ({action})\n"
                f"Rationale: {rationale}\n"
                f"HIDDEN CONSEQUENCE: {consequence}\n"
            )

        setting = global_circumstances.get("setting", "")
        conflict = global_circumstances.get("conflict", "")
        narrative = global_circumstances.get("narrative", "")

        if self.language == "ru":
            system = (
                "Ты — Game Master космической игры. Ты анализируешь ВСЕ решения, принятые "
                "игроками и NPC, вместе с их СКРЫТЫМИ последствиями, и создаёшь единый "
                "связный результат дня."
            )
            user = (
                f"Общие обстоятельства:\n"
                f"Локация: {setting}\n"
                f"Конфликт: {conflict}\n"
                f"Описание: {narrative}\n\n"
                f"Принятые решения (включая скрытые последствия):\n{decisions_text}\n\n"
                "Проанализируй все решения вместе и создай единый связанный результат:\n"
                "1. outcome_narrative — что на самом деле произошло в результате всех решений (2-3 абзаца)\n"
                "2. ship_status_change — как изменилось состояние корабля\n"
                "3. crew_morale_change — как изменился моральный дух экипажа\n"
                "4. next_day_hook — зацепка для следующего дня\n\n"
                "Всё на русском языке."
            )
        else:
            system = (
                "You are a Game Master. You analyze ALL decisions made by "
                "players and NPCs together with their HIDDEN consequences, "
                "and produce a single coherent day outcome."
            )
            user = (
                f"Global circumstances:\n"
                f"Setting: {setting}\n"
                f"Conflict: {conflict}\n"
                f"Narrative: {narrative}\n\n"
                f"All decisions (including hidden consequences):\n{decisions_text}\n\n"
                "Analyze all decisions together and create a coherent combined result:\n"
                "1. outcome_narrative — what actually happened as a result (2-3 paragraphs)\n"
                "2. ship_status_change — how the ship's condition changed\n"
                "3. crew_morale_change — how crew morale shifted\n"
                "4. next_day_hook — teaser for the next day\n"
            )

        try:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=COMBINED_OUTCOME_SCHEMA,
                max_tokens=4096,
            )
            logger.info(f"[DAY] Combined outcome generated: {str(parsed.get('outcome_narrative', ''))[:80]}...")
            return parsed
        except Exception as e:
            logger.error(f"[DAY] Combined outcome analysis failed: {e}")
            return {
                "outcome_narrative": narrative or "The day passed without major incident.",
                "ship_status_change": "No significant change.",
                "crew_morale_change": "Stable.",
                "next_day_hook": "Tomorrow brings new challenges.",
            }

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
