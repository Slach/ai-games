"""Centralized ComfyUI image-model config: ``kind -> model_key``.

Three generation paths are routed through this module, each with its own
model registry and per-kind override table:

* **txt2img** (:func:`resolve_txt2img_model`) — plain text-to-image
  (avatars, scenes, splash, loading, ...). Backed by
  ``_TXT2IMG_BUILDERS`` in ``image_generator.py``.
* **img2img** (:func:`resolve_img2img_model`) — image-to-image
  (avatar-as-latent-reference action scenes). Uses the same underlying
  diffusion model family as txt2img, so it shares the model key space;
  the builder is selected from ``_IMG2IMG_BUILDERS``.
* **edit** (:func:`resolve_edit_model`) — identity-preserving instruction
  editing (Qwen-Image-Edit). A separate model family (instruction-editing
  architecture, not a diffusion-model swap), backed by
  ``_EDIT_BUILDERS``. File references (UNET/CLIP/VAE/LoRA) live in the
  config so a future edit model only adds an ``EDIT_MODELS`` entry plus
  a builder, without touching call sites.

Resolution order is the same for all three:

1. ``<PATH>_MODEL_OVERRIDES[kind]`` — an exact per-kind override.
2. Longest underscore-prefix of ``kind`` in the override table — covers
   parameterized kinds such as ``npc_avatar_<role>``.
3. ``DEFAULT_<PATH>_MODEL`` — the global default for that path.

img2img deliberately uses the *txt2img* override table and a shared
``DEFAULT_TXT2IMG_MODEL``: an img2img workflow is the same model with a
latent-image start instead of an empty latent, so a kind that resolves
to FLUX for txt2img should also resolve to FLUX for img2img.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    """A txt2img / img2img model entry: a human label plus the builder key.

    The actual ComfyUI workflow dicts are constructed in
    ``image_generator.py``; this config only selects WHICH builder runs.
    """

    label: str   # human-readable, used in logs
    builder: str  # key into image_generator._TXT2IMG_BUILDERS / _IMG2IMG_BUILDERS


@dataclass(frozen=True)
class EditModelConfig:
    """An instruction-editing model entry (identity-preserving path).

    Unlike txt2img/img2img, an edit model carries its own file references
    here (UNET/CLIP/VAE/LoRA): a future edit model (e.g. a FLUX-Kontext
    variant) differs in every file and in the workflow node graph, so the
    builder receives these filenames as a parameter rather than reading
    module-level constants.
    """

    label: str       # human-readable, used in logs
    builder: str     # key into image_generator._EDIT_BUILDERS
    unet: str        # UNET (or GGUF) filename
    clip: str        # CLIP text-encoder filename
    vae: str         # VAE filename
    lora: str        # accelerator LoRA filename (e.g. Lightning 4-step)


# ============== txt2img + img2img (shared model family) ==============

# Registry of known txt2img models. img2img builders are keyed by the same
# model key (see resolve_img2img_model).
MODELS: dict[str, ModelConfig] = {
    "z_image_turbo": ModelConfig(
        label="Z-Image Turbo (8-step distilled)",
        builder="z_image_turbo",
    ),
    "flux_dev_gguf_q4": ModelConfig(
        label="FLUX.1 [dev] GGUF Q4_K_S",
        builder="flux_dev",
    ),
}

# Global default for txt2img (and img2img). Override via COMFYUI_TXT2IMG_MODEL.
DEFAULT_TXT2IMG_MODEL = os.getenv("COMFYUI_TXT2IMG_MODEL", "z_image_turbo")

# Per-kind overrides for txt2img AND img2img. Kinds not listed fall back
# to DEFAULT_TXT2IMG_MODEL. A key here also acts as a prefix: "npc_avatar"
# covers every "npc_avatar_<role>" kind (see _prefix_override).
KIND_MODEL_OVERRIDES: dict[str, str] = {
    # Avatars of non-humanoid / alien characters benefit from FLUX, which
    # follows anatomy prompts far better than the Z-Image Turbo default.
    "avatar": "flux_dev_gguf_q4",
    "npc_avatar": "flux_dev_gguf_q4",
}


def _prefix_override(overrides: dict[str, str], kind: str) -> str | None:
    """Return the model key for the longest ``overrides`` prefix of ``kind``.

    Splits ``kind`` on ``_`` and shrinks from the right, so
    ``npc_avatar_security`` checks ``npc_avatar_security`` then
    ``npc_avatar`` then ``npc``. The last underscore-separated token is
    never left dangling as a single word.
    """
    parts = kind.split("_")
    for end in range(len(parts), 0, -1):
        candidate = "_".join(parts[:end])
        if candidate in overrides:
            return overrides[candidate]
    return None


def resolve_txt2img_model(kind: str | None) -> str:
    """Return the model key for a given image-generation ``kind`` (txt2img).

    Exact match on ``KIND_MODEL_OVERRIDES`` wins; otherwise the longest
    underscore-prefix match is used (so ``npc_avatar_<role>`` falls under
    the ``npc_avatar`` override). Falls back to ``DEFAULT_TXT2IMG_MODEL``.
    """
    if kind:
        if kind in KIND_MODEL_OVERRIDES:
            return KIND_MODEL_OVERRIDES[kind]
        prefix_match = _prefix_override(KIND_MODEL_OVERRIDES, kind)
        if prefix_match is not None:
            return prefix_match
    return DEFAULT_TXT2IMG_MODEL


def resolve_img2img_model(kind: str | None) -> str:
    """Return the model key for an img2img generation of ``kind``.

    img2img shares the txt2img model family (the workflow differs only in
    the latent-image start), so this uses the same override table and
    default as :func:`resolve_txt2img_model`.
    """
    return resolve_txt2img_model(kind)


def get_model_config(model_key: str) -> ModelConfig:
    """Return the :class:`ModelConfig` for ``model_key``.

    Raises ``KeyError`` for an unknown key — every resolved key must map
    to a registered model.
    """
    return MODELS[model_key]


# ============== edit (identity-preserving instruction editing) ==============

# Registry of instruction-editing models. Currently a single entry; a
# future FLUX-Kontext / Redux edit model would add an entry here plus a
# builder in image_generator._EDIT_BUILDERS.
EDIT_MODELS: dict[str, EditModelConfig] = {
    "qwen_image_edit_2511": EditModelConfig(
        label="Qwen-Image-Edit-2511 (GGUF Q4_K_M)",
        builder="qwen_image_edit",
        unet="qwen-image-edit-2511-Q4_K_M.gguf",
        clip="qwen_2.5_vl_7b_fp8_scaled.safetensors",
        vae="qwen_image_vae.safetensors",
        lora="Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
    ),
}

# Global default for the edit path. Override via COMFYUI_EDIT_MODEL.
DEFAULT_EDIT_MODEL = os.getenv("COMFYUI_EDIT_MODEL", "qwen_image_edit_2511")

# Per-kind overrides for the edit path. Kinds not listed fall back to
# DEFAULT_EDIT_MODEL. Same prefix-match semantics as txt2img.
KIND_EDIT_OVERRIDES: dict[str, str] = {
    # All identity-preserving edits currently go through Qwen-Image-Edit.
    # Add a kind here only to diverge from the default (e.g. route a
    # specific kind to a future FLUX-Kontext edit model).
}


def resolve_edit_model(kind: str | None) -> str:
    """Return the model key for an identity-preserving edit of ``kind``.

    Exact match on ``KIND_EDIT_OVERRIDES`` wins; otherwise the longest
    underscore-prefix match is used. Falls back to ``DEFAULT_EDIT_MODEL``.
    """
    if kind:
        if kind in KIND_EDIT_OVERRIDES:
            return KIND_EDIT_OVERRIDES[kind]
        prefix_match = _prefix_override(KIND_EDIT_OVERRIDES, kind)
        if prefix_match is not None:
            return prefix_match
    return DEFAULT_EDIT_MODEL


def get_edit_model_config(model_key: str) -> EditModelConfig:
    """Return the :class:`EditModelConfig` for ``model_key``.

    Raises ``KeyError`` for an unknown key.
    """
    return EDIT_MODELS[model_key]
