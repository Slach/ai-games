"""One-off: send the 2026-11-13 feedback-fix regeneration (althef turn 3) to the GM chat."""

import asyncio
import json
import os

import aiohttp
from aiohttp_socks import ProxyConnector

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_BOT_GAME_MASTER_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
REVIEW = "/app/logs/gm_review_20261111"
FILES = "/app/logs/gm_review_20261111"
GAME = "althef"
TURN = 3

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
        # char + actions already delivered
        await send_photo(
            f"{REVIEW}/outcome3_comic.png",
            "OUTCOME — КОМИКС-СТРАНИЦА 2×2: четыре панели = четыре живых члена экипажа, каждый в деле со своей частью исхода (связист на горящем эфире, инженер заваривает трещину, Спектр-14 над картой резонансных каналов, медик слышит вибрации сквозь переборку). Никто не смотрит в камеру, медик на месте.",
            "outcome",
        )

    print("sent OK")


asyncio.run(run())
