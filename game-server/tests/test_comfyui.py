"""
Tests for ComfyUI image generation via Z-Image Turbo workflow.

Tests verify:
1. Workflow JSON structure is correct
2. ComfyUI API connectivity and prompt submission
3. Image generation end-to-end (requires running ComfyUI)
4. Avatar prompt generation from LLM
"""

import asyncio
import json
import os
import sys
import unittest
import urllib.request
from unittest.mock import patch

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from image_generator import (
    ImageGenerator,
    _build_qwen_edit_workflow,
    _build_zimage_turbo_workflow,
    create_image_generator,
)


class TestZImageTurboWorkflow(unittest.TestCase):
    """Test Z-Image Turbo workflow JSON structure."""

    def test_basic_workflow_structure(self):
        """Workflow should have all required nodes."""
        wf = _build_zimage_turbo_workflow(prompt="test prompt")

        required_nodes = ["28", "30", "29", "27", "13", "11", "3", "33", "8", "9"]
        for node_id in required_nodes:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_unet_loader(self):
        """UNETLoader should use z_image_turbo_bf16."""
        wf = _build_zimage_turbo_workflow(prompt="test")
        unet = wf["28"]

        self.assertEqual(unet["class_type"], "UNETLoader")
        self.assertEqual(unet["inputs"]["unet_name"], "z_image_turbo_bf16.safetensors")

    def test_clip_loader(self):
        """CLIPLoader must use qwen_3_4b with type lumina2."""
        wf = _build_zimage_turbo_workflow(prompt="test")
        clip = wf["30"]

        self.assertEqual(clip["class_type"], "CLIPLoader")
        self.assertEqual(clip["inputs"]["clip_name"], "qwen_3_4b.safetensors")
        self.assertEqual(clip["inputs"]["type"], "lumina2")

    def test_vae_loader(self):
        """VAELoader should use ae.safetensors."""
        wf = _build_zimage_turbo_workflow(prompt="test")
        vae = wf["29"]

        self.assertEqual(vae["class_type"], "VAELoader")
        self.assertEqual(vae["inputs"]["vae_name"], "ae.safetensors")

    def test_ksampler_settings(self):
        """KSampler should use 8 steps, cfg=1.0, res_multistep."""
        wf = _build_zimage_turbo_workflow(prompt="test")
        ksampler = wf["3"]

        self.assertEqual(ksampler["class_type"], "KSampler")
        self.assertEqual(ksampler["inputs"]["steps"], 8)
        self.assertEqual(ksampler["inputs"]["cfg"], 1.0)
        self.assertEqual(ksampler["inputs"]["sampler_name"], "res_multistep")
        self.assertEqual(ksampler["inputs"]["scheduler"], "simple")
        self.assertEqual(ksampler["inputs"]["denoise"], 1.0)

    def test_model_sampling_aura_flow(self):
        """ModelSamplingAuraFlow should use shift=3.0."""
        wf = _build_zimage_turbo_workflow(prompt="test")
        aura = wf["11"]

        self.assertEqual(aura["class_type"], "ModelSamplingAuraFlow")
        self.assertEqual(aura["inputs"]["shift"], 3.0)

    def test_conditioning_zero_out(self):
        """Negative conditioning should use ConditioningZeroOut."""
        wf = _build_zimage_turbo_workflow(prompt="test")
        zero_out = wf["33"]

        self.assertEqual(zero_out["class_type"], "ConditioningZeroOut")
        # Should connect to positive conditioning output
        self.assertEqual(zero_out["inputs"]["conditioning"], ["27", 0])

    def test_custom_dimensions(self):
        """Workflow should respect custom width/height."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=768, height=1024)
        latent = wf["13"]

        self.assertEqual(latent["inputs"]["width"], 768)
        self.assertEqual(latent["inputs"]["height"], 1024)

    def test_custom_filename_prefix(self):
        """Workflow should pass filename_prefix to SaveImage."""
        wf = _build_zimage_turbo_workflow(prompt="test", filename_prefix="avatar_123")
        save = wf["9"]

        self.assertEqual(save["inputs"]["filename_prefix"], "avatar_123")

    def test_seed_zero_generates_random(self):
        """When seed=0, should generate a random seed."""
        wf1 = _build_zimage_turbo_workflow(prompt="test", seed=0)
        wf2 = _build_zimage_turbo_workflow(prompt="test", seed=0)

        seed1 = wf1["3"]["inputs"]["seed"]
        seed2 = wf2["3"]["inputs"]["seed"]

        # Very unlikely to be equal with random seeds
        self.assertNotEqual(seed1, seed2)

    def test_fixed_seed(self):
        """When seed is provided, should use it exactly."""
        wf = _build_zimage_turbo_workflow(prompt="test", seed=42)

        self.assertEqual(wf["3"]["inputs"]["seed"], 42)

    def test_prompt_in_text_encode(self):
        """Prompt should be passed to CLIPTextEncode node."""
        wf = _build_zimage_turbo_workflow(prompt="a starship captain portrait")

        self.assertEqual(wf["27"]["inputs"]["text"], "a starship captain portrait")

    def test_node_connections(self):
        """All node links should reference valid node IDs."""
        wf = _build_zimage_turbo_workflow(prompt="test")
        all_node_ids = set(wf.keys())

        for node_id, node in wf.items():
            for _key, value in node["inputs"].items():
                if isinstance(value, list) and len(value) == 2:
                    linked_node, slot = value
                    self.assertIn(
                        str(linked_node),
                        all_node_ids,
                        f"Node {node_id} links to non-existent node {linked_node}",
                    )

    def test_workflow_is_valid_json(self):
        """Workflow should serialize to valid JSON."""
        wf = _build_zimage_turbo_workflow(prompt="test")
        json_str = json.dumps(wf)
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as e:
            self.fail(f"Workflow JSON is invalid: {e}")
        self.assertEqual(parsed, wf)


class TestQwenEditWorkflow(unittest.TestCase):
    """Test Qwen-Image-Edit-2511 workflow JSON structure."""

    def test_required_nodes_no_background(self):
        """Workflow without background should use single-reference mode."""
        wf = _build_qwen_edit_workflow(
            instruction="Place the character...", character_filename="avatar.png"
        )
        required = ["10", "30", "29", "41", "50", "70", "75", "90", "100", "110", "120"]
        for node_id in required:
            self.assertIn(node_id, wf, f"Missing node {node_id}")
        # No background loader when background_filename is None
        self.assertNotIn("42", wf)
        # Single-reference conditioning node
        self.assertEqual(wf["70"]["class_type"], "TextEncodeQwenImageEdit")

    def test_required_nodes_with_background(self):
        """Workflow with background should use Plus (two-image) mode."""
        wf = _build_qwen_edit_workflow(
            instruction="Place the character...",
            character_filename="avatar.png",
            background_filename="bg.png",
        )
        self.assertIn("42", wf)
        self.assertEqual(wf["42"]["class_type"], "LoadImage")
        self.assertEqual(wf["42"]["inputs"]["image"], "bg.png")
        self.assertEqual(wf["70"]["class_type"], "TextEncodeQwenImageEditPlus")
        # image1 = character, image2 = background
        self.assertEqual(wf["70"]["inputs"]["image1"], ["41", 0])
        self.assertEqual(wf["70"]["inputs"]["image2"], ["42", 0])

    def test_gguf_loader(self):
        """UnetLoaderGGUF should load the Q4_K_M GGUF model."""
        wf = _build_qwen_edit_workflow(instruction="t", character_filename="a.png")
        self.assertEqual(wf["10"]["class_type"], "UnetLoaderGGUF")
        self.assertEqual(wf["10"]["inputs"]["unet_name"], "qwen-image-edit-2511-Q4_K_M.gguf")

    def test_clip_loader_qwen_image(self):
        """CLIPLoader should use qwen_2.5_vl with type qwen_image."""
        wf = _build_qwen_edit_workflow(instruction="t", character_filename="a.png")
        clip = wf["30"]
        self.assertEqual(clip["class_type"], "CLIPLoader")
        self.assertEqual(clip["inputs"]["clip_name"], "qwen_2.5_vl_7b_fp8_scaled.safetensors")
        self.assertEqual(clip["inputs"]["type"], "qwen_image")

    def test_vae_loader(self):
        """VAELoader should use the Qwen-Image VAE (not the Z-Image ae)."""
        wf = _build_qwen_edit_workflow(instruction="t", character_filename="a.png")
        self.assertEqual(wf["29"]["inputs"]["vae_name"], "qwen_image_vae.safetensors")

    def test_lightning_lora(self):
        """LoraLoaderModelOnly should attach the Lightning LoRA at strength 1.0."""
        wf = _build_qwen_edit_workflow(instruction="t", character_filename="a.png")
        lora = wf["50"]
        self.assertEqual(lora["class_type"], "LoraLoaderModelOnly")
        self.assertIn("Lightning-4steps", lora["inputs"]["lora_name"])
        self.assertEqual(lora["inputs"]["strength_model"], 1.0)

    def test_ksampler_lightning_4_steps(self):
        """KSampler should use 4 steps (Lightning LoRA) with euler/simple."""
        wf = _build_qwen_edit_workflow(instruction="t", character_filename="a.png")
        ks = wf["100"]
        self.assertEqual(ks["class_type"], "KSampler")
        self.assertEqual(ks["inputs"]["steps"], 4)
        self.assertEqual(ks["inputs"]["cfg"], 1.0)
        self.assertEqual(ks["inputs"]["sampler_name"], "euler")
        self.assertEqual(ks["inputs"]["scheduler"], "simple")
        self.assertEqual(ks["inputs"]["denoise"], 1.0)

    def test_layered_latent(self):
        """EmptyQwenImageLayeredLatentImage should use layers=3."""
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", width=768, height=1024
        )
        latent = wf["90"]
        self.assertEqual(latent["class_type"], "EmptyQwenImageLayeredLatentImage")
        self.assertEqual(latent["inputs"]["layers"], 3)
        self.assertEqual(latent["inputs"]["width"], 768)
        self.assertEqual(latent["inputs"]["height"], 1024)

    def test_instruction_in_conditioning(self):
        """Instruction text should be passed to the TextEncode node."""
        wf = _build_qwen_edit_workflow(
            instruction="Place the character from Picture 1 at the console.",
            character_filename="a.png",
        )
        self.assertEqual(
            wf["70"]["inputs"]["prompt"],
            "Place the character from Picture 1 at the console.",
        )

    def test_node_connections_valid(self):
        """All node links should reference valid node IDs."""
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", background_filename="bg.png"
        )
        all_node_ids = set(wf.keys())
        for node_id, node in wf.items():
            for _key, value in node["inputs"].items():
                if isinstance(value, list) and len(value) == 2:
                    linked_node, _slot = value
                    self.assertIn(
                        str(linked_node),
                        all_node_ids,
                        f"Node {node_id} links to non-existent node {linked_node}",
                    )

    def test_seed_zero_randomizes(self):
        """seed=0 should produce different seeds across calls."""
        wf1 = _build_qwen_edit_workflow(instruction="t", character_filename="a.png", seed=0)
        wf2 = _build_qwen_edit_workflow(instruction="t", character_filename="a.png", seed=0)
        self.assertNotEqual(wf1["100"]["inputs"]["seed"], wf2["100"]["inputs"]["seed"])

    def test_serializable_json(self):
        """Workflow should round-trip through JSON."""
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", background_filename="bg.png"
        )
        self.assertEqual(json.loads(json.dumps(wf)), wf)


class TestImageGeneratorUnit(unittest.TestCase):
    """Unit tests for ImageGenerator (mocked HTTP calls)."""

    def test_factory_function(self):
        """create_comic_generator() should return ImageGenerator."""
        gen = create_image_generator()
        self.assertIsInstance(gen, ImageGenerator)

    def test_default_comfyui_url(self):
        """Default ComfyUI URL should be http://comfyui:8188."""
        gen = ImageGenerator()
        self.assertEqual(gen.comfyui_url, "http://comfyui:8188")

    def test_custom_comfyui_url(self):
        """Should respect COMFYUI_URL env var."""
        with patch.dict(os.environ, {"COMFYUI_URL": "http://custom:9999"}):
            gen = ImageGenerator()
            self.assertEqual(gen.comfyui_url, "http://custom:9999")

    def test_extract_image_url(self):
        """Should extract image URL from ComfyUI outputs."""
        gen = ImageGenerator()
        outputs = {
            "9": {
                "images": [
                    {
                        "filename": "avatar_001.png",
                        "subfolder": "",
                        "type": "output",
                    }
                ]
            }
        }
        url = gen._extract_image_url(outputs)
        self.assertIn("avatar_001.png", url or "")
        self.assertIn("/view?", url or "")

    def test_extract_image_url_no_images(self):
        """Should return None when no images in output."""
        gen = ImageGenerator()
        outputs = {"9": {"images": []}}
        url = gen._extract_image_url(outputs)
        self.assertIsNone(url)

    def test_extract_image_url_empty_outputs(self):
        """Should return None for empty outputs."""
        gen = ImageGenerator()
        url = gen._extract_image_url({})
        self.assertIsNone(url)


class TestImageGeneratorIntegration(unittest.TestCase):
    """Integration tests that require running ComfyUI service.

    Run with: COMFYUI_URL=http://localhost:8188 python -m pytest tests/test_comfyui.py -v -k integration
    """

    def setUp(self):
        self.comfyui_url = os.getenv("COMFYUI_URL", "http://localhost:8188")
        self.gen = ImageGenerator()
        self.gen.comfyui_url = self.comfyui_url

    def _check_comfyui_available(self):
        """Skip test if ComfyUI is not running."""
        try:
            urllib.request.urlopen(f"{self.comfyui_url}/system_stats", timeout=5)
            return True
        except Exception:
            return False

    def test_comfyui_connectivity(self):
        """ComfyUI should be reachable."""
        if not self._check_comfyui_available():
            self.skipTest("ComfyUI not running")

        resp = urllib.request.urlopen(f"{self.comfyui_url}/system_stats")
        self.assertEqual(resp.status, 200)

    def test_submit_workflow(self):
        """Should submit Z-Image Turbo workflow and get prompt_id."""
        if not self._check_comfyui_available():
            self.skipTest("ComfyUI not running")

        wf = _build_zimage_turbo_workflow(
            prompt="test image, simple geometric shapes",
            width=512,
            height=512,
            filename_prefix="test_verify",
        )

        async def _test():
            prompt_id = await self.gen._queue_prompt(wf)
            self.assertIsNotNone(prompt_id)
            self.assertTrue(len(prompt_id) > 0)
            return prompt_id

        prompt_id = asyncio.get_event_loop().run_until_complete(_test())

        # Wait for completion
        async def _wait():
            outputs = await self.gen._wait_for_completion(prompt_id, timeout=120)
            return outputs

        outputs = asyncio.get_event_loop().run_until_complete(_wait())
        self.assertIsNotNone(outputs)

        image_url = self.gen._extract_image_url(outputs)
        self.assertIsNotNone(image_url, "No image URL in ComfyUI output")

    def test_full_avatar_generation(self):
        """End-to-end avatar generation test."""
        if not self._check_comfyui_available():
            self.skipTest("ComfyUI not running")

        async def _test():
            url = await self.gen.generate_avatar_image(
                prompt="Sci-fi character portrait: Chief Engineer. Technical specialist in engineering suit. Futuristic uniform, detailed face, cinematic lighting. Space opera style.",
                filename_prefix="test_avatar",
                width=512,
                height=512,
            )
            return url

        url = asyncio.get_event_loop().run_until_complete(_test())
        self.assertIsNotNone(url, "Avatar generation returned None")
        assert url is not None
        self.assertIn("/view?", url)
        print(f"\nGenerated avatar URL: {url}")


if __name__ == "__main__":
    unittest.main()
