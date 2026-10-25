import tempfile
import unittest
from pathlib import Path

from backup import create_snapshot
from storage import Inventory


class InviteTests(unittest.TestCase):
    def test_first_member_can_join_without_any_configured_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tools.sqlite3'
            db = Inventory(path)
            self.assertEqual(db.allowed_ids(), set())
            self.assertFalse(db.join('anything', 1, 'Не приглашён'))
            token = db.invite_token()
            self.assertLessEqual(len('join_' + token), 64)
            self.assertFalse(db.join('неверный', 1, 'Не приглашён'))
            self.assertTrue(db.join(token, 1, 'Арсений'))
            self.assertTrue(db.join(token, 1, 'Обновлённое имя'))
            reloaded = Inventory(path)
            self.assertEqual(reloaded.allowed_ids(), {1})
            self.assertEqual(reloaded.members({1})[0]['name'], 'Обновлённое имя')
            self.assertEqual(reloaded.invite_token(), token)
            snapshot = create_snapshot(path, Path(directory) / 'backups')
            restored = Inventory(snapshot)
            self.assertEqual(restored.allowed_ids(), {1})
            self.assertTrue(restored.join(token, 2, 'Иван'))
            self.assertEqual(restored.allowed_ids(), {1, 2})

    def test_seed_access_is_idempotent_and_does_not_erase_invited_members(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Inventory(Path(directory) / 'tools.sqlite3')
            db.seed_access({1, 2})
            db.seed_access({1, 2})
            db.join(db.invite_token(), 3, 'Иван')
            db.seed_access(set())
            self.assertEqual(db.allowed_ids(), {1, 2, 3})
