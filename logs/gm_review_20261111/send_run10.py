"""One-off: send the 2026-11-14 fix verification (run 10) to the GM chat."""

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


async def send_photo(session, path, caption, stage):
    for attempt in range(4):
        try:
            with open(path, "rb") as fh:
                data = aiohttp.FormData()
                data.add_field("chat_id", CHAT)
                data.add_field("caption", caption)
                data.add_field("reply_markup", kb(stage))
                data.add_field("photo", fh, filename=os.path.basename(path), content_type="image/png")
                async with session.post(
                    f"{API}/sendPhoto", data=data,
                    timeout=aiohttp.ClientTimeout(total=240),
                ) as r:
                    body = await r.text()
                    assert '"ok":true' in body, body[:200]
            print(f"sent {stage} OK")
            return
        except Exception as exc:
            print(f"{stage} attempt {attempt + 1} failed: {exc}")
            await asyncio.sleep(5)
    raise RuntimeError(f"{stage}: failed after 4 attempts")


async def run():
    conn = ProxyConnector(host=_proxy_host, port=int(_proxy_port))
    async with aiohttp.ClientSession(connector=conn) as session:
        await send_photo(
            session,
            f"{REVIEW}/run10_medic_medbay.png",
            "ПРОГОН 10 — проверка фикса «задник ≠ локация действия»: действие медика "
            "в медотсеке → выведена новая локация medbay_emergency, для неё сгенерирован "
            "ОТСЕК их корабля в общем design language (тёмный алюминий, янтарные и "
            "циановые консоли), медик на коленях у операционного стола в профиль, руки "
            "работают. Рубки больше нет там, где её не должно быть.",
            "action",
        )
        await asyncio.sleep(2)
        await send_photo(
            session,
            f"{REVIEW}/run10_scene_comic.png",
            "ПРОГОН 10 — scene-стадия теперь СТРАНИЦА КОМИКСА: панель на каждого члена "
            "экипажа в начале хода, каждый в СВОЁМ помещении (детерминированная склейка "
            "уже готовых картинок — панель не может потеряться, задвоиться или "
            "перерисоваться моделью). Одна клетка пуста: в тестовой сборке одна панель "
            "не нашлась (в живом пайплайне берутся только существующие).",
            "scene",
        )
    print("all sent OK")


asyncio.run(run())
