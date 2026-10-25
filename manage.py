"""Print a participant invitation locally, including before anyone has joined."""
import argparse
import asyncio

from aiogram import Bot

from config import load_settings
from storage import Inventory


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['invite', 'newinvite'])
    args = parser.parse_args()
    settings = load_settings()
    inventory = Inventory(settings.database_path)
    async with Bot(settings.token) as bot:
        user = await bot.get_me()
    token = inventory.invite_token(rotate=args.command == 'newinvite')
    print(f'https://t.me/{user.username}?start=join_{token}')


if __name__ == '__main__':
    asyncio.run(main())
