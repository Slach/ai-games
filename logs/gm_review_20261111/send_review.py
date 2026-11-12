"""One-off: send ixisyr turn-8 intermediate images to the GM chat for review.

Runs inside the telegram-bot container (needs the SOCKS proxy to reach
api.telegram.org). Images are staged in /app/logs/gm_review_20261111/.
"""

import asyncio
import os
import re

import aiohttp
from aiohttp_socks import ProxyConnector

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_BOT_GAME_MASTER_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
IMG_DIR = "/app/logs/gm_review_20261111"

_proxy = os.environ.get("TELEGRAM_SOCKS_PROXY", "")
_proxy_host, _proxy_port = _proxy.split(":") if ":" in _proxy else (_proxy, 1080)


def cut(s, n):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


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

        async def send_photo(fname, caption):
            with open(os.path.join(IMG_DIR, fname), "rb") as fh:
                data = aiohttp.FormData()
                data.add_field("chat_id", CHAT)
                data.add_field("caption", caption)
                data.add_field("photo", fh, filename=fname, content_type="image/png")
                async with session.post(
                    f"{API}/sendPhoto", data=data,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as r:
                    body = await r.text()
            assert '"ok":true' in body, body
            await asyncio.sleep(1.2)

        await send_text(
            "🔎 ПРОГОН КАРТИНОЧНОГО PIPELINE — игра ixisyr, ход 8 (свежая регенерация,\n"
            "текущие модели: аватары MOSS-Image-SFT NVFP4, действия Qwen-Image-2.1 viggle-turbo).\n\n"
            "Пришли 4 стадии, как их видит игрок. По каждой ответь, пожалуйста:\n"
            "👍 / 👎 и одной фразой ЧТО именно не так (поза, привязка к фону, стиль, логика).\n\n"
            "Стадии: 1️⃣ аватар+персонаж → 2️⃣ вводная+общий задник хода → 3️⃣ картинка брифинга+список действий → 4️⃣.1–4️⃣.4 картинка выбранного действия каждого члена экипажа."
        )

        await send_photo(
            "01_p281412419_avatar.png",
            "1️⃣ Аватар + персонаж\nSlach — Научный офицер, гуманоид.\nМодель: MOSS-Image-SFT NVFP4, RGBA-вырезка на прозрачном фоне.\n\nНравится ли аватар как «лицо» персонажа?",
        )

        await send_photo(
            "turn_bg.png",
            "2️⃣ Общая вводная хода 8 + turn_background\n"
            "Сеттинг: «Зона у горизонта событий, «Свидетель» привязан к обломкам «Антареса», приливная тяга нарастает».\n"
            "Конфликт: приливная резонансная волна разрушает оба корабля; у экипажа минуты, чтобы отстыковаться, но двое не могут выйти из пролома.\n\n"
            "Это общий задник ВСЕГО хода (без персонажей), на который потом накладывается каждый персонаж.\n"
            "Соответствует ли задник вводной? Достаточно ли «якорей» (объектов, за которые можно цеплять персонажей)?",
        )

        await send_photo(
            "char_slach.png",
            "3️⃣ Картинка личного брифинга (приходит перед списком действий)\nХод 8 — Slach — Научный офицер — «твои модели лгут, а переборка третьего отсека не собирается».\nСписок действий следующим сообщением.",
        )

        await send_text(
            "3️⃣ Список действий хода 8 (Slach, научный офицер):\n"
            "1. " + cut("Перепрограммировать систему аварийного шлюза так, чтобы отстыковка сработала автоматически через три цикла, даже если Александр не успеет вернуться", 140) + "\n"
            "2. " + cut("Взять портативный резонансный стабилизатор из аварийного ящика и попытаться удержать переборку, пока Стойлова и Никитка не смогут безопасно отключиться", 140) + "\n"
            "3. " + cut("Пройти через расширяющуюся трещину переборки, чтобы передать Стойловой и Никитке координаты спорного маршрута напрямую", 140) + "\n"
            "4. " + cut("Оставить консоли и лично помочь Александру разорвать стыковочный узел вручную — держать второй конец троса", 140) + "\n"
            "5. " + cut("Связаться с Никиткой напрямую через его нити, чтобы вытащить его из паттерна силой — нужно вставить себя в поток данных", 140) + "\n\n"
            "Игрок выбрал: действие 1.\nСочетается ли картинка брифинга с этим списком?"
        )

        entries = [
            ("4️⃣.1 Slach — Научный офицер (игрок)", "01_p281412419_action.png",
             "Перепрограммировать систему аварийного шлюза для автоотстыковки через три цикла; нужен доступ к закрытому сегменту бортового ядра.",
             "Instruction: Place the character… at the severed docking clamps junction, leaning against the exposed framework, one hand gripping a sparking cable, the other reaching toward a cracked hull panel"),
            ("4️⃣.2 Aleksandr — Начальник безопасности (игрок)", "02_p741428082_action.png",
             "Физически тащить Стойлову к шлюзу за титановый ошейник, разрывая её нейронные связи с «архитектурой».", None),
            ("4️⃣.3 Маргарита Стойлова — Инженер-механик (NPC)", "03_chief_engineer_action.png",
             "Заклеить трещину в переборке третьего отсека аварийным герметиком, пока она не стала разломом.", None),
            ("4️⃣.4 Глеб Касаткин — Пилот (NPC)", "04_pilot_action.png",
             "Покинуть пилотское кресло, взять гидравлический резак и идти в кормовой отсек помогать Александру.", None),
        ]

        for title, fname, action, extra in entries:
            caption = f"{title}\nВыбранное действие: {cut(action, 220)}"
            if extra:
                caption += "\n\n" + cut(extra, 200)
            caption += "\n\nПерсонаж стоит на чём-то или «висит»? Поза соответствует действию? Тот же задник, что у остальных?"
            await send_photo(fname, caption)

        await send_text(
            "✅ Это всё. Аватары NPC (перегенерированы заново MOSS-Image) лежат в logs/gm_review_20261111/.\n"
            "Жду фидбек по номерам 1️⃣–4️⃣.4: 👍/👎 + что именно смущает."
        )

    print("sent OK")


asyncio.run(run())
