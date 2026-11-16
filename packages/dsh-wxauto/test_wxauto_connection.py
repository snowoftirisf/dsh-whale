import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from wxauto_connection import connect, refresh_chat_binding,ensure_main_tree

class ConnectionTests(unittest.TestCase):
    def environment(self, duplicate=False, fail=False, modal=False):
        clicks=[]
        class Control:
            def __init__(self, handle):
                self.NativeWindowHandle = handle
                self.ClassName = 'mmui::MainWindow' if handle == 2 or duplicate else 'mmui::LoginWindow'
                if handle==3:self.ClassName='mmui::SnsPublishPanel'
                self.ProcessId=100 if handle in (2,3) else 200
            def GetChildren(self): return []
            def ToolBarControl(self, *args, **kwargs):
                return SimpleNamespace(AutomationId='actual-navigation',Exists=lambda _:True,GetChildren=lambda: [SimpleNamespace(ClassName='mmui::XTabBarItem', Name='WeChat',Click=lambda **kwargs:clicks.append(kwargs))])
            def ButtonControl(self, *args, **kwargs):
                return kwargs
        package = ModuleType('wxauto4'); package.__path__ = []
        module = ModuleType('wxauto4.wx')
        uia = ModuleType('wxauto4.uia')
        uia.Control = Control; uia.ControlFromHandle = Control
        uia.GetForegroundWindow=lambda:3 if modal else 2
        package.uia = uia
        package.test_clicks=clicks
        param = ModuleType('wxauto4.param'); param.WxParam = SimpleNamespace()
        utils = ModuleType('wxauto4.utils')
        utils.GetAllWindows = lambda: [(1, 'Qt', 'WeChat'), (2, 'Qt', 'WeChat')]
        class Main:
            def __init__(self, nickname=None, hwnd=None, resize=True):
                assert self.control.NativeWindowHandle == 2
                assert hwnd == 2
        module.WeChatMainWnd = Main
        def wechat(**kwargs):
            if fail:
                raise RuntimeError('test-init-failure')
            return module.WeChatMainWnd(None, None, False)
        module.WeChat = wechat
        return {'wxauto4': package, 'wxauto4.wx': module, 'wxauto4.uia': uia, 'wxauto4.param': param, 'wxauto4.utils': utils}

    def test_exact_main_binding_and_brand_alias(self):
        modules = self.environment()
        original = modules['wxauto4.wx'].WeChatMainWnd
        with patch.dict(sys.modules, modules):
            result = connect()
            self.assertEqual(result.control.NativeWindowHandle, 2)
            self.assertEqual(modules['wxauto4'].test_clicks,[{'show_window':True}])
            self.assertIs(modules['wxauto4.wx'].WeChatMainWnd, original)
            button = result.control.ButtonControl(ClassName='mmui::XTabBarItem', Name='微信')
            self.assertEqual(button['Name'], 'WeChat')
            self.assertEqual(result.control.ButtonControl(ClassName='other', Name='微信')['Name'], '微信')

    def test_multiple_main_windows_fail_before_initialization(self):
        with patch.dict(sys.modules, self.environment(duplicate=True)):
            with self.assertRaisesRegex(RuntimeError, 'expected-one-main-window'):
                connect()

    def test_existing_wechat_modal_is_not_closed_or_navigated(self):
        modules=self.environment(modal=True)
        with patch.dict(sys.modules,modules):
            with self.assertRaisesRegex(RuntimeError,'modal-must-be-closed'):
                connect()
        self.assertEqual(modules['wxauto4'].test_clicks,[])

    def test_failed_initialization_restores_library_selectors(self):
        modules = self.environment(fail=True)
        control = modules['wxauto4.uia'].Control
        original_button, original_toolbar = control.ButtonControl, control.ToolBarControl
        original_main = modules['wxauto4.wx'].WeChatMainWnd
        with patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(RuntimeError, 'test-init-failure'):
                connect()
        self.assertIs(control.ButtonControl, original_button)
        self.assertIs(control.ToolBarControl, original_toolbar)
        self.assertIs(modules['wxauto4.wx'].WeChatMainWnd, original_main)

    def test_repeated_connect_alias_has_constant_base_and_failure_restores_prior(self):
        modules = self.environment()
        control = modules['wxauto4.uia'].Control
        base_button, base_toolbar = control.ButtonControl, control.ToolBarControl
        with patch.dict(sys.modules, modules):
            for _ in range(40):
                result = connect()
                self.assertIs(control.ButtonControl._whale_alias_base, base_button)
                self.assertIs(control.ToolBarControl._whale_alias_base, base_toolbar)
                self.assertEqual(result.control.ButtonControl(ClassName='mmui::XTabBarItem', Name='微信')['Name'], 'WeChat')
            prior_button, prior_toolbar = control.ButtonControl, control.ToolBarControl
            with patch.object(modules['wxauto4.wx'], 'WeChat', side_effect=RuntimeError('new-connect-failed')):
                with self.assertRaisesRegex(RuntimeError, 'new-connect-failed'): connect()
            self.assertIs(control.ButtonControl, prior_button)
            self.assertIs(control.ToolBarControl, prior_toolbar)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.clicks = []
        self.editor = SimpleNamespace(Exists=lambda _:True, IsOffscreen=False,
            BoundingRectangle=SimpleNamespace(left=0, top=0, right=100, bottom=20))
        self.button = SimpleNamespace(ClassName='mmui::XTabBarItem', Name='WeChat',
            Click=lambda **kw:self.clicks.append(kw))
        self.root = SimpleNamespace(ClassName='mmui::MainWindow', NativeWindowHandle=2, ProcessId=100,
            GetChildren=lambda:[], ToolBarControl=lambda **_:SimpleNamespace(Exists=lambda _:True,GetChildren=lambda:[self.button]),
            EditControl=lambda **_:self.editor)
        self.old_api = SimpleNamespace(control=self.root, cache='stale')
        self.new_api = SimpleNamespace(control=self.root, cache='fresh')
        class Wx:
            def __init__(self, api): self._api=api
            def ChatWith(inner, target, exact):
                if inner._api.cache == 'stale': raise RuntimeError('old-COM-cache')
                inner.target=target
            def ChatInfo(inner): return {'chat_name':inner.target,'chat_type':'friend'}
        self.wx,self.fresh=Wx(self.old_api),Wx(self.new_api)
        package=ModuleType('wxauto4')
        package.uia=SimpleNamespace(GetForegroundWindow=lambda:2, ControlFromHandle=lambda _:self.root)
        self.modules={'wxauto4':package}
        self.client={'pid':100,'clientVersion':'4.2.0.12','executable':'fixed.exe','clientSha256':'hash'}

    def invoke(self, **kwargs):
        with patch.dict(sys.modules,self.modules), patch('wxauto_connection.connect',return_value=self.fresh), \
                patch('wxauto_connection._client_identity',return_value=self.client):
            return refresh_chat_binding(self.wx,'owner','friend',timeout=0,**kwargs)

    def test_same_object_gets_fresh_cache_without_seed_or_send(self):
        with self.assertRaisesRegex(RuntimeError,'old-COM-cache'):self.wx.ChatWith('owner',exact=True)
        original=self.wx
        self.assertIs(self.invoke(),original)
        self.assertIs(self.wx._api,self.new_api)
        self.wx.ChatWith('owner',exact=True)
        self.assertEqual(self.wx.ChatInfo()['chat_name'],'owner')
        self.assertEqual(self.clicks,[{'move':True,'show_window':True}])

    def test_real_com_integer_false_is_accepted(self):
        self.editor.IsOffscreen=0
        self.assertIs(self.invoke(),self.wx)
        self.assertIs(self.wx._api,self.new_api)

    def test_non_bool_values_do_not_claim_visible_input(self):
        for value in (None,'0',0.0,2):
            with self.subTest(value=value):
                self.editor.IsOffscreen=value
                with self.assertRaisesRegex(RuntimeError,'chat-input-unavailable'):self.invoke()
                self.assertIs(self.wx._api,self.old_api)

    def test_window_or_client_change_refuses_replacement(self):
        self.fresh._api=SimpleNamespace(control=SimpleNamespace(NativeWindowHandle=4,ProcessId=100))
        with self.assertRaisesRegex(RuntimeError,'client-changed'):self.invoke()
        self.assertIs(self.wx._api,self.old_api)
        self.fresh._api=self.new_api
        with patch.dict(sys.modules,self.modules),patch('wxauto_connection.connect',return_value=self.fresh), \
                patch('wxauto_connection._client_identity',side_effect=[self.client,{**self.client,'clientVersion':'changed'}]):
            with self.assertRaisesRegex(RuntimeError,'client-changed'):
                refresh_chat_binding(self.wx,'owner','friend',timeout=0)
        self.assertIs(self.wx._api,self.old_api)

    def test_unresolved_draft_dialog_or_recorder_does_not_navigate(self):
        for cls in ('mmui::SnsPublishPanel','mmui::XDialog','mmui::ChatVoiceRecordView'):
            self.root.GetChildren=lambda:[SimpleNamespace(ClassName=cls)]
            with self.assertRaisesRegex(RuntimeError,'modal-must-be-closed'):self.invoke()
            self.assertEqual(self.clicks,[])

    def test_missing_visible_input_or_wrong_target_never_replaces_api(self):
        self.editor.IsOffscreen=True
        with self.assertRaisesRegex(RuntimeError,'chat-input-unavailable'):self.invoke()
        self.assertIs(self.wx._api,self.old_api)
        self.editor.IsOffscreen=False
        self.fresh.ChatInfo=lambda:{'chat_name':'other','chat_type':'friend'}
        with self.assertRaisesRegex(RuntimeError,'target-mismatch'):self.invoke()
        self.assertIs(self.wx._api,self.old_api)

    def test_readonly_cython_style_api_is_explicit_failure(self):
        class ReadOnly:
            @property
            def _api(inner):return self.old_api
        self.wx=ReadOnly()
        with self.assertRaisesRegex(RuntimeError,'api-not-assignable'):self.invoke()

class TreeRecoveryTests(unittest.TestCase):
    def environment(self,*,healthy=False,canvas=False,effect=True):
        calls=[]
        class Node:
            def __init__(self,cls,pid=100,hwnd=0,children=()):
                self.ClassName=cls;self.ProcessId=pid;self.NativeWindowHandle=hwnd;self.children=list(children)
                self.Name='';self.IsOffscreen=0;self.IsEnabled=1;self.ready=False
            def GetChildren(self):return self.children
            def GetRuntimeId(self):return [42,id(self)]
            def ToolBarControl(self,**kwargs):return SimpleNamespace(Exists=lambda _:self.ready)
        root=Node('mmui::MainWindow',hwnd=2);root.ready=healthy
        if canvas:root.children=[Node('MMUIRenderSubWindowHW')]
        shell=Node('Shell_TrayWnd',pid=200,hwnd=9);button=Node('SystemTray.NormalButton',pid=200);button.Name=' WeChat'
        shell.children=[button];desktop=Node('desktop',pid=0,children=[root,shell])
        current=[root]
        def action():
            calls.append('default')
            if effect:
                fresh=Node('mmui::MainWindow',hwnd=2);fresh.ready=True;current[0]=fresh
        button.GetLegacyIAccessiblePattern=lambda:SimpleNamespace(DoDefaultAction=action)
        uia=SimpleNamespace(ControlFromHandle=lambda hwnd:current[0],GetForegroundWindow=lambda:2,
            GetRootControl=lambda:desktop)
        return root,shell,button,desktop,current,uia,calls,Node

    def test_healthy_tree_has_zero_tray_enumeration_or_input(self):
        root,_,_,_,_,uia,calls,_=self.environment(healthy=True)
        uia.GetRootControl=lambda:(_ for _ in()).throw(AssertionError('normal must not inspect tray'))
        self.assertEqual(ensure_main_tree(root,uia=uia),{'restored':False,'root':root});self.assertEqual(calls,[])
        root.ToolBarControl=lambda **kw:SimpleNamespace(Exists=lambda _:1)
        self.assertFalse(ensure_main_tree(root,uia=uia)['restored'])

    def test_unknown_navigation_existence_is_not_assumed_absent(self):
        for value in(None,'0',0.0,2):
            root,_,_,_,_,uia,calls,_=self.environment()
            root.ToolBarControl=lambda **kw:SimpleNamespace(Exists=lambda _:value)
            with self.assertRaisesRegex(RuntimeError,'navigation-unreadable'):ensure_main_tree(root,uia=uia)
            self.assertEqual(calls,[])

    def test_empty_or_canvas_tree_uses_one_action_and_reacquires_original_identity(self):
        for canvas in (False,True):
            root,_,_,_,current,uia,calls,_=self.environment(canvas=canvas)
            result=ensure_main_tree(root,uia=uia)
            self.assertTrue(result['restored']);self.assertIs(result['root'],current[0]);self.assertIsNot(result['root'],root)
            self.assertEqual((result['root'].NativeWindowHandle,result['root'].ProcessId),(2,100));self.assertEqual(calls,['default'])

    def test_unknown_shape_ambiguous_tray_foreign_node_or_disabled_button_do_not_act(self):
        for kind in ('shape','ambiguous','foreign','disabled','bad-name','modal'):
            with self.subTest(kind=kind):
                root,shell,button,desktop,current,uia,calls,Node=self.environment()
                if kind=='shape':root.children=[Node('QWidget')]
                if kind=='ambiguous':shell.children=[button,button]
                if kind=='foreign':shell.children.append(Node('foreign',pid=999))
                if kind=='disabled':button.IsEnabled=0
                if kind=='bad-name':button.Name='WeChat'
                if kind=='modal':desktop.children.append(Node('mmui::XDialog',hwnd=3))
                with self.assertRaises(RuntimeError):ensure_main_tree(root,uia=uia)
                self.assertEqual(calls,[])

    def test_failed_action_or_timeout_never_repeats_default_action(self):
        root,_,button,_,_,uia,calls,_=self.environment(effect=False)
        now=[0]
        def sleep(seconds):now[0]+=seconds
        with self.assertRaisesRegex(RuntimeError,'restore-timeout'):
            ensure_main_tree(root,uia=uia,timeout=.1,clock=lambda:now[0],sleep=sleep)
        self.assertEqual(calls,['default'])
        root,_,button,_,_,uia,calls,_=self.environment(effect=False)
        def fail():calls.append('default');raise OSError('fixture')
        button.GetLegacyIAccessiblePattern=lambda:SimpleNamespace(DoDefaultAction=fail)
        with self.assertRaisesRegex(RuntimeError,'default-action-unknown'):ensure_main_tree(root,uia=uia)
        self.assertEqual(calls,['default'])

    def test_changed_window_identity_after_action_is_not_rebound(self):
        root,_,button,_,current,uia,calls,Node=self.environment(effect=False)
        def change():calls.append('default');current[0]=Node('mmui::MainWindow',pid=999,hwnd=2)
        button.GetLegacyIAccessiblePattern=lambda:SimpleNamespace(DoDefaultAction=change)
        with self.assertRaisesRegex(RuntimeError,'main-changed'):ensure_main_tree(root,uia=uia)
        self.assertEqual(calls,['default'])


if __name__ == '__main__':
    unittest.main()
