#!/usr/bin/env python3
"""
init_db.py — инициализация базы данных для бота барахолки.

Создаёт файл базы данных и таблицу bo2sale_posts,
если они ещё не существуют. Данные в существующей базе
не трогаются.
"""

import sqlite3

try:
    # Предпочитаем имя БД из конфига
    from config import DB_NAME
except ImportError:
    # Запасной вариант
    DB_NAME = "bo2sale.db"


CREATE_TABLE_SQL = """
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
);
"""


def init_db(db_name: str = DB_NAME) -> None:
    """Создаёт базу данных и таблицу объявлений, если их ещё нет."""
    conn = sqlite3.connect(db_name)
    try:
        cursor = conn.cursor()
        cursor.execute(CREATE_TABLE_SQL)
        conn.commit()
        print(f"[OK] База данных '{db_name}' и таблица 'bo2sale_posts' готовы.")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
