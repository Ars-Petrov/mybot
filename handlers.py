from html import escape

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import keyboards as kb
import text
from storage import Conflict
from notifications import Notifier


class Form(StatesGroup):
    name = State()
    description = State()
    home = State()
    photo = State()
    search = State()
    location = State()


class FamilyAccess(BaseMiddleware):
    def __init__(self, inventory, family_ids):
        self.inventory, self.family_ids = inventory, family_ids

    async def __call__(self, handler, event, data):
        message = event if isinstance(event, Message) else event.message
        user = event.from_user
        if not isinstance(message, Message) or message.chat.type != 'private' or user is None:
            if isinstance(event, CallbackQuery):
                await event.answer('Откройте личный чат с ботом.', show_alert=True)
            return
        words = (message.text or '').split(maxsplit=1)
        command = words[0].split('@')[0] if words else ''
        if isinstance(event, Message) and command == '/id':
            await message.answer(f'Ваш Telegram ID: <code>{user.id}</code>')
            return
        if user.id not in self.family_ids:
            if isinstance(event, CallbackQuery):
                await event.answer('Нет доступа к семейному каталогу.', show_alert=True)
            else:
                await message.answer(
                    f'Каталог доступен только семье. Ваш ID: <code>{user.id}</code>.\n'
                    'Попросите администратора добавить его в FAMILY_IDS.')
            return
        self.inventory.register(user.id, user.full_name)
        return await handler(event, data)


def create_router(inventory, family_ids, notifier=None):
    notifier = notifier or Notifier(inventory, family_ids)
    router = Router()
    access = FamilyAccess(inventory, family_ids)
    router.message.outer_middleware(access)
    router.callback_query.outer_middleware(access)

    async def show_card(message, tool_id, user_id):
        tool = inventory.get(tool_id)
        if tool is None:
            await message.answer('Инструмент не найден.')
            return
        body = (
            f"<b>{escape(tool['name'])}</b> · №{tool['id']}\n"
            f"{escape(tool['description'])}\n\n"
            f"🙋 Сейчас у: {escape(tool['holder_name'] or 'никого — в месте хранения')}\n"
            f"📍 Где лежит: {escape(tool['location'])}\n"
            f"📦 Место возврата: {escape(tool['home'])}"
        )
        if tool['photo']:
            await message.answer_photo(tool['photo'])
        await message.answer(body, reply_markup=kb.card(tool, user_id))

    async def show_list(message, user_id, state, mode='all', page=0):
        data = await state.get_data()
        query = data.get('query', '') if mode == 'search' else ''
        if mode == 'search' and not query:
            await message.answer('Поиск устарел. Введите запрос заново.', reply_markup=kb.menu)
            return
        tools, total = inventory.listing(user_id if mode == 'mine' else None, query, page)
        title = {'all': 'Все инструменты', 'mine': 'Мои инструменты', 'search': 'Результаты поиска'}[mode]
        if not tools:
            await message.answer(f'{title}: пусто.', reply_markup=kb.menu)
            return
        rows = [[(f"{tool['name']} · {tool['holder_name'] or tool['location']}"[:60], f"tool:{tool['id']}")]
                for tool in tools]
        nav = []
        if page:
            nav.append(('← Назад', f'list:{mode}:{page - 1}'))
        if (page + 1) * 8 < total:
            nav.append(('Далее →', f'list:{mode}:{page + 1}'))
        if nav:
            rows.append(nav)
        await message.answer(f'<b>{title}</b> · {total} · страница {page + 1}', reply_markup=kb.inline(rows))

    @router.message(Command('start', 'menu', 'cancel'))
    @router.message(F.text.in_({kb.CANCEL, 'Меню', '◀️ Выйти в меню'}))
    async def menu(message: Message, state: FSMContext):
        await state.clear()
        await message.answer('🧰 Семейные инструменты. Выберите действие.', reply_markup=kb.menu)

    @router.message(F.text == kb.HELP)
    @router.message(Command('help'))
    async def help_message(message: Message, state: FSMContext):
        await state.clear()
        await message.answer(text.HELP, reply_markup=kb.menu)

    @router.message(F.text.in_({kb.CATALOG, kb.MINE, '📝 Список'}))
    async def catalog(message: Message, state: FSMContext):
        await state.clear()
        await show_list(message, message.from_user.id, state, 'mine' if message.text == kb.MINE else 'all')

    @router.message(F.text == kb.ADD)
    async def add(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(Form.name)
        await message.answer('Как называется инструмент? До 80 символов.', reply_markup=kb.cancel)

    @router.message(F.text == kb.SEARCH)
    async def search(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(Form.search)
        await message.answer('Введите название или часть описания.', reply_markup=kb.cancel)

    # Menu handlers above deliberately let the user leave any input flow.
    @router.message(Form.name)
    async def name(message: Message, state: FSMContext):
        value = (message.text or '').strip()
        if not 1 <= len(value) <= 80:
            await message.answer('Введите название текстом, от 1 до 80 символов.')
            return
        await state.update_data(name=value)
        await state.set_state(Form.description)
        await message.answer('Описание или модель? До 500 символов. Отправьте «-», чтобы пропустить.')

    @router.message(Form.description)
    async def description(message: Message, state: FSMContext):
        value = (message.text or '').strip()
        if not 1 <= len(value) <= 500:
            await message.answer('Введите описание текстом до 500 символов или «-».')
            return
        await state.update_data(description='' if value == '-' else value)
        await state.set_state(Form.home)
        await message.answer('Где обычно хранится инструмент? Например: «Гараж, второй ящик». До 120 символов.')

    @router.message(Form.home)
    async def home(message: Message, state: FSMContext):
        value = (message.text or '').strip()
        if not 1 <= len(value) <= 120:
            await message.answer('Введите место текстом, от 1 до 120 символов.')
            return
        await state.update_data(home=value)
        await state.set_state(Form.photo)
        await message.answer('Пришлите одно фото или «-», чтобы сохранить без фото.')

    @router.message(Form.photo)
    async def photo(message: Message, state: FSMContext):
        if not message.photo and (message.text or '').strip() != '-':
            await message.answer('Пришлите фотографию или «-».')
            return
        data = await state.get_data()
        tool_id = inventory.add(data['name'], data['description'], data['home'],
                                message.photo[-1].file_id if message.photo else None, message.from_user.id)
        await state.clear()
        await message.answer('Инструмент добавлен в общее место хранения.', reply_markup=kb.menu)
        await show_card(message, tool_id, message.from_user.id)

    @router.message(Form.search)
    async def search_query(message: Message, state: FSMContext):
        value = (message.text or '').strip()
        if not 1 <= len(value) <= 80:
            await message.answer('Введите запрос текстом, от 1 до 80 символов.')
            return
        await state.set_state(None)
        await state.update_data(query=value)
        await message.answer(f'Поиск: {escape(value)}', reply_markup=kb.menu)
        await show_list(message, message.from_user.id, state, 'search')

    @router.message(Form.location)
    async def set_location(message: Message, state: FSMContext):
        value = (message.text or '').strip()
        if not 1 <= len(value) <= 120:
            await message.answer('Введите место текстом, от 1 до 120 символов.')
            return
        data = await state.get_data()
        try:
            inventory.move(data['tool_id'], data['version'], message.from_user.id, 'location', location=value)
        except Conflict as error:
            await message.answer(str(error))
        await state.clear()
        await message.answer('📍 Карточка инструмента:', reply_markup=kb.menu)
        await show_card(message, data['tool_id'], message.from_user.id)

    @router.callback_query()
    async def callbacks(call: CallbackQuery, state: FSMContext):
        parts = (call.data or '').split(':')
        action = parts[0]
        user_id = call.from_user.id
        try:
            if action == 'list' and len(parts) == 3:
                mode, page = parts[1], int(parts[2])
                if mode not in {'all', 'mine', 'search'} or page < 0:
                    raise ValueError
                await state.set_state(None)
                await call.answer()
                await show_list(call.message, user_id, state, mode, page)
                return
            if action in {'tool', 'history'} and len(parts) == 2:
                tool_id = int(parts[1])
                await state.set_state(None)
                await call.answer()
                if action == 'tool':
                    await show_card(call.message, tool_id, user_id)
                else:
                    rows = inventory.history(tool_id)
                    lines = []
                    for row in rows:
                        lines.append(
                            f"{row['created_at']} · {escape(row['action'])}\n"
                            f"Кто: {escape(row['actor_name'])} · у кого: {escape(row['holder_name'] or 'в месте хранения')}\n"
                            f"📍 {escape(row['location'])}"
                        )
                    # Five records per message also fit with long Telegram names.
                    for offset in range(0, max(len(lines), 1), 5):
                        await call.message.answer(
                            '<b>Последние 10 действий · время UTC</b>\n\n' + '\n\n'.join(lines[offset:offset + 5]),
                            reply_markup=kb.inline([[('← К инструменту', f'tool:{tool_id}')]]))
                return
            if action not in {'take', 'return', 'transfer', 'location', 'give', 'delete', 'delete_confirm'} or len(parts) != (4 if action == 'give' else 3):
                raise ValueError
            tool_id, version = int(parts[1]), int(parts[2])
            tool = inventory.get(tool_id)
            if not tool or tool['version'] != version:
                raise Conflict('Карточка устарела. Откройте инструмент ещё раз.')
            if action == 'delete':
                await state.clear()
                await call.answer()
                await call.message.answer(
                    f"Удалить «{escape(tool['name'])}» из каталога для всей семьи?",
                    reply_markup=kb.inline([
                        [('Да, удалить', f'delete_confirm:{tool_id}:{version}')],
                        [('Отмена', f'tool:{tool_id}')],
                    ]))
                return
            if action == 'delete_confirm':
                inventory.delete(tool_id, version, user_id)
                await state.clear()
                await call.answer('Удалено')
                await call.message.answer('Инструмент удалён из каталога.', reply_markup=kb.menu)
                return
            if action in {'transfer', 'location'}:
                if tool['holder_id'] is not None and tool['holder_id'] != user_id:
                    raise Conflict('Изменить инструмент может тот, у кого он сейчас находится.')
                await state.clear()
                if action == 'transfer':
                    if tool['holder_id'] != user_id:
                        raise Conflict('Сначала возьмите инструмент себе.')
                    members = [m for m in inventory.members(family_ids) if m['id'] != user_id]
                    if not members:
                        raise Conflict('Пока некому передать. Родственник должен запустить бота.')
                    await call.answer()
                    await call.message.answer('Кому передать? Место пока останется прежним.', reply_markup=kb.inline(
                        [[(m['name'][:60], f"give:{tool_id}:{version}:{m['id']}")] for m in members]
                        + [[('Отмена', f'tool:{tool_id}')]]))
                else:
                    await state.set_state(Form.location)
                    await state.update_data(tool_id=tool_id, version=version)
                    await call.answer()
                    await call.message.answer('Где инструмент лежит сейчас? До 120 символов.', reply_markup=kb.cancel)
                return
            target = int(parts[3]) if action == 'give' else None
            if action == 'give' and target not in family_ids:
                raise Conflict('Получатель не входит в семейный список.')
            inventory.move(tool_id, version, user_id, 'transfer' if action == 'give' else action, holder_id=target)
            notifier.wake.set()
            await state.clear()
            await call.answer('Сохранено')
            await show_card(call.message, tool_id, user_id)
        except (ValueError, IndexError):
            await call.answer('Неизвестная кнопка. Откройте меню /menu.', show_alert=True)
        except Conflict as error:
            await call.answer(str(error), show_alert=True)

    @router.message(StateFilter(None))
    async def unknown(message: Message):
        await message.answer('Выберите действие в меню.', reply_markup=kb.menu)

    return router
