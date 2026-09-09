"""Export compiled optimizer artifacts into paste-ready prompts.py blocks.

Reads the JSON produced by run_optimize.py and prints:

1. The optimized instruction per predictor — for manual review; when you
   are happy with it, replace the corresponding system-prompt constant in
   prompts.py (translate it yourself if the optimizer produced English for
   a Russian prompt — GEPA mostly preserves the seed language, MIPRO/COPRO
   do not).
2. A paste-ready assignment of the demo-block constant, e.g.
   NPC_DECISION_DEMOS[LANGUAGE_RU] = triple-quoted block. Paste it right
   after the demo dict definition in prompts.py; builders pick it up
   automatically.
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from language import LANGUAGE_RU  # noqa: E402
from signatures import SIGNATURES  # noqa: E402

logger = logging.getLogger("export_demos")

INPUT_FIELD_ORDER = {
    "npc_choice": ["npc_name", "npc_role", "traits", "loyalty", "loyalty_rule", "choices_text"],
    "scene_instruction": ["action_text", "species_desc", "background_location", "scene_context", "species_category"],
    "combined_outcome": ["setting", "conflict", "narrative", "previous_summary", "mission_text", "ship_status_text", "decisions_text", "roster_text"],
    "avatar_prompt": ["role", "traits", "avatar_description", "species_category"],
    "npc_avatar": ["role_name", "species", "gender_line", "traits"],
    "bridge_image": ["mission_name", "mission_description", "crew_list"],
}

FIELD_LABELS_RU = {
    "npc_name": "Имя",
    "npc_role": "Роль",
    "traits": "Характер",
    "loyalty": "Лояльность",
    "loyalty_rule": "Правило лояльности",
    "choices_text": "Доступные действия",
    "action_text": "Действие",
    "species_desc": "Описание вида",
    "background_location": "Локация",
    "scene_context": "Обстановка",
    "species_category": "Категория вида",
    "setting": "Локация хода",
    "conflict": "Конфликт",
    "narrative": "Нарратив",
    "previous_summary": "Предыдущие события",
    "mission_text": "Статус миссии",
    "ship_status_text": "Статус корабля",
    "decisions_text": "Решения",
    "roster_text": "Ростер экипажа",
    "role": "Роль",
    "avatar_description": "Описание персонажа",
    "role_name": "Роль NPC",
    "species": "Вид",
    "gender_line": "Пол",
    "mission_name": "Миссия",
    "mission_description": "Описание миссии",
    "crew_list": "Экипаж",
}
FIELD_LABELS_EN = {
    "npc_name": "Name",
    "npc_role": "Role",
    "traits": "Traits",
    "loyalty": "Loyalty",
    "loyalty_rule": "Loyalty rule",
    "choices_text": "Available actions",
    "action_text": "Action",
    "species_desc": "Species",
    "background_location": "Location",
    "scene_context": "Scene context",
    "species_category": "Species category",
    "setting": "Setting",
    "conflict": "Conflict",
    "narrative": "Narrative",
    "previous_summary": "Previous events",
    "mission_text": "Mission status",
    "ship_status_text": "Ship status",
    "decisions_text": "Decisions",
    "roster_text": "Crew roster",
    "role": "Role",
    "avatar_description": "Character description",
    "role_name": "NPC role",
    "species": "Species",
    "gender_line": "Gender",
    "mission_name": "Mission",
    "mission_description": "Mission description",
    "crew_list": "Crew",
}

HEADER_RU = {
    "npc_choice": (
        "ПРИМЕРЫ ДЛЯ КАЛИБРОВКИ СТИЛЯ (ситуации вымышлены; выбирай строго среди "
        "ТЕКУЩИХ доступных действий, буквально не копируй):"
    ),
    "scene_instruction": (
        "ПРИМЕРЫ ДЛЯ КАЛИБРОВКИ (инструкция всегда на английском и начинается с "
        "'Place the character from Picture 1'; буквально не копируй):"
    ),
    "combined_outcome": (
        "ПРИМЕРЫ ДЛЯ КАЛИБРОВКИ (ходы вымышлены; соблюдай КОНТРАКТ: entity_id строго "
        "из ростера, метки [fatal]/[injury] — обязательства, character_name без роли "
        "и id, дельты вместо абсолютов; буквально не копируй):"
    ),
    "avatar_prompt": (
        "ПРИМЕРЫ ДЛЯ КАЛИБРОВКИ (промпт всегда на английском; категория вида — "
        "КОНТРАКТ АНАТОМИИ, она главнее описания; буквально не копируй):"
    ),
    "npc_avatar": (
        "ПРИМЕРЫ ДЛЯ КАЛИБРОВКИ (промпт всегда на английском; для людей и киборгов "
        "пол ОБЯЗАТЕЛЬНО назван явно, для чужих — не навязывай человеческий пол; "
        "буквально не копируй):"
    ),
    "bridge_image": (
        "ПРИМЕРЫ ДЛЯ КАЛИБРОВКИ (промпт всегда на английском; экипаж — живые люди "
        "в кадре на уровне глаз, не схема и не вид сверху; буквально не копируй):"
    ),
}
HEADER_EN = {
    "npc_choice": (
        "STYLE CALIBRATION EXAMPLES (fictional situations; choose strictly among "
        "the CURRENT available actions, do not copy verbatim):"
    ),
    "scene_instruction": (
        "CALIBRATION EXAMPLES (the instruction is always English and starts with "
        "'Place the character from Picture 1'; do not copy verbatim):"
    ),
    "combined_outcome": (
        "CALIBRATION EXAMPLES (fictional turns; honor the CONTRACT: entity_id strictly "
        "from the roster, [fatal]/[injury] tags are obligations, bare character_name, "
        "deltas not absolutes; do not copy verbatim):"
    ),
    "avatar_prompt": (
        "CALIBRATION EXAMPLES (the prompt is always English; the species category is "
        "the ANATOMY CONTRACT and overrides the description; do not copy verbatim):"
    ),
    "npc_avatar": (
        "CALIBRATION EXAMPLES (the prompt is always English; for humans and cyborgs "
        "the gender MUST be named explicitly, for aliens do not impose human gender; "
        "do not copy verbatim):"
    ),
    "bridge_image": (
        "CALIBRATION EXAMPLES (the prompt is always English; the crew are living "
        "people in frame at eye level, never a schematic or top-down view; do not "
        "copy verbatim):"
    ),
}

OUTPUT_FIELDS = {
    "npc_choice": ["action_id", "rationale"],
    "scene_instruction": ["instruction", "scene_background_location"],
    "combined_outcome": ["outcome_json"],
    "avatar_prompt": ["avatar_prompt"],
    "npc_avatar": ["avatar_prompt"],
    "bridge_image": ["bridge_prompt", "crew_positions"],
}

OUTPUT_KEYS = {
    "npc_choice": ["action_id", "rationale"],
    "scene_instruction": ["instruction", "background_location"],
    "combined_outcome": ["outcome_json"],
    "avatar_prompt": ["avatar_prompt"],
    "npc_avatar": ["avatar_prompt"],
    "bridge_image": ["bridge_prompt", "crew_positions"],
}

DEMO_CONSTANT = {
    "npc_choice": "NPC_DECISION_DEMOS",
    "scene_instruction": "SCENE_INSTRUCTION_DEMOS",
    "combined_outcome": "COMBINED_OUTCOME_DEMOS",
    "avatar_prompt": "AVATAR_PROMPT_DEMOS",
    "npc_avatar": "NPC_AVATAR_DEMOS",
    "bridge_image": "BRIDGE_IMAGE_DEMOS",
}


def _format_answer(use_case: str, demo: dict) -> str:
    if use_case == "combined_outcome":
        # The demo answer IS the outcome JSON — show it raw, not re-wrapped.
        return str(demo.get("outcome_json", "")).strip()
    payload = {key: demo[field] for key, field in zip(OUTPUT_KEYS[use_case], OUTPUT_FIELDS[use_case]) if field in demo}
    return json.dumps(payload, ensure_ascii=False)


def build_demo_block(use_case: str, language: str, demos: list[dict]) -> str:
    labels = FIELD_LABELS_RU if language == LANGUAGE_RU else FIELD_LABELS_EN
    header = HEADER_RU[use_case] if language == LANGUAGE_RU else HEADER_EN[use_case]
    lines = [header, ""]
    for i, demo in enumerate(demos, start=1):
        marker = "Пример" if language == LANGUAGE_RU else "Example"
        lines.append(f"--- {marker} {i} ---")
        for field in INPUT_FIELD_ORDER[use_case]:
            value = str(demo.get(field, "")).rstrip()
            if field in ("choices_text", "crew_list"):
                lines.append(f"{labels[field]}:")
                lines.append(value)
            else:
                lines.append(f"{labels[field]}: {value}")
        lines.append(f"Ответ: {_format_answer(use_case, demo)}" if language == LANGUAGE_RU
                     else f"Answer: {_format_answer(use_case, demo)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Export compiled artifacts for prompts.py")
    parser.add_argument("--artifacts", required=True, help="JSON written by run_optimize.py")
    parser.add_argument("--out", default="", help="Also write the paste-ready snippet to this file")
    args = parser.parse_args()

    with open(args.artifacts, encoding="utf-8") as f:
        payload = json.load(f)

    use_case = payload["use_case"]
    language = payload["language"]
    if (use_case, language) not in SIGNATURES:
        raise SystemExit(f"Unknown use case/language: {use_case}/{language}")
    lang_const = "LANGUAGE_RU" if language == LANGUAGE_RU else "LANGUAGE_EN"

    print("=" * 72)
    print("OPTIMIZED INSTRUCTIONS (review, then update the system prompt in prompts.py)")
    print("=" * 72)
    for name, instruction in payload["instructions"].items():
        print(f"\n--- predictor: {name} ---\n")
        print(instruction)

    demos = payload.get("demos", [])
    block = build_demo_block(use_case, language, demos)
    constant = DEMO_CONSTANT[use_case]
    snippet = f'{constant}[{lang_const}] = """\n{block}\n"""\n'

    print()
    print("=" * 72)
    print("DEMO BLOCK (paste into prompts.py right after the demo dict definition)")
    print("=" * 72)
    print(snippet)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(snippet)
        logger.info("Snippet written to %s", args.out)


if __name__ == "__main__":
    main()
