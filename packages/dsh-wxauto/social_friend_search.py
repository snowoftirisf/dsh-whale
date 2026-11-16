"""Observed WeChat 4.2 account lookup, without applying or accepting a friend.

The caller owns opening/closing the form and restoring the chat. This helper is
not registered as a social ActionBinding. A dispatched search is not evidence
of a matching account, a submitted application, or friendship.
"""
import re


def valid_search_query(value):
    """Literal lookup input, not a claim that it is a valid public WeChat ID."""
    return isinstance(value,str) and re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_\-]{4,127}|\+[0-9]{5,15})',value) is not None


def _true(value):
    return type(value) in (bool, int) and value == 1


class FriendSearch:
    """One lookup on an already-open, caller-verified wxauto popup.

No SDK monkeypatch, global keyboard input, guessed coordinates or Enter key.
The client/version acceptance gate belongs to the caller before construction.
Once a click is attempted this instance cannot dispatch again, even if UIA
raises after the click. The result must be independently observed by the caller.
"""
    def __init__(self, window, *, client_pid, foreground):
        if type(client_pid) is not int or client_pid <= 0:
            raise ValueError('friend-search-client-pid-invalid')
        self.window, self.client_pid, self.foreground = window, client_pid, foreground
        self.attempted = False
        self.identity = None

    def _controls(self):
        window = self.window
        if (window.ClassName != 'mmui::AddFriendWindow'
                or window.ControlTypeName != 'WindowControl'
                or window.Name != '添加朋友' or window.AutomationId != 'AddFriendWindow'
                or window.ProcessId != self.client_pid):
            raise RuntimeError('friend-search-window-mismatch')
        identity = (window.ProcessId, window.NativeWindowHandle, tuple(window.GetRuntimeId()))
        if not identity[1] or not identity[2]:
            raise RuntimeError('friend-search-window-identity-unavailable')
        if self.identity is None:
            self.identity = identity
        if identity != self.identity or self.foreground() != identity[1]:
            raise RuntimeError('friend-search-window-changed-or-not-foreground')
        found = {'edit': [], 'button': []}
        stack, count = [(window, 0)], 0
        while stack:
            control, depth = stack.pop()
            count += 1
            if count > 1000 or depth > 25:
                raise RuntimeError('friend-search-tree-budget')
            if control.Name == '搜索':
                if (control.ClassName, control.ControlTypeName) == ('mmui::XValidatorTextEdit', 'EditControl'):
                    found['edit'].append(control)
                if (control.ClassName, control.ControlTypeName) == ('mmui::XButton', 'ButtonControl'):
                    found['button'].append(control)
            stack.extend((child, depth + 1) for child in control.GetChildren())
        if any(len(nodes) != 1 for nodes in found.values()):
            raise RuntimeError('friend-search-controls-missing-or-ambiguous')
        for control in (window, found['edit'][0], found['button'][0]):
            if (control.ProcessId != self.client_pid or not _true(control.IsEnabled)
                    or type(control.IsOffscreen) not in (bool, int) or control.IsOffscreen != 0):
                raise RuntimeError('friend-search-controls-not-usable')
        return found['edit'][0], found['button'][0]

    def dispatch(self, account):
        if self.attempted:
            raise RuntimeError('friend-search-already-attempted')
        if not valid_search_query(account):
            raise ValueError('friend-search-account-invalid')
        edit, button = self._controls()
        initial = (tuple(edit.GetRuntimeId()), tuple(button.GetRuntimeId()))
        pattern = edit.GetValuePattern()
        if pattern.Value not in ('', account):
            raise RuntimeError('friend-search-existing-input')
        if pattern.Value != account:
            pattern.SetValue(account)
        edit, button = self._controls()
        if initial != (tuple(edit.GetRuntimeId()), tuple(button.GetRuntimeId())):
            raise RuntimeError('friend-search-controls-changed')
        if edit.GetValuePattern().Value != account:
            raise RuntimeError('friend-search-input-not-confirmed')
        self.attempted = True
        try:
            button.Click(move=True, show_window=False)
        except Exception:
            # The exception may follow a successful native click. Never retry.
            return {'state': 'unknown', 'code': 'friend-search-click-outcome-unknown',
                    'applicationSubmitted': False, 'accountMatched': False}
        return {'state': 'dispatched', 'applicationSubmitted': False, 'accountMatched': False}
