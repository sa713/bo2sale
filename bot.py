import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import StateFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import hlink
from aiogram.utils.formatting import as_list
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
import os
import asyncio
from config import (
    API_TOKEN, CHANNEL_ID, ALLOWED_CHAT_ID, CATEGORIES,
    PICKUP_LOCATIONS, AUTO_DELETE_SECONDS, DB_NAME, AUTO_DELETE_DAYS,
    HELP_TEXT, RULES_TEXT
)

# Создаём путь к лог-файлу
LOG_FILE = os.path.join(os.path.dirname(__file__), "bot.log")

# Настройка логирования
logger = logging.getLogger()
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logger.addHandler(file_handler)

# Чтобы логи шли и в консоль (journald), можно оставить:
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(console_handler)

# --- Инициализация ---
bot = Bot(token=API_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
logging.basicConfig(level=logging.INFO)

# --- БД ---
conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS bo2sale_posts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    username TEXT,
    full_name TEXT,
    description TEXT,
    price TEXT,
    pickup TEXT,
    category TEXT,
    photo_ids TEXT,
    post_date TEXT,
    message_id INTEGER,
    message_ids TEXT
)
""")
conn.commit()
# Миграция: заполняем message_ids для старых записей, если они пустые
cursor.execute("""
    UPDATE bo2sale_posts
    SET message_ids = CAST(message_id AS TEXT)
    WHERE (message_ids IS NULL OR message_ids = '')
      AND message_id IS NOT NULL
""")
conn.commit()


# --- FSM ---
class PostFSM(StatesGroup):
    description = State()
    photos = State()
    category = State()
    price = State()
    pickup = State()
    confirm = State()


user_data = {}


# --- Хелпер форматирования поста ---
def format_post(post: dict) -> str:
    author = (
        f"@{post['username']}"
        if post["username"]
        else hlink(post["full_name"], f"tg://user?id={post['user_id']}")
    )
    return (
        f"{post['description']}\n\n"
        f"#{post['category'].replace(' ', '_')}\n\n"
        f"{post['price']}\n\n"
        f"{post['pickup']}\n"
        f"{author}"
    )


# --- Проверка, что пользователь состоит в нужном чате ---
async def check_membership(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(ALLOWED_CHAT_ID, user_id)
        return member.status not in ("left", "kicked")
    except Exception as e:
        logger.error(f"Ошибка при проверке участия в чате: {e}")
        return False


# --- Старт / помощь / правила ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать объявление", callback_data="create_post")],
            [InlineKeyboardButton(text="Мои объявления", callback_data="my_posts")],
            [InlineKeyboardButton(text="Правила", callback_data="rules")],
#            [InlineKeyboardButton(text="Помощь", callback_data="help")],
        ]
    )
    await message.answer(
        "Привет! 👋\n\n"
        "Я помогу тебе разместить объявление в барахолке.\n"
        "Нажми «Создать объявление», чтобы начать.",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "help")
async def callback_help(call: types.CallbackQuery):
    await call.message.edit_text(HELP_TEXT, disable_web_page_preview=True)


@dp.callback_query(F.data == "rules")
async def callback_rules(call: types.CallbackQuery):
    await call.message.edit_text(RULES_TEXT, disable_web_page_preview=True)


# --- Создание объявления ---
@dp.callback_query(F.data == "create_post")
async def callback_create_post(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    is_member = await check_membership(user_id)

    if not is_member:
        await call.message.answer(
            "Похоже, ты не состоишь в чате жильцов.\n\n"
            "Вступи в чат, потом возвращайся ко мне 😊"
        )
        return

    await state.set_state(PostFSM.description)
    user_data[user_id] = {
        "user_id": user_id,
        "username": call.from_user.username,
        "full_name": call.from_user.full_name,
        "photo_ids": [],
    }
    await call.message.answer("Отправь текст объявления (до 4000 символов).")


@dp.message(PostFSM.description)
async def process_description(message: types.Message, state: FSMContext):
    if len(message.text) > 4000:
        await message.answer("Слишком длинное описание. Попробуй сократить до 4000 символов.")
        return

    data = user_data.get(message.from_user.id, {})
    data["description"] = message.text
    user_data[message.from_user.id] = data

    await state.set_state(PostFSM.photos)
    await message.answer(
        "Теперь отправь до 10 фотографий товара (можно одним альбомом).\n"
        "Если фото не нужны — отправь «Пропустить».\n"
        "После добавления фотографий напиши «Дальше»."
    )


@dp.message(PostFSM.photos, F.photo)
async def process_photos(message: types.Message, state: FSMContext):
    data = user_data.get(message.from_user.id, {})
    photos = data.get("photo_ids", [])

    if len(photos) >= 10:
        await message.answer("Достигнут лимит в 10 фотографий. Переходим дальше.")
        await ask_category(message, state)
        return

    # фото может приходить как отдельной фоткой или частью альбома
    photos.append(message.photo[-1].file_id)
    data["photo_ids"] = photos
    user_data[message.from_user.id] = data


@dp.message(PostFSM.photos, F.text.casefold() == "пропустить")
async def skip_photos(message: types.Message, state: FSMContext):
    await ask_category(message, state)


@dp.message(PostFSM.photos)
async def process_photos_text(message: types.Message, state: FSMContext):
    # любое другое текстовое сообщение на этом шаге — считаем сигналом "дальше"
    await ask_category(message, state)


async def ask_category(message: types.Message, state: FSMContext):
    await state.set_state(PostFSM.category)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=cat, callback_data=f"cat:{cat}")]
            for cat in CATEGORIES
        ]
    )
    await message.answer("Выбери категорию:", reply_markup=kb)


@dp.callback_query(F.data.startswith("cat:"))
async def select_category(call: types.CallbackQuery, state: FSMContext):
    category = call.data.split(":", 1)[1]
    data = user_data.get(call.from_user.id, {})
    data["category"] = category
    user_data[call.from_user.id] = data

    await state.set_state(PostFSM.price)
    await call.message.edit_text("Укажи цену (например, «1,500» или «За шоколадку»).")


@dp.message(PostFSM.price)
async def process_price(message: types.Message, state: FSMContext):
    data = user_data.get(message.from_user.id, {})
    data["price"] = message.text
    user_data[message.from_user.id] = data

    await state.set_state(PostFSM.pickup)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=loc, callback_data=f"pickup:{loc}")]
            for loc in PICKUP_LOCATIONS
        ]
    )
    await message.answer("Где забирать товар?", reply_markup=kb)


@dp.callback_query(F.data.startswith("pickup:"))
async def select_pickup(call: types.CallbackQuery, state: FSMContext):
    pickup = call.data.split(":", 1)[1]
    data = user_data.get(call.from_user.id, {})
    data["pickup"] = pickup
    user_data[call.from_user.id] = data

    post_text = format_post(data)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Опубликовать", callback_data="publish"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
            ]
        ]
    )

    photos = data.get("photo_ids") or []

    # 1) показываем все фото как альбом (если они есть)
    if photos:
        media_group = [types.InputMediaPhoto(media=pid) for pid in photos]
        await call.message.answer_media_group(media_group)

    # 2) отдельным сообщением – текст + кнопки
    await call.message.answer(post_text, reply_markup=kb)

    await state.set_state(PostFSM.confirm)


# --- Публикация ---
@dp.callback_query(F.data == "publish")
async def publish_post(call: types.CallbackQuery, state: FSMContext):
    try:
        data = user_data.get(call.from_user.id)
        if not data:
            await call.message.answer("Что-то пошло не так. Попробуй заново /start.")
            return

        post_text = format_post(data)
        media_group = [types.InputMediaPhoto(media=photo_id) for photo_id in data["photo_ids"]]

        message_ids = []

        if media_group:
            # первая фотка с подписью
            media_group[0].caption = post_text
            media_group[0].parse_mode = ParseMode.HTML

            sent_messages = await bot.send_media_group(CHANNEL_ID, media_group)
            message_ids = [m.message_id for m in sent_messages]
        else:
            sent = await bot.send_message(CHANNEL_ID, post_text)
            message_ids = [sent.message_id]

        now = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            """
            INSERT INTO bo2sale_posts (
                user_id, username, full_name,
                description, price, pickup, category,
                photo_ids, post_date,
                message_id, message_ids
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                data["user_id"],
                data["username"],
                data["full_name"],
                data["description"],
                data["price"],
                data["pickup"],
                data["category"],
                ",".join(data["photo_ids"]),
                now,
                message_ids[0],  # первый message_id (для совместимости)
                ",".join(str(mid) for mid in message_ids),  # все id через запятую
            ),
        )
        conn.commit()

        await bot.send_message(call.from_user.id, "✅ Объявление опубликовано!")

        # Повторяем меню действий
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Создать объявление", callback_data="create_post")],
                [InlineKeyboardButton(text="Мои объявления", callback_data="my_posts")],
                [InlineKeyboardButton(text="Правила", callback_data="rules")],
#                [InlineKeyboardButton(text="Помощь", callback_data="help")],
            ]
        )

        await bot.send_message(
            call.from_user.id,
            "Хочешь создать ещё одно объявление? 👇",
            reply_markup=keyboard
        )

        # Возврат к первому шагу FSM
        await state.set_state(PostFSM.description)
        user_data.pop(call.from_user.id, None)

    except Exception as e:
        print(f"Ошибка при публикации: {e}")
        await bot.send_message(
            call.from_user.id, "Произошла ошибка при публикации. Попробуй ещё раз."
        )


@dp.callback_query(F.data == "cancel")
async def cancel_post(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Объявление отменено.")
    await state.clear()
    user_data.pop(call.from_user.id, None)


# --- Мои объявления ---
@dp.callback_query(F.data == "my_posts")
async def my_posts(call: types.CallbackQuery):
    user_id = call.from_user.id

    cursor.execute(
        """
        SELECT id, description, message_id
        FROM bo2sale_posts
        WHERE user_id = ?
    """,
        (user_id,),
    )
    rows = cursor.fetchall()

    if not rows:
        await call.message.answer("У тебя пока нет объявлений.")
        return

    for post_id, desc, message_id in rows:
        channel_link = f"https://t.me/c/{str(CHANNEL_ID)[4:]}/{message_id}"
        text = (
            f"{desc[:100]}...\n\n"
            f"{hlink('🔗 Открыть объявление', channel_link)}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Удалить", callback_data=f"delete:{post_id}")
                ]
            ]
        )
        await call.message.answer(text, reply_markup=kb, disable_web_page_preview=True)


# --- Удаление объявления по кнопке ---
@dp.callback_query(F.data.startswith("delete:"))
async def delete_post(call: types.CallbackQuery):
    post_id = int(call.data.split(":")[1])

    # достаем и основной message_id, и все сохраненные message_ids
    cursor.execute(
        """
        SELECT message_id, message_ids
        FROM bo2sale_posts
        WHERE id = ?
    """,
        (post_id,),
    )
    row = cursor.fetchone()

    if row:
        single_message_id, message_ids_str = row

        # список всех сообщений, которые нужно удалить
        message_ids = []

        if message_ids_str:
            message_ids = [
                mid for mid in message_ids_str.split(",") if mid.strip()
            ]
        elif single_message_id:
            # старые записи, где ещё не было заполненного поля message_ids
            message_ids = [str(single_message_id)]

        # удаляем все сообщения альбома / одиночный пост
        for mid in message_ids:
            try:
                await bot.delete_message(CHANNEL_ID, int(mid))
            except Exception as e:
                print(f"Не смог удалить сообщение {mid}: {e}")

        # удаляем запись из БД
        cursor.execute("DELETE FROM bo2sale_posts WHERE id = ?", (post_id,))
        conn.commit()

        await call.message.edit_text("Объявление удалено.")
    else:
        await call.message.answer("Объявление не найдено.")


# --- Автоудаление старых объявлений ---
async def auto_delete_old():
    while True:
        threshold = (datetime.now() - timedelta(days=AUTO_DELETE_DAYS)).strftime(
            "%Y-%m-%d"
        )

        cursor.execute(
            """
            SELECT id, message_id, message_ids
            FROM bo2sale_posts
            WHERE post_date < ?
        """,
            (threshold,),
        )
        rows = cursor.fetchall()

        for post_id, message_id, message_ids_str in rows:
            message_ids = []

            if message_ids_str:
                message_ids = [
                    mid for mid in (message_ids_str or "").split(",") if mid.strip()
                ]
            elif message_id:
                # старые записи, где ещё не было заполненного поля message_ids
                message_ids = [str(message_id)]

            for mid in message_ids:
                try:
                    await bot.delete_message(CHANNEL_ID, int(mid))
                except Exception as e:
                    print(f"Не смог удалить сообщение {mid}: {e}")

            cursor.execute("DELETE FROM bo2sale_posts WHERE id = ?", (post_id,))

        conn.commit()
        await asyncio.sleep(86400)


# --- Запуск ---
async def main():
    asyncio.create_task(auto_delete_old())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
