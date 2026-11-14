"""One-off: send the farewell-panel outcome (run 13) to the GM chat."""

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


async def run():
    conn = ProxyConnector(host=_proxy_host, port=int(_proxy_port))
    async with aiohttp.ClientSession(connector=conn) as session:
        caption = (
            "OUTCOME (ход 6, реген) — погибшие больше не исчезают молча:\n"
            "• Погибшие этого хода (из dead_crew_members, с причиной смерти) попадают в "
            "outcome отдельной ПРОЩАЛЬНОЙ ПАНЕЛЬЮ — последний момент или экипаж над телом. "
            "В регене: панель 4 — Тарас растворяется в пепельной мгле (причина — удушье), "
            "панель 2 — прощание. Живые считаются отдельно, погибший не появляется живым "
            "в других панелях.\n"
            "• «Второй персонаж» в action хода 6 был галлюцинацией модели (участников не "
            "передавалось — (none) в логе): теперь при пустых participants инструкция "
            "обязана сказать «персонаж В СЦЕНЕ ПОЛНОСТЬЮ ОДИН, без двойников и фоновых "
            "силуэтов».\n"
            "• Char хода 7 ≈ хода 6: частично ожидаемо (тот же аватар + та же комната), "
            "теперь каждому ходу задаётся своя рамка кадра по кругу (низкий ракурс / "
            "крупный бок / со спины в сцену / контровой свет / сверху).\n"
            "Честно: Тарас в панелях прощания — растворяющийся силуэт, а не лава-шар "
            "(лица нет). Драматично, но анатомию шарика модель здесь не удержала."
        )
        for attempt in range(4):
            try:
                with open(f"{REVIEW}/run13_outcome_farewell.png", "rb") as fh:
                    data = aiohttp.FormData()
                    data.add_field("chat_id", CHAT)
                    data.add_field("caption", caption)
                    data.add_field("reply_markup", kb("outcome"))
                    data.add_field("photo", fh, filename="run13_outcome_farewell.png", content_type="image/png")
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
