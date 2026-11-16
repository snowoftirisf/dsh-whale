"""Experimental diagnostic, NOT used by the production worker.

wxauto4 parsing can invoke right-click/copy and scrolling. Block input entirely
rather than sending keys to another app after suppressing focus. Not deployed:
live tests found incomplete records with SetFocus suppressed.
"""
from contextlib import contextmanager

@contextmanager
def without_focus(control_class=None):
    if control_class is None:
        from wxauto4 import uia
        control_class = uia.Control
    original = control_class.SetFocus
    inputs = {}
    for name in ('SendKey', 'SendKeys', 'Click', 'DoubleClick', 'RightClick', 'WheelDown', 'WheelUp', 'MoveCursorToInnerPos', 'Input', 'Paste', 'ShortcutPaste'):
        if hasattr(control_class, name):
            inputs[name] = getattr(control_class, name)
            def deny(*args, _name=name, **kwargs):
                raise RuntimeError('read-requested-input:' + _name)
            setattr(control_class, name, deny)
    counts = {'suppressed': 0}
    def no_focus(self):
        counts['suppressed'] += 1
        return True
    control_class.SetFocus = no_focus
    try:
        yield counts
    finally:
        control_class.SetFocus = original
        for name, method in inputs.items():
            setattr(control_class, name, method)

def read_chat_snapshot(chat, control_class=None):
    with without_focus(control_class) as counts:
        info = chat.ChatInfo()
        records = []
        for message in chat.GetAllMessage():
            attr = getattr(message, 'attr', '')
            kind = getattr(message, 'type', '')
            records.append({
                'id': str(getattr(message, 'id', '')),
                'attr': attr,
                'type': kind,
                'content': getattr(message, 'content', '') if attr == 'friend' and kind == 'text' else '',
                'sender': str(getattr(message, 'sender', '') or '未识别的群成员') if attr == 'friend' else '',
            })
        return info, records, counts['suppressed']
