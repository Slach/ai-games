"""
Game Server - Direct OpenAI API for game orchestration

Uses openai client with json_schema response_format for all LLM calls.
Compatible with llama.cpp / vLLM / any OpenAI-compatible endpoint.
"""

import json
import logging
import os
import re
from typing import Any, cast

from database import SHIP_ROLE_KEYS
from game_rules import normalize_mission, select_mission_seeds
from language import (
    LANGUAGE_EN,
    LANGUAGE_RU,
    get_dimension_tags,
    get_game_strings,
    get_gender_questions_data,
    get_species_questions_data,
)
from openai import OpenAI
from prompts import (
    COMBINED_OUTCOME_SCHEMA,
    GAME_OVER_SCHEMA,
    BACKGROUND_LOCATION_TYPES,
    build_auto_choice_prompts,
    build_background_prompts_system,
    build_background_prompts_user,
    build_combined_outcome_prompts,
    build_content_prompt_note,
    build_turn_story_prompts,
    build_dynamic_sg_question_prompts,
    build_game_over_prompts,
    build_game_title_prompts,
    build_global_circumstances_prompts,
    build_mission_prompts,
    build_npc_decision_prompts,
    build_npc_dialogue_lang_note,
    build_npc_name_system,
    build_npc_name_user,
    build_onboarding_prompts,
    build_personal_briefing_system,
    build_player_message_prompts,
    build_scene_instruction_system,
    build_scene_instruction_user,
    build_species_description_prompts,
)
from openai.types.chat import (
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.shared_params.response_format_json_schema import (
    ResponseFormatJSONSchema,
)
from pydantic import BaseModel

from verbalize_sampling import DIVERSITY_HINTS, repair_json, select_response, verbalize_prompt, vs_response_schema

logger = logging.getLogger(__name__)


def _safe_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        logger.warning("Invalid %s, using default %d", name, default)
        return default


# ============== Pydantic Models ==============


class GameStory(BaseModel):
    """Generated story for a game turn"""

    turn: int
    setting: str
    conflict: str
    narrative: str
    decision_points: list[dict[str, Any]]


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

    questions: list[dict[str, Any]]


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
                    "description": "The story description for the turn",
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
try:
    ONBOARDING_QUESTIONS_COUNT = int(os.getenv("ONBOARDING_QUESTIONS_COUNT", "5"))
except (ValueError, TypeError):
    logger.warning("Invalid ONBOARDING_QUESTIONS_COUNT, using default 5")
    ONBOARDING_QUESTIONS_COUNT = 5

try:
    ONBOARDING_OPTIONS_COUNT = int(os.getenv("ONBOARDING_OPTIONS_COUNT", "5"))
except (ValueError, TypeError):
    logger.warning("Invalid ONBOARDING_OPTIONS_COUNT, using default 5")
    ONBOARDING_OPTIONS_COUNT = 5

# Minimum ratio of second-place tag count to first-place for hybrid detection.
# E.g. 0.25 means if second species/gender tag has >= 25% of first-place votes,
# the character is considered a hybrid. Range: 0.0 (always hybrid) to 1.0 (only tie).
# Minimum ratio of second-place tag count to first-place for hybrid detection.
# Range: 0.0 (always hybrid) to 1.0 (only tie).
try:
    GAME_SPECIES_HYBRID_THRESHOLD = float(os.getenv("GAME_SPECIES_HYBRID_THRESHOLD", "0.25"))
except (ValueError, TypeError):
    logger.warning("Invalid GAME_SPECIES_HYBRID_THRESHOLD, using default 0.25")
    GAME_SPECIES_HYBRID_THRESHOLD = 0.25

try:
    GAME_GENDER_HYBRID_THRESHOLD = float(os.getenv("GAME_GENDER_HYBRID_THRESHOLD", "0.25"))
except (ValueError, TypeError):
    logger.warning("Invalid GAME_GENDER_HYBRID_THRESHOLD, using default 0.25")
    GAME_GENDER_HYBRID_THRESHOLD = 0.25


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
                                "image_prompt": {
                                    "type": "string",
                                    "description": "A detailed English image generation prompt that visualizes the EXACT SAME scenario described in the text field — same location, same situation, same characters/objects. Cinematic, sci-fi/space opera, 4K quality.",
                                },
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
                                                "description": "Full action description displayed to the player — e.g. 'Run to engineering and repair the warp drive'",
                                            },
                                            "role_scores": {
                                                "type": "object",
                                                "description": "Points awarded to each role when this option is selected. Keys are role_key strings, values are integers 0-3.",
                                                "properties": role_score_properties,
                                                "required": SHIP_ROLE_KEYS,
                                                "additionalProperties": False,
                                            },
                                        },
                                        "required": ["value", "role_scores"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["image_prompt", "text", "options"],
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


def _build_dynamic_sg_question_schema(dimension: str) -> dict:
    """Build a strict JSON schema for ONE dynamically generated species/gender question.

    Forces the model to return a label for EVERY canonical tag of the dimension,
    so the caller can attach tags programmatically without any cleanup.
    """
    tags = get_dimension_tags(dimension)
    label_properties = {tag: {"type": "string"} for tag in tags}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": f"dynamic_{dimension}_question",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "labels": {
                        "type": "object",
                        "properties": label_properties,
                        "required": tags,
                        "additionalProperties": False,
                    },
                },
                "required": ["text", "labels"],
                "additionalProperties": False,
            },
        },
    }


DYNAMIC_SPECIES_QUESTION_SCHEMA = _build_dynamic_sg_question_schema("species")
DYNAMIC_GENDER_QUESTION_SCHEMA = _build_dynamic_sg_question_schema("gender")

NPC_DIALOGUE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "crew_dialogue",
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

CHOSEN_ACTION_PROMPT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "chosen_action_prompt",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "chosen_action_prompt": {
                    "type": "string",
                    "description": (
                        "Detailed image generation prompt showing the character performing "
                        "their chosen action. Must describe: character appearance (from avatar "
                        "description), the specific action being performed, setting/environment, "
                        "cinematic composition and lighting, sci-fi space opera aesthetic, 4K quality."
                    ),
                }
            },
            "required": ["chosen_action_prompt"],
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
            "required": [
                "species_description",
                "gender_description",
                "combined_description",
            ],
            "additionalProperties": False,
        },
    },
}

SPECIES_OPTION_PROMPTS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "species_option_prompts",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "prompts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "option_value": {
                                "type": "string",
                                "description": "Exact option value from the question, returned AS-IS without any brackets, quotes, or extra formatting. Example: 's4_a' not '[s4_a]'",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "Short creative image prompt in English for Stable Diffusion, ~20-30 words, cinematic sci-fi style",
                            },
                        },
                        "required": ["option_value", "prompt"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["prompts"],
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
                    "description": "The shared story description for the turn from the GM's perspective. Include [avatar: role_key] markers when describing crew members (e.g. '[avatar: captain] stands at the helm, [avatar: chief_engineer] works in engineering')",
                },
                "key_events": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-5 key events happening in the background that all characters can perceive",
                },
                "scene_prompt": {
                    "type": "string",
                    "description": "A detailed English image generation prompt for this turn's scene. Must be cinematic, sci-fi/space opera, 4K quality. Describe the setting, crew at their positions, lighting, and atmosphere.",
                },
                "crew_positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role_key": {"type": "string"},
                            "position": {
                                "type": "string",
                                "description": "Where this crew member is and what they are doing",
                            },
                            "avatar_ref": {
                                "type": "string",
                                "description": "Marker like [avatar: role_key] that will be replaced with actual avatar URL",
                            },
                        },
                        "required": ["role_key", "position"],
                        "additionalProperties": False,
                    },
                    "description": "Positions of each crew member in the scene for avatar reference",
                },
            },
            "required": [
                "setting",
                "conflict",
                "narrative",
                "key_events",
                "scene_prompt",
                "crew_positions",
            ],
            "additionalProperties": False,
        },
    },
}

MISSION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "mission",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Mission name (e.g. 'First Contact at Proxima')",
                },
                "description": {
                    "type": "string",
                    "description": "Mission overview narrative (2-3 paragraphs)",
                },
                "short_description": {
                    "type": "string",
                    "description": "Condensed 1-2 sentence summary, no more than 500 characters — used for image captions with length limits",
                },
                "objectives": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "stage": {"type": "integer", "description": "Stage number"},
                            "name": {"type": "string", "description": "Stage name"},
                            "description": {
                                "type": "string",
                                "description": "What needs to be accomplished",
                            },
                            "success_threshold": {
                                "type": "integer",
                                "description": "How many progress points needed (1-10)",
                            },
                        },
                        "required": [
                            "stage",
                            "name",
                            "description",
                            "success_threshold",
                        ],
                        "additionalProperties": False,
                    },
                    "description": "Mission stages/objectives (2-4 stages)",
                },
            },
            "required": ["name", "description", "objectives"],
            "additionalProperties": False,
        },
    },
}


BRIDGE_IMAGE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "bridge_image_prompt",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "bridge_prompt": {
                    "type": "string",
                    "description": "A detailed English image prompt for the starship bridge scene with the full crew at their stations",
                },
                "crew_descriptions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "position_description": {
                                "type": "string",
                                "description": "Where this crew member is on the bridge and what they are doing",
                            },
                        },
                        "required": ["role", "position_description"],
                        "additionalProperties": False,
                    },
                    "description": "Descriptions of where each crew member is positioned on the bridge",
                },
            },
            "required": ["bridge_prompt", "crew_descriptions"],
            "additionalProperties": False,
        },
    },
}


BACKGROUND_PROMPTS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "background_prompts",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "backgrounds": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "location_type": {
                                "type": "string",
                                "description": "One of: " + ", ".join(BACKGROUND_LOCATION_TYPES),
                            },
                            "prompt": {
                                "type": "string",
                                "description": "Detailed English image prompt for the empty location (no characters)",
                            },
                        },
                        "required": ["location_type", "prompt"],
                        "additionalProperties": False,
                    },
                    "description": "One entry per location type",
                },
            },
            "required": ["backgrounds"],
            "additionalProperties": False,
        },
    },
}


SCENE_INSTRUCTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "scene_instruction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "English instruction for Qwen-Image-Edit, starting with 'Place the character from Picture 1...'",
                },
                "background_location": {
                    "type": ["string", "null"],
                    "description": "Best-matching location type for this scene, or null if no background applies",
                },
            },
            "required": ["instruction", "background_location"],
            "additionalProperties": False,
        },
    },
}


NPC_AVATAR_PROMPT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "npc_avatar_prompts",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "prompts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role_key": {"type": "string"},
                            "prompt": {
                                "type": "string",
                                "description": "Detailed English image prompt for this NPC's avatar, randomized for variety",
                            },
                        },
                        "required": ["role_key", "prompt"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["prompts"],
            "additionalProperties": False,
        },
    },
}


NPC_NAME_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "npc_name",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Full name for this NPC character. Must match their species, gender, and role. Creative and unique — not generic.",
                },
                "explanation": {
                    "type": "string",
                    "description": "Brief explanation of the name choice (1 sentence)",
                },
            },
            "required": ["name", "explanation"],
            "additionalProperties": False,
        },
    },
}


# ============== Game Server ==============


class GameServer:
    """
    Game Server agent using direct OpenAI API calls with json_schema
    structured outputs for all LLM interactions.
    """

    def __init__(self, language: str = "en"):
        self.llm_base_url = os.getenv("LLM_URL", "http://llama.cpp:8090/v1")
        self.llm_api_key = os.getenv("LLM_API_KEY", "placeholder-key-for-llama-cpp")
        self.llm_model = os.getenv("LLM_MODEL", "unsloth/Qwen3.5-27B")
        self.llm_max_tokens = _safe_int_env("LLM_MAX_TOKENS", 32768)
        self.llm_max_avatar_tokens = _safe_int_env("LLM_MAX_AVATAR_TOKENS", 4096)
        self.turn_good_actions = _safe_int_env("GAME_TURN_GOOD_ACTIONS", 3)
        self.turn_bad_actions = _safe_int_env("GAME_TURN_BAD_ACTIONS", 1)
        self.turn_neutral_actions = _safe_int_env("GAME_TURN_NEUTRAL_ACTIONS", 1)
        self.language = language
        self.npcs: dict[str, dict[str, Any]] = {}

        # Verbalized Sampling config
        self.vs_enabled = os.getenv("VS_ENABLED", "1") == "1"
        self.vs_k = _safe_int_env("VS_K", 5)
        self.vs_mode = os.getenv("VS_MODE", "full")

        self.client = OpenAI(
            api_key=self.llm_api_key,
            base_url=self.llm_base_url,
        )

        self._init_default_npcs()
        logger.info(f"GameServer initialized: model={self.llm_model}, language={language}, max_tokens={self.llm_max_tokens}")

        # Logging context — set by caller to enable compact LLM logging
        self._llm_game_id: str | None = None
        self._llm_player_id: str | None = None
        self._llm_turn: int | None = None
        self._llm_kind: str | None = None

    def _get_player_briefing_schema(self) -> dict[str, object]:
        """Build the player briefing JSON schema with dynamic maxItems."""
        total_actions = self.turn_good_actions + self.turn_bad_actions + self.turn_neutral_actions
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "player_briefing",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "personal_title": {
                            "type": "string",
                            "description": "A unique, atmospheric title for this player's personal turn introduction. Format: 'Ход {turn} — {role} — {personal_greeting}' (Russian) or 'Turn {turn} — {role} — {personal_greeting}' (English). The greeting MUST include the player's name and role.",
                        },
                        "briefing": {
                            "type": "string",
                            "description": "Personal narrative for this specific player — what they see, hear, and feel from their unique perspective",
                        },
                        "choices": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {
                                        "type": "string",
                                        "description": "Short unique identifier for this action, e.g. 'action_1', 'scan_hull', 'retreat'",
                                    },
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
                            "minItems": max(1, total_actions),
                            "maxItems": total_actions,
                            "description": "Action choices with hidden consequences for the player to pick from",
                        },
                    },
                    "required": ["personal_title", "briefing", "choices"],
                    "additionalProperties": False,
                },
            },
        }

    def _init_default_npcs(self):
        """Initialize default NPCs with distinct personalities"""
        self.npcs = {
            "captain": NPC_TEMPLATES["captain"].copy(),
            "pilot": NPC_TEMPLATES["pilot"].copy(),
            "engineer": NPC_TEMPLATES["engineer"].copy(),
            "communications": NPC_TEMPLATES["communications"].copy(),
        }

    def generate_team_npcs(self, player_role: str) -> dict[str, dict[str, Any]]:
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
        response_schema: dict[str, Any],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> dict[str, Any]:
        """Call LLM with json_schema structured output.

        Falls back to plain text + JSON extraction if the endpoint
        does not support response_format (e.g. older llama.cpp).

        When ``self._llm_kind`` is set (via the caller), writes full
        request/response to a dedicated log file under logs/ and logs
        only a compact one-line summary. Otherwise falls back to legacy
        verbose inline logging.

        Args:
            enable_thinking: If True, allows the LLM to use reasoning/thinking tokens
                before generating the final output.
        """
        from logging_utils import write_llm_log

        if max_tokens is None:
            max_tokens = self.llm_max_tokens
        messages: list[ChatCompletionSystemMessageParam | ChatCompletionUserMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        kind = self._llm_kind
        ctx_game = self._llm_game_id or "none"
        ctx_player = str(self._llm_player_id) if self._llm_player_id else ""
        ctx_turn = str(self._llm_turn) if self._llm_turn is not None else "0"

        if kind is not None:
            request_log = (
                f"Model: {self.llm_model}\n"
                f"Temperature: {temperature}\n"
                f"Max tokens: {max_tokens}\n"
                f"Enable thinking: {enable_thinking}\n"
                f"Response schema: {json.dumps(response_schema, indent=2, ensure_ascii=False)}\n\n"
                f"--- SYSTEM PROMPT ---\n{system_prompt}\n\n"
                f"--- USER PROMPT ---\n{user_prompt}"
            )
            write_llm_log(
                game_id=ctx_game,
                player_id=ctx_player,
                turn=ctx_turn,
                kind=kind,
                log_type="request",
                content=request_log,
            )
            prompt_len = len(system_prompt) + len(user_prompt)
            logger.info(
                "LLM [%s] game=%s player=%s turn=%s | model=%s temp=%.2f max_tok=%d thinking=%s prompt_len=%d",
                kind,
                ctx_game,
                ctx_player,
                ctx_turn,
                self.llm_model,
                temperature,
                max_tokens,
                enable_thinking,
                prompt_len,
            )
        else:
            logger.info("=== LLM REQUEST (structured) ===")
            logger.info(f"Model: {self.llm_model}")
            logger.info(f"Temperature: {temperature}")
            logger.info(f"Max tokens: {max_tokens}")
            logger.info(f"Enable thinking: {enable_thinking}")
            logger.info(f"Response schema: {json.dumps(response_schema, indent=2, ensure_ascii=False)}")
            logger.info("--- SYSTEM PROMPT ---")
            for line in system_prompt.split("\n"):
                logger.info(line)
            logger.info("--- USER PROMPT ---")
            for line in user_prompt.split("\n"):
                logger.info(line)
            logger.info("=== END LLM REQUEST ===")

        extra_body = {"chat_template_kwargs": {"enable_thinking": enable_thinking}}
        response = None
        try:
            # Try structured output first
            response = self.client.chat.completions.create(
                model=self.llm_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=cast(ResponseFormatJSONSchema, response_schema),
                extra_body=extra_body,
            )
            content = response.choices[0].message.content
            finish_reason = response.choices[0].finish_reason
            _u = response.usage

            if kind is not None:
                response_log = f"Finish reason: {finish_reason}\nUsage: {_u.prompt_tokens if _u else 0}p/{_u.completion_tokens if _u else 0}c/{_u.total_tokens if _u else 0}t\n\n--- RESPONSE CONTENT ---\n{content or ''}"
                write_llm_log(
                    game_id=ctx_game,
                    player_id=ctx_player,
                    turn=ctx_turn,
                    kind=kind,
                    log_type="response",
                    content=response_log,
                )
                logger.info(
                    "LLM [%s] OK game=%s player=%s turn=%s | finish=%s prompt_tok=%d compl_tok=%d total_tok=%d",
                    kind,
                    ctx_game,
                    ctx_player,
                    ctx_turn,
                    finish_reason,
                    _u.prompt_tokens if _u else 0,
                    _u.completion_tokens if _u else 0,
                    _u.total_tokens if _u else 0,
                )
            else:
                logger.info("=== LLM RESPONSE (structured) ===")
                logger.info(f"Finish reason: {finish_reason}")
                if response.usage:
                    logger.info(f"Usage: prompt_tokens={response.usage.prompt_tokens}, completion_tokens={response.usage.completion_tokens}, total_tokens={response.usage.total_tokens}")
                logger.info("--- RESPONSE CONTENT ---")
                for line in (content or "").split("\n"):
                    logger.info(line)
                logger.info("=== END LLM RESPONSE ===")

            if content is None:
                raise ValueError(f"LLM returned content=None. Finish reason: {finish_reason}. Usage: {_u}")
            return json.loads(content)

        except Exception as e:
            logger.warning(f"Structured output failed ({e}), falling back to plain JSON extraction")

            json_instruction = "\n\nIMPORTANT: Return ONLY valid JSON. No markdown, no code blocks, no explanation. Pure JSON only."

            if kind is not None:
                logger.info(
                    "LLM [%s] FALLBACK game=%s player=%s turn=%s | structured output failed: %s",
                    kind,
                    ctx_game,
                    ctx_player,
                    ctx_turn,
                    e,
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
            _u = response.usage

            if kind is not None:
                response_log = f"=== FALLBACK ===\nFinish reason: {finish_reason}\nUsage: {_u.prompt_tokens if _u else 0}p/{_u.completion_tokens if _u else 0}c/{_u.total_tokens if _u else 0}t\n\n--- RESPONSE CONTENT ---\n{content or ''}"
                write_llm_log(
                    game_id=ctx_game,
                    player_id=ctx_player,
                    turn=ctx_turn,
                    kind=kind,
                    log_type="response",
                    content=response_log,
                )
                logger.info(
                    "LLM [%s] FALLBACK OK game=%s player=%s turn=%s | finish=%s prompt_tok=%d compl_tok=%d",
                    kind,
                    ctx_game,
                    ctx_player,
                    ctx_turn,
                    finish_reason,
                    _u.prompt_tokens if _u else 0,
                    _u.completion_tokens if _u else 0,
                )
            else:
                logger.info("=== LLM RESPONSE (fallback text) ===")
                logger.info(f"Finish reason: {finish_reason}")
                logger.info("--- RESPONSE CONTENT ---")
                for line in (content or "").split("\n"):
                    logger.info(line)
                logger.info("=== END LLM RESPONSE (fallback) ===")

            if content is None:
                raise ValueError(f"LLM returned empty content on fallback. Finish reason: {finish_reason}. Raw response:\n{str(response)}") from e
            if content.strip() == "":
                raise ValueError(f"LLM returned empty content on fallback. Finish reason: {finish_reason}. Raw response:\n{str(response)}") from e

            content = content.strip()
            content = self._strip_json_block(content)
            try:
                return json.loads(content)
            except json.JSONDecodeError as parse_err:
                repaired = repair_json(content)
                if repaired != content:
                    logger.info("Attempting JSON repair on fallback response...")
                if repaired != content:
                    try:
                        return json.loads(repaired)
                    except json.JSONDecodeError:
                        pass
                if repaired != content:
                    logger.warning("JSON repair did not help, still unparsable")
                logger.error(f"Fallback JSON parse failed: {parse_err}\nRaw content:\n{content}\nFinish reason: {finish_reason}", exc_info=True)
                raise

    _JSON_BLOCK_PATTERNS = (
        re.compile(r"\{.*\}", re.DOTALL),
        re.compile(r"\[.*\]", re.DOTALL),
    )

    @staticmethod
    def _strip_json_block(text: str) -> str:
        """Remove markdown code blocks and extract JSON."""
        # Remove markdown code blocks
        cleaned = re.sub(r"```(?:json)?\s*", "", text)
        cleaned = re.sub(r"\s*```", "", cleaned)

        # Try to find JSON object or array
        for pat in GameServer._JSON_BLOCK_PATTERNS:
            match = pat.search(cleaned)
            if match:
                return match.group()

        return cleaned.strip()

    # ============== Onboarding ==============

    def generate_onboarding_questions(
        self,
        underrepresented_hint: str = "",
    ) -> list[dict[str, Any]]:
        """Generate dynamic onboarding questions using LLM with json_schema.

        Args:
            underrepresented_hint: Optional hint about which roles need more
                attention based on recent onboarding history.
                Example: "navigator, communications_officer, xenobiologist"
        """
        logger.info(f"Generating onboarding questions, language: {self.language}, hint: {underrepresented_hint or 'none'}")

        questions_count = ONBOARDING_QUESTIONS_COUNT
        options_count = ONBOARDING_OPTIONS_COUNT
        role_keys_str = ", ".join(SHIP_ROLE_KEYS)
        # Build example role_scores dynamically from actual role keys
        _example = {k: (3 if k == "chief_engineer" else 1 if k in ("science_officer", "pilot") else 0) for k in SHIP_ROLE_KEYS}
        example_role_scores_json = json.dumps(_example, ensure_ascii=False)

        system, user = build_onboarding_prompts(
            self.language,
            questions_count,
            options_count,
            role_keys_str,
            example_role_scores_json,
            underrepresented_hint,
        )
        # Note: build_onboarding_prompts returns (system, user); user is identical below
        _system = system

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
            seen_values = set()
            unique_options = []
            for opt in options:
                value = opt.get("value", "")
                # Skip duplicate values
                if value in seen_values:
                    continue
                # Skip overly short values (single letters, "A", "B", etc.)
                if len(value.strip()) < 5:
                    logger.warning(f"Skipping short option value: '{value}' in question: {q.get('text', '')}")
                    continue
                seen_values.add(value)
                unique_options.append(opt)

            # If we filtered out too many, keep original options
            if len(unique_options) < 2 and len(options) >= 2:
                logger.warning(f"Question had invalid options, using original: {q.get('text', '')}")
                unique_options = options

            q["options"] = unique_options

        for i, q in enumerate(questions, start=1):
            q["id"] = i

        logger.info(f"Generated {len(questions)} onboarding questions")
        return questions

    def assign_role_from_answers(
        self,
        answers: dict[int, str],
        available_roles: list[dict[str, Any]],
        questions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
        logger.info(f"[ROLE] Assigning role from {len(answers)} answers, {len(available_roles)} roles available, questions provided: {questions is not None}")

        if not available_roles:
            raise ValueError("No roles available")

        # Build role scores from answer selections
        role_points: dict[str, int] = dict.fromkeys(SHIP_ROLE_KEYS, 0)

        if questions:
            # Build lookup: question_id -> question data
            question_map = {q.get("id"): q for q in questions}

            for question_id, selected_label in answers.items():
                # Answers dict keys are strings after json.loads from DB (SQLite JSON stores all keys as strings)
                try:
                    qid = int(question_id) if not isinstance(question_id, int) else question_id
                except (ValueError, TypeError):
                    logger.warning("[ROLE] Invalid question_id %r, skipping", question_id)
                    continue
                q_data = question_map.get(qid)
                if not q_data:
                    logger.warning(f"[ROLE] Question {question_id} (type={type(question_id).__name__}) not found in session data")
                    continue

                # Find the selected option by matching value
                selected_option = None
                for opt in q_data.get("options", []):
                    if opt.get("value") == selected_label:
                        selected_option = opt
                        break

                if not selected_option:
                    logger.warning(f"[ROLE] Answer '{selected_label}' not found in options for Q{question_id}")
                    continue

                # Add role_scores from the selected option
                scores = selected_option.get("role_scores", {})
                for role_key, points in scores.items():
                    if role_key in role_points:
                        try:
                            role_points[role_key] += int(points)
                        except (ValueError, TypeError):
                            logger.warning("[ROLE] Invalid points %r for role %s, skipping", points, role_key)

        # Sort available roles by their accumulated points (descending)
        available_keys = {r["role_key"] for r in available_roles}
        scored_available = [(key, role_points.get(key, 0)) for key in sorted(role_points.keys(), key=lambda k: role_points[k], reverse=True) if key in available_keys]

        if not scored_available:
            # Fallback: pick first available
            best_key = available_roles[0]["role_key"]
            logger.warning(f"[ROLE] No scored roles available, falling back to {best_key}")
        else:
            best_key, best_score = scored_available[0]

        # Build reasoning string
        top_roles = sorted(role_points.items(), key=lambda x: x[1], reverse=True)
        reasoning = "Points: " + ", ".join(f"{k}={v}" for k, v in top_roles)

        logger.info(f"[ROLE] Point-based assignment: role_key={best_key}, {reasoning}")

        return {
            "role_key": best_key,
            "reasoning": reasoning,
            "role_points": role_points,
        }

    def generate_game_title(self) -> dict[str, str]:
        """Generate a creative game title and welcome message."""
        logger.info(f"[TITLE] Generating game title, language: {self.language}")

        system, user = build_game_title_prompts(self.language, use_vs=self.vs_enabled, vs_k=self.vs_k)

        if self.vs_enabled:
            vs_result = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=vs_response_schema(GAME_TITLE_SCHEMA),
                temperature=0.9,
            )
            chosen = select_response(vs_result["responses"], self.vs_mode)
            logger.info("[VS-TITLE] Selected p=%.3f", chosen["probability"])
            result = chosen["text"]
        else:
            result = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=GAME_TITLE_SCHEMA,
                temperature=0.9,
            )

        logger.info(f"[TITLE] Generated: {result.get('title', '')}")
        return result

    # ============== Daily Story ==============

    def generate_turn_story(self, turn: int, previous_summary: str = "", player_role: str = "") -> GameStory:
        """Generate daily story using LLM with json_schema."""
        logger.info(f"[STORY] Starting story generation for Turn {turn}, language: {self.language}")

        system, user = build_turn_story_prompts(self.language, turn, previous_summary, player_role, use_vs=self.vs_enabled, vs_k=self.vs_k)

        if self.vs_enabled:
            vs_result = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=vs_response_schema(STORY_SCHEMA),
                max_tokens=8192,
            )
            chosen = select_response(vs_result["responses"], self.vs_mode)
            logger.info("[VS-STORY] Selected p=%.3f", chosen["probability"])
            parsed = chosen["text"]
        else:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=STORY_SCHEMA,
                max_tokens=4096,
            )

        story = GameStory(
            turn=turn,
            setting=parsed.get("setting", ""),
            conflict=parsed.get("conflict", ""),
            narrative=parsed.get("narrative", ""),
            decision_points=parsed.get("decision_points", []),
        )
        logger.info(f"[STORY] Story generated: setting='{story.setting}...', {len(story.decision_points)} actions")
        return story

    # ============== NPC Dialogues ==============

    def generate_crew_dialogues(
        self,
        story: GameStory,
        player_role: str,
        crew_members: list[dict[str, Any]] | None = None,
    ) -> list[NPCDialogue]:
        """Generate NPC dialogues for the turn.

        Args:
            story: The current turn's story context
            player_role: Role of the player receiving the dialogues
            crew_members: Optional list of actual crew profiles from the database.
                Each dict should have 'name' (display name) and 'role'.
                When provided, replaces the default NPC_TEMPLATES system.
        """
        logger.info(f"[NPC] Starting NPC dialogue generation, language: {self.language}")

        lang_note, player_role_display = build_npc_dialogue_lang_note(self.language, player_role)

        if crew_members:
            # Use real crew profiles from the database
            dialogue_targets = []
            for m in crew_members:
                name = m.get("name") or m.get("npc_name") or m.get("player_name") or m.get("role", "Crew")
                role = m.get("role", "Crew Member")
                species = m.get("species", "") or ""
                traits = m.get("personality_traits", []) or []
                if isinstance(traits, str):
                    traits = []
                traits_str = ", ".join(traits) if traits else "professional"
                species_str = f" ({species})" if species else ""
                dialogue_targets.append(
                    {
                        "key": name,
                        "name": name,
                        "role": role,
                        "personality": traits_str,
                        "species": species_str,
                    }
                )
        else:
            # Fallback: use default NPC templates
            team_npcs = self.generate_team_npcs(player_role)
            dialogue_targets = []
            for npc_key, npc in team_npcs.items():
                npc_name = npc.get("name", npc.get("default_name", "Unknown"))
                dialogue_targets.append(
                    {
                        "key": npc_key,
                        "name": npc_name,
                        "role": npc["role"],
                        "personality": npc["personality"],
                        "speech_style": npc.get("speech_style", "direct"),
                    }
                )

        dialogues = []
        for target in dialogue_targets:
            try:
                npc_name = target["name"]
                npc_role = target["role"]
                logger.info(f"[NPC] Generating dialogue for {npc_name} ({npc_role})")

                if "speech_style" in target:
                    personality_block = f"Personality: {target['personality']}\nSpeech style: {target['speech_style']}\n"
                else:
                    personality_block = f"Personality: {target['personality']}\nSpecies: {target.get('species', '')}\n"

                system = f"You are {npc_name}, {npc_role}.\n{personality_block}{lang_note}"
                user = f"Game context: {story.narrative}\nPlayer role: {player_role_display}\n\nGenerate a short in-character reaction (1-2 sentences)."

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
                        npc_role=npc_role,
                        dialogue=parsed.get("dialogue", ""),
                        emotion=parsed.get("emotion", "neutral"),
                    )
                )
            except Exception as e:
                err_name = target.get("name", target.get("key", "?"))
                logger.error(f"[NPC] Dialogue generation failed for {err_name}: {e}", exc_info=True)
                raise

        logger.info(f"[NPC] Generated {len(dialogues)} NPC dialogues")
        return dialogues

    # ============== Content Prompts ==============

    def generate_content_prompts(self, story: GameStory, dialogues: list[NPCDialogue], player_role: str) -> ContentPrompts:
        """Generate prompts for content generation (image, video, comic)."""
        logger.info(f"[CONTENT] Starting content prompt generation, language: {self.language}")

        lang_note = build_content_prompt_note(self.language)

        system = "You are an AI art prompt engineer. Generate detailed, high-quality prompts for image/video generation."
        user = f"Story: {story.narrative}\nPlayer role: {player_role}\n\nGenerate content prompts for image, video, 3D scene, and comic strip.\n{lang_note}"

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

    def _call_llm_text(self, system_prompt: str, user_prompt: str) -> str:
        """Simple text completion without structured output — last-resort fallback."""

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.llm_model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0.7,
                max_tokens=1024,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            content = response.choices[0].message.content
            return content or "Game Master received your message."
        except Exception as e:
            logger.error(f"_call_llm_text failed: {e}", exc_info=True)
            return "Game Master received your message."

    def process_player_message(self, player_id: int, message: str, player_profile: dict[str, Any], game_context: dict[str, Any] | None = None) -> str:
        """Process a player message and generate Game Master response.

        Args:
            player_id: Telegram user ID
            message: Player's text message
            player_profile: Dict with role, personality_traits, etc.
            game_context: Optional dict with game_title, mission_name,
                mission_description, mission_objectives, turn,
                previous_turn_summary, global_circumstances_setting,
                global_circumstances_conflict, global_circumstances_narrative,
                crew_context.
        """
        ctx = game_context or {}

        system, user = build_player_message_prompts(
            language=self.language,
            player_name=player_profile.get("player_name", ""),
            player_role=player_profile.get("role", "Crew Member"),
            player_traits=player_profile.get("personality_traits", []),
            message=message,
            game_title=ctx.get("game_title", ""),
            mission_name=ctx.get("mission_name", ""),
            mission_description=ctx.get("mission_description", ""),
            mission_objectives=ctx.get("mission_objectives", ""),
            turn=ctx.get("turn", 1),
            previous_turn_summary=ctx.get("previous_turn_summary", ""),
            global_circumstances_setting=ctx.get("global_circumstances_setting", ""),
            global_circumstances_conflict=ctx.get("global_circumstances_conflict", ""),
            global_circumstances_narrative=ctx.get("global_circumstances_narrative", ""),
            crew_context=ctx.get("crew_context", ""),
            use_vs=self.vs_enabled,
            vs_k=self.vs_k,
        )

        try:
            if self.vs_enabled:
                vs_result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=vs_response_schema(PLAYER_MESSAGE_SCHEMA),
                    max_tokens=2048,
                )
                chosen = select_response(vs_result["responses"], self.vs_mode)
                logger.info("[VS-PLAYERMSG] Selected p=%.3f", chosen["probability"])
                parsed = chosen["text"]
                return parsed.get("response", "")
            else:
                parsed = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=PLAYER_MESSAGE_SCHEMA,
                    max_tokens=1024,
                )
                return parsed.get("response", "Game Master received your message.")
        except Exception as e:
            logger.error(f"Message processing failed: {e}", exc_info=True)
            return self._call_llm_text(system, user)

    # ============== Avatar Prompt ==============

    def _species_prompt_instructions(self, category: str) -> dict:
        """Return (focus_instructions, framing) for a given species category.

        ``genre`` controls the stylistic frame handed to the LLM: human and
        humanoid keep the Star Trek uniformed-portrait look, while non-humanoid,
        energy, and symbiotic beings are framed as alien concept art instead —
        the Trek/uniform framing is a strong humanoid prior that collapses
        alien anatomy back into "two arms, two legs, a head".
        """
        prompts = {
            "human": {
                "intro": "character avatar",
                "appearance": "- Character appearance (face, expression, uniform details)",
                "framing": "- Portrait style, upper body",
                "genre": "Star Trek space opera",
            },
            "humanoid": {
                "intro": "humanoid alien character avatar",
                "appearance": "- Character appearance: humanoid anatomy with subtle alien features (unusual skin/hair/eye color, distinct ears/ridges, etc.)",
                "framing": "- Portrait style, upper body",
                "genre": "Star Trek space opera",
            },
            "non_humanoid": {
                "intro": "non-humanoid alien creature",
                "appearance": (
                    "- The creature's ACTUAL physical form from the description — alien anatomy "
                    "(tentacles, carapace, exoskeleton, crystalline structure, gas sac, hive cluster, etc.)\n"
                    "- The creature must NOT resemble a human: NO two arms ending in hands, "
                    "NO two legs, NO human face or hair, NOT a bipedal humanoid silhouette\n"
                    "- Limbs, sensory organs, and body shape must match the described alien biology\n"
                    "- The creature does not wear a uniform"
                ),
                "framing": "- Full body or 3/4 view showing the alien physiology",
                "genre": "alien creature concept art, NOT a Star Trek uniformed officer",
            },
            "energy": {
                "intro": "energy being",
                "appearance": (
                    "- The being's form as energy, plasma, or light — NO solid physical body\n"
                    "- Describe the visual signature: glow, frequency patterns, luminosity, spatial presence\n"
                    "- Must NOT resemble a human: NO solid body, NO face, NO limbs, NO two arms/two legs\n"
                    "- The being does not wear a uniform or clothing"
                ),
                "framing": "- Full body showing the energy form in its environment",
                "genre": "abstract energy-being concept art, NOT a Star Trek uniformed officer",
            },
            "cybernetic": {
                "intro": "cybernetic / synthetic character",
                "appearance": (
                    "- The character's mechanical/cybernetic body — "
                    "metal, circuits, synthetic components, digital displays\n"
                    "- If part-organic, highlight the blend of biological and mechanical\n"
                    "- Describe the technological aesthetic of their form"
                ),
                "framing": "- Full body or 3/4 view showing the mechanical/cybernetic anatomy",
                "genre": "cyberpunk sci-fi concept art",
            },
            "symbiotic": {
                "intro": "symbiotic / composite creature",
                "appearance": (
                    "- The creature as a composite of multiple organisms or entities — "
                    "describe how the different parts coexist in one form\n"
                    "- Highlight the hybrid nature: textures, connections, shared biology\n"
                    "- Must NOT default to a single humanoid body, NO two arms/two legs/human face\n"
                    "- The creature does not wear a uniform"
                ),
                "framing": "- Full body view showing the composite/symbiotic nature",
                "genre": "alien creature concept art, NOT a Star Trek uniformed officer",
            },
        }
        return prompts.get(category, prompts["human"])

    def generate_avatar_prompt(
        self,
        role: str,
        traits: list[str],
        avatar_description: str,
        species_category: str = "",
    ) -> str:
        """Generate an image prompt for player avatar using LLM with json_schema.

        Args:
            role: Player's role on the ship
            traits: Personality traits
            avatar_description: Full avatar visual description
            species_category: Species category from onboarding (human, humanoid,
                              non_humanoid, energy, cybernetic, symbiotic).
                              Empty string = fallback to 'human'.
        """
        logger.info(f"[AVATAR] Generating avatar prompt for role: {role}")

        species_cat = species_category or "human"
        logger.info(f"[AVATAR] Using species category: {species_cat}")
        instr = self._species_prompt_instructions(species_cat)

        system = (
            "You are an expert AI art prompt engineer specializing in sci-fi character portraits. "
            "Generate detailed, cinematic-quality image prompts for character avatars.\n\n"
            "CRITICAL RULE: The character description below is the DEFINITIVE source for the "
            "character's appearance. If it describes an alien, non-humanoid, energy, cybernetic, "
            "or symbiotic being — describe their ACTUAL form, NOT human anatomy.\n"
            'Never default to "face, hair, eyes, upper body" for non-human characters.\n\n'
            "For non-humanoid, energy, and symbiotic beings: invent an appropriate non-human "
            "biological identity (reproductive cycle, colonial structure, plasma resonance, etc.) "
            "that fits their physiology. Do NOT impose human gender concepts (male/female) on "
            "beings whose biology would not have them."
        )

        user = (
            f"Generate an image prompt for a {instr['genre']} {instr['intro']}.\n"
            f"Role: {role}\n"
            f"Personality traits: {', '.join(traits)}\n"
            f"Character description (definitive source): {avatar_description}\n\n"
            "The prompt should describe:\n"
            f"{instr['appearance']}\n"
            "- Environment setting (ship interior, lab, planet surface, etc.)\n"
            "- Cinematic lighting and composition appropriate to the character\n"
            f"- {instr['genre']} aesthetic\n"
            "- High quality, 4K, detailed\n"
            f"{instr['framing']}\n"
            "Write the prompt in English."
        )

        if self.vs_enabled:
            vs_system, vs_user = verbalize_prompt(system, user, DIVERSITY_HINTS["avatar"], k=self.vs_k)
            parsed = self._call_llm(
                system_prompt=vs_system,
                user_prompt=vs_user,
                response_schema=vs_response_schema(AVATAR_PROMPT_SCHEMA),
                max_tokens=self.llm_max_avatar_tokens,
            )
            chosen = select_response(parsed["responses"], self.vs_mode)
            inner = chosen["text"]
            avatar_prompt = inner.get("avatar_prompt", "")
            logger.info("[VS-AVATAR] Selected p=%.3f", chosen["probability"])
        else:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=AVATAR_PROMPT_SCHEMA,
                max_tokens=self.llm_max_avatar_tokens,
            )
            avatar_prompt = parsed.get("avatar_prompt", "")
        logger.info(f"[AVATAR] Avatar prompt generated ({species_cat}): {avatar_prompt}...")
        return avatar_prompt

    def generate_chosen_action_prompt(
        self,
        role: str,
        traits: list[str],
        avatar_description: str,
        action_text: str,
        setting: str,
        species_desc: str = "",
        species_type: str = "",
        species_category: str = "",
    ) -> str:
        """Generate an image prompt for the chosen action scene using LLM.

        Produces a prompt in the same cinematic style as avatar prompts,
        showing the character performing the selected action.

        Args:
            role: Player's role on the ship
            traits: Personality traits
            avatar_description: Full avatar visual description
            action_text: The action the player chose to perform
            setting: Story setting / environment
            species_desc: Extra species appearance details
            species_type: Species type label
            species_category: Species category from onboarding (human, humanoid,
                              non_humanoid, energy, cybernetic, symbiotic).
                              Empty string = fallback to 'human'.

        Returns:
            LLM-generated image prompt string
        """
        logger.info(f"[ACTION_PROMPT] Generating chosen action prompt for {role}")

        species_cat = species_category or "human"
        instr = self._species_prompt_instructions(species_cat)

        # Combine avatar context for character appearance
        avatar_context = avatar_description or ""
        if species_type and species_type not in ("Unknown", "Неизвестно"):
            avatar_context += f"\nSpecies type: {species_type}"
        if species_desc:
            avatar_context += f"\nAppearance details: {species_desc}"

        system = (
            "You are an expert AI art prompt engineer specializing in sci-fi character portraits "
            "and action scenes. Generate detailed, cinematic-quality image prompts that show "
            "a character ACTIVELY PERFORMING an action (not just posing).\n\n"
            "CRITICAL RULE: The character description below is the DEFINITIVE source for the "
            "character's appearance. If it describes an alien, non-humanoid, energy, cybernetic, "
            "or symbiotic being — describe their ACTUAL form, NOT human anatomy.\n"
            'Never default to "face, hair, eyes, upper body" for non-human characters.\n\n'
            "The prompt must match the visual style of the character's existing avatar — "
            "use the SAME aesthetic, NOT a comic book or cartoon style."
        )

        user = (
            f"Generate an image prompt showing a Star Trek-style {instr['intro']} "
            f"PERFORMING a specific action.\n\n"
            f"Role: {role}\n"
            f"Personality traits: {', '.join(traits)}\n"
            f"Character appearance (definitive source): {avatar_context}\n\n"
            f"ACTION TO PERFORM: {action_text}\n\n"
            f"Setting: {setting[:300]}\n\n"
            "The prompt should describe:\n"
            f"{instr['appearance']}\n"
            f"- The character ACTIVELY PERFORMING the action described above\n"
            "- Environment matching the setting (ship interior, planet surface, etc.)\n"
            "- Dynamic composition showing the action in progress\n"
            "- Cinematic lighting and camera angle appropriate to the scene\n"
            "- Sci-fi/space opera aesthetic (NOT comic book style)\n"
            "- High quality, 4K, detailed\n"
            f"{instr['framing']}\n\n"
            "IMPORTANT: This is an action SCENE image, NOT a comic panel. "
            "Use the same cinematic photorealistic style as the character's avatar. "
            "Do NOT add comic book effects, panel borders, speech bubbles, or halftone dots."
        )

        if self.vs_enabled:
            vs_system, vs_user = verbalize_prompt(system, user, DIVERSITY_HINTS["action_prompt"], k=self.vs_k)
            parsed = self._call_llm(
                system_prompt=vs_system,
                user_prompt=vs_user,
                response_schema=vs_response_schema(CHOSEN_ACTION_PROMPT_SCHEMA),
                max_tokens=self.llm_max_avatar_tokens,
            )
            chosen = select_response(parsed["responses"], self.vs_mode)
            inner = chosen["text"]
            prompt = inner.get("chosen_action_prompt", "")
            logger.info("[VS-ACTION] Selected p=%.3f", chosen["probability"])
        else:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=CHOSEN_ACTION_PROMPT_SCHEMA,
                max_tokens=self.llm_max_avatar_tokens,
            )
            prompt = parsed.get("chosen_action_prompt", "")
        logger.info(f"[ACTION_PROMPT] Generated for {role}: {prompt[:120]}...")
        return prompt

    # ============== Species and Gender ==============

    def generate_dynamic_species_gender_question(
        self,
        dimension: str,
        sg_step: int,
        accumulated_tags: dict[str, int],
    ) -> dict[str, Any]:
        """Generate ONE species or gender onboarding question via LLM.

        Returns {"text": ..., "labels": {tag: label}} where labels covers every
        canonical tag of the dimension. Tags themselves are NOT chosen by the LLM
        — the caller assigns them in canonical order, which keeps the tag-counting
        species/gender determination logic reliable.

        Falls back to a static question from SPECIES/GENDER_QUESTIONS_DATA on failure.
        """
        logger.info(f"[SG_Q] Generating dynamic {dimension} question step {sg_step}, accumulated={accumulated_tags}")
        system, user = build_dynamic_sg_question_prompts(self.language, dimension, sg_step, accumulated_tags)
        schema = DYNAMIC_SPECIES_QUESTION_SCHEMA if dimension == "species" else DYNAMIC_GENDER_QUESTION_SCHEMA
        try:
            result = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=schema,
                temperature=0.9,
                max_tokens=1024,
            )
            text = (result.get("text") or "").strip()
            if text:
                tags = get_dimension_tags(dimension)
                raw_labels = result.get("labels") or {}
                labels = {tag: str(raw_labels.get(tag, tag)).strip() for tag in tags}
                logger.info(f"[SG_Q] LLM question ok: {text[:60]}...")
                return {"text": text, "labels": labels}
            logger.warning("[SG_Q] LLM returned empty text, using fallback")
        except Exception as e:
            logger.warning(f"[SG_Q] LLM call failed: {e}, using fallback")
        return self._fallback_dynamic_sg_question(dimension, sg_step)

    def _fallback_dynamic_sg_question(self, dimension: str, sg_step: int) -> dict[str, Any]:
        """Pick a static species/gender question as fallback and derive per-tag labels."""
        if dimension == "species":
            pool = get_species_questions_data(self.language)
            idx = sg_step // 2  # species steps 1,3,5 -> 0,1,2
        else:
            pool = get_gender_questions_data(self.language)
            idx = (sg_step // 2) - 1  # gender steps 2,4 -> 0,1
        idx = max(0, min(idx, len(pool) - 1))
        text = pool[idx]["text"]
        tag_field = "species_tags" if dimension == "species" else "gender_tags"
        labels: dict[str, str] = {}
        for q in pool:
            for opt in q["options"]:
                for tag in opt.get(tag_field, []):
                    labels.setdefault(tag, opt["label"])
        tags = get_dimension_tags(dimension)
        logger.info(f"[SG_Q] fallback {dimension} step {sg_step}: {text[:60]}...")
        return {"text": text, "labels": {tag: labels.get(tag, tag) for tag in tags}}

    @staticmethod
    def _count_tags_from_answers(
        answers: dict[int, str],
        tag_key: str,
        questions: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Count occurrences of a given tag type across all answered questions."""
        if not questions:
            return {}

        question_map = {q.get("id"): q for q in questions}
        tag_counts: dict[str, int] = {}

        for question_id, selected_value in answers.items():
            try:
                qid = int(question_id) if not isinstance(question_id, int) else question_id
            except (ValueError, TypeError):
                continue
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
            tags = selected_option.get(tag_key, [])
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return tag_counts

    @staticmethod
    def calculate_species_from_answers(
        answers: dict[int, str],
        questions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Calculate species type by counting species_tags across answers.

        Returns dict with primary species, secondary (for hybrid), and hybrid flag.
        """
        tag_counts = GameServer._count_tags_from_answers(answers, "species_tags", questions)
        if not tag_counts:
            return {"primary": "", "secondary": "", "hybrid": False}

        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        primary = sorted_tags[0][0]
        primary_count = sorted_tags[0][1]
        secondary = ""
        hybrid = False
        if len(sorted_tags) > 1:
            second_count = sorted_tags[1][1]
            if second_count == primary_count or (second_count >= max(2, primary_count * GAME_SPECIES_HYBRID_THRESHOLD)):
                secondary = sorted_tags[1][0]
                hybrid = True

        return {"primary": primary, "secondary": secondary, "hybrid": hybrid}

    @staticmethod
    def calculate_gender_from_answers(
        answers: dict[int, str],
        questions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Calculate gender type by counting gender_tags across answers.

        Returns dict with primary gender, secondary (for hybrid), and hybrid flag.
        """
        tag_counts = GameServer._count_tags_from_answers(answers, "gender_tags", questions)
        if not tag_counts:
            return {"primary": "", "secondary": "", "hybrid": False}

        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        primary = sorted_tags[0][0]
        primary_count = sorted_tags[0][1]
        secondary = ""
        hybrid = False
        if len(sorted_tags) > 1:
            second_count = sorted_tags[1][1]
            if second_count == primary_count or (second_count >= max(2, primary_count * GAME_GENDER_HYBRID_THRESHOLD)):
                secondary = sorted_tags[1][0]
                hybrid = True

        return {"primary": primary, "secondary": secondary, "hybrid": hybrid}

    def generate_species_gender_description(
        self,
        species_result: dict[str, Any],
        gender_result: dict[str, Any],
        role: str,
    ) -> str:
        """Generate a vivid narrative description of the player's species and gender using LLM."""
        logger.info(f"[SPECIES] Generating species+gender description for role: {role}")

        species_display = species_result.get("primary", "unknown")
        gender_display = gender_result.get("primary", "undefined")
        species_hybrid = species_result.get("hybrid", False)
        species_secondary = species_result.get("secondary", "")
        gender_hybrid = gender_result.get("hybrid", False)
        gender_secondary = gender_result.get("secondary", "")

        system, user = build_species_description_prompts(
            self.language,
            role,
            species_display,
            species_secondary,
            species_hybrid,
            gender_display,
            gender_secondary,
            gender_hybrid,
            use_vs=self.vs_enabled,
            vs_k=self.vs_k,
        )

        try:
            if self.vs_enabled:
                vs_result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=vs_response_schema(SPECIES_GENDER_DESC_SCHEMA),
                    temperature=0.8,
                    max_tokens=2048,
                )
                chosen = select_response(vs_result["responses"], self.vs_mode)
                parsed = chosen["text"]
            else:
                parsed = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=SPECIES_GENDER_DESC_SCHEMA,
                    temperature=0.8,
                    max_tokens=1024,
                )
            species_desc = parsed.get("species_description", "")
            logger.info(f"[SPECIES] Description generated: {species_desc}...")
            return species_desc
        except Exception as e:
            logger.warning(f"[SPECIES] LLM description failed, using fallback: {e}")
            return self._fallback_species_gender_description(species_display, gender_display, species_hybrid, species_secondary, role)

    def _fallback_species_gender_description(
        self,
        species_type: str,
        gender_type: str,
        hybrid: bool,
        secondary: str,
        role: str,
    ) -> str:
        """Generate a fallback template-based species+gender description when LLM fails."""
        gs = get_game_strings(self.language)
        gm = gs["gm_fallback"]
        species_map = gm["fallback_species"]
        if hybrid and secondary and species_type in species_map and secondary in species_map:
            hybrid_key = "hybrid_format_ru" if self.language == LANGUAGE_RU else "hybrid_format_en"
            base = f"{species_map.get(species_type, species_type)}{gm[hybrid_key].format(secondary=species_map.get(secondary, secondary).lower() if self.language == LANGUAGE_RU else species_map.get(secondary, secondary))}"
        else:
            unknown = gm["unknown_species_format"].format(species_type=species_type)
            base = species_map.get(species_type, unknown)
        gender_note = gm["gender_note"].format(gender_type=gender_type)
        role_note = gm["role_note"].format(role=role)
        return f"{base}{gender_note}{role_note}"

    # ============== Species/Gender Option Image Prompts ==============

    def generate_species_option_prompts(
        self,
        question: dict[str, Any],
        accumulated_tags: dict[str, int],
        tag_type: str = "species_tags",
    ) -> dict[str, str]:
        """Generate one image per answer option for a species/gender question.

        Each option image shows cumulative visual effect of all previous
        species/gender choices + this option's specific trait.

        Args:
            question: The next question to generate option images for
            accumulated_tags: Dict of species/gender tag -> count accumulated so far
            tag_type: 'species_tags' or 'gender_tags'

        Returns:
            Dict mapping option_value -> short image prompt (English, ~20-30 words)
        """
        question_text = question.get("text", "")
        options = question.get("options", [])

        # Build aggregated tag description from accumulated tags
        sorted_tags = sorted(accumulated_tags.items(), key=lambda x: x[1], reverse=True)
        accumulated_desc = " and ".join(f"{tag} ({count}){' times' if count > 1 else ''}" for tag, count in sorted_tags[:3])

        options_text = ""
        for opt in options:
            opt_value = opt.get("value", "")
            opt_label = opt.get("label", "")
            tags = opt.get(tag_type, [])
            tag_str = ", ".join(tags)
            options_text += f"  - value='{opt_value}' label='{opt_label}' tags: {tag_str}\n"

        system_prompt = (
            "You are a creative sci-fi portrait prompt writer. "
            "Write SHORT image prompts in English for Stable Diffusion. "
            "Each prompt shows a Star Trek character whose appearance reflects "
            "the accumulated species/gender traits. "
            "MAXIMUM 30 words per prompt. Cinematic, dramatic lighting, 4K quality."
        )

        def _get_prompts_from_llm(prompt: str) -> dict[str, str]:
            try:
                result = self._call_llm(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    response_schema=SPECIES_OPTION_PROMPTS_SCHEMA,
                    temperature=0.8,
                    max_tokens=1024,
                )
                prompts_dict = {}
                if result and "prompts" in result:
                    for entry in result["prompts"]:
                        opt_val = entry.get("option_value", "").strip("[]")
                        prompt_text = entry.get("prompt", "")
                        if opt_val and prompt_text:
                            prompts_dict[opt_val] = prompt_text
                return prompts_dict
            except Exception as e:
                logger.warning(f"[OPTION_PROMPTS] LLM call failed: {e}")
                return {}

        # 1. Initial attempt
        user_prompt = (
            f"Question: {question_text}\n"
            f"Accumulated traits so far: {accumulated_desc or 'none yet'}\n"
            f"Options (each with its own trait tags):\n{options_text}\n\n"
            f"IMPORTANT: You must generate exactly {len(options)} prompts, one for each option listed above.\n"
            "For EACH option, write a short English image prompt showing a "
            "character with the accumulated traits AND the option's specific trait. "
            "Each prompt MAX 30 words. "
            'Output as JSON array: [{"option_value": ..., "prompt": ...}].'
        )
        prompts_dict = _get_prompts_from_llm(user_prompt)

        # 2. Retry for missing options
        missing_options = [opt.get("value") for opt in options if opt.get("value") not in prompts_dict]

        if missing_options:
            logger.info(f"[OPTION_PROMPTS] Missing {len(missing_options)} prompts. Retrying for: {missing_options}")
            retry_user_prompt = (
                f"You previously missed some options. Please generate prompts ONLY for these "
                f"specific option values: {missing_options}. "
                f"You MUST return exactly {len(options)} objects in total in your JSON array "
                f"(including the ones you already provided). "
                f"Each prompt MUST be a short English image prompt (~20-30 words) "
                f"reflecting the accumulated traits: {accumulated_desc or 'none yet'} "
                f"and the option's specific trait. "
                'Output as JSON array: [{"option_value": ..., "prompt": ...}].'
            )
            retry_prompts = _get_prompts_from_llm(retry_user_prompt)
            prompts_dict.update(retry_prompts)

        # 3. Final fallback for any remaining missing options
        final_missing = [opt.get("value") for opt in options if opt.get("value") not in prompts_dict]
        if final_missing:
            logger.warning(f"[OPTION_PROMPTS] Still missing {len(final_missing)} prompts after retry. Using fallback.")
            for opt in options:
                opt_val = opt.get("value")
                if opt_val not in prompts_dict:
                    tags = opt.get(tag_type, [])
                    tag_str = ", ".join(tags) if tags else "character"
                    prompts_dict[opt_val] = f"Star Trek character portrait, {tag_str} traits, cinematic lighting, uniform, 4K quality, portrait, upper_body."

        logger.info(f"[OPTION_PROMPTS] Successfully resolved {len(prompts_dict)}/{len(options)} prompts")
        return prompts_dict

    # ============== NPC Decision Making (LLM-based, no consequences visible) ==============

    def generate_npc_choice(self, choices: list[dict[str, Any]], npc_profile: dict[str, Any]) -> dict[str, Any]:
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
            clean_choices.append(
                {
                    "id": c.get("id", ""),
                    "text": c.get("text", ""),
                }
            )

        # Build the choice text for the NPC
        choices_text = "\n".join([f"  [{c['id']}] {c['text']}" for c in clean_choices])

        system, user = build_npc_decision_prompts(self.language, npc_name, npc_role, traits, choices_text, use_vs=self.vs_enabled, vs_k=self.vs_k)

        try:
            if self.vs_enabled:
                vs_result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=vs_response_schema(NPC_CHOICE_SCHEMA),
                    temperature=0.8,
                    max_tokens=2048,
                )
                chosen = select_response(vs_result["responses"], self.vs_mode)
                parsed = chosen["text"]
            else:
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
                logger.warning(f"[NPC] LLM returned invalid choice '{action_id}' for {npc_name}, falling back to first available")
                action_id = valid_ids[0] if valid_ids else ""
                rationale = "Fallback: first available action"

            logger.info(f"[NPC] {npc_name} chose '{action_id}': {rationale}...")
            return {"action_id": action_id, "rationale": rationale}

        except Exception as e:
            logger.error(f"[NPC] LLM choice failed for {npc_name}: {e}", exc_info=True)
            # Fallback: pick first action
            action_id = choices[0].get("id", "") if choices else ""
            return {"action_id": action_id, "rationale": "Fallback: system default"}

    def generate_player_auto_choice(
        self,
        choices: list[dict[str, Any]],
        player_profile: dict[str, Any],
        personal_briefing: str,
        global_circumstances: dict[str, Any] | None = None,
        player_name: str = "",
    ) -> dict[str, Any]:
        """Generate an LLM-based auto-choice for a player who didn't choose in time.

        Unlike NPC choice generation, this has more context:
        - The player's personal briefing text
        - The global circumstances (setting, conflict, narrative)
        - The player's full profile (traits, role, species)

        Args:
            choices: List of action dicts with id, text (consequences stripped)
            player_profile: Dict with role, personality_traits, species, etc.
            personal_briefing: The player's personal briefing text for this turn
            global_circumstances: Dict with setting, conflict, narrative, key_events
            player_name: Optional player name for personalized prompt

        Returns:
            Dict with action_id and rationale.
        """
        role = player_profile.get("role", "Crew Member")
        traits = player_profile.get("personality_traits", [])
        species = player_profile.get("species", "")
        display_name = player_name or role

        # Strip consequences
        clean_choices = []
        for c in choices:
            clean_choices.append(
                {
                    "id": c.get("id", ""),
                    "text": c.get("text", c.get("description", "")),
                }
            )
        choices_text = "\n".join([f"  [{c['id']}] {c['text']}" for c in clean_choices])

        # Build global context snippet
        gc_settings = ""
        if global_circumstances:
            setting = global_circumstances.get("setting", "")
            conflict = global_circumstances.get("conflict", "")
            narrative = global_circumstances.get("narrative", "")
            gc_settings = f"\n\nLocation: {setting}\nConflict: {conflict}\nSituation: {narrative[:500]}"

        species_line = f"\nSpecies: {species}" if species else ""

        system, user = build_auto_choice_prompts(
            self.language,
            display_name,
            role,
            traits,
            species_line,
            personal_briefing,
            gc_settings,
            choices_text,
            use_vs=self.vs_enabled,
            vs_k=self.vs_k,
        )

        try:
            if self.vs_enabled:
                vs_result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=vs_response_schema(NPC_CHOICE_SCHEMA),
                    temperature=0.8,
                    max_tokens=2048,
                )
                chosen = select_response(vs_result["responses"], self.vs_mode)
                parsed = chosen["text"]
            else:
                parsed = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=NPC_CHOICE_SCHEMA,
                    temperature=0.8,
                    max_tokens=512,
                )
            action_id = parsed.get("action_id", "")
            rationale = parsed.get("rationale", "")

            valid_ids = [c.get("id") for c in choices]
            if action_id not in valid_ids:
                logger.warning(f"[AUTO_CHOICE] LLM returned invalid choice '{action_id}' for {display_name}, falling back to first available")
                action_id = valid_ids[0] if valid_ids else ""
                rationale = "Fallback: first available action"

            logger.info(f"[AUTO_CHOICE] Player {display_name} auto-chose '{action_id}': {rationale[:80]}...")
            return {"action_id": action_id, "rationale": rationale}

        except Exception as e:
            logger.error(f"[AUTO_CHOICE] LLM failed for {display_name}: {e}", exc_info=True)
            action_id = choices[0].get("id", "") if choices else ""
            return {"action_id": action_id, "rationale": "Fallback: LLM error"}

    # ============== Restructured Game Turn Generation ==============

    def generate_global_circumstances(
        self,
        turn: int,
        previous_summary: str = "",
        player_profiles: list[dict[str, Any]] | None = None,
        mission_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate the shared global circumstances for a game turn.

        This is the first step — creates the setting, conflict, and key events
        that all players and NPCs will experience from their own perspectives.

        Args:
            turn: current game turn number
            previous_summary: Summary of previous events
            player_profiles: List of player/npc profiles
            mission_context: Optional mission data to ensure story consistency
        """
        logger.info(f"[TURN] Generating global circumstances for Turn {turn}")

        player_descriptions = ""
        if player_profiles:
            player_lines = []
            for p in player_profiles:
                role = p.get("role", "Crew Member")
                name = p.get("player_name") or p.get("npc_name", "") or role
                species = p.get("species", "")
                avatar_desc = p.get("avatar_description", "")
                species_desc = p.get("species_description", "")
                # Build appearance description
                appearance_parts = []
                if species and species not in ("Unknown", "Неизвестно", ""):
                    appearance_parts.append(f"Вид: {species}")
                if species_desc:
                    appearance_parts.append(species_desc)
                if avatar_desc:
                    appearance_parts.append(avatar_desc)
                appearance_str = f" ({'; '.join(appearance_parts)})" if appearance_parts else ""
                player_lines.append(f"  - {name} ({role}){appearance_str}")
            player_descriptions = "\n".join(player_lines)

        # Build mission context string if available
        mission_str = ""
        if mission_context:
            mission_name = mission_context.get("name", "")
            mission_desc = mission_context.get("description", "")
            objectives = mission_context.get("objectives", [])

            gs = get_game_strings(self.language)
            ml = gs["gm_fallback"]["mission_labels"]
            stage_label = ml["stage_label"]
            mission_header = ml["mission_header"]
            mission_sub = ml["mission_sub"]
            name_label = ml["name_label"]
            desc_label = ml["desc_label"]
            stages_header = ml["stages_header"]
            importance_text = ml["importance_text"]

            stages_str = "\n".join([f"  - {stage_label} {o.get('stage', '?')}: {o.get('name', '')} — {o.get('description', '')}" for o in objectives])
            mission_str = f"\n{mission_header} ({mission_sub}):\n{name_label}: {mission_name}\n{desc_label}: {mission_desc}\n{stages_header}:\n{stages_str}\n{importance_text}\n"

        system, user = build_global_circumstances_prompts(
            self.language,
            turn,
            previous_summary,
            player_descriptions,
            mission_str,
            use_vs=self.vs_enabled,
            vs_k=self.vs_k,
        )

        try:
            if self.vs_enabled:
                vs_result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=vs_response_schema(GLOBAL_CIRCUMSTANCES_SCHEMA),
                    max_tokens=8192,
                )
                chosen = select_response(vs_result["responses"], self.vs_mode)
                logger.info("[VS-CIRCUMSTANCES] Selected %d/%d p=%.3f", vs_result["responses"].index(chosen) + 1, len(vs_result["responses"]), chosen["probability"])
                parsed = chosen["text"]
            else:
                parsed = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=GLOBAL_CIRCUMSTANCES_SCHEMA,
                    max_tokens=4096,
                )
            logger.info(f"[TURN] Global circumstances generated: setting='{str(parsed.get('setting', ''))}...'")
            return parsed
        except Exception as e:
            logger.error(f"[TURN] Global circumstances generation failed: {e}", exc_info=True)
            return {
                "setting": "Unknown space region",
                "conflict": "Routine operations",
                "narrative": "The ship continues its mission.",
                "key_events": ["Normal operations underway"],
            }

    def generate_player_briefing_and_choices(
        self,
        global_circumstances: dict[str, Any],
        player_profile: dict[str, Any],
        player_name: str = "",
        turn: int | None = None,
    ) -> dict[str, Any]:
        """Generate a personal briefing and unique choices for a specific player
        based on the shared global circumstances.

        Each player gets:
        - A personal_title with name + role + greeting
        - A personal briefing (their unique perspective on the situation)
        - Action choices with visible descriptions and hidden consequences
        """
        player_id = player_profile.get("player_id") or player_profile.get("npc_key", "?")
        player_role = player_profile.get("role", "Crew Member")
        traits = player_profile.get("personality_traits", [])
        logger.info(f"[TURN] Generating briefing for {player_id} ({player_role})")

        # Use player_name if provided, otherwise fall back to role
        display_name = player_name or player_role

        setting = global_circumstances.get("setting", "")
        conflict = global_circumstances.get("conflict", "")
        narrative = global_circumstances.get("narrative", "")
        key_events = global_circumstances.get("key_events", [])

        key_events_text = "\n".join([f"  - {e}" for e in key_events])
        total_actions = self.turn_good_actions + self.turn_bad_actions + self.turn_neutral_actions

        system = build_personal_briefing_system(self.language)
        user = "Global circumstances:\n" if self.language == LANGUAGE_EN else "Общие обстоятельства дня:\n"
        # Build user prompt based on language inline (complex formatting with instance state)
        if self.language == LANGUAGE_RU:
            good = "хороших"
            bad = "плохое"
            neutral = "нейтральное"
            user = (
                f"Общие обстоятельства дня:\n"
                f"Локация: {setting}\n"
                f"Конфликт: {conflict}\n"
                f"Общий нарратив: {narrative}\n\n"
                f"Ключевые события:\n{key_events_text}\n\n"
                f"Персонаж:\n"
                f"  Имя: {display_name}\n"
                f"  Роль: {player_role}\n"
                f"  Характер: {', '.join(traits) if isinstance(traits, list) else str(traits)}\n\n"
                "Создай:\n"
                "1. personal_title — уникальный, атмосферный заголовок для ПЕРСОНАЛЬНОЙ вводной этого игрока. "
                f"Формат: '{display_name} — {{{player_role}}} — {{персональное приветствие}}'. "
                f"Приветствие должно включать имя персонажа ({display_name}) и его роль ({player_role}), "
                "отражать его характер и текущую ситуацию. "
                "Пример: 'Маркус — Инженер — твои руки помнят гул реактора лучше любого сканера'.\n"
                "2. briefing — персональная вводная — что этот конкретный персонаж видит, слышит, чувствует. "
                "Как его роль и характер влияют на восприятие ситуации. (2-3 предложения)\n"
                f"3. Ровно {total_actions} вариантов действий с последствиями: "
                f"{self.turn_good_actions} {good}, {self.turn_bad_actions} {bad}, {self.turn_neutral_actions} {neutral}.\n\n"
                "КРИТИЧЕСКИЕ ТРЕБОВАНИЯ К ВАРИАНТАМ ДЕЙСТВИЙ:\n"
                "- Каждое действие ДОЛЖНО иметь РЕАЛЬНЫЙ РИСК. "
                "Успех приближает к цели миссии, провал — отдаляет. Последствия должны быть РАДИКАЛЬНЫМИ.\n"
                "- Последствия НЕ ДОЛЖНЫ быть очевидны из текста действия! Игрок ВЫБИРАЕТ вслепую.\n"
                "- Последствия могут включать: гибель членов экипажа, повреждение систем корабля, "
                "потерю ресурсов, ранения.\n"
                "- Нейтральное действие — безопасное (ничего не делать / ждать), "
                "оно НЕ продвигает миссию и может УХУДШИТЬ ситуацию.\n"
                "- Разные варианты должны давать РАЗНЫЕ уровни риска и награды.\n"
                "- Варианты должны соответствовать РОЛИ персонажа.\n\n"
                "Всё на русском языке."
            )
        else:
            user = (
                f"Global circumstances:\n"
                f"Setting: {setting}\n"
                f"Conflict: {conflict}\n"
                f"Narrative: {narrative}\n\n"
                f"Key events:\n{key_events_text}\n\n"
                f"Character:\n"
                f"  Name: {display_name}\n"
                f"  Role: {player_role}\n"
                f"  Traits: {', '.join(traits) if isinstance(traits, list) else str(traits)}\n\n"
                "Create:\n"
                "1. personal_title…\n"
                f"3. Exactly {total_actions} action choices: "
                f"{self.turn_good_actions} good, {self.turn_bad_actions} bad, {self.turn_neutral_actions} neutral.\n"
            )

        try:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=self._get_player_briefing_schema(),
                max_tokens=4096,
            )
            logger.info(f"[TURN] Briefing generated for {player_id}")

            # Override action IDs with guaranteed non-empty values —
            # LLM sometimes returns empty/missing IDs which breaks NPC choice logic.
            choices = parsed.get("choices", [])
            for idx, choice in enumerate(choices, start=1):
                choice["id"] = f"action_{idx}"
            parsed["choices"] = choices

            return parsed
        except Exception as e:
            role_label = player_role
            gs = get_game_strings(self.language)
            gm = gs["gm_fallback"]
            fallback_title = gm["fallback_title"].format(display_name=display_name, role_label=role_label)
            fallback_briefing = gm["fallback_briefing"].format(display_name=display_name, role_label=role_label)
            logger.error(f"[TURN] Briefing generation failed for {player_id}: {e}", exc_info=True)
            return {
                "personal_title": fallback_title,
                "briefing": fallback_briefing,
                "choices": [
                    {
                        "id": "a1",
                        "text": "Proceed with standard protocol",
                        "consequence": "No significant change",
                    },
                    {
                        "id": "a2",
                        "text": "Consult with colleagues",
                        "consequence": "Gather more information",
                    },
                    {
                        "id": "a3",
                        "text": "Wait and observe",
                        "consequence": "Situation develops without your input",
                    },
                ],
            }

    def analyze_combined_outcome(
        self,
        global_circumstances: dict[str, Any],
        all_decisions: list[dict[str, Any]],
        previous_summary: str = "",
        mission_context: dict[str, Any] | None = None,
        crew_roster: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Analyze all player and NPC choices together with their hidden consequences
        to produce a coherent combined outcome narrative.

        Player decisions are weighted MORE than NPC decisions.
        Mission progress is tracked and non-linear.
        Crew death is possible.

        Args:
            global_circumstances: Shared circumstances for the turn
            all_decisions: All player and NPC decisions with consequences
            previous_summary: Summary of previous turn for continuity
            mission_context: Current mission state for progress tracking
            crew_roster: Full roster of all crew members (name, role) to prevent
                the LLM from inventing non-existent crew members.

        Returns:
            Dict with outcome_narrative, ship_status_change, crew_morale_change,
            next_turn_hook, mission_progress, dead_crew_members
        """
        logger.info(f"[TURN] Analyzing combined outcome from {len(all_decisions)} decisions")

        # Weight is indicated in the decision text passed to LLM
        decisions_text = ""
        for i, d in enumerate(all_decisions, 1):
            name = d.get("name", d.get("player_id", d.get("npc_key", f"Character {i}")))
            role = d.get("role", "")
            action = d.get("action_id", "")
            action_text = d.get("action_text", "")
            consequence = d.get("consequence", "")
            rationale = d.get("rationale", "")
            is_player = bool(d.get("player_id"))
            weight = "HIGH (PLAYER)" if is_player else "NORMAL (NPC)"
            decisions_text += f"\n--- Decision {i} (Weight: {weight}) ---\nCharacter: {name} ({role})\nChose: {action_text} ({action})\nRationale: {rationale}\nHIDDEN CONSEQUENCE: {consequence}\n"

        # Full crew roster — prevents the LLM from inventing non-existent crew members
        roster_text = ""
        if crew_roster:
            roster_lines = []
            for r in crew_roster:
                cname = r.get("name", "?")
                crole = r.get("role", "?")
                is_dead = r.get("is_dead", False)
                status = "DEAD" if is_dead else "ALIVE"
                roster_lines.append(f"  - {cname} ({crole}) — {status}")
            roster_text = "\nFull crew roster (ONLY these characters exist on the ship):\n" + "\n".join(roster_lines) + "\n"

        # Mission context for progress tracking
        mission_text = ""
        if mission_context:
            objectives = mission_context.get("objectives", [])
            stage_progress = mission_context.get("stage_progress", {})
            current_stage = mission_context.get("current_stage", 0)
            mission_text = "\nMission context:\n"
            for obj in objectives:
                stage = obj.get("stage", 0)
                name = obj.get("name", "")
                desc = obj.get("description", "")
                threshold = obj.get("success_threshold", 5)
                progress = stage_progress.get(str(stage), 0)
                status = "COMPLETED" if stage < current_stage else ("CURRENT" if stage == current_stage else "UPCOMING")
                display_progress = min(progress, threshold) if status == "COMPLETED" else progress
                progress_note = " (capped at threshold — stage already completed)" if status == "COMPLETED" else ""
                mission_text += f"  Stage {stage}: {name} - {desc}\n    Progress: {display_progress}/{threshold}{progress_note}\n    Status: {status}\n"

        setting = global_circumstances.get("setting", "")
        conflict = global_circumstances.get("conflict", "")
        narrative = global_circumstances.get("narrative", "")

        system, user = build_combined_outcome_prompts(
            language=self.language,
            setting=setting,
            conflict=conflict,
            narrative=narrative,
            previous_summary=previous_summary,
            mission_text=mission_text,
            decisions_text=decisions_text,
            roster_text=roster_text,
            use_vs=self.vs_enabled,
            vs_k=self.vs_k,
        )

        try:
            if self.vs_enabled:
                vs_result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=vs_response_schema(COMBINED_OUTCOME_SCHEMA),
                    max_tokens=8192,
                    enable_thinking=True,
                )
                chosen = select_response(vs_result["responses"], self.vs_mode)
                logger.info("[VS-OUTCOME] Selected %d/%d p=%.3f", vs_result["responses"].index(chosen) + 1, len(vs_result["responses"]), chosen["probability"])
                parsed = chosen["text"]
            else:
                parsed = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=COMBINED_OUTCOME_SCHEMA,
                    max_tokens=4096,
                    enable_thinking=True,
                )
            logger.info(f"[TURN] Combined outcome generated: {str(parsed.get('outcome_narrative', ''))}...")
            return parsed
        except Exception as e:
            logger.error(f"[TURN] Combined outcome analysis failed: {e}", exc_info=True)
            return {
                "outcome_narrative": narrative if narrative else "The turn passed without major incident.",
                "ship_status_change": "No significant change.",
                "crew_morale_change": "Stable.",
                "next_turn_hook": "Tomorrow brings new challenges.",
                "mission_progress": [],
                "dead_crew_members": [],
                "ship_destroyed": False,
                "ship_hull_integrity": 100,
                "ship_shields": 100,
                "ship_systems_offline": [],
                "crew_injured": [],
                "personal_outcomes": [],
            }

    # ============== Game Over Generation ==============

    def generate_game_over_outcome(
        self,
        outcome_type: str,
        outcome_narrative: str,
        mission_summary: str,
    ) -> dict[str, str]:
        """Generate a dramatic finale narrative and image prompt for game end.

        Args:
            outcome_type: "victory" or "defeat" — label the LLM uses to frame the finale
            outcome_narrative: The last turn's outcome narrative for context
            mission_summary: Summary of mission stages and their completion status

        Returns:
            Dict with finale_narrative and finale_image_prompt
        """
        logger.info(f"[GAME_OVER] Generating {outcome_type} finale, language={self.language}")

        system, user = build_game_over_prompts(
            language=self.language,
            outcome_type=outcome_type,
            outcome_narrative=outcome_narrative,
            mission_summary=mission_summary,
            use_vs=self.vs_enabled,
            vs_k=self.vs_k,
        )

        try:
            if self.vs_enabled:
                vs_result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=vs_response_schema(GAME_OVER_SCHEMA),
                    max_tokens=4096,
                    enable_thinking=True,
                )
                chosen = select_response(vs_result["responses"], self.vs_mode)
                logger.info("[VS-GAMEOVER] Selected p=%.3f", chosen["probability"])
                parsed = chosen["text"]
            else:
                parsed = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=GAME_OVER_SCHEMA,
                    max_tokens=2048,
                    enable_thinking=True,
                )
            logger.info(f"[GAME_OVER] Finale generated: {str(parsed.get('finale_narrative', ''))[:100]}...")
            return parsed
        except Exception as e:
            logger.error(f"[GAME_OVER] Finale generation failed: {e}", exc_info=True)
            gs = get_game_strings(self.language)
            fallback_key = f"fallback_{outcome_type}"
            fallback = gs["game_over"].get(fallback_key, gs["game_over"]["fallback_defeat"])
            return fallback

    # ============== Mission Generation ==============

    def generate_mission(self, all_participants: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate a mission with stages/objectives for the game.

        Objectives are normalized (1-based, thresholds 3-5) and the mission
        carries current_stage=1 / total_stages=len(objectives) so it is
        completable from the start (spec defect A fix).
        """
        logger.info(f"[MISSION] Generating mission for {len(all_participants)} participants")

        crew_desc = "\n".join([f"  - {p.get('role', '?')} ({p.get('type', '?')})" for p in all_participants])

        mission_seeds = select_mission_seeds(self.language)
        system, user = build_mission_prompts(
            self.language,
            crew_desc,
            archetype=mission_seeds["archetype"],
            seeds=mission_seeds["seeds"],
            use_vs=self.vs_enabled,
            vs_k=self.vs_k,
        )

        try:
            if self.vs_enabled:
                vs_result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=vs_response_schema(MISSION_SCHEMA),
                    max_tokens=8192,
                    temperature=0.8,
                )
                chosen = select_response(vs_result["responses"], self.vs_mode)
                logger.info(
                    "[VS-MISSION] Selected %d/%d p=%.3f",
                    vs_result["responses"].index(chosen) + 1,
                    len(vs_result["responses"]),
                    chosen["probability"],
                )
                result = chosen["text"]
            else:
                result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=MISSION_SCHEMA,
                    max_tokens=4096,
                    temperature=0.8,
                )
        except Exception as e:
            logger.error(f"[MISSION] Generation failed: {e}", exc_info=True)
            gs = get_game_strings(self.language)
            mf = gs["gm_fallback"]["mission_fallback"]
            result = {
                "name": mf["name"],
                "description": mf["description"],
                "short_description": mf.get("description", "")[:500],
                "objectives": [{"stage": i + 1, "name": s["name"], "description": s["description"], "success_threshold": [3, 5, 7][i]} for i, s in enumerate(mf["stages"])],
            }

        # Ensure short_description exists (LLM may omit it)
        if "short_description" not in result or not result["short_description"]:
            result["short_description"] = result.get("description", "")[:500]

        # normalize: 1-based stages, thresholds 3-5, derive current/total/completed
        result["archetype"] = mission_seeds["archetype"]
        result["seeds"] = mission_seeds["seeds"]
        result = normalize_mission(result)
        logger.info(f"[MISSION] Generated: {result.get('name', '')} ({result['total_stages']} stages)")
        return result

    # ============== Bridge Image Prompt Generation ==============

    def generate_bridge_image_prompt(
        self,
        mission: dict[str, Any],
        all_participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate a detailed prompt for the bridge scene image and crew positioning.

        Uses crew roles, species/gender descriptions, and mission context
        to create a cinematic scene with the full crew on the bridge.
        """
        logger.info(f"[BRIDGE] Generating bridge image prompt for {len(all_participants)} crew")

        crew_desc = "\n".join([f"  - {p.get('role', '?')} ({p.get('type', '?')}): species={p.get('species') or '?'}, traits={', '.join(p.get('personality_traits', []))}" for p in all_participants])

        mission_name = mission.get("name", "Unknown mission")
        mission_desc = mission.get("description", "")

        system = "You are an expert cinematic prompt engineer for AI image generation. Create detailed English prompts for a starship bridge scene with the full crew. Focus on composition, lighting, crew positioning, and space opera aesthetic."
        user = (
            f"Mission: {mission_name}\n"
            f"Mission description: {mission_desc}\n\n"
            f"Crew on the bridge:\n{crew_desc}\n\n"
            "Create:\n"
            "1. A detailed English image prompt for the bridge scene — cinematic, "
            "Star Trek style, showing the crew at their stations on the bridge, "
            "holographic displays, stars visible through the viewport, dramatic lighting, 4K.\n"
            "2. Position descriptions for each crew member — where they are "
            "on the bridge and what they are doing at their station.\n\n"
            "Write the prompt and descriptions in English."
        )

        try:
            if self.vs_enabled:
                vs_system, vs_user = verbalize_prompt(system, user, DIVERSITY_HINTS["bridge_image"], k=self.vs_k)
                vs_result = self._call_llm(
                    system_prompt=vs_system,
                    user_prompt=vs_user,
                    response_schema=vs_response_schema(BRIDGE_IMAGE_SCHEMA),
                    max_tokens=8192,
                    temperature=0.8,
                )
                chosen = select_response(vs_result["responses"], self.vs_mode)
                logger.info("[VS-BRIDGE] Selected p=%.3f", chosen["probability"])
                result = chosen["text"]
            else:
                result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=BRIDGE_IMAGE_SCHEMA,
                    max_tokens=4096,
                    temperature=0.8,
                )
            logger.info(f"[BRIDGE] Prompt generated: {str(result.get('bridge_prompt', ''))[:100]}...")
            return result
        except Exception as e:
            logger.error(f"[BRIDGE] Generation failed: {e}", exc_info=True)
            return {
                "bridge_prompt": ("Star Trek starship bridge interior, full crew at their stations, holographic displays glowing, viewport showing starfield and nebula, cinematic lighting, dramatic composition, 4K quality, space opera aesthetic."),
                "brief_description": "Мостик корабля в готовности к выполнению миссии.",
                "crew_descriptions": [
                    {
                        "role": p.get("role", "?"),
                        "position_description": "At their station on the bridge",
                    }
                    for p in all_participants
                ],
            }

    # ============== Background Library Prompts ==============

    def generate_background_prompts(
        self,
        mission: dict[str, Any],
        all_participants: list[dict[str, Any]],
        language: str = LANGUAGE_EN,
    ) -> dict[str, str]:
        """Generate empty-location background prompts via LLM.

        Returns a mapping of ``location_type -> English image prompt`` covering
        the canonical ship locations (bridge, engineering, sickbay, ...). The
        prompts reflect the mission tone and the crew's species composition so
        that workstations and decor fit the inhabitants.

        Args:
            mission: Mission dict (name + description used for tone).
            all_participants: Crew list (species/gender composition informs decor).
            language: Game content language (prompts are always English; this
                only affects the LLM instruction language).

        Returns:
            Dict mapping location_type -> prompt. Missing locations on failure
            are simply absent; callers should treat the dict as best-effort.
        """
        logger.info("[BACKGROUND] Generating prompts for %d participants", len(all_participants))
        crew_summary = "\n".join(
            f"- {p.get('role', '?')} ({p.get('type', '?')}): species={p.get('species') or '?'}, "
            f"gender={p.get('gender') or '?'}"
            for p in all_participants
        )

        system = build_background_prompts_system(language)
        user = build_background_prompts_user(language, mission, crew_summary)

        try:
            result = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=BACKGROUND_PROMPTS_SCHEMA,
                max_tokens=8192,
                temperature=0.8,
            )
            backgrounds = result.get("backgrounds", [])
            mapping: dict[str, str] = {}
            for entry in backgrounds:
                loc = entry.get("location_type", "")
                prompt = entry.get("prompt", "")
                if loc in BACKGROUND_LOCATION_TYPES and prompt:
                    mapping[loc] = prompt
            logger.info("[BACKGROUND] Generated %d/%d location prompts", len(mapping), len(BACKGROUND_LOCATION_TYPES))
            return mapping
        except Exception:
            logger.error("[BACKGROUND] Generation failed", exc_info=True)
            return {}

    # ============== Scene Instruction (Qwen-Image-Edit) ==============

    def generate_scene_instruction(
        self,
        action_text: str,
        role: str,
        species_desc: str,
        language: str = LANGUAGE_EN,
        background_location: str | None = None,
    ) -> dict[str, Any]:
        """Generate a Qwen-Image-Edit instruction for placing a character in a scene.

        Returns an instruction string referring to "Picture 1" (the character)
        and the best-matching background location for this action.

        Args:
            action_text: What the character is doing (player action or scene setup).
            role: The character's role (e.g. "Captain").
            species_desc: Short species description (informs pose/environment fit).
            language: Game content language (instruction is always English).
            background_location: Optional explicit location override.

        Returns:
            Dict with "instruction" (str) and "background_location" (str|None).
        """
        system = build_scene_instruction_system(language)
        user = build_scene_instruction_user(language, action_text, role, species_desc, background_location)

        try:
            result = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=SCENE_INSTRUCTION_SCHEMA,
                max_tokens=1024,
                temperature=0.7,
            )
            instruction = result.get("instruction", "").strip()
            if not instruction:
                instruction = f"Place the character from Picture 1 in the scene. {action_text}. Cinematic lighting, photorealistic, 4K."
            loc = result.get("background_location")
            return {"instruction": instruction, "background_location": loc}
        except Exception:
            logger.warning("[SCENE_INSTRUCTION] failed, using fallback", exc_info=True)
            fallback = f"Place the character from Picture 1 in the scene. {role} {action_text}. Cinematic lighting, photorealistic, 4K."
            return {"instruction": fallback, "background_location": background_location}

    # ============== NPC Name Generation (creative, species/gender-aware) ==============

    def generate_npc_name(
        self,
        role_key: str,
        role_name: str,
        species: str,
        gender: str,
        avatar_description: str,
        personality_traits: list[str],
        avoid_names: set[str] | None = None,
    ) -> str:
        """Generate a creative name for an NPC using LLM.

        The name is generated with high temperature for creativity and
        matches the species, gender, role, and visual description.

        Args:
            role_key: Ship role key (e.g. 'navigator', 'medical_officer')
            role_name: Localized role name (e.g. 'Штурман', 'Медицинский офицер')
            species: Species type (e.g. 'human', 'humanoid', 'non_humanoid', etc.)
            gender: Gender type (e.g. 'male', 'female', 'neutral', etc.)
            avatar_description: Visual description of the character
            personality_traits: Personality traits for this role
            avoid_names: Set of names already used — the generated name must not be in this set

        Returns:
            Generated name string, or fallback format "Роль Имя" on failure.
        """
        logger.info(f"[NPC_NAME] Generating name for {role_key} ({role_name})")

        system = build_npc_name_system(self.language)
        user = build_npc_name_user(
            self.language,
            role_name,
            role_key,
            species,
            gender,
            avatar_description,
            personality_traits,
            avoid_names or set(),
        )

        try:
            parsed = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=NPC_NAME_SCHEMA,
                temperature=0.95,
                max_tokens=256,
            )
            name = parsed.get("name", "").strip()
            explanation = parsed.get("explanation", "")

            if name:
                logger.info(f"[NPC_NAME] {role_name} → '{name}' ({explanation})")
                return name

            logger.warning(f"[NPC_NAME] LLM returned empty name for {role_key}")
        except Exception as e:
            logger.warning(f"[NPC_NAME] LLM failed for {role_key}: {e}")

        # Fallback: build a simple name from role
        gs = get_game_strings(self.language)
        gm = gs["gm_fallback"]
        fn = gm["fallback_npc_names"]
        default = gm["fallback_npc_default"].format(role_name=role_name)
        return fn.get(role_key, default)

    # ============== NPC Avatar Prompts (simplified, random) ==============

    def generate_npc_avatar_prompts(self, npc_roles: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Generate simplified avatar prompts for NPCs at game start.

        Unlike human players who go through full onboarding with species/gender interviews,
        NPCs get randomized prompts for variety. No interview needed.
        """
        logger.info(f"[NPC_AVATAR] Generating avatar prompts for {len(npc_roles)} NPCs")

        # For non-humanoid, energy, and symbiotic beings a human gender label
        # (e.g. "Male"/"Female") is a strong humanoid prior that collapses the
        # alien form back into a person. Tell the LLM to invent a fitting
        # non-human biological identity for those species instead of passing the
        # human gender through.
        alien_species = {"non_humanoid", "energy", "symbiotic"}

        role_lines = []
        for r in npc_roles:
            sp = r.get("species", "random")
            if sp in alien_species:
                gender = "invent a non-human biological identity fitting the species"
            else:
                gender = r.get("gender", "random")
            role_lines.append(
                f"  - {r.get('role_key', '?')}: {r.get('role_name', '?')} | species={sp} gender={gender} | traits: {', '.join(r.get('personality_traits', []))}"
            )
        roles_text = "\n".join(role_lines)

        system = (
            "You are an expert AI art prompt engineer specializing in sci-fi character portraits. "
            "Generate VARIED, DIVERSE character portrait prompts in English.\n\n"
            "For non-humanoid, energy, and symbiotic beings: invent an appropriate non-human "
            "biological identity (reproductive cycle, colonial structure, plasma resonance, etc.) "
            "that fits their physiology. Do NOT impose human gender concepts (male/female) on "
            "beings whose biology would not have them."
        )

        # Species-specific instructions mirroring _species_prompt_instructions
        species_rules = {
            "human": "The character is human. Describe face, expression, uniform details. Portrait style, upper body.",
            "humanoid": "The character is humanoid — subtle alien features (unusual skin/hair/eye color, distinct ears/ridges, etc.) but overall human-like silhouette. Portrait style, upper body.",
            "non_humanoid": (
                "The creature is NON-HUMANOID — alien anatomy (tentacles, carapace, exoskeleton, crystalline "
                "structure, multiple limbs, amorphous form, hive cluster, etc.). "
                "The image MUST NOT look like a human or humanoid: NO two arms ending in hands, NO two legs, "
                "NO human face or hair, NOT a bipedal silhouette. The creature does NOT wear a uniform or clothing. "
                "Start the prompt with the creature itself (e.g. 'A towering crystalline entity', 'A mass of pulsating bio-gel', "
                "'An insectoid being with chitinous armor'). Alien creature concept art, NOT a Star Trek officer. "
                "Full body or 3/4 view showing the alien physiology."
            ),
            "cybernetic": "The character is CYBERNETIC/SYNTHETIC — mechanical or cybernetic body (metal, circuits, synthetic components, digital displays). If part-organic, highlight the blend of biological and mechanical. Do NOT default to a plain human with robot parts. Start the prompt with the species/mechanical description. Full body or 3/4 view.",
            "energy": (
                "The being is an ENERGY BEING — NO solid physical body, composed of energy, plasma, or light. "
                "Describe the visual signature (glow, frequency patterns, luminosity). "
                "The image MUST NOT resemble a human: NO solid body, NO face, NO limbs, NO two arms/two legs. "
                "The being does NOT wear a uniform or clothing. Start the prompt with the energy-form description. "
                "Abstract energy-being concept art, NOT a Star Trek officer. Full body view."
            ),
            "symbiotic": (
                "The creature is a SYMBIOTIC/COMPOSITE being — a hybrid of multiple organisms. Describe how different "
                "parts coexist. The image MUST NOT default to a single humanoid body: NO two arms/two legs/human face. "
                "The creature does NOT wear a uniform or clothing. Start the prompt with the composite nature. "
                "Alien creature concept art, NOT a Star Trek officer. Full body view."
            ),
        }

        user = (
            f"NPC roles needing avatar prompts:\n{roles_text}\n\n"
            "For EACH role, generate a unique, detailed English image prompt for a character portrait.\n\n"
            "CRITICAL: RESPECT the species specified for each role. Use that exact value — do not randomize it.\n\n"
            "Species-specific rules (FOLLOW THEM EXACTLY):\n"
        )
        for sp_key in ["human", "humanoid", "non_humanoid", "cybernetic", "energy", "symbiotic"]:
            if sp_key in species_rules:
                user += f"  - {sp_key}: {species_rules[sp_key]}\n"
        user += '\n~50 words per prompt. Cinematic lighting. 4K quality. Output as JSON array: [{"role_key": ..., "prompt": ...}]'

        try:
            if self.vs_enabled:
                vs_system, vs_user = verbalize_prompt(system, user, DIVERSITY_HINTS["npc_avatars"], k=self.vs_k)
                vs_result = self._call_llm(
                    system_prompt=vs_system,
                    user_prompt=vs_user,
                    response_schema=vs_response_schema(NPC_AVATAR_PROMPT_SCHEMA),
                    max_tokens=8192,
                    temperature=0.9,
                )
                chosen = select_response(vs_result["responses"], self.vs_mode)
                logger.info("[VS-NPCAVATAR] Selected p=%.3f", chosen["probability"])
                result = chosen["text"]
                prompts_list = result.get("prompts", [])
            else:
                result = self._call_llm(
                    system_prompt=system,
                    user_prompt=user,
                    response_schema=NPC_AVATAR_PROMPT_SCHEMA,
                    max_tokens=4096,
                    temperature=0.9,
                )
                prompts_list = result.get("prompts", [])
            logger.info(f"[NPC_AVATAR] Generated {len(prompts_list)} prompts")
            return prompts_list
        except Exception as e:
            logger.error(f"[NPC_AVATAR] Generation failed: {e}", exc_info=True)
            # Fallback: simple role-based prompts
            fallback = []

            fallback_framing = {
                "human": ("human, portrait style, upper body, uniform", "Star Trek character portrait of"),
                "humanoid": ("humanoid alien with subtle alien features, portrait style, upper body, uniform", "Star Trek character portrait of"),
                "non_humanoid": ("non-humanoid alien creature, alien anatomy, NOT a human or humanoid, NO two arms and two legs, NO human face, NO uniform, NO clothing, full body or 3/4 view showing alien physiology", "Alien creature concept art of a"),
                "cybernetic": ("cybernetic/synthetic being with mechanical body, full body or 3/4 view", "Sci-fi concept art of a"),
                "energy": ("energy being composed of plasma or light, NO solid body, NO face, NO limbs, NOT a human or humanoid, full body view", "Abstract energy-being concept art of an"),
                "symbiotic": ("symbiotic composite creature, hybrid of multiple organisms, NOT a single humanoid body, NO human face, full body view", "Alien creature concept art of a"),
            }

            for r in npc_roles:
                sp = r.get("species", "human").lower()
                if sp not in fallback_framing:
                    sp = "human"
                framing, lead = fallback_framing[sp]
                fallback.append(
                    {
                        "role_key": r.get("role_key", "?"),
                        "prompt": (f"{lead} {r.get('role_name', '?')}, {framing}. Cinematic lighting, 4K quality, highly detailed. Unique appearance."),
                    }
                )
            return fallback

    def generate_default_action(self, story: GameStory, player_profile: dict[str, Any]) -> dict[str, Any]:
        """Generate a default action when player doesn't choose"""
        traits = player_profile.get("personality_traits", [])
        actions = story.decision_points

        if "логичный" in traits or "аналитический" in traits or "logical" in traits or "analytical" in traits:
            return actions[0] if actions else {}
        elif "смелый" in traits or "решительный" in traits or "bold" in traits or "decisive" in traits:
            return actions[1] if len(actions) > 1 else (actions[0] if actions else {})
        else:
            return actions[2] if len(actions) > 2 else (actions[0] if actions else {})


# ============== Factory Function ==============


def create_game_server(language: str = "en") -> GameServer:
    """Create and initialize Game Server agent.

    Args:
        language: Language for content generation ("en" or "ru")
    """
    return GameServer(language=language)
