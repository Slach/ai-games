"""One-off: send the rebuilt scene comic (run 10 v2) to the GM chat."""

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
            "SCENE-КОМИКС v2 — по всем трём замечаниям:\n"
            "1) Одинаковый задник был потому, что панели были СТАРОЙ генерации (до фикса). "
            "Все 4 панели перегенерированы новым кодом: у медика теперь медбай (справа "
            "внизу), остальные трое — в периметре, они там реально ВМЕСТЕ, поэтому у них "
            "помещение одно и это корректно.\n"
            "2) Белый угол убран: раскладка теперь без пустых клеток в принципе.\n"
            "3) Раскладка по числу игроков ровно как ты просил (кодом): 1 = полная "
            "картинка; 2 = одна строка; 3 = одна строка шире; 4 = квадрат 2×2; 5 = 3+2 "
            "(нижний ряд шире); 6+ = до 3 строк. Каждый ряд — полная ширина, у ряда с "
            "меньшим числом панелей картинки крупнее."
        )
        for attempt in range(4):
            try:
                with open(f"{REVIEW}/run10_scene_comic_v2.png", "rb") as fh:
                    data = aiohttp.FormData()
                    data.add_field("chat_id", CHAT)
                    data.add_field("caption", caption)
                    data.add_field("reply_markup", kb("scene"))
                    data.add_field("photo", fh, filename="run10_scene_comic_v2.png", content_type="image/png")
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
