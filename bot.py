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
import asyncio
from config import (
    API_TOKEN, CHANNEL_ID, ALLOWED_CHAT_ID, CATEGORIES,
    PICKUP_LOCATIONS, AUTO_DELETE_SECONDS, DB_NAME, AUTO_DELETE_DAYS,
    HELP_TEXT, RULES_TEXT
)
from database import (
    init_db, save_post as save_post_db, delete_post as delete_post_db,
    get_post_by_channel_message_id, set_channel_message_id
)

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

# --- FSM ---
class PostFSM(StatesGroup):
    description = State()
    photos = State()
    category = State()
    price = State()
    pickup = State()
    confirm = State()

user_data = {}

# --- Команды ---
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
#        [InlineKeyboardButton(text="Мои объявления", callback_data="my_posts")]
    ])
    await message.answer(
        "Привет! Отправь текст описания товара, и начнём оформление объявления.\n\n"
        "Также ты можешь посмотреть инструкцию по использованию бота - просто вызови команду /help.",
        reply_markup=kb
    )
    await state.set_state(PostFSM.description)

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return

    await message.answer(HELP_TEXT)

@dp.message(Command("rules"))
async def cmd_rules(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return

    await message.answer(RULES_TEXT)

@dp.message(Command("my"))
async def cmd_my_posts(message: types.Message):
    if message.chat.type != "private":
        return

    user_id = message.from_user.id
    cursor.execute("SELECT id, description FROM bo2sale_posts WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    if not rows:
        return await message.answer("У вас пока нет объявлений.")
    for desc, message_id in rows:
        channel_link = f"https://t.me/c/{str(CHANNEL_ID)[4:]}/{message_id}"
        text = f"{desc[:100]}...\n\n{hlink('🔗 Открыть объявление', channel_link)}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Удалить", callback_data=f"delete:{post_id}")]
        ])
        await message.answer(text, reply_markup=kb)

@dp.message(PostFSM.description)
async def process_description(message: types.Message, state: FSMContext):
    # Проверка, состоит ли пользователь в чате
    try:
        member = await bot.get_chat_member(ALLOWED_CHAT_ID, message.from_user.id)
        if member.status in ['left', 'kicked']:
            return await message.answer("Вы должны быть участником чата, чтобы публиковать объявления.")
    except Exception as e:
        return await message.answer("Не удалось проверить ваше участие в чате.")

    user_data[message.from_user.id] = {
        "description": message.text,
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "full_name": message.from_user.full_name,
        "photo_ids": []
    }
    await message.answer("Теперь пришли фото товара (от 1 до 10). Когда закончишь добавлять фотографии - напиши 'дальше'.")
    await state.set_state(PostFSM.photos)

@dp.message(PostFSM.photos, F.photo)
async def process_photos(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    user_data[message.from_user.id]["photo_ids"].append(photo_id)
    if len(user_data[message.from_user.id]["photo_ids"]) >= 10:
        await message.answer("Максимум 10 фото. Переходим к следующему шагу.")
        await ask_category(message)
        await state.set_state(PostFSM.category)

@dp.message(PostFSM.photos, F.text.lower() == "дальше")
async def skip_photos(message: types.Message, state: FSMContext):
    await ask_category(message)
    await state.set_state(PostFSM.category)

@dp.message(PostFSM.photos)
async def limit_photos(message: types.Message, state: FSMContext):
    await message.answer("Пожалуйста, пришли фото товара (до 10). Когда закончишь, напиши 'дальше'.")

async def ask_category(message: types.Message):
    kb = InlineKeyboardBuilder()
    for cat in CATEGORIES:
        kb.button(text=cat, callback_data=f"category:{cat}")
    kb.adjust(1)
    await message.answer("Выбери категорию:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("category:"))
async def process_category(call: types.CallbackQuery, state: FSMContext):
    category = call.data.split(":", 1)[1]
    user_data[call.from_user.id]["category"] = category
    await call.message.edit_text(f"Категория: {category}")
    await call.message.answer("Укажи цену (или напиши 'бесплатно' или 'за шоколадку'):")
    await state.set_state(PostFSM.price)

@dp.message(PostFSM.price)
async def process_price(message: types.Message, state: FSMContext):
    user_data[message.from_user.id]["price"] = message.text
    kb = InlineKeyboardBuilder()
    for place in PICKUP_LOCATIONS:
        kb.button(text=place, callback_data=f"pickup:{place}")
    await message.answer("Выбери место получения:", reply_markup=kb.as_markup())
    await state.set_state(PostFSM.pickup)

@dp.callback_query(F.data.startswith("pickup:"))
async def process_pickup(call: types.CallbackQuery, state: FSMContext):
    pickup = call.data.split(":", 1)[1]
    user_data[call.from_user.id]["pickup"] = pickup
    await call.message.edit_text(f"Место получения: {pickup}")
    await confirm_post(call.message, call.from_user.id)
    await state.set_state(PostFSM.confirm)

async def confirm_post(_, user_id: int):
    post_text = format_post(user_data[user_id])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Опубликовать", callback_data="publish")],
        [InlineKeyboardButton(text="Отменить", callback_data="cancel")]
    ])
    await bot.send_message(user_id, post_text, reply_markup=kb)

@dp.callback_query(F.data == "publish")
async def publish_post(call: types.CallbackQuery, state: FSMContext):
    try:
        await call.answer()

        data = user_data.get(call.from_user.id)
        if not data:
            await call.message.answer("Что-то пошло не так.")
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
        cursor.execute("""
            INSERT INTO bo2sale_posts (
                user_id, username, full_name,
                description, price, pickup, category,
                photo_ids, post_date,
                message_id, message_ids
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["user_id"],
            data["username"],
            data["full_name"],
            data["description"],
            data["price"],
            data["pickup"],
            data["category"],
            ",".join(data["photo_ids"]),
            now,
            message_ids[0],                        # первый message_id (для совместимости)
            ",".join(str(mid) for mid in message_ids)  # все id через запятую
        ))
        conn.commit()

        await bot.send_message(call.from_user.id, "✅ Объявление опубликовано!")
        await bot.send_message(call.from_user.id, "Хочешь добавить ещё одно? Просто отправь описание товара.")

        await state.set_state(PostFSM.description)
        user_data.pop(call.from_user.id, None)

    except Exception as e:
        print(f"Ошибка при публикации: {e}")
        await bot.send_message(call.from_user.id, "Произошла ошибка при публикации. Попробуй ещё раз.")

@dp.callback_query(F.data == "cancel")
async def cancel_post(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Объявление отменено.")
    await state.clear()
    user_data.pop(call.from_user.id, None)

@dp.callback_query(F.data.startswith("delete:"))
async def delete_post(call: types.CallbackQuery):
    post_id = int(call.data.split(":")[1])

    # достаем все message_id для объявления
    cursor.execute("SELECT message_ids FROM bo2sale_posts WHERE id = ?", (post_id,))
    row = cursor.fetchone()

    if row:
        message_ids_str = row[0] or ""
        message_ids = [mid for mid in message_ids_str.split(",") if mid.strip()]

        # удаляем все сообщения альбома
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

# --- Формат поста ---
def format_post(post: dict) -> str:
    author = f"@{post['username']}" if post['username'] else hlink(post['full_name'], f"tg://user?id={post['user_id']}")
    return f"{post['description']}\n\n" \
           f"#{post['category'].replace(' ', '_')}\n\n" \
           f"{post['price']}\n\n" \
           f"{post['pickup']}\n" \
           f"{author}"

# --- Автоудаление ---
async def auto_delete_old():
    while True:
        threshold = (datetime.now() - timedelta(days=AUTO_DELETE_DAYS)).strftime("%Y-%m-%d")
        cursor.execute("SELECT id, message_id FROM bo2sale_posts WHERE post_date < ?", (threshold,))
        rows = cursor.fetchall()
        for row in rows:
            post_id, message_id = row
            try:
                await bot.delete_message(CHANNEL_ID, message_id)
            except:
                pass
            cursor.execute("DELETE FROM bo2sale_posts WHERE id = ?", (post_id,))
        conn.commit()
        await asyncio.sleep(86400)

# --- Запуск ---
async def main():
    asyncio.create_task(auto_delete_old())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
