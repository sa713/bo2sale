import sqlite3
from config import DB_NAME, DB_PREFIX

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(f'''
        CREATE TABLE IF NOT EXISTS {DB_PREFIX}_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            pickup TEXT NOT NULL,
            price TEXT NOT NULL,
            photos TEXT,  -- Сохраняем file_id через запятую
            channel_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def save_post(user_id, username, description, category, pickup, price, photos):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(f'''
        INSERT INTO {DB_PREFIX}_posts 
        (user_id, username, description, category, pickup, price, photos)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, description, category, pickup, price, ','.join(photos)))
    post_id = c.lastrowid
    conn.commit()
    conn.close()
    return post_id

def set_channel_message_id(post_id, message_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(f'''
        UPDATE {DB_PREFIX}_posts
        SET channel_message_id = ?
        WHERE id = ?
    ''', (message_id, post_id))
    conn.commit()
    conn.close()

def get_post_by_channel_message_id(message_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(f'''
        SELECT * FROM {DB_PREFIX}_posts WHERE channel_message_id = ?
    ''', (message_id,))
    result = c.fetchone()
    conn.close()
    return result

def delete_post(post_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(f'''
        DELETE FROM {DB_PREFIX}_posts WHERE id = ?
    ''', (post_id,))
    conn.commit()
    conn.close()
