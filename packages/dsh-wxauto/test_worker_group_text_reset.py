import copy,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from worker_group_text_candidate import WorkerGroupTextBridge
from native_group_text_authority import NativeGroupTextAuthority

KEY='wechat:group_fixture@chatroom'

class FakeAuthority:
 def __init__(self,config,key):self.config,self.key=config,key;self.mode='ui';self.epoch=None;self.ticket=None;self.deferred=[];self.dirty=False;self.closed=False
 def restore(self):return self.status()
 def status(self):
  value={'version':1,'accountId':self.config['accountId'],'nativeChatKey':self.key,'target':'group','mode':self.mode,'authorityEpoch':self.epoch,
   'ticket':self.ticket,'dirty':self.dirty,'deferred':len(self.deferred),'readsStopped':self.mode=='blocked'}
  if self.mode=='blocked':value['blockedReason']='fake-reason'
  return value
 def prepare(self):self.mode='preparing';self.ticket='a'*32;return self.status()
 def route_event(self,e):return {'action':'pass','event':copy.deepcopy(e)} if self.mode=='ui' else {'action':'hint','authorityEpoch':self.epoch}
 def commit(self,ticket,epoch):self.mode='native';self.epoch=epoch
 def paths(self):
  folder=Path(self.config['dataRoot'])/'config/native-group-text/fixture'
  return folder,folder/'authority.json',folder/'inbox.json',folder/'transition.json'
 def close(self):self.closed=True

class ResetTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).absolute();self.config={'dataRoot':str(self.root),'accountId':'account','target':'owner'}
  self.emitted=[]
  self.routes=[{'accountId':'account','nativeChatKey':KEY,'target':'group','sessionId':'session','chatId':'chat','status':'active'}]
  self.bridge=WorkerGroupTextBridge(self.config,publish=lambda e:None,emit=self.emitted.append,controller_factory=FakeAuthority,route_provider=lambda:self.routes)
  self.monitor=SimpleNamespace(routes={'group':'group'},sessions=lambda:{'group':{'isnew':False,'new_count':0}},
     wx=SimpleNamespace(GetSession=lambda:[SimpleNamespace(name='group')]),startup_pending={},startup_uncertain=set(),deferred_direction={},
     current=lambda:'owner',gate=SimpleNamespace(handled={'group':(None,None,0,False)}))
 def call(self,op,**more):return self.bridge.command({'op':'group-text-'+op,'id':'request','target':'group','nativeChatKey':KEY,**more},monitor=self.monitor)

 def test_route_change_after_native_reports_reason_and_reset_returns_to_ui(self):
  self.assertEqual(self.call('reset')['code'],'group-text-reset-not-blocked')
  self.call('prepare');self.assertTrue(self.call('commit',ticket='a'*32,authorityEpoch='b'*32)['ok'])
  folder=self.root/'config/native-group-text/fixture';folder.mkdir(parents=True);(folder/'authority.json').write_text('{}')
  self.routes[0]['sessionId']='hand-edited'
  status=self.call('status')['value']
  self.assertEqual((status['mode'],status['blockedReason']),('blocked','route-session-or-chat-changed'))
  self.assertEqual(self.call('prepare')['code'],'group-text-blocked')
  result=self.call('reset')
  self.assertTrue(result['ok']);self.assertEqual(result['value']['mode'],'ui');self.assertNotIn('blockedReason',result['value'])
  self.assertFalse(folder.exists())
  archive=self.root/result['value']['resetArchive'];self.assertTrue((archive/'authority.json').exists())
  self.assertTrue(archive.parent.name=='native-group-text-reset')
  self.assertTrue(any(e.get('event')=='group-text-reset' for e in self.emitted))
  self.assertTrue(self.call('prepare')['ok'])

 def test_reset_refused_while_delivering_or_registry_unverified(self):
  self.call('prepare');self.call('commit',ticket='a'*32,authorityEpoch='b'*32);self.routes[0]['chatId']='other'
  busy=self.bridge.command({'op':'group-text-reset','id':'r','target':'group','nativeChatKey':KEY},monitor=self.monitor,delivery_active=True)
  self.assertEqual(busy['code'],'group-text-worker-busy')
  self.bridge.inventory_unknown=True
  self.assertEqual(self.call('reset')['code'],'group-text-registry-unverified')
  self.assertEqual(self.call('status')['value']['blockedReason'],'registry-unverified')

 def test_authority_keeps_first_blocked_reason(self):
  a=NativeGroupTextAuthority({'dataRoot':str(self.root),'accountId':'account'},KEY)
  a.mode='preparing';a.close();a._blocked('later-reason')
  value=a.status()
  self.assertEqual((value['mode'],value['blockedReason']),('blocked','closed-while-preparing'))

if __name__=='__main__':unittest.main()
