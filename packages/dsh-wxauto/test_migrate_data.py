import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('migrate_data', Path(__file__).with_name('migrate-data.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'home'
        self.project = self.base / 'project'
        self.root = self.base / 'new-data'
        self.write(self.home / '.dsh/memories/USER.md', b'user facts')
        self.write(self.home / '.dsh/memories/.credentials.yaml', b'NEVER COPY')
        self.write(self.home / '.dsh/memories/SUGGESTIONS.jsonl', b'{}\n')
        self.write(self.home / '.bili-ai/persona-history/old.md', b'old persona')
        self.write(self.home / '.bili-ai/persona.md',
                   ('edit ' + str(self.home / '.bili-ai/persona.md')).encode())
        self.write(self.home / '.bili-ai/credentials.json', b'NEVER COPY')
        self.write(self.home / '.dsh/whale-work/AGENTS.md', b'workspace instruction')
        self.write(self.home / '.dsh/whale-diary.md', b'diary')
        self.write(self.project / 'wx-media/chat-a/image.png', b'actual retained image')
        self.write(self.project / 'voice-data/models/large.safetensors', b'NEVER COPY')
        self.reference = self.project / 'voice-data/auditions/reference.wav'
        self.write(self.reference, b'approved sample')
        self.write(self.project / 'voice-data/voice.local.json', module.json_bytes({
            'approved': True, 'referenceAudio': str(self.reference), 'referenceText': '试听'}))
        self.config = self.project / 'plugins/dsh-wxauto/config.local.json'
        self.write(self.config, module.json_bytes({'workspace': str(self.home / '.dsh/whale-work'),
            'target': '主人', 'sessionId': 'bili-whale', 'token': 'NEVER COPY', 'python': 'runtime-python'}))
        self.write(self.config.with_name('receipts.local.json'), b'{"claims":["old-send"]}')

    def write(self, file, content):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)

    def plan(self):
        return module.Migration(self.project, self.home, self.root).build()

    def test_dry_run_builds_complete_plan_without_any_destination_write(self):
        before = self.config.read_bytes()
        result = self.plan().report()
        self.assertTrue(result['dryRun'])
        self.assertFalse(self.root.exists())
        self.assertEqual(self.config.read_bytes(), before)
        files = [item['relativePath'] for item in result['files']]
        self.assertIn('memory/USER.md', files)
        self.assertIn('media/legacy/chat-a/image.png', files)
        self.assertIn('config/workspace/AGENTS.md', files)
        self.assertFalse(any('models/' in name or 'credential' in name for name in files))

    def test_execute_verifies_copies_exports_configs_and_never_changes_source(self):
        before = self.config.read_bytes()
        result = self.plan().execute()
        self.assertTrue(result['complete'])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(self.reference.is_file())
        config = json.loads((self.root / 'config/wxauto.local.json').read_text('utf8'))
        self.assertNotIn('token', config)
        self.assertEqual(config['dataRoot'], str(self.root))
        self.assertEqual(config['workspace'], str(self.home / '.dsh/whale-work'))
        voice = json.loads((self.root / 'voice/voice.local.json').read_text('utf8'))
        self.assertEqual(voice['referenceAudioBase'], 'dataRoot')
        self.assertTrue((self.root / voice['referenceAudio']).is_file())
        self.assertIn(str(self.root / 'persona/persona.md'),
                      (self.root / 'persona/persona.md').read_text(encoding='utf8'))
        self.assertFalse((self.root / 'voice/models').exists())
        for item in result['files']:
            self.assertEqual(module.sha256(self.root / item['relativePath']), item['sha256'])

    def test_rerun_skips_identical_files_without_overwriting(self):
        self.plan().execute()
        file = self.root / 'memory/USER.md'
        timestamp = file.stat().st_mtime_ns
        self.assertTrue(self.plan().execute()['complete'])
        self.assertEqual(file.stat().st_mtime_ns, timestamp)

    def test_conflicting_destination_fails_before_copying_other_files(self):
        self.write(self.root / 'memory/USER.md', b'different')
        with self.assertRaisesRegex(RuntimeError, 'existing-file-conflict'):
            self.plan().execute()
        self.assertEqual((self.root / 'memory/USER.md').read_bytes(), b'different')
        self.assertFalse((self.root / 'manifest.json').exists())
        self.assertFalse((self.root / 'persona').exists())

    def test_future_manifest_is_rejected_without_data_changes(self):
        self.write(self.root / 'manifest.json', module.json_bytes({'format': 'whale-data','schemaVersion': 3}))
        with self.assertRaisesRegex(RuntimeError, 'unsupported-data-manifest'):
            self.plan()
        self.assertFalse((self.root / 'memory').exists())

    def test_changed_source_invalidates_stale_plan(self):
        plan = self.plan()
        self.reference.write_bytes(b'different')
        with self.assertRaisesRegex(RuntimeError, 'migration-source-changed'):
            plan.execute()
        self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()
