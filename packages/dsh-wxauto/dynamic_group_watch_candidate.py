"""Serialized main-window group admission; never sends or fabricates input."""
import copy
import re
from message_watch import control_key,signature,SessionGate


def _failure(code,*,pending=False,stop=False):
    return {'ok':False,'code':code,'pending':pending,'stopWorker':stop,
            'actualUiGroupConfirmed':False,'routeAdded':False}


def _unread(info):
    flag=info.get('isnew');count=info.get('new_count',0)
    if flag is not True and flag is not False:raise RuntimeError('dynamic-group-unread-state-unproven')
    if flag is False and (not isinstance(count,int) or isinstance(count,bool) or count!=0):
        raise RuntimeError('dynamic-group-unread-state-unproven')
    return flag is True


def _pending(info):
    count=info.get('new_count')
    if not isinstance(count,int) or isinstance(count,bool) or not 1<=count<=20 or not str(info.get('content','')).strip():
        raise RuntimeError('dynamic-group-unread-count-unproven')
    return dict(info)


def register_group(wx,monitor,routes,seen,registry,*,target,native_chat_key,account_id,expected_account_id,native_handoff=None,native_monitor_binding=None):
    """Called only by root's idle, serialized main-window IPC handler.

    Native keys originate in independently validated snapshots, not chat text.
    UI checks prove the current exact name/type, not a native-ID UI extraction.
    Registration restores the original verified chat even on success. With the
    existing recovery mode off, unread admission remains explicitly pending.
    """
    if (not isinstance(account_id,str) or not account_id.strip() or account_id!=expected_account_id):
        return _failure('dynamic-group-account-mismatch')
    if (not isinstance(target,str) or not target.strip() or target!=target.strip() or '\n' in target or '\r' in target
            or not isinstance(native_chat_key,str) or not re.fullmatch(r'wechat:[A-Za-z0-9_-]{1,128}@chatroom',native_chat_key)):
        return _failure('dynamic-group-identity-invalid')
    if not all(isinstance(x,dict) for x in (routes,seen,registry)) or getattr(monitor,'routes',None) is not routes or getattr(monitor,'wx',None) is not wx:
        return _failure('dynamic-group-monitor-binding-mismatch')
    if target in routes and routes[target]!='group':return _failure('dynamic-group-existing-friend-denied')
    if target in registry and registry[target]!=native_chat_key:return _failure('dynamic-group-native-conflict')
    if any(name!=target and key==native_chat_key for name,key in registry.items()):return _failure('dynamic-group-native-already-bound')
    if native_monitor_binding is not None:
        try:
            from native_group_ui_binding import revalidate_native_monitor_binding
            if native_handoff is not None:raise RuntimeError('mixed admission capabilities')
            revalidate_native_monitor_binding(native_monitor_binding,target=target,
                native_chat_key=native_chat_key,account_id=account_id)
        except Exception:return _failure('dynamic-group-native-monitor-binding-unverified',pending=True)
    if native_handoff is not None:
        try:
            from native_group_handoff import revalidate_handoff
            native_handoff=revalidate_handoff(native_handoff,target=target,native_chat_key=native_chat_key)
            if native_handoff.account_id!=account_id:raise RuntimeError('account mismatch')
        except Exception:return _failure('dynamic-group-native-handoff-unverified',pending=True)
    def native_preview(info):
        if native_handoff.last_kind=='voice':
            expected=SessionGate.preview_text(info)
            matched=re.fullmatch(r'\[语音\]\s*(?:(\d+)\s*(?:["″”]|秒)(?:秒)?)?',expected)
            if not matched or matched[1] is not None and not voice_seconds_match(int(matched[1])):
                raise RuntimeError('dynamic-group-native-voice-preview-mismatch')
            return expected
        expected=''.join(SessionGate.preview_text(info).split())
        if not expected or not ''.join(native_handoff.last_text.split()).startswith(expected):
            raise RuntimeError('dynamic-group-native-preview-mismatch')
        return expected
    def voice_seconds_match(seconds):
        duration=native_handoff.last_voice_duration_ms
        return (type(duration) is int and 0<duration<=120000 and 1<=seconds<=120
                and abs(seconds*1000-duration)<=1000)
    def voice_row_matches(row):
        if row.ClassName not in ('mmui::ChatVoiceItemView','mmui::ChatVoiceMessageItemView'):
            return False
        matched=re.fullmatch(r'\s*(?:\[语音\]|语音)\s*(\d+)\s*(?:["″”]|秒)(?:秒)?(?:未播放|已播放)?\s*',row.Name)
        return bool(matched and voice_seconds_match(int(matched[1])))
    added=target not in routes
    def sessions():
        rows=[row for row in wx.GetSession() if row.name==target]
        if len(rows)!=1:raise RuntimeError('dynamic-group-session-missing-or-ambiguous')
        return dict(rows[0].info)
    def exact(name,kind):
        info=wx.ChatInfo()
        return info.get('chat_name')==name and info.get('chat_type')==kind
    native_unread=None;has_unread=False
    try:
        before=sessions() # Even idempotency must refuse a newly ambiguous name.
        if target in routes and registry.get(target)==native_chat_key and native_handoff is None:
            return {'ok':True,'actualUiGroupConfirmed':True,'confirmationReused':True,
                    'checkedThisCall':False,'routeAdded':False,'pending':False,
                    'nativeQueuedHandoff':native_handoff is not None}
        original=wx.ChatInfo();name=original.get('chat_name');kind=original.get('chat_type')
        if kind not in ('friend','group') or routes.get(name)!=kind:
            return _failure('dynamic-group-original-chat-unverified')
        has_unread=_unread(before)
        recover=getattr(monitor,'recover_startup_unread',False) is True or native_monitor_binding is not None
        if native_monitor_binding is not None and has_unread:
            # Establish ownership of *every* unread row before navigation can
            # clear the badge. Never retry a failed media proof as quiet history.
            from native_group_monitor_unread import qualify_native_monitor_unread
            native_unread=qualify_native_monitor_unread(native_monitor_binding,before)
        if native_handoff is not None:
            if has_unread:_pending(before) # Preserve strict integer/count proof.
            if has_unread and before.get('new_count')!=native_handoff.unread_count:
                return _failure('dynamic-group-native-unread-count-mismatch',pending=True)
            native_preview(before)
        elif has_unread and not recover:return _failure('dynamic-unread-recovery-required',pending=True)
        pending=_pending(before) if has_unread and native_handoff is None else None
    except Exception as error:
        code=str(error) if isinstance(error,RuntimeError) and str(error).startswith('dynamic-') else 'dynamic-group-preflight-failed'
        if native_monitor_binding is not None and has_unread:code='dynamic-group-native-unread-proof-required'
        return _failure(code,pending=native_handoff is not None or 'unread' in code)
    cursor=monitor.cursor;old_current=cursor.current
    old_cursor={field:copy.deepcopy(getattr(cursor,field).get(target)) for field in ('rows','accepted','delivery_anchors')}
    present={field:target in getattr(cursor,field) for field in old_cursor}
    seeded=False;failure=None;handoff_observation=None
    routes[target]='group'
    try:
        monitor.select(target)
        if not exact(target,'group') or not exact(target,'group'):raise RuntimeError('dynamic-group-target-mismatch')
        controls=monitor.controls() if native_handoff is not None or native_unread is not None or target not in cursor.rows and not pending else None
        captured=[(control_key(c),signature(c)) for c in controls] if native_handoff is not None else None
        after=sessions();after_unread=_unread(after)
        if native_monitor_binding is not None:
            # A source marker authorizes restoration, never historical SDK replay.
            # Every unread native row was proven before navigation. The UI
            # preview must remain stable while restoring its monitor cursor.
            if after_unread or monitor.preview_identity(before)!=monitor.preview_identity(after):
                raise RuntimeError('dynamic-group-arrival-during-native-restoration')
            if native_unread is not None:
                from native_group_monitor_unread import revalidate_native_monitor_unread
                revalidate_native_monitor_unread(native_unread,native_monitor_binding)
                # The authenticated unread set contains native-owned text/voice
                # only. UI rows provide a cursor, never message source IDs.
                pending=None
            revalidate_native_monitor_binding(native_monitor_binding,target=target,
                native_chat_key=native_chat_key,account_id=account_id)
        if native_handoff is not None:
            handoff_observation={'afterUnread':after_unread,'beforeUnreadCount':before.get('new_count'),
                'afterUnreadCount':after.get('new_count'),'timeUnchanged':before.get('time')==after.get('time'),
                'contentUnchanged':before.get('content')==after.get('content'),
                'renderedPreviewUnchanged':SessionGate.preview_text(before)==SessionGate.preview_text(after)}
            if after_unread or monitor.preview_identity(before)!=monitor.preview_identity(after):
                raise RuntimeError('dynamic-group-arrival-during-native-handoff')
            preview=native_preview(after)
            repeated=monitor.controls()
            if captured!=[(control_key(c),signature(c)) for c in repeated]:
                raise RuntimeError('dynamic-group-native-viewport-changed')
            rows=[c for c in repeated if c.ClassName!='mmui::ChatItemView']
            if native_handoff.last_kind=='voice':
                matching=[c for c in rows if voice_row_matches(c)]
                if not rows or len(matching)!=1 or matching[0] is not rows[-1]:
                    raise RuntimeError('dynamic-group-native-voice-tail-missing-or-ambiguous')
            else:
                matching=[c for c in rows if preview in ''.join(c.Name.split())]
                if (not rows or rows[-1].ClassName not in ('mmui::ChatTextItemView','mmui::ChatTextMessageItemView')
                        or len(matching)!=1 or matching[0] is not rows[-1]):
                    raise RuntimeError('dynamic-group-native-tail-missing-or-ambiguous')
            revalidate_handoff(native_handoff,target=target,native_chat_key=native_chat_key)
        elif after_unread:
            if not recover:raise RuntimeError('dynamic-unread-recovery-required')
            pending=_pending(after)
        elif not has_unread and monitor.preview_identity(before)!=monitor.preview_identity(after):
            raise RuntimeError('dynamic-group-arrival-during-confirmation')
        if not exact(target,'group'):raise RuntimeError('dynamic-group-target-mismatch')
        if (native_handoff is not None or target not in cursor.rows) and pending is None:
            if not controls:raise RuntimeError('dynamic-group-baseline-unavailable')
            seeded=True;cursor.seed(target,controls)
    except Exception as error:
        code=str(error) if isinstance(error,RuntimeError) and str(error).startswith('dynamic-') else 'dynamic-group-confirmation-failed'
        failure=_failure(code,pending=(native_handoff is not None or 'unread' in code or 'arrival' in code or 'baseline' in code))
    # Always restore; a failed restore is fatal to the worker, regardless of
    # the original navigation outcome. Never continue through a stale Chat API.
    try:
        monitor.select(name)
        if not exact(name,kind) or not exact(name,kind):raise RuntimeError('restore-target-mismatch')
    except Exception:
        failure=_failure('dynamic-group-chat-restore-failed',stop=True)
    finally:
        cursor.current=old_current
    if failure is None and native_handoff is not None:
        try:revalidate_handoff(native_handoff,target=target,native_chat_key=native_chat_key)
        except Exception:failure=_failure('dynamic-group-native-handoff-unverified',pending=True)
    if failure is None and native_monitor_binding is not None:
        try:revalidate_native_monitor_binding(native_monitor_binding,target=target,
            native_chat_key=native_chat_key,account_id=account_id)
        except Exception:failure=_failure('dynamic-group-native-monitor-binding-unverified',pending=True)
    if failure:
        if handoff_observation is not None:failure['handoffObservation']=handoff_observation
        if added:routes.pop(target,None)
        if seeded:
            for field,value in old_cursor.items():
                mapping=getattr(cursor,field)
                if present[field]:mapping[target]=value
                else:mapping.pop(target,None)
        return failure
    if pending is not None:monitor.startup_pending[target]=pending
    seen.setdefault(target,set());registry[target]=native_chat_key
    return {'ok':True,'actualUiGroupConfirmed':True,'confirmationReused':False,'checkedThisCall':True,
            'routeAdded':added,'pending':False,'startupUnreadPreserved':pending is not None,
            'historicalBaselineOnly':seeded,'originalChatRestored':True,
            'nativeQueuedHandoff':native_handoff is not None,
            'nativeMonitorRestored':native_monitor_binding is not None,
            'handoffLimitations':(['Native queued IDs establish request source; UI observations only establish a baseline handoff, not exact UI/native IDs or a ScrollPattern bottom proof.'] if native_handoff is not None else [])}
