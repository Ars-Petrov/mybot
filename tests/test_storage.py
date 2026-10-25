import tempfile
import unittest
import sqlite3
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
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE tools DROP COLUMN deleted_at')
        migrated = Inventory(self.path)
        self.assertEqual(migrated.get(self.tool)['home'], 'Гараж')
        migrated.delete(self.tool, 0, 2)
        self.assertIsNone(migrated.get(self.tool))


if __name__ == '__main__':
    unittest.main()
