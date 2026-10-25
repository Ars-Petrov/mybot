"""Persistent notification outbox. A Telegram outage does not roll back a take."""
import asyncio
import logging
from html import escape

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter


class Notifier:
    def __init__(self, inventory, family_ids):
        self.inventory = inventory
        self.inventory.seed_access(family_ids)
        self.wake = asyncio.Event()

    async def deliver(self, bot):
        for item in self.inventory.pending_notifications():
            if item['recipient_id'] not in self.inventory.allowed_ids():
                self.inventory.notification_done(item['id'])
                continue
            try:
                await bot.send_message(
                    item['recipient_id'],
                    f"🧰 {escape(item['taker_name'])} взял(а) инструмент «{escape(item['tool_name'])}», "
                    'который числился у вас.\nТеперь он в списке «Мои инструменты» у этого человека.',
                    parse_mode='HTML', request_timeout=15,
                )
            except TelegramRetryAfter as error:
                self.inventory.notification_retry(item['id'], max(error.retry_after, 1))
                # Respect Telegram's flood limit for the whole bot.
                break
            except (TelegramAPIError, OSError, asyncio.TimeoutError) as error:
                delay = 3600 if isinstance(error, TelegramForbiddenError) else min(30 * 2 ** min(item['attempts'], 10), 21600)
                self.inventory.notification_retry(item['id'], delay)
                logging.warning('Notification %s pending after %s', item['id'], type(error).__name__)
            else:
                self.inventory.notification_done(item['id'])

    async def run(self, bot):
        while True:
            self.wake.clear()
            await self.deliver(bot)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
