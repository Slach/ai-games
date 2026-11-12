"""One-off: send run #3 (pose-reference pipeline) to the GM chat with buttons."""

import asyncio
import json
import os
import re

import aiohttp
from aiohttp_socks import ProxyConnector

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_BOT_GAME_MASTER_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
IMG_DIR = "/app/logs/gm_review_20261111/run3"
GAME = "ixisyr"
TURN = 9

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
            "🆕 ПРОГОН №3 — НОВЫЙ PIPELINE С POSE-РЕФЕРЕНСОМ (ixisyr, ход 9).\n\n"
            "Что изменилось с прошлого прогона:\n"
            "• LLM теперь выбирает ПОЗУ из библиотеки OpenPose-скелетов → скелет идёт ТРЕТЬЕЙ картинкой в Qwen-Edit («персонаж принимает позу Picture 3»)\n"
            "• Energy/non-humanoid виды — поза пропускается (анатомический гвард)\n"
            "• Мой vision-тест Slach: интеграция 7.5/10 против 2/10 в прошлом прогоне — руки на консолях, персонаж заякорен\n\n"
            "⚠️ Ход 9 и его задник сгенерились ещё СТАРЫМ кодом (гонка с деплоем) — локационные задники появятся со следующего хода. Крю: для NPC здесь ещё старый промпт аватара (контракт экипажа действует на новых).\n\n"
            "Оцени кнопками: 1️⃣ сам скелет-референс, 2️⃣ задник (старый), 3️⃣.1–3️⃣.4 картинки действий."
        )

        await send_photo(
            "pose_ref.png",
            "1️⃣ Скелет-референс pose_lib_console_typing.png\nOpenPose-скелет, отрендерен чистым Python (0 GPU), загружен в ComfyUI input.\nLLM выбрал именно эту позу для действия «перенаправить спектральный канал».\n\nПонятен ли скелет как референс?",
            "s1",
        )

        await send_photo(
            "turn_bg.png",
            "2️⃣ Задник хода 9 (СТАРЫЙ промпт — wide shot).\nВ следующей итерации задник = «сцена-площадка» (поверхность под ногами, якорные объекты, экраны вместо окон) и кэшируется по ЛОКАЦИИ.\n\nДля сравнения: сюда сейчас ставились персонажи.",
            "s2",
        )

        entries = [
            ("a1", "3️⃣.1 Slach — Научный офицер (гуманоид, pose=console_typing)", "02_p281412419_action.png",
             "Перенаправить весь спектральный канал на объект, жертвуя мониторингом обломков «Антареса»."),
            ("a2", "3️⃣.2 Jimmy Pop — Тактический офицер (человек, pose=console_typing)", "03_p703779807_action.png",
             "Выдвинуться навстречу объекту, используя обломки «Антареса» как заслон."),
            ("a3", "3️⃣.3 Nikitka — Офицер связи (ENERGY, поза отключена)", "01_p217329201_action.png",
             "Синхронизировать частоты всех бортовых систем с предполагаемым диапазоном объекта."),
            ("a4", "3️⃣.4 Маргарита Стойлова — Инженер-механик (NPC, pose=console_typing)", "06_chief_engineer_action.png",
             "Игнорировать приказ Александра и выдвинуться навстречу объекту для прямого инженерного анализа."),
        ]

        for stage, title, fname, action in entries:
            await send_photo(
                fname,
                f"{title}\nДействие: {cut(action, 200)}\n\nПоза соответствует? Персонаж заякорен? Смотрит в камеру или на оборудование?",
                stage,
            )

        await send_text(
            "✅ Прогон №3 закончен. Остальные 3 картинки (Aleksandr, Анна, Глеб) — в logs/gm_review_20261111/run3/.\n"
            "После следующего хода (локационный задник + staging-промпт) сделаю прогон №4."
        )

    print("sent OK")


asyncio.run(run())
