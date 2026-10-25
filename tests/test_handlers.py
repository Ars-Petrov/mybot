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
from notifications import Notifier


class TelegramStub(BaseSession):
    """Exercise real aiogram dispatch without sending any Telegram requests."""
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if method.__api_method__ == 'getMe':
            return User(id=123456, is_bot=True, first_name='Tools', username='whereIsMyToolsBot')
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
        self.notifier = Notifier(self.db, frozenset({1, 2}))
        self.dp.include_router(create_router(self.db, frozenset({1, 2}), self.notifier))
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

    async def test_relative_takes_and_previous_holder_is_notified(self):
        await self.add_tool()
        await self.send(callback='take:1:0')
        await self.send(callback='tool:1', user_id=2)
        card = self.session.sent[-1].reply_markup
        self.assertTrue(any(b.callback_data == 'take:1:1' for row in card.inline_keyboard for b in row))
        await self.send(callback='take:1:1', user_id=2)
        self.assertEqual(self.db.get(1)['holder_id'], 2)
        self.assertTrue(self.notifier.wake.is_set())
        await self.notifier.deliver(self.bot)
        notification = self.session.sent[-1]
        self.assertEqual(notification.chat_id, 1)
        self.assertIn('Член семьи 2', notification.text)
        self.assertIn('&lt;Перфоратор&gt;', notification.text)
        self.assertEqual(self.db.pending_notifications(), [])

    async def test_any_member_adds_and_deletes_with_confirmation(self):
        await self.add_tool()
        await self.send(callback='take:1:0')
        await self.send(callback='delete:1:1', user_id=2)
        self.assertIn('Удалить', self.output())
        self.assertIsNotNone(self.db.get(1))
        await self.send(callback='tool:1', user_id=2)  # Cancel confirmation.
        self.assertIsNotNone(self.db.get(1))
        await self.send(callback='delete_confirm:1:1', user_id=2)
        self.assertIsNone(self.db.get(1))
        await self.send(callback='take:1:1')
        self.assertIn('Карточка устарела', self.output())
        for value in [kb.ADD, 'Молоток', '-', 'Дача', '-']:
            await self.send(value, user_id=2)
        self.assertEqual(self.db.listing()[1], 1)

    async def test_invite_join_registers_automatically_and_can_receive_tools(self):
        await self.send('/invite')
        invite = self.output()
        self.assertIn('https://t.me/whereIsMyToolsBot?start=join_', invite)
        token = self.db.invite_token()
        await self.send('/start join_' + token, user_id=3)
        self.assertIn(3, self.db.allowed_ids())
        self.assertEqual(self.db.members({3})[0]['name'], 'Член семьи 3')
        await self.add_tool()
        await self.send(callback='take:1:0')
        await self.send(callback='give:1:1:3')
        self.assertEqual(self.db.get(1)['holder_id'], 3)
        await self.send(callback='take:1:2', user_id=2)
        await self.notifier.deliver(self.bot)
        self.assertEqual(self.session.sent[-1].chat_id, 3)
        await self.send(kb.MINE, user_id=3)
        self.assertIn('Мои инструменты: пусто', self.output())

    async def test_unknown_start_bad_invite_and_group_join_are_denied(self):
        await self.send('/start', user_id=3)
        self.assertNotIn(3, self.db.allowed_ids())
        await self.send('/start join_wrong', user_id=3)
        self.assertIn('недействительно', self.output())
        token = self.db.invite_token()
        await self.send('/start join_' + token, user_id=3, chat_type='group')
        self.assertNotIn(3, self.db.allowed_ids())
        await self.send('/invite', user_id=3)
        self.assertNotIn('https://t.me/', self.session.sent[-1].text)
        await self.send(callback='take:1:0', user_id=3)
        self.assertIn('Нет доступа', self.output())

    async def test_rotation_rejects_old_link_preserves_members_and_button_works(self):
        await self.send(kb.INVITE)
        token = self.db.invite_token()
        await self.send('/start join_' + token, user_id=3)
        await self.send('/newinvite')
        new_token = self.db.invite_token()
        self.assertNotEqual(token, new_token)
        await self.send('/start join_' + token, user_id=4)
        self.assertNotIn(4, self.db.allowed_ids())
        await self.send('/start join_' + new_token, user_id=4)
        self.assertIn(4, self.db.allowed_ids())
        await self.send('/start', user_id=3)
        self.assertIn('Выберите действие', self.session.sent[-1].text)

    async def test_add_tool_by_selecting_existing_location(self):
        for value in [kb.ADD, 'Пила', '-']:
            await self.send(value)
        await self.send(callback='loc:home:0:0:1')
        await self.send('-')
        self.assertEqual(self.db.get(1)['home'], 'Дом')
        self.assertEqual(self.db.get(1)['location'], 'Дом')
        await self.send(callback='loc:home:0:0:2')
        self.assertIn('Выбор места устарел', self.output())

    async def test_add_location_during_tool_creation(self):
        for value in [kb.ADD, 'Пила', '-']:
            await self.send(value)
        await self.send(callback='newplace:home:0:0')
        await self.send('Склад <А>')
        await self.send('-')
        tool = self.db.get(1)
        self.assertEqual(tool['name'], 'Пила')
        self.assertEqual(tool['home'], 'Склад <А>')
        self.assertIn('Склад &lt;А&gt;', self.output())
        self.assertEqual(self.db.locations()[1], 4)

    async def test_all_participants_add_locations_and_put_tool(self):
        await self.send(kb.ADD_LOCATION, user_id=2)
        await self.send('Мастерская', user_id=2)
        place = self.db.add_location('Мастерская')
        await self.add_tool()
        await self.send(callback='take:1:0')
        await self.send(callback='put:1:1')
        self.assertIn('Куда положить', self.output())
        await self.send(callback=f"loc:put:1:1:{place['id']}")
        self.assertIsNone(self.db.get(1)['holder_id'])
        self.assertEqual(self.db.get(1)['location'], 'Мастерская')
        await self.send(kb.MINE)
        self.assertIn('Мои инструменты: пусто', self.session.sent[-1].text)
        await self.send(kb.LOCATIONS, user_id=2)
        await self.send(callback=f"loc:browse:0:0:{place['id']}", user_id=2)
        self.assertIn('инструментов: 1', self.session.sent[-1].text)
        await self.send(callback='loc:put:1:1:1')
        self.assertIn('Карточка устарела', self.session.sent[-1].text)

    async def test_new_location_during_put_and_cancel(self):
        await self.add_tool()
        await self.send(callback='take:1:0')
        await self.send(callback='newplace:put:1:1')
        await self.send('/cancel')
        self.assertEqual(self.db.get(1)['holder_id'], 1)
        await self.send(callback='newplace:put:1:1')
        await self.send('Подвал')
        self.assertIsNone(self.db.get(1)['holder_id'])
        self.assertEqual(self.db.get(1)['location'], 'Подвал')
        self.assertEqual(self.db.get(1)['home'], 'Гараж')

    async def test_location_pagination_access_and_neutral_labels(self):
        for index in range(10):
            self.db.add_location(f'Локация {index}')
        await self.send(kb.LOCATIONS)
        await self.send(callback='places:browse:0:0:1')
        self.assertIn('страница 2', self.session.sent[-1].text)
        await self.send(callback='newplace:browse:0:0', user_id=99)
        self.assertIn('Нет доступа', self.session.sent[-1].text)
        await self.send('/start')
        self.assertIn('Поиск инструмента', self.session.sent[-1].text)
        await self.send('/help')
        await self.send(kb.INVITE)
        bodies = self.output().casefold()
        for word in ['семей', 'семь', 'родствен']:
            self.assertNotIn(word, bodies)

    async def test_other_participant_edits_name_description_and_photo(self):
        await self.add_tool(photo=True)
        await self.send(callback='take:1:0')
        await self.send(callback='edit:1:1', user_id=2)
        self.assertIn('Что изменить', self.session.sent[-1].text)
        await self.send(callback='editfield:1:1:name', user_id=2)
        await self.send('Новое <имя>', user_id=2)
        self.assertEqual(self.db.get(1)['name'], 'Новое <имя>')
        self.assertIn('Новое &lt;имя&gt;', self.session.sent[-1].text)
        await self.send(callback='editfield:1:2:description', user_id=2)
        await self.send('Описание модели', user_id=2)
        self.assertEqual(self.db.get(1)['description'], 'Описание модели')
        await self.send(callback='editfield:1:3:description', user_id=2)
        await self.send('-', user_id=2)
        self.assertEqual(self.db.get(1)['description'], '')
        await self.send(callback='editfield:1:4:photo', user_id=2)
        await self.send('не фото', user_id=2)
        self.assertEqual(self.db.get(1)['version'], 4)
        await self.send(photo=True, user_id=2)
        await self.send(callback='editfield:1:5:photo', user_id=2)
        await self.send('-', user_id=2)
        self.assertIsNone(self.db.get(1)['photo'])
        self.assertEqual(self.db.get(1)['holder_id'], 1)

    async def test_edit_return_place_existing_and_new_does_not_move_tool(self):
        await self.add_tool()
        await self.send(callback='editfield:1:0:home', user_id=2)
        await self.send(callback='loc:edithome:1:0:2', user_id=2)
        self.assertEqual(self.db.get(1)['home'], 'Дача')
        self.assertEqual(self.db.get(1)['location'], 'Гараж')
        await self.send(callback='editfield:1:1:home')
        await self.send(callback='newplace:edithome:1:1')
        await self.send('Мастерская')
        self.assertEqual(self.db.get(1)['home'], 'Мастерская')
        self.assertEqual(self.db.get(1)['location'], 'Гараж')

    async def test_edit_cancel_invalid_input_and_concurrent_move(self):
        await self.add_tool()
        await self.send(callback='editfield:1:0:name')
        await self.send('/cancel')
        self.assertEqual(self.db.get(1)['version'], 0)
        await self.send(callback='editfield:1:0:name')
        await self.send(photo=True)
        self.assertIn('Введите название', self.session.sent[-1].text)
        await self.send(callback='take:1:0', user_id=2)
        await self.send('Устаревшее имя')
        self.assertIn('Карточка устарела', self.output())
        self.assertEqual(self.db.get(1)['name'], '<Перфоратор>')
        self.assertEqual(self.db.get(1)['holder_id'], 2)

    async def test_any_participant_renames_location_and_duplicate_can_be_corrected(self):
        await self.add_tool()
        await self.send(callback='loc:browse:0:0:3', user_id=2)
        markup = self.session.sent[-1].reply_markup
        self.assertTrue(any(button.callback_data == 'renameplace:3:0' for row in markup.inline_keyboard for button in row))
        await self.send(callback='renameplace:3:0', user_id=2)
        await self.send('Дом', user_id=2)
        self.assertIn('уже существует', self.session.sent[-1].text)
        self.assertEqual(self.db.get_location(3)['name'], 'Гараж')
        await self.send('Мастерская <А>', user_id=2)
        self.assertEqual(self.db.get(1)['home'], 'Мастерская <А>')
        self.assertEqual(self.db.get(1)['location'], 'Мастерская <А>')
        self.assertIn('Мастерская &lt;А&gt;', self.session.sent[-1].text)
        await self.send(callback='renameplace:3:0')
        self.assertIn('Локация уже изменена', self.session.sent[-1].text)

    async def test_location_rename_cancel_and_edit_access_denied(self):
        await self.send(callback='renameplace:1:0')
        await self.send('/cancel')
        self.assertEqual(self.db.get_location(1)['name'], 'Дом')
        await self.send(callback='renameplace:1:0', user_id=99)
        await self.send(callback='editfield:1:0:name', user_id=99)
        self.assertIn('Нет доступа', self.session.sent[-1].text)
        self.assertEqual(self.db.get_location(1)['version'], 0)


if __name__ == '__main__':
    unittest.main()
