"""Local wxauto-only navigation inventory; never submits social content.

Use while the Harness UI worker is stopped. Each invocation performs at most
one exact, previously observed navigation click, followed by a fresh inventory.
"""
import argparse
import json
from pathlib import Path
import time
import psutil

from wxauto4 import uia
from wxauto4.utils import GetAllWindows


def walk(node, depth=24):
    yield node
    if depth and node.ClassName not in {
        'mmui::ChatMessageList', 'mmui::ChatTextItemView',
        'mmui::ChatSessionList', 'mmui::ContactListView',
        'mmui::TimelineCell',
    }:
        for child in node.GetChildren():
            yield from walk(child, depth - 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--click-name')
    parser.add_argument('--click-class')
    parser.add_argument('--foreground', action='store_true')
    parser.add_argument('--move', action='store_true')
    parser.add_argument('--right-click', action='store_true')
    parser.add_argument('--invoke', action='store_true')
    parser.add_argument('--show-window', action='store_true')
    parser.add_argument('--screenshot-main')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    status_file = Path(__file__).with_name('status.local.json')
    status = json.loads(status_file.read_text('utf-8-sig'))
    if args.click_name and (status.get('event') != 'worker-exited' or status.get('ready') is not False):
        raise RuntimeError('Harness worker must be stopped before navigation')
    mains = []
    for hwnd, _, title in GetAllWindows():
        if title in ('微信', 'WeChat', 'Weixin'):
            control = uia.ControlFromHandle(hwnd)
            if control.ClassName == 'mmui::MainWindow':
                mains.append(control)
    if len(mains) != 1:
        raise RuntimeError('Expected exactly one actual WeChat main window')
    root = mains[0]
    current = uia.ControlFromHandle(uia.GetForegroundWindow()) if args.foreground else root
    if current.ProcessId != root.ProcessId:
        raise RuntimeError('Foreground window is not the bound WeChat process')
    if args.click_name:
        if not args.click_class:
            raise ValueError('Exact observed class is required')
        allowed = {'发现', 'WeChat', '朋友圈', '通讯录', '聊天信息', '关闭', '返回', '取消', '发表', '发表文字'}
        if args.click_name not in allowed:
            raise ValueError('Only non-submitting navigation is allowed')
        choices = [node for node in walk(current) if node.Name == args.click_name
                   and node.ClassName == args.click_class and
                   ('Button' in node.ControlTypeName or
                    (args.click_name == '发表文字' and node.ControlTypeName == 'MenuItemControl'
                     and node.ClassName == 'mmui::XMenuView' and node.AutomationId == 'XMenuItem'))]
        if len(choices) != 1:
            raise RuntimeError('Observed navigation control is absent or ambiguous')
        if args.click_name == '发表':
            if args.click_class != 'mmui::XTabBarItem':
                raise RuntimeError('Final publish button is not a navigation target')
            parent = choices[0].GetParentControl()
            ancestors = []
            for _ in range(6):
                ancestors.append(parent.ClassName)
                parent = parent.GetParentControl()
            if 'mmui::SNSWindowToolBar' not in ancestors:
                raise RuntimeError('Only the observed timeline editor entry is allowed')
        if args.invoke:
            if args.click_name not in ('取消', '返回'):
                raise RuntimeError('Invoke is limited to cancel/back navigation')
            choices[0].GetInvokePattern().Invoke()
        elif args.right_click:
            if args.click_name != '发表' or args.click_class != 'mmui::XTabBarItem':
                raise RuntimeError('Only the observed timeline entry has a navigation context menu')
            choices[0].RightClick(move=args.move)
        else:
            choices[0].Click(move=args.move, show_window=args.show_window)
        time.sleep(0.4)
    foreground = uia.ControlFromHandle(uia.GetForegroundWindow())
    roots = [('main', root)]
    if foreground.ProcessId == root.ProcessId and foreground.NativeWindowHandle != root.NativeWindowHandle:
        roots.append(('foreground', foreground))
    seen = {node.NativeWindowHandle for _, node in roots}
    for hwnd, _, _ in GetAllWindows():
        if hwnd in seen:
            continue
        current = uia.ControlFromHandle(hwnd)
        belongs = current.ProcessId == root.ProcessId
        if not belongs and current.ClassName.startswith('Chrome_WidgetWin_'):
            try:
                process = psutil.Process(current.ProcessId)
                belongs = (process.name().lower() == 'wechatappex.exe'
                           and root.ProcessId in {parent.pid for parent in process.parents()})
            except psutil.Error:
                pass
        if belongs:
            roots.append(('secondary:' + current.ClassName, current))
            seen.add(hwnd)
    controls = []
    for label, current in roots:
        for node in walk(current):
            kind = node.ControlTypeName
            if any(part in kind for part in ('Button', 'Edit', 'Tab', 'Menu')):
                controls.append({'root': label, 'type': kind, 'class': node.ClassName,
                                 'name': node.Name, 'automationId': node.AutomationId})
    result = {'source': 'wxauto-uia', 'observedAt': time.time(),
              'mainClass': root.ClassName, 'foregroundClass': foreground.ClassName,
              'windowClasses': [node.ClassName for _, node in roots],
              'controls': controls, 'submitted': False}
    if args.screenshot_main:
        result['screenshotSaved'] = root.ScreenShot(args.screenshot_main)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
