# config.py

# Telegram
BOT_TOKEN = xxx
CHANNEL_ID = -xxx  # ID канала, куда публикуются объявления
ALLOWED_CHAT_ID = -xxx  # ID чата, в котором должен состоять пользователь

# Базовая информация
MAX_DESCRIPTION_LENGTH = 4000
MAX_PHOTOS = 10
AUTO_DELETE_AFTER_SECONDS = 30 * 24 * 60 * 60  # 30 дней

# Категории товаров
CATEGORIES = [
    "📱 Электроника",
    "👗 Одежда",
    "📚 Книги",
    "🧸 Детские товары",
    "🔧 Ремонт",
    "📦 Прочее"
]

# Варианты получения
PICKUP_LOCATIONS = [
    "1.1", "1.2", "1.3", "1.4", "2.2"
]

# Префикс для базы данных
DB_PREFIX = "bo2sale"
