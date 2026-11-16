"""Compatibility at the wxauto boundary; no native input or client modification."""
import time


class WeChatTreeRecoveryError(RuntimeError):
    pass


def _tree_need(value, code):
    if not value:raise WeChatTreeRecoveryError(code)


def _navigation_exists(root):
    try:
        navigation=root.ToolBarControl(ClassName='mmui::MainTabBar')
        value=navigation.Exists(0)
    except Exception as error:raise WeChatTreeRecoveryError('wechat-tree-navigation-unreadable')from error
    _tree_need(type(value)in(bool,int)and value in(0,1),'wechat-tree-navigation-unreadable')
    return value==1


def _known_collapsed_tree(root):
    children=root.GetChildren()
    if not children:return True
    return (len(children)==1 and children[0].ClassName=='MMUIRenderSubWindowHW'
            and children[0].ProcessId==root.ProcessId and not children[0].GetChildren())


def ensure_main_tree(root, *, uia=None, timeout=1.5, sleep=time.sleep, clock=time.monotonic):
    """Restore ONLY the observed collapsed Qt tree via its exact tray item.

    Normal trees incur no tray enumeration/input. One standard default action,
    never a click/keyboard sequence; unknown shapes/modals/ambiguity refuse.
    Returned root is freshly acquired from the original HWND after recovery.
    """
    if uia is None:
        from wxauto4 import uia
    _tree_need(type(timeout)in(int,float)and 0<timeout<=5,'wechat-tree-invalid-timeout')
    identity=(root.NativeWindowHandle,root.ProcessId)
    _tree_need(root.ClassName=='mmui::MainWindow'and all(type(v)is int and v>0 for v in identity),'wechat-tree-main-unverified')
    if _navigation_exists(root):return {'restored':False,'root':root}
    _tree_need(_known_collapsed_tree(root),'wechat-tree-unknown-shape')
    _assert_no_modal(root,uia)
    desktop=uia.GetRootControl();tops=desktop.GetChildren()
    _tree_need(len(tops)<=512,'wechat-tray-tree-budget')
    for item in tops:
        if item.ProcessId==identity[1] and item.NativeWindowHandle!=identity[0]:
            offscreen=item.IsOffscreen
            _tree_need(type(offscreen)in(bool,int)and offscreen==1,'wechat-tree-unknown-owned-window')
    shells=[item for item in tops if item.ClassName=='Shell_TrayWnd']
    _tree_need(len(shells)==1,'wechat-tray-shell-ambiguous')
    shell=shells[0];shell_pid=shell.ProcessId
    _tree_need(type(shell_pid)is int and shell_pid>0,'wechat-tray-shell-unverified')
    pending=[(shell,0)];matches=[];count=0
    while pending:
        item,depth=pending.pop();count+=1
        _tree_need(count<=256 and depth<=16,'wechat-tray-tree-budget')
        _tree_need(item.ProcessId==shell_pid,'wechat-tray-foreign-node')
        if item.ClassName=='SystemTray.NormalButton'and item.Name==' WeChat':matches.append(item)
        pending.extend((c,depth+1)for c in item.GetChildren())
    _tree_need(len(matches)==1,'wechat-tray-button-ambiguous')
    button=matches[0]
    def validate_button():
        _tree_need(button.ProcessId==shell_pid and button.ClassName=='SystemTray.NormalButton'and button.Name==' WeChat',
            'wechat-tray-button-changed')
        for name,value in (('IsOffscreen',0),('IsEnabled',1)):
            current=getattr(button,name)
            _tree_need(type(current)in(bool,int)and current==value,'wechat-tray-button-unavailable')
        rid=tuple(button.GetRuntimeId());_tree_need(rid and all(type(v)is int for v in rid),'wechat-tray-button-unverified')
        return rid
    rid=validate_button()
    def fresh():
        value=uia.ControlFromHandle(identity[0])
        _tree_need(value.ClassName=='mmui::MainWindow'and(value.NativeWindowHandle,value.ProcessId)==identity,'wechat-tree-main-changed')
        return value
    current=fresh()
    if _navigation_exists(current):return {'restored':False,'root':current}
    _tree_need(_known_collapsed_tree(current),'wechat-tree-unknown-shape')
    _assert_no_modal(current,uia)
    _tree_need(validate_button()==rid,'wechat-tray-button-changed')
    pattern=button.GetLegacyIAccessiblePattern()
    _tree_need(pattern is not None and callable(getattr(pattern,'DoDefaultAction',None)),'wechat-tray-default-action-unavailable')
    try:pattern.DoDefaultAction()
    except Exception as error:raise WeChatTreeRecoveryError('wechat-tray-default-action-unknown')from error
    deadline=clock()+timeout
    while True:
        current=fresh();_assert_no_modal(current,uia)
        if _navigation_exists(current):return {'restored':True,'root':current}
        if clock()>=deadline:raise WeChatTreeRecoveryError('wechat-tree-restore-timeout')
        sleep(.05)


def _assert_no_modal(control, uia):
    foreground = uia.ControlFromHandle(uia.GetForegroundWindow())
    if foreground.ProcessId == control.ProcessId and foreground.ClassName != 'mmui::MainWindow':
        raise RuntimeError('wechat-modal-must-be-closed-before-connect')
    pending = [control]
    count = 0
    while pending:
        current = pending.pop(); count += 1
        if count > 4000:
            raise RuntimeError('wechat-modal-tree-budget')
        if current.ClassName in ('mmui::SnsPublishPanel', 'mmui::XDialog', 'mmui::ChatVoiceRecordView', 'mmui::XLayerWindow', 'mmui::XMenu'):
            raise RuntimeError('wechat-modal-must-be-closed-before-connect')
        pending.extend(current.GetChildren())


def connect():
    import wxauto4.wx as module
    from wxauto4 import uia
    from wxauto4.param import WxParam
    from wxauto4.utils import GetAllWindows
    WxParam.ENABLE_FILE_LOGGER = False
    WxParam.TELEMETRY_ENABLED = False
    candidates = []
    seen = []
    for hwnd, _, title in GetAllWindows():
        if title not in ('微信', 'WeChat', 'Weixin'):
            continue
        control = uia.ControlFromHandle(hwnd)
        seen.append(str(control.ClassName))
        if control.ClassName == 'mmui::MainWindow':
            candidates.append((hwnd, control))
    if len(candidates) != 1:
        # Say what was seen so a remote user can report it (setup/wx-window-probe.py has more).
        raise RuntimeError(f'expected-one-main-window: found {len(candidates)}; '
                           f'WeChat-titled windows by UIA class: {sorted(seen) or "none"}')
    hwnd, control = candidates[0]
    control=ensure_main_tree(control,uia=uia)['root']
    navigation = control.ToolBarControl(ClassName='mmui::MainTabBar')
    labels = [child.Name for child in navigation.GetChildren() if child.ClassName == 'mmui::XTabBarItem']
    label = '微信' if '微信' in labels else 'WeChat' if 'WeChat' in labels else None
    if label is None:
        raise RuntimeError('chat-navigation-unavailable')
    # The 4.2 client can remain on Discover/Moments. The legacy SDK constructs
    # its search/message controls immediately and cannot bind them there.
    # Select the actual observed navigation item before constructing the SDK;
    # activation is required for Qt's popup/navigation controls in this build.
    buttons = [child for child in navigation.GetChildren()
               if child.ClassName == 'mmui::XTabBarItem' and child.Name == label]
    if len(buttons) != 1:
        raise RuntimeError('chat-navigation-ambiguous')
    _assert_no_modal(control, uia)
    buttons[0].Click(show_window=True)
    # wxauto4 41.1.7 contains hard-coded Chinese navigation lookups even when
    # a Chinese WeChat account's brand button is named WeChat.
    original_button = uia.Control.ButtonControl
    original_toolbar = uia.Control.ToolBarControl
    base_button = getattr(original_button, '_whale_alias_base', original_button)
    base_toolbar = getattr(original_toolbar, '_whale_alias_base', original_toolbar)
    navigation_id = navigation.AutomationId
    def toolbar_control(self, *args, **kwargs):
        if kwargs.get('ClassName') == 'mmui::MainTabBar' and kwargs.get('AutomationId') == 'main_tabbar':
            kwargs = {**kwargs, 'AutomationId': navigation_id}
        return base_toolbar(self, *args, **kwargs)
    def button_control(self, *args, **kwargs):
        if kwargs.get('ClassName') == 'mmui::XTabBarItem' and kwargs.get('Name') == '微信':
            kwargs = {**kwargs, 'Name': label}
        return base_button(self, *args, **kwargs)
    button_control._whale_alias_base = base_button
    toolbar_control._whale_alias_base = base_toolbar
    uia.Control.ButtonControl = button_control
    uia.Control.ToolBarControl = toolbar_control
    original_main = module.WeChatMainWnd
    class BoundMain(original_main):
        def __init__(self, nickname=None, hwnd=None, resize=True):
            # Explicit-hwnd branch in this build assumes control already exists.
            self.control = control
            super().__init__(nickname=nickname, hwnd=control.NativeWindowHandle, resize=resize)
    module.WeChatMainWnd = BoundMain
    try:
        result = module.WeChat(start_listener=False, resize=False, ads=False)
    except BaseException:
        uia.Control.ButtonControl = original_button
        uia.Control.ToolBarControl = original_toolbar
        raise
    finally:
        module.WeChatMainWnd = original_main
    return result


def _client_identity(wx):
    from social_wxauto_backend import running_client
    return running_client(wx)


def refresh_chat_binding(wx, target, kind, *, timeout=4):
    """Rebind stale SDK caches after a page rebuild; never receive/seed/send.

    Caller must explicitly resolve all drafts/modals first. No cached session
    object is reused. Only the original wx object's _api reference is replaced,
    and only after fresh exact chat and client identity verification succeeds.
    """
    from wxauto4 import uia
    if not isinstance(target, str) or not target or kind not in ('friend', 'group'):
        raise RuntimeError('wechat-refresh-invalid-target')
    root = wx._api.control
    if root.ClassName != 'mmui::MainWindow':
        raise RuntimeError('wechat-refresh-main-mismatch')
    identity = (root.NativeWindowHandle, root.ProcessId)
    client = _client_identity(wx)
    _assert_no_modal(root, uia)
    root=ensure_main_tree(root,uia=uia)['root']
    navigation = root.ToolBarControl(ClassName='mmui::MainTabBar')
    buttons = [c for c in navigation.GetChildren() if c.ClassName == 'mmui::XTabBarItem'
               and c.Name in ('微信', 'WeChat')]
    if len(buttons) != 1:
        raise RuntimeError('chat-navigation-ambiguous')
    buttons[0].Click(move=True, show_window=True)
    deadline = time.monotonic() + timeout
    while True:
        editor = root.EditControl(ClassName='mmui::ChatInputField')
        offscreen = editor.IsOffscreen
        if editor.Exists(0) and type(offscreen) in (bool, int) and offscreen == 0:
            rect = editor.BoundingRectangle
            if rect.right > rect.left and rect.bottom > rect.top:
                break
        if time.monotonic() >= deadline:
            raise RuntimeError('wechat-refresh-chat-input-unavailable')
        time.sleep(.05)
    fresh = connect()
    def verify():
        fresh_root = fresh._api.control
        if ((fresh_root.NativeWindowHandle, fresh_root.ProcessId) != identity
                or _client_identity(fresh) != client):
            raise RuntimeError('wechat-refresh-client-changed')
    verify()
    fresh.ChatWith(target, exact=True)
    info = fresh.ChatInfo()
    if info.get('chat_name') != target or info.get('chat_type') != kind:
        raise RuntimeError('wechat-refresh-target-mismatch')
    verify()
    _assert_no_modal(fresh._api.control, uia)
    replacement = fresh._api
    try:
        wx._api = replacement
    except (AttributeError, TypeError) as error:
        raise RuntimeError('wechat-refresh-api-not-assignable') from error
    if wx._api is not replacement:
        raise RuntimeError('wechat-refresh-api-not-assigned')
    return wx

if __name__ == '__main__':
    w = connect()
    print('ONLINE=' + str(w.IsOnline()))
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else ''
    w.ChatWith(target, exact=True)
    print('TARGET_MATCH=' + str(w.ChatInfo().get('chat_name') == target))
    print('MESSAGE_SCHEMA=' + str([{'type': getattr(m, 'type', None), 'attr': getattr(m, 'attr', None), 'has_id': bool(getattr(m, 'id', None))} for m in w.GetAllMessage()[-3:]]))
