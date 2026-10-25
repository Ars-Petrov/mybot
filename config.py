import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    family_ids: frozenset[int]
    database_path: str


def load_settings():
    load_dotenv()
    token = os.getenv('BOT_TOKEN', '').strip()
    if not token:
        raise ValueError('Укажите BOT_TOKEN в .env')
    try:
        family_ids = frozenset(int(value.strip()) for value in os.getenv('FAMILY_IDS', '').split(',') if value.strip())
    except ValueError:
        raise ValueError('FAMILY_IDS: укажите числовые Telegram ID через запятую') from None
    if any(value <= 0 for value in family_ids):
        raise ValueError('FAMILY_IDS: ID должны быть положительными числами')
    return Settings(token, family_ids, os.getenv('DATABASE_PATH', 'data/tools.sqlite3'))
