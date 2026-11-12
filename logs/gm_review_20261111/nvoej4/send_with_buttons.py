"""One-off: send nvoej4 turn-8 review WITH inline feedback buttons (gmfb:*).

Runs inside the telegram-bot container (SOCKS proxy). Callbacks are handled
by gm_feedback_callback in bot.py and recorded to logs/gm_feedback-*.jsonl.
"""

import asyncio
import json
import os
import re

import aiohttp
from aiohttp_socks import ProxyConnector

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_BOT_GAME_MASTER_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
IMG_DIR = "/app/logs/gm_review_20261111/nvoej4"
GAME = "nvoej4"
TURN = 8

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
            "🔎 ПРОГОН №2, ТЕПЕРЬ С КНОПКАМИ — игра nvoej4, ход 8.\n"
            "Экипаж целиком NPC: Пётр Лавров (научный), Велитта Со-Нейра (медик), Вектор Ноль (пилот).\n"
            "Аватары и картинки действий перегенерированы только что.\n\n"
            "Жми 👍 / 👎 / 🤔 под каждой картинкой. На 👎/🤔 бот спросит причину кнопками "
            "(поза / задник / свет / стиль / логика / другое) — можно потом добавить коммент текстом, "
            "он прикрепится к стадии. Всё пишется в logs/gm_feedback-*.jsonl."
        )

        await send_photo(
            "01_science_officer_avatar.png",
            "1️⃣ Аватар + персонаж\nПётр Лавров — Научный офицер.\nМодель: MOSS-Image-SFT NVFP4 (свежая генерация).\n\nНравится как «лицо» персонажа?",
            "s1",
        )

        await send_photo(
            "turn_bg.png",
            "2️⃣ Общая вводная хода 8 + turn_background\n"
            "Сеттинг: корабль дрейфует на глубине 180 км в атмосфере Таргус-IV, в зоне резонанса. Чужие корабли вернулись.\n"
            "Конфликт: чужие эскадры поняли, что карты были ложными, и начали преследование; резонанс усиливается, Вектор Ноль больше его не контролирует.\n\n"
            "Это общий задник хода (без персонажей), на который накладывается каждый персонаж.\n"
            "Соответствует ли вводной? Есть ли поверхность/объекты, на которых можно «поставить» персонажа?",
            "s2",
        )

        await send_photo(
            "scene.png",
            "3️⃣ Общая сцена хода (приходит игроку перед списком действий).\nСписок действий Петра Лаврова — следующим сообщением.",
            "s3",
        )

        await send_text(
            "3️⃣ Список действий хода 8 (Пётр Лавров, научный офицер):\n"
            "1. " + cut("Открываешь резервную запись формулы активного сопротивления и пересчитываешь её под условия узла на 230-м километре; если сойдётся — сигнал можно сгенерировать дистанционно", 150) + "\n"
            "2. " + cut("Ищешь в данных резонанса природный узел генерации — место, где волна сама создаёт конструктивную интерференцию; требует вскрытия старых архивов", 150) + "\n"
            "3. " + cut("Забираешь портативный генератор и переходишь в отсек герметизации, чтобы лично активировать формулу изнутри резонанса", 150) + "\n"
            "4. " + cut("Подключаешь сенсорный кабель к нейроинтерфейсу, чтобы считать резонанс без фильтров — но нейроинтерфейс тоже «хром»", 150) + "\n"
            "5. " + cut("Перехватываешь управление навигацией у Вектора Ноль и сам ведёшь корабль к узлу через математический фильтр", 150) + "\n\n"
            "NPC выбрал: действие 3 (лично в отсек герметизации).\nСочетается ли сцена с этим списком?"
        )

        entries = [
            ("a1", "4️⃣.1 Пётр Лавров — Научный офицер (NPC)", "01_science_officer_action.png",
             "Перейти в отсек герметизации с портативным генератором и лично активировать формулу изнутри резонанса; обшивка потрескалась, каждый шаг — по рёбрам."),
            ("a2", "4️⃣.2 Велитта Со-Нейра — Медицинский офицер (NPC)", "02_medical_officer_action.png",
             "Держать раненого: резонансная синхронизация хромированных ладоней с его грудной клеткой, дав волне доступ к своим системам."),
            ("a3", "4️⃣.3 Вектор Ноль — Пилот (NPC)", "03_pilot_action.png",
             "Резкий вертикальный сброс на 230 километров на полном ходу, пока спираль чужих не замкнулась."),
        ]

        for stage, title, fname, action in entries:
            await send_photo(
                fname,
                f"{title}\nВыбранное действие: {cut(action, 230)}\n\nПерсонаж стоит на чём-то или «висит»? Поза соответствует действию?",
                stage,
            )

        await send_text(
            "✅ Прогон №2 закончился. Кнопки работают — фидбек падает в JSONL, я его читаю и агрегирую.\n"
            "Если хочешь ещё прогон другой игры/хода — скажи."
        )

    print("sent OK")


asyncio.run(run())
