"""One-off: send the outcome comic (run 9 tail) to the GM chat."""

import asyncio
import json
import os

import aiohttp
from aiohttp_socks import ProxyConnector

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_BOT_GAME_MASTER_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
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
        path = "/app/logs/gm_review_20261111/outcome3_comic.png"
        caption = (
            "OUTCOME — КОМИКС-СТРАНИЦА 2×2: четыре панели = четыре живых члена экипажа, каждый "
            "в деле со своей частью исхода (связист на горящем эфире, инженер заваривает трещину, "
            "Спектр-14 над картой резонансных каналов, медик слышит вибрации сквозь переборку). "
            "Никто не смотрит в камеру, медик на месте."
        )
        for attempt in range(4):
            try:
                with open(path, "rb") as fh:
                    data = aiohttp.FormData()
                    data.add_field("chat_id", CHAT)
                    data.add_field("caption", caption)
                    data.add_field("reply_markup", kb("outcome"))
                    data.add_field("photo", fh, filename="outcome3_comic.png", content_type="image/png")
                    async with session.post(
                        f"{API}/sendPhoto", data=data,
                        timeout=aiohttp.ClientTimeout(total=240),
                    ) as r:
                        assert '"ok":true' in await r.text()
                print("sent OK")
                return
            except Exception as exc:
                print(f"attempt {attempt + 1} failed: {exc}")
                await asyncio.sleep(4)
    raise RuntimeError("failed after 4 attempts")


asyncio.run(run())
