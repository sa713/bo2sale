from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import pickup_locations, categories

def category_keyboard():
    buttons = [
        [InlineKeyboardButton(text=category, callback_data=f"category:{category}")]
        for category in categories
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def pickup_keyboard():
    buttons = [
        [InlineKeyboardButton(text=location, callback_data=f"pickup:{location}")]
        for location in pickup_locations
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
