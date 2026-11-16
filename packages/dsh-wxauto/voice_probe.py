"""Read-only WeChat capability inventory, exclusively via wxauto4 UIA.

Never constructs WeChat(), clicks, focuses, changes chats or records audio.
Names are collected only for buttons, not chat messages or account names.
"""
import json


def inspect():
    from wxauto4 import uia
    from wxauto4.utils import GetAllWindows
    found = []
    for hwnd, _, title in GetAllWindows():
        if title not in ('微信', 'WeChat', 'Weixin'):
            continue
        root = uia.ControlFromHandle(hwnd)
        if root.ClassName != 'mmui::MainWindow':
            continue
        buttons = []
        def walk(node, depth):
            if 'Button' in node.ControlTypeName:
                buttons.append({'class': node.ClassName, 'name': node.Name,
                                'id': node.AutomationId})
            if depth:
                for child in node.GetChildren():
                    # Message history is irrelevant to recording capability.
                    if child.ClassName not in ('mmui::ChatMessageList', 'mmui::ChatTextItemView'):
                        walk(child, depth - 1)
        walk(root, 10)
        found.append({'class': root.ClassName, 'buttons': buttons})
    return {'windows': found}


if __name__ == '__main__':
    print(json.dumps(inspect(), ensure_ascii=False))
