import copy,json,tempfile,unittest,hashlib,subprocess,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from worker_group_text import WorkerGroupTextBridge
from message_watch import SessionGate,MessageCursor
from native_owner_text_claim import _compact
from native_group_text_authority import NativeGroupTextAuthority

KEY='wechat:group_fixture@chatroom'
def event(id='one',target='group',kind='group',media='text'):
 return {'id':id,'event':'message','target':target,'kind':kind,'mediaType':media,'text':'private fixture '+id,'sender':'fixture','images':[],'files':[]}

class FakeAuthority:
 def __init__(self,config,key):self.config,self.key=config,key;self.mode='ui';self.epoch=None;self.ticket=None;self.deferred=[];self.dirty=False;self.limit=256
 def restore(self):return self.status()
 def status(self):return {'version':1,'accountId':self.config['accountId'],'nativeChatKey':self.key,'target':getattr(self,'target','group'),'mode':self.mode,'authorityEpoch':self.epoch,'ticket':self.ticket,'dirty':self.dirty,'deferred':len(self.deferred),'readsStopped':self.mode=='blocked'}
 def prepare(self):self.mode='preparing';self.ticket='a'*32;return self.status()
 def route_event(self,e):
  if self.mode=='ui':return {'action':'pass','event':copy.deepcopy(e)}
  if self.mode=='native':return {'action':'hint','authorityEpoch':self.epoch}
  if self.mode=='preparing':
   self.dirty=True
   if len(self.deferred)>=self.limit:self.mode='blocked';return {'action':'blocked'}
   self.deferred.append(copy.deepcopy(e));return {'action':'deferred'}
  return {'action':'blocked'}
 def abort(self,ticket):
  if self.mode!='preparing' or ticket!=self.ticket:raise RuntimeError('bad')
  return {'events':copy.deepcopy(self.deferred),'dirty':self.dirty}
 def finish_abort(self,ticket,drained):
  if drained!=len(self.deferred):raise RuntimeError('bad')
  self.mode='ui';self.deferred=[];self.dirty=False
 def commit(self,ticket,epoch):
  if self.mode!='preparing' or ticket!=self.ticket or self.dirty:raise RuntimeError('bad')
  self.mode='native';self.epoch=epoch
 def paths(self):
  folder=Path(self.config['dataRoot'])/'config/native-group-text/fixture'
  return folder,folder/'authority.json',folder/'inbox.json',folder/'transition.json'
 def close(self):self.closed=True

class GroupBridgeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).absolute();self.config={'dataRoot':str(self.root),'accountId':'account','target':'owner'}
  self.published=[];self.emitted=[]
  self.routes=[{'accountId':'account','nativeChatKey':KEY,'target':'group','sessionId':'session','chatId':'chat','status':'active'}]
  self.bridge=WorkerGroupTextBridge(self.config,publish=self.published.append,emit=self.emitted.append,controller_factory=FakeAuthority,route_provider=lambda:self.routes)
  self.monitor=SimpleNamespace(routes={'group':'group'},sessions=lambda:{'group':{'isnew':False,'new_count':0}},
     wx=SimpleNamespace(GetSession=lambda:[SimpleNamespace(name='group')]),startup_pending={},startup_uncertain=set(),deferred_direction={})
  self.monitor.current=lambda:'owner'
  self.monitor.gate=SimpleNamespace(handled={'group':(None,None,0,False)})
 def command(self,op,**more):return {'op':'group-text-'+op,'id':'request','target':'group','nativeChatKey':KEY,**more}
 def call(self,op,**more):return self.bridge.command(self.command(op,**more),monitor=self.monitor)
 def test_default_ui_keeps_all_exact_events_no_files_and_no_social(self):
  events=[event(),event('voice',media='voice'),event('owner','owner','friend')]
  self.bridge.route_batch(events);self.assertEqual(self.published,events);self.assertEqual(self.bridge.status()[0]['mode'],'ui')
  self.assertFalse((self.root/'config').exists())
 def test_prepare_defer_abort_drains_once_then_finish_ui(self):
  result=self.call('prepare');self.assertEqual(result['value']['version'],1)
  e=event();self.bridge.route(e);self.assertEqual(self.published,[])
  self.assertNotIn(e['text'],str(self.bridge.status()))
  result=self.call('abort',ticket='a'*32);self.assertTrue(result['ok']);self.assertEqual(self.published,[e]);self.assertEqual(result['value']['mode'],'ui')
  self.assertFalse(self.call('abort',ticket='a'*32)['ok']);self.assertEqual(self.published,[e])
 def test_native_hint_no_text_and_other_media_private_unchanged(self):
  self.call('prepare');result=self.call('commit',ticket='a'*32,authorityEpoch='b'*32);self.assertTrue(result['ok'])
  self.bridge.route_batch([event(),event('image',media='image'),event('owner','owner','friend')])
  hint=next(x for x in self.emitted if x.get('event')=='native-group-text-observed')
  self.assertNotIn('text',hint);self.assertEqual(hint['nativeChatKey'],KEY);self.assertEqual(len(self.published),2)
 def test_prepare_checks_unread_before_navigation_and_all_uncertainty(self):
  for attr,value in [('startup_pending',{'group':{'isnew':True}}),('startup_uncertain',{'group'}),('deferred_direction',{'group':{'token':False}})]:
   old=getattr(self.monitor,attr);setattr(self.monitor,attr,value)
   self.assertEqual(self.call('prepare')['code'],'group-text-prepare-not-quiet');setattr(self.monitor,attr,old)
  self.monitor.sessions=lambda:{'group':{'isnew':True,'new_count':1}}
  self.assertFalse(self.call('prepare')['ok']);self.assertEqual(self.bridge.status()[0]['mode'],'ui')
 def test_prepare_delivery_independent_and_exact_schema(self):
  for kw in ({'delivery_active':True},{'independent':True}):
   result=self.bridge.command(self.command('prepare'),monitor=self.monitor,**kw);self.assertEqual(result['code'],'group-text-worker-busy')
  self.assertEqual(self.bridge.command({**self.command('prepare'),'path':'arbitrary'},monitor=self.monitor)['code'],'group-text-request-invalid')
 def test_shutdown_and_overflow_persist_exact_events_not_owner(self):
  self.call('prepare');self.bridge.controllers[KEY].limit=1
  first,second=event('one'),event('two');self.bridge.route_batch([first,second,event('owner','owner','friend')])
  self.assertEqual(self.published,[event('owner','owner','friend')]);self.assertEqual(self.bridge.status()[0]['mode'],'blocked')
  files=list(self.root.rglob('deferred/*.json'));self.assertEqual(len(files),1)
  self.assertEqual(json.loads(files[0].read_text())['events'],[first,second]);self.bridge.close();self.assertEqual(len(list(self.root.rglob('deferred/*.json'))),1)
 def test_close_preparing_saves_pending_and_normal_close_has_no_file(self):
  self.call('prepare');self.bridge.route(event());self.bridge.close();self.bridge.close()
  self.assertEqual(json.loads(next(self.root.rglob('deferred/*.json')).read_text())['events'],[event()])
  self.assertTrue(self.bridge.controllers[KEY].closed)

 def test_prepare_missing_duplicate_visible_target_and_empty_pending_refused(self):
  for rows in ([],[SimpleNamespace(name='group'),SimpleNamespace(name='group')]):
   self.monitor.wx.GetSession=lambda:rows
   self.assertEqual(self.call('prepare')['code'],'group-text-prepare-not-quiet')
  self.monitor.wx.GetSession=lambda:[SimpleNamespace(name='group')]
  self.monitor.startup_pending={'group':{}}
  self.assertEqual(self.call('prepare')['code'],'group-text-prepare-not-quiet')
 def test_persistence_failure_stays_blocked_and_never_claims_save(self):
  self.call('prepare');self.bridge.controllers[KEY].limit=0
  with patch.object(self.bridge,'_persist',side_effect=OSError('fixture-private-path')):self.bridge.route(event())
  self.assertEqual(self.bridge.deferred[KEY],[event()]);self.assertNotIn(KEY,self.bridge.saved)
  self.assertEqual(self.emitted[-1]['code'],'group-text-deferral-persistence-failed');self.assertNotIn('fixture-private-path',str(self.emitted))
 def test_invalid_marker_and_route_revocation_do_not_fallback(self):
  self.bridge.controllers[KEY].mode='blocked';self.assertEqual(self.bridge.monitor_authority('group')['mode'],'blocked')
  self.routes[0]['status']='pending';self.bridge.sync_routes();self.assertEqual(self.bridge.monitor_authority('group')['mode'],'blocked')
  self.assertTrue(self.call('status')['ok']);self.assertFalse(self.call('prepare')['ok'])
 def test_batch_passthrough_preserves_owner_overflow_batch_boundary(self):
  batches=[];self.bridge.publish_batch=lambda events:batches.append(events)
  events=[event('one','owner','friend'),event('two','owner','friend')]
  self.bridge.route_batch(events);self.assertEqual(batches,[events]);self.assertEqual(self.published,[])
 def test_trusted_rename_preserves_controller_epoch_but_session_chat_change_blocks(self):
  controller=self.bridge.controllers[KEY];controller.mode='native';controller.epoch='b'*32
  self.routes[0]['target']='renamed';controller.target='renamed'
  self.assertTrue(self.bridge.sync_routes());self.assertIs(self.bridge.controllers[KEY],controller)
  self.assertNotIn('group',self.bridge.targets)
  self.assertEqual(self.bridge.monitor_authority('renamed')['mode'],'native')
  self.assertEqual(self.bridge.monitor_authority('renamed')['authorityEpoch'],'b'*32)
  self.routes[0]['sessionId']='foreign-session';self.bridge.sync_routes()
  self.assertEqual(self.bridge.monitor_authority('renamed')['mode'],'blocked')
 def test_real_authority_prepare_abort_and_shutdown_release_only_owned_fd(self):
  self.config['capture']={'enabled':True,'accountUsername':'wxid_fixture_own','sourceId':'fixture'}
  directory=self.root/'config';directory.mkdir()
  (directory/'group-routes.local.json').write_text(json.dumps({'version':1,'accountId':'account','groups':self.routes}))
  self.bridge=WorkerGroupTextBridge(self.config,publish=self.published.append,emit=self.emitted.append)
  prepared=self.call('prepare');self.assertTrue(prepared['ok'])
  controller=self.bridge.controllers[KEY];lock=controller.paths()[0]/'transition.lock'
  self.assertTrue(lock.exists());self.assertIsNotNone(controller._lease_fd)
  self.bridge.route(event())
  result=self.call('abort',ticket=prepared['value']['ticket']);self.assertTrue(result['ok']);self.assertEqual(self.published,[event()])
  self.assertFalse(lock.exists());self.assertIsNone(controller._lease_fd)
  self.assertTrue(self.call('prepare')['ok']);self.bridge.route(event('shutdown'))
  self.bridge.close();self.assertIsNone(controller._lease_fd);self.assertTrue(lock.exists())
  self.assertEqual(json.loads(next(controller.paths()[0].glob('deferred/*.json')).read_text())['events'],[event('shutdown')])

 def test_auto_read_new_current_bubble_or_changed_preview_cannot_be_baselined(self):
  self.monitor.current=lambda:'group';self.monitor.cursor=MessageCursor();self.monitor.cursor.seed('group',[])
  row=SimpleNamespace(Name='new text',ClassName='mmui::ChatTextItemView',GetRuntimeId=lambda:(99,),GetChildren=lambda:[])
  self.monitor.controls=lambda:[row]
  for cls in ('mmui::ChatTextItemView','mmui::ChatImageItemView'):
   row.ClassName=cls
   self.assertEqual(self.call('prepare')['code'],'group-text-prepare-not-quiet')
  self.assertEqual(self.monitor.cursor.rows['group'],[])
  self.monitor.controls=lambda:[];prepared=self.call('prepare');self.assertTrue(prepared['ok'])
  self.monitor.sessions=lambda:{'group':{'isnew':False,'new_count':0,'content':'new read preview','time':'12:00'}}
  self.monitor.gate.handled['group']=SessionGate.fingerprint(self.monitor.sessions()['group'])
  result=self.call('commit',ticket='a'*32,authorityEpoch='b'*32)
  self.assertEqual(result['code'],'group-text-prepare-not-quiet');self.assertEqual(self.bridge.status()[0]['mode'],'preparing')

 def test_missing_or_invalid_optional_registry_without_markers_keeps_legacy_ui(self):
  for invalid in (False,True):
   if invalid:
    folder=self.root/'config';folder.mkdir(exist_ok=True);(folder/'group-routes.local.json').write_text('invalid json')
   bridge=WorkerGroupTextBridge(self.config,publish=self.published.append,emit=self.emitted.append)
   self.assertEqual(bridge.monitor_authority('group')['mode'],'ui')
   bridge.route(event());self.assertEqual(self.published[-1],event());bridge.close()
  self.assertFalse((self.root/'config/native-group-text').exists())

 def test_protected_marker_missing_registry_blocks_exact_target_not_owner_media(self):
  folder=self.root/'config/native-group-text'/hashlib.sha256(KEY.encode()).hexdigest()[:32];folder.mkdir(parents=True)
  body={'format':'whale-native-group-text-authority','version':1,'accountId':'account','nativeChatKey':KEY,'target':'group'}
  body['digest']=hashlib.sha256(_compact(body)).hexdigest();(folder/'authority.json').write_text(json.dumps(body))
  bridge=WorkerGroupTextBridge(self.config,publish=self.published.append,emit=self.emitted.append)
  self.assertEqual(bridge.monitor_authority('group')['mode'],'blocked')
  self.assertEqual(bridge.monitor_authority('other-group')['mode'],'ui')
  bridge.route_batch([event('owner','owner','friend'),event('image',media='image')])
  self.assertEqual(len(self.published),2);bridge.close()
  self.assertTrue((folder/'authority.json').exists())

 def test_unmapped_or_corrupt_residual_blocks_group_text_and_saves_real_deferred(self):
  folder=self.root/'config/native-group-text'/'unknown-residual';folder.mkdir(parents=True)
  (folder/'untrusted.tmp').write_text('not read as a payload')
  bridge=WorkerGroupTextBridge(self.config,publish=self.published.append,emit=self.emitted.append)
  self.assertEqual(bridge.monitor_authority('any-group')['mode'],'blocked')
  bridge.route_batch([event(),event('owner','owner','friend'),event('image',media='image')])
  self.assertEqual(len(self.published),2)
  files=list((self.root/'config/native-group-text/unmapped/deferred').glob('*.json'))
  self.assertEqual(len(files),1);self.assertEqual(json.loads(files[0].read_text())['events'],[event()])
  bridge.close()

 def test_real_node_publisher_commit_archives_survive_bridge_restart(self):
  self.config['capture']={'enabled':True,'accountUsername':'wxid_fixture_own','sourceId':'fixture'}
  directory=self.root/'config';directory.mkdir()
  (directory/'group-routes.local.json').write_text(json.dumps({'version':1,'accountId':'account','groups':self.routes}))
  row={'nativeId':'wechat-server:10','serverId':'10','nativeChatKey':KEY,'senderId':'wxid_member',
       'senderDisplayName':'Member','senderNumericId':7,'rawText':'old fixture','renderedText':'old fixture',
       'renderStatus':'raw-preserved','timestampSeconds':1900,'localId':1,'snapshotSourceId':'message_0/table/1','direction':'incoming'}
  catalog={'source':'authenticated-native-group-text-catalog','accountId':'account','nativeChatKey':KEY,
      'scopeDigest':hashlib.sha256(_compact(['account',KEY])).hexdigest(),'snapshotId':str(uuid.uuid4()),'complete':True,
      'messages':[row],'unresolved':[],'observedNativeIds':['wechat-server:10']}
  def factory(config,key):return NativeGroupTextAuthority(config,key,catalog_reader=lambda *a,**kw:copy.deepcopy(catalog),clock=lambda:2000.123)
  self.bridge=WorkerGroupTextBridge(self.config,publish=self.published.append,emit=self.emitted.append,controller_factory=factory)
  prepared=self.call('prepare');self.assertTrue(prepared['ok'])
  script="import fs from 'node:fs';import {createGroupTextBaselinePublisher}from './group-text-baseline.mjs';const v=JSON.parse(fs.readFileSync(0,'utf8'));const p=createGroupTextBaselinePublisher({config:v.config,nativeChatKey:v.key});const token=p.prepare(v.catalog,{cutoverAtMs:v.cutoverAtMs});p.publish(token);process.stdout.write(JSON.stringify({epoch:token.authorityEpoch}));"
  result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps({'config':self.config,'key':KEY,'catalog':catalog,'cutoverAtMs':prepared['value']['preparedAtMs']}),
      text=True,encoding='utf8',capture_output=True,cwd=Path(__file__).parent,timeout=15)
  self.assertEqual(result.returncode,0,result.stderr);epoch=json.loads(result.stdout)['epoch']
  committed=self.call('commit',ticket=prepared['value']['ticket'],authorityEpoch=epoch);self.assertTrue(committed['ok'])
  folder=self.bridge.controllers[KEY].paths()[0];self.assertTrue((folder/'native-group-text-cutover').is_dir())
  (folder/'deferred').mkdir()
  restarted=WorkerGroupTextBridge(self.config,publish=self.published.append,emit=self.emitted.append,controller_factory=factory)
  self.assertEqual(restarted.monitor_authority('group')['mode'],'native')
  self.assertFalse(restarted.inventory_unknown);self.assertFalse(restarted.pending_recovery_keys)
  restarted.close();self.bridge.close()

 def test_terminal_ui_transition_empty_deferred_and_archives_do_not_block_old_ui(self):
  self.config['capture']={'enabled':True,'accountUsername':'wxid_fixture_own','sourceId':'fixture'}
  directory=self.root/'config';directory.mkdir()
  registry=directory/'group-routes.local.json';registry.write_text(json.dumps({'version':1,'accountId':'account','groups':self.routes}))
  self.bridge=WorkerGroupTextBridge(self.config,publish=self.published.append,emit=self.emitted.append)
  prepared=self.call('prepare');self.assertTrue(self.call('abort',ticket=prepared['value']['ticket'])['ok'])
  folder=self.bridge.controllers[KEY].paths()[0];(folder/'deferred').mkdir();(folder/'native-group-text-cutover').mkdir()
  registry.unlink();restarted=WorkerGroupTextBridge(self.config,publish=self.published.append,emit=self.emitted.append)
  self.assertEqual(restarted.monitor_authority('group')['mode'],'ui');self.assertFalse(restarted.inventory)
  restarted.close();self.bridge.close()

 def test_nonempty_deferred_is_only_a_known_group_guard(self):
  directory=self.root/'config';directory.mkdir()
  (directory/'group-routes.local.json').write_text(json.dumps({'version':1,'accountId':'account','groups':self.routes}))
  folder=directory/'native-group-text'/hashlib.sha256(KEY.encode()).hexdigest()[:32]/'deferred';folder.mkdir(parents=True)
  (folder/'pending.json').write_text('private unread recovery payload is not parsed')
  restarted=WorkerGroupTextBridge(self.config,publish=self.published.append,emit=self.emitted.append)
  self.assertEqual(restarted.monitor_authority('group')['mode'],'blocked')
  self.assertEqual(restarted.monitor_authority('other-group')['mode'],'ui');self.assertFalse(restarted.inventory_unknown)
  restarted.close()

if __name__=='__main__':unittest.main()

class StagedWorkerTests(unittest.TestCase):
 def setUp(self):
  import importlib.util
  import test_worker_social
  self.fixture=test_worker_social.WorkerSocialTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
  self.fixture_module=test_worker_social
  file=Path(__file__).resolve().parents[2]/'artifacts/dev-group-text/worker.py'
  spec=importlib.util.spec_from_file_location('staged_group_text_worker',file)
  self.staged=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.staged)
 def run_staged(self,*args,**kwargs):
  with patch.object(self.fixture_module,'worker',self.staged):return self.fixture.run_worker(*args,**kwargs)
 def test_staged_defaults_preserve_real_owner_and_group_envelopes_and_heartbeat(self):
  inbound={'owner':[{'id':'owner-ui','text':'owner fixture','sender':'owner'}],
           'group-a':[{'id':'group-ui','text':'group fixture','sender':'member'}]}
  events,operations,_=self.run_staged([None,None],inbound=inbound,clock_step=11)
  actual=[e for e in events if e.get('event')=='message'];self.assertEqual({e['text']for e in actual},{'owner fixture','group fixture'})
  ready=next(e for e in events if e.get('event')=='ready');self.assertEqual(ready['groupTextAuthorities'],[])
  self.assertEqual(ready['groupTextObservationVersion'],1)
  self.assertTrue(all('groupTextAuthorities'in e for e in events if e.get('event')=='heartbeat'))
 def test_staged_untrusted_native_hint_is_not_forwarded_and_old_stop_works(self):
  events,_,_=self.run_staged([None],inbound={'group-a':[{'id':'unbound','observation':'native-group-text',
      'nativeChatKey':KEY,'authorityEpoch':'b'*32}]})
  self.assertFalse(any(e.get('event')=='native-group-text-observed'for e in events))
  self.assertTrue(any(e.get('event')=='native-group-text-blocked'for e in events))
 def test_staged_group_command_unregistered_route_denied_without_send(self):
  events,operations,_=self.run_staged([{'op':'group-text-prepare','id':'prepare','target':'group-a','nativeChatKey':KEY}])
  result=next(e for e in events if e.get('event')=='group-text-result');self.assertFalse(result['ok'])
  self.assertFalse(any(op[0]=='send'for op in operations))

 def test_staged_real_main_successful_private_ops_callback_hint_and_cleanup(self):
  import sys
  from worker_group_text import WorkerGroupTextBridge as RealBridge
  captured={'bridges':[],'options':[],'ownerBatches':[]}
  original_owner=self.staged.WorkerOwnerTextBridge
  def owner_factory(*args,**kw):
   bridge=original_owner(*args,**kw);original=bridge.route_batch
   def route_batch(events):captured['ownerBatches'].append(copy.deepcopy(events));return original(events)
   bridge.route_batch=route_batch;return bridge
  def factory(config,**kw):
   # The existing SDK fake intentionally has no UI properties; extend only
   # this injected fake with the actual public monitor/session contract.
   module=sys.modules['message_watch'];old=module.MainWindowMonitor
   def construct(wx,routes,**options):
    captured['options'].append(options);monitor=old(wx,routes,**options)
    monitor.wx=wx;monitor.routes=routes
    wx.GetSession=lambda:[SimpleNamespace(name='group-a',info={'isnew':False,'new_count':0})]
    monitor.sessions=lambda:{'group-a':{'isnew':False,'new_count':0}}
    monitor.startup_pending={};monitor.startup_uncertain=set();monitor.deferred_direction={}
    monitor.current=lambda:wx.ChatInfo()['chat_name']
    monitor.gate=SimpleNamespace(handled={'group-a':(None,None,0,False)})
    return monitor
   module.MainWindowMonitor=construct
   config['accountId']='account'
   route={'accountId':'account','nativeChatKey':KEY,'target':'group-a','sessionId':'session','chatId':'chat','status':'active'}
   bridge=RealBridge(config,**kw,controller_factory=FakeAuthority,route_provider=lambda:[route])
   bridge.controllers[KEY].target='group-a';captured['bridges'].append(bridge);return bridge
  def command(op,**kw):return {'op':'group-text-'+op,'id':op,'target':'group-a','nativeChatKey':KEY,**kw}
  steps=[command('status'),command('prepare'),command('abort',ticket='a'*32),command('prepare'),
         command('commit',ticket='a'*32,authorityEpoch='b'*32),None,None]
  inbound={'group-a':[{'id':'native-ui-token','observation':'native-group-text','nativeChatKey':KEY,'authorityEpoch':'b'*32}],
           'owner':[{'id':'o1','text':'owner one','sender':'owner'},{'id':'o2','text':'owner two','sender':'owner'}]}
  with patch.object(self.staged,'WorkerGroupTextBridge',side_effect=factory),patch.object(self.staged,'WorkerOwnerTextBridge',side_effect=owner_factory):
   events,operations,_=self.run_staged(steps,inbound=inbound,clock_step=11)
  replies=[e for e in events if e.get('event')=='group-text-result'];self.assertEqual(len(replies),5)
  self.assertTrue(all(e['ok'] and e['value']['version']==1 for e in replies))
  self.assertEqual(replies[-1]['value']['mode'],'native')
  self.assertTrue(callable(captured['options'][0]['native_group_text_authority']))
  hints=[e for e in events if e.get('event')=='native-group-text-observed'];self.assertEqual(len(hints),1)
  self.assertNotIn('text',hints[0]);self.assertEqual(hints[0]['authorityEpoch'],'b'*32)
  ready=next(e for e in events if e.get('event')=='ready');self.assertEqual(ready['groupTextAuthorities'][0]['mode'],'ui')
  self.assertTrue(any(e.get('event')=='heartbeat' and e['groupTextAuthorities'][0]['mode']=='native'for e in events))
  owner_batches=[b for b in captured['ownerBatches'] if b and b[0].get('target')=='owner']
  self.assertEqual(len(owner_batches),1);self.assertEqual(len(owner_batches[0]),2)
  self.assertEqual(len([e for e in events if e.get('event')=='message' and e.get('target')=='owner']),2)
  self.assertTrue(captured['bridges'][0].controllers[KEY].closed)
  self.assertFalse(any(op[0]=='send'for op in operations))
