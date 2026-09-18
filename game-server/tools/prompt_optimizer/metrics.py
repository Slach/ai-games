"""Judge metrics for the offline prompt optimizer.

Both metrics return a float in [0, 1] so they work with every dspy
optimizer (BootstrapFewShot truthiness, GEPA/SIMBA scalar objectives):

* npc_choice_metric — hard code gates (action_id must be one of the offered
  ids, rationale non-trivial) followed by an LLM judge that scores how well
  the choice fits the NPC's personality, role, and loyalty band.
* scene_vl_metric — composes the character into the scene via ComfyUI
  (Qwen-Image-Edit, the exact runtime path) and scores the result image
  with a vision-language judge: form preservation (no humanoid collapse),
  action depiction, environment match.

Requires COMFYUI_URL pointing at the host-exposed ComfyUI (see
run_optimize.py which defaults it to http://localhost:8188).
"""

import asyncio
import base64
import json
import logging
import os
import re
import sys
import threading

import dspy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from image_generator import ImageGenerator  # noqa: E402
from prompts import BACKGROUND_LOCATION_TYPES  # noqa: E402
from signatures import AvatarVLJudge, BridgeVLJudge, NPCChoiceJudge, SceneVLJudge  # noqa: E402

logger = logging.getLogger(__name__)

_CHOICE_ID_RE = re.compile(r"\[([a-z0-9_]+)\]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_WOMAN_RE = re.compile(r"\b(woman|women|female|feminine)\b", re.IGNORECASE)
_MAN_RE = re.compile(r"\b(man|men|male|masculine)\b", re.IGNORECASE)
_ANDROGYNE_RE = re.compile(r"\b(androgynous|androgyn|gender-ambiguous|genderless|synthetic)\b", re.IGNORECASE)

# The runtime ANATOMY CONTRACT for humans forbids these terms in the output
# prompt verbatim (game_server.generate_avatar_prompt).
_FORBIDDEN_HUMAN_TERMS = (
    "extra legs", "six legs", "tentacle", "carapace", "exoskeleton",
    "plasma", "energy body", "absence of face", "sensor cluster",
    "swarm", "colony", "hive", "parasitic", "symbiotic",
)

_HUMAN_LIKE = {"human", "humanoid"}

# Files directory of the ComfyUI output volume (repo mount), used to turn
# generated image URLs into local files for the VL judge.
_COMFYUI_FILES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "..", "comfyui", "files",
)

_npc_judge: dspy.Predict | None = None
_vl_judge: dspy.Predict | None = None
_avatar_vl_judge: dspy.Predict | None = None
_bridge_vl_judge: dspy.Predict | None = None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# One persistent event loop for every async runtime call (ComfyUI generation).
# asyncio.run() per metric call would create and close a fresh loop each time,
# while image_generator's module-level semaphore binds to the loop that first
# contends it: under GEPA's multi-threaded evaluation mixed loops raise
# "bound to a different event loop" and a slot is never released, deadlocking
# every later generation. A single shared loop keeps the binding stable and is
# safe to call from any worker thread; the timeout turns a stuck request into
# a scored-zero case instead of a hung run.
_async_loop: asyncio.AbstractEventLoop | None = None
_async_loop_lock = threading.Lock()
_ASYNC_CALL_TIMEOUT = 900.0


def _run_async(coro, timeout: float = _ASYNC_CALL_TIMEOUT):
    global _async_loop
    with _async_loop_lock:
        if _async_loop is None:
            _async_loop = asyncio.new_event_loop()
            threading.Thread(target=_async_loop.run_forever, daemon=True, name="optimizer-async").start()
    return asyncio.run_coroutine_threadsafe(coro, _async_loop).result(timeout)


def _parse_score(raw: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(raw))
    if not match:
        return None
    return _clamp01(float(match.group()))


def make_npc_choice_metric(judge_lm: dspy.LM):
    """Build the npc_choice metric bound to a judge LM."""
    global _npc_judge
    if _npc_judge is None:
        _npc_judge = dspy.Predict(NPCChoiceJudge)

    def metric(example, pred, trace=None) -> float:
        valid_ids = _CHOICE_ID_RE.findall(example.choices_text)
        action_id = str(getattr(pred, "action_id", "") or "").strip()
        rationale = str(getattr(pred, "rationale", "") or "").strip()
        if action_id not in valid_ids:
            logger.info("metric[npc_choice]: action_id %r not among %s", action_id, valid_ids)
            return 0.0
        if len(rationale.split()) < 5:
            logger.info("metric[npc_choice]: rationale too short (%d words)", len(rationale.split()))
            return 0.0
        npc_context = (
            f"Name: {example.npc_name}\nRole: {example.npc_role}\n"
            f"Traits: {example.traits}\nLoyalty: {example.loyalty} — {example.loyalty_rule}"
        )
        try:
            with dspy.context(lm=judge_lm):
                verdict = _npc_judge(
                    npc_context=npc_context,
                    choices_text=example.choices_text,
                    action_id=action_id,
                    rationale=rationale,
                )
        except Exception:
            logger.warning("npc_choice judge call failed", exc_info=True)
            return 0.0
        score = _parse_score(verdict.score)
        if score is None:
            logger.warning("npc_choice judge returned unparsable score %r", verdict.score)
            return 0.0
        logger.info("metric[npc_choice]: %s %s/%s -> %.2f (%s)", example.npc_name, action_id, example.loyalty_band, score, verdict.feedback)
        return score

    return metric


def _data_url(filename: str) -> str:
    path = os.path.join(os.path.abspath(_COMFYUI_FILES_DIR), filename)
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/png;base64,{b64}"


def _view_url(filename: str) -> str:
    comfyui = os.environ["COMFYUI_URL"].rstrip("/")
    if "/" in filename:
        subfolder, name = filename.split("/", 1)
        return f"{comfyui}/view?filename={name}&subfolder={subfolder}&type=output"
    return f"{comfyui}/view?filename={filename}&type=output"


def _compose_scene(example, instruction: str) -> str | None:
    """Run the runtime compose path (Qwen-Image-Edit) for one instruction."""
    avatar_url = _view_url(example.avatar_filename)
    background_url = (
        _view_url(example.background_filename)
        if getattr(example, "background_filename", "")
        else None
    )
    generator = ImageGenerator()
    try:
        return _run_async(generator.generate_character_in_scene(
            instruction_prompt=instruction,
            character_avatar_url=avatar_url,
            background_url=background_url,
            character_description=str(example.species_desc),
            filename_prefix="optimizer_scene",
            width=1024,
            height=1024,
            game_id="optimizer",
            player_id=None,
            turn=0,
            kind="scene_instruction_eval",
            species_category=str(getattr(example, "species_category", "") or ""),
        ))
    except Exception:
        logger.warning("metric[scene_vl]: scene compose failed", exc_info=True)
        return None


def make_scene_vl_metric(judge_lm: dspy.LM):
    """Build the scene_instruction metric bound to a judge LM."""
    global _vl_judge
    if _vl_judge is None:
        _vl_judge = dspy.Predict(SceneVLJudge)

    def metric(example, pred, trace=None) -> float:
        instruction = str(getattr(pred, "instruction", "") or "").strip()
        if not instruction.lower().startswith("place the character"):
            logger.info("metric[scene_vl]: instruction does not follow the 'Place the character' convention")
            return 0.0
        location = str(getattr(pred, "scene_background_location", "") or "").strip()
        if location not in BACKGROUND_LOCATION_TYPES:
            logger.info("metric[scene_vl]: background location %r not valid", location)
            return 0.0

        scene_url = _compose_scene(example, instruction)
        if not scene_url:
            logger.warning("metric[scene_vl]: ComfyUI compose returned no image")
            return 0.0
        scene_filename = ImageGenerator._extract_filename_from_url(scene_url)
        if not scene_filename:
            logger.warning("metric[scene_vl]: cannot parse generated image filename from %s", scene_url, stack_info=True)
            return 0.0
        try:
            avatar_du = _data_url(example.avatar_filename)
            scene_du = _data_url(scene_filename)
        except OSError:
            logger.warning("metric[scene_vl]: cannot read image file for the judge", exc_info=True)
            return 0.0

        try:
            with dspy.context(lm=judge_lm):
                verdict = _vl_judge(
                    instruction=instruction,
                    species_description=str(example.species_desc),
                    avatar=dspy.Image(url=avatar_du),
                    scene=dspy.Image(url=scene_du),
                )
        except Exception:
            logger.warning("scene_vl judge call failed", exc_info=True)
            return 0.0
        score = _parse_score(verdict.score)
        if score is None:
            logger.warning("scene_vl judge returned unparsable score %r", verdict.score)
            return 0.0
        logger.info("metric[scene_vl]: %s -> %.2f (%s)", getattr(example, "case_id", "?"), score, verdict.feedback)
        return score

    return metric


def _generate_txt2img(prompt: str, *, width: int, height: int, kind: str, prefix: str) -> str | None:
    """Run one txt2img generation through the runtime ImageGenerator.

    Returns None on failure: a failed generation is an honest 0.0 score for
    the case. Raising instead breaks py-gepa's bookkeeping (its valset eval
    assumes one output per example and dies with IndexError when dspy
    Evaluate drops failed examples).
    """
    generator = ImageGenerator()
    try:
        return _run_async(generator.generate_image(
            prompt=prompt,
            filename_prefix=prefix,
            width=width,
            height=height,
            max_retries=2,
            game_id="optimizer",
            player_id=None,
            turn=0,
            kind=kind,
        ))
    except Exception:
        logger.warning("metric[txt2img]: generation failed for kind=%s", kind, exc_info=True)
        return None


def _avatar_common_gates(prompt: str, species_category: str) -> str | None:
    """Shared code gates for player and NPC avatar prompts. Returns an error
    reason when the prompt violates the runtime contracts, else None."""
    if len(prompt.split()) < 15:
        return f"prompt too short ({len(prompt.split())} words)"
    if _CYRILLIC_RE.search(prompt):
        return "prompt is not English"
    if species_category in _HUMAN_LIKE:
        lowered = prompt.lower()
        hits = [t for t in _FORBIDDEN_HUMAN_TERMS if t in lowered]
        if hits:
            return f"human contract violated: {hits}"
    return None


def _gender_gate(prompt: str, gender_line: str) -> str | None:
    """The npc_avatar gender contract: humans must name the gender explicitly."""
    lowered = gender_line.lower()
    if "woman" in lowered:
        if not _WOMAN_RE.search(prompt):
            return "gender line names a woman but the prompt does not"
    elif "androgyn" in lowered or "genderless" in lowered or "synthetic" in lowered:
        if not _ANDROGYNE_RE.search(prompt):
            return "gender line names an androgynous/synthetic person but the prompt does not"
    elif "man" in lowered:
        if not _MAN_RE.search(prompt):
            return "gender line names a man but the prompt does not"
    return None


def _judge_avatar(judge_lm: dspy.LM, *, image_prompt: str, species_category: str,
                  gender_line: str, role: str, filename: str, case_id: str) -> float:
    global _avatar_vl_judge
    if _avatar_vl_judge is None:
        _avatar_vl_judge = dspy.Predict(AvatarVLJudge)
    try:
        with dspy.context(lm=judge_lm):
            verdict = _avatar_vl_judge(
                image_prompt=image_prompt,
                species_category=species_category,
                gender_line=gender_line,
                role=role,
                image=dspy.Image(url=_data_url(filename)),
            )
    except Exception:
        logger.warning("avatar VL judge call failed", exc_info=True)
        return 0.0
    score = _parse_score(verdict.score)
    if score is None:
        logger.warning("avatar VL judge returned unparsable score %r", verdict.score)
        return 0.0
    logger.info("metric[avatar_vl]: %s -> %.2f (%s)", case_id, score, verdict.feedback)
    return score


def _avatar_vl_pipeline(example, pred, judge_lm: dspy.LM, *, kind: str, prefix: str) -> float:
    """Shared metric body for avatar_prompt and npc_avatar use cases."""
    prompt = str(getattr(pred, "avatar_prompt", "") or "").strip()
    species = str(getattr(example, "species_category", "") or
                  getattr(example, "species", "") or "").strip()
    gate = _avatar_common_gates(prompt, species)
    if gate:
        logger.info("metric[avatar_vl]: %s gate failed: %s", getattr(example, "case_id", "?"), gate)
        return 0.0
    gender_line = str(getattr(example, "gender_line", "") or "not applicable")
    if gender_line != "not applicable" and species not in {"non_humanoid", "energy", "symbiotic"}:
        gate = _gender_gate(prompt, gender_line)
        if gate:
            logger.info("metric[avatar_vl]: %s gate failed: %s", getattr(example, "case_id", "?"), gate)
            return 0.0

    url = _generate_txt2img(prompt, width=768, height=1024, kind=kind, prefix=prefix)
    if not url:
        logger.warning("metric[avatar_vl]: ComfyUI returned no image for %s", getattr(example, "case_id", "?"))
        return 0.0
    filename = ImageGenerator._extract_filename_from_url(url)
    if not filename:
        logger.warning("metric[avatar_vl]: cannot parse filename from %s", url, stack_info=True)
        return 0.0
    role = str(getattr(example, "role", "") or getattr(example, "role_name", "") or "?")
    return _judge_avatar(
        judge_lm,
        image_prompt=prompt,
        species_category=species,
        gender_line=gender_line,
        role=role,
        filename=filename,
        case_id=str(getattr(example, "case_id", "?")),
    )


def make_avatar_vl_metric(judge_lm: dspy.LM):
    """Build the avatar_prompt metric bound to a judge LM."""

    def metric(example, pred, trace=None) -> float:
        return _avatar_vl_pipeline(example, pred, judge_lm, kind="avatar_prompt_eval", prefix="optimizer_avatar")

    return metric


def make_npc_avatar_vl_metric(judge_lm: dspy.LM):
    """Build the npc_avatar metric bound to a judge LM."""

    def metric(example, pred, trace=None) -> float:
        return _avatar_vl_pipeline(example, pred, judge_lm, kind="npc_avatar_eval", prefix="optimizer_npc_avatar")

    return metric


def make_bridge_vl_metric(judge_lm: dspy.LM):
    """Build the bridge_image metric bound to a judge LM."""
    global _bridge_vl_judge
    if _bridge_vl_judge is None:
        _bridge_vl_judge = dspy.Predict(BridgeVLJudge)

    def metric(example, pred, trace=None) -> float:
        prompt = str(getattr(pred, "bridge_prompt", "") or "").strip()
        positions = str(getattr(pred, "crew_positions", "") or "").strip()
        if len(prompt.split()) < 20:
            logger.info("metric[bridge_vl]: bridge_prompt too short (%d words)", len(prompt.split()))
            return 0.0
        if _CYRILLIC_RE.search(prompt):
            logger.info("metric[bridge_vl]: bridge_prompt is not English")
            return 0.0
        if not positions:
            logger.info("metric[bridge_vl]: crew_positions empty")
            return 0.0

        url = _generate_txt2img(prompt, width=1024, height=1024, kind="bridge_image_eval", prefix="optimizer_bridge")
        if not url:
            logger.warning("metric[bridge_vl]: ComfyUI returned no image for %s", getattr(example, "case_id", "?"))
            return 0.0
        filename = ImageGenerator._extract_filename_from_url(url)
        if not filename:
            logger.warning("metric[bridge_vl]: cannot parse filename from %s", url, stack_info=True)
            return 0.0
        try:
            with dspy.context(lm=judge_lm):
                verdict = _bridge_vl_judge(
                    bridge_prompt=prompt,
                    crew_list=str(example.crew_list),
                    image=dspy.Image(url=_data_url(filename)),
                )
        except Exception:
            logger.warning("bridge VL judge call failed", exc_info=True)
            return 0.0
        score = _parse_score(verdict.score)
        if score is None:
            logger.warning("bridge VL judge returned unparsable score %r", verdict.score)
            return 0.0
        logger.info("metric[bridge_vl]: %s -> %.2f (%s)", getattr(example, "case_id", "?"), score, verdict.feedback)
        return score

    return metric


def make_combined_outcome_metric(judge_lm: dspy.LM | None = None):
    """Build the combined_outcome metric — pure code checks, no judge.

    Measures the documented failure modes of the outcome schema: invented or
    wrong entity_ids, missed [fatal]/[injury] obligations, deaths where the
    tag says injury, role/entity_id duplicated inside character_name,
    personal outcomes missing for deciders, already-offline systems
    re-reported, insane hull/shield deltas, positive hull delta without a
    repair action.
    """
    required_keys = [
        "outcome_narrative", "ship_status_change", "crew_morale_change",
        "next_turn_hook", "mission_progress", "dead_crew_members",
        "ship_hull_change", "ship_shields_change", "systems_taken_offline",
        "systems_restored", "crew_injured", "crew_healed", "personal_outcomes",
    ]

    def metric(example, pred, trace=None) -> float:
        raw = str(getattr(pred, "outcome_json", "") or "").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.info("metric[combined_outcome]: outcome_json is not valid JSON")
            return 0.0

        checks: list[bool] = []
        checks.append(all(k in data for k in required_keys))
        if not checks[0]:
            logger.info("metric[combined_outcome]: missing keys %s",
                        [k for k in required_keys if k not in data])
            return 0.0

        alive = set(example.alive_ids)
        dead = {e.get("entity_id", "") for e in data["dead_crew_members"]}
        injured = {e.get("entity_id", "") for e in data["crew_injured"]}

        # Only existing AND alive characters may be addressed; exact ids.
        checks.append(dead <= alive and injured <= alive)
        if not (dead <= alive and injured <= alive):
            logger.info("metric[combined_outcome]: invented/dead entity_ids: dead=%s injured=%s alive=%s",
                        dead - alive, injured - alive, alive)

        # Tag obligations: every [fatal] decider dies, every [injury] decider
        # is wounded but does NOT die.
        checks.append(set(example.expect_fatal) <= dead)
        if not (set(example.expect_fatal) <= dead):
            logger.info("metric[combined_outcome]: missed fatal obligation %s (dead=%s)",
                        set(example.expect_fatal) - dead, dead)
        checks.append(set(example.expect_injury) <= injured)
        if not (set(example.expect_injury) <= injured):
            logger.info("metric[combined_outcome]: missed injury obligation %s (injured=%s)",
                        set(example.expect_injury) - injured, injured)
        checks.append(set(example.expect_injury).isdisjoint(dead))
        if not set(example.expect_injury).isdisjoint(dead):
            logger.info("metric[combined_outcome]: injury-tagged killed: %s",
                        set(example.expect_injury) & dead)

        # Personal outcome for every decider; bare character_name.
        names = {str(p.get("character_name", "")) for p in data["personal_outcomes"]}
        checks.append(set(example.makers) <= names)
        if not (set(example.makers) <= names):
            logger.info("metric[combined_outcome]: personal_outcomes missing for %s (got %s)",
                        set(example.makers) - names, names)
        checks.append(all("(" not in n and "[" not in n for n in names))
        if not all("(" not in n and "[" not in n for n in names):
            logger.info("metric[combined_outcome]: role/id leaked into character_name: %s",
                        [n for n in names if "(" in n or "[" in n])

        # Deltas: sane integers; positive hull delta only with a repair action.
        try:
            hull = int(data["ship_hull_change"])
            shields = int(data["ship_shields_change"])
            checks.append(-60 <= hull <= 20 and -60 <= shields <= 20)
            checks.append(bool(example.repair_present) or hull <= 0)
        except (TypeError, ValueError):
            logger.info("metric[combined_outcome]: non-integer deltas %r/%r",
                        data["ship_hull_change"], data["ship_shields_change"])
            checks.append(False)
            checks.append(False)

        # Already-offline systems must not be reported as newly offline.
        new_offline = {str(s).lower() for s in data["systems_taken_offline"]}
        existing = {str(s).lower() for s in example.existing_offline}
        checks.append(new_offline.isdisjoint(existing))
        if not new_offline.isdisjoint(existing):
            logger.info("metric[combined_outcome]: re-reported offline systems %s",
                        new_offline & existing)

        score = sum(checks) / len(checks)
        logger.info("metric[combined_outcome]: %s -> %.2f (%d/%d checks)",
                    example.case_id, score, sum(checks), len(checks))
        return score

    return metric


METRIC_FACTORIES = {
    "npc_choice": make_npc_choice_metric,
    "scene_instruction": make_scene_vl_metric,
    "combined_outcome": make_combined_outcome_metric,
    "avatar_prompt": make_avatar_vl_metric,
    "npc_avatar": make_npc_avatar_vl_metric,
    "bridge_image": make_bridge_vl_metric,
}
