import json,sys,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import native_group_text_io as m
from vault.test_snapshot_import import make_snapshot,OWNER,TALKER

class IoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).absolute();(self.root/'config').mkdir();self.snap=str(uuid.uuid4())
        self.config={'dataRoot':str(self.root),'accountId':'account','target':'owner','capture':{'accountUsername':OWNER,'sourceId':'fixture'}}
        self.route={'version':1,'accountId':'account','groups':[{'accountId':'account','nativeChatKey':'wechat:'+TALKER,'target':'group','sessionId':'group-session','status':'active'}]}
        self.write();self.latest=self.root/'snapshots/windows-live/latest.json';self.latest.parent.mkdir(parents=True)
        self.latest.write_text(json.dumps({'snapshot_id':self.snap,'import':{'account_id':'account'}}))
        self.args={'op':'catalog-page','nativeChatKey':'wechat:'+TALKER,'scanKey':'a'*64};self.calls=[]
    def write(self):
        (self.root/'config/wxauto.local.json').write_text(json.dumps(self.config))
        (self.root/'config/group-routes.local.json').write_text(json.dumps(self.route))
    def fake(self,*args,**kw):self.calls.append((args,kw));return {'fixture':'page'}
    def call(self,args=None,callback=None):
        with patch.dict(sys.modules,{'native_group_text_catalog':SimpleNamespace(page_native_group_texts=callback or self.fake)}):return m.request(self.root,args or self.args)
    def test_fixed_active_route_and_readonly(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.call(),{'fixture':'page'})
        args,kw=self.calls[0];self.assertEqual(kw['native_group_chat_key'],'wechat:'+TALKER);self.assertEqual(kw['account_id'],'account')
        self.assertEqual(kw['expected_snapshot_id'],self.snap)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
    def test_unknown_pending_ambiguous_foreign_or_owner_route_denied(self):
        for mode in ('pending','duplicate','foreign','owner'):
            original=json.loads(json.dumps(self.route))
            if mode=='pending':self.route['groups'][0]['status']='pending'
            if mode=='duplicate':self.route['groups'].append(self.route['groups'][0].copy())
            if mode=='foreign':self.route['accountId']='foreign'
            if mode=='owner':self.route['groups'][0]['target']='owner'
            self.write()
            with self.assertRaisesRegex(RuntimeError,'route-'):self.call()
            self.route=original
        self.assertEqual(self.calls,[])
    def test_untrusted_fields_and_paths_refused(self):
        for change in ({'accountId':'foreign'},{'path':'elsewhere'},{'nativeChatKey':'wechat:owner'},{'snapshotId':'../../wrong'},{'scanKey':'secret'}):
            with self.assertRaises(RuntimeError):self.call({**self.args,**change})
        self.assertEqual(self.calls,[])
    def test_configuration_and_registry_cas(self):
        def changed(*args,**kwargs):
            self.route['groups'][0]['status']='pending';self.write();return {'secret':'must not return'}
        with self.assertRaisesRegex(RuntimeError,'configuration-changed'):self.call(callback=changed)
    def test_latest_account_and_snapshot_are_bound(self):
        self.latest.write_text(json.dumps({'snapshot_id':self.snap,'import':{'account_id':'foreign'}}))
        with self.assertRaisesRegex(RuntimeError,'latest-account'):self.call()
        self.call({**self.args,'snapshotId':self.snap})
        self.assertEqual(self.calls[0][1]['expected_snapshot_id'],self.snap)
    def test_real_authenticated_sqlite_fixture_through_io(self):
        snapshots=self.latest.parent/'snapshots';snapshots.mkdir()
        root=make_snapshot(snapshots/self.snap,hash_manifest=True)
        file=root/'snapshot-manifest.json';manifest=json.loads(file.read_text());manifest['snapshot_id']=self.snap;manifest['source_id']='fixture';file.write_text(json.dumps(manifest))
        result=m.request(self.root,self.args)
        self.assertEqual(result['source'],'authenticated-native-group-text-catalog-page')
        self.assertEqual(len(result['messages']),1)
        self.assertEqual(result['snapshotId'],self.snap)

if __name__=='__main__':unittest.main()
