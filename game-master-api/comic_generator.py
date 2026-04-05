"""
Image Generator - Direct ComfyUI API integration for image generation

Calls ComfyUI /prompt API directly instead of going through Pixelle-MCP.
Uses Z-Image Turbo model for text-to-image generation.

Model combination (verified working):
  UNET: z_image_turbo_bf16.safetensors
  CLIP: qwen_3_4b.safetensors (type: lumina2)
  VAE:  ae.safetensors
"""

import os
import json
import uuid
import logging
import random
import aiohttp
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


# ============== ComfyUI Workflow Templates ==============


def _build_zimage_turbo_workflow(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    seed: int = 0,
    filename_prefix: str = "ComfyUI",
) -> Dict[str, Any]:
    """Build a Z-Image Turbo text-to-image workflow for ComfyUI API.

    Uses the correct model combination:
      UNET: z_image_turbo_bf16.safetensors (distilled, 8 steps)
      CLIP: qwen_3_4b.safetensors, type=lumina2 (produces 2560-dim embeddings)
      VAE:  ae.safetensors
      ConditioningZeroOut for negative (required by Z-Image Turbo)
    """
    if seed == 0:
        seed = random.randint(0, 2**63)

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


class ImageGenerator:
    """
    Generates images using ComfyUI API directly via Z-Image Turbo model.
    """

    def __init__(self):
        self.comfyui_url = os.getenv("COMFYUI_URL", "http://comfyui:8188")
        self.client_id = str(uuid.uuid4())

    async def _queue_prompt(self, workflow: Dict[str, Any]) -> str:
        """Submit a workflow to ComfyUI /prompt endpoint and return the prompt_id."""
        payload = {
            "prompt": workflow,
            "client_id": self.client_id,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.comfyui_url}/prompt",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(
                        f"ComfyUI /prompt error {resp.status}: {error_text}"
                    )
                result = await resp.json()
                prompt_id = result.get("prompt_id")
                logger.info(f"ComfyUI prompt queued: {prompt_id}")
                return prompt_id

    async def _wait_for_completion(
        self, prompt_id: str, timeout: int = 300
    ) -> Dict[str, Any]:
        """Wait for ComfyUI to finish processing a prompt via /history endpoint."""
        import asyncio

        start = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start) < timeout:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.comfyui_url}/history/{prompt_id}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        history = await resp.json()
                        if prompt_id in history:
                            status = history[prompt_id].get("status", {})
                            if (
                                status.get("completed", False)
                                or status.get("status_str") == "success"
                            ):
                                outputs = history[prompt_id].get("outputs", {})
                                logger.info(f"ComfyUI prompt {prompt_id} completed")
                                return outputs
                            elif status.get("status_str") == "error":
                                raise Exception(f"ComfyUI execution error: {status}")
            await asyncio.sleep(2)

        raise TimeoutError(f"ComfyUI prompt {prompt_id} timed out after {timeout}s")

    def _extract_image_url(self, outputs: Dict[str, Any]) -> Optional[str]:
        """Extract image URL from ComfyUI outputs."""
        for node_id, node_output in outputs.items():
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
        filename_prefix: str = "ComfyUI",
        width: int = 1024,
        height: int = 1024,
    ) -> Optional[str]:
        """Generate an image via ComfyUI using Z-Image Turbo.

        Args:
            prompt: Detailed image generation prompt (English)
            filename_prefix: Prefix for output filename
            width: Image width (multiple of 16)
            height: Image height (multiple of 16)

        Returns:
            URL of the generated image, or None on failure
        """
        logger.info(f"[IMAGE] Generating image via Z-Image Turbo")
        logger.info(f"[IMAGE] Prompt: {prompt[:100]}...")
        logger.info(f"[IMAGE] Size: {width}x{height}")

        try:
            workflow = _build_zimage_turbo_workflow(
                prompt=prompt,
                width=width,
                height=height,
                filename_prefix=filename_prefix,
            )

            prompt_id = await self._queue_prompt(workflow)
            outputs = await self._wait_for_completion(prompt_id, timeout=180)
            image_url = self._extract_image_url(outputs)

            if image_url:
                logger.info(f"[IMAGE] Image generated: {image_url}")
            else:
                logger.warning("[IMAGE] No image in ComfyUI output")

            return image_url

        except Exception as e:
            logger.error(f"[IMAGE] Image generation failed: {e}")
            return None

    async def generate_avatar_image(
        self,
        prompt: str,
        filename_prefix: str = "avatar",
        width: int = 768,
        height: int = 1024,
    ) -> Optional[str]:
        """Generate a character avatar image via ComfyUI.

        Alias for generate_image with portrait-oriented defaults.
        """
        return await self.generate_image(
            prompt=prompt,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
        )

    async def generate_scene_image(
        self,
        prompt: str,
        filename_prefix: str = "scene",
        width: int = 1024,
        height: int = 1024,
    ) -> Optional[str]:
        """Generate a scene image via ComfyUI."""
        return await self.generate_image(
            prompt=prompt,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
        )

    async def generate_character_image(
        self,
        character_name: str,
        role: str,
        traits: List[str],
        scene_description: str = "",
    ) -> Optional[str]:
        """Generate a character image (backward compatible interface)."""
        prompt = (
            f"Sci-fi character portrait: {character_name}, {role}. "
            f"Personality traits: {', '.join(traits)}. "
            f"{scene_description} "
            f"Futuristic uniform, detailed face, cinematic lighting, 4K quality. "
            f"Space opera style, Star Trek aesthetic. Portrait, upper body."
        )

        return await self.generate_avatar_image(
            prompt=prompt,
            filename_prefix=f"char_{character_name.replace(' ', '_')}",
        )

    async def generate_personalized_comic(
        self,
        day: int,
        story: str,
        player_profile: Dict[str, Any],
        npc_dialogues: List[Dict[str, str]],
    ) -> str:
        """Generate a personalized comic/scene for a player.

        For now generates a single key scene image.
        Full multi-panel comic strip can be added later.
        """
        role = player_profile.get("role", "Crew Member")
        traits = player_profile.get("personality_traits", [])

        prompt = (
            f"Sci-fi comic book panel: {role} in action during a space mission. "
            f"Story: {story[:200]}. "
            f"Character traits: {', '.join(traits)}. "
            f"Dynamic action pose, dramatic lighting, detailed environment. "
            f"Space opera comic book style, vibrant colors, 4K quality."
        )

        image_url = await self.generate_scene_image(
            prompt=prompt,
            filename_prefix=f"comic_day{day}_{role.replace(' ', '_')}",
        )

        if image_url:
            logger.info(f"Generated comic for day {day}: {image_url}")
            return image_url
        else:
            logger.warning(f"Comic generation failed for day {day}, using placeholder")
            return f"/content/comics/day_{day}_placeholder.webp"


# Backward compatibility alias
ComicGenerator = ImageGenerator


# ============== Factory Function ==============


def create_comic_generator() -> ImageGenerator:
    """Create and configure ImageGenerator instance"""
    return ImageGenerator()
