"""dspy.LM factory for the offline prompt optimizer.

Targets the llama.cpp llama-server OpenAI-compatible endpoint (the same
service the game-server uses, http://llama.cpp:8090/v1 from inside the
docker network — on the host it is http://localhost:8090/v1). Vision-capable
models (any preset with an mmproj in llama.cpp.models.ini) accept base64
data-URL images through the same endpoint, which is how the VL judge works.
"""

import logging
import os

import dspy
import requests

logger = logging.getLogger(__name__)


def _router_base() -> str:
    """llama.cpp router root (without /v1) derived from LLM_URL."""
    base = os.getenv("LLM_URL", "http://localhost:8090/v1").rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def unload_model(model: str) -> None:
    """POST /models/unload on the llama.cpp router — frees the model's VRAM.

    The bake-offs run in sequential phases (every student generates and
    saves its outputs first, then each judge scores them in turn); unloading
    between phases keeps at most ONE model resident, so combinations like
    bartowski/Altworld_Hemmingway-1 + unsloth/flint + unsloth/Qwen3.8-27B
    never have to fit in memory at the same time. Loading needs no explicit
    call: with --models-autoload the router loads the model named in the
    request on first use.
    """
    resp = requests.post(f"{_router_base()}/models/unload", json={"model": model}, timeout=120)
    if resp.ok:
        logger.info("router: unloaded %s", model)
    elif "model is not running" in resp.text:
        # already unloaded (e.g. idle sleep kicked in) — the target state
        logger.info("router: %s already unloaded", model)
    else:
        logger.warning("router refused to unload %s: HTTP %d %s",
                       model, resp.status_code, resp.text)

# cache=False is critical: dspy caches LM responses on disk by default, which
# would silently freeze every creative generation to the first sampled output.
# enable_thinking mirrors the runtime's chat_template_kwargs extra body — with
# thinking on, small local models burn the whole budget on reasoning_content
# and return an empty answer.
def make_lm(
    temperature: float = 0.7,
    max_tokens: int = 2048,
    model: str | None = None,
    api_base: str | None = None,
    enable_thinking: bool = False,
) -> dspy.LM:
    """Build a dspy.LM pointing at the llama.cpp endpoint."""
    model = model or os.getenv("LLM_MODEL", "unsloth/Qwen3.5-27B")
    api_base = api_base or os.getenv("LLM_URL", "http://localhost:8090/v1")
    return dspy.LM(
        model=f"openai/{model}",
        api_base=api_base,
        api_key=os.getenv("LLM_API_KEY", "placeholder-key-for-llama-cpp"),
        temperature=temperature,
        max_tokens=max_tokens,
        cache=False,
        num_retries=2,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )


def make_judge_lm(
    model: str | None = None,
    api_base: str | None = None,
    *,
    temperature: float = 0.1,
    max_tokens: int = 1024,
) -> dspy.LM:
    """Low-temperature LM for judge/rubric scoring. GEPA's reflection LM
    overrides temperature/max_tokens via the keyword arguments.

    Set JUDGE_MODEL (e.g. unsloth/Qwen3.8-27B-MTP) to grade with a model
    other than the student — reduces self-agreement bias and, for GEPA,
    gives a different reflection voice. The bake-offs accept SEVERAL judges
    (one pass each) and unload every model between phases via
    unload_model(), so student/judge combos that don't fit in VRAM together
    still work without restarting llama.cpp.
    """
    model = model or os.getenv("JUDGE_MODEL") or None
    return make_lm(temperature=temperature, max_tokens=max_tokens, model=model, api_base=api_base)
