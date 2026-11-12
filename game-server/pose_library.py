"""OpenPose-style skeleton library rendered as pose-reference images.

Qwen-Image-2.1 edit natively understands "the pose of Picture N": a third
reference image carrying an OpenPose skeleton lets the scene-instruction LLM
pick a concrete body pose for a character instead of describing it in words
(text-only pose cues are routinely ignored — the model copies the avatar's
hero stance instead).

Skeletons are rendered with the Python stdlib only (zlib PNG writer + a
simple distance-rasterizer): no Pillow dependency, no GPU, deterministic
output. Rendered PNGs are uploaded to ComfyUI's input folder once per
process and then referenced by filename from edit workflows.

Keypoint layout is OpenPose COCO-18:
    0 nose, 1 neck,
    2 R shoulder, 3 R elbow, 4 R wrist,
    5 L shoulder, 6 L elbow, 7 L wrist,
    8 R hip, 9 R knee, 10 R ankle,
    11 L hip, 12 L knee, 13 L ankle,
    14 R eye, 15 L eye, 16 R ear, 17 L ear
Coordinates live on a 768x1024 canvas, y grows downward.
"""

import logging
import struct
import zlib

import aiohttp

logger = logging.getLogger(__name__)

CANVAS_W = 768
CANVAS_H = 1024

# (from, to) keypoint index pairs, with the canonical OpenPose limb colors.
_LIMBS = [
    (0, 1, (255, 128, 0)),    # nose-neck
    (1, 2, (255, 153, 255)),  # neck-R shoulder
    (2, 3, (170, 255, 2)),    # R shoulder-R elbow
    (3, 4, (255, 85, 0)),     # R elbow-R wrist
    (1, 5, (255, 255, 0)),    # neck-L shoulder
    (5, 6, (85, 255, 170)),   # L shoulder-L elbow
    (6, 7, (0, 255, 255)),    # L elbow-L wrist
    (1, 8, (255, 255, 0)),    # neck-R hip
    (8, 9, (255, 128, 0)),    # R hip-R knee
    (9, 10, (170, 255, 2)),   # R knee-R ankle
    (1, 11, (0, 255, 255)),   # neck-L hip
    (11, 12, (255, 255, 0)),  # L hip-L knee
    (12, 13, (85, 255, 170)), # L knee-L ankle
    (0, 14, (0, 0, 255)),     # nose-R eye
    (14, 16, (0, 0, 255)),    # R eye-R ear
    (0, 15, (255, 0, 0)),     # nose-L eye
    (15, 17, (255, 0, 0)),    # L eye-L ear
]

_JOINT_COLOR = (0, 0, 255)

# pose_id -> (english description for the scene-instruction LLM, 18 keypoints).
# Descriptions double as usage hints: the LLM defaulted to a single pose when
# they were bare ("leaning over a console") — tell it WHEN each pose fits and
# whether the face stays visible, so side/three-quarter views win over the
# back-to-camera one (GM feedback 2026-11-12: "персонаж стоит жопой в экран").
# Person reference proportions on the 768x1024 canvas: head top ~y=70,
# neck ~y=150, hips ~y=430, ankles ~y=930, shoulder half-width ~95.
POSES: dict[str, tuple[str, tuple[tuple[int, int], ...]]] = {
    "standing_three_quarter": (
        "relaxed three-quarter standing view, weight on one leg, face clearly visible — talking, deciding, observing; more natural than a flat frontal stance",
        ((392, 92), (386, 156), (472, 170), (488, 304), (492, 438), (302, 174),
         (270, 308), (262, 444), (448, 432), (456, 676), (462, 926), (326, 436),
         (300, 678), (292, 926), (412, 80), (372, 78), (436, 94), (352, 92)),
    ),
    "standing": (
        "standing upright facing the camera, arms relaxed — neutral presenting pose; use only for talking or calmly observing",
        ((384, 90), (384, 155), (479, 170), (497, 305), (503, 440), (289, 170),
         (271, 305), (265, 440), (444, 430), (452, 675), (458, 925), (324, 430),
         (316, 675), (310, 925), (409, 78), (359, 78), (432, 95), (336, 95)),
    ),
    "hands_on_hips": (
        "standing confident, both hands on the hips, elbows out, face visible — commanding, arguing, objecting",
        ((384, 90), (384, 155), (479, 170), (560, 300), (462, 430), (289, 170),
         (208, 300), (306, 430), (444, 430), (452, 675), (458, 925), (324, 430),
         (316, 675), (310, 925), (409, 78), (359, 78), (432, 95), (336, 95)),
    ),
    "console_side": (
        "SIDE VIEW standing at a console, both hands on the controls, head in profile — face stays visible while working; the best default for operating any station",
        ((300, 105), (362, 162), (392, 176), (296, 288), (198, 332), (372, 182),
         (282, 296), (188, 342), (432, 436), (422, 680), (412, 928), (402, 440),
         (362, 684), (332, 930), (288, 102), (314, 98), (348, 112), (338, 108)),
    ),
    "console_sitting_side": (
        "SIDE VIEW seated at an operator station, hands on the controls, face in profile — piloting, monitoring or working long shifts while seated",
        ((298, 298), (352, 348), (382, 364), (296, 452), (208, 478), (366, 370),
         (286, 458), (202, 488), (438, 586), (348, 600), (342, 900), (424, 592),
         (330, 610), (326, 906), (286, 295), (312, 291), (340, 306), (330, 302)),
    ),
    "examining_hologram": (
        "THREE-QUARTER view, one hand raised to a head-height holographic display, studying the data — analysis, scanning, reading sensor results, comparing with databases",
        ((376, 92), (382, 156), (474, 170), (562, 138), (614, 108), (290, 174),
         (264, 306), (258, 442), (444, 434), (452, 678), (456, 926), (322, 434),
         (312, 678), (306, 926), (400, 80), (356, 78), (426, 95), (334, 92)),
    ),
    "console_typing": (
        "BACK TO CAMERA, leaning forward over a wall-mounted console, both hands working the controls — use ONLY for dead-on wall consoles; the face is hidden from the viewer",
        ((384, 105), (384, 165), (474, 175), (566, 258), (656, 330), (294, 175),
         (202, 258), (112, 330), (448, 435), (466, 685), (478, 930), (320, 435),
         (304, 685), (290, 930), (410, 92), (358, 92), (433, 108), (335, 108)),
    ),
    "reaching_up": (
        "reaching up with the right arm stretched high, head tilted up — pulling cables, turning valves, repairing overhead equipment",
        ((384, 95), (384, 160), (479, 175), (551, 90), (577, 20), (289, 175),
         (267, 310), (275, 445), (444, 435), (452, 680), (458, 928), (324, 435),
         (316, 680), (310, 928), (409, 82), (359, 82), (432, 98), (336, 98)),
    ),
    "walking": (
        "mid-stride walking forward in three-quarter view, face visible — moving across the scene to another position",
        ((384, 95), (384, 160), (479, 175), (505, 305), (470, 435), (289, 175),
         (235, 300), (280, 420), (430, 435), (560, 665), (620, 900), (338, 435),
         (240, 670), (170, 905), (409, 82), (359, 82), (432, 98), (336, 98)),
    ),
    "running": (
        "running fast, body leaning forward, arms pumping, one leg kicked back — sprinting, escaping, rushing to a station",
        ((420, 120), (400, 185), (492, 195), (560, 130), (630, 170), (308, 195),
         (238, 275), (180, 330), (448, 450), (590, 610), (560, 800), (352, 460),
         (260, 680), (180, 880), (444, 106), (395, 106), (467, 122), (371, 122)),
    ),
    "combat_stance": (
        "braced combat stance, knees bent, weight low, arms raised guarding — fighting, bracing for impact, readying weapons",
        ((384, 130), (384, 195), (479, 210), (540, 250), (590, 320), (289, 210),
         (228, 250), (178, 320), (448, 470), (500, 680), (470, 900), (320, 470),
         (268, 680), (300, 900), (409, 117), (359, 117), (432, 133), (336, 133)),
    ),
    "pointing_forward": (
        "standing and pointing straight ahead with the right arm — giving directions, ordering a course, indicating a target on screen",
        ((384, 95), (384, 160), (479, 175), (590, 195), (700, 205), (289, 175),
         (271, 305), (265, 440), (444, 430), (452, 675), (458, 925), (324, 430),
         (316, 675), (310, 925), (409, 82), (359, 82), (432, 98), (336, 98)),
    ),
    "carrying": (
        "carrying a heavy box or equipment with both arms held in front of the chest — hauling cargo, moving gear",
        ((384, 100), (384, 165), (479, 180), (530, 300), (410, 370), (289, 180),
         (238, 300), (358, 370), (444, 435), (452, 680), (458, 928), (324, 435),
         (316, 680), (310, 928), (409, 87), (359, 87), (432, 103), (336, 103)),
    ),
    "pulling_lever": (
        "pulling a lever with the right arm at shoulder height, body leaning into the pull — mechanical actions on handles, hatches, switches",
        ((360, 105), (368, 168), (462, 182), (570, 170), (660, 220), (276, 185),
         (208, 320), (196, 450), (432, 440), (456, 685), (468, 930), (306, 440),
         (286, 685), (272, 930), (386, 92), (336, 92), (409, 108), (313, 108)),
    ),
    "bracing_wall": (
        "leaning sideways against a wall or bulkhead, one forearm resting on it — waiting, holding position, sheltering from a blast",
        ((384, 100), (384, 165), (484, 178), (592, 195), (660, 260), (284, 185),
         (272, 320), (268, 450), (444, 438), (448, 682), (430, 928), (324, 438),
         (308, 682), (344, 920), (410, 87), (360, 87), (433, 103), (337, 103)),
    ),
    "kneeling": (
        "kneeling on one knee, hands working at low level — repairs at floor level, examining wreckage, tending the wounded",
        ((384, 240), (384, 305), (479, 320), (540, 430), (560, 540), (289, 320),
         (228, 430), (208, 540), (448, 560), (470, 790), (500, 930), (320, 560),
         (300, 760), (290, 930), (409, 227), (359, 227), (432, 243), (336, 243)),
    ),
    "crouching": (
        "crouched low, knees fully bent, hands forward — sneaking, crawling under something, bracing in a tight space",
        ((384, 300), (384, 365), (479, 380), (520, 480), (470, 560), (289, 380),
         (248, 480), (298, 560), (452, 610), (500, 800), (520, 930), (316, 610),
         (268, 800), (250, 930), (409, 287), (359, 287), (432, 303), (336, 303)),
    ),
    "sitting": (
        "sitting facing the camera, hands on knees — only when a frontal seated view is needed (briefings, medical exam)",
        ((384, 250), (384, 315), (479, 330), (563, 420), (600, 500), (289, 330),
         (205, 420), (168, 500), (462, 555), (560, 570), (600, 900), (306, 555),
         (208, 570), (170, 900), (409, 237), (359, 237), (432, 253), (336, 253)),
    ),
}

# Uploaded pose filename per pose_id for the current process: rendering and
# uploading once is enough — the file persists in ComfyUI's input folder.
_uploaded: dict[str, str] = {}


def pose_ids() -> list[str]:
    """All pose ids in the library."""
    return list(POSES)


def pose_description(pose_id: str) -> str:
    """English one-line description of a pose (for LLM prompt)."""
    return POSES[pose_id][0]


def _stroke(img: bytearray, x0: int, y0: int, x1: int, y1: int,
            color: tuple[int, int, int], width: int) -> None:
    """Draw a thick line segment by marking pixels within width/2 of it."""
    radius = width / 2
    min_x = max(int(min(x0, x1) - radius) - 1, 0)
    max_x = min(int(max(x0, x1) + radius) + 1, CANVAS_W - 1)
    min_y = max(int(min(y0, y1) - radius) - 1, 0)
    max_y = min(int(max(y0, y1) + radius) + 1, CANVAS_H - 1)
    dx, dy = x1 - x0, y1 - y0
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        seg_len_sq = 1e-9
    r_sq = radius * radius
    for py in range(min_y, max_y + 1):
        row = py * CANVAS_W
        for px in range(min_x, max_x + 1):
            t = ((px - x0) * dx + (py - y0) * dy) / seg_len_sq
            t = max(0.0, min(1.0, t))
            cx, cy = x0 + t * dx, y0 + t * dy
            if (px - cx) ** 2 + (py - cy) ** 2 <= r_sq:
                base = (row + px) * 3
                img[base] = color[0]
                img[base + 1] = color[1]
                img[base + 2] = color[2]


def _joint(img: bytearray, x: int, y: int, radius: int) -> None:
    _stroke(img, x, y, x, y, _JOINT_COLOR, radius * 2)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def render_pose_png(pose_id: str) -> bytes:
    """Render a pose skeleton to an RGB PNG (stdlib only)."""
    keypoints = POSES[pose_id][1]
    img = bytearray(CANVAS_W * CANVAS_H * 3)  # black background
    for a, b, color in _LIMBS:
        x0, y0 = keypoints[a]
        x1, y1 = keypoints[b]
        _stroke(img, x0, y0, x1, y1, color, 9)
    for x, y in keypoints[:14]:  # body joints only (skip eye/ear dots)
        _joint(img, x, y, 5)
    raw = b"".join(
        b"\x00" + bytes(img[row * CANVAS_W * 3:(row + 1) * CANVAS_W * 3])
        for row in range(CANVAS_H)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", CANVAS_W, CANVAS_H, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw, 6))
        + _png_chunk(b"IEND", b"")
    )


async def ensure_pose_uploaded(comfyui_url: str, pose_id: str) -> str | None:
    """Render+upload a pose skeleton to ComfyUI once; return its filename.

    Returns None on upload failure — callers fall back to a pose-less edit.
    """
    if pose_id not in POSES:
        return None
    if pose_id in _uploaded:
        return _uploaded[pose_id]
    filename = f"pose_lib_{pose_id}.png"
    png = render_pose_png(pose_id)
    try:
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field("overwrite", "true")
            form.add_field("type", "input")
            form.add_field("image", png, filename=filename, content_type="image/png")
            async with session.post(
                f"{comfyui_url}/upload/image", data=form,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                body = await resp.text()
        if resp.status != 200:
            logger.error(
                "[POSE_LIB] ComfyUI upload failed for %s: HTTP %s %s",
                pose_id, resp.status, body[:200], stack_info=True,
            )
            return None
        _uploaded[pose_id] = filename
        logger.info("[POSE_LIB] Uploaded %s (%d bytes)", filename, len(png))
        return filename
    except Exception:
        logger.error("[POSE_LIB] ComfyUI upload error for %s", pose_id, exc_info=True)
        return None


def pose_prompt_block() -> str:
    """Pose catalogue block for the scene-instruction user prompt."""
    lines = [f"- {pid}: {desc}" for pid, (desc, _) in POSES.items()]
    return "\n".join(lines)


def reset_upload_cache() -> None:
    """Test helper: forget uploaded filenames."""
    _uploaded.clear()
