"""One-off: send run #4 (side-view poses + staging backdrop) to the GM chat."""

import asyncio
import json
import os
import re

import aiohttp
from aiohttp_socks import ProxyConnector

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_BOT_GAME_MASTER_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
IMG_DIR = "/app/logs/gm_review_20261111/run4"
GAME = "ixisyr"
TURN = 11

_proxy = os.environ.get("TELEGRAM_SOCKS_PROXY", "")
_proxy_host, _proxy_port = _proxy.split(":") if ":" in _proxy else (_proxy, 1080)


def cut(s, n):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


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
                f"{API}/sendMessage",
                json={"chat_id": CHAT, "text": text},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                body = await r.text()
            assert '"ok":true' in body, body
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
                    body = await r.text()
            assert '"ok":true' in body, body
            await asyncio.sleep(1.2)

        await send_text(
            "🆕 ПРОГОН №4 — БОКОВЫЕ РАКУРСЫ + STAGING-ЗАДНИК (ixisyr, ход 11).\n\n"
            "По твоему фидбеку «жопой в экран / всегда одна поза»:\n"
            "• Библиотека поз выросла до 18, добавлены боковые и 3/4: console_side, console_sitting_side, examining_hologram, standing_three_quarter\n"
            "• Промпт требует НЕ дефолтить к одной позе и предпочитать боковой/3-4 ракурс (лицо видно), спина к камере — только для консоли в стене\n"
            "• pose_id теперь строгий enum — LLM не может галлюцинировать\n\n"
            "Результат регенерации: твой «спектральный анализ» → examining_hologram (3/4, рука у голограммы), пилот → console_side (боком).\n"
            "Мой vision-тест твоей картинки: 8/10 — лицо видно, рука у голограммы, ноги на палубе, взгляд на данные (не в камеру).\n\n"
            "⚠️ Честно: у хода 11 вводная упала в fallback из-за битого JSON от LLM (дописал хвост после JSON) — задник хода 11 импровизированный. Парсер починен, локационный задник (ход 10, тот же id) — стадией 2."
        )

        await send_photo(
            "pose_slach.png",
            "1️⃣ Выбранный скелет для твоего действия: examining_hologram\n(3/4 ракурс, рука поднята к голограмме на высоте головы — «анализ, сканирование, сверка с базой»)\n\nLLM теперь выбирает из 18 поз с подсказками «когда использовать».",
            "s1",
        )

        await send_photo(
            "turn_bg.png",
            "2️⃣ Локационный задник (ход 10, location=witness_orbit_black_hole_zone)\nStaging-промпт отработал: широкая палуба в нижней половине, якорные объекты, главный экран с объектом.\nХод 11 вернул ТОТ ЖЕ location.id — фон переиспользован бы, если бы не fallback-инцидент (починен).\n\nЕсть поверхность под ногами? Якоря в масштабе персонажа?",
            "s2",
        )

        entries = [
            ("a1", "3️⃣.1 Slach — Научный офицер (pose=examining_hologram)", "01_p281412419_action.png",
             "Провести углублённый спектральный анализ аномалии, которую Nikitka почувствовала в энергетическом фоне, и сравнить результаты с базой данных.",
             "Это регенерация ТОГО САМОГО действия, на которое ты жаловался."),
            ("a2", "3️⃣.2 Глеб Касаткин — Пилот (NPC, pose=console_side)", "02_pilot_action.png",
             "Повысить частоту навигационного импульса и просканировать сектор вперёд по курсу на полную глубину.",
             None),
        ]

        for stage, title, fname, action, extra in entries:
            caption = f"{title}\nДействие: {cut(action, 220)}"
            if extra:
                caption += f"\n\n{extra}"
            caption += "\n\nРакурс боковой/3-4? Лицо видно? Взгляд на оборудование?"
            await send_photo(fname, caption, stage)

        await send_text(
            "✅ Прогон №4 закончен. game-analyzer сейчас дорабатывается (субагент) — скоро в графе будет видно входы edit-модели: аватар + фон + скелет позы + инструкция."
        )

    print("sent OK")


asyncio.run(run())
