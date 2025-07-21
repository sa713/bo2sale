import logging
import sqlite3
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from config import TOKEN, CHANNEL_ID, AUTHORIZED_CHAT_ID, AUTO_DELETE_DAYS, CATEGORIES, PICKUP_LOCATIONS, DB_PATH

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# Хранилище временных данных пользователей
user_states = {}

# Подключение к базе данных
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute(f"""
CREATE TABLE IF NOT EXISTS bo2sale_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    description TEXT,
    photos TEXT,
    category TEXT,
    price TEXT,
    pickup TEXT,
    channel_message_id INTEGER,
    timestamp INTEGER
)
""")
conn.commit()


def is_user_authorized(chat_member: types.ChatMember) -> bool:
    return chat_member.status in ['member', 'creator', 'administrator']


@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    chat_member = await bot.get_chat_member(chat_id=AUTHORIZED_CHAT_ID, user_id=user_id)
    if not is_user_authorized(chat_member):
        await message.answer("Извините, бот доступен только для участников закрытого чата.")
        return
    await message.answer("Привет! Отправь описание товара (до 4000 символов).")
    user_states[user_id] = {'step': 'description'}


@dp.message_handler(content_types=types.ContentType.TEXT)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    if not state:
        await cmd_start(message)
        return

    if state['step'] == 'description':
        if len(message.text) > 4000:
            await message.answer("Описание слишком длинное (макс. 4000 символов). Попробуй ещё раз.")
            return
        state['description'] = message.text
        state['step'] = 'photos'
        state['photos'] = []
        await message.answer("Теперь отправь до 10 фотографий товара одним или несколькими сообщениями.")
    elif state['step'] == 'price':
        state['price'] = message.text
        await ask_pickup_location(message)


@dp.message_handler(content_types=types.ContentType.PHOTO)
async def handle_photos(message: types.Message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    if not state or state.get('step') != 'photos':
        return

    state['photos'].extend([photo.file_id for photo in message.photo[-1:]])
    if len(state['photos']) >= 10:
        state['photos'] = state['photos'][:10]
        await ask_category(message)
    else:
        await message.answer(f"Фотографии получены ({len(state['photos'])}/10). Добавь ещё или напиши /done, чтобы продолжить.")


@dp.message_handler(commands=['done'])
async def done_adding_photos(message: types.Message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    if not state or state.get('step') != 'photos':
        return
    if not state['photos']:
        await message.answer("Пожалуйста, добавь хотя бы одну фотографию.")
        return
    await ask_category(message)


async def ask_category(message: types.Message):
    user_id = message.from_user.id
    state = user_states[user_id]
    state['step'] = 'category'
    markup = InlineKeyboardMarkup()
    for cat in CATEGORIES:
        markup.add(InlineKeyboardButton(cat, callback_data=f"cat:{cat}"))
    await message.answer("Выбери категорию:", reply_markup=markup)


async def ask_price(message: types.Message):
    user_id = message.from_user.id
    user_states[user_id]['step'] = 'price'
    await message.answer("Укажи цену или напиши, на что хочешь обменять товар.")


async def ask_pickup_location(message: types.Message):
    user_id = message.from_user.id
    user_states[user_id]['step'] = 'pickup'
    markup = InlineKeyboardMarkup()
    for loc in PICKUP_LOCATIONS:
        markup.add(InlineKeyboardButton(loc, callback_data=f"pickup:{loc}"))
    await message.answer("Где забирать товар?", reply_markup=markup)


def build_post_text(state):
    parts = [
        f"<b>Описание:</b> {state['description']}",
        f"<b>Категория:</b> {state['category']}",
        f"<b>Цена/обмен:</b> {state['price']}",
        f"<b>Где забирать:</b> {state['pickup']}",
        f"<b>Автор:</b> @{state['username']}" if state['username'] else "<i>Автор без ника</i>"
    ]
    return "\n\n".join(parts)


async def confirm_post(user_id):
    state = user_states[user_id]
    state['step'] = 'confirm'
    caption = build_post_text(state)
    media = [types.InputMediaPhoto(photo, caption=caption if i == 0 else "") for i, photo in enumerate(state['photos'])]
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Опубликовать", callback_data="confirm:yes"),
        InlineKeyboardButton("✏️ Изменить", callback_data="confirm:no")
    )
    await bot.send_media_group(user_id, media)
    await bot.send_message(user_id, "Публикуем?", reply_markup=markup)


@dp.callback_query_handler(lambda c: c.data.startswith('cat:'))
async def callback_category(call: types.CallbackQuery):
    category = call.data.split(':')[1]
    user_id = call.from_user.id
    user_states[user_id]['category'] = category
    await call.answer()
    await ask_price(call.message)


@dp.callback_query_handler(lambda c: c.data.startswith('pickup:'))
async def callback_pickup(call: types.CallbackQuery):
    pickup = call.data.split(':')[1]
    user_id = call.from_user.id
    user_states[user_id]['pickup'] = pickup
    await call.answer()
    await confirm_post(user_id)


@dp.callback_query_handler(lambda c: c.data.startswith('confirm:'))
async def callback_confirm(call: types.CallbackQuery):
    user_id = call.from_user.id
    state = user_states.get(user_id)
    if call.data == 'confirm:no':
        await call.message.answer("Давай начнём заново. Отправь описание.")
        user_states[user_id] = {'step': 'description'}
        return
    await call.answer("Публикуем...")
    text = build_post_text(state)
    media = [types.InputMediaPhoto(photo, caption=text if i == 0 else "") for i, photo in enumerate(state['photos'])]
    sent = await bot.send_media_group(CHANNEL_ID, media)
    message_id = sent[0].message_id
    keyboard = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🗑 Удалить объявление", callback_data=f"delete:{message_id}")
    )
    await bot.send_message(user_id, "Готово! Объявление опубликовано.", reply_markup=keyboard)

    # Сохраняем в БД
    cursor.execute(f"""
        INSERT INTO bo2sale_posts 
        (user_id, username, description, photos, category, price, pickup, channel_message_id, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%s', 'now'))
    """, (
        user_id, state.get('username'), state['description'],
        ','.join(state['photos']), state['category'],
        state['price'], state['pickup'], message_id
    ))
    conn.commit()
    user_states.pop(user_id, None)


@dp.callback_query_handler(lambda c: c.data.startswith('delete:'))
async def delete_post(call: types.CallbackQuery):
    message_id = int(call.data.split(':')[1])
    try:
        await bot.delete_message(CHANNEL_ID, message_id)
        await call.message.edit_text("Объявление удалено.")
    except Exception:
        await call.message.answer("Не удалось удалить сообщение.")


async def auto_delete_old_posts():
    while True:
        await asyncio.sleep(3600 * 24)
        cursor.execute(f"""
            SELECT id, channel_message_id FROM bo2sale_posts
            WHERE strftime('%s','now') - timestamp > ?
        """, (AUTO_DELETE_DAYS * 86400,))
        rows = cursor.fetchall()
        for row in rows:
            try:
                await bot.delete_message(CHANNEL_ID, row[1])
            except:
                pass
            cursor.execute("DELETE FROM bo2sale_posts WHERE id = ?", (row[0],))
        conn.commit()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(auto_delete_old_posts())
    executor.start_polling(dp, skip_updates=True)
