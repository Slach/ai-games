"""Scene-stage comic: one row per location group (GM feedback 2026-11-14).

The scene stage shows what the crew does at the start of the turn. The crew
splits across compartments, and characters sharing a location are combined
into ONE wide group picture ("3 в рубке + 2 в медотсеке" → two rows: a wide
3-person strip and a wider 2-person strip), so the page never repeats the
same room panel after panel and never leaves an empty cell.

Panels are stitched with Pillow (deterministic pixel work — no image model in
the loop, so no character can drift, drop or be duplicated) and uploaded to
ComfyUI's input dir, served via /view like every other image. Each group
picture arrives already generated at its group-size aspect (see
:func:`group_dimensions`), so a row is just that image scaled to full page
width — groups with fewer members show bigger figures.
"""

import asyncio
import io
import logging
from urllib.parse import quote

import aiohttp
from PIL import Image

logger = logging.getLogger(__name__)

# Telegram re-encodes photos; beyond ~1568px the extra pixels are wasted and
# large PNGs start bumping against the Bot API's 10 MB photo limit.
MAX_PAGE_WIDTH = 1568


def group_dimensions(k: int) -> tuple[int, int]:
    """(width, height) for a scene-stage picture of k co-located characters.

    Every row is a wide cinematic strip (GM feedback 2026-11-14 v4: a solo
    character gets the same wide treatment — "его наверное одной широкой
    картинкой можно сделать тоже"): 1 or 3 → 3:1; 2 → 2:1; 4+ → clamped at
    4:1. Heights stay multiples of 32 (latent-size friendly).
    """
    width = 1536
    # A solo strip is 3:1 like a trio's (GM feedback 2026-11-14 v4:
    # "допустим 3x1").
    divisor = 3 if k <= 1 else k
    height = min(768, max(384, width // divisor))
    return width, (height // 32) * 32


def stitch_panels(panels: list[bytes]) -> bytes:
    """Compose panel PNG bytes into one page PNG.

    Each panel is one ROW, scaled to the full page width preserving its
    aspect — a wide group strip stays a thin row, a single square keeps more
    height. The page is a tight stack: no gaps, no empty cells.
    """
    images = [Image.open(io.BytesIO(p)).convert("RGB") for p in panels]
    page_w = MAX_PAGE_WIDTH if len(images) > 1 else max(im.width for im in images)
    rows = [
        im.resize((page_w, round(page_w * im.height / im.width)), Image.LANCZOS)
        for im in images
    ]
    page = Image.new("RGB", (page_w, sum(r.height for r in rows)), (255, 255, 255))
    y = 0
    for row in rows:
        page.paste(row, (0, y))
        y += row.height
    buf = io.BytesIO()
    page.save(buf, format="PNG")
    return buf.getvalue()


async def build_scene_comic(
    panel_urls: list[str],
    *,
    comfyui_url: str,
    game_id: str,
    turn: int,
) -> str | None:
    """Download the group panels, stitch them, upload to ComfyUI, return URL.

    Returns None when nothing could be stitched (caller falls back to the
    shared backdrop). Individual panel download failures are logged and the
    panel skipped — one missing group must not kill the whole stage.
    """
    panels: list[bytes] = []
    async with aiohttp.ClientSession() as session:
        for url in panel_urls:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    resp.raise_for_status()
                    panels.append(await resp.read())
            except Exception:
                logger.warning(
                    "[SCENE_COMIC] panel download failed (%s), skipping it", url,
                    exc_info=True,
                )
    if not panels:
        logger.warning("[SCENE_COMIC] no panels downloaded for game %s turn %s", game_id, turn)
        return None

    page_png = await asyncio.to_thread(stitch_panels, panels)

    filename = f"scene_comic_{game_id}_t{turn}.png"
    try:
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field("overwrite", "true")
            form.add_field("type", "input")
            form.add_field("image", page_png, filename=filename, content_type="image/png")
            async with session.post(
                f"{comfyui_url}/upload/image", data=form,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                body = await resp.text()
        if resp.status != 200:
            logger.error(
                "[SCENE_COMIC] ComfyUI upload failed for %s: HTTP %s %s",
                filename, resp.status, body[:200], stack_info=True,
            )
            return None
    except Exception:
        logger.error("[SCENE_COMIC] ComfyUI upload error for %s", filename, exc_info=True)
        return None

    url = f"{comfyui_url}/view?filename={quote(filename)}&subfolder=&type=input"
    logger.info("[SCENE_COMIC] page built: %d rows -> %s", len(panels), url)
    return url
