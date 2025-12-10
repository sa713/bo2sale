from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto
)
from aiogram.enums import ContentType
from aiogram.filters import Command
import sqlite3
import logging
from config import pickup_locations
from keyboards import category_keyboard, pickup_keyboard
from utils import save_post, delete_post, check_membership, format_post

router = Router()

user_data = {}

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("👋 Привет! Отправь описание товара (до 4000 символов).")


@router.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    bot = message.bot
    text = message.text.strip()

    step = user_data.get(user_id, {}).get("step")

    if step is None or step == "description":
        if not await check_membership(bot, user_id):
            await message.reply("❌ Для публикации товара нужно быть участником чата.")
            return

        if not text or len(text) > 4000:
            await message.reply("❌ Описание не должно быть пустым и не более 4000 символов.")
            return

        user_data[user_id] = {
            "user_id": user_id,
            "username": message.from_user.username,
            "step": "photos",
            "description": text,
            "photos": [],
            "category": None,
            "price": None,
            "pickup": None,
            "message_id": message.message_id,
            "chat_id": message.chat.id,
        }
        await message.answer("📸 Теперь отправь фотографии товара (до 10 штук). После — нажми «✅ Готово».",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                 [InlineKeyboardButton(text="✅ Готово", callback_data="photos_done")]
                             ]))

    elif step == "price":
        user_data[user_id]["price"] = text
        user_data[user_id]["step"] = "pickup"
        await message.answer("📍 Укажи место получения товара:", reply_markup=pickup_keyboard())

    elif step == "photos":
        if text == "✅ Готово":
            await handle_photos_done(message)
        else:
            await message.answer("❌ Отправь фотографии товара или нажми «✅ Готово».")

    else:
        await message.answer("❌ Пожалуйста, следуй шагам: описание → фото → категория → цена → место получения.")


@router.message(F.content_type == ContentType.PHOTO)
async def handle_photo(message: Message):
    user_id = message.from_user.id

    if user_id not in user_data or user_data[user_id].get("step") != "photos":
        return

    if "photos" not in user_data[user_id]:
        user_data[user_id]["photos"] = []

    if len(user_data[user_id]["photos"]) >= 10:
        await message.answer("❌ Можно добавить не более 10 фотографий.")
        return

    photo_id = message.photo[-1].file_id
    user_data[user_id]["photos"].append(photo_id)

    if len(user_data[user_id]["photos"]) == 1:
        await message.answer("✅ Фотография получена. Добавь ещё или нажми «✅ Готово».",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                 [InlineKeyboardButton(text="✅ Готово", callback_data="photos_done")]
                             ]))
    else:
        await message.answer(f"✅ Добавлено фото {len(user_data[user_id]['photos'])}/10.")


@router.callback_query(F.data == "photos_done")
async def handle_photos_done(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = user_data.get(user_id)

    if not data or data.get("step") != "photos":
        await callback.answer("Сначала отправьте фотографии.")
        return

    if not data.get("photos"):
        await callback.answer("📸 Нужно отправить хотя бы одну фотографию.")
        return

    user_data[user_id]["step"] = "category"
    await callback.message.answer("📂 Выбери категорию:", reply_markup=category_keyboard())


@router.callback_query(F.data.startswith("category:"))
async def handle_category(callback: CallbackQuery):
    user_id = callback.from_user.id
    category = callback.data.split("category:")[1]
    user_data[user_id]["category"] = category
    user_data[user_id]["step"] = "price"
    await callback.message.answer("💸 Укажи цену товара (можно текстом: «бесплатно», «по договорённости» и т.д.).")


@router.callback_query(F.data.startswith("pickup:"))
async def handle_pickup(callback: CallbackQuery):
    user_id = callback.from_user.id
    pickup = callback.data.split("pickup:")[1]

    if pickup not in pickup_locations:
        await callback.answer("❌ Некорректное место.")
        return

    user_data[user_id]["pickup"] = pickup
    user_data[user_id]["step"] = "done"

    post_text = format_post(user_data[user_id])
    photos = user_data[user_id]["photos"]

    media = [InputMediaPhoto(media=photos[0], caption=post_text)]
    for photo in photos[1:10]:
        media.append(InputMediaPhoto(media=photo))

    post_data = {
        "user_id": user_id,
        "photos": photos,
        "pickup": user_data[user_id]["pickup"],
        "category": user_data[user_id]["category"],
        "price": user_data[user_id]["price"],
        "description": user_data[user_id]["description"],
        # "username": user_data[user_id].get("username"),
    }
    post_id = await save_post(post_data, callback.bot)

    await callback.message.answer("🎉 Твое объявление опубликовано!")

    del user_data[user_id]
