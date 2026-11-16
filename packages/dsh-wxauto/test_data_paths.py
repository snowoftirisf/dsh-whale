import json
import tempfile
import unittest
from pathlib import Path

from data_paths import (absolute_data_path, initialize_data_root, read_data_manifest,
                        relative_data_path, resolve_data_paths, validate_manifest)


class DataPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'data'

    def test_resolve_does_not_create_and_relative_paths_survive_moving(self):
        paths = resolve_data_paths({'dataRoot': str(self.root)})
        self.assertEqual(paths['database'], self.root / 'vault/whale.sqlite')
        self.assertFalse(self.root.exists())
        relative = relative_data_path(paths['media'] / '聊天/image.png', root=self.root)
        self.assertEqual(relative, 'media/聊天/image.png')
        moved = self.root.with_name('moved')
        self.assertEqual(absolute_data_path(relative, root=moved), moved / 'media/聊天/image.png')

    def test_manifest_accepts_legacy_vault_layout_and_rejects_future_or_custom_paths(self):
        result = validate_manifest({'format': 'whale-data', 'schemaVersion': 1,
                                    'paths': {'database': 'vault/whale.sqlite'}})
        self.assertEqual(result['paths']['voice'], 'voice')
        for value in ({'format': 'other', 'schemaVersion': 1},
                      {'format': 'whale-data', 'schemaVersion': True},
                      {'format': 'whale-data', 'schemaVersion': 99},
                      {'format': 'whale-data', 'schemaVersion': 1, 'paths': {'voice': '../private'}}):
            with self.assertRaises(RuntimeError):
                validate_manifest(value)

    def test_initialization_is_explicit_and_preserves_existing_manifest(self):
        paths = initialize_data_root(root=self.root)
        content = paths['manifest'].read_bytes()
        initialize_data_root(root=self.root)
        self.assertEqual(paths['manifest'].read_bytes(), content)
        self.assertEqual(read_data_manifest(root=self.root)['schemaVersion'], 2)
        self.assertTrue(paths['persona'].is_dir())
        self.assertNotIn(str(self.root), content.decode())

    def test_escape_and_nonportable_paths_are_rejected(self):
        for value in ('../secret', '/absolute', r'voice\test.wav', 'C:/private.wav', '.'):
            with self.assertRaises(RuntimeError):
                absolute_data_path(value, root=self.root)
        with self.assertRaises(RuntimeError):
            relative_data_path(self.root.parent / 'outside', root=self.root)
        with self.assertRaises(RuntimeError):
            read_data_manifest(root=self.root, required=True)


if __name__ == '__main__':
    unittest.main()
