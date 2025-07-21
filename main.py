import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from aiogram.dispatcher.filters import CommandStart
from config import (
    BOT_TOKEN, CHANNEL_ID, ALLOWED_CHAT_ID, DB_NAME,
    CATEGORIES, PICKUP_LOCATIONS, AUTO_DELETE_SECONDS
)
from db import init_db, save_post, set_channel_message_id, get_post_by_channel_message_id, delete_post

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# Временное хранилище для шагов создания объявления
user_sessions = {}

def make_category_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(text=cat, callback_data=f"cat_{cat}") for cat in CATEGORIES]
    keyboard.add(*buttons)
    return keyboard

def make_pickup_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(text=place, callback_data=f"pickup_{place}") for place in PICKUP_LOCATIONS]
    keyboard.add(*buttons)
    return keyboard

@dp.message_handler(CommandStart())
async def cmd_start(message: types.Message):
    # Проверка на участие в чате
    try:
        member = await bot.get_chat_member(ALLOWED_CHAT_ID, message.from_user.id)
        if member.status not in ['member', 'creator', 'administrator']:
            raise ValueError()
    except:
        await message.answer("Извините, бот доступен только участникам определённого чата.")
        return

    user_sessions[message.from_user.id] = {
        "photos": [],
        "step": "description"
    }
    await message.answer("Привет! Отправьте описание товара (до 4000 символов).")

@dp.message_handler(lambda m: m.from_user.id in user_sessions and user_sessions[m.from_user.id]["step"] == "description")
async def process_description(message: types.Message):
    if len(message.text) > 4000:
        await message.answer("Слишком длинное описание. Попробуйте сократить до 4000 символов.")
        return
    user_sessions[message.from_user.id]["description"] = message.text
    user_sessions[message.from_user.id]["step"] = "price"
    await message.answer("Теперь введите цену (можно текстом).")

@dp.message_handler(lambda m: m.from_user.id in user_sessions and user_sessions[m.from_user.id]["step"] == "price")
async def process_price(message: types.Message):
    user_sessions[message.from_user.id]["price"] = message.text
    user_sessions[message.from_user.id]["step"] = "category"
    await message.answer("Выберите категорию:", reply_markup=make_category_keyboard())

@dp.callback_query_handler(lambda c: c.data.startswith("cat_"))
async def process_category(callback: types.CallbackQuery):
    category = callback.data[4:]
    user_sessions[callback.from_user.id]["category"] = category
    user_sessions[callback.from_user.id]["step"] = "pickup"
    await callback.message.edit_text("Теперь выберите, где можно забрать товар:", reply_markup=make_pickup_keyboard())

@dp.callback_query_handler(lambda c: c.data.startswith("pickup_"))
async def process_pickup(callback: types.CallbackQuery):
    pickup = callback.data[7:]
    session = user_sessions.get(callback.from_user.id, {})
    if not session:
        await callback.answer("Сессия устарела. Начните заново /start.")
        return

    session["pickup"] = pickup
    session["step"] = "photos"
    await callback.message.edit_text("Теперь отправьте до 10 фотографий товара (одна за другой). Когда закончите — напишите 'Готово'.")

@dp.message_handler(content_types=types.ContentType.PHOTO)
async def process_photos(message: types.Message):
    session = user_sessions.get(message.from_user.id)
    if session and session.get("step") == "photos":
        photo_id = message.photo[-1].file_id
        session["photos"].append(photo_id)
        if len(session["photos"]) >= 10:
            session["step"] = "confirm"
            await confirm_post(message)
        else:
            await message.answer(f"Фото {len(session['photos'])}/10 получено. Отправьте следующее или напишите 'Готово'.")

@dp.message_handler(lambda m: m.text.lower() == "готово")
async def finish_photos(message: types.Message):
    session = user_sessions.get(message.from_user.id)
    if session and session.get("step") == "photos":
        session["step"] = "confirm"
        await confirm_post(message)

async def confirm_post(message: types.Message):
    session = user_sessions[message.from_user.id]
    text = f"<b>Категория:</b> {session['category']}\n<b>Где забирать:</b> {session['pickup']}\n<b>Цена:</b> {session['price']}\n\n{session['description']}"
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("✅ Опубликовать", callback_data="publish"))
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@dp.callback_query_handler(lambda c: c.data == "publish")
async def publish_post(callback: types.CallbackQuery):
    session = user_sessions.pop(callback.from_user.id, None)
    if not session:
        await callback.answer("Сессия устарела.")
        return

    text = (
        f"<b>{session['category']}</b>\n"
        f"<b>Где забирать:</b> {session['pickup']}\n"
        f"<b>Цена:</b> {session['price']}\n\n"
        f"{session['description']}\n\n"
        f"Автор: @{callback.from_user.username or 'без ника'}"
    )
    media_group = [types.InputMediaPhoto(media=pid) for pid in session["photos"][:10]]
    msg_ids = []

    if len(media_group) > 1:
        sent = await bot.send_media_group(chat_id=CHANNEL_ID, media=media_group)
        msg_ids = [m.message_id for m in sent]
        msg = await bot.send_message(CHANNEL_ID, text, parse_mode="HTML")
    else:
        msg = await bot.send_photo(CHANNEL_ID, photo=session["photos"][0], caption=text, parse_mode="HTML")

    # Сохраняем в БД
    post_id = save_post(
        callback.from_user.id,
        callback.from_user.username,
        session["description"],
        session["category"],
        session["pickup"],
        session["price"],
        session["photos"]
    )
    set_channel_message_id(post_id, msg.message_id)

    # Кнопка удаления
    delete_kb = InlineKeyboardMarkup()
    delete_kb.add(InlineKeyboardButton("❌ Удалить объявление", callback_data=f"delete_{msg.message_id}"))
    await bot.send_message(callback.from_user.id, "Объявление опубликовано:", reply_markup=delete_kb)

    # Автоудаление
    if AUTO_DELETE_SECONDS > 0:
        asyncio.create_task(schedule_auto_delete(msg.chat.id, msg.message_id, post_id))

async def schedule_auto_delete(chat_id, message_id, post_id):
    await asyncio.sleep(AUTO_DELETE_SECONDS)
    try:
        await bot.delete_message(chat_id, message_id)
        delete_post(post_id)
    except Exception:
        pass

@dp.callback_query_handler(lambda c: c.data.startswith("delete_"))
async def handle_delete(callback: types.CallbackQuery):
    message_id = int(callback.data.split("_")[1])
    post = get_post_by_channel_message_id(message_id)
    if post and post[1] == callback.from_user.id:
        try:
            await bot.delete_message(CHANNEL_ID, message_id)
            delete_post(post[0])
            await callback.message.edit_text("Объявление удалено.")
        except Exception:
            await callback.message.edit_text("Не удалось удалить сообщение.")
    else:
        await callback.answer("Вы не автор этого объявления или оно уже удалено.")

if __name__ == "__main__":
    init_db()
    executor.start_polling(dp, skip_updates=True)
