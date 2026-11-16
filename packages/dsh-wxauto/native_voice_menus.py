"""Avatar-menu cleanup helper. Importing it performs no UI actions.

Only explicitly requested Escape on one precisely recognized observed menu is
permitted. Never opens an avatar, invokes a menu item, records names or assumes
that a compiled SDK's SendKeys succeeded. No dependency on real wxauto imports.
"""
import hashlib
import json
import time


class MenuCleanupError(RuntimeError):
    """Fixed code/facts only: no control Name, prompt or private message text."""
    def __init__(self, code, **facts):
        super().__init__(code)
        self.facts = facts


def _rid(control):
    value = control.GetRuntimeId()
    if (not isinstance(value, (list, tuple)) or not value
            or any(type(n) is not int for n in value)):
        raise MenuCleanupError('avatar-menu-unreadable')
    return tuple(value)


def _visible(control):
    value = control.IsOffscreen
    if type(value) not in (bool, int) or value not in (0, 1):
        raise MenuCleanupError('avatar-menu-unreadable')
    return value == 0


def _snapshot(root, uia, *, max_nodes, max_depth):
    """One bounded deduplicated view; never scans unrelated process subtrees."""
    pid = root.ProcessId
    if type(pid) is not int or pid <= 0:
        raise MenuCleanupError('avatar-menu-unreadable')
    roots = [root]
    for control in uia.GetRootControl().GetChildren():
        if control.ProcessId == pid:
            roots.append(control)
    pending = [(control, 0) for control in roots]
    seen, controls, menus = set(), {}, []
    visited = 0
    while pending:
        control, depth = pending.pop()
        visited += 1
        if visited > max_nodes:
            raise MenuCleanupError('avatar-menu-tree-budget')
        if control.ProcessId != pid:
            continue
        identity = _rid(control)
        if identity in seen:
            continue
        seen.add(identity)
        controls[identity] = control
        class_name = control.ClassName
        if class_name == 'mmui::ChatVoiceRecordView':
            raise MenuCleanupError('avatar-menu-recorder-active')
        if class_name == 'mmui::XMenu' and _visible(control):
            menus.append(control)
        children = list(control.GetChildren())
        if children and depth >= max_depth:
            raise MenuCleanupError('avatar-menu-tree-budget')
        pending.extend((child, depth + 1) for child in children)
    return controls, menus


def _foreground(root, uia, allowed_menu=None):
    control = uia.GetForegroundControl()
    if control.ProcessId != root.ProcessId:
        raise MenuCleanupError('avatar-menu-external-foreground')
    if (control.ClassName == 'mmui::MainWindow'
            and control.NativeWindowHandle == root.NativeWindowHandle):
        return
    if allowed_menu is not None and _rid(control) == _rid(allowed_menu):
        return
    raise MenuCleanupError('avatar-menu-foreground-unverified')


def _shape(menu):
    children = list(menu.GetChildren())
    if len(children) not in (1, 2):
        return 'unknown'
    if any(c.ClassName != 'mmui::XMenuView' or not _visible(c) for c in children):
        return 'unknown'
    names = [c.Name for c in children]  # In-memory matching only; never exported.
    # Actually observed own-avatar menu: only 拍一拍. Other one-item menus
    # remain unknown; this does not permit selecting or opening an avatar.
    if len(children) == 1:
        return 'own-pat-only' if names == ['拍一拍'] else 'unknown'
    recognized = (all(isinstance(name, str) for name in names)
            and sum(name.startswith('@') and len(name) > 1 for name in names) == 1
            and names.count('拍一拍') == 1)
    return 'peer-at-and-pat' if recognized else 'unknown'


def _recognized(menu):
    return _shape(menu) != 'unknown'


def observe_avatar_context_menu(root, uia, *, max_nodes=4000, max_depth=26):
    """Read-only bounded menu facts; never inputs or returns any Name text.

    Unknown/multiple menus are observations with fixed refusal codes, not
    authorization to dismiss them. SDK failures never export exception text.
    """
    count = None
    try:
        if (type(max_nodes) is not int or not 1 <= max_nodes <= 10000
                or type(max_depth) is not int or not 1 <= max_depth <= 50):
            raise MenuCleanupError('avatar-menu-invalid-bounds')
        if root.ClassName != 'mmui::MainWindow':
            raise MenuCleanupError('avatar-menu-main-unverified')
        if uia.GetForegroundControl().ProcessId != root.ProcessId:
            raise MenuCleanupError('avatar-menu-external-foreground')
        _, menus = _snapshot(root, uia, max_nodes=max_nodes, max_depth=max_depth)
        count = len(menus)
        _foreground(root, uia, menus[0] if count == 1 else None)
        facts = [{'shape': _shape(menu), 'runtimeIdSha256':
                  hashlib.sha256(json.dumps(_rid(menu)).encode('ascii')).hexdigest()}
                 for menu in menus]
        value = {'observed': True, 'menuCount': count, 'menus': facts}
        if count > 1:
            value['code'] = 'avatar-menu-count-unverified'
        elif any(item['shape'] == 'unknown' for item in facts):
            value['code'] = 'avatar-menu-shape-unverified'
        return value
    except MenuCleanupError as error:
        return {'observed': False, 'code': str(error), **({'menuCount': count} if count is not None else {})}
    except Exception:
        return {'observed': False, 'code': 'avatar-menu-unreadable', **({'menuCount': count} if count is not None else {})}


def close_avatar_context_menu(root, uia, *, timeout=.5, max_nodes=4000, max_depth=26):
    """Close exactly one observed avatar context menu or fail without guessing.

    Returns only non-text facts. Refusals raise fixed MenuCleanupError codes.
    Caller must not retry an unknown outcome as permission for additional input.
    Importing this helper never registers it or triggers automatic cleanup.
    """
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or not 0 <= timeout <= 2 or type(max_nodes) is not int or not 1 <= max_nodes <= 10000
            or type(max_depth) is not int or not 1 <= max_depth <= 50):
        raise MenuCleanupError('avatar-menu-invalid-bounds')
    try:
        if root.ClassName != 'mmui::MainWindow':
            raise MenuCleanupError('avatar-menu-main-unverified')
        # An external foreground refuses before tree enumeration or input.
        if uia.GetForegroundControl().ProcessId != root.ProcessId:
            raise MenuCleanupError('avatar-menu-external-foreground')
        _, menus = _snapshot(root, uia, max_nodes=max_nodes, max_depth=max_depth)
        if not menus:
            _foreground(root, uia)
            return {'closed': False, 'menuCount': 0}
        if len(menus) != 1:
            raise MenuCleanupError('avatar-menu-count-unverified', menuCount=len(menus))
        menu = menus[0]
        if not _recognized(menu):
            raise MenuCleanupError('avatar-menu-shape-unverified')
        _foreground(root, uia, menu)
        identity = _rid(menu)
        digest = hashlib.sha256(json.dumps(identity).encode('ascii')).hexdigest()
        # Only the observed menu control receives Escape, never a generic
        # desktop/root control and never an avatar/menu-item click.
        menu.SendKeys('{Esc}')
        deadline = time.monotonic() + timeout
        while True:
            _foreground(root, uia, menu)
            controls, _ = _snapshot(root, uia, max_nodes=max_nodes, max_depth=max_depth)
            if identity not in controls:
                return {'closed': True, 'menuCount': 1, 'method': 'escape', 'runtimeIdSha256': digest}
            if time.monotonic() >= deadline:
                raise MenuCleanupError('avatar-menu-close-unconfirmed', menuCount=1, runtimeIdSha256=digest)
            time.sleep(.025)
    except MenuCleanupError:
        raise
    except Exception:
        # SDK/COM errors can include control names; never relay their text.
        raise MenuCleanupError('avatar-menu-unreadable') from None
