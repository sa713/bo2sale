#!/usr/bin/env python3
"""
init_db.py — инициализация базы данных для бота барахолки.

Создаёт файл базы данных и таблицы bo2sale_posts/fsm_states,
если они ещё не существуют. Данные в существующей базе не теряются.
"""

import sqlite3

try:
    from config import DB_NAME
except ImportError:
    DB_NAME = "bo2sale.db"


CREATE_POSTS_TABLE_SQL = """
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
);
"""

CREATE_FSM_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fsm_states (
    bot_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL DEFAULT 0,
    destiny TEXT NOT NULL,
    state TEXT,
    data TEXT,
    PRIMARY KEY (bot_id, chat_id, user_id, thread_id, destiny)
);
"""


def init_db(db_name: str = DB_NAME) -> None:
    conn = sqlite3.connect(db_name)
    try:
        cursor = conn.cursor()
        cursor.execute(CREATE_POSTS_TABLE_SQL)
        cursor.execute(CREATE_FSM_TABLE_SQL)

        columns = {
            row[1]
            for row in cursor.execute("PRAGMA table_info(bo2sale_posts)").fetchall()
        }

        if "message_ids" not in columns:
            cursor.execute("ALTER TABLE bo2sale_posts ADD COLUMN message_ids TEXT")

        if "status" not in columns:
            cursor.execute(
                "ALTER TABLE bo2sale_posts ADD COLUMN status TEXT NOT NULL DEFAULT 'published'"
            )

        cursor.execute(
            """
            UPDATE bo2sale_posts
            SET message_ids = CAST(message_id AS TEXT)
            WHERE (message_ids IS NULL OR message_ids = '')
              AND message_id IS NOT NULL
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_posts_user_status
            ON bo2sale_posts(user_id, status)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_posts_status_date
            ON bo2sale_posts(status, post_date)
            """
        )

        conn.commit()
        print(f"[OK] База данных '{db_name}' и таблицы готовы.")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
