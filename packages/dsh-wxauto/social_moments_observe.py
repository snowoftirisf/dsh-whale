"""Bounded, read-only Moments metadata collector. No navigation or action binding.

The caller supplies reviewed navigation and the existing backend recovery guard;
see MOMENTS-OBSERVE.md. Import/construction does not access wxauto or the UI.
"""
from contextlib import AbstractContextManager
import hashlib
import json
import math
import re
import threading
import time

VERSION = '4.2.0.12'
ROOT_CLASSES = frozenset(('mmui::SNSContentView', 'mmui::AlbumListView'))
FORBIDDEN = frozenset(('mmui::SnsPublishPanel',))
SAFE_CLASS = re.compile(r'[A-Za-z0-9_:.-]{0,160}')


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _label(value):
    if not isinstance(value, str) or len(value) > 32000:
        raise RuntimeError('social-moments-observation-value-unavailable')
    return {'sha256': _hash(value), 'length': len(value)}


def _read_attr(control, name):
    try:
        return getattr(control, name)
    except Exception:
        return None


def _safe_class(value):
    return value if isinstance(value, str) and SAFE_CLASS.fullmatch(value) else ''


def _geometry(control):
    try:
        rect = control.BoundingRectangle
        result = [getattr(rect, k) for k in ('left', 'top', 'right', 'bottom')]
        if (any(type(n) not in (int, float) or not math.isfinite(n) for n in result)
                or result[2] <= result[0] or result[3] <= result[1]):
            return None
        return result
    except Exception:
        return None


def _pattern(control, method, properties=(), *, text=False):
    """Read only; never invokes an action or sets a value."""
    result = {'available': False}
    try:
        pattern = getattr(control, method)()
        if pattern is None:
            return result
        result['available'] = True
        for prop in properties:
            value = getattr(pattern, prop)
            if isinstance(value, str):
                result[prop] = _label(value)
        if text:
            # Standard UIA TextPattern.DocumentRange.GetText has a character cap.
            value = pattern.DocumentRange.GetText(2048)
            result['documentText'] = _label(value)
            result['textMayBeTruncated'] = len(value) >= 2048
    except Exception:
        result['readFailed'] = True
    return result


def observe_moments(wx, *, recover_chat, navigation_context, ui_thread_id,
                    foreground, max_nodes=256, max_depth=10, clock=time.time):
    """Collect through an already-bound backend and reviewed navigation context.

    recover_chat MUST be the same backend's bound _with_chat_recovery method.
    navigation_context(wx) MUST yield {roots, guard, clientVersion, scope}; guard
    verifies current client/account/foreground/owned-root identity on every call.
    This function deliberately cannot discover or interpret a post on its own.
    """
    backend = getattr(recover_chat, '__self__', None)
    if (backend is None or getattr(recover_chat, '__name__', '') != '_with_chat_recovery'
            or getattr(backend, 'wx', None) is not wx
            or getattr(backend, 'ui_thread_id', None) != ui_thread_id
            or not callable(getattr(backend, '_start_navigation', None))
            or threading.get_ident() != ui_thread_id
            or not callable(navigation_context) or not callable(foreground)
            or type(max_nodes) is not int or not 1 <= max_nodes <= 256
            or type(max_depth) is not int or not 0 <= max_depth <= 10):
        raise RuntimeError('social-moments-observation-context-required')

    def collect():
        # _with_chat_recovery restores only a started navigation context. Capture
        # that original chat before entering any helper that may navigate.
        backend._start_navigation()
        context = navigation_context(wx)
        if not isinstance(context, AbstractContextManager):
            raise RuntimeError('social-moments-observation-navigation-required')
        with context as binding:
            if (type(binding) is not dict or binding.get('clientVersion') != VERSION
                    or binding.get('scope') not in ('moments-feed', 'own-album')
                    or not callable(binding.get('guard'))
                    or not isinstance(binding.get('roots'), (list, tuple))
                    or not 1 <= len(binding['roots']) <= 2):
                raise RuntimeError('social-moments-observation-binding-invalid')
            main = wx._api.control
            pid, handle = main.ProcessId, main.NativeWindowHandle
            main_id = tuple(main.GetRuntimeId())

            def guard():
                # Callback supplies stronger version/account/navigation evidence;
                # false/None is never interpreted as a verified guard.
                if binding['guard']() is not True:
                    raise RuntimeError('social-moments-observation-guard-unverified')
                if (wx._api.control is not main or main.ClassName != 'mmui::MainWindow'
                        or main.ProcessId != pid or main.NativeWindowHandle != handle
                        or tuple(main.GetRuntimeId()) != main_id or foreground() != handle):
                    raise RuntimeError('social-moments-observation-owner-changed')

            guard()
            roots = tuple(binding['roots'])
            if any(root.ClassName not in ROOT_CLASSES for root in roots):
                raise RuntimeError('social-moments-observation-root-unobserved')
            pending = [(root, None, 0, i) for i, root in reversed(tuple(enumerate(roots)))]
            nodes, visited, seen = [], 0, set()
            truncated = False
            while pending:
                if visited >= max_nodes:
                    truncated = True
                    break
                control, parent, depth, root_index = pending.pop()
                visited += 1
                guard()
                if control.ProcessId != pid:
                    raise RuntimeError('social-moments-observation-owner-changed')
                if control.ClassName in FORBIDDEN:
                    raise RuntimeError('social-moments-observation-composer-forbidden')
                offscreen = control.IsOffscreen
                if type(offscreen) not in (int, bool):
                    raise RuntimeError('social-moments-observation-visibility-unverified')
                if offscreen:
                    continue  # Never descend into hidden pages/dialogs.
                runtime_id = tuple(control.GetRuntimeId())
                if not runtime_id or runtime_id in seen:
                    raise RuntimeError('social-moments-observation-tree-ambiguous')
                seen.add(runtime_id)
                name, aid = _read_attr(control, 'Name'), _read_attr(control, 'AutomationId')
                node = {'index': len(nodes), 'parentIndex': parent, 'depth': depth,
                    'rootIndex': root_index, 'class': _safe_class(control.ClassName),
                    'type': _safe_class(control.ControlTypeName),
                    'runtimeIdSha256': _hash(json.dumps(runtime_id, separators=(',', ':'))),
                    'bounds': _geometry(control),
                    'name': _label(name) if isinstance(name, str) else None,
                    'automationId': _label(aid) if isinstance(aid, str) else None,
                    'legacy': _pattern(control, 'GetLegacyIAccessiblePattern', ('Name', 'Value', 'Description')),
                    'value': _pattern(control, 'GetValuePattern', ('Value',)),
                    'text': _pattern(control, 'GetTextPattern', text=True)}
                guard()
                nodes.append(node)
                children = control.GetChildren()
                if depth < max_depth:
                    room = max(0, max_nodes - visited - len(pending))
                    if len(children) > room:
                        truncated = True
                    pending.extend((child, node['index'], depth + 1, root_index)
                                   for child in reversed(children[:room]))
                elif children:
                    truncated = True
            guard()
            return {'version': 1, 'source': 'wxauto-uia-readonly-metadata',
                'clientVersion': VERSION, 'scope': binding['scope'], 'observedAt': clock(),
                'state': 'post-reader-unobserved', 'postIdentityVerified': False,
                'authorVerified': False, 'headBoundaryVerified': False, 'emptyTimelineVerified': False,
                'creationTimeGranularity': 'unobserved', 'nodes': nodes,
                'visitedNodes': visited, 'truncated': truncated}

    # The existing backend owns exact original-chat capture, finally restoration,
    # and stop-on-restoration-failure semantics, including collector exceptions.
    return recover_chat(collect)
