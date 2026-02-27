"""
Comic Generator - Integration with Pixelle-MCP for comic generation
"""

import os
import logging
import aiohttp
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ComicPanel(BaseModel):
    """A single panel in a comic"""
    description: str
    character_focus: str  # Which character is the focus
    emotion: str
    setting: str


class ComicGenerationRequest(BaseModel):
    """Request for comic generation"""
    story: str
    player_role: str
    player_traits: List[str]
    npc_dialogues: List[Dict[str, str]]
    panels: List[ComicPanel]


class ComicGenerator:
    """
    Generates personalized comics using Pixelle-MCP and ComfyUI workflows.
    """

    def __init__(self):
        self.pixelle_mcp_url = os.getenv("PIXELLE_MCP_URL", "http://pixelle-mcp:9004/pixelle/mcp")
        self.comfyui_url = os.getenv("COMFYUI_URL", "http://comfyui:8188")

        # Workflow templates for different content types
        self.workflows = {
            "comic_single": "i2i_by_flux_kontext_pro.json",  # Image-to-image for panel editing
            "comic_merge": "i_merge.json",  # Merge multiple images into comic strip
            "character": "t2i_qwen_image.json",  # Text-to-image for character generation
            "scene": "t2i_Z_image.json",  # Text-to-image for scene generation
        }

    def _generate_panel_prompts(
        self,
        story: str,
        player_role: str,
        player_traits: List[str],
        npc_dialogues: List[Dict[str, str]],
    ) -> List[ComicPanel]:
        """Generate panel descriptions based on story and player profile"""

        panels = [
            ComicPanel(
                description=f"Establishing shot: {story[:100]}... showing the crew in their workspace",
                character_focus="ensemble",
                emotion="curious",
                setting="spaceship interior",
            ),
            ComicPanel(
                description=f"Close-up on {player_role} reacting to the situation with determination",
                character_focus=player_role,
                emotion="determined" if "решительный" in player_traits else "thoughtful",
                setting="spaceship bridge",
            ),
            ComicPanel(
                description="NPC team discussing options, showing their distinct personalities",
                character_focus="npcs",
                emotion="concerned",
                setting="conference room",
            ),
            ComicPanel(
                description=f"{player_role} making a critical decision, showing the weight of responsibility",
                character_focus=player_role,
                emotion="decisive",
                setting="command center",
            ),
            ComicPanel(
                description="The immediate consequence of the decision unfolding",
                character_focus="ensemble",
                emotion="surprised",
                setting="varies by outcome",
            ),
        ]

        return panels

    async def generate_character_image(
        self,
        character_name: str,
        role: str,
        traits: List[str],
        scene_description: str = "",
    ) -> Optional[str]:
        """Generate a character image using ComfyUI"""

        prompt = f"""
        Sci-fi character portrait: {character_name}, {role}.
        Personality traits: {', '.join(traits)}.
        {scene_description}
        Futuristic uniform, detailed face, cinematic lighting, 4K quality.
        Space opera style, Star Trek aesthetic.
        """

        try:
            # Call Pixelle-MCP to trigger character generation
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.pixelle_mcp_url}/generate/image",
                    json={
                        "prompt": prompt,
                        "workflow": self.workflows["character"],
                        "output_format": "webp",
                    },
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("image_url", result.get("path", ""))
                    else:
                        logger.error(f"Character generation failed: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"Character generation error: {e}")
            return None

    async def generate_scene_image(
        self,
        scene_description: str,
        characters: List[str] = None,
    ) -> Optional[str]:
        """Generate a scene image using ComfyUI"""

        prompt = f"""
        Sci-fi scene: {scene_description}.
        {f'Characters present: {", ".join(characters)}' if characters else ''}
        Cinematic composition, detailed environment, atmospheric lighting.
        Space opera style, 4K quality.
        """

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.pixelle_mcp_url}/generate/image",
                    json={
                        "prompt": prompt,
                        "workflow": self.workflows["scene"],
                        "output_format": "webp",
                    },
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("image_url", result.get("path", ""))
                    else:
                        logger.error(f"Scene generation failed: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"Scene generation error: {e}")
            return None

    async def generate_comic_strip(
        self,
        request: ComicGenerationRequest,
    ) -> Optional[str]:
        """
        Generate a complete comic strip for a game day.

        Process:
        1. Generate individual panel images
        2. Merge panels into a comic strip
        3. Add speech bubbles with NPC dialogue
        """

        panels = self._generate_panel_prompts(
            request.story,
            request.player_role,
            request.player_traits,
            request.npc_dialogues,
        )

        logger.info(f"Generating comic with {len(panels)} panels")

        # Generate panel images
        panel_images = []
        for i, panel in enumerate(panels):
            logger.info(f"Generating panel {i + 1}/{len(panels)}")

            image_url = await self.generate_scene_image(
                scene_description=f"{panel.description}. {panel.character_focus} showing {panel.emotion} expression",
                characters=[panel.character_focus] if panel.character_focus != "ensemble" else None,
            )

            if image_url:
                panel_images.append(image_url)
            else:
                # Fallback: use placeholder
                panel_images.append(f"/content/panels/day_{i}.placeholder.webp")

        # Merge panels into comic strip
        if len(panel_images) > 1:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.pixelle_mcp_url}/generate/comic",
                        json={
                            "panel_images": panel_images,
                            "npc_dialogues": request.npc_dialogues,
                            "workflow": self.workflows["comic_merge"],
                            "layout": "vertical",  # or "horizontal", "grid"
                        },
                        timeout=aiohttp.ClientTimeout(total=180),
                    ) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            return result.get("comic_url", result.get("path", ""))
                        else:
                            logger.error(f"Comic merge failed: {resp.status}")
            except Exception as e:
                logger.error(f"Comic merge error: {e}")

        # Return first panel as fallback
        return panel_images[0] if panel_images else None

    async def generate_personalized_comic(
        self,
        day: int,
        story: str,
        player_profile: Dict[str, Any],
        npc_dialogues: List[Dict[str, str]],
    ) -> str:
        """
        Generate a personalized comic for a player.

        This is the main entry point called by Game Master.
        """

        request = ComicGenerationRequest(
            story=story,
            player_role=player_profile.get("role", "Crew Member"),
            player_traits=player_profile.get("personality_traits", []),
            npc_dialogues=npc_dialogues,
            panels=[],
        )

        comic_url = await self.generate_comic_strip(request)

        if comic_url:
            logger.info(f"Generated comic for day {day}: {comic_url}")
            return comic_url
        else:
            # Return placeholder path if generation fails
            logger.warning(f"Comic generation failed for day {day}, using placeholder")
            return f"/content/comics/day_{day}_placeholder.webp"


# Factory function
def create_comic_generator() -> ComicGenerator:
    """Create and configure ComicGenerator instance"""
    return ComicGenerator()