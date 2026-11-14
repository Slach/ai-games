"""One-off: send the solo-wide grouped scene comic (run 10 v8) to the GM chat."""

import asyncio
import json
import os

import aiohttp
from aiohttp_socks import ProxyConnector

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_BOT_GAME_MASTER_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
REVIEW = "/app/logs/gm_review_20261111"
GAME = "althef"
TURN = 5

_proxy = os.environ.get("TELEGRAM_SOCKS_PROXY", "")
_proxy_host, _proxy_port = _proxy.split(":") if ":" in _proxy else (_proxy, 1080)


def kb(stage):
    return json.dumps(
        {
            "inline_keyboard": [
                [
                    {"text": "👍", "callback_data": f"gmfb:v:{GAME}:{TURN}:{stage}:up"},
                    {"text": "👎", "callback_data": f"gmfb:v:{GAME}:{TURN}:{stage}:down"},
                    {"text": "🤔", "callback_data": f"gmfb:v:{GAME}:{TURN}:{stage}:meh"},
                ]
            ]
        }
    )


async def run():
    conn = ProxyConnector(host=_proxy_host, port=int(_proxy_port))
    async with aiohttp.ClientSession(connector=conn) as session:
        caption = (
            "SCENE-КОМИКС v4 — соло тоже широкой полосой (3:1):\n"
            "• Верхняя строка: трое co-located в периметре одной картинкой — шарик с лавой "
            "сохранён (анатомия участника теперь прописана LOCK-уровнем в задании).\n"
            "• Нижняя строка: медик одна в медбае — тоже широкая полоса, страница стала "
            "единообразной (раньше соло-квадрат съедал полстраницы).\n"
            "Правило аспектов теперь: 1-3 человека → 3:1, 2 человека → 2:1, 4+ → 4:1. "
            "Смешанная группа: актор — гуманоид (физическая лексика не-гуманоида больше не "
            "заставляет людей парить), не-гуманоидные участники идут с анатомической меткой."
        )
        for attempt in range(4):
            try:
                with open(f"{REVIEW}/run10_scene_comic_v8.png", "rb") as fh:
                    data = aiohttp.FormData()
                    data.add_field("chat_id", CHAT)
                    data.add_field("caption", caption)
                    data.add_field("reply_markup", kb("scene"))
                    data.add_field("photo", fh, filename="run10_scene_comic_v8.png", content_type="image/png")
                    async with session.post(
                        f"{API}/sendPhoto", data=data,
                        timeout=aiohttp.ClientTimeout(total=240),
                    ) as r:
                        body = await r.text()
                        assert '"ok":true' in body, body[:200]
                print("sent OK")
                return
            except Exception as exc:
                print(f"attempt {attempt + 1} failed: {exc}")
                await asyncio.sleep(5)
    raise RuntimeError("failed after 4 attempts")


asyncio.run(run())
