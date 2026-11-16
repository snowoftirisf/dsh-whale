import unittest
from social_friend_search import FriendSearch


class Control:
    serial = 0
    def __init__(self, cls, kind, name='', children=()):
        Control.serial += 1
        self.runtime = [42, Control.serial]
        self.ClassName, self.ControlTypeName, self.Name = cls, kind, name
        self.AutomationId = ''
        self.ProcessId, self.NativeWindowHandle = 7, 123
        self.IsEnabled, self.IsOffscreen = 1, 0
        self.children, self.Value, self.clicks = list(children), '', 0
        self.on_set, self.on_click = lambda: None, lambda: None
    def GetRuntimeId(self): return self.runtime
    def GetChildren(self): return self.children
    def GetValuePattern(self): return self
    def SetValue(self, value): self.Value = value; self.on_set()
    def Click(self, **kwargs):
        assert kwargs == {'move': True, 'show_window': False}
        self.clicks += 1
        self.on_click()


def fixture():
    edit = Control('mmui::XValidatorTextEdit', 'EditControl', '搜索')
    button = Control('mmui::XButton', 'ButtonControl', '搜索', [Control('mmui::XTextView', 'TextControl', '搜索')])
    window = Control('mmui::AddFriendWindow', 'WindowControl', '添加朋友', [edit, button])
    window.AutomationId = 'AddFriendWindow'
    return window, edit, button


class FriendSearchTests(unittest.TestCase):
    def test_new_observed_button_once_no_apply_or_match_claim(self):
        root, edit, button = fixture()
        lookup = FriendSearch(root, client_pid=7, foreground=lambda: 123)
        result = lookup.dispatch('fixture_account')
        self.assertEqual(result, {'state': 'dispatched', 'applicationSubmitted': False, 'accountMatched': False})
        self.assertEqual(edit.Value, 'fixture_account')
        with self.assertRaisesRegex(RuntimeError, 'already-attempted'): lookup.dispatch('another_account')
        self.assertEqual(button.clicks, 1)

    def test_ambiguous_old_sdk_button_wrong_process_or_purpose_refused(self):
        for case in ('duplicate', 'old-button', 'process', 'purpose', 'foreground', 'hidden', 'unknown-enabled'):
            root, edit, button = fixture()
            if case == 'duplicate': root.children.append(Control('mmui::XButton', 'ButtonControl', '搜索'))
            if case == 'old-button': button.ClassName = 'mmui::XOutlineButton'
            if case == 'process': button.ProcessId = 99
            if case == 'purpose': root.Name = '发起群聊'
            if case == 'hidden': button.IsOffscreen = 1
            if case == 'unknown-enabled': button.IsEnabled = '1'
            lookup = FriendSearch(root, client_pid=7, foreground=lambda: 999 if case == 'foreground' else 123)
            with self.subTest(case=case), self.assertRaises(RuntimeError): lookup.dispatch('fixture_account')
            self.assertEqual(button.clicks, 0)
            self.assertEqual(edit.Value, '')

    def test_existing_input_is_preserved(self):
        root, edit, button = fixture(); edit.Value = 'unfinished_user_input'
        with self.assertRaisesRegex(RuntimeError, 'existing-input'):
            FriendSearch(root, client_pid=7, foreground=lambda: 123).dispatch('fixture_account')
        self.assertEqual(edit.Value, 'unfinished_user_input'); self.assertEqual(button.clicks, 0)

    def test_international_phone_and_existing_exact_query(self):
        root, edit, button = fixture(); edit.Value = '+6598765432'
        def no_overwrite(): self.fail('same query should not be rewritten')
        edit.on_set = no_overwrite
        result = FriendSearch(root, client_pid=7, foreground=lambda: 123).dispatch('+6598765432')
        self.assertEqual(result['state'], 'dispatched'); self.assertEqual(button.clicks, 1)

    def test_changed_focus_control_or_value_after_set_prevents_click(self):
        for case in ('focus', 'runtime', 'value', 'window'):
            root, edit, button = fixture(); foreground = [123]
            def change():
                if case == 'focus': foreground[0] = 456
                if case == 'runtime': button.runtime = [42, 9876]
                if case == 'value': edit.Value = 'other_account'
                if case == 'window': root.NativeWindowHandle = 456; foreground[0] = 456
            edit.on_set = change
            lookup = FriendSearch(root, client_pid=7, foreground=lambda: foreground[0])
            with self.subTest(case=case), self.assertRaises(RuntimeError): lookup.dispatch('fixture_account')
            self.assertEqual(button.clicks, 0)

    def test_click_exception_is_unknown_and_never_retried(self):
        root, edit, button = fixture()
        def fail(): raise RuntimeError('native click may already have happened')
        button.on_click = fail
        lookup = FriendSearch(root, client_pid=7, foreground=lambda: 123)
        self.assertEqual(lookup.dispatch('fixture_account')['state'], 'unknown')
        with self.assertRaisesRegex(RuntimeError, 'already-attempted'): lookup.dispatch('fixture_account')
        self.assertEqual(button.clicks, 1)

    def test_invalid_accounts_never_touch_form(self):
        root, edit, button = fixture()
        for account in ('', 'x', 'foo\nbar', ' foo123', 'foo123 ', None, 123, 'x' * 129):
            with self.subTest(account=account), self.assertRaises(ValueError):
                FriendSearch(root, client_pid=7, foreground=lambda: 123).dispatch(account)
        self.assertEqual(edit.Value, ''); self.assertEqual(button.clicks, 0)


if __name__ == '__main__': unittest.main()
