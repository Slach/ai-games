"""One-off: send the turn-6 repair package (avatar pose + shaft backdrop) to the GM chat."""

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
TURN = 6

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
            f"{REVIEW}/run12_slach_avatar.png",
            "РЕМОНТ 1 — аватар Slach (ход 6, char 👎 «поза в аватаре кривая»): корень найден — "
            "в промпте аватара было «Сидит на корабельном кресле, обхватив колени», и edit "
            "тащил эту позу в каждую картинку. Аватар перегенерирован ПЕРЕСАДКОЙ ПОЗЫ: "
            "edit от старого аватара — лицо, кожа, форма и знаки различия сохранены, поза "
            "теперь нейтральная (стоя, руки вдоль тела). Регенерация из текста теряет "
            "идентичность (первая попытка дала другого человека) — поэтому только edit.",
            "char",
        )
        await asyncio.sleep(2)
        await send_photo(
            session,
            f"{REVIEW}/run12_slach_char.png",
            "РЕМОНТ 2 — char Slach ход 6 с новым аватаром и исправленным задником шахты: "
            "«корабль в шахту» был из-за того, что gc-LLM пометил шахту aboard_ship=True → "
            "задник строился как EDIT МОСТИКА (отсюда люк и клёпаный пол в древних руинах). "
            "Теперь: aboard_ship=True только для внутренних помещений корабля (строгое "
            "правило в схеме), шахта перегенерирована txt2img как руины, gc хода 6 "
            "исправлен. Не-гуманоидам добавлен запрет ЛИЦА на уровне guard/lock "
            "(«it has NO face at all — no eyes, no mouth»).",
            "action",
        )
    print("all sent OK")


asyncio.run(run())
