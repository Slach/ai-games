"""One-off: send the alive-crew-capped outcome to the GM chat."""

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
            "🆕 «МАКСИМУМ X» ВМЕСТО МАГИЧЕСКОЙ ЧЕТВЁРКИ (твоё замечание)\n\n"
            "Переделано как ты сказал: X = живые члены экипажа НА МОМЕНТ генерации картинки "
            "(после применения итогов хода — гибели уже учтены).\n\n"
            "• Референсы = только живые: живые игроки + активные NPC (мёртвые больше не попадают в слоты)\n"
            "• Промпт: «не более X персонажей — живой экипаж сейчас = X; никогда не больше людей, чем X»\n"
            "• Для итога хода 11 сейчас X=4 → на кадре ровно 4 персонажа, все разные\n"
            "• Случай «персонаж погиб в этом ходе» остаётся покрыт отдельной death-картинкой (его собственный аватар), outcome собирает только живых\n"
            "• Попутно словил и починил свой же синтакс-баг (сирота-кавычка в docstring) — тесты 507 OK, контейнер healthy"
        )

        await send_photo(
            "outcome_new.png",
            "Итог хода 11: X=4 живых → 4 персонажа\nЭнергетик у окна, пилот с шлемом за консолью, член экипажа у кофейного пятна, кибернетик сидит.\nДубликатов нет, масштаб в норме, тот же мостик.\n\nТеперь норм?",
            "o3",
        )

    print("sent OK")


asyncio.run(run())
