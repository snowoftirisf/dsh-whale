import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from .capture import CaptureService,SyncLock
from .snapshot_import import import_snapshot,load_reader
from .store import Vault,VaultError,atomic_json
from .test_snapshot_import import make_snapshot
from .vendor import verify,ROOT,COMMIT,module_context


class FakeCollector:
    """Synthetic producer. Does not invoke any vendor process/key functions."""
    def __init__(self,source):
        self.source=source;self.calls=0;self.fail=False;self.on_capture=None
        self.private_root=lambda:source/'unused-output'
        self.atomic_json=atomic_json
    def discover(self,db_root=None):return [(self.source,[self.source/'message.db'])]
    def source_id(self,root):return 'fixture-source'
    def choose_source(self,candidates,requested):
        if requested!='fixture-source':raise VaultError('capture-source-mismatch')
        return candidates[0]
    def selected_files(self,root,scope='chat'):return [root/'message.db']
    def process_ids(self):return [123]
    def capture(self,root,files,pids,**kwargs):
        self.calls+=1
        output=self.private_root();output.mkdir(parents=True,exist_ok=True)
        target=make_snapshot(output/('fake-'+str(self.calls)))
        manifest=json.loads((target/'snapshot-manifest.json').read_text(encoding='utf8'))
        receipt={'status':'complete','snapshot_id':manifest['snapshot_id'],'snapshot':str(target),
                 'source_id':'fixture-source','read_only_source':True,'keys_persisted':False}
        self.atomic_json(output/'latest.json',receipt)
        if self.on_capture:self.on_capture()
        if self.fail:raise RuntimeError('synthetic capture failure')
        return receipt


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.base=Path(self.temp.name)
        self.source=self.base/'db_storage';self.source.mkdir()
        self.fake=FakeCollector(self.source)
        self.service=CaptureService(self.base/'data',collector=self.fake)
        self.args={'db_root':str(self.source),'source_id':'fixture-source','account_id':'trusted-account'}
    def tearDown(self):self.temp.cleanup()

    def test_discover_never_calls_capture_and_keeps_environment(self):
        before=os.environ.get('LOCALAPPDATA')
        result=self.service.discover(str(self.source))
        self.assertFalse(result['memory_read']);self.assertEqual(self.fake.calls,0)
        self.assertEqual(result['weixin_processes'],[123]);self.assertEqual(os.environ.get('LOCALAPPDATA'),before)
        self.assertFalse((self.service.output/'latest.json').exists())

    def test_two_captures_are_idempotent_and_publish_only_imported_snapshot(self):
        before=os.environ.get('LOCALAPPDATA')
        first=self.service.capture_then_import(**self.args)
        second=self.service.capture_then_import(**self.args)
        self.assertNotEqual(first['snapshot_id'],second['snapshot_id'])
        self.assertEqual(second['import']['inserted'],0)
        vault=Vault(self.service.root)
        try:
            self.assertEqual(vault.status()['counts']['messages'],2)
            self.assertEqual(vault.status()['cursors']['wechat-snapshot:trusted-account']['snapshot_id'],second['snapshot_id'])
        finally:vault.close()
        latest=json.loads((self.service.output/'latest.json').read_text(encoding='utf8'))
        self.assertEqual(latest['snapshot_id'],second['snapshot_id']);self.assertIn('imported_at',latest)
        self.assertEqual(os.environ.get('LOCALAPPDATA'),before)
        self.assertTrue((self.service.root/'vault/sync.lock').is_file())

    def test_capture_failure_keeps_previous_latest_and_previous_cursor(self):
        self.service.capture_then_import(**self.args)
        latest=(self.service.output/'latest.json').read_bytes()
        vault=Vault(self.service.root)
        try:cursor=vault.status()['cursors']
        finally:vault.close()
        self.fake.fail=True
        with self.assertRaises(RuntimeError):self.service.capture_then_import(**self.args)
        self.assertEqual((self.service.output/'latest.json').read_bytes(),latest)
        vault=Vault(self.service.root)
        try:self.assertEqual(vault.status()['cursors'],cursor)
        finally:vault.close()
        self.assertEqual(self.service.status()['lastAttempt']['state'],'failed')
        self.assertTrue((self.service.output/'fake-2/snapshot-manifest.json').exists())

    def test_import_failure_rolls_back_checkpoint_and_restores_collector_functions(self):
        self.service.capture_then_import(**self.args)
        latest=(self.service.output/'latest.json').read_bytes()
        original_root=self.fake.private_root;original_json=self.fake.atomic_json
        def bad_import(vault,snapshot,account):
            import_snapshot(vault,snapshot,account)
            raise RuntimeError('failure after imported checkpoint')
        service=CaptureService(self.service.root,collector=self.fake,importer=bad_import)
        with self.assertRaises(RuntimeError):service.capture_then_import(**self.args)
        self.assertEqual((service.output/'latest.json').read_bytes(),latest)
        vault=Vault(service.root)
        try:self.assertEqual(vault.status()['cursors']['wechat-snapshot:trusted-account']['snapshot_id'],json.loads(latest)['snapshot_id'])
        finally:vault.close()
        self.assertIs(self.fake.private_root,original_root);self.assertIs(self.fake.atomic_json,original_json)

    def test_first_failure_does_not_create_success_pointer_or_cursor(self):
        self.fake.fail=True
        with self.assertRaises(RuntimeError):self.service.capture_then_import(**self.args)
        self.assertFalse((self.service.output/'latest.json').exists())
        vault=Vault(self.service.root)
        try:self.assertEqual(vault.status()['cursors'],{})
        finally:vault.close()

    def test_single_lock_rejects_reentry_and_is_released_after_error(self):
        with SyncLock(self.service.root):
            with self.assertRaisesRegex(VaultError,'sync-busy'):self.service.capture_then_import(**self.args)
            self.assertEqual(self.fake.calls,0)
        self.service.capture_then_import(**self.args)
        self.assertEqual(self.fake.calls,1)

    def test_os_lock_blocks_an_independent_python_process(self):
        code='from vault.capture import SyncLock\nimport sys\ntry:\n with SyncLock(sys.argv[1]): print("acquired")\nexcept Exception as e: print(str(e))\n'
        with SyncLock(self.service.root):
            process=subprocess.run([sys.executable,'-c',code,str(self.service.root)],capture_output=True,text=True,timeout=10,cwd=Path(__file__).parent.parent)
        self.assertEqual(process.stdout.strip(),'sync-busy',process.stderr)
        with SyncLock(self.service.root):pass

    def test_explicit_source_and_pid_validation_precede_capture(self):
        with self.assertRaisesRegex(VaultError,'source-mismatch'):
            self.service.capture_then_import(**{**self.args,'source_id':'another'})
        with self.assertRaisesRegex(VaultError,'authorized-weixin-process'):
            self.service.capture_then_import(**self.args,pids=[999])
        self.assertEqual(self.fake.calls,0)

    def test_vendored_modules_are_pinned_and_external_reader_loading_is_denied(self):
        self.assertTrue(verify())
        self.assertEqual(json.loads((ROOT/'vendor-lock.json').read_text(encoding='utf8'))['commit'],COMMIT)
        with self.assertRaisesRegex(VaultError,'external-snapshot-reader-denied'):load_reader(self.base)
        # Imports only; prevent any accidental real process scan in this test.
        with module_context(capture=True) as modules:
            with patch.object(modules['windows_native'],'process_ids',side_effect=AssertionError('no real discovery')):
                self.assertEqual(modules['windows_live'].PROFILE,'windows-chat-capture-v1')


if __name__=='__main__':unittest.main()
