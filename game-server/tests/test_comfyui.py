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
    _build_flux_dev_workflow,
    _build_qwen_edit_workflow,
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


class TestFluxDevWorkflow(unittest.TestCase):
    """Test FLUX.1 [dev] GGUF Q4_K_M workflow JSON structure."""

    def test_basic_workflow_structure(self):
        """Workflow should have all required nodes."""
        wf = _build_flux_dev_workflow(prompt="test prompt", width=1024, height=1024, seed=0, filename_prefix="")

        required_nodes = ["10", "30", "29", "27", "13", "11", "3", "33", "8", "9"]
        for node_id in required_nodes:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_gguf_unet_loader(self):
        """UnetLoaderGGUF should load flux1-dev-Q4_K_S.gguf."""
        wf = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        unet = wf["10"]

        self.assertEqual(unet["class_type"], "UnetLoaderGGUF")
        self.assertEqual(unet["inputs"]["unet_name"], "flux1-dev-Q4_K_S.gguf")

    def test_dual_clip_loader(self):
        """DualCLIPLoader should pair clip_l + t5xxl_fp8 with type=flux."""
        wf = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        clip = wf["30"]

        self.assertEqual(clip["class_type"], "DualCLIPLoader")
        self.assertEqual(clip["inputs"]["clip_name1"], "clip_l.safetensors")
        self.assertEqual(clip["inputs"]["clip_name2"], "t5xxl_fp8_e4m3fn.safetensors")
        self.assertEqual(clip["inputs"]["type"], "flux")

    def test_shared_vae(self):
        """VAELoader should reuse the shared ae.safetensors (same as Z-Image)."""
        wf = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        self.assertEqual(wf["29"]["inputs"]["vae_name"], "ae.safetensors")

    def test_ksampler_settings(self):
        """KSampler should use 20 steps, cfg=1.0, euler/simple for FLUX dev."""
        wf = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        ks = wf["3"]

        self.assertEqual(ks["class_type"], "KSampler")
        self.assertEqual(ks["inputs"]["steps"], 20)
        self.assertEqual(ks["inputs"]["cfg"], 1.0)
        self.assertEqual(ks["inputs"]["sampler_name"], "euler")
        self.assertEqual(ks["inputs"]["scheduler"], "simple")
        self.assertEqual(ks["inputs"]["denoise"], 1.0)

    def test_model_sampling_shift(self):
        """ModelSamplingAuraFlow should use the FLUX shift=1.73."""
        wf = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        self.assertEqual(wf["11"]["class_type"], "ModelSamplingAuraFlow")
        self.assertEqual(wf["11"]["inputs"]["shift"], 1.73)

    def test_custom_dimensions(self):
        """Workflow should respect custom width/height."""
        wf = _build_flux_dev_workflow(prompt="test", width=768, height=1024, seed=0, filename_prefix="")
        self.assertEqual(wf["13"]["inputs"]["width"], 768)
        self.assertEqual(wf["13"]["inputs"]["height"], 1024)

    def test_custom_filename_prefix(self):
        """Workflow should pass filename_prefix to SaveImage."""
        wf = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="avatar_123")
        self.assertEqual(wf["9"]["inputs"]["filename_prefix"], "avatar_123")

    def test_seed_zero_randomizes(self):
        """seed=0 should produce different seeds across calls."""
        wf1 = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        wf2 = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        self.assertNotEqual(wf1["3"]["inputs"]["seed"], wf2["3"]["inputs"]["seed"])

    def test_fixed_seed(self):
        """A fixed seed should be passed through unchanged."""
        wf = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=42, filename_prefix="")
        self.assertEqual(wf["3"]["inputs"]["seed"], 42)

    def test_prompt_in_text_encode(self):
        """Prompt should be passed to CLIPTextEncode node."""
        wf = _build_flux_dev_workflow(prompt="a six-legged alien", width=1024, height=1024, seed=0, filename_prefix="")
        self.assertEqual(wf["27"]["inputs"]["text"], "a six-legged alien")

    def test_node_connections(self):
        """All node links should reference valid node IDs."""
        wf = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
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

    def test_serializable_json(self):
        """Workflow should round-trip through JSON."""
        wf = _build_flux_dev_workflow(prompt="test", width=1024, height=1024, seed=0, filename_prefix="")
        self.assertEqual(json.loads(json.dumps(wf)), wf)


class TestFluxDevImg2ImgWorkflow(unittest.TestCase):
    """Test FLUX.1 [dev] GGUF img2img workflow (avatar-as-latent-reference)."""

    def setUp(self):
        self.gen = ImageGenerator()

    def _build(self, **overrides):
        kwargs = dict(
            prompt="test", reference_filename="avatar.png", denoise=0.75,
            width=1024, height=1024, seed=0, filename_prefix="",
        )
        kwargs.update(overrides)
        return self.gen._build_flux_dev_img2img_workflow(**kwargs)

    def test_basic_workflow_structure(self):
        """Workflow should have all required nodes including LoadImage/VAEEncode."""
        wf = self._build()
        required = ["40", "41", "10", "30", "29", "27", "11", "3", "33", "8", "9"]
        for node_id in required:
            self.assertIn(node_id, wf, f"Missing node {node_id}")

    def test_reference_image_loads(self):
        """LoadImage (node 40) should reference the supplied avatar filename."""
        wf = self._build(reference_filename="game_x/avatar_123.png")
        self.assertEqual(wf["40"]["class_type"], "LoadImage")
        self.assertEqual(wf["40"]["inputs"]["image"], "game_x/avatar_123.png")

    def test_vae_encode_feeds_latent(self):
        """KSampler should sample from the VAEEncode latent (node 41), not an empty latent."""
        wf = self._build()
        self.assertEqual(wf["41"]["class_type"], "VAEEncode")
        self.assertEqual(wf["3"]["inputs"]["latent_image"], ["41", 0])
        for node in wf.values():
            self.assertNotEqual(node["class_type"], "EmptySD3LatentImage")

    def test_gguf_unet_loader(self):
        """UnetLoaderGGUF should load flux1-dev-Q4_K_S.gguf."""
        wf = self._build()
        self.assertEqual(wf["10"]["class_type"], "UnetLoaderGGUF")
        self.assertEqual(wf["10"]["inputs"]["unet_name"], "flux1-dev-Q4_K_S.gguf")

    def test_dual_clip_loader(self):
        """DualCLIPLoader should pair clip_l + t5xxl_fp8 with type=flux."""
        wf = self._build()
        clip = wf["30"]
        self.assertEqual(clip["class_type"], "DualCLIPLoader")
        self.assertEqual(clip["inputs"]["clip_name1"], "clip_l.safetensors")
        self.assertEqual(clip["inputs"]["clip_name2"], "t5xxl_fp8_e4m3fn.safetensors")
        self.assertEqual(clip["inputs"]["type"], "flux")

    def test_shared_vae(self):
        """VAELoader should reuse the shared ae.safetensors."""
        wf = self._build()
        self.assertEqual(wf["29"]["inputs"]["vae_name"], "ae.safetensors")

    def test_ksampler_settings_and_denoise(self):
        """KSampler should use FLUX settings and the caller-supplied denoise."""
        wf = self._build(denoise=0.6)
        ks = wf["3"]
        self.assertEqual(ks["inputs"]["steps"], 20)
        self.assertEqual(ks["inputs"]["cfg"], 1.0)
        self.assertEqual(ks["inputs"]["sampler_name"], "euler")
        self.assertEqual(ks["inputs"]["scheduler"], "simple")
        self.assertEqual(ks["inputs"]["denoise"], 0.6)

    def test_model_sampling_shift(self):
        """ModelSamplingAuraFlow should use the FLUX shift=1.73."""
        wf = self._build()
        self.assertEqual(wf["11"]["inputs"]["shift"], 1.73)

    def test_seed_zero_randomizes(self):
        """seed=0 should produce different seeds across calls."""
        wf1 = self._build()
        wf2 = self._build()
        self.assertNotEqual(wf1["3"]["inputs"]["seed"], wf2["3"]["inputs"]["seed"])

    def test_fixed_seed(self):
        """A fixed seed should be passed through unchanged."""
        wf = self._build(seed=42)
        self.assertEqual(wf["3"]["inputs"]["seed"], 42)

    def test_node_connections_valid(self):
        """All node links should reference valid node IDs."""
        wf = self._build()
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

    def test_serializable_json(self):
        """Workflow should round-trip through JSON."""
        wf = self._build()
        self.assertEqual(json.loads(json.dumps(wf)), wf)


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

    def test_alien_species_no_lora_8_steps(self):
        """Non-humanoid species must skip Lightning LoRA and use 8 steps.

        At strength 1.0 the Lightning LoRA suppresses avatar identity and
        collapses non-humanoid characters into a humanoid. The LoRA node is
        removed and the sampler runs the full 8-step schedule directly on the
        GGUF model.
        """
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", background_filename=None,
            width=1024, height=1024, seed=0, filename_prefix="",
            cfg=self.cfg,
            species_category="non_humanoid",
        )
        self.assertNotIn("50", wf)
        ks = wf["100"]
        self.assertEqual(ks["inputs"]["steps"], 8)
        self.assertEqual(ks["inputs"]["model"], ["10", 0])

    def test_energy_species_no_lora_8_steps(self):
        """Energy species must also skip Lightning LoRA and use 8 steps."""
        wf = _build_qwen_edit_workflow(
            instruction="t", character_filename="a.png", background_filename=None,
            width=1024, height=1024, seed=0, filename_prefix="",
            cfg=self.cfg,
            species_category="energy",
        )
        self.assertNotIn("50", wf)
        self.assertEqual(wf["100"]["inputs"]["steps"], 8)
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

    def test_avatar_uses_flux(self):
        """Avatar kind should resolve to FLUX (better non-humanoid anatomy)."""
        self.assertEqual(comfyui_config.resolve_txt2img_model("avatar"), "flux_dev_gguf_q4")

    def test_npc_avatar_prefix_matches_flux(self):
        """Parameterized npc_avatar_<role> should match the npc_avatar prefix."""
        self.assertEqual(comfyui_config.resolve_txt2img_model("npc_avatar_security"), "flux_dev_gguf_q4")
        self.assertEqual(comfyui_config.resolve_txt2img_model("npc_avatar_engineer"), "flux_dev_gguf_q4")

    def test_scene_uses_default(self):
        """Scene kind should fall back to the default model (z_image_turbo)."""
        self.assertEqual(comfyui_config.resolve_txt2img_model("scene"), comfyui_config.DEFAULT_TXT2IMG_MODEL)

    def test_none_kind_uses_default(self):
        """A None kind should resolve to the default model."""
        self.assertEqual(comfyui_config.resolve_txt2img_model(None), comfyui_config.DEFAULT_TXT2IMG_MODEL)

    def test_unknown_kind_uses_default(self):
        """An unlisted kind should resolve to the default model."""
        self.assertEqual(comfyui_config.resolve_txt2img_model("splash"), comfyui_config.DEFAULT_TXT2IMG_MODEL)

    def test_default_is_z_image_turbo(self):
        """Out of the box the default model should be z_image_turbo."""
        self.assertEqual(comfyui_config.DEFAULT_TXT2IMG_MODEL, "z_image_turbo")

    def test_env_override_changes_default(self):
        """COMFYUI_TXT2IMG_MODEL env should override the default model key."""
        with patch.object(comfyui_config, "DEFAULT_TXT2IMG_MODEL", "flux_dev_gguf_q4"):
            # With the default flipped, an unknown kind now resolves to FLUX.
            self.assertEqual(comfyui_config.resolve_txt2img_model("splash"), "flux_dev_gguf_q4")
            # An explicit override still wins over the flipped default.
            self.assertEqual(comfyui_config.resolve_txt2img_model("avatar"), "flux_dev_gguf_q4")

    def test_get_model_config_known(self):
        """get_model_config should return a ModelConfig for registered keys."""
        cfg = comfyui_config.get_model_config("flux_dev_gguf_q4")
        self.assertEqual(cfg.builder, "flux_dev")

    def test_get_model_config_unknown_raises(self):
        """get_model_config should raise KeyError for an unregistered key."""
        with self.assertRaises(KeyError):
            comfyui_config.get_model_config("does_not_exist")

    def test_every_override_key_is_registered(self):
        """Every override value must index into MODELS (no dangling pointers)."""
        for model_key in comfyui_config.KIND_MODEL_OVERRIDES.values():
            self.assertIn(model_key, comfyui_config.MODELS)

    # ---- img2img shares the txt2img model family ----

    def test_img2img_avatar_uses_flux(self):
        """img2img should resolve via the same table as txt2img (avatar→FLUX)."""
        self.assertEqual(comfyui_config.resolve_img2img_model("avatar"), "flux_dev_gguf_q4")

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
