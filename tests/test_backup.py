import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backup import create_snapshot, sha256, verify
from storage import Inventory


class BackupTests(unittest.TestCase):
    def test_online_wal_snapshot_restores_inventory_history_deletion_and_outbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Inventory(root / 'live.sqlite3')
            # Keep a WAL connection open so committed data is not checkpointed to main file.
            with closing(sqlite3.connect(db.path)) as writer, writer:
                writer.execute('PRAGMA journal_mode=WAL')
                db.register(1, 'Арсений')
                db.register(2, 'Иван')
                custom_place = db.add_location('Склад')
                tool = db.add('Перфоратор', 'Синий', 'Гараж', 'photo-id', 1)
                db.move(tool, 0, 1, 'take')
                db.move(tool, 1, 2, 'take')
                deleted = db.add('Старый', '', 'Дом', None, 1)
                db.delete(deleted, 0, 2)
                snapshot = create_snapshot(db.path, root / 'backups')
                verify(snapshot)
                restored = Inventory(snapshot)
                self.assertEqual(restored.get(tool)['holder_id'], 2)
                self.assertEqual(restored.get(tool)['photo'], 'photo-id')
                self.assertEqual(len(restored.history(tool)), 3)
                self.assertEqual(len(restored.pending_notifications()), 1)
                self.assertIsNone(restored.get(deleted))
                self.assertEqual(restored.history(deleted)[0]['action'], 'Удалён')
                self.assertEqual(restored.get_location(custom_place['id'])['name'], 'Склад')

    def test_manifest_hashes_and_rotation_preserve_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Inventory(root / 'live.sqlite3')
            destination = root / 'backups'
            first = create_snapshot(db.path, destination, keep=2)
            (destination / 'personal.sqlite3').write_bytes(b'do not delete')
            create_snapshot(db.path, destination, keep=2)
            latest = create_snapshot(db.path, destination, keep=2)
            manifest = json.loads((destination / 'manifest.json').read_text())
            self.assertEqual(len(manifest['snapshots']), 2)
            self.assertFalse(first.exists())
            self.assertTrue(latest.exists())
            self.assertTrue((destination / 'personal.sqlite3').exists())
            for item in manifest['snapshots']:
                self.assertEqual(sha256(destination / item['filename']), item['sha256'])

    def test_missing_or_wrong_database_never_publishes_empty_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileNotFoundError):
                create_snapshot(root / 'missing.sqlite3', root / 'backups')
            with closing(sqlite3.connect(root / 'wrong.sqlite3')) as db, db:
                db.execute('CREATE TABLE unrelated(id INTEGER)')
            with self.assertRaises(ValueError):
                create_snapshot(root / 'wrong.sqlite3', root / 'backups')
            self.assertFalse((root / 'backups' / 'manifest.json').exists())
