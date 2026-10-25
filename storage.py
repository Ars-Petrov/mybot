"""Shared tool inventory, persisted in SQLite. No ownership concept."""
import sqlite3
import time
import secrets
from contextlib import contextmanager
from pathlib import Path


class Conflict(Exception):
    """An old card must not overwrite a more recent movement."""


class Inventory:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS members (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tools (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    description TEXT NOT NULL, home TEXT NOT NULL,
                    location TEXT NOT NULL, photo TEXT,
                    holder_id INTEGER REFERENCES members(id),
                    version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS movements (
                    id INTEGER PRIMARY KEY,
                    tool_id INTEGER NOT NULL REFERENCES tools(id),
                    actor_id INTEGER NOT NULL REFERENCES members(id),
                    action TEXT NOT NULL, holder_id INTEGER REFERENCES members(id),
                    location TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY, recipient_id INTEGER NOT NULL,
                    tool_name TEXT NOT NULL, taker_name TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    delivered INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS family_access (
                    user_id INTEGER PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS locations (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE
                );
            """)
            # Migrate databases created by the first version without losing inventory.
            db.execute('BEGIN IMMEDIATE')
            if 'deleted_at' not in {row['name'] for row in db.execute('PRAGMA table_info(tools)')}:
                db.execute('ALTER TABLE tools ADD COLUMN deleted_at TEXT')
            if 'version' not in {row['name'] for row in db.execute('PRAGMA table_info(locations)')}:
                db.execute('ALTER TABLE locations ADD COLUMN version INTEGER NOT NULL DEFAULT 0')
            if db.execute("SELECT 1 FROM settings WHERE key='locations_initialized'").fetchone() is None:
                for name in ('Дом', 'Дача', 'Гараж'):
                    self._ensure_location(db, name)
                db.execute("INSERT INTO settings(key,value) VALUES ('locations_initialized','1')")
            # Preserve existing text and history; import legacy places into the picker.
            for row in db.execute('SELECT home, location FROM tools').fetchall():
                self._ensure_location(db, row['home'])
                self._ensure_location(db, row['location'])

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, user_id, name):
        with self.connect() as db:
            db.execute("INSERT INTO members VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                       (user_id, name))

    def members(self, allowed_ids):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM members ORDER BY name")
                    if row['id'] in allowed_ids]

    def seed_access(self, user_ids):
        with self.connect() as db:
            db.executemany('INSERT OR IGNORE INTO family_access(user_id) VALUES (?)',
                           [(user_id,) for user_id in user_ids])

    def allowed_ids(self):
        with self.connect() as db:
            return {row['user_id'] for row in db.execute('SELECT user_id FROM family_access')}

    def invite_token(self, rotate=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM settings WHERE key='invite_token'").fetchone()
            if row is not None and not rotate:
                return row['value']
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO settings(key,value) VALUES ('invite_token',?) "
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (token,))
            return token

    def join(self, token, user_id, name):
        """Validate invitation and persist identity/access in one transaction."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM settings WHERE key='invite_token'").fetchone()
            if row is None or not token.isascii() or not secrets.compare_digest(row['value'], token):
                return False
            db.execute('INSERT OR IGNORE INTO family_access(user_id) VALUES (?)', (user_id,))
            db.execute('INSERT INTO members VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name',
                       (user_id, name))
            return True

    @staticmethod
    def _ensure_location(db, name):
        name = ' '.join(name.split())
        if not 1 <= len(name) <= 120:
            raise ValueError('Название локации должно содержать от 1 до 120 символов.')
        key = name.casefold()
        db.execute('INSERT OR IGNORE INTO locations(name,name_key) VALUES (?,?)', (name, key))
        return db.execute('SELECT * FROM locations WHERE name_key=?', (key,)).fetchone()

    def add_location(self, name):
        with self.connect() as db:
            return dict(self._ensure_location(db, name))

    def get_location(self, location_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM locations WHERE id=?', (location_id,)).fetchone()
            return dict(row) if row else None

    def locations(self, page=0, page_size=8):
        with self.connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM locations ORDER BY id')]
            return rows[page * page_size:(page + 1) * page_size], len(rows)

    def rename_location(self, location_id, version, name, actor_id):
        name = ' '.join(name.split())
        if not 1 <= len(name) <= 120:
            raise ValueError('Введите название локации от 1 до 120 символов.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            place = db.execute('SELECT * FROM locations WHERE id=?', (location_id,)).fetchone()
            if not place or place['version'] != version:
                raise Conflict('Локация уже изменена. Откройте её заново.')
            duplicate = db.execute('SELECT id FROM locations WHERE name_key=? AND id<>?',
                                   (name.casefold(), location_id)).fetchone()
            if duplicate:
                raise ValueError('Локация с таким названием уже существует. Выберите другое.')
            if name == place['name']:
                return 0
            db.execute('UPDATE locations SET name=?,name_key=?,version=version+1 WHERE id=?',
                       (name, name.casefold(), location_id))
            affected = 0
            for tool in db.execute('SELECT * FROM tools').fetchall():
                current_matches = ' '.join(tool['location'].split()).casefold() == place['name_key']
                home_matches = ' '.join(tool['home'].split()).casefold() == place['name_key']
                if not (current_matches or home_matches):
                    continue
                location = name if current_matches else tool['location']
                home = name if home_matches else tool['home']
                db.execute('UPDATE tools SET location=?,home=?,version=version+1 WHERE id=?',
                           (location, home, tool['id']))
                if tool['deleted_at'] is None:
                    affected += 1
                    db.execute('INSERT INTO movements(tool_id,actor_id,action,holder_id,location) VALUES (?,?,?,?,?)',
                               (tool['id'], actor_id, 'Локация переименована', tool['holder_id'], location))
            return affected

    def edit_tool(self, tool_id, version, actor_id, field, value):
        labels = {'name': 'Изменено название', 'description': 'Изменено описание',
                  'photo': 'Изменено фото', 'home': 'Изменено место возврата'}
        if field not in labels:
            raise ValueError('Неизвестное поле инструмента.')
        if field == 'name':
            value = (value or '').strip()
            if not 1 <= len(value) <= 80:
                raise ValueError('Введите название от 1 до 80 символов.')
        elif field == 'description':
            value = (value or '').strip()
            if len(value) > 500:
                raise ValueError('Описание должно быть не длиннее 500 символов.')
        elif field == 'photo' and value is not None and (not isinstance(value, str) or not value):
            raise ValueError('Пришлите фото или удалите текущее.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            tool = db.execute('SELECT * FROM tools WHERE id=?', (tool_id,)).fetchone()
            if not tool or tool['deleted_at'] is not None or tool['version'] != version:
                raise Conflict('Карточка устарела. Откройте инструмент ещё раз.')
            if field == 'home':
                place = db.execute('SELECT * FROM locations WHERE id=?', (value,)).fetchone()
                if place is None:
                    raise Conflict('Локация не найдена. Выберите её заново.')
                value = place['name']
            # Column name comes exclusively from the fixed field allowlist above.
            db.execute(f'UPDATE tools SET {field}=?,version=version+1 WHERE id=?', (value, tool_id))
            db.execute('INSERT INTO movements(tool_id,actor_id,action,holder_id,location) VALUES (?,?,?,?,?)',
                       (tool_id, actor_id, labels[field], tool['holder_id'], tool['location']))

    def add(self, name, description, home, photo, actor_id):
        with self.connect() as db:
            if isinstance(home, int):
                place = db.execute('SELECT * FROM locations WHERE id=?', (home,)).fetchone()
                if place is None:
                    raise ValueError('Локация не найдена.')
            else:
                place = self._ensure_location(db, home)
            home = place['name']
            tool_id = db.execute(
                "INSERT INTO tools(name, description, home, location, photo) VALUES (?, ?, ?, ?, ?)",
                (name, description, home, home, photo)).lastrowid
            db.execute("INSERT INTO movements(tool_id, actor_id, action, location) VALUES (?, ?, ?, ?)",
                       (tool_id, actor_id, 'Добавлен', home))
            return tool_id

    def get(self, tool_id, include_deleted=False):
        with self.connect() as db:
            row = db.execute("""SELECT t.*, m.name AS holder_name FROM tools t
                              LEFT JOIN members m ON m.id=t.holder_id WHERE t.id=?""",
                             (tool_id,)).fetchone()
            return dict(row) if row and (include_deleted or row['deleted_at'] is None) else None

    def listing(self, holder_id=None, query='', page=0, page_size=8, location_id=None):
        with self.connect() as db:
            # Python casefold also supports Cyrillic, unlike SQLite's default LIKE.
            rows = [dict(row) for row in db.execute("""SELECT t.*, m.name AS holder_name FROM tools t
                    LEFT JOIN members m ON m.id=t.holder_id WHERE t.deleted_at IS NULL ORDER BY t.id DESC""")]
        rows = [row for row in rows if (holder_id is None or row['holder_id'] == holder_id)
                and query.casefold() in (row['name'] + ' ' + row['description']).casefold()]
        if location_id is not None:
            place = self.get_location(location_id)
            if place is None:
                return [], 0
            rows = [row for row in rows if ' '.join(row['location'].split()).casefold() == place['name_key']]
        return rows[page * page_size:(page + 1) * page_size], len(rows)

    def move(self, tool_id, version, actor_id, action, holder_id=None, location=None, location_id=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tools WHERE id=?', (tool_id,)).fetchone()
            if not row or row['deleted_at'] is not None or row['version'] != version:
                raise Conflict('Карточка устарела. Откройте инструмент ещё раз.')
            if action == 'take':
                if row['holder_id'] == actor_id:
                    raise Conflict('Инструмент уже у вас.')
                if row['holder_id'] is not None:
                    taker = db.execute('SELECT name FROM members WHERE id=?', (actor_id,)).fetchone()
                    db.execute('INSERT INTO notifications(recipient_id, tool_name, taker_name) VALUES (?, ?, ?)',
                               (row['holder_id'], row['name'], taker['name']))
                holder_id = actor_id
                location = row['location']
                label = 'Взят'
            else:
                if row['holder_id'] is not None and row['holder_id'] != actor_id:
                    raise Conflict('Изменить инструмент может тот, у кого он сейчас находится.')
                if action == 'put':
                    place = db.execute('SELECT * FROM locations WHERE id=?', (location_id,)).fetchone()
                    if place is None:
                        raise Conflict('Локация не найдена. Выберите её заново.')
                    holder_id, location, label = None, place['name'], 'Положен в локацию'
                elif action == 'return':
                    if row['holder_id'] != actor_id:
                        raise Conflict('Инструмент уже в месте хранения.')
                    holder_id, location, label = None, row['home'], 'Возвращён'
                elif action == 'transfer':
                    if row['holder_id'] != actor_id or holder_id == actor_id:
                        raise Conflict('Передать можно только инструмент, который сейчас у вас.')
                    if not db.execute('SELECT 1 FROM members WHERE id=?', (holder_id,)).fetchone():
                        raise Conflict('Участник ещё не запустил бота.')
                    location, label = row['location'], 'Передан'
                elif action == 'location':
                    if not location or not location.strip() or len(location) > 120:
                        raise ValueError('Место должно содержать от 1 до 120 символов.')
                    location = self._ensure_location(db, location)['name']
                    holder_id, label = row['holder_id'], 'Изменено место'
                else:
                    raise ValueError('Unknown movement')
            db.execute('UPDATE tools SET holder_id=?, location=?, version=version+1 WHERE id=?',
                       (holder_id, location, tool_id))
            db.execute('INSERT INTO movements(tool_id, actor_id, action, holder_id, location) VALUES (?, ?, ?, ?, ?)',
                       (tool_id, actor_id, label, holder_id, location))

    def delete(self, tool_id, version, actor_id):
        """Any participant may delete; keep a tombstone and history for recovery."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tools WHERE id=?', (tool_id,)).fetchone()
            if not row or row['deleted_at'] is not None or row['version'] != version:
                raise Conflict('Карточка устарела. Откройте инструмент ещё раз.')
            db.execute("UPDATE tools SET deleted_at=datetime('now'), version=version+1 WHERE id=?", (tool_id,))
            db.execute('INSERT INTO movements(tool_id, actor_id, action, holder_id, location) VALUES (?, ?, ?, ?, ?)',
                       (tool_id, actor_id, 'Удалён', row['holder_id'], row['location']))

    def pending_notifications(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT * FROM notifications WHERE delivered=0 AND next_attempt<=? ORDER BY id LIMIT 20',
                (time.time(),))]

    def notification_done(self, notification_id):
        with self.connect() as db:
            db.execute('UPDATE notifications SET delivered=1 WHERE id=?', (notification_id,))

    def notification_retry(self, notification_id, delay):
        with self.connect() as db:
            db.execute('UPDATE notifications SET attempts=attempts+1, next_attempt=? WHERE id=?',
                       (time.time() + delay, notification_id))

    def history(self, tool_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT h.*, a.name AS actor_name, m.name AS holder_name
                FROM movements h JOIN members a ON a.id=h.actor_id
                LEFT JOIN members m ON m.id=h.holder_id
                WHERE tool_id=? ORDER BY h.id DESC LIMIT 10""", (tool_id,))]
