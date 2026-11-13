"""One-off: send the staging-fixed outcome to the GM chat."""

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
            "🆕 ФИКС ПОСТАНОВКИ В ИТОГАХ (обе твои претензии по o2/o3)\n\n"
            "Причина: семантические правила (лицом к оборудованию, энергетик на объекте) были только в action-инструкции, а outcome-промпт — отдельный, без них.\n\n"
            "Добавлено в outcome-промпт (система + композиция):\n"
            "• каждый персонаж ЛИЦОМ к своей станции, взгляд на её экраны; никогда спиной к своей консоли\n"
            "• при панорамном окне — все лицом В комнату, если нарратив прямо не смотрит наружу\n"
            "• энергетики/плазма взаимодействуют с конкретным объектом сцены (парят над площадкой консоли, вплетены в панель), не болтаются в воздухе и не за окнами\n\n"
            "Также проверил ночной E2E: nvoej4 ход 10 сгенерирован полностью новым кодом — локация targus_iv_deep_atmosphere, action-картинки на локационном заднике, поза выбрана. Всё работает."
        )

        await send_photo(
            "outcome_new.png",
            "Итог хода 11 — регенерация с постановкой\nЭнергетик у панели консоли (внутри рубки), пилот лицом в комнату к своей станции, никто спиной к оборудованию, X=4 без дубликатов.\n\nТеперь норм?",
            "o4",
        )

    print("sent OK")


asyncio.run(run())
