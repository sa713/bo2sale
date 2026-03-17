import asyncio
import fcntl
import logging
import os
import socket
import sqlite3
from contextlib import suppress
from datetime import datetime, timedelta
from html import escape
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.markdown import hlink

from config import (
    ALLOWED_CHAT_ID,
    API_TOKEN,
    AUTO_DELETE_DAYS,
    CATEGORIES,
    CHANNEL_ID,
    DB_NAME,
    HELP_TEXT,
    PICKUP_LOCATIONS,
    RULES_TEXT,
)
from sqlite_storage import SQLiteStorage


LOG_FILE = os.path.join(os.path.dirname(__file__), "bot.log")
LOCK_FILE = os.path.join(os.path.dirname(__file__), ".bot.lock")


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("bo2sale")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False

    return logger


logger = configure_logging()


_instance_lock_handle = None


def acquire_instance_lock() -> None:
    global _instance_lock_handle

    _instance_lock_handle = open(LOCK_FILE, "w", encoding="utf-8")
    try:
        fcntl.flock(_instance_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("Bot already running: lock is held by another process") from exc

    _instance_lock_handle.write(str(os.getpid()))
    _instance_lock_handle.flush()


conn = sqlite3.connect(DB_NAME, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
db_lock = asyncio.Lock()


def ensure_posts_schema() -> None:
    conn.execute(
        """
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
            message_ids TEXT,
            status TEXT NOT NULL DEFAULT 'published'
        )
        """
    )

    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(bo2sale_posts)").fetchall()
    }

    if "message_ids" not in columns:
        conn.execute("ALTER TABLE bo2sale_posts ADD COLUMN message_ids TEXT")

    if "status" not in columns:
        conn.execute("ALTER TABLE bo2sale_posts ADD COLUMN status TEXT NOT NULL DEFAULT 'published'")

    conn.execute(
        """
        UPDATE bo2sale_posts
        SET message_ids = CAST(message_id AS TEXT)
        WHERE (message_ids IS NULL OR message_ids = '')
          AND message_id IS NOT NULL
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_posts_user_status
        ON bo2sale_posts(user_id, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_posts_status_date
        ON bo2sale_posts(status, post_date)
        """
    )

    conn.commit()


ensure_posts_schema()


bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    session=AiohttpSession(),
)
# У части VPS нестабилен IPv6-маршрут до api.telegram.org, поэтому фиксируем IPv4.
bot.session._connector_init["family"] = socket.AF_INET
bot.session._connector_init["ttl_dns_cache"] = 300
dp = Dispatcher(storage=SQLiteStorage(DB_NAME))


class PostFSM(StatesGroup):
    description = State()
    photos = State()
    category = State()
    price = State()
    pickup = State()
    confirm = State()


def build_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать объявление", callback_data="create_post")],
            [InlineKeyboardButton(text="Мои объявления", callback_data="my_posts")],
            [InlineKeyboardButton(text="Правила", callback_data="rules")],
        ]
    )


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return escape(str(value), quote=False)


def category_hashtag(value: str) -> str:
    raw = (value or "прочее").strip().replace(" ", "_").lower()
    normalized = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    normalized = normalized.strip("_") or "прочее"
    return f"#{normalized}"


def format_post(post: Dict[str, Any]) -> str:
    username = safe_text(post.get("username"))
    full_name = safe_text(post.get("full_name") or "Пользователь")

    if username:
        author = f"@{username}"
    else:
        author = hlink(full_name, f"tg://user?id={post['user_id']}")

    return (
        f"{safe_text(post.get('description'))}\n\n"
        f"{category_hashtag(str(post.get('category', 'прочее')))}\n\n"
        f"{safe_text(post.get('price'))}\n\n"
        f"{safe_text(post.get('pickup'))}\n"
        f"{author}"
    )


def parse_message_ids(message_id: Optional[int], message_ids_str: Optional[str]) -> List[int]:
    if message_ids_str:
        parsed = []
        for item in message_ids_str.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                parsed.append(int(item))
            except ValueError:
                logger.warning("Invalid message id in DB: %s", item)
        if parsed:
            return parsed

    if message_id:
        return [int(message_id)]

    return []


async def check_membership(user_id: int) -> bool:
    for attempt in range(1, 4):
        try:
            member = await bot.get_chat_member(ALLOWED_CHAT_ID, user_id)
            return member.status not in ("left", "kicked")
        except TelegramNetworkError:
            logger.exception(
                "Сетевая ошибка при проверке участия в чате (попытка %s/3)",
                attempt,
            )
            if attempt < 3:
                await asyncio.sleep(0.6 * attempt)
                continue
            raise
        except Exception:
            logger.exception("Ошибка при проверке участия в чате")
            return False

    return False


async def insert_pending_post(data: Dict[str, Any], post_date: str) -> int:
    async with db_lock:
        cursor = conn.execute(
            """
            INSERT INTO bo2sale_posts (
                user_id, username, full_name,
                description, price, pickup, category,
                photo_ids, post_date,
                message_id, message_ids, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["user_id"],
                data.get("username"),
                data.get("full_name"),
                data["description"],
                data["price"],
                data["pickup"],
                data["category"],
                ",".join(data.get("photo_ids", [])),
                post_date,
                None,
                None,
                "pending",
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


async def mark_post_published(post_id: int, message_ids: List[int]) -> None:
    async with db_lock:
        conn.execute(
            """
            UPDATE bo2sale_posts
            SET message_id = ?, message_ids = ?, status = 'published'
            WHERE id = ?
            """,
            (message_ids[0], ",".join(str(mid) for mid in message_ids), post_id),
        )
        conn.commit()


async def mark_post_failed(post_id: int) -> None:
    async with db_lock:
        conn.execute(
            "UPDATE bo2sale_posts SET status = 'failed' WHERE id = ?",
            (post_id,),
        )
        conn.commit()


def has_complete_post_data(data: Dict[str, Any]) -> bool:
    required = ["user_id", "description", "category", "price", "pickup", "full_name"]
    for key in required:
        if not data.get(key):
            return False

    photos = data.get("photo_ids")
    if photos is None:
        return False

    return isinstance(photos, list)


@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! 👋\n\n"
        "Я помогу тебе разместить объявление в барахолке.\n"
        "Нажми «Создать объявление», чтобы начать.",
        reply_markup=build_main_menu(),
    )


@dp.callback_query(F.data == "help")
async def callback_help(call: types.CallbackQuery) -> None:
    await call.message.edit_text(HELP_TEXT, disable_web_page_preview=True)


@dp.callback_query(F.data == "rules")
async def callback_rules(call: types.CallbackQuery) -> None:
    await call.message.edit_text(RULES_TEXT, disable_web_page_preview=True)


@dp.callback_query(F.data == "create_post")
async def callback_create_post(call: types.CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    try:
        is_member = await check_membership(user_id)
    except TelegramNetworkError:
        await call.message.answer(
            "Сейчас проблемы с соединением к Telegram API.\n"
            "Попробуй ещё раз через минуту."
        )
        return

    if not is_member:
        await call.message.answer(
            "Похоже, ты не состоишь в чате жильцов.\n\n"
            "Вступи в чат, потом возвращайся ко мне 😊"
        )
        return

    await state.set_state(PostFSM.description)
    await state.set_data(
        {
            "user_id": user_id,
            "username": call.from_user.username,
            "full_name": call.from_user.full_name,
            "photo_ids": [],
        }
    )
    await call.message.answer("Отправь текст объявления (до 4000 символов).")


@dp.message(PostFSM.description)
async def process_description(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Нужен текст объявления. Отправь описание текстом.")
        return

    if len(message.text) > 4000:
        await message.answer("Слишком длинное описание. Попробуй сократить до 4000 символов.")
        return

    await state.update_data(description=message.text)
    await state.set_state(PostFSM.photos)
    await message.answer(
        "Теперь отправь до 10 фотографий товара (можно одним альбомом).\n"
        "Если фото не нужны — отправь «Пропустить».\n"
        "После добавления фотографий напиши «Дальше»."
    )


@dp.message(PostFSM.photos, F.photo)
async def process_photos(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos = data.get("photo_ids", [])

    if len(photos) >= 10:
        await message.answer("Достигнут лимит в 10 фотографий. Переходим дальше.")
        await ask_category(message, state)
        return

    photos.append(message.photo[-1].file_id)
    await state.update_data(photo_ids=photos)


@dp.message(PostFSM.photos, F.text.casefold() == "пропустить")
async def skip_photos(message: types.Message, state: FSMContext) -> None:
    await state.update_data(photo_ids=[])
    await ask_category(message, state)


@dp.message(PostFSM.photos, F.text.casefold() == "дальше")
async def process_photos_next(message: types.Message, state: FSMContext) -> None:
    await ask_category(message, state)


@dp.message(PostFSM.photos)
async def process_photos_text(message: types.Message) -> None:
    await message.answer("Отправь фото, «Пропустить» или «Дальше».")


async def ask_category(message: types.Message, state: FSMContext) -> None:
    await state.set_state(PostFSM.category)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=cat, callback_data=f"cat:{cat}")]
            for cat in CATEGORIES
        ]
    )
    await message.answer("Выбери категорию:", reply_markup=kb)


@dp.callback_query(F.data.startswith("cat:"))
async def select_category(call: types.CallbackQuery, state: FSMContext) -> None:
    category = call.data.split(":", 1)[1]

    if category not in CATEGORIES:
        await call.answer("Некорректная категория", show_alert=True)
        return

    await state.update_data(category=category)
    await state.set_state(PostFSM.price)
    await call.message.edit_text("Укажи цену (например, «1,500» или «За шоколадку»).")


@dp.message(PostFSM.price)
async def process_price(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Укажи цену текстом.")
        return

    await state.update_data(price=message.text)
    await state.set_state(PostFSM.pickup)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=loc, callback_data=f"pickup:{loc}")]
            for loc in PICKUP_LOCATIONS
        ]
    )
    await message.answer("Где забирать товар?", reply_markup=kb)


@dp.callback_query(F.data.startswith("pickup:"))
async def select_pickup(call: types.CallbackQuery, state: FSMContext) -> None:
    pickup = call.data.split(":", 1)[1]

    if pickup not in PICKUP_LOCATIONS:
        await call.answer("Некорректное место", show_alert=True)
        return

    await state.update_data(pickup=pickup)
    data = await state.get_data()

    if not has_complete_post_data(data):
        await state.clear()
        await call.message.answer("Сессия устарела. Начни заново через /start.")
        return

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
    if photos:
        media_group = [types.InputMediaPhoto(media=pid) for pid in photos]
        await call.message.answer_media_group(media_group)

    await call.message.answer(post_text, reply_markup=kb)
    await state.set_state(PostFSM.confirm)


@dp.callback_query(F.data == "publish")
async def publish_post(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not has_complete_post_data(data):
        await state.clear()
        await call.message.answer("Что-то пошло не так. Попробуй заново через /start.")
        return

    sent_message_ids: List[int] = []
    post_id: Optional[int] = None

    try:
        post_date = datetime.now().strftime("%Y-%m-%d")
        post_id = await insert_pending_post(data, post_date)

        post_text = format_post(data)
        media_group = [types.InputMediaPhoto(media=photo_id) for photo_id in data.get("photo_ids", [])]

        if media_group:
            media_group[0].caption = post_text
            media_group[0].parse_mode = ParseMode.HTML
            sent_messages = await bot.send_media_group(CHANNEL_ID, media_group)
            sent_message_ids = [m.message_id for m in sent_messages]
        else:
            sent = await bot.send_message(CHANNEL_ID, post_text)
            sent_message_ids = [sent.message_id]

        await mark_post_published(post_id, sent_message_ids)

        await bot.send_message(call.from_user.id, "✅ Объявление опубликовано!")
        await bot.send_message(
            call.from_user.id,
            "Хочешь создать ещё одно объявление? 👇",
            reply_markup=build_main_menu(),
        )

        await state.clear()

    except Exception:
        logger.exception("Ошибка при публикации")

        for message_id in sent_message_ids:
            try:
                await bot.delete_message(CHANNEL_ID, message_id)
            except Exception:
                logger.exception("Не удалось откатить сообщение %s", message_id)

        if post_id is not None:
            await mark_post_failed(post_id)

        await bot.send_message(
            call.from_user.id, "Произошла ошибка при публикации. Попробуй ещё раз."
        )


@dp.callback_query(F.data == "cancel")
async def cancel_post(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.message.answer("Объявление отменено.")
    await state.clear()


@dp.callback_query(F.data == "my_posts")
async def my_posts(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id

    async with db_lock:
        rows = conn.execute(
            """
            SELECT id, description, message_id
            FROM bo2sale_posts
            WHERE user_id = ? AND status = 'published'
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()

    if not rows:
        await call.message.answer("У тебя пока нет объявлений.")
        return

    channel_suffix = str(CHANNEL_ID)
    if channel_suffix.startswith("-100"):
        channel_suffix = channel_suffix[4:]

    for post_id, desc, message_id in rows:
        channel_link = f"https://t.me/c/{channel_suffix}/{message_id}"
        snippet = (desc or "")[:100]
        if len(desc or "") > 100:
            snippet += "..."

        text = (
            f"{safe_text(snippet)}\n\n"
            f"{hlink('🔗 Открыть объявление', channel_link)}"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Удалить", callback_data=f"delete:{post_id}")]
            ]
        )
        await call.message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@dp.callback_query(F.data.startswith("delete:"))
async def delete_post(call: types.CallbackQuery) -> None:
    try:
        post_id = int(call.data.split(":", 1)[1])
    except ValueError:
        await call.answer("Некорректный идентификатор", show_alert=True)
        return

    user_id = call.from_user.id

    async with db_lock:
        row = conn.execute(
            """
            SELECT message_id, message_ids
            FROM bo2sale_posts
            WHERE id = ? AND user_id = ? AND status = 'published'
            """,
            (post_id, user_id),
        ).fetchone()

    if not row:
        await call.message.answer("Объявление не найдено или у тебя нет прав на удаление.")
        return

    single_message_id, message_ids_str = row
    message_ids = parse_message_ids(single_message_id, message_ids_str)

    for message_id in message_ids:
        try:
            await bot.delete_message(CHANNEL_ID, message_id)
        except Exception:
            logger.exception("Не смог удалить сообщение %s", message_id)

    async with db_lock:
        conn.execute(
            "DELETE FROM bo2sale_posts WHERE id = ? AND user_id = ?",
            (post_id, user_id),
        )
        conn.commit()

    await call.message.edit_text("Объявление удалено.")


async def auto_delete_old() -> None:
    while True:
        try:
            threshold = (datetime.now() - timedelta(days=AUTO_DELETE_DAYS)).strftime("%Y-%m-%d")

            async with db_lock:
                rows = conn.execute(
                    """
                    SELECT id, message_id, message_ids
                    FROM bo2sale_posts
                    WHERE post_date < ? AND status = 'published'
                    """,
                    (threshold,),
                ).fetchall()

            for post_id, message_id, message_ids_str in rows:
                message_ids = parse_message_ids(message_id, message_ids_str)

                for mid in message_ids:
                    try:
                        await bot.delete_message(CHANNEL_ID, mid)
                    except Exception:
                        logger.exception("Не смог удалить сообщение %s", mid)

                async with db_lock:
                    conn.execute("DELETE FROM bo2sale_posts WHERE id = ?", (post_id,))
                    conn.commit()

        except Exception:
            logger.exception("Ошибка в автоудалении старых объявлений")

        await asyncio.sleep(86400)


async def main() -> None:
    acquire_instance_lock()

    cleanup_task = asyncio.create_task(auto_delete_old())
    try:
        await dp.start_polling(bot)
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task

        await dp.storage.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
