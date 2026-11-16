import copy,unittest
from types import SimpleNamespace
from unittest.mock import patch
from dynamic_group_watch import register_group
from message_watch import MainWindowMonitor


class Row:
    ClassName='mmui::ChatTextItemView'
    def __init__(self,key,text):self.key,self.Name=key,text
    def GetRuntimeId(self):return [self.key]
    def GetChildren(self):return []


def fixture(recover=False,unread=0):
    routes={'owner':'friend'};seen={'owner':{'old-event'}};registry={}
    state={'current':'owner','calls':[],'kind':{'owner':'friend','new group':'group'},
        'rows':{'owner':[Row(1,'old private')],'new group':[Row(2,'old group')]},
        'info':{'owner':{'isnew':False,'new_count':0,'time':'12:00','content':'old private'},
                'new group':{'isnew':bool(unread),'new_count':unread,'time':'12:01','content':'old group'}}}
    def info():return {'chat_name':state['current'],'chat_type':state['kind'][state['current']]}
    def sessions():return [SimpleNamespace(name=name,info=dict(value)) for name,value in state['info'].items()]
    wx=SimpleNamespace(ChatInfo=info,GetSession=sessions)
    box=SimpleNamespace(Exists=lambda _:True,GetChildren=lambda:state['rows'][state['current']],
        GetScrollPattern=lambda:SimpleNamespace(VerticallyScrollable=False),WheelDown=lambda **kw:None)
    wx._api=SimpleNamespace(_chat_api=SimpleNamespace(msgbox=box))
    monitor=MainWindowMonitor(wx,routes,recover_startup_unread=recover)
    def select(name):
        state['calls'].append(name);state['current']=name
        state['info'][name].update(isnew=False,new_count=0)
        if state.get('fail')==name:raise RuntimeError('synthetic select failure')
    monitor.select=select
    monitor.cursor.seed('owner',state['rows']['owner']);monitor.gate.acknowledge('owner',state['info']['owner'])
    def register(**extra):
        args={'target':'new group','native_chat_key':'wechat:12345@chatroom','account_id':'own-account','expected_account_id':'own-account',**extra}
        return register_group(wx,monitor,routes,seen,registry,**args)
    return SimpleNamespace(wx=wx,monitor=monitor,routes=routes,seen=seen,registry=registry,state=state,register=register)


class DynamicGroupTests(unittest.TestCase):
    def test_read_group_baseline_is_not_input_and_next_real_message_uses_existing_reader(self):
        f=fixture();old=copy.deepcopy(f.monitor.cursor.rows['owner']);gate=copy.deepcopy(f.monitor.gate.handled)
        result=f.register()
        self.assertTrue(result['ok']);self.assertTrue(result['actualUiGroupConfirmed']);self.assertTrue(result['routeAdded'])
        self.assertEqual(f.state['current'],'owner');self.assertEqual(f.state['calls'],['new group','owner'])
        self.assertEqual(f.monitor.cursor.rows['owner'],old);self.assertEqual(f.monitor.gate.handled,gate)
        self.assertEqual(f.monitor.cursor.current,'owner');self.assertEqual(f.seen,{'owner':{'old-event'},'new group':set()})
        self.assertEqual(f.registry['new group'],'wechat:12345@chatroom');self.assertEqual(f.monitor.startup_pending,{})
        f.state['rows']['new group'].append(Row(3,'actually new after registration'))
        f.state['info']['new group'].update(isnew=True,new_count=1,content='actually new after registration',time='12:02')
        with patch.object(f.monitor,'message_direction',lambda row:'friend'),patch.object(f.monitor,'parse_incoming',
                lambda row:SimpleNamespace(attr='friend',type='text',content=row.Name,sender='member')):
            self.assertEqual([r['text'] for r in f.monitor.read('new group')],['actually new after registration'])
            self.assertEqual(f.monitor.read('new group'),[])

    def test_unread_with_recovery_disabled_is_pending_before_navigation_and_keeps_badge(self):
        f=fixture(unread=1);before=copy.deepcopy(f.state['info']);cursor=copy.deepcopy(f.monitor.cursor.__dict__)
        result=f.register()
        self.assertEqual(result['code'],'dynamic-unread-recovery-required');self.assertTrue(result['pending'])
        self.assertEqual(f.state['calls'],[]);self.assertEqual(f.state['info'],before)
        self.assertEqual(f.routes,{'owner':'friend'});self.assertEqual(f.registry,{})
        self.assertEqual(f.monitor.cursor.__dict__,cursor);self.assertNotIn('new group',f.seen)

    def test_real_unread_with_existing_recovery_preserves_preview_without_seeding_or_ack(self):
        f=fixture(recover=True,unread=1);before=copy.deepcopy(f.state['info']['new group']);gate=copy.deepcopy(f.monitor.gate.handled)
        result=f.register()
        self.assertTrue(result['ok']);self.assertTrue(result['startupUnreadPreserved'])
        self.assertNotIn('new group',f.monitor.cursor.rows)
        self.assertEqual(f.monitor.startup_pending['new group'],before);self.assertEqual(f.monitor.gate.handled,gate)
        with patch.object(f.monitor,'message_direction',lambda row:'friend'),patch.object(f.monitor,'parse_incoming',
                lambda row:SimpleNamespace(attr='friend',type='text',content=row.Name,sender='member')):
            self.assertEqual([r['text'] for r in f.monitor.read('new group')],['old group'])
            self.assertEqual(f.monitor.read('new group'),[])

    def test_duplicate_visible_name_or_identity_conflicts_never_navigate(self):
        f=fixture();original=f.wx.GetSession;f.wx.GetSession=lambda:original()+[original()[-1]]
        self.assertEqual(f.register()['code'],'dynamic-group-session-missing-or-ambiguous');self.assertEqual(f.state['calls'],[])
        for kwargs,code in [({'account_id':'another'},'dynamic-group-account-mismatch'),
                            ({'native_chat_key':'wechat:person'},'dynamic-group-identity-invalid'),
                            ({'target':'owner'},'dynamic-group-existing-friend-denied')]:
            f=fixture();self.assertEqual(f.register(**kwargs)['code'],code);self.assertEqual(f.state['calls'],[])
        f=fixture();f.registry['new group']='wechat:another@chatroom'
        self.assertEqual(f.register()['code'],'dynamic-group-native-conflict');self.assertEqual(f.state['calls'],[])

    def test_failed_confirmation_restores_chat_and_removes_temporary_route(self):
        for fail in ['new group','wrong-kind','empty-baseline','partial-seed']:
            f=fixture();old=copy.deepcopy(f.monitor.cursor.__dict__)
            if fail=='new group':f.state['fail']='new group'
            elif fail=='wrong-kind':f.state['kind']['new group']='friend'
            elif fail=='empty-baseline':f.state['rows']['new group']=[]
            else:
                original=f.monitor.cursor.seed
                def partial(name,controls):original(name,controls);raise RuntimeError('seed interrupted')
                f.monitor.cursor.seed=partial
            result=f.register()
            self.assertFalse(result['ok']);self.assertFalse(result['stopWorker']);self.assertEqual(f.state['current'],'owner')
            self.assertEqual(f.routes,{'owner':'friend'});self.assertEqual(f.registry,{})
            self.assertEqual(f.seen,{'owner':{'old-event'}})
            self.assertEqual(f.monitor.cursor.rows,old['rows']);self.assertEqual(f.monitor.cursor.accepted,old['accepted'])

    def test_restore_failure_is_fatal_and_registration_is_not_published(self):
        f=fixture();f.state['fail']='owner'
        result=f.register()
        self.assertEqual(result['code'],'dynamic-group-chat-restore-failed');self.assertTrue(result['stopWorker'])
        self.assertEqual(f.routes,{'owner':'friend'});self.assertEqual(f.registry,{})
        self.assertNotIn('new group',f.seen);self.assertNotIn('new group',f.monitor.cursor.rows)

    def test_existing_same_key_is_idempotent_without_navigation_and_legacy_group_preserves_cursor(self):
        f=fixture();f.routes['new group']='group';f.monitor.cursor.seed('new group',f.state['rows']['new group'])
        original=copy.deepcopy(f.monitor.cursor.rows['new group']);f.monitor.cursor.current='owner'
        result=f.register()
        self.assertTrue(result['ok']);self.assertFalse(result['routeAdded']);self.assertEqual(f.monitor.cursor.rows['new group'],original)
        f.state['calls'].clear()
        f.wx.ChatInfo=lambda:self.fail('idempotent call must not read UI')
        result=f.register();self.assertTrue(result['confirmationReused']);self.assertEqual(f.state['calls'],[])
        original=f.wx.GetSession;f.wx.GetSession=lambda:original()+[original()[-1]]
        self.assertEqual(f.register()['code'],'dynamic-group-session-missing-or-ambiguous')
        self.assertEqual(f.state['calls'],[])

    def test_arrival_during_navigation_is_not_seeded_as_old_history(self):
        f=fixture();original=f.monitor.controls
        def arrival():
            controls=original();f.state['info']['new group'].update(time='12:02',content='new arrival')
            return controls
        f.monitor.controls=arrival
        result=f.register()
        self.assertEqual(result['code'],'dynamic-group-arrival-during-confirmation');self.assertTrue(result['pending'])
        self.assertNotIn('new group',f.monitor.cursor.rows);self.assertEqual(f.registry,{})


if __name__=='__main__':unittest.main()
