"""dspy signatures for the offline prompt optimizer.

Each signature mirrors the input/output contract of the corresponding
prompts.py builder (same fields the runtime schema validates), seeded with
the current system-prompt text so the optimizer starts from today's
behavior and improves from there.

Adding a new use case: define signature classes here, register them in
SIGNATURES / JUDGE registry, and wire a metric in metrics.py.
"""

import os
import sys

import dspy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402

# ── NPC decision (mirrors build_npc_decision_prompts + NPC_CHOICE_SCHEMA) ──


class NPCChoiceRU(dspy.Signature):
    """Ты — член экипажа космического корабля, принимающий решение в текущей
    ситуации. Тебе даны твоё имя, роль, характер и уровень лояльности
    командованию, а также список доступных действий — ТОЛЬКО их описания,
    без последствий. Сделай выбор одного действия на основе своей личности,
    роли и лояльности. Ты не знаешь последствий — действуй интуитивно."""

    npc_name: str = dspy.InputField(desc="Имя NPC")
    npc_role: str = dspy.InputField(desc="Роль NPC на корабле")
    traits: str = dspy.InputField(desc="Черты характера NPC через запятую")
    loyalty: str = dspy.InputField(desc="Лояльность командованию, например '70/100'")
    loyalty_rule: str = dspy.InputField(desc="Как эта лояльность велит себя вести")
    choices_text: str = dspy.InputField(desc="Доступные действия, по строке на действие в формате '[id] текст'")
    action_id: str = dspy.OutputField(desc="ID выбранного действия, строго один из предложенных id")
    rationale: str = dspy.OutputField(desc="Обоснование выбора в характере NPC (2-3 предложения), без упоминания скрытых последствий")


class NPCChoiceEN(dspy.Signature):
    """You are a starship crew member making a decision in the current
    situation. You are given your name, role, personality, loyalty to
    command, and the list of available actions — ONLY their descriptions,
    with no consequences. Choose one action based on your personality,
    role, and loyalty. You don't know the consequences — act on instinct."""

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
# only the input texts vary by game language. The docstring is seeded from the
# RU system prompt because the RU builder is the one real games exercise.


class SceneInstruction(dspy.Signature):
    """Ты — эксперт по написанию инструкций для AI image-editing модели
    Qwen-Image-Edit. Модель получает два изображения: Picture 1 — персонаж
    (аватар), Picture 2 — фон сцены. Напиши инструкцию на АНГЛИЙСКОМ, как
    разместить персонажа из Picture 1 в окружение из Picture 2: поза,
    действие, эмоция, освещение, композиция. Описание персонажа и его
    идентичность НЕ повторяй — модель сохранит их сама. Фокус на действии
    и постановке."""

    action_text: str = dspy.InputField(desc="Выбранное действие персонажа")
    species_desc: str = dspy.InputField(desc="Описание вида персонажа")
    background_location: str = dspy.InputField(desc="Подсказка локации сцены (может быть пустой)")
    scene_context: str = dspy.InputField(desc="Описание текущей обстановки хода (Setting/Conflict)")
    species_category: str = dspy.InputField(desc="Канонический ключ вида: human / humanoid / non_humanoid / energy / cybernetic / symbiotic")
    instruction: str = dspy.OutputField(desc="English instruction for Qwen-Image-Edit starting with 'Place the character from Picture 1...'")
    scene_background_location: str = dspy.OutputField(desc="Best-matching location type from: bridge, engineering, sickbay, lab, corridor, exterior_ship, planet_surface, main_screen")


# ── Combined outcome (mirrors build_combined_outcome_prompts + COMBINED_OUTCOME_SCHEMA) ──


class CombinedOutcomeRU(dspy.Signature):
    """Ты — Game Master космической игры. Ты анализируешь ВСЕ решения, принятые
    игроками и NPC, вместе с их СКРЫТЫМИ последствиями, и создаёшь единый
    связный результат хода.

    ГЛАВНЫЕ ПРИНЦИПЫ:
    1. Решения ИГРОКОВ (Weight: HIGH) имеют БОЛЬШИЙ вес, чем решения NPC.
    2. Прогресс и регресс — равновозможные последствия решений.
    3. Космос враждебен и безразличен. Раны и гибель — законная цена риска.
    4. Гибель и ранения наступают СТРОГО по метке в HIDDEN CONSEQUENCE:
       [fatal] — участник гибнет; [injury] — ранение без смерти; [progress] —
       миссия продвигается без жертв; [delay] — потерянное время. Метка —
       ОБЯЗАТЕЛЬСТВО, не смягчай и не игнорируй.
    5. У каждого принявшего решение должен быть ПЕРСОНАЛЬНЫЙ ИСХОД.
    6. Корпус, щиты и офлайн-системы — накопленное состояние; ты возвращаешь
       ТОЛЬКО ИЗМЕНЕНИЯ за ход.

    АДРЕСАЦИЯ ЖЕРТВ: в dead_crew_members / crew_injured / crew_healed адресуй
    персонажей ТОЛЬКО по entity_id из ростера экипажа, копируй ТОЧНО.
    В personal_outcomes.character_name — ТОЛЬКО чистое имя, без роли и id."""

    setting: str = dspy.InputField(desc="Локация текущего хода")
    conflict: str = dspy.InputField(desc="Центральный конфликт хода")
    narrative: str = dspy.InputField(desc="Нарратив обстановки")
    previous_summary: str = dspy.InputField(desc="Предыдущие события")
    mission_text: str = dspy.InputField(desc="Статус миссии по этапам")
    ship_status_text: str = dspy.InputField(desc="ТЕКУЩИЕ значения корпуса/щитов/офлайн-систем")
    decisions_text: str = dspy.InputField(desc="Все решения с метками HIDDEN CONSEQUENCE и entity_id")
    roster_text: str = dspy.InputField(desc="Полный ростер экипажа со стабильными entity_id и статусом")
    outcome_json: str = dspy.OutputField(desc="Валидный JSON объекта combined_outcome: outcome_narrative, ship_status_change, crew_morale_change, next_turn_hook, mission_progress[{stage,points}], dead_crew_members[{entity_id,cause}], ship_hull_change, ship_shields_change, systems_taken_offline[], systems_restored[], crew_injured[{entity_id,severity}], crew_healed[{entity_id,new_severity}], personal_outcomes[{character_name,role,outcome_text}] — дельты, не абсолюты; entity_id строго из ростера")


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


SIGNATURES = {
    ("npc_choice", LANGUAGE_RU): NPCChoiceRU,
    ("npc_choice", LANGUAGE_EN): NPCChoiceEN,
    ("scene_instruction", LANGUAGE_RU): SceneInstruction,
    ("scene_instruction", LANGUAGE_EN): SceneInstruction,
    ("combined_outcome", LANGUAGE_RU): CombinedOutcomeRU,
}
