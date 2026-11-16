import copy,json,unittest
from pathlib import Path
from social_contact_observation import read_contact_profile,read_group_creation_picker,read_friend_search_result,DISPLAY_ID,ROW_ID,KEY_ID,VALUE_ID,PICKER_TITLE


class ReplayControl:
    """Offline tree only; exposes reads, deliberately no action methods."""
    serial=0
    def __init__(self,cls,name='',id='',type='GroupControl',children=(),runtime=None):
        ReplayControl.serial+=1
        self.ClassName=cls;self.Name=name;self.AutomationId=id;self.ControlTypeName=type
        self.children=list(children);self.runtime=runtime or [42,ReplayControl.serial]
        self.ProcessId=4242;self.NativeWindowHandle=9001;self.IsOffscreen=False;self.value='fixture_query_123'
    def GetChildren(self):return list(self.children)
    def GetRuntimeId(self):return self.runtime
    def GetValuePattern(self):
        node=self
        class ReadOnlyPattern:
            @property
            def Value(self):return node.value
        return ReadOnlyPattern()


def replay(document):
    roots=[];stack=[]
    for row in document['controls']:
        depth=row['depth']
        while stack and stack[-1][0]>=depth:stack.pop()
        node=ReplayControl(row['class'],row.get('name',''),row.get('automationId',row.get('id','')),
                           row['type'],runtime=row['runtimeId'])
        if stack:stack[-1][1].children.append(node)
        else:roots.append(node)
        stack.append((depth,node))
    if len(roots)!=1:raise ValueError('replay-needs-one-root')
    return roots[0]


def profile():
    def row(label,value):return ReplayControl('mmui::XHBoxView',id=ROW_ID,children=[
        ReplayControl('mmui::XTextView',label,KEY_ID,'TextControl'),
        ReplayControl('mmui::ProfileTextView',value,VALUE_ID,'TextControl')])
    return ReplayControl('mmui::ProfileHeaderUi',children=[
        ReplayControl('mmui::XTextView','Fixture Friend',DISPLAY_ID,'TextControl'),
        ReplayControl('mmui::ContactHeadView','Fixture Friend',type='ButtonControl'),
        row('地区：','北京 西城'),row('WeChat ID: ','fixture_friend_123')])


def picker():return ReplayControl('mmui::SessionPickerWindow',PICKER_TITLE,type='WindowControl',children=[
    ReplayControl('mmui::XValidatorTextEdit','搜索',type='EditControl'),
    ReplayControl('mmui::XTableView','已选择0个联系人','sp_choice_contact_list','TextControl'),
    ReplayControl('mmui::XOutlineButton','取消','cancel_btn','ButtonControl'),
    ReplayControl('mmui::XButton','完成','confirm_btn','ButtonControl'),
    ReplayControl('mmui::SPSelectionBaseRow','',type='CheckBoxControl')])


def search_window(state='negative'):
    children=[ReplayControl('mmui::XValidatorTextEdit','搜索',type='EditControl')]
    if state=='negative':children.append(ReplayControl('mmui::XTextView','找不到相关账号或内容',type='TextControl'))
    if state=='profile':children.append(profile())
    return ReplayControl('mmui::AddFriendWindow','添加朋友','AddFriendWindow','WindowControl',children)


def search_result(window,**extra):
    return read_friend_search_result(window,'fixture_query_123',client_pid=4242,window_handle=9001,
                                    foreground=lambda:9001,replay=True,**extra)


class ContactObservationTests(unittest.TestCase):
    def test_search_states_are_current_observation_not_fresh_response_or_identity(self):
        for mode,state in [('negative','no-match-observed'),('profile','profile-observed'),('empty','unknown')]:
            result=search_result(search_window(mode))
            self.assertEqual(result['state'],state)
            for key in ['newSearchCompleted','responseFreshnessVerified','accountMatched','applicationSubmitted','friendshipConfirmed','actionAuthorized','liveAcceptance']:
                self.assertFalse(result[key])
            self.assertNotIn('fixture_query_123',json.dumps(result))
            self.assertTrue(result['queryValueMatched']);self.assertTrue(result['readOnly'])
        window=search_window()
        for _ in range(2):
            observed=search_result(window)
            self.assertEqual(observed['state'],'no-match-observed');self.assertFalse(observed['newSearchCompleted'])

    def test_search_window_query_and_foreground_are_strictly_bound(self):
        for field,value in [('ProcessId',99),('NativeWindowHandle',99),('Name','other'),('AutomationId','other')]:
            root=search_window();setattr(root,field,value)
            with self.subTest(field=field),self.assertRaises(RuntimeError):search_result(root)
        root=search_window();root.children[0].value='unrelated_input'
        with self.assertRaisesRegex(RuntimeError,'query-mismatch'):search_result(root)
        with self.assertRaisesRegex(RuntimeError,'not-foreground'):
            read_friend_search_result(search_window(),'fixture_query_123',client_pid=4242,window_handle=9001,foreground=lambda:99)

    def test_search_rejects_ambiguous_hidden_foreign_or_changing_evidence(self):
        for mode in ['duplicate-negative','negative-and-profile','duplicate-edit','hidden','foreign-child','query-changed','window-changed']:
            root=search_window()
            if mode=='duplicate-negative':root.children.append(copy.deepcopy(root.children[-1]))
            elif mode=='negative-and-profile':root.children.append(profile())
            elif mode=='duplicate-edit':root.children.append(copy.deepcopy(root.children[0]))
            elif mode=='hidden':root.children[-1].IsOffscreen=True
            elif mode=='foreign-child':root.children[-1].ProcessId=99
            elif mode=='query-changed':
                edit=root.children[0];old=edit.GetChildren
                def mutate():edit.value='changed_query';return old()
                edit.GetChildren=mutate
            else:
                leaf=root.children[-1];old=leaf.GetChildren
                def mutate():root.NativeWindowHandle=99;return old()
                leaf.GetChildren=mutate
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):search_result(root)

    def test_real_negative_artifact_replay_does_not_prove_dispatch_or_query_freshness(self):
        file=Path(__file__).resolve().parents[2]/'artifacts/contact-forms-live-20261102/controls-008.json'
        if not file.exists():self.skipTest('local observation fixture not distributed')
        controls=json.loads(file.read_text(encoding='utf8'))['controls']
        starts=[i for i,c in enumerate(controls) if c['class']=='mmui::AddFriendWindow'];self.assertEqual(len(starts),1)
        start=starts[0];depth=controls[start]['depth']
        end=next((i for i in range(start+1,len(controls)) if controls[i]['depth']<=depth),len(controls))
        window=replay({'controls':controls[start:end]})
        # The original JSON has no ValuePattern/PID/HWND fields. Their fixture
        # values above are explicitly synthetic; only hierarchy/text is replayed.
        result=search_result(window)
        self.assertEqual(result['source'],'wxauto-uia-offline-replay')
        self.assertEqual(result['state'],'no-match-observed')
        self.assertFalse(result['newSearchCompleted']);self.assertFalse(result['accountMatched'])

    def test_profile_same_row_pair_not_region_and_exact_trusted_identity(self):
        value=read_contact_profile(profile(),'Fixture Friend',expected_wechat_id='fixture_friend_123',replay=True)
        self.assertEqual(value['wechatId'],'fixture_friend_123');self.assertTrue(value['identityMatched'])
        self.assertEqual(value['source'],'wxauto-uia-offline-replay');self.assertTrue(value['readOnly'])
        self.assertFalse(value['actionAuthorized']);self.assertFalse(value['liveAcceptance'])
        with self.assertRaisesRegex(RuntimeError,'display-mismatch'):read_contact_profile(profile(),'Another Friend')
        with self.assertRaisesRegex(RuntimeError,'identity-mismatch'):read_contact_profile(profile(),'Fixture Friend',expected_wechat_id='other_person')

    def test_missing_duplicate_cross_row_or_empty_identity_fails(self):
        for kind in ('empty','duplicate-row','duplicate-label','duplicate-value','cross-row','region-as-id'):
            root=profile();row=root.children[-1]
            if kind=='empty':row.children[1].Name=''
            elif kind=='duplicate-row':root.children.append(copy.deepcopy(row))
            elif kind=='duplicate-label':row.children.append(copy.deepcopy(row.children[0]))
            elif kind=='duplicate-value':row.children.append(copy.deepcopy(row.children[1]))
            elif kind=='cross-row':row.children.remove(row.children[1])
            else:row.children[1].Name='北京 西城'
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):read_contact_profile(root,'Fixture Friend')

    def test_mutating_control_tree_is_rejected(self):
        root=profile();value=root.children[-1].children[1]
        # Change the value after its first captured properties were read.
        old_children=value.GetChildren
        def leaf_children():value.Name='changed_identity';return old_children()
        value.GetChildren=leaf_children
        with self.assertRaisesRegex(RuntimeError,'controls-changed'):read_contact_profile(root,'Fixture Friend')

    def test_picker_is_exact_empty_creation_purpose_and_never_resolves_checkbox_names(self):
        root=picker();result=read_group_creation_picker(root,replay=True)
        self.assertEqual(result['selectedCount'],0);self.assertEqual(result['contacts'],[])
        self.assertFalse(result['contactsResolved']);self.assertFalse(result['actionAuthorized'])
        root.children[-1].Name='Fixture Friend'
        self.assertEqual(read_group_creation_picker(root,replay=True)['contacts'],[])
        root.Name='微信发送给'
        with self.assertRaisesRegex(RuntimeError,'purpose-mismatch'):read_group_creation_picker(root)

    def test_picker_count_and_controls_must_be_unique_observed_classes(self):
        for kind in ('selected','duplicate-search','wrong-cancel','missing-confirm'):
            root=picker()
            if kind=='selected':root.children[1].Name='已选择1个联系人'
            elif kind=='duplicate-search':root.children.append(copy.deepcopy(root.children[0]))
            elif kind=='wrong-cancel':root.children[2].ClassName='mmui::XButton'
            else:root.children.pop(3)
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):read_group_creation_picker(root)

    def test_actual_artifacts_offline_replay_without_live_control_or_action(self):
        base=Path(__file__).resolve().parents[2]/'artifacts/contact-forms-live-20261102'
        if not (base/'owner-profile-header.json').exists():self.skipTest('local observation fixture not distributed')
        document=json.loads((base/'owner-profile-header.json').read_text(encoding='utf8'))
        root=replay(document);value=read_contact_profile(root,document['selectedExistingContact'],replay=True)
        self.assertTrue(value['wechatId']);self.assertFalse(value['identityMatched']);self.assertFalse(value['liveAcceptance'])
        document=json.loads((base/'controls-004.json').read_text(encoding='utf8'))
        result=read_group_creation_picker(replay(document),replay=True)
        self.assertEqual(result['selectedCount'],0);self.assertEqual(result['contacts'],[])
        self.assertFalse(result['liveAcceptance']);self.assertTrue(result['readOnly'])


if __name__=='__main__':unittest.main()
