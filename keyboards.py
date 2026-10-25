from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

CATALOG = '🧰 Все инструменты'
MINE = '🙋 Мои инструменты'
ADD = '➕ Добавить инструмент'
SEARCH = '🔍 Найти инструмент'
HELP = '❓ Помощь'
CANCEL = 'Отмена'

menu = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text=CATALOG), KeyboardButton(text=MINE)],
    [KeyboardButton(text=ADD), KeyboardButton(text=SEARCH)],
    [KeyboardButton(text=HELP)],
], resize_keyboard=True)
cancel = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=CANCEL)]], resize_keyboard=True)


def inline(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


def card(tool, user_id):
    prefix = f"{tool['id']}:{tool['version']}"
    rows = []
    if tool['holder_id'] is None:
        rows.append([('🙋 Взять себе', f'take:{prefix}')])
    elif tool['holder_id'] == user_id:
        rows.append([('🤝 Передать', f'transfer:{prefix}'), ('📦 Вернуть', f'return:{prefix}')])
    if tool['holder_id'] is None or tool['holder_id'] == user_id:
        rows.append([('📍 Изменить место', f'location:{prefix}')])
    rows.append([('📜 История', f"history:{tool['id']}")])
    rows.append([('🔄 Обновить', f"tool:{tool['id']}")])
    return inline(rows)
