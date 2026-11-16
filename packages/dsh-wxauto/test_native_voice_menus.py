"""Synthetic controls only: no wxauto import, UI, keys or production writes."""
import json
from types import SimpleNamespace
import unittest

from native_voice_menus import close_avatar_context_menu, observe_avatar_context_menu, MenuCleanupError


class Control:
    serial = 0
    def __init__(self, cls, name='', children=(), pid=1, handle=0):
        Control.serial += 1
        self.ClassName,self.Name,self.children = cls,name,children
        self.ProcessId,self.NativeWindowHandle = pid,handle
        self.identity = Control.serial
        self.IsOffscreen = 0
        self.sent = []
        self.on_escape = lambda: None
    def GetRuntimeId(self):return [42,self.identity]
    def GetChildren(self):return self.children() if callable(self.children) else self.children
    def SendKeys(self,keys):self.sent.append(keys);self.on_escape()


class AvatarMenuTests(unittest.TestCase):
    def setUp(self):
        self.present = True
        self.menu=Control('mmui::XMenu',children=[Control('mmui::XMenuView','@private-avatar-name'),
                                                  Control('mmui::XMenuView','拍一拍')])
        self.root=Control('mmui::MainWindow',handle=123,children=lambda:[self.menu] if self.present else [])
        self.desktop=Control('desktop',children=lambda:[self.root,*([self.menu] if self.present else [])])
        self.foreground=self.root
        self.uia=SimpleNamespace(GetRootControl=lambda:self.desktop,GetForegroundControl=lambda:self.foreground)
        self.menu.on_escape=lambda:setattr(self,'present',False)
    def close(self):return close_avatar_context_menu(self.root,self.uia,timeout=0)
    def refused(self,code):
        with self.assertRaisesRegex(MenuCleanupError,code) as raised:self.close()
        self.assertNotIn('private-avatar-name',str(raised.exception))
        self.assertNotIn('private-avatar-name',json.dumps(raised.exception.facts))
        return raised.exception

    def test_exact_menu_escape_once_and_no_names_in_facts(self):
        result=self.close()
        self.assertTrue(result['closed']);self.assertEqual(result['menuCount'],1)
        self.assertEqual(self.menu.sent,['{Esc}'])
        self.assertNotIn('private-avatar-name',json.dumps(result))
        self.assertEqual(len(result['runtimeIdSha256']),64)

    def test_top_level_menu_outside_main_tree_can_be_exact_foreground(self):
        self.root.children=[]
        self.foreground=self.menu
        def escape():self.present=False;self.foreground=self.root
        self.menu.on_escape=escape
        self.assertTrue(self.close()['closed'])
        self.assertEqual(self.menu.sent,['{Esc}'])

    def test_external_menu_is_never_touched(self):
        self.menu.ProcessId=2
        self.assertEqual(self.close(),{'closed':False,'menuCount':0})
        self.assertEqual(self.menu.sent,[])

    def test_no_menu_is_explicit_noop_without_keys(self):
        self.present=False
        self.assertEqual(self.close(),{'closed':False,'menuCount':0})
        self.assertEqual(self.menu.sent,[])

    def test_unknown_menu_is_never_touched(self):
        self.menu.children[0].Name='unobserved operation'
        self.refused('shape-unverified');self.assertEqual(self.menu.sent,[])

    def test_observed_own_avatar_single_pat_item_can_close(self):
        self.menu.children=[Control('mmui::XMenuView','拍一拍')]
        self.assertTrue(self.close()['closed'])
        self.assertEqual(self.menu.sent,['{Esc}'])

    def test_unknown_single_item_is_not_an_own_avatar_menu(self):
        self.menu.children=[Control('mmui::XMenuView','@private-avatar-name')]
        self.refused('shape-unverified')
        self.assertEqual(self.menu.sent,[])

    def test_escape_return_is_not_confirmation(self):
        self.menu.on_escape=lambda:None
        self.refused('close-unconfirmed');self.assertEqual(self.menu.sent,['{Esc}'])

    def test_multiple_distinct_menus_refuse_but_duplicate_runtime_views_are_deduped(self):
        other=Control('mmui::XMenu',children=self.menu.children)
        self.desktop.children=[self.root,self.menu,other]
        error=self.refused('count-unverified')
        self.assertEqual(error.facts['menuCount'],2)
        self.assertEqual(self.menu.sent,[]);self.assertEqual(other.sent,[])

    def test_external_and_unknown_same_pid_foreground_refuse(self):
        self.foreground=Control('external',pid=2)
        self.refused('external-foreground');self.assertEqual(self.menu.sent,[])
        self.foreground=Control('mmui::XDialog')
        self.refused('foreground-unverified');self.assertEqual(self.menu.sent,[])

    def test_active_recorder_refuses_without_escape(self):
        self.root.children=[self.menu,Control('mmui::ChatVoiceRecordView')]
        self.refused('recorder-active');self.assertEqual(self.menu.sent,[])

    def test_unreadable_menu_never_relays_private_com_errors(self):
        def broken():raise RuntimeError('private-avatar-name')
        self.menu.GetChildren=broken
        self.refused('unreadable');self.assertEqual(self.menu.sent,[])

    def test_unknown_com_visibility_refuses(self):
        self.menu.IsOffscreen='0'
        self.refused('unreadable');self.assertEqual(self.menu.sent,[])

    def test_foreground_loss_after_escape_cannot_claim_success(self):
        def escape():self.present=False;self.foreground=Control('external',pid=2)
        self.menu.on_escape=escape
        self.refused('external-foreground');self.assertEqual(self.menu.sent,['{Esc}'])

    def test_hidden_original_runtime_is_not_disappearance(self):
        self.menu.on_escape=lambda:setattr(self.menu,'IsOffscreen',1)
        self.refused('close-unconfirmed');self.assertEqual(self.menu.sent,['{Esc}'])

    def test_budget_refuses_without_input(self):
        with self.assertRaisesRegex(MenuCleanupError,'tree-budget'):
            close_avatar_context_menu(self.root,self.uia,max_nodes=1)
        self.assertEqual(self.menu.sent,[])

    def test_readonly_observer_distinguishes_peer_own_unknown_and_none_without_keys(self):
        peer=observe_avatar_context_menu(self.root,self.uia)
        self.assertTrue(peer['observed']);self.assertEqual(peer['menuCount'],1)
        self.assertEqual(peer['menus'][0]['shape'],'peer-at-and-pat')
        self.assertEqual(len(peer['menus'][0]['runtimeIdSha256']),64)
        self.menu.children=[Control('mmui::XMenuView','拍一拍')]
        own=observe_avatar_context_menu(self.root,self.uia)
        self.assertEqual(own['menus'][0]['shape'],'own-pat-only')
        self.menu.children=[Control('mmui::XMenuView','private unknown option')]
        unknown=observe_avatar_context_menu(self.root,self.uia)
        self.assertEqual(unknown['menus'][0]['shape'],'unknown')
        self.assertEqual(unknown['code'],'avatar-menu-shape-unverified')
        self.present=False
        empty=observe_avatar_context_menu(self.root,self.uia)
        self.assertEqual(empty,{'observed':True,'menuCount':0,'menus':[]})
        self.assertEqual(self.menu.sent,[])
        for result in (peer,own,unknown,empty):
            self.assertNotIn('private',json.dumps(result))
            self.assertNotIn('拍一拍',json.dumps(result))

    def test_readonly_observer_reports_fixed_external_recorder_and_sdk_refusals(self):
        self.foreground=Control('external',pid=2)
        self.assertEqual(observe_avatar_context_menu(self.root,self.uia)['code'],'avatar-menu-external-foreground')
        self.foreground=self.root
        self.root.children=[self.menu,Control('mmui::ChatVoiceRecordView')]
        self.assertEqual(observe_avatar_context_menu(self.root,self.uia)['code'],'avatar-menu-recorder-active')
        self.root.children=[self.menu]
        def broken():raise RuntimeError('private member name')
        self.menu.GetChildren=broken
        result=observe_avatar_context_menu(self.root,self.uia)
        self.assertEqual(result['code'],'avatar-menu-unreadable')
        self.assertNotIn('private',json.dumps(result));self.assertEqual(self.menu.sent,[])

    def test_readonly_multiple_menus_report_counts_but_do_not_dismiss_any(self):
        other=Control('mmui::XMenu',children=[Control('mmui::XMenuView','拍一拍')])
        self.desktop.children=[self.root,self.menu,other]
        result=observe_avatar_context_menu(self.root,self.uia)
        self.assertEqual(result['menuCount'],2)
        self.assertEqual(result['code'],'avatar-menu-count-unverified')
        self.assertEqual(self.menu.sent,[]);self.assertEqual(other.sent,[])


if __name__=='__main__':unittest.main()
