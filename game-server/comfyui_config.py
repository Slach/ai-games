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
    "flux2_klein_4b_gguf_q4": ModelConfig(
        label="FLUX.2 [klein] 4B distilled GGUF Q4_K_S",
        builder="flux2_klein_4b",
    ),
    "flux3_klein": ModelConfig(
        label="FLUX.3 [klein] distilled int8 convrot (16 steps, Qwen3-VL-4B)",
        builder="flux3_klein",
    ),
    "flux3_klein_nvfp4": ModelConfig(
        label="FLUX.3 [klein] distilled NVFP4 (Blackwell-only UNET quant)",
        builder="flux3_klein_nvfp4",
    ),
    "llada_image_turbo": ModelConfig(
        label="LLaDA-Image-Turbo (INT8 transformer + Q4_K_M GGUF encoder)",
        builder="llada_image_turbo",
    ),
    "qwen_image_2512": ModelConfig(
        label="Qwen-Image-2512 (GGUF Q4_K_M + Lightning 4-step LoRA)",
        builder="qwen_image_2512",
    ),
    "qwen_image_2_1": ModelConfig(
        label="Qwen-Image-2.1 (int8_convrot + qwen3vl_8b text encoder)",
        builder="qwen_image_2_1",
    ),
    # ── chatrlabs Uncensored Omni: rank-128 adapter merged into 2.1 ────────
    # NVFP4 quant of step 90000, uncensored finetune. Shares the 2.1 encoder
    # and VAE; model-card sampler (35 steps, er_sde, true CFG 5.0) instead of
    # the distilled-style 25/cfg-1 template — not a drop-in speed match.
    "qwen_image_2_1_uncensored_nvfp4": ModelConfig(
        label="Qwen-Image-2.1 Uncensored Omni (chatrlabs, NVFP4, 35 steps true CFG)",
        builder="qwen_image_2_1_uncensored_nvfp4",
    ),
    # ── Viggle Turbo: DMD-distilled student of Qwen-Image-2.1 ──────────────
    # Same base files as qwen_image_2_1 (UNET/encoder/VAE shared) + the v0.2.1
    # 6-step LoRA applied unmerged via the ViggleTurboLora custom node and the
    # ViggleTurboSigmas 6-step schedule: 6 transformer passes instead of 25,
    # no CFG. Quality is on par with the base model on t2i and most edits.
    "qwen_image_2_1_viggle_turbo": ModelConfig(
        label="Qwen-Image-2.1 Viggle Turbo (v0.2.1 LoRA, 6-step DMD, no CFG)",
        builder="qwen_image_2_1_viggle_turbo",
    ),
    # ── P-H3-Image: MiniMax-H3 33B txt2img/edit, 8 steps, no CFG ────────────
    # Native 8MP model (game sizes work too — memory is weights-bound:
    # ~19 GB peak @ 1MP, ~22 GB @ 8MP on GB10). Needs ComfyUI >= 0.44
    # (PH3ImageConditioning / MiniMaxH3SigmaShift / CLIPLoader type=minimax).
    # 2026-11-04 bake-off: 9.01 vs flux3's 9.13 — kept off the default path,
    # use for kinds that profit from 33B photorealism. The GGUF quant lost
    # to w4a8 (8.56, 61s) and was removed.
    # License: MiniMax H3 Community (excludes EU/UK/KR/US, requires
    # "Powered by MiniMax H3" attribution for derived products).
    "p_h3_w4a8": ModelConfig(
        label="P-H3-Image w4a8 (MiniMax-H3 33B, 8-step, no CFG)",
        builder="p_h3_w4a8",
    ),
    # ── MiniMax-H3-Image: official pruned 33B, 6-step PDD, no CFG ───────────
    # Native MiniMax-H3-Image (NOT the PrunaAI P-H3 above): different UNET
    # weights, 32B Qwen3-VL text encoder (vs P-H3's 4B — fixes the connector
    # that lost avatar identity in the 2026-11-04 edit bake-off) and its own
    # VAE. PDD sigma grid baked into checkpoint config — KSampler euler/simple
    # at 6 steps, no ManualSigmas. Needs ComfyUI >= 0.45
    # (MiniMaxH3ImageConditioning). nvfp4 UNET (LSDJesus comfy-kitchen quant)
    # is Blackwell-only and ~35% faster per step than w4a8; w4a8 is the
    # portable pick. Same 32B nvfp4_awq encoder + fp16 VAE for both.
    # License: MiniMax H3 Image Community (EU/UK/KR/US excluded; hosted
    # service or >$1M revenue needs written MiniMax authorization).
    "minimax_h3_w4a8": ModelConfig(
        label="MiniMax-H3-Image w4a8 (official pruned 33B, 6-step PDD, no CFG)",
        builder="minimax_h3_w4a8",
    ),
    "minimax_h3_nvfp4": ModelConfig(
        label="MiniMax-H3-Image NVFP4 (LSDJesus quant, 6-step PDD, Blackwell-only)",
        builder="minimax_h3_nvfp4",
    ),
    # ── MOSS-Image: OpenMOSS 5B single-stream DiT, Apache 2.0 ────────────────
    # Official t2i template: SLG(single_layers=22, end 0.5), 25 steps, cfg 2.5
    # (true CFG), er_sde/simple, negative "nsfw, worst quality, ...". Encoder
    # qwen3.5_9b_w4a8_gptq (CLIPLoader type=moss_image), flux2-vae_small VAE.
    # NVFP4 quant (BennyDaBall Schur-Replay GPTQ, blocks 0-23 FP4 / 24-41
    # int8): LPIPS vs bf16 0.143 vs int8's 0.094 — int8 is the accuracy pick,
    # nvfp4 the speed/VRAM one. Both Blackwell-OK (GB10 sm_121).
    "moss_image_sft_int8": ModelConfig(
        label="MOSS-Image SFT int8_convrot (25 steps, SLG, true CFG 2.5)",
        builder="moss_image",
    ),
    "moss_image_sft_nvfp4": ModelConfig(
        label="MOSS-Image SFT NVFP4 (BennyDaBall GPTQ, same 25-step template)",
        builder="moss_image_nvfp4",
    ),
}

# Global default for txt2img (and img2img). Override via COMFYUI_TXT2IMG_MODEL.
# FLUX.3 [klein] distilled NVFP4: 16 steps, native Qwen3-VL-4B prompt following.
# Won both 2026-10-18 bake-off rounds vs int8-convrot (9.19/9.20 vs 9.13/8.89,
# VL judge, same speed ~10s/img) and stages 5.0 GB vs 5.8 GB. Blackwell-only
# (GB10 sm_121) — this deployment targets DGX Spark exclusively; on non-Blackwell
# use flux3_klein (int8) or flux2_klein_4b_gguf_q4 (Apache 2.0).
# NOTE: FLUX Kommunity License (non-commercial; commercial-output exception
# under US$5M revenue) — unlike the Apache 2.0 FLUX.2 klein.
DEFAULT_TXT2IMG_MODEL = os.getenv("COMFYUI_TXT2IMG_MODEL", "flux3_klein_nvfp4")

# Per-kind overrides for txt2img AND img2img. Kinds not listed fall back
# to DEFAULT_TXT2IMG_MODEL. A key here also acts as a prefix: "npc_avatar"
# covers every "npc_avatar_<role>" kind (see _prefix_override).
KIND_MODEL_OVERRIDES: dict[str, str] = {
}

# Route avatar generation (player "avatar" and "npc_avatar_<role>" kinds) to
# a stronger txt2img model. The 4B FLUX.2 [klein] default tends to collapse
# complex prompts (exotic non-humanoid aliens) into a generic person; the 20B
# Qwen-Image-2512 follows them. Set in .env, e.g. COMFYUI_AVATAR_MODEL=qwen_image_2512.
# Empty/unset = avatars use the global default model.
_avatar_model_env = os.getenv("COMFYUI_AVATAR_MODEL", "").strip()
if _avatar_model_env:
    if _avatar_model_env not in MODELS:
        raise ValueError(
            f"COMFYUI_AVATAR_MODEL={_avatar_model_env!r} is not a registered model "
            f"(known: {', '.join(MODELS)})"
        )
    KIND_MODEL_OVERRIDES["avatar"] = _avatar_model_env
    KIND_MODEL_OVERRIDES["npc_avatar"] = _avatar_model_env


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
    "qwen_image_edit_2_1": EditModelConfig(
        label="Qwen-Image-2.1 edit (int8_convrot, unified t2i+edit)",
        builder="qwen_image_edit_21",
        unet="qwen_image_2.1_int8_convrot.safetensors",
        clip="qwen3vl_8b_int8_convrot.safetensors",
        vae="qwen_image_2.1_vae_bf16.safetensors",
        lora="",  # 2.1 has no Lightning LoRA; single 25-step path for all species
    ),
    # ── Viggle Turbo edit: the same DMD student on the edit path ───────────
    # Identical base files to qwen_image_edit_2_1; the turbo LoRA replaces the
    # 25-step euler/simple KSampler with 6 custom-sigma steps (no CFG) via
    # ViggleTurboLora + ViggleTurboSigmas custom nodes.
    "qwen_image_edit_2_1_viggle_turbo": EditModelConfig(
        label="Qwen-Image-2.1 Viggle Turbo edit (v0.2.1 LoRA, 6-step DMD)",
        builder="qwen_image_edit_21_viggle_turbo",
        unet="qwen_image_2.1_int8_convrot.safetensors",
        clip="qwen3vl_8b_int8_convrot.safetensors",
        vae="qwen_image_2.1_vae_bf16.safetensors",
        # r128 is the rank the shipped ComfyUI workflows default to; the r256
        # file is a drop-in swap (LPIPS 0.024 apart ≈ float noise floor).
        lora="Qwen-Image-2.1-viggle-turbo-v0.2.1-6step-lora-r128.safetensors",
    ),
    # P-H3-Image edit: background = image_1 (edited canvas), avatar = image_2;
    # the builder swaps the Qwen "Picture N" numbering. Same w4a8 files as
    # the txt2img p_h3_w4a8 entry — needs ComfyUI >= 0.44.
    "p_h3_w4a8": EditModelConfig(
        label="P-H3-Image w4a8 edit (MiniMax-H3 33B, 8-step, no CFG)",
        builder="p_h3_edit",
        unet="p-h3-image_w4a8.safetensors",
        clip="p-h3-image_text_encoder_int8_convrot.safetensors",
        vae="p-h3-image_vae_int8_convrot.safetensors",
        lora="",
    ),
    # MiniMax-H3-Image edit (official pruned): same image_1/image_2 canvas/
    # reference layout as p_h3_w4a8, but the 32B encoder sees the references
    # and the builder adds the "Edit <Picture 1>. " prefix itself. Shared
    # encoder/VAE between the two UNET quants — needs ComfyUI >= 0.45.
    "minimax_h3_w4a8": EditModelConfig(
        label="MiniMax-H3-Image w4a8 edit (official pruned 33B, 6-step PDD)",
        builder="minimax_h3_edit",
        unet="minimax_h3_image_pruned_w4a8.safetensors",
        clip="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        vae="minimax_h3_image_vae_fp16.safetensors",
        lora="",
    ),
    "minimax_h3_nvfp4": EditModelConfig(
        label="MiniMax-H3-Image NVFP4 edit (LSDJesus quant, 6-step PDD)",
        builder="minimax_h3_edit",
        unet="minimax_h3_image_pruned_nvfp4.safetensors",
        clip="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        vae="minimax_h3_image_vae_fp16.safetensors",
        lora="",
    ),
    # MOSS-Image edit: int8 SFT UNET + rank-64 edit LoRA (official template:
    # TextEncodeMOSSImageEdit, edit_format Moss-Image-Edit, 30 steps, cfg 4,
    # euler/normal, SLG 0->1). Same w4a8 encoder / flux2-vae_small as the
    # txt2img entries. int8 UNET only — LoRAs are untested on the NVFP4 file
    # (ComfyUI requantizes FP4 layers when patching).
    "moss_image_edit": EditModelConfig(
        label="MOSS-Image SFT edit (int8 + rank-64 edit LoRA, 30 steps)",
        builder="moss_edit",
        unet="moss_image_sft_int8_convrot.safetensors",
        clip="qwen3.5_9b_w4a8_gptq.safetensors",
        vae="flux2-vae_small.safetensors",
        lora="moss_image_edit_lora_rank_64_bf16.safetensors",
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
