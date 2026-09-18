"""dspy signatures for the offline prompt optimizer.

Student signatures mirror the input/output contract of the corresponding
prompts.py builder (same fields the runtime schema validates). Their
instructions are SEEDED FROM prompts.py at the bottom of this module —
the runtime prompt text is the single source of truth, so the optimizer
always measures today's prompt and cannot drift from it.

Judge signatures have no runtime counterpart and stay hand-written.

Adding a new use case: define the signature class here, seed its
instructions from the prompts.py source, register it in SIGNATURES, and
wire a metric in metrics.py.
"""

import os
import sys

import dspy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402
from prompts import (  # noqa: E402
    AVATAR_ANATOMY_CONTRACT_ALIEN,
    AVATAR_ANATOMY_CONTRACT_HUMAN,
    AVATAR_PROMPT_SYSTEM_INTRO,
    AVATAR_PROMPT_SYSTEM_TAIL,
    BRIDGE_IMAGE_PROMPT_SYSTEM,
    BRIDGE_IMAGE_VIEWPOINT_RULE,
    NPC_AVATAR_PROMPT_SYSTEM,
    _COMBINED_OUTCOME_SYSTEM_RU,
    _NPC_DECISION_SYSTEM_TMPL_EN,
    _NPC_DECISION_SYSTEM_TMPL_RU,
    build_scene_instruction_system,
)

# ── NPC decision (mirrors build_npc_decision_prompts + NPC_CHOICE_SCHEMA) ──


class NPCChoiceRU(dspy.Signature):
    """Instructions seeded from prompts._NPC_DECISION_SYSTEM_TMPL_RU below."""

    npc_name: str = dspy.InputField(desc="Имя NPC")
    npc_role: str = dspy.InputField(desc="Роль NPC на корабле")
    traits: str = dspy.InputField(desc="Черты характера NPC через запятую")
    loyalty: str = dspy.InputField(desc="Лояльность командованию, например '70/100'")
    loyalty_rule: str = dspy.InputField(desc="Как эта лояльность велит себя вести")
    choices_text: str = dspy.InputField(desc="Доступные действия, по строке на действие в формате '[id] текст'")
    action_id: str = dspy.OutputField(desc="ID выбранного действия, строго один из предложенных id")
    rationale: str = dspy.OutputField(desc="Обоснование выбора в характере NPC (2-3 предложения), без упоминания скрытых последствий")


class NPCChoiceEN(dspy.Signature):
    """Instructions seeded from prompts._NPC_DECISION_SYSTEM_TMPL_EN below."""

    npc_name: str = dspy.InputField(desc="NPC name")
    npc_role: str = dspy.InputField(desc="NPC role aboard the ship")
    traits: str = dspy.InputField(desc="NPC personality traits, comma-separated")
    loyalty: str = dspy.InputField(desc="Loyalty to command, e.g. '70/100'")
    loyalty_rule: str = dspy.InputField(desc="How this loyalty tells you to behave")
    choices_text: str = dspy.InputField(desc="Available actions, one per line in '[id] text' format")
    action_id: str = dspy.OutputField(desc="ID of the chosen action, strictly one of the offered ids")
    rationale: str = dspy.OutputField(desc="In-character reasoning for the choice (2-3 sentences), without revealing hidden consequences")


# ── Scene instruction (mirrors build_scene_instruction_* + SCENE_INSTRUCTION_SCHEMA) ──
# The output instruction is ALWAYS English (it is consumed by Qwen-Image-Edit);
# only the input texts vary by game language. One class per language because
# dspy instructions live on the signature class.


class SceneInstructionRU(dspy.Signature):
    """Instructions seeded from prompts.build_scene_instruction_system(ru) below."""

    action_text: str = dspy.InputField(desc="Выбранное действие персонажа")
    species_desc: str = dspy.InputField(desc="Описание вида персонажа")
    background_location: str = dspy.InputField(desc="Подсказка локации сцены (может быть пустой)")
    scene_context: str = dspy.InputField(desc="Описание текущей обстановки хода (Setting/Conflict)")
    species_category: str = dspy.InputField(desc="Канонический ключ вида: human / humanoid / non_humanoid / energy / cybernetic / symbiotic")
    instruction: str = dspy.OutputField(desc="English instruction for Qwen-Image-Edit starting with 'Place the character from Picture 1...'")
    scene_background_location: str = dspy.OutputField(desc="Best-matching location type from: bridge, engineering, sickbay, lab, corridor, exterior_ship, planet_surface, main_screen")


class SceneInstructionEN(dspy.Signature):
    """Instructions seeded from prompts.build_scene_instruction_system(en) below."""

    action_text: str = dspy.InputField(desc="Выбранное действие персонажа")
    species_desc: str = dspy.InputField(desc="Описание вида персонажа")
    background_location: str = dspy.InputField(desc="Подсказка локации сцены (может быть пустой)")
    scene_context: str = dspy.InputField(desc="Описание текущей обстановки хода (Setting/Conflict)")
    species_category: str = dspy.InputField(desc="Канонический ключ вида: human / humanoid / non_humanoid / energy / cybernetic / symbiotic")
    instruction: str = dspy.OutputField(desc="English instruction for Qwen-Image-Edit starting with 'Place the character from Picture 1...'")
    scene_background_location: str = dspy.OutputField(desc="Best-matching location type from: bridge, engineering, sickbay, lab, corridor, exterior_ship, planet_surface, main_screen")


# ── Combined outcome (mirrors build_combined_outcome_prompts + COMBINED_OUTCOME_SCHEMA) ──


class CombinedOutcomeRU(dspy.Signature):
    """Instructions seeded from prompts._COMBINED_OUTCOME_SYSTEM_RU below."""

    setting: str = dspy.InputField(desc="Локация текущего хода")
    conflict: str = dspy.InputField(desc="Центральный конфликт хода")
    narrative: str = dspy.InputField(desc="Нарратив обстановки")
    previous_summary: str = dspy.InputField(desc="Предыдущие события")
    mission_text: str = dspy.InputField(desc="Статус миссии по этапам")
    ship_status_text: str = dspy.InputField(desc="ТЕКУЩИЕ значения корпуса/щитов/офлайн-систем")
    decisions_text: str = dspy.InputField(desc="Все решения с метками HIDDEN CONSEQUENCE и entity_id")
    roster_text: str = dspy.InputField(desc="Полный ростер экипажа со стабильными entity_id и статусом")
    outcome_json: str = dspy.OutputField(desc="Валидный JSON объекта combined_outcome: outcome_narrative, ship_status_change, crew_morale_change, next_turn_hook, mission_progress[{stage,points}], dead_crew_members[{entity_id,cause}], ship_hull_change, ship_shields_change, systems_taken_offline[], systems_restored[], crew_injured[{entity_id,severity}], crew_healed[{entity_id,new_severity}], personal_outcomes[{character_name,role,outcome_text}] — дельты, не абсолюты; entity_id строго из ростера")


# ── Avatar / bridge image prompts (mirror the txt2img prompt generators;
# their system prompts live in prompts.py as module constants). The image
# prompt output is ALWAYS English (consumed by the txt2img model); input
# texts come from RU games. ──


class AvatarPrompt(dspy.Signature):
    """Instructions seeded from the prompts.py avatar-prompt constants below."""

    role: str = dspy.InputField(desc="Character's role on the ship")
    traits: str = dspy.InputField(desc="Personality traits, comma-separated")
    avatar_description: str = dspy.InputField(desc="Full character visual description (flavour ONLY; the species contract above wins)")
    species_category: str = dspy.InputField(desc="Anatomy contract key: human / humanoid / non_humanoid / energy / cybernetic / symbiotic")
    avatar_prompt: str = dspy.OutputField(desc="Detailed English image-generation prompt for the character avatar portrait")


class NpcAvatarPrompt(dspy.Signature):
    """Instructions seeded from prompts.NPC_AVATAR_PROMPT_SYSTEM below."""

    role_name: str = dspy.InputField(desc="NPC role name on the ship")
    species: str = dspy.InputField(desc="Species key: human / humanoid / non_humanoid / energy / cybernetic / symbiotic")
    gender_line: str = dspy.InputField(desc="Gender directive, e.g. 'a woman, feminine facial features' or 'invent a non-human biological identity fitting the species'")
    traits: str = dspy.InputField(desc="NPC personality traits, comma-separated")
    avatar_prompt: str = dspy.OutputField(desc="Detailed English image-generation prompt for the NPC avatar portrait")


class BridgeImagePrompt(dspy.Signature):
    """Instructions seeded from prompts.BRIDGE_IMAGE_PROMPT_SYSTEM below."""

    mission_name: str = dspy.InputField(desc="Mission name")
    mission_description: str = dspy.InputField(desc="Mission description")
    crew_list: str = dspy.InputField(desc="Crew on the bridge, one line each: role (type): species=..., traits=...")
    bridge_prompt: str = dspy.OutputField(desc="Detailed English image prompt for the bridge scene with the full crew at their stations")
    crew_positions: str = dspy.OutputField(desc="One line per crew member: role — where they stand on the bridge and what they do")


class AvatarVLJudge(dspy.Signature):
    """You are a strict visual judge of AI-generated character portraits in
    a cinematic sci-fi / space-opera context. Given the image prompt that
    produced the portrait, the species contract, the gender directive, and
    the image itself, score 0.0-1.0 on: (a) ANATOMY CONTRACT — decisive: a
    human/humanoid contract must yield a human (two arms, two legs, a human
    face); a non_humanoid/energy contract must NOT collapse into a standing
    uniformed human; symbiotic means a host body with a clearly visible
    alien symbiote organism growing on it; cybernetic means a humanoid with
    visible implants; (b) GENDER — when the gender line names a woman, a man, or an
    androgynous person, the face, hair, and build must read that way;
    'not applicable' means skip this criterion; (c) ROLE — uniform or
    station cues make the role recognizable; (d) portrait quality — no
    deformed anatomy, garbled text, or artifacts."""

    image_prompt: str = dspy.InputField(desc="The image-generation prompt that produced the portrait")
    species_category: str = dspy.InputField(desc="Anatomy contract key: human / humanoid / non_humanoid / energy / cybernetic / symbiotic")
    gender_line: str = dspy.InputField(desc="Gender directive, or 'not applicable'")
    role: str = dspy.InputField(desc="Character's role on the ship")
    image: dspy.Image = dspy.InputField(desc="The generated portrait")
    score: float = dspy.OutputField(desc="Contract+gender+role+quality score from 0.0 to 1.0")
    feedback: str = dspy.OutputField(desc="One short sentence explaining the score")


class BridgeVLJudge(dspy.Signature):
    """You are a strict visual judge of AI-generated starship bridge scenes.
    Given the image prompt, the crew roster it was built from, and the image
    itself, score 0.0-1.0 on: (a) CREW DEPICTED AS PEOPLE — visible faces,
    bodies, and poses at their stations; a floor plan, schematic, top-down,
    isometric, or satellite view, or an empty bridge, is a failure; (b)
    ENVIRONMENT — a recognizable bridge: consoles, holographic displays,
    viewport with stars, dramatic lighting; (c) CREW — roughly the roster
    size, distinct individuals; (d) quality — anatomy, no garbled text or
    artifacts."""

    bridge_prompt: str = dspy.InputField(desc="The image-generation prompt that produced the scene")
    crew_list: str = dspy.InputField(desc="The crew roster the prompt was built from")
    image: dspy.Image = dspy.InputField(desc="The generated bridge scene")
    score: float = dspy.OutputField(desc="Crew+environment+count+quality score from 0.0 to 1.0")
    feedback: str = dspy.OutputField(desc="One short sentence explaining the score")


class ImageBakeoffJudge(dspy.Signature):
    """You are a strict judge of AI-generated images in a cinematic sci-fi /
    space-opera context. Given the prompt the image was generated from and
    the image itself, score 0-10 on: (a) prompt adherence — every requested
    element present and correct; (b) composition and visual quality; (c)
    absence of artifacts — deformed anatomy, garbled text, smears, nonsense
    geometry. 10 = flawless professional concept art, 5 = usable but flawed,
    0 = broken."""

    prompt: str = dspy.InputField(desc="The image generation prompt")
    image: dspy.Image = dspy.InputField(desc="The generated image")
    score: float = dspy.OutputField(desc="Overall score from 0 to 10")
    feedback: str = dspy.OutputField(desc="One short sentence naming the main flaw or strength")


# ── Judge signatures (English rubric; content under judge may be RU or EN) ──


class NPCChoiceJudge(dspy.Signature):
    """You are a strict judge of a game AI's role-play quality. Given an NPC
    profile (name, role, personality, loyalty state) and the offered actions,
    score how well the chosen action fits that NPC: personality, role aboard
    the ship, and loyalty band (a mutinous NPC must NOT obediently serve the
    mission; a steadfast NPC must not sabotage). Also penalize rationales
    that are out of character, reference hidden consequences the NPC cannot
    know, or contradict the chosen action. Score 0.0-1.0."""

    npc_context: str = dspy.InputField(desc="NPC profile: name, role, traits, loyalty state and rule")
    choices_text: str = dspy.InputField(desc="Offered actions in '[id] text' format")
    action_id: str = dspy.InputField(desc="The action the NPC chose")
    rationale: str = dspy.InputField(desc="The NPC's stated reasoning")
    score: float = dspy.OutputField(desc="Fit score from 0.0 to 1.0")
    feedback: str = dspy.OutputField(desc="One short sentence explaining the score")


class SceneVLJudge(dspy.Signature):
    """You are a strict visual judge for AI-composited scenes. Picture 1 is
    the character reference avatar; Picture 2 is the generated scene. Score
    how well the scene: (a) preserved the character's identity and form —
    CRITICAL: non-humanoid/energy/symbiotic beings must NOT collapse into a
    standing human with a face; (b) depicted the requested action; (c) placed
    the character in the requested kind of environment. Score 0.0-1.0."""

    instruction: str = dspy.InputField(desc="The instruction given to the image-editing model")
    species_description: str = dspy.InputField(desc="Description of the character's species")
    avatar: dspy.Image = dspy.InputField(desc="Picture 1: the character reference avatar")
    scene: dspy.Image = dspy.InputField(desc="Picture 2: the generated scene")
    score: float = dspy.OutputField(desc="Preservation+action+environment score from 0.0 to 1.0")
    feedback: str = dspy.OutputField(desc="One short sentence explaining the score")


# ── Instruction seeding: the runtime prompts are the single source of truth ──
# Every student signature gets its instructions from prompts.py below, so the
# optimizer always measures the prompt the game actually sends. Only the
# connective glue (branch selection for the avatar anatomy contract, the
# species-rules rendering) is local to this module.

NPCChoiceRU.instructions = _NPC_DECISION_SYSTEM_TMPL_RU
NPCChoiceEN.instructions = _NPC_DECISION_SYSTEM_TMPL_EN
SceneInstructionRU.instructions = build_scene_instruction_system(LANGUAGE_RU)
SceneInstructionEN.instructions = build_scene_instruction_system(LANGUAGE_EN)
CombinedOutcomeRU.instructions = _COMBINED_OUTCOME_SYSTEM_RU

# The runtime avatar system branches on the species category in code; the
# signature sees the category as an input, so both branches are spelled out
# over the same shared constants.
AvatarPrompt.instructions = (
    AVATAR_PROMPT_SYSTEM_INTRO
    + "\n\nThe species_category input is the ANATOMY CONTRACT — HIGHEST PRIORITY, "
    "it overrides anything in the free-text character description.\n"
    "- human / humanoid: " + AVATAR_ANATOMY_CONTRACT_HUMAN + "\n"
    "- non_humanoid / energy / symbiotic: " + AVATAR_ANATOMY_CONTRACT_ALIEN + "\n"
    "- cybernetic: a humanoid with clearly visible cybernetic implants.\n\n"
    + AVATAR_PROMPT_SYSTEM_TAIL
    + "\n\nWrite the image prompt in English."
)

# The runtime npc-avatar system prompt already carries the species rules
# inside (the GEPA-compiled text absorbs them), so it seeds verbatim.
NpcAvatarPrompt.instructions = NPC_AVATAR_PROMPT_SYSTEM

BridgeImagePrompt.instructions = (
    BRIDGE_IMAGE_PROMPT_SYSTEM + " " + BRIDGE_IMAGE_VIEWPOINT_RULE
)

SIGNATURES = {
    ("npc_choice", LANGUAGE_RU): NPCChoiceRU,
    ("npc_choice", LANGUAGE_EN): NPCChoiceEN,
    ("scene_instruction", LANGUAGE_RU): SceneInstructionRU,
    ("scene_instruction", LANGUAGE_EN): SceneInstructionEN,
    ("combined_outcome", LANGUAGE_RU): CombinedOutcomeRU,
    ("avatar_prompt", LANGUAGE_RU): AvatarPrompt,
    ("npc_avatar", LANGUAGE_RU): NpcAvatarPrompt,
    ("bridge_image", LANGUAGE_RU): BridgeImagePrompt,
}
