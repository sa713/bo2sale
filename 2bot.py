import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ContentType
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto
)
from aiogram.filters import Command
from config import (
    BOT_TOKEN, CHANNEL_ID, ALLOWED_CHAT_ID, CATEGORIES,
    PICKUP_LOCATIONS, AUTO_DELETE_SECONDS
)
from database import (
    init_db, save_post as save_post_db, delete_post as delete_post_db,
    get_post_by_channel_message_id, set_channel_message_id
)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()
user_data = {}

# === Utils ===
def format_post(post: dict) -> str:
    author = f"@{post['username']}" if post.get("username") else f"[профиль](tg://user?id={post['user_id']})"
    parts = [
        f"<b>Автор:</b> {author}",
        f"<b>Место получения:</b> {post['pickup']}",
        f"<b>Категория:</b> {post['category']}",
        f"<b>Описание:</b>\n{post['description']}"
    ]
    if post.get("price"):
        parts.insert(3, f"<b>Цена:</b> {post['price']}")
    return "\n\n".join(parts)

async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(ALLOWED_CHAT_ID, user_id)
        return member.status in ("member", "creator", "administrator")
    except:
        return False

async def save_post(post_data: dict) -> int:
    media = [InputMediaPhoto(media=pid) for pid in post_data["photos"][:10]]
    caption = format_post(post_data)

    if len(media) == 1:
        sent = await bot.send_photo(CHANNEL_ID, photo=post_data["photos"][0], caption=caption)
    else:
        media[0].caption = caption
        await bot.send_media_group(CHANNEL_ID, media=media)
        sent = await bot.send_message(CHANNEL_ID, text=caption)

    post_id = save_post_db(
        post_data["user_id"],
        post_data.get("username"),
        post_data["description"],
        post_data["category"],
        post_data["pickup"],
        post_data["price"],
        post_data["photos"]
    )
    set_channel_message_id(post_id, sent.message_id)
    return post_id

async def schedule_auto_delete(chat_id, message_id, post_id):
    await asyncio.sleep(AUTO_DELETE_SECONDS)
    try:
        await bot.delete_message(chat_id, message_id)
        delete_post_db(post_id)
    except:
        pass

# === Handlers ===
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not await check_membership(bot, message.from_user.id):
        await message.answer("❌ Для публикации товара нужно быть участником чата.")
        return

    user_data[message.from_user.id] = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "step": "description",
        "photos": []
    }
    await message.answer("👋 Привет! Отправь описание товара (до 4000 символов).")

@dp.message(F.text & F.from_user.id.in_(user_data))
async def handle_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    data = user_data[user_id]
    step = data.get("step")

    if step == "description":
        if not text or len(text) > 4000:
            await message.answer("❌ Описание не должно быть пустым и не более 4000 символов.")
            return
        data["description"] = text
        data["step"] = "photos"
        await message.answer("📸 Теперь отправь до 10 фотографий. После нажми 'Готово'.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="✅ Готово", callback_data="photos_done")]]
            ))

    elif step == "price":
        data["price"] = text
        data["step"] = "pickup"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=loc, callback_data=f"pickup:{loc}")]
                             for loc in PICKUP_LOCATIONS])
        await message.answer("📍 Укажи место получения товара:", reply_markup=kb)

    elif step == "photos" and text.lower() == "готово":
        await handle_photos_done(message)
    else:
        await message.answer("Пожалуйста, следуй шагам: описание → фото → категория → цена → место получения.")

@dp.message(F.content_type == ContentType.PHOTO)
async def handle_photo(message: Message):
    data = user_data.get(message.from_user.id)
    if not data or data.get("step") != "photos":
        return

    if len(data["photos"]) >= 10:
        await message.answer("❌ Можно загрузить не более 10 фотографий.")
        return

    data["photos"].append(message.photo[-1].file_id)
    await message.answer(f"✅ Фото {len(data['photos'])}/10 добавлено.")

@dp.callback_query(F.data == "photos_done")
async def handle_photos_done(callback: CallbackQuery):
    data = user_data.get(callback.from_user.id)
    if not data or not data.get("photos"):
        await callback.answer("📸 Сначала отправь хотя бы одно фото.")
        return
    data["step"] = "category"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=cat, callback_data=f"category:{cat}")]
                         for cat in CATEGORIES])
    await callback.message.answer("📂 Выбери категорию:", reply_markup=kb)

@dp.callback_query(F.data.startswith("category:"))
async def handle_category(callback: CallbackQuery):
    category = callback.data.split(":")[1]
    data = user_data.get(callback.from_user.id)
    data["category"] = category
    data["step"] = "price"
    await callback.message.answer("💸 Укажи цену товара.")

@dp.callback_query(F.data.startswith("pickup:"))
async def handle_pickup(callback: CallbackQuery):
    pickup = callback.data.split(":")[1]
    data = user_data.get(callback.from_user.id)
    if pickup not in PICKUP_LOCATIONS:
        await callback.answer("❌ Неверное место.")
        return

    data["pickup"] = pickup
    data["step"] = "done"
    post_id = await save_post(data)
    await callback.message.answer("🎉 Объявление опубликовано!")

    if AUTO_DELETE_SECONDS > 0:
        asyncio.create_task(schedule_auto_delete(CHANNEL_ID, post_id, post_id))

    user_data.pop(callback.from_user.id, None)

# === Run Bot ===
if __name__ == "__main__":
    init_db()
    asyncio.run(dp.start_polling(bot))
