"""One-off: send the dedup-fixed outcome to the GM chat."""

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
            "🆕 ФИКС «два одинаковых персонажа» (итоги хода 11)\n\n"
            "Причина: в outcome-генерацию уходили референсы ВСЕГО экипажа (9-10 аватаров) — модель путалась в слотах и рендерила одного персонажа дважды.\n\n"
            "Исправлено двумя правилами:\n"
            "• Промпт: «не более 4 персонажей — только тех, на ком центр момента; каждый Picture используется МАКСИМУМ ОДИН РАЗ; дубликат персонажа — провал»\n"
            "• Код: кэп на 6 аватар-референсов в продовом flow (задник + 6 = 7 слотов вместо 11)\n\n"
            "Теперь на кадре: 4 разных персонажа, энергетик у окна, объект-сгусток на площадке, тот же мостик с горизонтом событий."
        )

        await send_photo(
            "outcome_new.png",
            "Итоги хода 11 — регенерация без дубликатов\nНикитка держит резонанс (энергетик у окна), экипаж анализирует объект-сгусток, спектральный результат получен.\n\nДубликаты исчезли? Масштаб/свет/место — ок?",
            "o2",
        )

        await send_text(
            "ℹ️ Статус игр: ixisyr завершена (mission_complete на ходу 12), nvoej4 — активна, ход 10 сгенерится планировщиком. Следующий живой ход nvoej4 будет первым полным E2E: локационный задник из кэша + 18 поз + outcome на заднике локации + кэп дубликатов.\n\n"
            "game-analyzer уже показывает входы edit-модели (аватар/фон/поза/инструкция) в карточке узла."
        )

    print("sent OK")


asyncio.run(run())
