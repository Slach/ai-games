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
    _EDIT_BUILDERS,
    _MULTIREF_BUILDERS,
    _TXT2IMG_BUILDERS,
    _build_flux2_klein_workflow,
    _build_flux3_klein_workflow,
    _build_qwen_edit_workflow,
    _build_qwen_image_21_multiref_workflow,
    _build_qwen_image_21_viggle_turbo_multiref_workflow,
    _build_qwen_image_21_viggle_turbo_workflow,
    _build_qwen_image_2512_workflow,
    _build_qwen_edit_21_viggle_turbo_workflow,
    _build_zimage_turbo_workflow,
    create_image_generator,
)
import comfyui_config
from comfyui_config import get_edit_model_config, resolve_edit_model


class TestZImageTurboWorkflow(unittest.TestCase):
    """Test Z-Image Turbo workflow JSON structure."""

    def test_basic_workflow_structure(self):
        """Workflow should have all required nodes."""
        wf = _build_zimage_turbo_workflow(prompt="test prompt", width=1024, height=1024, seed=0, filename_prefix="")

        required_nodes = ["28", "30", "29", "27", "13", "11", "3", "33", "8", "9"]
        for node_id in required_nodes:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_unet_loader(self):
        """UNETLoader should use z_image_turbo_bf16."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        unet = wf["28"]

        self.assertEqual(unet["class_type"], "UNETLoader")
        self.assertEqual(unet["inputs"]["unet_name"], "z_image_turbo_bf16.safetensors")

    def test_clip_loader(self):
        """CLIPLoader must use qwen_3_4b with type lumina2."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        clip = wf["30"]

        self.assertEqual(clip["class_type"], "CLIPLoader")
        self.assertEqual(clip["inputs"]["clip_name"], "qwen_3_4b.safetensors")
        self.assertEqual(clip["inputs"]["type"], "lumina2")

    def test_vae_loader(self):
        """VAELoader should use ae.safetensors."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        vae = wf["29"]

        self.assertEqual(vae["class_type"], "VAELoader")
        self.assertEqual(vae["inputs"]["vae_name"], "ae.safetensors")

    def test_ksampler_settings(self):
        """KSampler should use 8 steps, cfg=1.0, res_multistep."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        ksampler = wf["3"]

        self.assertEqual(ksampler["class_type"], "KSampler")
        self.assertEqual(ksampler["inputs"]["steps"], 8)
        self.assertEqual(ksampler["inputs"]["cfg"], 1.0)
        self.assertEqual(ksampler["inputs"]["sampler_name"], "res_multistep")
        self.assertEqual(ksampler["inputs"]["scheduler"], "simple")
        self.assertEqual(ksampler["inputs"]["denoise"], 1.0)

    def test_model_sampling_aura_flow(self):
        """ModelSamplingAuraFlow should use shift=3.0."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        aura = wf["11"]

        self.assertEqual(aura["class_type"], "ModelSamplingAuraFlow")
        self.assertEqual(aura["inputs"]["shift"], 3.0)

    def test_conditioning_zero_out(self):
        """Negative conditioning should use ConditioningZeroOut."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        zero_out = wf["33"]

        self.assertEqual(zero_out["class_type"], "ConditioningZeroOut")
        # Should connect to positive conditioning output
        self.assertEqual(zero_out["inputs"]["conditioning"], ["27", 0])

    def test_custom_dimensions(self):
        """Workflow should respect custom width/height."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=768, height=1024, seed=0, filename_prefix="")
        latent = wf["13"]

        self.assertEqual(latent["inputs"]["width"], 768)
        self.assertEqual(latent["inputs"]["height"], 1024)

    def test_custom_filename_prefix(self):
        """Workflow should pass filename_prefix to SaveImage."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="avatar_123")
        save = wf["9"]

        self.assertEqual(save["inputs"]["filename_prefix"], "avatar_123")

    def test_seed_zero_generates_random(self):
        """When seed=0, should generate a random seed."""
        wf1 = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        wf2 = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")

        seed1 = wf1["3"]["inputs"]["seed"]
        seed2 = wf2["3"]["inputs"]["seed"]

        # Very unlikely to be equal with random seeds
        self.assertNotEqual(seed1, seed2)

    def test_fixed_seed(self):
        """When seed is provided, should use it exactly."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=42, filename_prefix="")

        self.assertEqual(wf["3"]["inputs"]["seed"], 42)

    def test_prompt_in_text_encode(self):
        """Prompt should be passed to CLIPTextEncode node."""
        wf = _build_zimage_turbo_workflow(prompt="a starship captain portrait", width=1024, height=1024, seed=0, filename_prefix="")

        self.assertEqual(wf["27"]["inputs"]["text"], "a starship captain portrait")

    def test_node_connections(self):
        """All node links should reference valid node IDs."""
        wf = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
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
        wf = _build_zimage_turbo_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        json_str = json.dumps(wf)
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as e:
            self.fail(f"Workflow JSON is invalid: {e}")
        self.assertEqual(parsed, wf)


class TestFlux2KleinWorkflow(unittest.TestCase):
    """Test FLUX.2 [klein] 4B distilled GGUF Q4_K_S workflow JSON structure."""

    def test_basic_workflow_structure(self):
        """Workflow should have all required nodes for FLUX.2 klein."""
        wf = _build_flux2_klein_workflow(prompt="test prompt", width=1024, height=1024, seed=0, filename_prefix="")
        required = ["10", "30", "29", "27", "13", "11", "3", "33", "8", "9"]
        for node_id in required:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_unet_loader_gguf(self):
        """UnetLoaderGGUF should load flux-2-klein-4b-Q4_K_S.gguf."""
        wf = _build_flux2_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        unet = wf["10"]
        self.assertEqual(unet["class_type"], "UnetLoaderGGUF")
        self.assertEqual(unet["inputs"]["unet_name"], "flux-2-klein-4b-Q4_K_S.gguf")

    def test_clip_loader_qwen3(self):
        """CLIPLoader should load qwen_3_4b with type=flux2."""
        wf = _build_flux2_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        clip = wf["30"]
        self.assertEqual(clip["class_type"], "CLIPLoader")
        self.assertEqual(clip["inputs"]["clip_name"], "qwen_3_4b.safetensors")
        self.assertEqual(clip["inputs"]["type"], "flux2")

    def test_vae_is_flux2(self):
        """VAELoader should load flux2-vae.safetensors."""
        wf = _build_flux2_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        self.assertEqual(wf["29"]["class_type"], "VAELoader")
        self.assertEqual(wf["29"]["inputs"]["vae_name"], "flux2-vae.safetensors")

    def test_empty_flux2_latent(self):
        """Latent node should be EmptyFlux2LatentImage (128 channels)."""
        wf = _build_flux2_klein_workflow(prompt="test", width=768, height=1024, seed=0, filename_prefix="")
        latent = wf["13"]
        self.assertEqual(latent["class_type"], "EmptyFlux2LatentImage")
        self.assertEqual(latent["inputs"]["width"], 768)
        self.assertEqual(latent["inputs"]["height"], 1024)

    def test_model_sampling_flux(self):
        """ModelSamplingFlux should be used with max_shift=1.15, base_shift=0.5."""
        wf = _build_flux2_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        ms = wf["11"]
        self.assertEqual(ms["class_type"], "ModelSamplingFlux")
        self.assertEqual(ms["inputs"]["max_shift"], 1.15)
        self.assertEqual(ms["inputs"]["base_shift"], 0.5)

    def test_ksampler_4_steps(self):
        """KSampler should use 4 steps (distilled model)."""
        wf = _build_flux2_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        ks = wf["3"]
        self.assertEqual(ks["class_type"], "KSampler")
        self.assertEqual(ks["inputs"]["steps"], 4)
        self.assertEqual(ks["inputs"]["cfg"], 1.0)
        self.assertEqual(ks["inputs"]["sampler_name"], "euler")
        self.assertEqual(ks["inputs"]["scheduler"], "simple")

    def test_filename_prefix_passed(self):
        """filename_prefix should propagate to SaveImage node."""
        wf = _build_flux2_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="avatar_123")
        self.assertEqual(wf["9"]["inputs"]["filename_prefix"], "avatar_123")

    def test_seed_zero_randomized(self):
        """seed=0 should be replaced with a random positive integer."""
        wf1 = _build_flux2_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        wf2 = _build_flux2_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        seed1 = wf1["3"]["inputs"]["seed"]
        seed2 = wf2["3"]["inputs"]["seed"]
        self.assertNotEqual(seed1, seed2)
        self.assertGreater(seed1, 0)

    def test_serializable_json(self):
        """Workflow should round-trip through JSON."""
        wf = _build_flux2_klein_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        self.assertEqual(json.loads(json.dumps(wf)), wf)


class TestFlux3KleinWorkflow(unittest.TestCase):
    """Test FLUX.3 [klein] distilled int8 workflow JSON structure."""

    def test_basic_workflow_structure(self):
        """Workflow should have all required nodes for FLUX.3 klein."""
        wf = _build_flux3_klein_workflow(prompt="test prompt", width=1024, height=1024, seed=0, filename_prefix="")
        required = ["10", "30", "29", "27", "28", "13", "11", "3", "8", "9"]
        for node_id in required:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_unet_loader(self):
        """UNETLoader should load the int8 convrot checkpoint."""
        wf = _build_flux3_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        unet = wf["10"]
        self.assertEqual(unet["class_type"], "UNETLoader")
        self.assertEqual(unet["inputs"]["unet_name"], "flux3-image-klein-int8-convrot.safetensors")

    def test_clip_loader_flux3(self):
        """CLIPLoader should load qwen3vl_4b with type=flux3."""
        wf = _build_flux3_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        clip = wf["30"]
        self.assertEqual(clip["class_type"], "CLIPLoader")
        self.assertEqual(clip["inputs"]["clip_name"], "qwen3vl_4b_int8_convrot.safetensors")
        self.assertEqual(clip["inputs"]["type"], "flux3")

    def test_vae_is_flux2(self):
        """VAELoader should load flux2-vae.safetensors (shared with FLUX.2)."""
        wf = _build_flux3_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        self.assertEqual(wf["29"]["class_type"], "VAELoader")
        self.assertEqual(wf["29"]["inputs"]["vae_name"], "flux2-vae.safetensors")

    def test_negative_is_empty_clip_encode(self):
        """Negative should be an empty CLIPTextEncode (official template, cfg 1)."""
        wf = _build_flux3_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        self.assertEqual(wf["28"]["class_type"], "CLIPTextEncode")
        self.assertEqual(wf["28"]["inputs"]["text"], "")
        self.assertEqual(wf["3"]["inputs"]["negative"], ["28", 0])

    def test_empty_flux2_latent(self):
        """Latent node should be EmptyFlux2LatentImage (128 channels)."""
        wf = _build_flux3_klein_workflow(prompt="test", width=768, height=1024, seed=0, filename_prefix="")
        latent = wf["13"]
        self.assertEqual(latent["class_type"], "EmptyFlux2LatentImage")
        self.assertEqual(latent["inputs"]["width"], 768)
        self.assertEqual(latent["inputs"]["height"], 1024)

    def test_model_sampling_flux_ln16(self):
        """ModelSamplingFlux should use max_shift = base_shift = ln(16) ≈ 2.7726."""
        wf = _build_flux3_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        ms = wf["11"]
        self.assertEqual(ms["class_type"], "ModelSamplingFlux")
        self.assertAlmostEqual(ms["inputs"]["max_shift"], 2.7726, places=3)
        self.assertAlmostEqual(ms["inputs"]["base_shift"], 2.7726, places=3)

    def test_ksampler_16_steps(self):
        """KSampler should use 16 steps (distilled model), cfg 1.0, euler/simple."""
        wf = _build_flux3_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        ks = wf["3"]
        self.assertEqual(ks["class_type"], "KSampler")
        self.assertEqual(ks["inputs"]["steps"], 16)
        self.assertEqual(ks["inputs"]["cfg"], 1.0)
        self.assertEqual(ks["inputs"]["sampler_name"], "euler")
        self.assertEqual(ks["inputs"]["scheduler"], "simple")

    def test_filename_prefix_passed(self):
        """filename_prefix should propagate to SaveImage node."""
        wf = _build_flux3_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="avatar_123")
        self.assertEqual(wf["9"]["inputs"]["filename_prefix"], "avatar_123")

    def test_seed_zero_randomized(self):
        """seed=0 should be replaced with a random positive integer."""
        wf1 = _build_flux3_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        wf2 = _build_flux3_klein_workflow(prompt="test", width=512, height=512, seed=0, filename_prefix="")
        self.assertNotEqual(wf1["3"]["inputs"]["seed"], wf2["3"]["inputs"]["seed"])
        self.assertGreater(wf1["3"]["inputs"]["seed"], 0)

    def test_serializable_json(self):
        """Workflow should round-trip through JSON."""
        wf = _build_flux3_klein_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        self.assertEqual(json.loads(json.dumps(wf)), wf)


class TestFlux3KleinImg2ImgWorkflow(unittest.TestCase):
    """Test FLUX.3 [klein] distilled int8 img2img workflow."""

    def setUp(self):
        self.gen = ImageGenerator()

    def _build(self, **overrides):
        kwargs = dict(
            prompt="test", reference_filename="avatar.png", denoise=0.75,
            width=1024, height=1024, seed=0, filename_prefix="",
        )
        kwargs.update(overrides)
        return self.gen._build_flux3_klein_img2img_workflow(**kwargs)

    def test_basic_workflow_structure(self):
        """Workflow should have all required nodes including LoadImage/VAEEncode."""
        wf = self._build()
        required = ["40", "41", "10", "30", "29", "27", "28", "11", "3", "8", "9"]
        for node_id in required:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_unet_is_flux3_klein(self):
        """UNETLoader should load the int8 convrot checkpoint."""
        wf = self._build()
        self.assertEqual(wf["10"]["inputs"]["unet_name"], "flux3-image-klein-int8-convrot.safetensors")

    def test_clip_is_flux3(self):
        """CLIPLoader should use qwen3vl_4b_int8_convrot with type=flux3."""
        wf = self._build()
        self.assertEqual(wf["30"]["class_type"], "CLIPLoader")
        self.assertEqual(wf["30"]["inputs"]["type"], "flux3")

    def test_vae_encode_feeds_latent(self):
        """KSampler should sample from the VAEEncode latent (node 41), not an empty latent."""
        wf = self._build()
        self.assertEqual(wf["41"]["class_type"], "VAEEncode")
        self.assertEqual(wf["3"]["inputs"]["latent_image"], ["41", 0])

    def test_denoise_passed_through(self):
        """denoise parameter should propagate to the KSampler."""
        wf = self._build(denoise=0.6)
        self.assertEqual(wf["3"]["inputs"]["denoise"], 0.6)


class TestFlux2KleinImg2ImgWorkflow(unittest.TestCase):
    """Test FLUX.2 [klein] 4B distilled GGUF img2img workflow."""

    def setUp(self):
        self.gen = ImageGenerator()

    def _build(self, **overrides):
        kwargs = dict(
            prompt="test", reference_filename="avatar.png", denoise=0.75,
            width=1024, height=1024, seed=0, filename_prefix="",
        )
        kwargs.update(overrides)
        return self.gen._build_flux2_klein_img2img_workflow(**kwargs)

    def test_basic_workflow_structure(self):
        """Workflow should have all required nodes including LoadImage/VAEEncode."""
        wf = self._build()
        required = ["40", "41", "10", "30", "29", "27", "11", "3", "33", "8", "9"]
        for node_id in required:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_unet_is_flux2_klein(self):
        """UnetLoaderGGUF should load flux-2-klein-4b-Q4_K_S.gguf."""
        wf = self._build()
        self.assertEqual(wf["10"]["inputs"]["unet_name"], "flux-2-klein-4b-Q4_K_S.gguf")

    def test_clip_is_qwen3_flux2(self):
        """CLIPLoader should use qwen_3_4b with type=flux2."""
        wf = self._build()
        self.assertEqual(wf["30"]["class_type"], "CLIPLoader")
        self.assertEqual(wf["30"]["inputs"]["type"], "flux2")

    def test_vae_encode_feeds_latent(self):
        """KSampler should sample from the VAEEncode latent (node 41), not an empty latent."""
        wf = self._build()
        self.assertEqual(wf["41"]["class_type"], "VAEEncode")
        self.assertEqual(wf["3"]["inputs"]["latent_image"], ["41", 0])

    def test_ksampler_4_steps(self):
        """KSampler should use 4 steps for distilled model."""
        wf = self._build()
        self.assertEqual(wf["3"]["inputs"]["steps"], 4)


class TestQwenImage2512Workflow(unittest.TestCase):
    """Test Qwen-Image-2512 (Lightning) text-to-image workflow JSON structure."""

    def test_basic_workflow_structure(self):
        """Workflow should have all required nodes."""
        wf = _build_qwen_image_2512_workflow(prompt="test prompt", width=768, height=1024, seed=0, filename_prefix="")
        required_nodes = ["10", "11", "12", "30", "29", "27", "28", "13", "3", "8", "9"]
        for node_id in required_nodes:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_gguf_loader(self):
        """UnetLoaderGGUF should load the 2512 Q4_K_M GGUF."""
        wf = _build_qwen_image_2512_workflow(prompt="t", width=768, height=1024, seed=0, filename_prefix="")
        self.assertEqual(wf["10"]["class_type"], "UnetLoaderGGUF")
        self.assertEqual(wf["10"]["inputs"]["unet_name"], "qwen-image-2512-Q4_K_M.gguf")

    def test_lightning_lora_then_shift(self):
        """Lightning LoRA must sit between the UNET and ModelSamplingAuraFlow."""
        wf = _build_qwen_image_2512_workflow(prompt="t", width=768, height=1024, seed=0, filename_prefix="")
        lora = wf["11"]
        self.assertEqual(lora["class_type"], "LoraLoaderModelOnly")
        self.assertIn("2512-Lightning-4steps", lora["inputs"]["lora_name"])
        self.assertEqual(lora["inputs"]["strength_model"], 1.0)
        shift = wf["12"]
        self.assertEqual(shift["class_type"], "ModelSamplingAuraFlow")
        self.assertEqual(shift["inputs"]["shift"], 3.1)
        self.assertEqual(shift["inputs"]["model"], ["11", 0])

    def test_clip_and_vae_match_edit_model(self):
        """Text encoder and VAE are shared with the Qwen-Edit model files."""
        wf = _build_qwen_image_2512_workflow(prompt="t", width=768, height=1024, seed=0, filename_prefix="")
        self.assertEqual(wf["30"]["inputs"]["clip_name"], "qwen_2.5_vl_7b_fp8_scaled.safetensors")
        self.assertEqual(wf["30"]["inputs"]["type"], "qwen_image")
        self.assertEqual(wf["29"]["inputs"]["vae_name"], "qwen_image_vae.safetensors")

    def test_ksampler_lightning_settings(self):
        """KSampler should use Lightning settings: 4 steps, cfg 1.0, euler/simple."""
        wf = _build_qwen_image_2512_workflow(prompt="t", width=768, height=1024, seed=0, filename_prefix="")
        ks = wf["3"]
        self.assertEqual(ks["class_type"], "KSampler")
        self.assertEqual(ks["inputs"]["steps"], 4)
        self.assertEqual(ks["inputs"]["cfg"], 1.0)
        self.assertEqual(ks["inputs"]["sampler_name"], "euler")
        self.assertEqual(ks["inputs"]["scheduler"], "simple")
        self.assertEqual(ks["inputs"]["model"], ["12", 0])
        self.assertEqual(ks["inputs"]["latent_image"][0], "13")

    def test_sd3_latent(self):
        """Latent must be EmptySD3LatentImage (16-channel SD3-style)."""
        wf = _build_qwen_image_2512_workflow(prompt="t", width=768, height=1024, seed=0, filename_prefix="")
        self.assertEqual(wf["13"]["class_type"], "EmptySD3LatentImage")
        self.assertEqual(wf["13"]["inputs"]["width"], 768)
        self.assertEqual(wf["13"]["inputs"]["height"], 1024)

    def test_negative_prompt_not_empty(self):
        """Unlike distilled models, 2512 Lightning uses a real negative prompt."""
        wf = _build_qwen_image_2512_workflow(prompt="t", width=768, height=1024, seed=0, filename_prefix="")
        self.assertTrue(wf["28"]["inputs"]["text"].strip())
        self.assertEqual(wf["28"]["class_type"], "CLIPTextEncode")


class TestQwenEditWorkflow(unittest.TestCase):
    """Test Qwen-Image-Edit-2511 workflow JSON structure."""

    def setUp(self):
        """Resolve the active edit-model config once per test."""
        self.cfg = get_edit_model_config(resolve_edit_model(None))

    def test_required_nodes_no_background(self):
        """Workflow without background should use single-reference mode."""
        wf = _build_qwen_edit_workflow(
            instruction="Place the character...", character_filename="avatar.png", background_filename=None, width=1024, height=1024, seed=0, filename_prefix="", cfg=self.cfg
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
            width=1024,
            height=1024,
            seed=0,
            filename_prefix="",
            cfg=self.cfg,
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
        wf = _build_qwen_edit_workflow(instruction="t", character_filename="a.png", background_filename=None, width=1024, height=1024, seed=0, filename_prefix="", cfg=self.cfg)
        self.assertEqual(wf["10"]["class_type"], "UnetLoaderGGUF")
        self.assertEqual(wf["10"]["inputs"]["unet_name"], "qwen-image-edit-2511-Q4_K_M.gguf")

    def test_clip_loader_qwen_image(self):
        """CLIPLoader should use qwen_2.5_vl with type qwen_image."""
        wf = _build_qwen_edit_workflow(instruction="t", character_filename="a.png", background_filename=None, width=1024, height=1024, seed=0, filename_prefix="", cfg=self.cfg)
        clip = wf["30"]
        self.assertEqual(clip["class_type"], "CLIPLoader")
        self.assertEqual(clip["inputs"]["clip_name"], "qwen_2.5_vl_7b_fp8_scaled.safetensors")
        self.assertEqual(clip["inputs"]["type"], "qwen_image")

    def test_vae_loader(self):
        """VAELoader should use the Qwen-Image VAE (not the Z-Image ae)."""
        wf = _build_qwen_edit_workflow(instruction="t", character_filename="a.png", background_filename=None, width=1024, height=1024, seed=0, filename_prefix="", cfg=self.cfg)
        self.assertEqual(wf["29"]["inputs"]["vae_name"], "qwen_image_vae.safetensors")

    def test_lightning_lora(self):
        """LoraLoaderModelOnly should attach the Lightning LoRA at strength 1.0."""
        wf = _build_qwen_edit_workflow(instruction="t", character_filename="a.png", background_filename=None, width=1024, height=1024, seed=0, filename_prefix="", cfg=self.cfg)
        lora = wf["50"]
        self.assertEqual(lora["class_type"], "LoraLoaderModelOnly")
        self.assertIn("Lightning-4steps", lora["inputs"]["lora_name"])
        self.assertEqual(lora["inputs"]["strength_model"], 1.0)

    def test_ksampler_lightning_4_steps(self):
        """KSampler should use 4 steps (Lightning LoRA) with euler/simple."""
        wf = _build_qwen_edit_workflow(instruction="t", character_filename="a.png", background_filename=None, width=1024, height=1024, seed=0, filename_prefix="", cfg=self.cfg)
        ks = wf["100"]
        self.assertEqual(ks["class_type"], "KSampler")
        self.assertEqual(ks["inputs"]["steps"], 4)
        self.assertEqual(ks["inputs"]["cfg"], 1.0)
        self.assertEqual(ks["inputs"]["sampler_name"], "euler")
        self.assertEqual(ks["inputs"]["scheduler"], "simple")
        self.assertEqual(ks["inputs"]["denoise"], 1.0)

    def test_alien_species_no_lora_20_steps(self):
        """Non-humanoid species must skip Lightning LoRA and use 20 steps.

        At strength 1.0 the Lightning LoRA suppresses avatar identity and
        collapses non-humanoid characters into a humanoid. The LoRA node is
        removed and the sampler runs the full step budget directly on the
        GGUF model (8 steps at cfg 1.0 produced undercooked output).
        """
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", background_filename=None,
            width=1024, height=1024, seed=0, filename_prefix="",
            cfg=self.cfg,
            species_category="non_humanoid",
        )
        self.assertNotIn("50", wf)
        ks = wf["100"]
        self.assertEqual(ks["inputs"]["steps"], 20)
        self.assertEqual(ks["inputs"]["model"], ["10", 0])

    def test_energy_species_no_lora_20_steps(self):
        """Energy species must also skip Lightning LoRA and use 20 steps."""
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", background_filename=None,
            width=1024, height=1024, seed=0, filename_prefix="",
            cfg=self.cfg,
            species_category="energy",
        )
        self.assertNotIn("50", wf)
        self.assertEqual(wf["100"]["inputs"]["steps"], 20)
        self.assertEqual(wf["100"]["inputs"]["model"], ["10", 0])

    def test_human_species_keeps_lora(self):
        """Human species must keep Lightning LoRA at strength 1.0 and 4 steps."""
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", background_filename=None,
            width=1024, height=1024, seed=0, filename_prefix="",
            cfg=self.cfg,
            species_category="human",
        )
        self.assertIn("50", wf)
        self.assertEqual(wf["50"]["inputs"]["strength_model"], 1.0)
        self.assertEqual(wf["100"]["inputs"]["steps"], 4)
        self.assertEqual(wf["100"]["inputs"]["model"], ["50", 0])

    def test_layered_latent(self):
        """EmptyQwenImageLayeredLatentImage should use layers=0 to avoid 5D shard output."""
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", background_filename=None, width=768, height=1024, seed=0, filename_prefix="", cfg=self.cfg
        )
        latent = wf["90"]
        self.assertEqual(latent["class_type"], "EmptyQwenImageLayeredLatentImage")
        self.assertEqual(latent["inputs"]["layers"], 0)
        self.assertEqual(latent["inputs"]["width"], 768)
        self.assertEqual(latent["inputs"]["height"], 1024)

    def test_instruction_in_conditioning(self):
        """Instruction text should be passed to the TextEncode node."""
        wf = _build_qwen_edit_workflow(
            instruction="Place the character from Picture 1 at the console.",
            character_filename="a.png",
            background_filename=None,
            width=1024,
            height=1024,
            seed=0,
            filename_prefix="",
            cfg=self.cfg,
        )
        self.assertEqual(
            wf["70"]["inputs"]["prompt"],
            "Place the character from Picture 1 at the console.",
        )

    def test_node_connections_valid(self):
        """All node links should reference valid node IDs."""
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", background_filename="bg.png", width=1024, height=1024, seed=0, filename_prefix="", cfg=self.cfg
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
        wf1 = _build_qwen_edit_workflow(instruction="t", character_filename="a.png", background_filename=None, width=1024, height=1024, seed=0, filename_prefix="", cfg=self.cfg)
        wf2 = _build_qwen_edit_workflow(instruction="t", character_filename="a.png", background_filename=None, width=1024, height=1024, seed=0, filename_prefix="", cfg=self.cfg)
        self.assertNotEqual(wf1["100"]["inputs"]["seed"], wf2["100"]["inputs"]["seed"])

    def test_serializable_json(self):
        """Workflow should round-trip through JSON."""
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", background_filename="bg.png", width=1024, height=1024, seed=0, filename_prefix="", cfg=self.cfg
        )
        self.assertEqual(json.loads(json.dumps(wf)), wf)


class TestComfyUiConfig(unittest.TestCase):
    """Test kind -> txt2img model resolution in comfyui_config."""

    def test_avatar_uses_default(self):
        """Avatar kind should resolve to the global default model."""
        self.assertEqual(comfyui_config.resolve_txt2img_model("avatar"), comfyui_config.DEFAULT_TXT2IMG_MODEL)

    def test_npc_avatar_prefix_matches_default(self):
        """Parameterized npc_avatar_<role> should resolve to the global default."""
        self.assertEqual(comfyui_config.resolve_txt2img_model("npc_avatar_security"), comfyui_config.DEFAULT_TXT2IMG_MODEL)
        self.assertEqual(comfyui_config.resolve_txt2img_model("npc_avatar_engineer"), comfyui_config.DEFAULT_TXT2IMG_MODEL)

    def test_scene_uses_default(self):
        """Scene kind should fall back to the default model."""
        self.assertEqual(comfyui_config.resolve_txt2img_model("scene"), comfyui_config.DEFAULT_TXT2IMG_MODEL)

    def test_none_kind_uses_default(self):
        """A None kind should resolve to the default model."""
        self.assertEqual(comfyui_config.resolve_txt2img_model(None), comfyui_config.DEFAULT_TXT2IMG_MODEL)

    def test_unknown_kind_uses_default(self):
        """An unlisted kind should resolve to the default model."""
        self.assertEqual(comfyui_config.resolve_txt2img_model("splash"), comfyui_config.DEFAULT_TXT2IMG_MODEL)

    def test_default_is_flux3_klein(self):
        """Out of the box the default model should be flux3_klein."""
        self.assertEqual(comfyui_config.DEFAULT_TXT2IMG_MODEL, "flux3_klein")

    def test_env_override_changes_default(self):
        """COMFYUI_TXT2IMG_MODEL env should override the default model key."""
        with patch.object(comfyui_config, "DEFAULT_TXT2IMG_MODEL", "z_image_turbo"):
            # With the default flipped to Z-Image, an unknown kind resolves to it.
            self.assertEqual(comfyui_config.resolve_txt2img_model("splash"), "z_image_turbo")

    def test_get_model_config_known(self):
        """get_model_config should return a ModelConfig for registered keys."""
        cfg = comfyui_config.get_model_config("flux2_klein_4b_gguf_q4")
        self.assertEqual(cfg.builder, "flux2_klein_4b")

    def test_get_model_config_unknown_raises(self):
        """get_model_config should raise KeyError for an unregistered key."""
        with self.assertRaises(KeyError):
            comfyui_config.get_model_config("does_not_exist")

    def test_every_override_key_is_registered(self):
        """Every override value must index into MODELS (no dangling pointers)."""
        for model_key in comfyui_config.KIND_MODEL_OVERRIDES.values():
            self.assertIn(model_key, comfyui_config.MODELS)

    # ---- img2img shares the txt2img model family ----

    def test_img2img_avatar_uses_default(self):
        """img2img should resolve via the same table as txt2img (avatar→default)."""
        self.assertEqual(comfyui_config.resolve_img2img_model("avatar"), comfyui_config.DEFAULT_TXT2IMG_MODEL)

    def test_img2img_unknown_uses_default(self):
        """img2img of an unlisted kind should fall back to the txt2img default."""
        self.assertEqual(comfyui_config.resolve_img2img_model("player_action"), comfyui_config.DEFAULT_TXT2IMG_MODEL)

    def test_img2img_matches_txt2img_for_all_override_kinds(self):
        """For every override key, img2img and txt2img must agree."""
        for kind in comfyui_config.KIND_MODEL_OVERRIDES:
            self.assertEqual(
                comfyui_config.resolve_img2img_model(kind),
                comfyui_config.resolve_txt2img_model(kind),
            )

    # ---- edit (identity-preserving instruction editing) ----

    def test_edit_default_is_qwen(self):
        """Out of the box the default edit model should be qwen_image_edit_2511."""
        self.assertEqual(comfyui_config.DEFAULT_EDIT_MODEL, "qwen_image_edit_2511")

    def test_edit_none_kind_uses_default(self):
        """A None kind should resolve to the default edit model."""
        self.assertEqual(comfyui_config.resolve_edit_model(None), comfyui_config.DEFAULT_EDIT_MODEL)

    def test_edit_unknown_kind_uses_default(self):
        """An unlisted kind should resolve to the default edit model."""
        self.assertEqual(comfyui_config.resolve_edit_model("character_scene"), comfyui_config.DEFAULT_EDIT_MODEL)

    def test_edit_override_wins_over_default(self):
        """An explicit edit override should win over the default."""
        with patch.dict(comfyui_config.KIND_EDIT_OVERRIDES, {"character_scene": "qwen_image_edit_2511"}):
            self.assertEqual(comfyui_config.resolve_edit_model("character_scene"), "qwen_image_edit_2511")

    def test_edit_env_override_changes_default(self):
        """COMFYUI_EDIT_MODEL env should override the default edit model key."""
        with patch.object(comfyui_config, "DEFAULT_EDIT_MODEL", "qwen_image_edit_2511"):
            self.assertEqual(comfyui_config.resolve_edit_model("character_scene"), "qwen_image_edit_2511")

    def test_get_edit_model_config_known(self):
        """get_edit_model_config should return an EditModelConfig for registered keys."""
        cfg = comfyui_config.get_edit_model_config("qwen_image_edit_2511")
        self.assertEqual(cfg.builder, "qwen_image_edit")
        # File references are carried on the config, not hardcoded in the builder.
        self.assertTrue(cfg.unet.endswith(".gguf"))
        self.assertTrue(cfg.clip.endswith(".safetensors"))
        self.assertTrue(cfg.vae.endswith(".safetensors"))
        self.assertIn("Lightning", cfg.lora)

    def test_get_edit_model_config_unknown_raises(self):
        """get_edit_model_config should raise KeyError for an unregistered key."""
        with self.assertRaises(KeyError):
            comfyui_config.get_edit_model_config("does_not_exist")

    def test_every_edit_override_key_is_registered(self):
        """Every edit override value must index into EDIT_MODELS (no dangling pointers)."""
        for model_key in comfyui_config.KIND_EDIT_OVERRIDES.values():
            self.assertIn(model_key, comfyui_config.EDIT_MODELS)

    def test_edit_cfg_carries_distinct_vae_from_txt2img(self):
        """The Qwen edit model must use its own VAE, distinct from the shared ae.safetensors."""
        cfg = comfyui_config.get_edit_model_config("qwen_image_edit_2511")
        self.assertNotEqual(cfg.vae, "ae.safetensors")


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


class TestQwenImage21MultirefWorkflow(unittest.TestCase):
    """Test the Qwen-Image-2.1 multi-reference workflow JSON structure."""

    REFS = [
        "6c9kxv/avatar_281412419_00001_.png",
        "6c9kxv/avatar_captain_00001_.png",
        "6c9kxv/avatar_pilot_00001_.png",
    ]

    def _build(self, refs=None):
        return _build_qwen_image_21_multiref_workflow(
            prompt="Place the character from Picture 1 at the command throne.",
            reference_filenames=self.REFS if refs is None else refs,
            width=1024,
            height=1024,
            seed=0,
            filename_prefix="test/bridge",
        )

    def test_basic_workflow_structure(self):
        wf = self._build()
        for node_id in ["10", "30", "29", "45", "70", "90", "100", "110", "120"]:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_reference_load_image_nodes(self):
        wf = self._build()
        for i in range(1, 4):
            node_id = str(200 + i)
            self.assertEqual(wf[node_id]["class_type"], "LoadImage")
            self.assertEqual(wf[node_id]["inputs"]["image"], self.REFS[i - 1])

    def test_text_encode_reference_slots(self):
        wf = self._build()
        encode = wf["70"]
        self.assertEqual(encode["class_type"], "TextEncodeQwenImage21")
        for i in range(1, 4):
            self.assertEqual(encode["inputs"][f"images.image_{i}"], [str(200 + i), 0])
        # No 4th slot when only 3 references are passed
        self.assertNotIn("images.image_4", encode["inputs"])
        self.assertEqual(encode["inputs"]["resolution"], 0)
        self.assertEqual(encode["inputs"]["vae"], ["29", 0])

    def test_reference_nodes_do_not_collide_with_cache_node(self):
        """LoadImage IDs must not overwrite the QwenImage21Cache node (45)."""
        refs = [f"game/avatar_{i}_00001_.png" for i in range(6)]
        wf = self._build(refs=refs)
        self.assertEqual(wf["45"]["class_type"], "QwenImage21Cache")
        for i in range(1, 7):
            self.assertEqual(wf[str(200 + i)]["class_type"], "LoadImage")

    def test_references_capped_at_ten(self):
        refs = [f"game/avatar_{i}_00001_.png" for i in range(15)]
        wf = self._build(refs=refs)
        encode = wf["70"]
        for i in range(1, 11):
            self.assertIn(f"images.image_{i}", encode["inputs"])
        for i in range(11, 16):
            self.assertNotIn(f"images.image_{i}", encode["inputs"])

    def test_ksampler_settings(self):
        wf = self._build()
        sampler = wf["100"]
        self.assertEqual(sampler["inputs"]["steps"], 25)
        self.assertEqual(sampler["inputs"]["cfg"], 1.0)
        self.assertEqual(sampler["class_type"], "KSampler")
        # Model flows through QwenImage21Cache
        self.assertEqual(sampler["inputs"]["model"], ["45", 0])
        self.assertEqual(sampler["inputs"]["positive"], ["70", 0])
        self.assertEqual(sampler["inputs"]["negative"], ["70", 1])

    def test_output_size_and_prefix(self):
        wf = self._build()
        self.assertEqual(wf["90"]["inputs"]["width"], 1024)
        self.assertEqual(wf["90"]["inputs"]["height"], 1024)
        self.assertEqual(wf["120"]["inputs"]["filename_prefix"], "test/bridge")

    def test_no_references_still_builds_txt2img_shape(self):
        wf = self._build(refs=[])
        encode = wf["70"]
        self.assertNotIn("images.image_1", encode["inputs"])
        # Workflow remains serializable JSON
        json.dumps(wf)


VIGGLE_TURBO_SIGMA_NODES = "1.0, 0.9375, 0.875, 0.75, 0.5, 0.25"


class TestQwenImage21ViggleTurboWorkflow(unittest.TestCase):
    """Test the Qwen-Image-2.1 Viggle Turbo txt2img workflow JSON structure."""

    def _build(self, seed=0):
        return _build_qwen_image_21_viggle_turbo_workflow(
            prompt="capybara under a banana leaf",
            width=1248,
            height=832,
            seed=seed,
            filename_prefix="test/viggle",
        )

    def test_basic_workflow_structure(self):
        wf = self._build()
        for node_id in ["10", "30", "29", "15", "27", "13", "20", "21", "22", "19", "23", "8", "9"]:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_no_ksampler_cfg_free_path(self):
        """Turbo samples via BasicGuider/SamplerCustomAdvanced, never a KSampler."""
        wf = self._build()
        for node in wf.values():
            self.assertNotEqual(node["class_type"], "KSampler")

    def test_lora_applied_unmerged_on_model_path(self):
        wf = self._build()
        lora = wf["15"]
        self.assertEqual(lora["class_type"], "ViggleTurboLora")
        self.assertEqual(lora["inputs"]["model"], ["10", 0])
        self.assertEqual(lora["inputs"]["strength"], 1.0)
        self.assertEqual(
            lora["inputs"]["lora_name"],
            "Qwen-Image-2.1-viggle-turbo-v0.2.1-6step-lora-r128.safetensors",
        )
        # BasicGuider consumes the LoRA'd model; no CFG, no negative used
        guider = wf["19"]
        self.assertEqual(guider["class_type"], "BasicGuider")
        self.assertEqual(guider["inputs"]["model"], ["15", 0])
        self.assertEqual(guider["inputs"]["conditioning"], ["27", 0])

    def test_sigma_schedule(self):
        wf = self._build()
        sigmas = wf["22"]
        self.assertEqual(sigmas["class_type"], "ViggleTurboSigmas")
        self.assertEqual(sigmas["inputs"]["nodes"], VIGGLE_TURBO_SIGMA_NODES)
        # The shift is computed from the same latent that gets sampled
        self.assertEqual(sigmas["inputs"]["latent"], ["13", 0])

    def test_sampler_custom_advanced_wiring(self):
        wf = self._build()
        sampler = wf["23"]
        self.assertEqual(sampler["class_type"], "SamplerCustomAdvanced")
        self.assertEqual(sampler["inputs"]["noise"], ["20", 0])
        self.assertEqual(sampler["inputs"]["guider"], ["19", 0])
        self.assertEqual(sampler["inputs"]["sampler"], ["21", 0])
        self.assertEqual(sampler["inputs"]["sigmas"], ["22", 0])
        self.assertEqual(sampler["inputs"]["latent_image"], ["13", 0])
        self.assertEqual(wf["21"]["inputs"]["sampler_name"], "euler")
        self.assertEqual(wf["8"]["inputs"]["samples"], ["23", 0])

    def test_prompt_size_prefix(self):
        wf = self._build()
        self.assertEqual(wf["27"]["inputs"]["prompt"], "capybara under a banana leaf")
        self.assertEqual(wf["27"]["inputs"]["negative_prompt"], "")
        self.assertEqual(wf["13"]["inputs"]["width"], 1248)
        self.assertEqual(wf["13"]["inputs"]["height"], 832)
        self.assertEqual(wf["9"]["inputs"]["filename_prefix"], "test/viggle")

    def test_seed_zero_randomizes_fixed_seed_kept(self):
        self.assertNotEqual(self._build(seed=0)["20"]["inputs"]["noise_seed"], 0)
        self.assertEqual(self._build(seed=42)["20"]["inputs"]["noise_seed"], 42)

    def test_workflow_is_valid_json(self):
        json.dumps(self._build())


class TestQwenImage21ViggleTurboMultirefWorkflow(unittest.TestCase):
    """Test the Viggle Turbo multi-reference workflow JSON structure."""

    REFS = [
        "6c9kxv/avatar_281412419_00001_.png",
        "6c9kxv/avatar_captain_00001_.png",
    ]

    def _build(self, refs=None):
        return _build_qwen_image_21_viggle_turbo_multiref_workflow(
            prompt="Bridge crew from Picture 1 and Picture 2 at their stations.",
            reference_filenames=self.REFS if refs is None else refs,
            width=1024,
            height=1024,
            seed=7,
            filename_prefix="test/viggle_bridge",
        )

    def test_model_chain_lora_then_cache(self):
        """UNET → ViggleTurboLora → QwenImage21Cache → BasicGuider (shipped edit graph)."""
        wf = self._build()
        self.assertEqual(wf["15"]["class_type"], "ViggleTurboLora")
        self.assertEqual(wf["15"]["inputs"]["model"], ["10", 0])
        self.assertEqual(wf["45"]["class_type"], "QwenImage21Cache")
        self.assertEqual(wf["45"]["inputs"]["model"], ["15", 0])
        self.assertEqual(wf["19"]["inputs"]["model"], ["45", 0])
        self.assertEqual(wf["22"]["inputs"]["nodes"], VIGGLE_TURBO_SIGMA_NODES)
        self.assertEqual(wf["22"]["inputs"]["latent"], ["90", 0])
        self.assertEqual(wf["23"]["inputs"]["latent_image"], ["90", 0])

    def test_reference_slots_and_resolution(self):
        wf = self._build()
        encode = wf["70"]
        for i in (1, 2):
            self.assertEqual(encode["inputs"][f"images.image_{i}"], [str(200 + i), 0])
        self.assertNotIn("images.image_3", encode["inputs"])
        self.assertEqual(encode["inputs"]["resolution"], 0)

    def test_references_capped_at_ten(self):
        refs = [f"game/avatar_{i}_00001_.png" for i in range(15)]
        wf = self._build(refs=refs)
        encode = wf["70"]
        for i in range(1, 11):
            self.assertIn(f"images.image_{i}", encode["inputs"])
        for i in range(11, 16):
            self.assertNotIn(f"images.image_{i}", encode["inputs"])

    def test_reference_nodes_do_not_collide_with_cache_node(self):
        refs = [f"game/avatar_{i}_00001_.png" for i in range(6)]
        wf = self._build(refs=refs)
        self.assertEqual(wf["45"]["class_type"], "QwenImage21Cache")
        for i in range(1, 7):
            self.assertEqual(wf[str(200 + i)]["class_type"], "LoadImage")


class TestQwenEdit21ViggleTurboWorkflow(unittest.TestCase):
    """Test the Viggle Turbo instruction-edit workflow JSON structure."""

    def _build(self, background="bg.png"):
        cfg = get_edit_model_config("qwen_image_edit_2_1_viggle_turbo")
        return _build_qwen_edit_21_viggle_turbo_workflow(
            instruction="Place the character on the bridge",
            character_filename="game/avatar_1_00001_.png",
            background_filename=background,
            width=1024,
            height=1024,
            seed=5,
            filename_prefix="test/viggle_edit",
            cfg=cfg,
        )

    def test_cfg_lora_used_unmerged(self):
        """The LoRA filename must come from EditModelConfig and sit before the cache."""
        wf = self._build()
        cfg = get_edit_model_config("qwen_image_edit_2_1_viggle_turbo")
        lora = wf["15"]
        self.assertEqual(lora["class_type"], "ViggleTurboLora")
        self.assertEqual(lora["inputs"]["lora_name"], cfg.lora)
        self.assertEqual(lora["inputs"]["strength"], 1.0)
        self.assertEqual(wf["45"]["inputs"]["model"], ["15", 0])
        self.assertEqual(wf["19"]["inputs"]["model"], ["45", 0])

    def test_reference_images(self):
        wf = self._build()
        encode = wf["70"]["inputs"]
        self.assertEqual(encode["images.image_1"], ["41", 0])
        self.assertEqual(encode["images.image_2"], ["42", 0])
        self.assertEqual(wf["42"]["inputs"]["image"], "bg.png")
        self.assertEqual(encode["resolution"], 0)

    def test_no_background_leaves_image_2_absent(self):
        wf = self._build(background=None)
        self.assertNotIn("42", wf)
        self.assertNotIn("images.image_2", wf["70"]["inputs"])

    def test_sigma_schedule_and_output(self):
        wf = self._build()
        self.assertEqual(wf["22"]["inputs"]["nodes"], VIGGLE_TURBO_SIGMA_NODES)
        self.assertEqual(wf["20"]["inputs"]["noise_seed"], 5)
        self.assertEqual(wf["90"]["inputs"]["width"], 1024)
        self.assertEqual(wf["120"]["inputs"]["filename_prefix"], "test/viggle_edit")


class TestViggleTurboModelWiring(unittest.TestCase):
    """Config entries for Viggle Turbo must map to registered builders."""

    def test_txt2img_entry_registered(self):
        cfg = comfyui_config.get_model_config("qwen_image_2_1_viggle_turbo")
        self.assertIn(cfg.builder, _TXT2IMG_BUILDERS)

    def test_edit_entry_registered(self):
        cfg = get_edit_model_config("qwen_image_edit_2_1_viggle_turbo")
        self.assertIn(cfg.builder, _EDIT_BUILDERS)
        self.assertTrue(cfg.lora.endswith(".safetensors"))

    def test_multiref_registry_has_viggle(self):
        # _MULTIREF_BUILDERS is keyed by model key, not builder name
        self.assertIn("qwen_image_2_1_viggle_turbo", _MULTIREF_BUILDERS)

    def test_every_registered_model_has_builder(self):
        for cfg in comfyui_config.MODELS.values():
            self.assertIn(cfg.builder, _TXT2IMG_BUILDERS)
        for cfg in comfyui_config.EDIT_MODELS.values():
            self.assertIn(cfg.builder, _EDIT_BUILDERS)


class TestAvatarRgbaTemplate(unittest.TestCase):
    """generate_avatar_image must wrap the prompt in the RGBA transparency template."""

    def test_prompt_wrapped_in_rgba_template(self):
        gen = ImageGenerator()
        captured = {}

        async def fake_generate_image(**kwargs):
            captured.update(kwargs)
            return "http://comfyui:8188/view?filename=x.png&type=output"

        with patch.object(ImageGenerator, "generate_image", new=lambda self, **kw: fake_generate_image(**kw)):
            url = asyncio.get_event_loop().run_until_complete(
                gen.generate_avatar_image(
                    prompt="Chief Engineer in futuristic uniform",
                    filename_prefix="g/avatar_1",
                    width=768,
                    height=1024,
                    game_id="g",
                    player_id="1",
                    turn=None,
                    kind="avatar",
                )
            )
        self.assertIsNotNone(url)
        self.assertIn("This is an RGBA image with transparency.", captured["prompt"])
        self.assertIn("The image has alpha channel", captured["prompt"])
        self.assertIn("Chief Engineer in futuristic uniform", captured["prompt"])


class TestBridgeImageFallbacks(unittest.TestCase):
    """generate_bridge_image falls back to plain txt2img without references."""

    def test_no_references_routes_to_scene_image(self):
        gen = ImageGenerator()
        captured = {}

        async def fake_scene(**kwargs):
            captured.update(kwargs)
            return "http://comfyui:8188/view?filename=bridge.png&type=output"

        with patch.object(ImageGenerator, "generate_scene_image", new=lambda self, **kw: fake_scene(**kw)):
            url = asyncio.get_event_loop().run_until_complete(
                gen.generate_bridge_image(
                    prompt="Starship bridge, crew at stations",
                    crew_descriptions=[{"role": "Капитан", "position_description": "At the throne"}],
                    avatar_urls=[None, None],
                    filename_prefix="g/bridge",
                    width=1024,
                    height=1024,
                    game_id="g",
                )
            )
        self.assertIsNotNone(url)
        self.assertEqual(captured["kind"], "bridge")
        self.assertIn("Crew positions: Капитан: At the throne", captured["prompt"])

    def test_references_do_not_append_crew_positions(self):
        """With references the trailing 'Crew positions' block must be absent.

        The block re-describes every crew member without Picture bindings;
        the model then renders extra/duplicated figures (e.g. the science
        officer twice), so it is only allowed on the txt2img fallback path.
        """
        gen = ImageGenerator()
        captured = {}

        async def fake_queue(workflow, **kwargs):
            captured["workflow"] = workflow
            return "pid-1"

        async def fake_wait(prompt_id, timeout):
            return {"120": {"images": [{"filename": "bridge.png", "subfolder": "", "type": "output"}]}}

        with patch.object(gen, "_queue_prompt", new=fake_queue), patch.object(gen, "_wait_for_completion", new=fake_wait):
            url = asyncio.get_event_loop().run_until_complete(
                gen.generate_bridge_image(
                    prompt="Place the character from Picture 1 at the engineering console.",
                    crew_descriptions=[
                        {"role": "Научный офицер", "position_description": "Right science console"},
                    ],
                    avatar_urls=["http://comfyui:8188/view?filename=avatar_1.png&subfolder=g&type=output"],
                    filename_prefix="g/bridge",
                    width=1024,
                    height=1024,
                    game_id="g",
                )
            )
        self.assertIsNotNone(url)
        encode_prompt = captured["workflow"]["70"]["inputs"]["prompt"]
        self.assertNotIn("Crew positions", encode_prompt)
        self.assertIn("Place the character from Picture 1 at the engineering console.", encode_prompt)


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
            seed=0,
            filename_prefix="test_verify",
        )

        async def _test():
            prompt_id = await self.gen._queue_prompt(wf, kind="test", ctx_game="test", ctx_player="test", ctx_turn="test")
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
                game_id=None,
                player_id=None,
                turn=None,
                kind="avatar",
            )
            return url

        url = asyncio.get_event_loop().run_until_complete(_test())
        self.assertIsNotNone(url, "Avatar generation returned None")
        assert url is not None
        self.assertIn("/view?", url)
        print(f"\nGenerated avatar URL: {url}")


if __name__ == "__main__":
    unittest.main()
