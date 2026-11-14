"""One-off: send the grounded scene comic (run 10 v9) to the GM chat."""

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
            "SCENE-КОМИКС v5 — по обоим замечаниям к «почти идеально»:\n"
            "• Шарик больше НЕ подвешен: не-гуманоиды заземляются — «resting its mass on "
            "the deck or hovering LOW over its workstation, NOT hanging in mid-air» (и для "
            "участников, и для актора).\n"
            "• Действия читаемы: каждому — РАЗНЫЙ конкретный объект сцены (консоль/панель/"
            "люк/стол), никто не стоит без дела лицом к остальным. Спектр-14 печатает, "
            "шарик лежит на постаменте у своей панели, медик в медбое у щитка.\n"
            "• Страница ландшафтная (обе строки 3:1).\n"
            "Честное замечание: в ЭТОМ сиде Слач получил красноватую кожу — блик анатомии "
            "шарика на соседа (trait bleed). Это сида-варианс multiref-модели, не механика "
            "раскладки; живой ход даст новый сид."
        )
        for attempt in range(4):
            try:
                with open(f"{REVIEW}/run10_scene_comic_v9.png", "rb") as fh:
                    data = aiohttp.FormData()
                    data.add_field("chat_id", CHAT)
                    data.add_field("caption", caption)
                    data.add_field("reply_markup", kb("scene"))
                    data.add_field("photo", fh, filename="run10_scene_comic_v9.png", content_type="image/png")
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
