"""
Image Generator - Direct ComfyUI API integration for image generation

Calls ComfyUI /prompt API directly for image generation.
Uses Z-Image Turbo model for text-to-image generation.

Model combination (verified working):
  UNET: z_image_turbo_bf16.safetensors
  CLIP: qwen_3_4b.safetensors (type: lumina2)
  VAE:  ae.safetensors
"""

import asyncio
import json
import logging
import os
import random
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

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


def _build_zimage_turbo_workflow(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    seed: int = 0,
    filename_prefix: str = "ComfyUI",
) -> dict[str, Any]:
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

    async def _queue_prompt(self, workflow: dict[str, Any]) -> str:
        """Submit a workflow to ComfyUI /prompt endpoint and return the prompt_id."""
        payload = {
            "prompt": workflow,
            "client_id": self.client_id,
        }

        # Log full ComfyUI workflow JSON
        logger.info("=== COMFYUI WORKFLOW ===")
        logger.info(f"Endpoint: {self.comfyui_url}/prompt")
        logger.info(f"Client ID: {self.client_id}")
        logger.info(f"Workflow JSON:\n{json.dumps(workflow, indent=2, ensure_ascii=False)}")
        logger.info("=== END COMFYUI WORKFLOW ===")

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
                    logger.error(f"ComfyUI connection failed after {max_retries} attempts: {last_error}")

        raise Exception(f"Failed to connect to ComfyUI after {max_retries} attempts: {last_error}")

    async def _wait_for_completion(self, prompt_id: str, timeout: int = 300) -> dict[str, Any]:
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
        filename_prefix: str = "ComfyUI",
        width: int = 1024,
        height: int = 1024,
        max_retries: int = 3,
    ) -> str | None:
        """Generate an image via ComfyUI using Z-Image Turbo with retry.

        On each retry the seed is randomized, so a transient failure or
        bad output will produce a different image on the next attempt.

        Args:
            prompt: Detailed image generation prompt (English)
            filename_prefix: Prefix for output filename
            width: Image width (multiple of 16)
            height: Image height (multiple of 16)
            max_retries: Number of generation attempts before giving up (default: 3)

        Returns:
            URL of the generated image, or None on failure
        """
        logger.info("=== IMAGE GENERATION REQUEST ===")
        logger.info("Model: Z-Image Turbo (8-step distilled)")
        logger.info(f"Size: {width}x{height}")
        logger.info(f"Filename prefix: {filename_prefix}")
        logger.info(f"Max retries: {max_retries}")
        logger.info("--- PROMPT TEXT ---")
        for line in prompt.split("\n"):
            logger.info(line)
        logger.info("=== END IMAGE GENERATION REQUEST ===")

        logger.info("[IMAGE] Generating image via Z-Image Turbo")
        logger.info(f"[IMAGE] Size: {width}x{height}, max_retries={max_retries}")
        logger.info(f"[IMAGE] Acquiring ComfyUI semaphore ({_image_semaphore._value}/{COMFYUI_IMAGE_CONCURRENCY} slots available)...")

        async with _image_semaphore:
            logger.info("[IMAGE] Semaphore acquired, starting generation")
            for attempt in range(1, max_retries + 1):
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
                        return image_url
                    elif attempt < max_retries:
                        logger.warning(f"[IMAGE] No image in ComfyUI output (attempt {attempt}/{max_retries}), retrying...")
                    else:
                        logger.warning(f"[IMAGE] No image in ComfyUI output after {max_retries} attempts, giving up")

                except Exception as e:
                    logger.error(f"[IMAGE] Generation attempt {attempt}/{max_retries} failed: {e}")
                    if attempt < max_retries:
                        wait = 2**attempt  # 2s, 4s, 8s backoff
                        logger.info(f"[IMAGE] Retrying in {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        logger.error(f"[IMAGE] All {max_retries} attempts exhausted, giving up")

        return None

    async def generate_avatar_image(
        self,
        prompt: str,
        filename_prefix: str = "avatar",
        game_id: str = "default_game",
        width: int = 768,
        height: int = 1024,
    ) -> str | None:
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
    ) -> str | None:
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
        traits: list[str],
        scene_description: str = "",
    ) -> str | None:
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
            filename_prefix=f"default_game/char_{character_name.replace(' ', '_')}",
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
            pass
        return None

    def _build_img2img_workflow(
        self,
        prompt: str,
        reference_filename: str,
        width: int = 1024,
        height: int = 1024,
        seed: int = 0,
        denoise: float = 0.75,
        filename_prefix: str = "action",
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
            seed = random.randint(0, 2**63)

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

    async def generate_action_image_with_reference(
        self,
        prompt: str,
        reference_image_url: str | None,
        character_description: str = "",
        filename_prefix: str = "action",
        width: int = 1024,
        height: int = 1024,
        denoise: float = 0.75,
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
                    workflow = self._build_img2img_workflow(
                        prompt=prompt,
                        reference_filename=ref_filename,
                        width=width,
                        height=height,
                        denoise=denoise,
                        filename_prefix=filename_prefix,
                    )

                    async with _image_semaphore:
                        prompt_id = await self._queue_prompt(workflow)
                        outputs = await self._wait_for_completion(prompt_id, timeout=300)
                        image_url = self._extract_image_url(outputs)

                    if image_url:
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
        )

        if image_url:
            logger.info(f"[ACTION_IMAGE] Generated (fallback text-to-image): {image_url}")
            return image_url

        logger.warning("[ACTION_IMAGE] Generation failed completely, using placeholder")
        return f"/content/comics/{filename_prefix}_placeholder.webp"

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
        count: int = 10,
        start_index: int = 0,
        filename_prefix: str = "loading",
        game_id: str = "default_game",
        width: int = 768,
        height: int = 768,
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
                )
                if url:
                    urls.append(url)
                    logger.info(f"[IMAGE] Loading image #{i + 1} generated: {url}...")
                else:
                    logger.warning(f"[IMAGE] Loading image #{i + 1} failed to generate")
            except Exception as e:
                logger.error(f"[IMAGE] Loading image #{i + 1} error: {e}")

        logger.info(f"[IMAGE] Generated {len(urls)}/{count} loading images")
        return urls

    async def generate_bridge_image(
        self,
        prompt: str,
        crew_descriptions: list[dict[str, str]],
        avatar_urls: list[str | None] | None = None,
        filename_prefix: str = "bridge",
        game_id: str = "default_game",
        width: int = 1024,
        height: int = 768,
    ) -> str | None:
        """Generate a bridge scene image with the crew.

        Currently uses the standard Z-Image Turbo workflow with a detailed prompt.
        When ComfyUI supports reference image features (ControlNet / IP-Adapter),
        this will use avatar_urls as reference images for consistent crew appearance.

        Args:
            prompt: Detailed bridge scene prompt from LLM
            crew_descriptions: Where each crew member is positioned
            avatar_urls: Optional list of avatar image URLs for reference (future IP-Adapter)
            filename_prefix: Prefix for output file
            game_id: Game to scope the image to
            width: Image width
            height: Image height

        Returns:
            URL of the generated bridge image
        """
        logger.info("[BRIDGE] Generating bridge scene image")
        if avatar_urls:
            logger.info(f"[BRIDGE] {len([u for u in avatar_urls if u])} avatar references available")

        # Enhanced prompt with crew positioning details
        enriched_prompt = prompt
        if crew_descriptions:
            positions = "; ".join([f"{d.get('role', '?')}: {d.get('position_description', '')}" for d in crew_descriptions])
            enriched_prompt = f"{prompt}. Crew positions: {positions}"

        return await self.generate_image(
            prompt=enriched_prompt,
            filename_prefix=f"{game_id}/{filename_prefix}",
            width=width,
            height=height,
        )

    async def generate_splash_images(
        self,
        game_title: str,
        welcome_text: str,
        count: int = 3,
        filename_prefix: str = "splash",
        game_id: str = "default_game",
        width: int = 1024,
        height: int = 768,
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
                )
                if url:
                    urls.append(url)
                    logger.info(f"[IMAGE] Splash image {i + 1}/{count} generated: {url}...")
                else:
                    logger.warning(f"[IMAGE] Splash image {i + 1}/{count} failed")
            except Exception as e:
                logger.error(f"[IMAGE] Splash image {i + 1}/{count} error: {e}")

        logger.info(f"[IMAGE] Generated {len(urls)}/{count} splash images")
        return urls


# ============== Factory Function ==============


def create_image_generator() -> ImageGenerator:
    """Create and configure ImageGenerator instance"""
    return ImageGenerator()
