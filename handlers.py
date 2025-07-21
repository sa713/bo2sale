from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ContentType
from aiogram.filters import Text
import sqlite3
import logging

from config import (
    DATABASE_PATH,
    CHANNEL_ID,
    ALLOWED_CHAT_ID,
    PICKUP_LOCATIONS,
    CATEGORIES,
    POST_EXPIRATION_DAYS
)

router = Router()

# Состояния пользователя - временно хранить вводимые данные
user_data = {}

# Проверка, состоит ли пользователь в нужном чате
async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(ALLOWED_CHAT_ID, user_id)
        if member.status in ("left", "kicked"):
            return False
        return True
    except Exception:
        return False

# Команда /start
@router.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer("👋 Привет! Отправь описание товара для публикации на барахолке.")

# Обработка описания товара (первое сообщение)
@router.message()
async def handle_description(message: Message):
    user_id = message.from_user.id
    bot = message.bot

    if not await check_membership(bot, user_id):
        await message.reply("❌ Для публикации товара нужно быть участником чата.")
        return

    text = message.text
    if not text or len(text) > 4000:
        await message.reply("❌ Описание не должно быть пустым и не более 4000 символов.")
        return

    user_data[user_id] = {
        "description": text,
        "photos": [],
        "category": None,
        "price": None,
        "pickup": None,
        "message_id": message.message_id,
        "chat_id": message.chat.id,
    }
    await message.answer("📸 Теперь отправь фотографии товара (до 10 штук).")

# Обработка фотографий
@router.message(F.content_type == ContentType.PHOTO)
async def handle_photos(message: Message):
    user_id = message.from_user.id
    if user_id not in user_data:
        await message.reply("❌ Сначала отправьте описание товара.")
        return
    photos = user_data[user_id]["photos"]
    if len(photos) >= 10:
        await message.reply("❌ Максимум 10 фотографий.")
        return
    file_id = message.photo[-1].file_id
    photos.append(file_id)
    await message.reply(f"✅ Фото добавлено. Сейчас у вас {len(photos)} фото.")
    if len(photos) == 10:
        await message.answer("📋 Теперь выберите категорию товара:", reply_markup=category_keyboard())

def category_keyboard():
    buttons = [
        [InlineKeyboardButton(text=cat, callback_data=f"category:{cat}")]
        for cat in CATEGORIES
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.callback_query(Text(startswith="category:"))
async def category_chosen(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_data:
        await callback.answer("❌ Сначала отправьте описание товара.")
        return
    category = callback.data.split(":", 1)[1]
    if category not in CATEGORIES:
        await callback.answer("❌ Неверная категория.")
        return
    user_data[user_id]["category"] = category
    await callback.message.edit_text(f"Категория выбрана: <b>{category}</b>\n\nТеперь укажите цену или подарок за товар:")
    await callback.answer()

# Обработка цены
@router.message()
async def handle_price(message: Message):
    user_id = message.from_user.id
    if user_id not in user_data:
        # Игнорируем сообщения, если пользователь не в процессе
        return
    if user_data[user_id]["category"] is None:
        # Ждем пока выберут категорию
        return
    if user_data[user_id]["price"] is not None:
        # Цена уже указана - ждем выбор места получения
        return

    price = message.text.strip()
    if len(price) == 0 or len(price) > 100:
        await message.reply("❌ Цена должна быть не пустой и не длиннее 100 символов.")
        return

    user_data[user_id]["price"] = price
    await message.answer("📍 Теперь выберите, где забирать товар:", reply_markup=pickup_keyboard())

def pickup_keyboard():
    buttons = [
        [InlineKeyboardButton(text=loc, callback_data=f"pickup:{loc}")]
        for loc in PICKUP_LOCATIONS
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.callback_query(Text(startswith="pickup:"))
async def pickup_chosen(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_data:
        await callback.answer("❌ Сначала отправьте описание товара.")
        return
    pickup = callback.data.split(":", 1)[1]
    if pickup not in PICKUP_LOCATIONS:
        await callback.answer("❌ Неверное место получения.")
        return
    user_data[user_id]["pickup"] = pickup

    data = user_data[user_id]

    # Собираем итоговое сообщение
    text = (
        f"<b>Описание:</b>\n{data['description']}\n\n"
        f"<b>Категория:</b> {data['category']}\n"
        f"<b>Цена:</b> {data['price']}\n"
        f"<b>Где забирать:</b> {data['pickup']}\n"
        f"<b>Автор:</b> @{callback.from_user.username or callback.from_user.full_name}"
    )

    media = []
    for file_id in data["photos"]:
        media.append({"type": "photo", "media": file_id})

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_post"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_post")
        ]
    ])

    # Сохраня в user_data для публикации
    user_data[user_id]["final_text"] = text
    user_data[user_id]["confirm_kb"] = confirm_kb

    # Если есть фото — отправляем альбом, иначе просто сообщение
    if len(media) > 0:
        await callback.message.answer_media_group(media)
    await callback.message.answer(text, reply_markup=confirm_kb)

    await callback.answer()

@router.callback_query(Text(startswith="confirm_post"))
async def confirm_post(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_data:
        await callback.answer("❌ Нет данных для публикации.")
        return

    data = user_data[user_id]
    bot = callback.bot

    # Сохраняем объявление в БД
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()

    c.execute("""
    INSERT INTO bo2sale_ads
    (user_id, username, description, category, price, pickup, photos, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        user_id,
        callback.from_user.username or "",
        data["description"],
        data["category"],
        data["price"],
        data["pickup"],
        ",".join(data["photos"])
    ))
    ad_id = c.lastrowid
    conn.commit()
    conn.close()

    # Формируем сообщение для канала
    text = data["final_text"] + f"\n\nID объявления: {ad_id}"

    media = []
    for file_id in data["photos"]:
        media.append({"type": "photo", "media": file_id})

    # Отправляем в канал (альбом если есть фото)
    if len(media) > 0:
        if len(media) == 1:
            await bot.send_photo(CHANNEL_ID, media[0]["media"], caption=text)
        else:
            await bot.send_media_group(CHANNEL_ID, media)
            await bot.send_message(CHANNEL_ID, text)
    else:
        await bot.send_message(CHANNEL_ID, text)

    # Запоминаем ID сообщения канала для удаления
    # Для этого нужно получить message_id после отправки
    # Поскольку send_media_group не возвращает message, добавим простое упрощение:
    # — не будем сейчас реализовывать точное привязывание, это можно улучшить

    # Очистка данных пользователя
    user_data.pop(user_id, None)

    await callback.message.edit_text("✅ Объявление опубликовано!")
    await callback.answer()

@router.callback_query(Text(startswith="cancel_post"))
async def cancel_post(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_data:
        user_data.pop(user_id)
    await callback.message.edit_text("❌ Публикация отменена.")
    await callback.answer()
