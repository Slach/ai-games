"""dspy.LM factory for the offline prompt optimizer.

Targets the llama.cpp llama-server OpenAI-compatible endpoint (the same
service the game-server uses, http://llama.cpp:8090/v1 from inside the
docker network — on the host it is http://localhost:8090/v1). Vision-capable
models (any preset with an mmproj in llama.cpp.models.ini) accept base64
data-URL images through the same endpoint, which is how the VL judge works.
"""

import os

import dspy

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
    gives a different reflection voice. NOTE: the llama.cpp router runs
    with --models-max 1, so every student/judge switch pays a ~15s model
    reload; raise --models-max to 2 in nvidia-spark before using this,
    otherwise runs thrash.
    """
    model = model or os.getenv("JUDGE_MODEL") or None
    return make_lm(temperature=temperature, max_tokens=max_tokens, model=model, api_base=api_base)
