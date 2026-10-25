import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, Update, User

import keyboards as kb
from handlers import create_router
from storage import Inventory


class TelegramStub(BaseSession):
    """Exercise real aiogram dispatch without sending any Telegram requests."""
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if method.__api_method__ == 'answerCallbackQuery':
            return True
        return Message(message_id=len(self.sent), date=datetime.now(timezone.utc),
                       chat=Chat(id=method.chat_id, type='private'), text=getattr(method, 'text', None))

    async def stream_content(self, *args, **kwargs):
        yield b''


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Inventory(Path(self.tmp.name) / 'tools.sqlite3')
        self.session = TelegramStub()
        self.bot = Bot('123456:TEST_TOKEN', session=self.session)
        self.dp = Dispatcher()
        self.dp.include_router(create_router(self.db, frozenset({1, 2})))
        self.sequence = 0

    async def asyncTearDown(self):
        await self.dp.storage.close()
        await self.bot.session.close()

    async def send(self, value=None, user_id=1, callback=None, chat_type='private', photo=False):
        self.sequence += 1
        user = {'id': user_id, 'is_bot': False, 'first_name': f'Член семьи {user_id}'}
        message = {'message_id': self.sequence, 'date': 1700000000,
                   'chat': {'id': user_id, 'type': chat_type}, 'from': user}
        if photo:
            message['photo'] = [{'file_id': 'photo-test', 'file_unique_id': 'unique', 'width': 50, 'height': 50}]
        elif value is not None:
            message['text'] = value
        payload = {'update_id': self.sequence, 'message': message}
        if callback:
            payload = {'update_id': self.sequence, 'callback_query': {
                'id': str(self.sequence), 'from': user, 'chat_instance': 'test', 'data': callback,
                'message': message}}
        await self.dp.feed_update(self.bot, Update.model_validate(payload))

    def output(self):
        return '\n'.join(getattr(method, 'text', '') or '' for method in self.session.sent)

    async def add_tool(self, photo=False):
        for value in [kb.ADD, '<Перфоратор>', '-', 'Гараж']:
            await self.send(value)
        await self.send(None if photo else '-', photo=photo)

    async def test_full_family_workflow_and_mine(self):
        await self.send('/start', user_id=2)
        await self.add_tool()
        self.assertIn('&lt;Перфоратор&gt;', self.output())
        await self.send(callback='take:1:0')
        await self.send(callback='transfer:1:1')
        self.assertIn('Кому передать?', self.output())
        await self.send(callback='give:1:1:2')
        self.session.sent.clear()
        await self.send(kb.MINE)
        self.assertIn('пусто', self.output())
        await self.send(kb.MINE, user_id=2)
        self.assertIn('Мои инструменты</b> · 1', self.output())
        await self.send(callback='location:1:2', user_id=2)
        await self.send('Дача', user_id=2)
        self.assertEqual(self.db.get(1)['location'], 'Дача')
        await self.send(callback='return:1:3', user_id=2)
        self.assertEqual(self.db.get(1)['location'], 'Гараж')
        await self.send(callback='history:1', user_id=2)
        self.assertIn('Возвращён', self.output())

    async def test_photo_and_text_validation_cancel(self):
        await self.send(kb.ADD)
        await self.send(photo=True)
        self.assertEqual(self.db.listing()[1], 0)
        self.assertIn('Введите название', self.output())
        await self.send('/cancel')
        await self.add_tool(photo=True)
        self.assertEqual(self.db.get(1)['photo'], 'photo-test')
        self.assertTrue(any(method.__api_method__ == 'sendPhoto' for method in self.session.sent))

    async def test_access_denied_for_messages_and_buttons_and_groups(self):
        await self.send('/id', user_id=99)
        self.assertIn('<code>99</code>', self.output())
        await self.send(kb.CATALOG, user_id=99)
        await self.send(callback='take:1:0', user_id=99)
        self.assertIn('Нет доступа', self.output())
        self.assertEqual(self.db.members({99}), [])
        count = len(self.session.sent)
        await self.send(kb.CATALOG, chat_type='group')
        self.assertEqual(len(self.session.sent), count)

    async def test_stale_callback_cannot_take_twice(self):
        await self.add_tool()
        await self.send(callback='take:1:0')
        await self.send(callback='take:1:0', user_id=2)
        self.assertIn('Карточка устарела', self.output())
        self.assertEqual(self.db.get(1)['holder_id'], 1)
        await self.send(callback='give:1:1:99')
        self.assertIn('не входит', self.output())

    async def test_search_pagination_and_expired_search(self):
        await self.send('/start')
        for i in range(10):
            self.db.add(f'Дрель {i}', '', 'Дом', None, 1)
        await self.send(kb.SEARCH)
        await self.send('ДРЕЛЬ')
        await self.send(callback='list:search:1')
        self.assertIn('страница 2', self.output())
        await self.send('/cancel')
        await self.send(callback='list:search:1')
        self.assertIn('Поиск устарел', self.output())


if __name__ == '__main__':
    unittest.main()
