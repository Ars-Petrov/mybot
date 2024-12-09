from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

startMenu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="📝 Список"),
            KeyboardButton(text="Инструмент"),
            KeyboardButton(text="🔎 Помощь")
        ]
    ],
    resize_keyboard=True
)

#menu = InlineKeyboardMarkup(inline_keyboard=menu)
#exit_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="◀️ Выйти в меню")]], resize_keyboard=True)
#iexit_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Выйти в меню", callback_data="menu")]])
