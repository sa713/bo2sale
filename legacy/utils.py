import logging
from config import ALLOWED_CHAT_ID

from aiogram.types import Message
from config import CHANNEL_ID, AUTO_DELETE_AFTER_DAYS
from database import get_post, delete_post
from datetime import datetime, timedelta
from aiogram.utils.markdown import hbold, hitalic

async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(ALLOWED_CHAT_ID, user_id)
        status = member.status
        logging.info(f"Проверка членства user_id={user_id}, статус={status}")
        if status in ("left", "kicked", "deleted"):
            return False
        return True
    except Exception as e:
        logging.error(f"Ошибка при проверке членства user_id={user_id}: {e}")
        return False

def format_post(post: dict) -> str:
    if post.get("username"):
        author_text = f"@{post['username']}"
    else:
        author_text = f"[профиль](tg://user?id={post['user_id']})"

    parts = [
#        f"{hbold('Дата публикации:')} {post['date']}",
        f"{hbold('Автор:')} {author_text}",
        f"{hbold('Место получения:')} {post['pickup']}",
        f"{hbold('Категория:')} {post['category']}",
        f"{hbold('Описание:')}\n{post['description']}"
    ]
    if post.get('price'):
        parts.insert(4, f"{hbold('Цена:')} {post['price']}")

    return "\n\n".join(parts)

async def save_post(post_data: dict, bot) -> int:
    from aiogram.types import InputMediaPhoto
    from aiogram import types

    media = []
    for idx, file_id in enumerate(post_data["photos"]):
        if idx == 0:
            continue
        media.append(InputMediaPhoto(media=file_id))

    caption = format_post(post_data)

    buttons = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🗑 Удалить объявление", callback_data=f"delete:{post_data['user_id']}")]
        ]
    )

    if len(post_data["photos"]) == 1:
        sent = await bot.send_photo(CHANNEL_ID, photo=post_data["photos"][0], caption=caption, reply_markup=buttons)
    else:
        media.insert(0, InputMediaPhoto(media=post_data["photos"][0], caption=caption))
        sent = await bot.send_media_group(CHANNEL_ID, media=media)
        # Чтобы прикрепить кнопки, нужно отправить ещё одно сообщение с текстом
        sent = await bot.send_message(CHANNEL_ID, text=caption)

    return sent.message_id

async def delete_post(callback_query, bot):
    message_id = callback_query.message.message_id
    user_id = callback_query.from_user.id
    post = get_post(user_id)
    if post and post.get("channel_message_id") == message_id:
        await bot.delete_message(CHANNEL_ID, message_id)
        delete_post_db(user_id)
        await callback_query.message.answer("Объявление удалено ✅")
    else:
        await callback_query.message.answer("Невозможно удалить объявление или оно уже удалено ❌")
