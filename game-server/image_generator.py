"""
Image Generator - Direct ComfyUI API integration for image generation

Calls ComfyUI /prompt API directly for image generation.

The txt2img model is selected per ``kind`` via :mod:`comfyui_config`
(``resolve_txt2img_model`` → ``_TXT2IMG_BUILDERS``). Registered models:

  z_image_turbo (8-step distilled):
    UNET: z_image_turbo_bf16.safetensors
    CLIP: qwen_3_4b.safetensors (type: lumina2)
    VAE:  ae.safetensors

  Note: both models share the same Qwen3-4B text encoder (qwen_3_4b.safetensors),
  loaded with different CLIPLoader types (lumina2 vs flux2).

  flux2_klein_4b (distilled, 4 steps — global default, fastest + Apache 2.0):
    UNET: flux-2-klein-4b-Q4_K_S.gguf (via ComfyUI-GGUF UnetLoaderGGUF)
    CLIP: qwen_3_4b.safetensors (CLIPLoader, type=flux2)
    VAE:  flux2-vae.safetensors

  llada_image_turbo (masked diffusion, 4 steps, RealRebelAI custom nodes):
    Transformer: LLaDA-Image-Turbo-INT8.safetensors (LLaDAImageLoader)
    Text encoder: LLaDA-Image-Turbo-text_encoder-Q4_K_M.gguf
    VAE:  LLaDa_VAE.safetensors
    img2img goes through the native LLaDAImageEdit node (no latent path).

  qwen_image_2512 (20B GGUF Q4_K_M + Lightning 4-step LoRA):
    UNET: qwen-image-2512-Q4_K_M.gguf (via ComfyUI-GGUF UnetLoaderGGUF)
    CLIP: qwen_2.5_vl_7b_fp8_scaled.safetensors (CLIPLoader, type=qwen_image)
    VAE:  qwen_image_vae.safetensors
    Routed to per-kind via COMFYUI_AVATAR_MODEL (avatar / npc_avatar kinds) —
    the 20B model follows complex alien-physiology prompts that FLUX.2 [klein]
    collapses into a person.

  qwen_image_2_1 (int8_convrot repack + qwen3vl_8b text encoder):
    UNET: qwen_image_2.1_int8_convrot.safetensors (UNETLoader)
    CLIP: qwen3vl_8b_int8_convrot.safetensors (CLIPLoader, type=qwen_image)
    VAE:  qwen_image_2.1_vae_bf16.safetensors
    Uses the TextEncodeQwenImage21 node (no CLIPTextEncode, no shift node)
    and EmptyLatentImage; 25 steps euler/simple at cfg 1 (no Lightning LoRA).
    Needs recent ComfyUI (COMFYUI_COMMIT pin in comfyui/Dockerfile.spark).

img2img (``_build_img2img_workflow``) and Qwen-Image-Edit
(``_build_qwen_edit_workflow``) have their own fixed model combinations
and are not routed through ``comfyui_config``.
"""

import asyncio
import json
import logging
import os
import secrets
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from comfyui_config import (
    EditModelConfig,
    get_edit_model_config,
    get_model_config,
    resolve_edit_model,
    resolve_img2img_model,
    resolve_txt2img_model,
)
from logging_utils import write_comfyui_log

logger = logging.getLogger(__name__)

# ============== Concurrency Control ==============

# Max concurrent ComfyUI image generation requests
# Default: 4 parallel generations at a time
try:
    COMFYUI_IMAGE_CONCURRENCY = int(os.getenv("COMFYUI_IMAGE_CONCURRENCY", "4"))
except (ValueError, TypeError):
    logger.warning("Invalid COMFYUI_IMAGE_CONCURRENCY, using default 4")
    COMFYUI_IMAGE_CONCURRENCY = 4
_image_semaphore = asyncio.Semaphore(COMFYUI_IMAGE_CONCURRENCY)
logger.info(f"ComfyUI image concurrency set to {COMFYUI_IMAGE_CONCURRENCY}")

# Total time (queue wait + generation) a txt2img prompt may take in
# _wait_for_completion. 180s is fine for an idle ComfyUI; under load (several
# jobs queued ahead) the same image legitimately takes longer, and timing out
# then re-queueing a retry only deepens the queue — raise via env instead.
try:
    COMFYUI_WAIT_TIMEOUT = int(os.getenv("COMFYUI_WAIT_TIMEOUT", "180"))
except (ValueError, TypeError):
    logger.warning("Invalid COMFYUI_WAIT_TIMEOUT, using default 180")
    COMFYUI_WAIT_TIMEOUT = 180

# Default fallback splash image URL (user can place a manually generated image in ComfyUI output)
# Place a file named 'splash_default.png' in comfyui/output/ directory
COMFYUI_BASE_URL = os.getenv("COMFYUI_URL", "http://comfyui:8188")
DEFAULT_SPLASH_FALLBACK_URL = os.getenv(
    "DEFAULT_SPLASH_FALLBACK_URL",
    f"{COMFYUI_BASE_URL}/view?filename=splash_default.png&type=output",
)

# Default fallback loading image URL (user can place a manually generated image in ComfyUI output)
# Place a file named 'loading_default.png' in comfyui/output/ directory
DEFAULT_LOADING_FALLBACK_URL = os.getenv(
    "DEFAULT_LOADING_FALLBACK_URL",
    f"{COMFYUI_BASE_URL}/view?filename=loading_default.png&type=output",
)

# ============== ComfyUI Workflow Templates ==============

# ============== Qwen-Image-Edit-2511 model references ==============
# Instruction-editing model: takes a character reference image + optional
# background image, and renders the character into the scene via a text
# instruction prompt. Preserves identity far better than img2img because it
# understands the subject semantically rather than treating the avatar as noise.
# File references (UNET/CLIP/VAE/LoRA) live in comfyui_config.EDIT_MODELS so a
# future edit model (e.g. a FLUX-Kontext variant) can be added there without
# touching this builder.


def _build_qwen_edit_workflow(
    instruction: str,
    character_filename: str,
    background_filename: str | None,
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
    cfg: EditModelConfig,
    *,
    species_category: str = "",
) -> dict[str, Any]:
    """Build a Qwen-Image-Edit-2511 workflow for placing a character into a scene.

    Uses Qwen-Image-Edit-2511 GGUF (Q4_K_M). For human/humanoid/cybernetic
    characters the Lightning LoRA is applied for 4-step fast generation. For
    non-humanoid / energy / symbiotic species (``species_category``) the LoRA
    is skipped and the sampler runs 20 steps: at strength 1.0 the distilled
    LoRA suppresses the avatar identity in ``image1`` and the model hallucinates a
    humanoid from the text instruction + background, collapsing a crystalline
    or energy being back into a human figure.

    The character avatar is always provided; if a background image is supplied
    it is passed as a second reference so the model composes the character into
    that specific environment.

    Args:
        instruction: Editing instruction referring to "Picture 1" (the
            character) and optionally "Picture 2" (the background), e.g.
            "Place the character from Picture 1 at the engineering console
            shown in Picture 2, red alert lighting."
        character_filename: LoadImage filename for the character avatar.
        background_filename: Optional LoadImage filename for the background.
        width, height: Output dimensions.
        seed: Random seed (0 = randomize).
        filename_prefix: Output filename prefix.
        species_category: Canonical species key (human / humanoid /
            non_humanoid / energy / cybernetic / symbiotic). When it is a
            non-humanoid/energy/symbiotic species the Lightning LoRA is
            disabled and the sampler uses 20 steps to preserve identity.

    Returns:
        ComfyUI API workflow dict.
    """
    if seed == 0:
        seed = secrets.randbelow(2**63 + 1)

    # Lightning LoRA at strength 1.0 suppresses the avatar identity in image1
    # for non-humanoid / energy / symbiotic species, collapsing them into a
    # humanoid. Skip the LoRA for those species to preserve identity
    # (experimentally confirmed: LoRA off → crystals preserved, LoRA on →
    # humanoid). Without the LoRA the base model needs the full step budget
    # (Qwen-Image-Edit-2511 official workflows use 20-40 steps; 8 steps at
    # cfg 1.0 produced undercooked, mushy output).
    alien_species = {"non_humanoid", "energy", "symbiotic"}
    use_lightning = species_category not in alien_species
    sampler_steps = 4 if use_lightning else 20

    if background_filename:
        # Two references: image1 = character, image2 = background.
        # The instruction refers to them as "Picture 1" and "Picture 2".
        text_encode_node: dict[str, Any] = {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {
                "clip": ["30", 0],
                "prompt": instruction,
                "vae": ["29", 0],
                "image1": ["41", 0],
                "image2": ["42", 0],
            },
        }
        load_bg_node = {
            "class_type": "LoadImage",
            "inputs": {"image": background_filename},
        }
    else:
        # Single reference: character only.
        text_encode_node = {
            "class_type": "TextEncodeQwenImageEdit",
            "inputs": {
                "clip": ["30", 0],
                "prompt": instruction,
                "vae": ["29", 0],
                "image": ["41", 0],
            },
        }
        load_bg_node = None

    workflow: dict[str, Any] = {
        # Load the GGUF model.
        "10": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": cfg.unet},
        },
        # Qwen2.5-VL text encoder (handles vision tokens + instruction).
        "30": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": cfg.clip,
                "type": "qwen_image",
            },
        },
        # Qwen-Image VAE (separate from the Z-Image-Turbo ae.safetensors).
        "29": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": cfg.vae},
        },
        # Load character avatar.
        "41": {
            "class_type": "LoadImage",
            "inputs": {"image": character_filename},
        },
        # Lightning LoRA for 4-step sampling (skipped for alien species —
        # see use_lightning above).
        "50": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["10", 0],
                "lora_name": cfg.lora,
                "strength_model": 1.0,
            },
        } if use_lightning else None,
        # Qwen-Image-Edit conditioning (references + instruction).
        "70": text_encode_node,
        # Empty negative (Qwen-Image-Edit does not use classifier-free guidance
        # the way SD/Z-Image do; an empty string is the conventional negative).
        "75": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["30", 0]},
        },
        # layers=0 produces a single 4D latent; VAEDecode then yields one image.
        # Any layers > 0 makes EmptyQwenImageLayeredLatentImage emit a 5D tensor
        # which VAEDecode reshapes into (4*layers+1) separate images — wasting
        # disk and corrupting character identity (the trailing "shards" invent
        # humanoid anatomy that doesn't match the avatar).
        "90": {
            "class_type": "EmptyQwenImageLayeredLatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "layers": 0,
                "batch_size": 1,
            },
        },
        "100": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": sampler_steps,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["50", 0] if use_lightning else ["10", 0],
                "positive": ["70", 0],
                "negative": ["75", 0],
                "latent_image": ["90", 0],
            },
        },
        "110": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["100", 0], "vae": ["29", 0]},
        },
        "120": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": ["110", 0]},
        },
    }
    if load_bg_node is not None:
        workflow["42"] = load_bg_node
    # Drop placeholder None entries (e.g. node "50" when Lightning is skipped).
    workflow = {k: v for k, v in workflow.items() if v is not None}
    return workflow


def _build_qwen_edit_21_workflow(
    instruction: str,
    character_filename: str,
    background_filename: str | None,
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
    cfg: EditModelConfig,
    *,
    species_category: str = "",
) -> dict[str, Any]:
    """Build a Qwen-Image-2.1 instruction-edit workflow (unified t2i+edit ckpt).

    Follows the official image-edit template: TextEncodeQwenImage21 takes the
    reference images as ``image_1`` / ``image_2`` slots (the encoder splices
    them into the token sequence AND appends their VAE latents to the
    conditioning), resolution=0 keeps each reference at its own size, and
    QwenImage21Cache tunes the KV-cache device between the UNET and the
    sampler. The latent canvas is EmptyLatentImage at the requested output
    size (the template's custom_size mode) so width/height are honored.

    One sampling path for every species (25 steps, cfg 1, euler/simple) —
    2.1 has no Lightning LoRA and none is needed for identity preservation.

    Args mirror :func:`_build_qwen_edit_workflow`; ``species_category`` is
    accepted for builder-signature parity and unused.
    """
    if seed == 0:
        seed = secrets.randbelow(2**63 + 1)

    workflow: dict[str, Any] = {
        # Unified t2i+edit checkpoint (int8_convrot repack, safetensors)
        "10": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": cfg.unet, "weight_dtype": "default"},
        },
        # Qwen3-VL-8B text encoder (sees image_1/image_2 as vision slots)
        "30": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": cfg.clip,
                "type": "qwen_image",
                "device": "default",
            },
        },
        # Qwen-Image-2.1 VAE (encodes references, decodes the result)
        "29": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": cfg.vae},
        },
        # Load character avatar
        "41": {
            "class_type": "LoadImage",
            "inputs": {"image": character_filename},
        },
        # KV-cache device/precision tuning between UNET and sampler
        "45": {
            "class_type": "QwenImage21Cache",
            "inputs": {"model": ["10", 0], "device": "auto", "dtype": "default"},
        },
        # Conditioning: instruction + reference images. resolution=0 keeps each
        # reference at its own size (rounded to a multiple of 32). Autogrow
        # inputs are addressed by their dotted API keys ("images.image_1");
        # ComfyUI's build_nested_inputs regroups them into the node's
        # ``images`` dict at execution time.
        "70": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": ["30", 0],
                "prompt": instruction,
                "negative_prompt": "",
                "resolution": 0,
                "vae": ["29", 0],
                "images.image_1": ["41", 0],
            },
        },
        # Latent canvas at the requested output size (template custom_size mode)
        "90": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "100": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 25,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["45", 0],
                "positive": ["70", 0],
                "negative": ["70", 1],
                "latent_image": ["90", 0],
            },
        },
        "110": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["100", 0], "vae": ["29", 0]},
        },
        "120": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": ["110", 0]},
        },
    }
    if background_filename:
        workflow["42"] = {
            "class_type": "LoadImage",
            "inputs": {"image": background_filename},
        }
        workflow["70"]["inputs"]["images.image_2"] = ["42", 0]
    return workflow


def _build_zimage_turbo_workflow(
    prompt: str,
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
) -> dict[str, Any]:
    """Build a Z-Image Turbo text-to-image workflow for ComfyUI API.

    Uses the correct model combination:
      UNET: z_image_turbo_bf16.safetensors (distilled, 8 steps)
      CLIP: qwen_3_4b.safetensors, type=lumina2 (produces 2560-dim embeddings)
      VAE:  ae.safetensors
      ConditioningZeroOut for negative (required by Z-Image Turbo)
    """
    if seed == 0:
        seed = secrets.randbelow(2**63 + 1)

    return {
        # Load UNET model
        "28": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "z_image_turbo_bf16.safetensors",
                "weight_dtype": "default",
            },
        },
        # Load CLIP text encoder - MUST use qwen_3_4b with type lumina2
        "30": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen_3_4b.safetensors",
                "type": "lumina2",
            },
        },
        # Load VAE
        "29": {
            "class_type": "VAELoader",
            "inputs": {
                "vae_name": "ae.safetensors",
            },
        },
        # Encode positive prompt
        "27": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt,
                "clip": ["30", 0],
            },
        },
        # Create empty latent image
        "13": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1,
            },
        },
        # Apply AuraFlow sampling (shift=3, required for Z-Image Turbo)
        "11": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {
                "model": ["28", 0],
                "shift": 3.0,
            },
        },
        # KSampler - Z-Image Turbo is distilled, 8 steps is optimal
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 8,
                "cfg": 1.0,
                "sampler_name": "res_multistep",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["11", 0],
                "positive": ["27", 0],
                "negative": ["33", 0],
                "latent_image": ["13", 0],
            },
        },
        # ConditioningZeroOut for negative (required by Z-Image Turbo)
        "33": {
            "class_type": "ConditioningZeroOut",
            "inputs": {
                "conditioning": ["27", 0],
            },
        },
        # Decode latent to image
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["29", 0],
            },
        },
        # Save image
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": filename_prefix,
                "images": ["8", 0],
            },
        },
    }


def _build_flux2_klein_workflow(
    prompt: str,
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
) -> dict[str, Any]:
    """Build a FLUX.2 [klein] 4B distilled GGUF Q4_K_S text-to-image workflow.

    FLUX.2 [klein] is a 4B distilled model optimized for 4-step generation.
    Uses 128-channel latents, Qwen3-4B text encoder, and flux2 VAE.
    Apache 2.0 license.

    Model combination:
      UNET: flux-2-klein-4b-Q4_K_S.gguf (~2.5 GB, via ComfyUI-GGUF ``UnetLoaderGGUF``)
      CLIP: qwen_3_4b.safetensors (``CLIPLoader``, type=flux2)
      VAE:  flux2-vae.safetensors
      Sampler: 4 steps, cfg 1.0, euler/simple
    """
    if seed == 0:
        seed = secrets.randbelow(2**63 + 1)

    return {
        # Load GGUF UNET (ComfyUI-GGUF custom node)
        "10": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {
                "unet_name": "flux-2-klein-4b-Q4_K_S.gguf",
            },
        },
        # CLIP text encoder: Qwen3-4B, type=flux2
        "30": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen_3_4b.safetensors",
                "type": "flux2",
            },
        },
        # Load VAE (flux2-specific)
        "29": {
            "class_type": "VAELoader",
            "inputs": {
                "vae_name": "flux2-vae.safetensors",
            },
        },
        # Encode prompt
        "27": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt,
                "clip": ["30", 0],
            },
        },
        # Empty FLUX2 latent (128 channels, not 16!)
        "13": {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1,
            },
        },
        # ModelSamplingFlux — shift computed from resolution
        "11": {
            "class_type": "ModelSamplingFlux",
            "inputs": {
                "model": ["10", 0],
                "max_shift": 1.15,
                "base_shift": 0.5,
                "width": width,
                "height": height,
            },
        },
        # KSampler — FLUX.2 klein distilled, 4 steps, cfg 1.0, euler/simple
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 4,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["11", 0],
                "positive": ["27", 0],
                "negative": ["33", 0],
                "latent_image": ["13", 0],
            },
        },
        # ConditioningZeroOut for negative (distilled FLUX uses empty negative)
        "33": {
            "class_type": "ConditioningZeroOut",
            "inputs": {
                "conditioning": ["27", 0],
            },
        },
        # Decode latent to image
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["29", 0],
            },
        },
        # Save image
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": filename_prefix,
                "images": ["8", 0],
            },
        },
    }


def _build_qwen_image_2512_workflow(
    prompt: str,
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
) -> dict[str, Any]:
    """Build a Qwen-Image-2512 GGUF Q4_K_M text-to-image workflow.

    Full-size Qwen-Image model (20B) for complex prompts (exotic non-humanoid
    aliens, unusual compositions) that the 4B FLUX.2 [klein] default collapses
    into a person. Lightning 4-step LoRA keeps generation speed comparable.

    Follows the official ComfyUI template for Qwen-Image-2512: euler/simple,
    ModelSamplingAuraFlow shift=3.1, SD3 16-channel latents, and the
    lightx2v Lightning-4steps LoRA (steps=4, cfg=1).

    Model combination:
      UNET: qwen-image-2512-Q4_K_M.gguf (~13 GB, via ComfyUI-GGUF ``UnetLoaderGGUF``)
      CLIP: qwen_2.5_vl_7b_fp8_scaled.safetensors (``CLIPLoader``, type=qwen_image)
      VAE:  qwen_image_vae.safetensors
      LoRA: Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors
      Sampler: 4 steps, cfg 1.0, euler/simple
    """
    if seed == 0:
        seed = secrets.randbelow(2**63 + 1)

    # Negative from the official template (translated): low quality, deformed
    # limbs/fingers, oversaturated, waxy, AI look, messy composition, blur.
    negative = (
        "low resolution, low quality, deformed limbs, deformed fingers, "
        "oversaturated, waxy skin, face without detail, overly smooth, "
        "AI artifacts, messy composition, blurry, distorted text"
    )

    return {
        # Load GGUF UNET (ComfyUI-GGUF custom node)
        "10": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": "qwen-image-2512-Q4_K_M.gguf"},
        },
        # Lightning 4-step LoRA (steps=4, cfg=1 with it attached)
        "11": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["10", 0],
                "lora_name": "Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors",
                "strength_model": 1.0,
            },
        },
        # AuraFlow-style sigma shift — required by the official 2512 template
        "12": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["11", 0], "shift": 3.1},
        },
        # Qwen2.5-VL text encoder (same file as the edit model, type=qwen_image)
        "30": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                "type": "qwen_image",
            },
        },
        # Qwen-Image VAE
        "29": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "qwen_image_vae.safetensors"},
        },
        # Encode prompts
        "27": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["30", 0]},
        },
        "28": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["30", 0]},
        },
        # Empty SD3 latent (Qwen-Image uses 16-channel SD3-style latents)
        "13": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        # KSampler — Lightning-distilled: 4 steps, cfg 1.0, euler/simple
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 4,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["12", 0],
                "positive": ["27", 0],
                "negative": ["28", 0],
                "latent_image": ["13", 0],
            },
        },
        # Decode latent to image
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["29", 0]},
        },
        # Save image
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]},
        },
    }


def _build_qwen_image_21_workflow(
    prompt: str,
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
) -> dict[str, Any]:
    """Build a Qwen-Image-2.1 text-to-image workflow (int8_convrot repack).

    Follows the official ComfyUI template. Differences from the 2512
    workflow above: TextEncodeQwenImage21 replaces CLIPTextEncode (its
    ``resolution`` param only sizes reference images — plain txt2img sends
    none) and replaces the ModelSamplingAuraFlow shift; EmptyLatentImage
    replaces EmptySD3LatentImage (sampling auto-fixes the 4ch /8 latent to
    the model's 64ch /16 format); no Lightning LoRA exists for 2.1 yet, so
    euler/simple runs 25 steps at cfg 1.

    Model combination:
      UNET: qwen_image_2.1_int8_convrot.safetensors (``UNETLoader``)
      CLIP: qwen3vl_8b_int8_convrot.safetensors (``CLIPLoader``, type=qwen_image)
      VAE:  qwen_image_2.1_vae_bf16.safetensors
      Sampler: 25 steps, cfg 1.0, euler/simple
    """
    if seed == 0:
        seed = secrets.randbelow(2**63)

    return {
        # int8_convrot UNET (GB10-optimized repack, safetensors — not GGUF)
        "10": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "qwen_image_2.1_int8_convrot.safetensors",
                "weight_dtype": "default",
            },
        },
        # Qwen3-VL-8B text encoder (CLIPLoader type stays qwen_image)
        "30": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                "type": "qwen_image",
                "device": "default",
            },
        },
        # Qwen-Image-2.1 VAE
        "29": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"},
        },
        # Encode prompts (cfg 1 makes the empty negative free at sampling)
        "27": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "prompt": prompt,
                "negative_prompt": "",
                "resolution": 1024,
                "clip": ["30", 0],
            },
        },
        # Empty latent — sampling resizes it to the model's own format
        "13": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        # KSampler — official template: 25 steps, cfg 1, euler/simple
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 25,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["10", 0],
                "positive": ["27", 0],
                "negative": ["27", 1],
                "latent_image": ["13", 0],
            },
        },
        # Decode latent to image
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["29", 0]},
        },
        # Save image
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]},
        },
    }


def _build_qwen_image_21_multiref_workflow(
    prompt: str,
    reference_filenames: list[str],
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
) -> dict[str, Any]:
    """Build a Qwen-Image-2.1 multi-reference workflow (up to 10 pictures).

    One generation call composes a scene from the crew's avatar references:
    TextEncodeQwenImage21 receives each avatar as an ``images.image_N`` slot
    (the text encoder sees them as "Picture N" vision tokens AND the VAE
    latents are appended to the conditioning), so the model renders each crew
    member with their canonical look instead of hallucinating generic humans.
    Mirrors :func:`_build_qwen_edit_21_workflow` (same unified t2i+edit
    checkpoint and node set), but with N reference slots and the output latent
    at the requested size. RGBA references are supported natively: the VAE
    keeps all four channels.

    Args:
        prompt: Generation instruction referring to the references as
            "Picture 1" .. "Picture N" (which picture is which crew member).
        reference_filenames: LoadImage filenames (``subfolder/file.png``) of
            the avatars, in Picture order. Hard-capped at 10 — the model was
            trained with up to 10 reference images.
        width, height: Output dimensions.
        seed: Random seed (0 = randomize).
        filename_prefix: Output filename prefix.

    Returns:
        ComfyUI API workflow dict.
    """
    if seed == 0:
        seed = secrets.randbelow(2**63)

    refs = reference_filenames[:10]
    workflow: dict[str, Any] = {
        # Unified t2i+edit checkpoint (int8_convrot repack, safetensors)
        "10": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "qwen_image_2.1_int8_convrot.safetensors", "weight_dtype": "default"},
        },
        # Qwen3-VL-8B text encoder (sees image_1..image_N as vision slots)
        "30": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                "type": "qwen_image",
                "device": "default",
            },
        },
        # Qwen-Image-2.1 VAE (encodes references, decodes the result)
        "29": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"},
        },
        # KV-cache device/precision tuning between UNET and sampler
        "45": {
            "class_type": "QwenImage21Cache",
            "inputs": {"model": ["10", 0], "device": "auto", "dtype": "default"},
        },
        # Conditioning: instruction + reference avatars. resolution=0 keeps each
        # reference at its own size (rounded to a multiple of 32).
        "70": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": ["30", 0],
                "prompt": prompt,
                "negative_prompt": "",
                "resolution": 0,
                "vae": ["29", 0],
            },
        },
        # Latent canvas at the requested output size
        "90": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "100": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 25,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["45", 0],
                "positive": ["70", 0],
                "negative": ["70", 1],
                "latent_image": ["90", 0],
            },
        },
        "110": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["100", 0], "vae": ["29", 0]},
        },
        "120": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": ["110", 0]},
        },
    }
    for i, ref in enumerate(refs, start=1):
        # 200..209: must not collide with the fixed node IDs above (the
        # QwenImage21Cache node is "45" — a LoadImage range crossing it
        # silently overwrites the cache node and breaks validation).
        node_id = str(200 + i)
        workflow[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": ref},
        }
        workflow["70"]["inputs"][f"images.image_{i}"] = [node_id, 0]
    return workflow


def _build_llada_turbo_workflow(
    prompt: str,
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
) -> dict[str, Any]:
    """Build a LLaDA-Image-Turbo text-to-image workflow for the ComfyUI API.

    Uses the RealRebelAI LLaDa-Image custom nodes: a monolithic pipeline
    loader (LLaDAImageLoader) followed by LLaDAImageTextToImage. There is
    no KSampler/VAE graph — the node runs the full masked-diffusion
    pipeline internally.

    Model combination (RealRebelAI quantized port):
      Transformer: LLaDA-Image-Turbo-INT8.safetensors (6.6 GB, native int8)
      Text encoder: LLaDA-Image-Turbo-text_encoder-Q4_K_M.gguf (9.2 GB)
      VAE: LLaDa_VAE.safetensors
    Turbo settings: 4 steps, guidance 1.0.
    """
    if seed == 0:
        seed = secrets.randbelow(2**63)

    return {
        # Monolithic pipeline loader (scans diffusion_models / text_encoders / vae)
        "1": {
            "class_type": "LLaDAImageLoader",
            "inputs": {
                "diffusion_model": "LLaDA-Image-Turbo-INT8.safetensors",
                "text_encoder": "LLaDA-Image-Turbo-text_encoder-Q4_K_M.gguf",
                "vae": "LLaDa_VAE.safetensors",
                "dtype": "bfloat16",
                "offload": "cuda",
                "vae_tiling": "On",
            },
        },
        # Text-to-image generation (Turbo: 4 steps, guidance 1.0)
        "3": {
            "class_type": "LLaDAImageTextToImage",
            "inputs": {
                "pipeline": ["1", 0],
                "prompt": prompt,
                "width": width,
                "height": height,
                "steps": 4,
                "guidance_scale": 1.0,
                "seed": seed,
                "negative_prompt": "",
            },
        },
        # Save image
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": filename_prefix,
                "images": ["3", 0],
            },
        },
    }


# Registry mapping comfyui_config.ModelConfig.builder -> workflow builder fn.
# Each builder has the signature (prompt, width, height, seed, filename_prefix)
# and returns a ComfyUI API workflow dict.
_TXT2IMG_BUILDERS = {
    "z_image_turbo": _build_zimage_turbo_workflow,
    "flux2_klein_4b": _build_flux2_klein_workflow,
    "llada_image_turbo": _build_llada_turbo_workflow,
    "qwen_image_2512": _build_qwen_image_2512_workflow,
    "qwen_image_2_1": _build_qwen_image_21_workflow,
}

# Registry mapping comfyui_config.EditModelConfig.builder -> edit workflow
# builder fn. Each builder takes (instruction, character_filename,
# background_filename, width, height, seed, filename_prefix, cfg, *,
# species_category) where cfg is the EditModelConfig carrying the file refs.
_EDIT_BUILDERS = {
    "qwen_image_edit": _build_qwen_edit_workflow,
    "qwen_image_edit_21": _build_qwen_edit_21_workflow,
}

# img2img builders are instance methods (they live on ImageGenerator because
# they share VAE/CLIP nodes with the txt2img path). Dispatch is done by
# builder-key -> method name in _IMG2IMG_BUILDER_METHODS below.
_IMG2IMG_BUILDER_METHODS = {
    "z_image_turbo": "_build_img2img_workflow",
    "flux2_klein_4b": "_build_flux2_klein_img2img_workflow",
    "llada_image_turbo": "_build_llada_img2img_workflow",
}


class ImageGenerator:
    """
    Generates images using ComfyUI API directly via Z-Image Turbo model.
    """

    def __init__(self):
        self.comfyui_url = os.getenv("COMFYUI_URL", "http://comfyui:8188")
        self.client_id = str(uuid.uuid4())

    async def _queue_prompt(
        self,
        workflow: dict[str, Any],
        *,
        kind: str | None,
        ctx_game: str,
        ctx_player: str,
        ctx_turn: str,
    ) -> str:
        """Submit a workflow to ComfyUI /prompt endpoint and return the prompt_id."""
        payload = {
            "prompt": workflow,
            "client_id": self.client_id,
        }

        # Detailed workflow JSON is written to a dedicated log file by the
        # caller (generate_image).  Only emit a compact one-liner here.

        # Retry logic for transient failures (DNS, connection refused, etc.)
        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                async with (
                    aiohttp.ClientSession() as session,
                    session.post(
                        f"{self.comfyui_url}/prompt",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp,
                ):
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise Exception(f"ComfyUI /prompt error {resp.status}: {error_text}")
                    response_text = await resp.text()
                    if not response_text or not response_text.strip():
                        raise Exception(f"ComfyUI /prompt returned empty response (status {resp.status})")
                    try:
                        result = await resp.json()
                    except (aiohttp.ContentTypeError, json.JSONDecodeError) as e:
                        raise Exception(f"ComfyUI /prompt returned non-JSON response: {response_text}") from e
                    prompt_id = result.get("prompt_id")
                    if not prompt_id:
                        raise Exception(f"ComfyUI /prompt response missing prompt_id: {result}")
                    logger.info(f"ComfyUI prompt queued: {prompt_id}")
                    return prompt_id
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = 2**attempt  # 1s, 2s, 4s backoff
                    logger.warning(f"ComfyUI connection failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"ComfyUI connection failed after {max_retries} attempts: {last_error}", exc_info=True)

        raise Exception(f"Failed to connect to ComfyUI after {max_retries} attempts: {last_error}")

    async def _wait_for_completion(self, prompt_id: str, timeout: int) -> dict[str, Any]:
        """Wait for ComfyUI to finish processing a prompt via /history endpoint."""
        start = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start) < timeout:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    f"{self.comfyui_url}/history/{prompt_id}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp,
            ):
                if resp.status == 200:
                    response_text = await resp.text()
                    if not response_text or not response_text.strip():
                        await asyncio.sleep(2)
                        continue
                    try:
                        history = await resp.json()
                    except (aiohttp.ContentTypeError, json.JSONDecodeError):
                        await asyncio.sleep(2)
                        continue
                    if prompt_id in history:
                        status = history[prompt_id].get("status", {})
                        if status.get("completed", False) or status.get("status_str") == "success":
                            elapsed = asyncio.get_event_loop().time() - start
                            outputs = history[prompt_id].get("outputs", {})
                            logger.info(f"ComfyUI prompt {prompt_id} completed in {elapsed:.1f}s")
                            return outputs
                        elif status.get("status_str") == "error":
                            raise Exception(f"ComfyUI execution error: {status}")
            await asyncio.sleep(2)

        raise TimeoutError(f"ComfyUI prompt {prompt_id} timed out after {timeout}s")

    def _extract_image_url(self, outputs: dict[str, Any]) -> str | None:
        """Extract image URL from ComfyUI outputs."""
        for _node_id, node_output in outputs.items():
            images = node_output.get("images", [])
            if images:
                img = images[0]
                filename = img.get("filename", "")
                subfolder = img.get("subfolder", "")
                img_type = img.get("type", "output")
                return f"{self.comfyui_url}/view?filename={filename}&subfolder={subfolder}&type={img_type}"
        return None

    async def generate_image(
        self,
        prompt: str,
        filename_prefix: str,
        width: int,
        height: int,
        *,
        max_retries: int,
        game_id: str | None,
        player_id: str | None,
        turn: int | None,
        kind: str | None,
    ) -> str | None:
        """Generate an image via ComfyUI using Z-Image Turbo with retry.

        On each retry the seed is randomized, so a transient failure or
        bad output will produce a different image on the next attempt.

        Args:
            prompt: Detailed image generation prompt (English)
            filename_prefix: Prefix for output filename
            width: Image width (multiple of 16)
            height: Image height (multiple of 16)
            max_retries: Number of generation attempts before giving up.
            game_id: Game identifier for log file naming.
            player_id: Player identifier for log file naming.
            turn: Turn number for log file naming.
            kind: Call type descriptor for log file naming.

        Returns:
            URL of the generated image, or None on failure
        """
        ctx_game = game_id or "none"
        ctx_player = str(player_id) if player_id else ""
        ctx_turn = str(turn) if turn is not None else "0"

        model_key = resolve_txt2img_model(kind)
        model_cfg = get_model_config(model_key)
        build_workflow = _TXT2IMG_BUILDERS[model_cfg.builder]

        logger.info(
            "ComfyUI [%s] game=%s player=%s turn=%s | model=%s | size=%dx%d prefix=%s retries=%d",
            kind or "unspecified",
            ctx_game,
            ctx_player,
            ctx_turn,
            model_cfg.label,
            width,
            height,
            filename_prefix,
            max_retries,
        )

        logger.info(f"[IMAGE] Generating image via {model_cfg.label}")
        logger.info(f"[IMAGE] Size: {width}x{height}, max_retries={max_retries}")
        logger.info(f"[IMAGE] Acquiring ComfyUI semaphore ({_image_semaphore._value}/{COMFYUI_IMAGE_CONCURRENCY} slots available)...")

        async with _image_semaphore:
            logger.info("[IMAGE] Semaphore acquired, starting generation")
            for attempt in range(1, max_retries + 1):
                try:
                    workflow = build_workflow(
                        prompt=prompt,
                        width=width,
                        height=height,
                        seed=0,
                        filename_prefix=filename_prefix,
                    )

                    comfyui_req = (
                        f"Model: {model_cfg.label}\n"
                        f"Size: {width}x{height}\n"
                        f"Filename prefix: {filename_prefix}\n"
                        f"Attempt: {attempt}/{max_retries}\n\n"
                        f"--- PROMPT ---\n{prompt}\n\n"
                        f"--- WORKFLOW JSON ---\n{json.dumps(workflow, indent=2, ensure_ascii=False)}"
                    )
                    write_comfyui_log(
                        game_id=ctx_game,
                        player_id=ctx_player,
                        turn=ctx_turn,
                        kind=kind or "unspecified",
                        log_type="request",
                        content=comfyui_req,
                    )

                    prompt_id = await self._queue_prompt(workflow, kind=kind, ctx_game=ctx_game, ctx_player=ctx_player, ctx_turn=ctx_turn)
                    outputs = await self._wait_for_completion(prompt_id, timeout=COMFYUI_WAIT_TIMEOUT)
                    image_url = self._extract_image_url(outputs)

                    if image_url:
                        write_comfyui_log(
                            game_id=ctx_game,
                            player_id=ctx_player,
                            turn=ctx_turn,
                            kind=kind or "unspecified",
                            log_type="response",
                            content=f"URL: {image_url}\nPrompt ID: {prompt_id}",
                        )
                        logger.info(
                            "ComfyUI [%s] OK game=%s player=%s turn=%s | attempt=%d | url=%s",
                            kind or "unspecified",
                            ctx_game,
                            ctx_player,
                            ctx_turn,
                            attempt,
                            image_url,
                        )
                        return image_url
                    elif attempt < max_retries:
                        logger.warning(f"[IMAGE] No image in ComfyUI output (attempt {attempt}/{max_retries}), retrying...")
                    else:
                        logger.warning(f"[IMAGE] No image in ComfyUI output after {max_retries} attempts, giving up")

                except Exception as e:
                    logger.error(f"[IMAGE] Generation attempt {attempt}/{max_retries} failed: {e}", exc_info=True)
                    if attempt < max_retries:
                        wait = 2**attempt  # 2s, 4s, 8s backoff
                        logger.info(f"[IMAGE] Retrying in {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        logger.error(f"[IMAGE] All {max_retries} attempts exhausted, giving up", exc_info=True)
        return None

    async def generate_avatar_image(
        self,
        prompt: str,
        filename_prefix: str,
        width: int,
        height: int,
        *,
        game_id: str | None,
        player_id: str | None,
        turn: int | None,
        kind: str | None,
    ) -> str | None:
        """Generate a character avatar as an RGBA cutout on a transparent background.

        Qwen-Image-2.1 only activates its native alpha channel when the prompt
        follows the official transparent-image template, so the template is
        wrapped around the character description here (deterministically —
        the LLM-authored part describes the character only). Avatars are then
        clean reference cutouts for multi-reference scene composition, with no
        baked-in environment.
        """
        wrapped_prompt = (
            "This is an RGBA image with transparency.\n"
            f"{prompt}\n"
            "The image has alpha channel."
        )
        return await self.generate_image(
            prompt=wrapped_prompt,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
            max_retries=3,
            game_id=game_id,
            player_id=player_id,
            turn=turn,
            kind=kind,
        )

    async def generate_scene_image(
        self,
        prompt: str,
        filename_prefix: str,
        width: int,
        height: int,
        *,
        game_id: str | None,
        player_id: str | None,
        turn: int | None,
        kind: str | None,
    ) -> str | None:
        """Generate a scene image via ComfyUI."""
        return await self.generate_image(
            prompt=prompt,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
            max_retries=3,
            game_id=game_id,
            player_id=player_id,
            turn=turn,
            kind=kind,
        )

    @staticmethod
    def _extract_filename_from_url(url: str) -> str | None:
        """Extract the filename from a ComfyUI /view URL.

        Since input/ and output/ are now the same directory on disk,
        we can reference avatar files directly by their output filename.
        Also handles subfolder paths for game-scoped images.

        Args:
            url: ComfyUI view URL like
                 http://comfyui:8188/view?filename=avatar_281412419_00001_.png&subfolder=default_game&type=output

        Returns:
            A path like ``default_game/avatar_281412419_00001_.png`` for LoadImage,
            or None on failure.
        """
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            filenames = params.get("filename")
            subfolders = params.get("subfolder")
            if filenames and filenames[0]:
                fn = filenames[0]
                if subfolders and subfolders[0]:
                    return f"{subfolders[0]}/{fn}"
                return fn
        except Exception:
            logger.warning("Failed to extract filename from ComfyUI output", exc_info=True)
        return None

    def _build_img2img_workflow(
        self,
        prompt: str,
        reference_filename: str,
        denoise: float,
        width: int,
        height: int,
        seed: int,
        filename_prefix: str,
    ) -> dict[str, Any]:
        """Build a Z-Image Turbo img2img workflow using reference image as latent.

        Encodes the reference image into VAE latent space, then uses that
        as the starting latent for partial denoising (denoise=0.75 by default).
        This allows the CLIP conditioning to substantially change the scene/action
        while still retaining some character appearance from the reference.

        Args:
            prompt: Text prompt for the scene
            reference_filename: Uploaded filename in ComfyUI input folder
            width, height: Output dimensions
            seed: Random seed (0 = randomize)
            denoise: How much to denoise (0.0=no change, 1.0=completely new)
            filename_prefix: Output filename prefix

        Returns:
            ComfyUI workflow dict ready for /prompt API
        """
        if seed == 0:
            seed = secrets.randbelow(2**63 + 1)

        return {
            # Load reference image (uploaded to ComfyUI input folder)
            "40": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": reference_filename,
                },
            },
            # VAE Encode reference image to latent space
            "41": {
                "class_type": "VAEEncode",
                "inputs": {
                    "pixels": ["40", 0],
                    "vae": ["29", 0],
                },
            },
            # Load UNET model (Z-Image Turbo)
            "28": {
                "class_type": "UNETLoader",
                "inputs": {
                    "unet_name": "z_image_turbo_bf16.safetensors",
                    "weight_dtype": "default",
                },
            },
            # Load CLIP text encoder - Qwen for Z-Image Turbo
            "30": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": "qwen_3_4b.safetensors",
                    "type": "lumina2",
                },
            },
            # Load VAE
            "29": {
                "class_type": "VAELoader",
                "inputs": {
                    "vae_name": "ae.safetensors",
                },
            },
            # Encode positive prompt
            "27": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": prompt,
                    "clip": ["30", 0],
                },
            },
            # Apply AuraFlow sampling (required for Z-Image Turbo)
            "11": {
                "class_type": "ModelSamplingAuraFlow",
                "inputs": {
                    "model": ["28", 0],
                    "shift": 3.0,
                },
            },
            # KSampler — img2img with partial denoising
            # Latent comes from VAEEncode of reference image (node 41).
            # denoise=0.75 adds ~75% noise: CLIP prompt has ~6 effective steps
            # to reshape the image into the new scene/action while still
            # retaining some character structure from the reference.
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": 8,
                    "cfg": 1.0,
                    "sampler_name": "res_multistep",
                    "scheduler": "simple",
                    "denoise": denoise,
                    "model": ["11", 0],
                    "positive": ["27", 0],
                    "negative": ["33", 0],
                    "latent_image": ["41", 0],
                },
            },
            # ConditioningZeroOut for negative (required by Z-Image Turbo)
            "33": {
                "class_type": "ConditioningZeroOut",
                "inputs": {
                    "conditioning": ["27", 0],
                },
            },
            # Decode latent to image
            "8": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["3", 0],
                    "vae": ["29", 0],
                },
            },
            # Save image
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": filename_prefix,
                    "images": ["8", 0],
                },
            },
        }

    def _build_flux2_klein_img2img_workflow(
        self,
        prompt: str,
        reference_filename: str,
        denoise: float,
        width: int,
        height: int,
        seed: int,
        filename_prefix: str,
    ) -> dict[str, Any]:
        """Build a FLUX.2 [klein] 4B distilled GGUF img2img workflow.

        Mirrors :meth:`_build_flux2_klein_workflow` but starts from a
        VAE-encoded reference latent (the avatar) instead of an empty latent,
        so the action prompt reshapes the scene while retaining character
        structure.

        Uses the same FLUX.2 node set: Qwen3-4B CLIP (type=flux2),
        flux2-vae, EmptyFlux2LatentImage node type for 128-channel latents,
        ModelSamplingFlux, 4-step distilled sampling.
        """
        if seed == 0:
            seed = secrets.randbelow(2**63 + 1)

        return {
            # Load reference image (uploaded to ComfyUI input folder)
            "40": {
                "class_type": "LoadImage",
                "inputs": {"image": reference_filename},
            },
            # VAE Encode reference image to latent space
            "41": {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["40", 0], "vae": ["29", 0]},
            },
            # Load GGUF UNET (ComfyUI-GGUF custom node)
            "10": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {"unet_name": "flux-2-klein-4b-Q4_K_S.gguf"},
            },
            # CLIP text encoder: Qwen3-4B, type=flux2
            "30": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": "qwen_3_4b.safetensors",
                    "type": "flux2",
                },
            },
            # Load VAE (flux2-specific)
            "29": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": "flux2-vae.safetensors"},
            },
            # Encode positive prompt
            "27": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["30", 0]},
            },
            # ModelSamplingFlux — shift computed from resolution
            "11": {
                "class_type": "ModelSamplingFlux",
                "inputs": {
                    "model": ["10", 0],
                    "max_shift": 1.15,
                    "base_shift": 0.5,
                    "width": width,
                    "height": height,
                },
            },
            # KSampler — img2img with partial denoising.
            # Latent comes from VAEEncode of reference image (node 41).
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": 4,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": denoise,
                    "model": ["11", 0],
                    "positive": ["27", 0],
                    "negative": ["33", 0],
                    "latent_image": ["41", 0],
                },
            },
            # ConditioningZeroOut for negative (distilled FLUX uses empty negative)
            "33": {
                "class_type": "ConditioningZeroOut",
                "inputs": {"conditioning": ["27", 0]},
            },
            # Decode latent to image
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["29", 0]},
            },
            # Save image
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": filename_prefix,
                    "images": ["8", 0],
                },
            },
        }

    def _build_llada_img2img_workflow(
        self,
        prompt: str,
        reference_filename: str,
        denoise: float,
        width: int,
        height: int,
        seed: int,
        filename_prefix: str,
    ) -> dict[str, Any]:
        """Build a LLaDA-Image-Turbo img2img workflow via native editing.

        LLaDA has no KSampler/latent pipeline to partially denoise, so the
        classic VAEEncode + partial-denoise img2img path does not apply.
        Instead the reference image (the avatar) goes through the model's
        native ``generation_mode="editing"`` path (SigVQ image conditioning),
        reshaped by the prompt — closer to instruction editing than to
        denoise-strength img2img, so ``denoise`` is ignored.

        The editing path requires width/height divisible by 32 (all game
        image sizes — 768/1024/576 — satisfy this).
        """
        if seed == 0:
            seed = secrets.randbelow(2**63)

        return {
            # Monolithic pipeline loader (same files as the txt2img builder)
            "1": {
                "class_type": "LLaDAImageLoader",
                "inputs": {
                    "diffusion_model": "LLaDA-Image-Turbo-INT8.safetensors",
                    "text_encoder": "LLaDA-Image-Turbo-text_encoder-Q4_K_M.gguf",
                    "vae": "LLaDa_VAE.safetensors",
                    "dtype": "bfloat16",
                    "offload": "cuda",
                },
            },
            # Load reference image (uploaded to ComfyUI input folder)
            "40": {
                "class_type": "LoadImage",
                "inputs": {"image": reference_filename},
            },
            # Native editing (Turbo: 4 steps, guidance 1.0)
            "3": {
                "class_type": "LLaDAImageEdit",
                "inputs": {
                    "pipeline": ["1", 0],
                    "image": ["40", 0],
                    "prompt": prompt,
                    "width": width,
                    "height": height,
                    "steps": 4,
                    "guidance_scale": 1.0,
                    "seed": seed,
                    "negative_prompt": "",
                },
            },
            # Save image
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": filename_prefix,
                    "images": ["3", 0],
                },
            },
        }

    async def generate_action_image_with_reference(
        self,
        prompt: str,
        reference_image_url: str | None,
        character_description: str,
        filename_prefix: str,
        width: int,
        height: int,
        *,
        denoise: float,
        game_id: str | None,
        player_id: str | None,
        turn: int | None,
        kind: str | None,
    ) -> str:
        """Generate an action scene image using avatar as visual reference.

        Tries img2img workflow first — encodes the avatar into VAE latent
        space, then partially denoises with the action prompt (denoise=0.75)
        to substantially change the scene while retaining some character features.

        Falls back to text-to-image with character description in prompt
        if no reference image is available or if img2img fails.

        Args:
            prompt: Main action prompt for the scene
            reference_image_url: URL of the avatar image to use as reference
            character_description: Text description of the character (fallback)
            filename_prefix: Output filename prefix
            width, height: Output dimensions
            denoise: Denoising strength for img2img (0.0=no change, 1.0=completely new)

        Returns:
            URL of the generated image, or placeholder on failure
        """
        # Try img2img workflow first (if reference image available)
        if reference_image_url:
            try:
                ref_filename = self._extract_filename_from_url(reference_image_url)
                if ref_filename:
                    model_key = resolve_img2img_model(kind)
                    model_cfg = get_model_config(model_key)
                    builder_method = _IMG2IMG_BUILDER_METHODS[model_cfg.builder]
                    workflow = getattr(self, builder_method)(
                        prompt=prompt,
                        reference_filename=ref_filename,
                        width=width,
                        height=height,
                        seed=0,
                        denoise=denoise,
                        filename_prefix=filename_prefix,
                    )

                    ctx_game = game_id or "none"
                    ctx_player = str(player_id) if player_id else ""
                    ctx_turn = str(turn) if turn is not None else "0"
                    log_kind = kind or "img2img"
                    comfyui_req = (
                        f"Model: {model_cfg.label} (img2img, denoise={denoise})\n"
                        f"Size: {width}x{height}\n"
                        f"Filename prefix: {filename_prefix}\n\n"
                        f"--- PROMPT ---\n{prompt}\n\n"
                        f"--- REFERENCE IMAGE ---\n{ref_filename}\n\n"
                        f"--- WORKFLOW JSON ---\n{json.dumps(workflow, indent=2, ensure_ascii=False)}"
                    )
                    write_comfyui_log(
                        game_id=ctx_game,
                        player_id=ctx_player,
                        turn=ctx_turn,
                        kind=log_kind,
                        log_type="request",
                        content=comfyui_req,
                    )
                    async with _image_semaphore:
                        prompt_id = await self._queue_prompt(workflow, kind=kind, ctx_game=ctx_game, ctx_player=ctx_player, ctx_turn=ctx_turn)
                        outputs = await self._wait_for_completion(prompt_id, timeout=300)
                        image_url = self._extract_image_url(outputs)

                    if image_url:
                        write_comfyui_log(
                            game_id=ctx_game,
                            player_id=ctx_player,
                            turn=ctx_turn,
                            kind=log_kind,
                            log_type="response",
                            content=f"URL: {image_url}\nPrompt ID: {prompt_id}",
                        )
                        logger.info(f"[ACTION_IMAGE] Generated via img2img: {image_url}")
                        return image_url
                    else:
                        logger.warning("[ACTION_IMAGE] img2img produced no output, falling back to text-to-image")
                else:
                    logger.warning(f"[ACTION_IMAGE] Could not parse filename from reference URL {reference_image_url}, falling back to text-to-image")
            except Exception as e:
                logger.warning(f"[ACTION_IMAGE] img2img failed: {e}, falling back to text-to-image")

        # Fallback: text-to-image with character description in prompt
        fallback_prompt = prompt if prompt else ""
        if character_description:
            fallback_prompt += f" Character reference: {character_description}."

        image_url = await self.generate_scene_image(
            prompt=fallback_prompt,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
            game_id=game_id,
            player_id=player_id,
            turn=turn,
            kind=kind,
        )

        if image_url:
            logger.info(f"[ACTION_IMAGE] Generated (fallback text-to-image): {image_url}")
            return image_url

        logger.warning("[ACTION_IMAGE] Generation failed completely, using placeholder")
        return f"/content/comics/{filename_prefix}_placeholder.webp"

    async def generate_character_in_scene(
        self,
        instruction_prompt: str,
        character_avatar_url: str | None,
        background_url: str | None,
        *,
        character_description: str,
        filename_prefix: str,
        width: int,
        height: int,
        game_id: str | None,
        player_id: str | None,
        turn: int | None,
        kind: str | None,
        species_category: str = "",
    ) -> str | None:
        """Place a character into a scene via Qwen-Image-Edit-2511.

        Uses the character avatar as a semantic reference (not img2img noise):
        Qwen-Image-Edit preserves the character's identity while following the
        instruction prompt. When a background image is supplied it is passed as
        a second reference so the character is composed into that environment.

        Falls back to img2img (:meth:`generate_action_image_with_reference`)
        when Qwen-Image-Edit is unavailable or fails — e.g. no avatar, or the
        GGUF model/custom node is missing from the ComfyUI instance.

        Args:
            instruction_prompt: Instruction referring to "Picture 1" (the
                character) and optionally "Picture 2" (the background).
            character_avatar_url: URL of the avatar to place into the scene.
            background_url: Optional URL of a pre-generated empty background.
            character_description: Short text for the img2img fallback.
            filename_prefix: Output filename prefix.
            width, height: Output dimensions.
            game_id, player_id, turn, kind: Logging context.

        Returns:
            URL of the generated image, or None on failure.
        """
        if not character_avatar_url:
            logger.warning("[QWEN_EDIT] No character avatar, falling back to img2img")
            return await self.generate_action_image_with_reference(
                prompt=instruction_prompt,
                reference_image_url=None,
                character_description=character_description,
                filename_prefix=filename_prefix,
                width=width,
                height=height,
                denoise=0.75,
                game_id=game_id,
                player_id=player_id,
                turn=turn,
                kind=kind,
            )

        char_filename = self._extract_filename_from_url(character_avatar_url)
        bg_filename = (
            self._extract_filename_from_url(background_url) if background_url else None
        )
        if not char_filename:
            logger.warning(
                "[QWEN_EDIT] Could not parse avatar filename from %s, falling back",
                character_avatar_url,
            )
            return await self.generate_action_image_with_reference(
                prompt=instruction_prompt,
                reference_image_url=character_avatar_url,
                character_description=character_description,
                filename_prefix=filename_prefix,
                width=width,
                height=height,
                denoise=0.75,
                game_id=game_id,
                player_id=player_id,
                turn=turn,
                kind=kind,
            )

        edit_model_key = resolve_edit_model(kind)
        edit_cfg = get_edit_model_config(edit_model_key)
        edit_builder = _EDIT_BUILDERS[edit_cfg.builder]
        workflow = edit_builder(
            instruction=instruction_prompt,
            character_filename=char_filename,
            background_filename=bg_filename,
            width=width,
            height=height,
            seed=0,
            filename_prefix=filename_prefix,
            cfg=edit_cfg,
            species_category=species_category,
        )

        ctx_game = game_id or "none"
        ctx_player = str(player_id) if player_id else ""
        ctx_turn = str(turn) if turn is not None else "0"
        log_kind = kind or "qwen_edit"

        # The edit path is identity-preserving. It can time out under GPU
        # contention (the ComfyUI queue backs up); before degrading to img2img
        # (which loses the character's identity at denoise=0.75), retry once so
        # the prompt re-enters the queue after contention clears.
        max_edit_attempts = 2
        for attempt in range(1, max_edit_attempts + 1):
            try:
                comfyui_req = (
                    f"Model: {edit_cfg.label}\n"
                    f"Size: {width}x{height}\n"
                    f"Filename prefix: {filename_prefix}\n"
                    f"Attempt: {attempt}/{max_edit_attempts}\n\n"
                    f"--- INSTRUCTION ---\n{instruction_prompt}\n\n"
                    f"--- CHARACTER AVATAR ---\n{char_filename}\n\n"
                    f"--- BACKGROUND ---\n{bg_filename or '(none)'}\n\n"
                    f"--- WORKFLOW JSON ---\n{json.dumps(workflow, indent=2, ensure_ascii=False)}"
                )
                write_comfyui_log(
                    game_id=ctx_game,
                    player_id=ctx_player,
                    turn=ctx_turn,
                    kind=log_kind,
                    log_type="request",
                    content=comfyui_req,
                )
                async with _image_semaphore:
                    prompt_id = await self._queue_prompt(
                        workflow, kind=kind, ctx_game=ctx_game, ctx_player=ctx_player, ctx_turn=ctx_turn
                    )
                    outputs = await self._wait_for_completion(prompt_id, timeout=600)
                    image_url = self._extract_image_url(outputs)
                if image_url:
                    write_comfyui_log(
                        game_id=ctx_game,
                        player_id=ctx_player,
                        turn=ctx_turn,
                        kind=log_kind,
                        log_type="response",
                        content=f"URL: {image_url}\nPrompt ID: {prompt_id}",
                    )
                    logger.info("[QWEN_EDIT] Generated: %s", image_url)
                    return image_url
                logger.warning(
                    "[QWEN_EDIT] No output (attempt %d/%d)", attempt, max_edit_attempts
                )
            except Exception:
                if attempt < max_edit_attempts:
                    logger.warning(
                        "[QWEN_EDIT] attempt %d/%d failed, retrying before img2img fallback",
                        attempt,
                        max_edit_attempts,
                        exc_info=True,
                    )
                    await asyncio.sleep(5)
                    continue
                logger.warning("[QWEN_EDIT] failed after %d attempts, falling back to img2img", attempt, exc_info=True)

        return await self.generate_action_image_with_reference(
            prompt=instruction_prompt,
            reference_image_url=character_avatar_url,
            character_description=character_description,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
            denoise=0.75,
            game_id=game_id,
            player_id=player_id,
            turn=turn,
            kind=kind,
        )

    # ============== Batch Image Generation ==============

    LOADING_IMAGE_PROMPTS = (
        "Starship bridge main computer console glowing with holographic star charts, 'SYSTEM BOOT' text display, blue neon lights, Star Trek style, cinematic shot from captain's chair perspective, 4K",
        "Starship computer core room with towering data pillars, energy conduits pulsing with blue light, holographic displays flickering to life, 'LOADING...' floating text, sci-fi interior",
        "Captain's chair on starship bridge viewed from behind, panoramic viewscreen showing starfield, consoles powering up, amber and blue indicator lights, 'LOADING SYSTEMS' hologram",
        "Starship engineering room warp core pulsing with blue energy, LCARS displays booting up, holographic status readouts, 'POWERING UP' text on screens, cinematic lighting",
        "Starship navigation console with interactive star map hologram, tactical display panels activating, 'CALIBRATING SENSORS' overlay, sci-fi UI elements, glowing buttons",
        "View from starship observation deck windows showing nebula, holographic data streams reflecting on glass, ambient blue lighting, 'WELCOME ABOARD' floating interface prompt",
        "Starship AI core chamber with crystalline data storage, floating light particles, neural interface glowing patterns, 'NEURAL LINK ESTABLISHED' text, ethereal blue-white lighting",
        "Helm station on starship bridge, holographic flight path projections, warp engine status displays, 'NAVIGATION SYSTEMS ONLINE' readout, amber alert glow",
        "Starship medical bay with biobeds, holographic patient scans, 'MEDICAL SYSTEMS LOADING' display, clean white-blue lighting, futuristic medical equipment",
        "Starship armory or security station with weapon lockers, tactical holographic map, 'SECURITY SYSTEMS ARMED' display, red-blue alert lighting, sci-fi interior",
    )

    async def generate_loading_images(
        self,
        count: int,
        start_index: int,
        filename_prefix: str,
        *,
        game_id: str,
        width: int,
        height: int,
    ) -> list[str]:
        """Generate N loading screen images for /start display.

        Args:
            count: Number of images to generate
            start_index: Starting index in LOADING_IMAGE_PROMPTS (for resuming)
            filename_prefix: Prefix for output files
            game_id: Game ID to scope generated filenames
            width: Image width
            height: Image height

        Returns:
            List of generated image URLs.
        """
        logger.info(f"[IMAGE] Generating {count} loading images (start={start_index})")
        urls = []

        for offset in range(count):
            i = start_index + offset
            prompt = self.LOADING_IMAGE_PROMPTS[i % len(self.LOADING_IMAGE_PROMPTS)]
            try:
                url = await self.generate_image(
                    prompt=prompt,
                    filename_prefix=f"{game_id}/{filename_prefix}_{i + 1}",
                    width=width,
                    height=height,
                    max_retries=2,
                    game_id=game_id,
                    player_id=None,
                    turn=None,
                    kind="loading",
                )
                if url:
                    urls.append(url)
                    logger.info(f"[IMAGE] Loading image #{i + 1} generated: {url}...")
                else:
                    logger.warning(f"[IMAGE] Loading image #{i + 1} failed to generate")
            except Exception as e:
                logger.error(f"[IMAGE] Loading image #{i + 1} error: {e}", exc_info=True)

        logger.info(f"[IMAGE] Generated {len(urls)}/{count} loading images")
        return urls

    async def generate_bridge_image(
        self,
        prompt: str,
        crew_descriptions: list[dict[str, str]],
        avatar_urls: list[str | None] | None,
        filename_prefix: str,
        *,
        game_id: str,
        width: int,
        height: int,
    ) -> str | None:
        """Generate a bridge scene image with the crew, avatar-consistent.

        Qwen-Image-2.1 composes the whole scene in ONE call from up to 10
        avatar reference images (``images.image_1..N`` on
        TextEncodeQwenImage21): the prompt refers to each crew member as
        "the character from Picture N", so their looks match the avatars the
        players already know from /team. No sequential Qwen-Image-Edit
        compositing — the unified 2.1 checkpoint handles multi-reference
        generation natively.

        Falls back to plain txt2img (:meth:`generate_scene_image`) when no
        usable avatar references exist or the multi-reference generation
        fails — the bridge is then crew-agnostic (prompt-only).

        Args:
            prompt: Bridge scene prompt from the LLM, referring to the
                references as "Picture N".
            crew_descriptions: Where each crew member is positioned; appended
                to the prompt as conditioning detail.
            avatar_urls: Avatar image URLs in Picture order (Picture 1 first).
                Entries may be None/unparseable and are skipped.
            filename_prefix: Prefix for output file.
            game_id: Game to scope the image to.
            width: Image width.
            height: Image height.

        Returns:
            URL of the generated image, or None on failure
        """
        logger.info("[BRIDGE] Generating bridge scene image")
        # Crew positions are appended ONLY on the prompt-only txt2img fallback.
        # With avatar references the bridge_prompt already stages every
        # character, and re-describing them in an unbound trailing block makes
        # the model render extra/duplicated figures (the enrichment text has
        # no Picture binding, so the model reuses the last-reinforced
        # reference for it — e.g. the science officer twice).
        fallback_prompt = prompt
        if crew_descriptions:
            positions = "; ".join([f"{d.get('role', '?')}: {d.get('position_description', '')}" for d in crew_descriptions])
            fallback_prompt = f"{prompt}. Crew positions: {positions}"

        ref_filenames = []
        for url in avatar_urls or []:
            if not url:
                continue
            filename = self._extract_filename_from_url(url)
            if filename:
                ref_filenames.append(filename)
            else:
                logger.warning("[BRIDGE] Could not parse avatar filename from %s, skipping reference", url)

        if not ref_filenames:
            logger.info("[BRIDGE] No avatar references, generating crew-agnostic txt2img")
            return await self.generate_scene_image(
                prompt=fallback_prompt,
                filename_prefix=filename_prefix,
                width=width,
                height=height,
                game_id=game_id,
                player_id=None,
                turn=None,
                kind="bridge",
            )

        workflow = _build_qwen_image_21_multiref_workflow(
            prompt=prompt,
            reference_filenames=ref_filenames,
            width=width,
            height=height,
            seed=0,
            filename_prefix=filename_prefix,
        )

        # Multi-reference conditioning is heavier than plain txt2img (10 vision
        # slots + reference latents); allow the queue to drain before retrying.
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                comfyui_req = (
                    f"Model: Qwen-Image-2.1 multi-reference ({len(ref_filenames)} pictures)\n"
                    f"Size: {width}x{height}\n"
                    f"Filename prefix: {filename_prefix}\n"
                    f"Attempt: {attempt}/{max_attempts}\n\n"
                    f"--- PROMPT ---\n{prompt}\n\n"
                    f"--- REFERENCE AVATARS ---\n" + "\n".join(f"Picture {i}: {fn}" for i, fn in enumerate(ref_filenames, start=1)) + "\n\n"
                    f"--- WORKFLOW JSON ---\n{json.dumps(workflow, indent=2, ensure_ascii=False)}"
                )
                write_comfyui_log(
                    game_id=game_id,
                    player_id="",
                    turn="0",
                    kind="bridge",
                    log_type="request",
                    content=comfyui_req,
                )
                async with _image_semaphore:
                    prompt_id = await self._queue_prompt(
                        workflow, kind="bridge", ctx_game=game_id, ctx_player="", ctx_turn="0"
                    )
                    outputs = await self._wait_for_completion(prompt_id, timeout=600)
                    image_url = self._extract_image_url(outputs)
                if image_url:
                    write_comfyui_log(
                        game_id=game_id,
                        player_id="",
                        turn="0",
                        kind="bridge",
                        log_type="response",
                        content=f"URL: {image_url}\nPrompt ID: {prompt_id}",
                    )
                    logger.info("[BRIDGE] Generated with %d references: %s", len(ref_filenames), image_url)
                    return image_url
                logger.warning("[BRIDGE] No output (attempt %d/%d)", attempt, max_attempts)
            except Exception:
                if attempt < max_attempts:
                    logger.warning(
                        "[BRIDGE] multi-reference attempt %d/%d failed, retrying before txt2img fallback",
                        attempt,
                        max_attempts,
                        exc_info=True,
                    )
                    await asyncio.sleep(5)
                    continue
                logger.warning("[BRIDGE] multi-reference generation failed, falling back to txt2img", exc_info=True)

        return await self.generate_scene_image(
            prompt=fallback_prompt,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
            game_id=game_id,
            player_id=None,
            turn=None,
            kind="bridge",
        )

    async def generate_splash_images(
        self,
        game_title: str,
        welcome_text: str,
        count: int,
        filename_prefix: str,
        *,
        game_id: str,
        width: int,
        height: int,
    ) -> list[str]:
        """Generate N splash images based on game title and description.

        Args:
            game_title: The generated game/ship title
            welcome_text: The atmospheric welcome description
            count: Number of splash images to generate
            filename_prefix: Prefix for output files
            game_id: Game ID to scope generated filenames

        Returns:
            List of generated image URLs.
        """
        logger.info(f"[IMAGE] Generating {count} splash images for: {game_title}...")

        prompts = [
            f"Epic establishing shot of {game_title}. {welcome_text}. Wide-angle view of starship exterior, nebula background, Star Trek style, cinematic lighting, 4K quality, space opera aesthetic.",
            f"Starship bridge interior scene for: {game_title}. {welcome_text}. Crew at stations, holographic displays, warm interior light through viewport showing stars, cinematic composition.",
            f"Dramatic space scene: {game_title}. {welcome_text}. Starship flying through cosmic phenomenon, lens flare, starfield, deep space colors, epic sci-fi art style, 4K.",
        ]

        urls = []
        for i in range(count):
            prompt = prompts[i] if i < len(prompts) else prompts[0]
            try:
                url = await self.generate_image(
                    prompt=prompt,
                    filename_prefix=f"{game_id}/{filename_prefix}_{i + 1}",
                    width=width,
                    height=height,
                    max_retries=2,
                    game_id=game_id,
                    player_id=None,
                    turn=None,
                    kind="splash",
                )
                if url:
                    urls.append(url)
                    logger.info(f"[IMAGE] Splash image {i + 1}/{count} generated: {url}...")
                else:
                    logger.warning(f"[IMAGE] Splash image {i + 1}/{count} failed")
            except Exception as e:
                logger.error(f"[IMAGE] Splash image {i + 1}/{count} error: {e}", exc_info=True)

        logger.info(f"[IMAGE] Generated {len(urls)}/{count} splash images")
        return urls


# ============== Factory Function ==============


def create_image_generator() -> ImageGenerator:
    """Create and configure ImageGenerator instance"""
    return ImageGenerator()
