import sqlite3
import os

DB_PATH = "bo2sale.db"

def recreate_database():
    if os.path.exists(DB_PATH):
        print(f"Удаляю старую базу: {DB_PATH}")
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("Создаю таблицу bo2sale_posts...")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bo2sale_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            description TEXT,
            photos TEXT,
            category TEXT,
            price TEXT,
            location TEXT,
            confirmed INTEGER,
            message_id INTEGER,
            post_date INTEGER
        );
    """)

    conn.commit()
    conn.close()
    print("Готово. База данных создана.")

if __name__ == "__main__":
    recreate_database()
