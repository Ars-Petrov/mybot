import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage

from notifications import Notifier
from storage import Inventory


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_failure_retries_after_restart_without_rolling_back_take(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tools.sqlite3'
            db = Inventory(path)
            db.register(1, 'Арсений')
            db.register(2, 'Иван')
            tool = db.add('Дрель', '', 'Дом', None, 1)
            db.move(tool, 0, 1, 'take')
            db.move(tool, 1, 2, 'take')
            bot = AsyncMock()
            bot.send_message.side_effect = TelegramNetworkError(method=SendMessage(chat_id=1, text=''), message='offline')
            await Notifier(db, {1, 2}).deliver(bot)
            self.assertEqual(db.get(tool)['holder_id'], 2)
            self.assertEqual(db.pending_notifications(), [])  # Backoff, not deleted.
            with sqlite3.connect(path) as connection:
                row = connection.execute('SELECT attempts, delivered FROM notifications').fetchone()
                self.assertEqual(row, (1, 0))
                connection.execute('UPDATE notifications SET next_attempt=0')
            bot.send_message.side_effect = None
            reloaded = Inventory(path)
            await Notifier(reloaded, {1, 2}).deliver(bot)
            self.assertEqual(bot.send_message.await_count, 2)
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute('SELECT delivered FROM notifications').fetchone()[0], 1)

    async def test_removed_family_member_gets_no_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Inventory(Path(directory) / 'tools.sqlite3')
            db.register(1, 'Бывший участник')
            db.register(2, 'Иван')
            tool = db.add('Дрель', '', 'Дом', None, 1)
            db.move(tool, 0, 1, 'take')
            db.move(tool, 1, 2, 'take')
            bot = AsyncMock()
            await Notifier(db, {2}).deliver(bot)
            bot.send_message.assert_not_awaited()
