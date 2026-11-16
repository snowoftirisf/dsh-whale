import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from .store import Vault, VaultError, restore


class BackupEphemeralTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.base=Path(self.temp.name);self.root=self.base/'data';self.v=Vault(self.root)
        self.module=(Path(__file__).parents[1]/'native-voice-inbox.mjs').as_uri()
        self.node('initialize',self.root)
    def tearDown(self):self.v.close();self.temp.cleanup()
    def node(self,op,root):
        code="import {createNativeVoiceInbox} from "+json.dumps(self.module)+";const i=createNativeVoiceInbox({dataRoot:process.argv[1],accountId:'fixture'});const c={source:'authenticated-native-voice-catalog',accountId:'fixture',snapshotId:'fixture',voices:[]};if(process.argv[2]==='initialize')i.initialize(c,{nativeChatKeys:['wechat:group@chatroom']});else i.enqueue(c);console.log(JSON.stringify(i.status()));"
        result=subprocess.run(['node','--input-type=module','-e',code,str(root.resolve()),op],capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stderr)
        return json.loads(result.stdout)
    def lock_and_business_files(self):
        for name in ('config/native-voice-inbox.json.lock','memory/provenance.json.lock','config/native-voice-inbox.json.12345678-1234-1234-1234-123456789abc.tmp','memory/.provenance.json.12345678-1234-1234-1234-123456789abc.tmp'):
            file=self.root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('in-progress',encoding='utf8')
        for name in ('config/business.lock','memory/user.tmp','media/important.lock','snapshots/windows-live/vault/sync.lock'):
            file=self.root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'keep-business-file')
    def test_actual_node_inbox_works_after_backup_restore_while_exclusive_marker_present(self):
        self.lock_and_business_files()
        # The exact marker used by Node's O_EXCL coordination is live in source.
        with self.assertRaisesRegex(AssertionError,'inbox-busy'):self.node('enqueue',self.root)
        backup=self.base/'backup';self.v.backup(backup);destination=self.base/'restored';restore(backup,destination)
        self.assertTrue(self.node('enqueue',destination)['initialized'])
        self.assertFalse((destination/'config/native-voice-inbox.json.lock').exists())
        self.assertFalse((destination/'memory/provenance.json.lock').exists())
        self.assertFalse((destination/'config/native-voice-inbox.json.12345678-1234-1234-1234-123456789abc.tmp').exists())
        self.assertFalse((destination/'memory/.provenance.json.12345678-1234-1234-1234-123456789abc.tmp').exists())
        for name in ('config/business.lock','memory/user.tmp','media/important.lock','snapshots/windows-live/vault/sync.lock'):
            self.assertEqual((destination/name).read_bytes(),b'keep-business-file')
        self.assertEqual((destination/'config/native-voice-inbox.json').read_bytes(),(self.root/'config/native-voice-inbox.json').read_bytes())
    def legacy_backup(self):
        backup=self.base/'legacy';self.v.backup(backup)
        file=backup/'config/native-voice-inbox.json.lock';file.write_bytes(b'legacy-exclusive-lock')
        manifest=backup/'backup-manifest.json';value=json.loads(manifest.read_text());value['files'].append({'path':'config/native-voice-inbox.json.lock','size':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()});manifest.write_text(json.dumps(value))
        return backup,file
    def test_old_backup_lock_is_checked_but_not_revived(self):
        backup,_=self.legacy_backup();destination=self.base/'restored';result=restore(backup,destination)
        self.assertFalse((destination/'config/native-voice-inbox.json.lock').exists());self.assertTrue(self.node('enqueue',destination)['initialized'])
        self.assertEqual(result['skippedEphemeralFiles'],1)
    def test_old_ephemeral_lock_checksum_still_must_validate_before_restore(self):
        backup,file=self.legacy_backup();file.write_bytes(b'tampered')
        with self.assertRaisesRegex(VaultError,'backup-checksum-mismatch'):restore(backup,self.base/'restored')
        self.assertFalse((self.base/'restored').exists())
    def test_ephemeral_filter_is_exact_not_extension_based(self):
        from .store import _ephemeral_backup_path
        uid='12345678-1234-1234-1234-123456789abc'
        for name in ('config/group-routes.local.json.'+uid+'.tmp','config/wxauto.local.json.'+uid+'.tmp','config/auto-group-inbox/'+'a'*64+'.json.'+uid+'.tmp','snapshots/realtime-inbox/'+uid+'.tmp','snapshots/realtime-evidence/'+uid+'.tmp','snapshots/realtime-outgoing/'+uid+'.tmp'):
            self.assertTrue(_ephemeral_backup_path(name),name)
        for name in ('config/customer.json.lock','memory/OTHER.json.lock','memory/provenance.json.tmp','config/native-voice-inbox.json.saved.tmp','snapshots/windows-live/vault/sync.lock','media/voice-recording.tmp','outbox/file.lock'):
            self.assertFalse(_ephemeral_backup_path(name),name)

if __name__=='__main__':unittest.main()
