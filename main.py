import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import SimpleEventIsolation

from config import load_settings
from handlers import create_router
from storage import Inventory


async def main():
    settings = load_settings()
    inventory = Inventory(settings.database_path)
    dispatcher = Dispatcher(events_isolation=SimpleEventIsolation())
    dispatcher.include_router(create_router(inventory, settings.family_ids))
    async with Bot(settings.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) as bot:
        await dispatcher.start_polling(bot, close_bot_session=False)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
