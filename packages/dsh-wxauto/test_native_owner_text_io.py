import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch
from native_owner_text_io import request


class OwnerTextIoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).absolute();(self.root/'config').mkdir()
        self.latest_file=self.root/'snapshots/windows-live/latest.json';self.latest_file.parent.mkdir(parents=True)
        self.snapshot=str(uuid.uuid4())
        self.config={'dataRoot':str(self.root),'accountId':'fixture-account','capture':{'accountUsername':'self','sourceId':'fixture'},
                     'nativeVoiceSources':{'ownerNativeChatKey':'wechat:fixture_peer'}}
        self.write_config();self.latest_file.write_text(json.dumps({'snapshot_id':self.snapshot,'import':{'account_id':'fixture-account'}}))
        self.args={'op':'catalog-page','scanKey':'a'*64};self.calls=[]

    def write_config(self):
        (self.root/'config/wxauto.local.json').write_text(json.dumps(self.config),encoding='utf8')

    def fake_page(self,*args,**kwargs):
        self.calls.append((args,kwargs));return {'fixture':'page'}

    def call(self,args=None,callback=None):
        module=SimpleNamespace(page_native_owner_texts=callback or self.fake_page)
        with patch.dict(sys.modules,{'native_owner_text_catalog':module}):
            return request(self.root,self.args if args is None else args)

    def test_fixed_config_identity_latest_pin_no_capture_or_production_write(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.call(),{'fixture':'page'});args,kw=self.calls[0]
        self.assertEqual(args[0],self.root/'snapshots/windows-live/snapshots'/self.snapshot)
        self.assertEqual(kw['expected_peer_username'],'fixture_peer')
        self.assertEqual(kw['owner_native_chat_key'],'wechat:fixture_peer')
        self.assertEqual(kw['expected_account_username'],'self');self.assertEqual(kw['expected_snapshot_id'],self.snapshot)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_arbitrary_scope_path_operation_or_scan_key_never_calls_catalog(self):
        for change in ({'allowedChatKeys':['wechat:other']},{'ownerNativeChatKey':'wechat:other'},
                       {'op':'extract'},{'scanKey':'private'},{'snapshotId':'../../other'}):
            with self.subTest(change=change),self.assertRaisesRegex(RuntimeError,'request-invalid|snapshot-invalid'):
                self.call({**self.args,**change})
        self.assertEqual(self.calls,[])

    def test_group_self_and_missing_owner_configuration_refused(self):
        for key in ('wechat:group@chatroom','wechat:self',None):
            self.config['nativeVoiceSources']['ownerNativeChatKey']=key;self.write_config()
            with self.assertRaisesRegex(RuntimeError,'owner-config-invalid'):self.call()
        self.assertEqual(self.calls,[])

    def test_latest_wrong_account_or_config_change_during_read_refused(self):
        self.latest_file.write_text(json.dumps({'snapshot_id':self.snapshot,'import':{'account_id':'other'}}))
        with self.assertRaisesRegex(RuntimeError,'latest-account-mismatch'):self.call()
        self.latest_file.write_text(json.dumps({'snapshot_id':self.snapshot,'import':{'account_id':'fixture-account'}}))
        def changed(*args,**kw):
            self.config['accountId']='changed';self.write_config();return {'private':'must not return'}
        with self.assertRaisesRegex(RuntimeError,'configuration-changed'):self.call(callback=changed)

    def test_explicit_snapshot_stays_pinned_and_cannot_retarget_identity(self):
        self.call({**self.args,'snapshotId':self.snapshot,'cursor':'opaque-page-token','pageSize':25})
        kw=self.calls[0][1];self.assertEqual(kw['cursor'],'opaque-page-token');self.assertEqual(kw['page_size'],25)
        self.assertEqual(kw['expected_peer_username'],'fixture_peer')

    def test_nonobject_config_never_reaches_catalog(self):
        file=self.root/'config/wxauto.local.json';file.write_text('[]')
        with self.assertRaisesRegex(RuntimeError,'file-invalid'):self.call()
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
