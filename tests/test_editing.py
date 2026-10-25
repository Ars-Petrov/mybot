import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path

from backup import create_snapshot
from storage import Conflict, Inventory


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'tools.sqlite3'
        self.db = Inventory(self.path)
        self.db.register(1, 'Арсений')
        self.db.register(2, 'Иван')
        self.tool = self.db.add('Дрель', 'Описание', 1, 'old-photo', 1)

    def test_any_participant_edits_fields_without_changing_custody_or_current_place(self):
        self.db.move(self.tool, 0, 1, 'take')
        for version, field, value in [(1, 'name', 'Новая дрель'), (2, 'description', ''),
                                      (3, 'photo', 'new-photo'), (4, 'photo', None), (5, 'home', 2)]:
            self.db.edit_tool(self.tool, version, 2, field, value)
        tool = self.db.get(self.tool)
        self.assertEqual(tool['name'], 'Новая дрель')
        self.assertEqual(tool['description'], '')
        self.assertIsNone(tool['photo'])
        self.assertEqual(tool['home'], 'Дача')
        self.assertEqual(tool['location'], 'Дом')
        self.assertEqual(tool['holder_id'], 1)
        self.assertEqual(self.db.listing(holder_id=1)[1], 1)
        self.assertEqual(self.db.listing(query='новая')[1], 1)
        self.assertEqual(self.db.history(self.tool)[-1]['action'], 'Добавлен')
        self.db.move(self.tool, 6, 1, 'return')
        self.assertEqual(self.db.get(self.tool)['location'], 'Дача')

    def test_edit_validation_stale_card_and_deleted_tool_leave_data_intact(self):
        for field, value in [('name', ''), ('name', 'a' * 81), ('description', 'a' * 501),
                             ('photo', ''), ('location', 'Дача')]:
            with self.assertRaises(ValueError):
                self.db.edit_tool(self.tool, 0, 2, field, value)
        with self.assertRaises(Conflict):
            self.db.edit_tool(self.tool, 0, 2, 'home', 999)
        self.assertEqual(self.db.get(self.tool)['version'], 0)
        self.db.move(self.tool, 0, 1, 'take')
        with self.assertRaises(Conflict):
            self.db.edit_tool(self.tool, 0, 2, 'name', 'Устарело')
        self.db.delete(self.tool, 1, 2)
        with self.assertRaises(Conflict):
            self.db.edit_tool(self.tool, 2, 1, 'name', 'Удалённый')

    def test_location_rename_updates_both_links_keeps_history_and_survives_restart(self):
        another = self.db.add('Пила', '', 1, None, 1)
        self.db.move(another, 0, 1, 'take')
        self.db.move(another, 1, 1, 'put', location_id=2)
        original_history = self.db.history(self.tool)
        count = self.db.rename_location(1, 0, 'Квартира', 2)
        self.assertEqual(count, 2)
        self.assertEqual(self.db.get(self.tool)['location'], 'Квартира')
        self.assertEqual(self.db.get(self.tool)['home'], 'Квартира')
        self.assertEqual(self.db.get(another)['home'], 'Квартира')
        self.assertEqual(self.db.get(another)['location'], 'Дача')
        self.assertEqual(self.db.history(self.tool)[1:], original_history)
        reloaded = Inventory(self.path)
        self.assertEqual(reloaded.get_location(1)['name'], 'Квартира')
        self.assertNotIn('Дом', {place['name'] for place in reloaded.locations()[0]})
        self.assertEqual(reloaded.listing(location_id=1)[1], 1)
        self.assertEqual(reloaded.add_location('квартира')['id'], 1)
        snapshot = create_snapshot(self.path, Path(self.tmp.name) / 'backups')
        restored = Inventory(snapshot)
        self.assertEqual(restored.get_location(1)['name'], 'Квартира')
        self.assertEqual(restored.get(self.tool)['home'], 'Квартира')

    def test_rename_duplicate_stale_and_invalid_do_not_partially_change_tools(self):
        for name in ['   ', 'a' * 121, ' даЧа  ']:
            with self.assertRaises(ValueError):
                self.db.rename_location(1, 0, name, 2)
        self.assertEqual(self.db.get_location(1)['name'], 'Дом')
        self.assertEqual(self.db.get(self.tool)['version'], 0)
        self.db.rename_location(1, 0, 'Квартира', 2)
        with self.assertRaises(Conflict):
            self.db.rename_location(1, 0, 'Устаревшее имя', 1)
        with self.assertRaises(Conflict):
            self.db.move(self.tool, 0, 1, 'take')
        self.assertEqual(self.db.get(self.tool)['home'], 'Квартира')
        self.assertEqual(self.db.get_location(1)['name'], 'Квартира')

    def test_case_change_and_deleted_tool_do_not_reintroduce_old_places(self):
        self.db.delete(self.tool, 0, 1)
        self.db.rename_location(1, 0, 'КВАРТИРА', 2)
        self.db.rename_location(1, 1, 'Квартира', 2)
        reloaded = Inventory(self.path)
        self.assertEqual(reloaded.get(self.tool, include_deleted=True)['location'], 'Квартира')
        self.assertEqual(reloaded.history(self.tool)[0]['action'], 'Удалён')
        self.assertNotIn('Дом', {place['name'] for place in reloaded.locations()[0]})

    def test_migrates_deployed_location_schema_without_losing_links(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('ALTER TABLE locations DROP COLUMN version')
            db.execute("DELETE FROM settings WHERE key='locations_initialized'")
        migrated = Inventory(self.path)
        self.assertEqual(migrated.get_location(1)['version'], 0)
        self.assertEqual(migrated.get(self.tool)['home'], 'Дом')
        self.assertEqual(migrated.locations()[1], 3)
        migrated.rename_location(1, 0, 'Квартира', 2)
        self.assertNotIn('Дом', {place['name'] for place in Inventory(self.path).locations()[0]})
