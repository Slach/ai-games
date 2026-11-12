"""One-off: send the location-backed outcome proof to the GM chat."""

import asyncio
import json
import os

import aiohttp
from aiohttp_socks import ProxyConnector

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_BOT_GAME_MASTER_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
IMG_DIR = "/app/logs/gm_review_20261111/run5"
GAME = "ixisyr"
TURN = 11

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

        async def send_text(text):
            async with session.post(
                f"{API}/sendMessage", json={"chat_id": CHAT, "text": text},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                assert '"ok":true' in await r.text()
            await asyncio.sleep(1.2)

        async def send_photo(fname, caption, stage):
            with open(os.path.join(IMG_DIR, fname), "rb") as fh:
                data = aiohttp.FormData()
                data.add_field("chat_id", CHAT)
                data.add_field("caption", caption)
                data.add_field("reply_markup", kb(stage))
                data.add_field("photo", fh, filename=fname, content_type="image/png")
                async with session.post(
                    f"{API}/sendPhoto", data=data,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as r:
                    assert '"ok":true' in await r.text()
            await asyncio.sleep(1.2)

        await send_text(
            "🆕 ФИКС «задник последствий — какой-то другой» (ixisyr, ход 11)\n\n"
            "Причина была в коде: outcome-картинка генерилась через multi-reference ТОЛЬКО с аватарами экипажа — задник локации туда вообще не передавался, модель придумывала свой мир.\n\n"
            "Исправлено: задник локации теперь Picture 1 в outcome-генерации, экипаж — Pictures 2-11, инструкция требует «вся сцена внутри среды Picture 1, только её объекты и свет» + «масштаб людей, никаких гигантов» (это же правило добавлено в action-инструкции — фикс «гигантского пилота»).\n\n"
            "Бонус-починка: строка хода 11 была отравлена fallback-обстоятельствами (битый JSON, парсер уже починен) — локация восстановлена хирургически, все картинки хода 11 теперь на заднике локации.\n\n"
            "Мой vision-тест нового итога: 8/10 — «то же место, что на action-картинках», экипаж в масштабе, свет согласован."
        )

        await send_photo(
            "turn_bg.png",
            "Задник локации witness_orbit_black_hole_zone (Picture 1)",
            "b1",
        )

        await send_photo(
            "outcome_new.png",
            "Итоги хода 11 — регенерация на заднике локации\nНикитка держит резонанс, Слэш получил результат спектрального анализа, объект пульсирует за панорамным окном.\n\nТот же мостик, что и на action-картинках? Масштаб экипажа нормальный?",
            "o1",
        )

        await send_text("✅ Это всё по фидбеку прогона №4. Жду 👍/👎.")

    print("sent OK")


asyncio.run(run())
