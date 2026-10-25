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
    location_name = State()


class FamilyAccess(BaseMiddleware):
    def __init__(self, inventory, family_ids):
        self.inventory = inventory
        self.inventory.seed_access(family_ids)

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
        if isinstance(event, Message) and command == '/start' and len(words) == 2:
            payload = words[1].strip()
            if payload.startswith('join_'):
                joined = self.inventory.join(payload[5:], user.id, user.full_name)
                if not joined and user.id not in self.inventory.allowed_ids():
                    await message.answer('Приглашение недействительно. Попросите новую ссылку у участника.')
                    return
        if user.id not in self.inventory.allowed_ids():
            if isinstance(event, CallbackQuery):
                await event.answer('Нет доступа к каталогу.', show_alert=True)
            else:
                await message.answer(
                    f'Каталог доступен по приглашению. Ваш ID: <code>{user.id}</code>.\n'
                    'Откройте пригласительную ссылку и нажмите Start.')
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
            f"🙋 Сейчас у: {escape(tool['holder_name'] or 'никого — лежит в локации')}\n"
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

    async def show_locations(message, purpose='browse', tool_id=0, version=0, page=0):
        places, total = inventory.locations(page=page)
        rows = [[(place['name'][:60], f"loc:{purpose}:{tool_id}:{version}:{place['id']}")] for place in places]
        nav = []
        if page:
            nav.append(('← Назад', f'places:{purpose}:{tool_id}:{version}:{page - 1}'))
        if (page + 1) * 8 < total:
            nav.append(('Далее →', f'places:{purpose}:{tool_id}:{version}:{page + 1}'))
        if nav:
            rows.append(nav)
        rows.append([('➕ Добавить локацию', f'newplace:{purpose}:{tool_id}:{version}')])
        if purpose == 'put':
            rows.append([('Отмена', f'tool:{tool_id}')])
        title = {'browse': 'Локации', 'home': 'Где хранится инструмент?', 'put': 'Куда положить инструмент?'}[purpose]
        await message.answer(f'<b>{title}</b> · страница {page + 1}', reply_markup=kb.inline(rows))

    async def show_location_tools(message, place_id, page=0):
        place = inventory.get_location(place_id)
        if place is None:
            await message.answer('Локация не найдена.')
            return
        tools, total = inventory.listing(location_id=place_id, page=page)
        rows = [[(f"{tool['name']} · {tool['holder_name'] or 'в локации'}"[:60], f"tool:{tool['id']}")]
                for tool in tools]
        nav = []
        if page:
            nav.append(('← Назад', f'loctools:{place_id}:{page - 1}'))
        if (page + 1) * 8 < total:
            nav.append(('Далее →', f'loctools:{place_id}:{page + 1}'))
        if nav:
            rows.append(nav)
        rows.append([('← Локации', 'places:browse:0:0:0')])
        await message.answer(f"📍 <b>{escape(place['name'])}</b> · инструментов: {total}", reply_markup=kb.inline(rows))

    async def select_home(message, state, place_id):
        await state.update_data(home=place_id)
        await state.set_state(Form.photo)
        await message.answer('Пришлите одно фото или «-», чтобы сохранить без фото.')

    @router.message(Command('start', 'menu', 'cancel'))
    @router.message(F.text.in_({kb.CANCEL, 'Меню', '◀️ Выйти в меню'}))
    async def menu(message: Message, state: FSMContext):
        await state.clear()
        await message.answer('🧰 Поиск инструмента. Выберите действие.', reply_markup=kb.menu)

    @router.message(Command('invite', 'newinvite'))
    @router.message(F.text.in_({kb.INVITE, '👨‍👩‍👧‍👦 Пригласить семью'}))
    async def invite(message: Message, state: FSMContext):
        await state.clear()
        bot_user = await message.bot.get_me()
        rotate = (message.text or '').split('@')[0] == '/newinvite'
        token = inventory.invite_token(rotate=rotate)
        await message.answer(
            'Отправьте ссылку новому участнику. После открытия и нажатия Start '
            'бот сам предоставит ему доступ:\n\n'
            f'https://t.me/{bot_user.username}?start=join_{token}\n\n'
            'Любой, у кого есть эта ссылка, сможет войти. '
            '/newinvite — заменить ссылку; уже вступившие сохранят доступ.',
            reply_markup=kb.menu,
            link_preview_options={'is_disabled': True},
        )

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

    @router.message(F.text == kb.LOCATIONS)
    @router.message(Command('locations'))
    async def locations(message: Message, state: FSMContext):
        await state.clear()
        await message.answer('📍 Выберите локацию, чтобы посмотреть инструменты.', reply_markup=kb.menu)
        await show_locations(message)

    @router.message(F.text == kb.ADD_LOCATION)
    async def add_location(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(Form.location_name)
        await state.update_data(place_purpose='browse')
        await message.answer('Название новой локации? Например: «Дом», «Дача», «Мастерская». До 120 символов.', reply_markup=kb.cancel)

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
        await message.answer('Выберите место хранения из списка или введите новое название.')
        await show_locations(message, 'home')

    @router.message(Form.home)
    async def home(message: Message, state: FSMContext):
        value = (message.text or '').strip()
        if not 1 <= len(value) <= 120:
            await message.answer('Введите место текстом, от 1 до 120 символов.')
            return
        place = inventory.add_location(value)
        await select_home(message, state, place['id'])

    @router.message(Form.location_name)
    async def location_name(message: Message, state: FSMContext):
        value = ' '.join((message.text or '').split())
        if not 1 <= len(value) <= 120:
            await message.answer('Введите название локации текстом, от 1 до 120 символов.')
            return
        data = await state.get_data()
        place = inventory.add_location(value)
        purpose = data.get('place_purpose', 'browse')
        if purpose == 'home':
            await select_home(message, state, place['id'])
        elif purpose == 'put':
            try:
                inventory.move(data['tool_id'], data['version'], message.from_user.id, 'put', location_id=place['id'])
                await message.answer(f"Инструмент положен в «{escape(place['name'])}».", reply_markup=kb.menu)
            except Conflict as error:
                await message.answer(f'Локация сохранена. {error}', reply_markup=kb.menu)
            await state.clear()
            await show_card(message, data['tool_id'], message.from_user.id)
        else:
            await state.clear()
            await message.answer(f"📍 Локация «{escape(place['name'])}» доступна всем участникам.", reply_markup=kb.menu)
            await show_location_tools(message, place['id'])

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
            if action == 'loctools' and len(parts) == 3:
                place_id, page = int(parts[1]), int(parts[2])
                if page < 0:
                    raise ValueError
                await state.clear()
                await call.answer()
                await show_location_tools(call.message, place_id, page)
                return
            if action in {'places', 'loc', 'newplace'}:
                if len(parts) != (4 if action == 'newplace' else 5):
                    raise ValueError
                purpose, tool_id, version = parts[1], int(parts[2]), int(parts[3])
                if purpose not in {'browse', 'home', 'put'}:
                    raise ValueError
                if purpose == 'home' and await state.get_state() != Form.home.state:
                    raise Conflict('Выбор места устарел. Начните добавление инструмента заново.')
                if purpose == 'put':
                    tool = inventory.get(tool_id)
                    if not tool or tool['version'] != version:
                        raise Conflict('Карточка устарела. Откройте инструмент ещё раз.')
                    if tool['holder_id'] not in {None, user_id}:
                        raise Conflict('Сначала возьмите инструмент себе.')
                if action == 'newplace':
                    if purpose != 'home':
                        await state.clear()
                    await state.update_data(place_purpose=purpose, tool_id=tool_id, version=version)
                    await state.set_state(Form.location_name)
                    await call.answer()
                    await call.message.answer('Как назвать новую локацию? До 120 символов.', reply_markup=kb.cancel)
                elif action == 'places':
                    page = int(parts[4])
                    if page < 0:
                        raise ValueError
                    if purpose == 'browse':
                        await state.clear()
                    await call.answer()
                    await show_locations(call.message, purpose, tool_id, version, page)
                else:
                    place_id = int(parts[4])
                    place = inventory.get_location(place_id)
                    if place is None:
                        raise Conflict('Локация не найдена.')
                    if purpose == 'home':
                        await call.answer()
                        await select_home(call.message, state, place_id)
                    elif purpose == 'put':
                        inventory.move(tool_id, version, user_id, 'put', location_id=place_id)
                        await state.clear()
                        await call.answer('Сохранено')
                        await call.message.answer(f"Инструмент положен в «{escape(place['name'])}».", reply_markup=kb.menu)
                        await show_card(call.message, tool_id, user_id)
                    else:
                        await state.clear()
                        await call.answer()
                        await show_location_tools(call.message, place_id)
                return
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
            if action not in {'take', 'return', 'transfer', 'location', 'put', 'give', 'delete', 'delete_confirm'} or len(parts) != (4 if action == 'give' else 3):
                raise ValueError
            tool_id, version = int(parts[1]), int(parts[2])
            tool = inventory.get(tool_id)
            if not tool or tool['version'] != version:
                raise Conflict('Карточка устарела. Откройте инструмент ещё раз.')
            if action == 'delete':
                await state.clear()
                await call.answer()
                await call.message.answer(
                    f"Удалить «{escape(tool['name'])}» из общего каталога?",
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
            if action in {'transfer', 'location', 'put'}:
                if tool['holder_id'] is not None and tool['holder_id'] != user_id:
                    raise Conflict('Изменить инструмент может тот, у кого он сейчас находится.')
                await state.clear()
                if action == 'transfer':
                    if tool['holder_id'] != user_id:
                        raise Conflict('Сначала возьмите инструмент себе.')
                    members = [m for m in inventory.members(inventory.allowed_ids()) if m['id'] != user_id]
                    if not members:
                        raise Conflict('Пока некому передать. Другой участник должен запустить бота.')
                    await call.answer()
                    await call.message.answer('Кому передать? Место пока останется прежним.', reply_markup=kb.inline(
                        [[(m['name'][:60], f"give:{tool_id}:{version}:{m['id']}")] for m in members]
                        + [[('Отмена', f'tool:{tool_id}')]]))
                elif action == 'put':
                    await call.answer()
                    await show_locations(call.message, 'put', tool_id, version)
                else:
                    await state.set_state(Form.location)
                    await state.update_data(tool_id=tool_id, version=version)
                    await call.answer()
                    await call.message.answer('Где инструмент лежит сейчас? До 120 символов.', reply_markup=kb.cancel)
                return
            target = int(parts[3]) if action == 'give' else None
            if action == 'give' and target not in inventory.allowed_ids():
                raise Conflict('Получатель не входит в список участников.')
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
