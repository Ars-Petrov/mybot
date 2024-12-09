import logging
import sys
import time
import aiogram
import asyncio

from config import TOKEN
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message
import handlers
import keyboards

bot = Bot(token=TOKEN)
dp = Dispatcher(bot=bot)


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    logging.info(f'{user_id=} {user_name=} {time.asctime()}')
    await message.answer(f"Hello, {user_name}!", reply_markup=keyboards.startMenu)


async def main():
    # Initialize Bot instance with default bot properties which will be passed to all API calls
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp.include_router(handlers.router)
    # And the run events dispatching
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
