import tempfile
import unittest
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from storage import Conflict, Inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'tools.sqlite3'
        self.db = Inventory(self.path)
        self.db.register(1, 'Арсений')
        self.db.register(2, 'Иван')
        self.tool = self.db.add('Перфоратор <Bosch>', 'Синий', 'Гараж', None, 1)

    def test_take_transfer_return_and_persistence(self):
        self.db.move(self.tool, 0, 1, 'take')
        self.assertEqual(self.db.listing(holder_id=1)[1], 1)
        self.db.move(self.tool, 1, 1, 'location', location='Багажник')
        self.db.move(self.tool, 2, 1, 'transfer', holder_id=2)
        self.assertEqual(self.db.listing(holder_id=1)[1], 0)
        self.assertEqual(self.db.listing(holder_id=2)[1], 1)
        self.assertEqual(self.db.get(self.tool)['location'], 'Багажник')
        self.db.move(self.tool, 3, 2, 'return')
        reloaded = Inventory(self.path)
        tool = reloaded.get(self.tool)
        self.assertIsNone(tool['holder_id'])
        self.assertEqual(tool['location'], 'Гараж')
        self.assertEqual(len(reloaded.history(self.tool)), 5)

    def test_old_buttons_and_other_member_cannot_overwrite(self):
        self.db.move(self.tool, 0, 1, 'take')
        for version, actor, action in [(0, 2, 'take'), (1, 2, 'return'), (1, 2, 'location')]:
            with self.assertRaises(Conflict):
                self.db.move(self.tool, version, actor, action, location='Дача')
        self.assertEqual(self.db.get(self.tool)['version'], 1)
        self.assertEqual(len(self.db.history(self.tool)), 2)

    def test_two_people_take_at_once_only_one_wins(self):
        def take(actor):
            try:
                self.db.move(self.tool, 0, actor, 'take')
                return True
            except Conflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(take, [1, 2])), 1)
        self.assertEqual(len(self.db.history(self.tool)), 2)

    def test_search_cyrillic_and_pagination(self):
        for i in range(10):
            self.db.add(f'Дрель {i}', '', 'Дом', None, 1)
        rows, total = self.db.listing(query='ДРЕЛЬ')
        self.assertEqual((len(rows), total), (8, 10))
        self.assertEqual(len(self.db.listing(query='ДРЕЛЬ', page=1)[0]), 2)
        self.assertEqual(self.db.listing(query="' OR 1=1 --")[1], 0)

    def test_take_from_relative_creates_persistent_notification(self):
        self.db.move(self.tool, 0, 1, 'take')
        self.assertEqual(self.db.pending_notifications(), [])
        self.db.move(self.tool, 1, 2, 'take')
        self.assertEqual(self.db.listing(holder_id=1)[1], 0)
        self.assertEqual(self.db.listing(holder_id=2)[1], 1)
        pending = Inventory(self.path).pending_notifications()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['recipient_id'], 1)
        self.assertEqual(pending[0]['taker_name'], 'Иван')
        with self.assertRaises(Conflict):
            self.db.move(self.tool, 1, 1, 'take')
        self.assertEqual(len(self.db.pending_notifications()), 1)

    def test_any_member_can_delete_occupied_tool_and_old_buttons_fail(self):
        self.db.move(self.tool, 0, 1, 'take')
        self.db.delete(self.tool, 1, 2)
        self.assertIsNone(self.db.get(self.tool))
        self.assertIsNotNone(self.db.get(self.tool, include_deleted=True))
        self.assertEqual(self.db.listing()[1], 0)
        self.assertEqual(self.db.listing(holder_id=1)[1], 0)
        self.assertEqual(self.db.history(self.tool)[0]['action'], 'Удалён')
        with self.assertRaises(Conflict):
            self.db.move(self.tool, 2, 2, 'take')
        with self.assertRaises(Conflict):
            self.db.delete(self.tool, 2, 2)

    def test_migration_from_first_version_keeps_data(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('ALTER TABLE tools DROP COLUMN deleted_at')
        migrated = Inventory(self.path)
        self.assertEqual(migrated.get(self.tool)['home'], 'Гараж')
        migrated.delete(self.tool, 0, 2)
        self.assertIsNone(migrated.get(self.tool))

    def test_locations_are_shared_unique_and_paginated(self):
        self.assertEqual([place['name'] for place in self.db.locations()[0]], ['Дом', 'Дача', 'Гараж'])
        first = self.db.add_location(' Мастерская   у  дома ')
        duplicate = self.db.add_location('мастерская у дома')
        self.assertEqual(first['id'], duplicate['id'])
        self.assertEqual(first['name'], 'Мастерская у дома')
        for index in range(10):
            self.db.add_location(f'Место {index}')
        self.assertEqual(len(self.db.locations()[0]), 8)
        self.assertEqual(self.db.locations(page=1)[1], 14)
        for name in ['', '   ', 'a' * 121]:
            with self.assertRaises(ValueError):
                self.db.add_location(name)

    def test_put_releases_holder_filters_location_and_preserves_home(self):
        place = self.db.add_location('Мастерская')
        self.db.move(self.tool, 0, 1, 'take')
        self.db.move(self.tool, 1, 1, 'put', location_id=place['id'])
        tool = self.db.get(self.tool)
        self.assertIsNone(tool['holder_id'])
        self.assertEqual(tool['location'], 'Мастерская')
        self.assertEqual(tool['home'], 'Гараж')
        self.assertEqual(self.db.listing(holder_id=1)[1], 0)
        self.assertEqual(self.db.listing(location_id=place['id'])[1], 1)
        self.assertEqual(self.db.history(self.tool)[0]['action'], 'Положен в локацию')
        self.db.move(self.tool, 2, 2, 'take')
        self.db.move(self.tool, 3, 2, 'return')
        self.assertEqual(self.db.listing(location_id=place['id'])[1], 0)
        self.assertEqual(self.db.get(self.tool)['location'], 'Гараж')

    def test_put_rejects_other_holder_invalid_place_and_stale_card(self):
        self.db.move(self.tool, 0, 1, 'take')
        for actor, version, place in [(2, 1, 1), (1, 0, 1), (1, 1, 999)]:
            with self.assertRaises(Conflict):
                self.db.move(self.tool, version, actor, 'put', location_id=place)
        self.assertEqual(self.db.get(self.tool)['version'], 1)
        self.assertEqual(self.db.get(self.tool)['holder_id'], 1)

    def test_legacy_location_migration_preserves_text_and_history(self):
        old_tool = self.db.add('Пила', '', 'Кладовка, полка №2', None, 1)
        self.db.move(old_tool, 0, 1, 'location', location='Лоджия')
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('DROP TABLE locations')
        migrated = Inventory(self.path)
        names = {row['name'] for row in migrated.locations(page_size=100)[0]}
        self.assertTrue({'Кладовка, полка №2', 'Лоджия'} <= names)
        self.assertEqual(migrated.get(old_tool)['home'], 'Кладовка, полка №2')
        self.assertEqual(migrated.get(old_tool)['location'], 'Лоджия')
        self.assertEqual(len(migrated.history(old_tool)), 2)
        count = migrated.locations()[1]
        self.assertEqual(Inventory(self.path).locations()[1], count)


if __name__ == '__main__':
    unittest.main()
