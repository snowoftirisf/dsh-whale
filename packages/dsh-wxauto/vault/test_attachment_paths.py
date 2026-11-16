"""History paths rebase at read time, without altering stored evidence."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from .store import Vault, restore


class AttachmentPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.base = Path(self.temp.name)
        self.v = Vault(self.base / 'old')
        self.chat = self.v.ensure_chat('account', 'native:owner', 'friend', 'owner')['id']
        self.message = self.v.record_message(self.chat, 'fixture', 'file-1', text='received file', type='file')['id']
        self.file = self.v.root / 'media' / 'attachments' / 'one.txt'
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b'fixture bytes')
        self.v.attach(self.message, self.file, {'path': str(self.file),
            'relativePath': 'obsolete/metadata/path', 'contentStatus': 'unknown'})

    def tearDown(self):
        self.v.close()
        self.temp.cleanup()

    def attachment(self, vault=None):
        vault = vault or self.v
        return vault.search(self.chat)['messages'][0]['attachments'][0]

    def stored_metadata(self, vault):
        return vault.db.execute('SELECT metadata FROM attachments WHERE message_id=?', (self.message,)).fetchone()[0]

    def test_restored_query_rebases_paths_without_changing_database_evidence(self):
        stored = self.stored_metadata(self.v)
        backup = self.v.backup(self.base / 'backup')
        moved = self.base / 'moved'
        restore(backup['directory'], moved)
        v = Vault(moved)
        try:
            result = self.attachment(v)
            current = moved / 'media' / 'attachments' / 'one.txt'
            self.assertEqual(result['path'], 'media/attachments/one.txt')
            self.assertEqual(result['absolutePath'], str(current))
            self.assertEqual(result['pathStatus'], 'available')
            self.assertEqual(result['metadata']['path'], str(current))
            self.assertEqual(result['metadata']['relativePath'], result['path'])
            self.assertEqual(result['metadata']['contentStatus'], 'unknown')
            self.assertEqual(self.stored_metadata(v), stored)
            self.assertEqual(self.stored_metadata(self.v), stored)
            self.assertEqual(current.read_bytes(), b'fixture bytes')
            self.assertNotIn(str(self.v.root), json.dumps(result))
        finally:
            v.close()

    def test_missing_and_invalid_canonical_paths_never_fall_back_to_metadata(self):
        self.file.unlink()
        result = self.attachment()
        self.assertEqual(result['pathStatus'], 'missing')
        self.assertEqual(result['path'], 'media/attachments/one.txt')
        self.assertIsNone(result['absolutePath'])
        self.assertNotIn('path', result['metadata'])
        for invalid in ('../outside.txt', 'C:/old/file.txt', r'media\one.txt', 'media/../one.txt'):
            self.v.db.execute('UPDATE attachments SET path=?', (invalid,))
            self.v.db.commit()
            result = self.attachment()
            self.assertEqual(result['pathStatus'], 'invalid')
            self.assertIsNone(result['path'])
            self.assertIsNone(result['absolutePath'])
            self.assertNotIn('path', result['metadata'])
            self.assertNotIn('relativePath', result['metadata'])

    def test_linked_path_does_not_expose_an_usable_absolute_path(self):
        outside = self.base / 'outside.txt'
        outside.write_bytes(b'outside fixture')
        self.file.unlink()
        try:
            os.symlink(outside, self.file)
        except OSError:
            self.skipTest('Host does not allow unprivileged symlink creation')
        result = self.attachment()
        self.assertEqual(result['pathStatus'], 'invalid')
        self.assertIsNone(result['absolutePath'])
        self.assertNotIn('path', result['metadata'])

    def test_hardlinked_file_is_unavailable_without_changing_stored_metadata(self):
        stored = self.stored_metadata(self.v)
        alias = self.base / 'alias.txt'
        try:
            os.link(self.file, alias)
        except OSError:
            self.skipTest('Host does not allow hardlink creation')
        result = self.attachment()
        self.assertEqual(result['pathStatus'], 'unavailable')
        self.assertIsNone(result['absolutePath'])
        self.assertNotIn('path', result['metadata'])
        self.assertEqual(result['path'], 'media/attachments/one.txt')
        self.assertEqual(self.stored_metadata(self.v), stored)


if __name__ == '__main__':
    unittest.main()
