"""One-off: send the per-location outcome comic (run 11) to the GM chat."""

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
            "OUTCOME (ход 6, реген) — пер-локационные задники:\n"
            "• В outcome теперь по ЗАДНИКУ НА ЛОКАЦИЮ: собираются локации, где реально "
            "занят экипаж (рубка/шахта + медбей), каждая входит отдельной референс-"
            "картинкой, LLM мапит «панель → задник её локации». Больше не бывает "
            "«безопасник вне корабля, а фон рубка».\n"
            "• CREW ONLY: панелям запрещено рисовать людей вне референсов — заложник в "
            "медбае больше не появится, пока экипаж его не встретил (нижняя панель — "
            "медик одна, стол пустой).\n"
            "• По Slach из прошлого фидбека: аватар НЕ перегенерировали — краснокожий "
            "гигант был бликом анатомии шарика в групповой панели; участникам групповой "
            "генерации теперь прописываются их внешностные якоря.\n"
            "Честно: в ЭТОМ сиде шарик снова схлопнулся в гуманоида справа — варианс "
            "multiref-модели, механика (задники/панели/запреты) работает."
        )
        for attempt in range(4):
            try:
                with open(f"{REVIEW}/run11_outcome_backdrops.png", "rb") as fh:
                    data = aiohttp.FormData()
                    data.add_field("chat_id", CHAT)
                    data.add_field("caption", caption)
                    data.add_field("reply_markup", kb("outcome"))
                    data.add_field("photo", fh, filename="run11_outcome_backdrops.png", content_type="image/png")
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
