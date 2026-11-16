"""Passive UIA change detection. Parsing and input happen only for new text rows."""
import hashlib
import time
import re
from connection_policy import error_diagnostic
from message_direction import read_message_direction

MESSAGE_CURSOR_SNAPSHOT_VERSION = 3


def media_trace(data_root, name, control, stage, code=None):
    """Local-only media receive trace: class names and fixed codes, never content."""
    if not data_root:
        return
    try:
        import json, os
        folder = os.path.join(data_root, 'diagnostics')
        os.makedirs(folder, exist_ok=True)
        entry = {'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'chat': hashlib.sha256(name.encode()).hexdigest()[:12],
                 'class': getattr(control, 'ClassName', None), 'stage': stage, 'code': code}
        with open(os.path.join(folder, 'media-trace.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
    except Exception:
        pass  # Diagnostics must never change receive behavior.


def control_key(control):
    return tuple(control.GetRuntimeId())


def event_key(run_id, name, identity):
    # Qt accessibility ids are reusable, including after chat/window recreation.
    # UI IDs are process-local; this is NOT persistent cross-restart dedup.
    return hashlib.sha256(repr((run_id, name, identity)).encode()).hexdigest()


def signature(control):
    # Runtime ids can be recreated after switching chats. Text/structure survives.
    def names(node, depth):
        result = [node.ClassName, node.Name]
        if depth:
            for child in node.GetChildren():
                result.extend(names(child, depth - 1))
        return result
    return hashlib.sha256(repr(names(control, 3)).encode()).hexdigest()


class MessageCursor:
    def __init__(self):
        self.rows = {}
        self.current = None
        self.accepted = {}
        self.delivery_anchors = {}

    def seed(self, name, controls):
        self.rows[name] = [(control_key(c), signature(c)) for c in controls]
        self.accepted.setdefault(name, set()).update(row[1] for row in self.rows[name])
        self.current = name

    def pending(self, name, controls):
        now = [(control_key(c), signature(c)) for c in controls]
        old = self.rows.get(name)
        if old is None:
            raise RuntimeError('missing-startup-baseline')
        if now == old:
            self.current = name
            return []
        # Find the latest old tail in the newly rendered history. This also
        # handles repeated identical texts using the longest sequence overlap.
        before = [r[1] for r in old]
        after = [r[1] for r in now]
        known = self.accepted.get(name, set()) | set(before)
        anchors = self.delivery_anchors.get(name, set())
        anchor_positions = [index for index, row in enumerate(now) if row[1] in anchors]
        first_sent = min(anchor_positions) if anchor_positions else len(now)
        for length in range(min(len(before), len(after)), 0, -1):
            for end in range(length, len(after) + 1):
                if before[-length:] == after[end-length:end]:
                    # A member may speak between two outgoing bubbles. Keep
                    # unknown rows before the final known anchor as well.
                    return [control for index, control in enumerate(controls)
                            if index >= end or (first_sent < index < end and now[index][1] not in known)]
        if self.current == name and any(key in {r[0] for r in old} for key, _ in now):
            # Runtime IDs are recycled by Qt. A new ID is not evidence of a
            # new message; never replay a previously accepted fingerprint.
            last_known = max((index for index, row in enumerate(now) if row[1] in known), default=-1)
            if last_known >= 0:
                return [control for index, control in enumerate(controls)
                        if now[index][1] not in known and (index > last_known or first_sent < index)]
        if not before:
            return controls
        if not now:
            return []  # UI temporarily unloaded; retain baseline.
        if all(row[1] in self.accepted.get(name, set()) for row in now):
            return []  # Older/recreated viewport, not new input.
        raise RuntimeError('message-history-gap')


class SessionGate:
    def __init__(self):
        self.handled = {}

    @staticmethod
    def fingerprint(info):
        return (info.get('time'), info.get('content'), info.get('new_count'), info.get('isnew'))

    def acknowledge(self, name, info):
        self.handled[name] = self.fingerprint(info)

    def changed(self, name, info):
        return self.fingerprint(info) != self.handled.get(name)

    @staticmethod
    def preview_text(info):
        lines = [line.strip() for line in str(info.get('content', '')).splitlines() if line.strip()]
        lines = [line for line in lines if line != '[有人@我]' and not re.fullmatch(r'\[\d+条\]', line)]
        if not lines:
            return ''
        return lines[0].split(': ', 1)[-1].rstrip('…').removesuffix('...')[:60]


class MainWindowMonitor:
    def __init__(self, wx, routes, receive_voice=False, image_directory=None,
                 attachment_directory=None, data_root=None, recover_startup_unread=False,
                 native_voice_sources=False, native_group_text_authority=None):
        self.wx, self.routes = wx, routes
        self.receive_voice = receive_voice
        self.native_voice_sources = native_voice_sources is True
        if native_group_text_authority is not None and not callable(native_group_text_authority):
            raise ValueError('native-group-text-authority-callback-invalid')
        self.native_group_text_authority = native_group_text_authority
        self.image_directory = image_directory
        self.attachment_directory = attachment_directory
        self.data_root = data_root
        self.cursor, self.gate = MessageCursor(), SessionGate()
        self.histories = {}
        self.recovered_tails = 0
        self.recover_startup_unread = recover_startup_unread is True
        self.startup_pending = {}
        self.startup_uncertain = set()
        self.startup_last_attempt = {}
        # Run-local exact row tokens only, never native/persistent message IDs.
        # Unknown direction is not accepted merely because a later row succeeds.
        self.deferred_direction = {}

    @staticmethod
    def unread(info):
        return bool(info and info.get('isnew') is True)

    def group_text_state(self, name):
        callback = self.native_group_text_authority
        if self.routes.get(name) != 'group' or callback is None:
            return {'mode':'ui'}
        try:
            value = callback(name)
            if type(value) is not dict or value.get('version') != 1 or value.get('mode') not in ('ui','preparing','native','blocked'):
                raise ValueError()
            if value['mode'] == 'native' and (value.get('target') != name
                    or not re.fullmatch(r'wechat:[A-Za-z0-9_-]{1,256}@chatroom',value.get('nativeChatKey',''))
                    or not re.fullmatch(r'[a-f0-9]{32}',value.get('authorityEpoch',''))
                    or not isinstance(value.get('accountId'),str) or not value['accountId']):
                raise ValueError()
            return value
        except Exception:
            return {'mode':'blocked'}

    @staticmethod
    def preview_identity(info):
        return (info.get('time'), info.get('content')) if info else None

    def current(self):
        info = self.wx.ChatInfo()
        return info.get('chat_name') if info.get('chat_type') == self.routes.get(info.get('chat_name')) else None

    def controls(self):
        box = self.wx._api._chat_api.msgbox
        if not box.Exists(0):
            raise RuntimeError('message-list-unavailable')
        return box.GetChildren()

    def select(self, name):
        if name not in self.routes:
            raise RuntimeError('target-or-operation-denied')
        if self.current() == name:
            return
        self.wx.ChatWith(name, exact=True)
        # Search returns after clicking; the chat header can still be rendering.
        # Observe completion without clicking again or relaxing exact matching.
        deadline = time.monotonic() + 2
        matches = 0
        while time.monotonic() < deadline:
            matches = matches + 1 if self.current() == name else 0
            if matches >= 2:
                return
            time.sleep(0.1)
        raise RuntimeError('target-mismatch')

    def sessions(self):
        return {row.name: dict(row.info) for row in self.wx.GetSession() if row.name in self.routes}

    def seed_observed(self, name, controls):
        blocked = self.group_text_state(name)['mode']=='blocked'
        excluded = [c for c in controls if blocked and c.ClassName=='mmui::ChatTextItemView']
        if excluded:
            pending=self.deferred_direction.setdefault(name,{})
            for c in excluded:pending[(control_key(c),signature(c))]=False
        self.cursor.seed(name,[c for c in controls if not (blocked and c.ClassName=='mmui::ChatTextItemView')])
        return not excluded

    def initialize(self):
        original = self.current()
        if not self.recover_startup_unread:
            # Legacy baseline behavior, retained until a real live-bottom
            # proof is observed. Qt 4.2 RecyclerListView has no ScrollPattern.
            for name in self.routes:
                self.select(name)
                observed = self.sessions().get(name)
                may_ack=self.seed_observed(name, self.controls())
                if observed is not None and may_ack:
                    self.gate.acknowledge(name, observed)
            if original:
                self.select(original)
                observed = self.sessions().get(original)
                may_ack=self.seed_observed(original, self.controls())
                if observed is not None and may_ack:
                    self.gate.acknowledge(original, observed)
            return
        # Capture badges BEFORE any navigation can mark those chats read.
        initial = self.sessions()
        for name in self.routes:
            latest = self.sessions().get(name)
            unread = latest if self.unread(latest) else initial.get(name)
            if self.unread(unread):
                self.startup_pending[name] = dict(unread)
                continue  # No selection, seed, parsing, download or acknowledgement.
            before = latest
            self.select(name)
            controls = self.controls()
            after = self.sessions().get(name)
            if self.unread(after):
                self.startup_pending[name] = dict(after)
                continue
            may_ack=self.seed_observed(name, controls)
            if self.preview_identity(before) != self.preview_identity(after):
                # Arrival during selection may already have lost its badge. A
                # changed preview alone cannot prove how many old rows are new.
                self.startup_uncertain.add(name)
            elif before is not None and may_ack:
                self.gate.acknowledge(name, before)
        if original and original not in self.startup_pending and original not in self.startup_uncertain:
            self.select(original)
            # Never reseed: messages arriving after the first baseline are new.

    def candidates(self):
        current = self.current()
        sessions = self.sessions()
        if not self.recover_startup_unread:
            result = [current] if current else []
            result.extend(name for name in self.routes if name != current and name in sessions
                          and self.gate.changed(name, sessions[name]))
            return result
        for name in list(self.startup_uncertain):
            if self.unread(sessions.get(name)):
                self.startup_pending[name] = dict(sessions[name])
                self.startup_uncertain.remove(name)
        result = [current] if current else []
        result.extend(name for name in self.routes if name != current and name in sessions and self.gate.changed(name, sessions[name]))
        result.extend(name for name in self.startup_pending if name not in result)
        # An unproved startup race must not cause idle window-switch polling.
        def eligible(name):
            if name in self.startup_uncertain and not self.fresh_current_tail(name):
                return False
            if name in self.startup_pending and name != current and name in self.startup_last_attempt:
                info = sessions.get(name)
                # A failed proof must not repeatedly switch back to the same
                # cleared badge. A genuinely changed unread badge can retry.
                return self.unread(info) and SessionGate.fingerprint(info) != self.startup_last_attempt[name]
            return True
        return [name for name in result if eligible(name)]

    def fresh_current_tail(self, name):
        # A held startup boundary must not disable later genuinely new messages
        # in the currently open chat. Existing in-run overlap can prove those.
        if self.current() != name:
            return False
        try:
            return bool(self.cursor.pending(name, self.controls()))
        except RuntimeError:
            return False

    def startup_tail(self, name, controls, snapshot):
        count = snapshot.get('new_count')
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 20:
            raise RuntimeError('startup-unread-count-unproven-or-over-limit')
        # Identical text/voice durations can occur in old viewports. An unread
        # badge plus matching text does not prove we are at the live bottom.
        try:
            scroll = self.wx._api._chat_api.msgbox.GetScrollPattern()
            at_bottom = scroll is not None and (scroll.VerticallyScrollable is False or
                scroll.VerticallyScrollable is True and isinstance(scroll.VerticalScrollPercent, (int, float))
                and 99.9 <= scroll.VerticalScrollPercent <= 100)
        except Exception:
            at_bottom = False
        if not at_bottom:
            raise RuntimeError('startup-unread-bottom-unverified')
        rows = [c for c in controls if c.ClassName != 'mmui::ChatItemView']
        if len(rows) < count:
            raise RuntimeError('startup-unread-tail-not-visible')
        selected = rows[-count:]
        tail = selected[-1]
        expected = SessionGate.preview_text(snapshot)
        compact = lambda s: ''.join(s.split())
        voice = re.fullmatch(r'\[语音\]\s*(?:(\d+)\s*(?:["″”]|秒)(?:秒)?)?', expected)
        if voice:
            valid = tail.ClassName in ('mmui::ChatVoiceItemView', 'mmui::ChatVoiceMessageItemView')
            duration = re.search(r'(?:\[语音\]|语音)\s*(\d+)\s*(?:["″”]|秒)', tail.Name)
            valid = valid and (voice.group(1) is None or duration and duration.group(1) == voice.group(1))
        elif expected in ('图片', '[图片]', '表情', '[表情]', '[动画表情]', '动画表情'):
            valid = tail.Name in ('图片', '[图片]', '表情', '[表情]', '[动画表情]', '动画表情')
        else:
            valid = bool(expected) and compact(expected) in compact(tail.Name)
        if not valid:
            raise RuntimeError('startup-unread-preview-tail-mismatch')
        # Only recognized time separators are ignored inside a multi-message
        # suffix. Unknown system rows cannot silently distort unread counting.
        start = next(i for i, row in enumerate(controls) if row is selected[0])
        for row in controls[start:]:
            if row.ClassName == 'mmui::ChatItemView' and not re.fullmatch(
                    r'(?:(?:昨天|前天|星期[一二三四五六日天]|\d{4}年\d{1,2}月\d{1,2}日)\s*)?\d{1,2}:\d{2}', row.Name.strip()):
                raise RuntimeError('startup-unread-separator-unproven')
        allowed = {'text', 'quote', 'voice', 'image', 'emotion', 'file'}
        # Qualify the entire suffix before constructing any SDK message. Its
        # generic parser right-clicks avatars, even for our own outgoing rows.
        # An unread badge does not prove every row in this suffix is incoming.
        for row in selected:
            if row.ClassName == 'mmui::ChatTextItemView' and self.group_text_state(name)['mode'] in ('native','blocked'):
                continue
            if self.native_voice_sources and row.ClassName in ('mmui::ChatVoiceItemView', 'mmui::ChatVoiceMessageItemView'):
                continue  # Observation only; native source resolves incoming/self later.
            if self.message_direction(row) != 'friend':
                raise RuntimeError('startup-unread-sender-or-type-unproven')
        for row in selected:
            if row.ClassName == 'mmui::ChatTextItemView' and self.group_text_state(name)['mode'] in ('native','blocked'):
                continue
            if self.native_voice_sources and row.ClassName in ('mmui::ChatVoiceItemView', 'mmui::ChatVoiceMessageItemView'):
                continue
            message = (self.parse_private(row,name) if self.routes.get(name)=='friend'
                else self.parse_incoming(row))
            if getattr(message, 'attr', '') != 'friend' or getattr(message, 'type', '') not in allowed:
                raise RuntimeError('startup-unread-sender-or-type-unproven')
        return selected

    def note_outbound(self, name):
        """Record our own visible rows without consuming incoming member rows."""
        if self.current() != name:
            raise RuntimeError('target-mismatch')
        before = self.cursor.rows.get(name, [])
        known = set(before)
        added = []
        for control in self.controls():
            try:
                row = (control_key(control), signature(control))
                if row in known:
                    continue
                if control.ClassName=='mmui::ChatItemView':
                    continue  # Time/system separators are not human bubbles.
                if control.ClassName=='mmui::ChatTextItemView' and self.group_text_state(name)['mode'] in ('native','blocked'):
                    continue  # No SDK author lookup even when noting our own send.
                if self.message_direction(control) == 'self':
                    added.append(row)
            except Exception:
                continue
        self.cursor.rows[name] = (before + added)[-100:]
        self.cursor.delivery_anchors.setdefault(name, set()).update(row[1] for row in added)
        self.cursor.current = name

    def parse_incoming(self, control):
        """Retain SDK sender identity, but close only its observed avatar popup."""
        from wxauto4.msgs.msg import parse_msg
        try:
            return parse_msg(control,self.wx._api._chat_api)
        finally:
            # Parsing currently opens a context menu to resolve direction/name.
            # Never leave that keyboard grab behind after actual input parsing.
            # Unknown menus are not dismissed; cleanup must not lose a received row.
            try:
                from wxauto4 import uia
                from native_voice_menus import close_avatar_context_menu
                close_avatar_context_menu(self.wx._api.control,uia)
            except Exception:
                pass

    def parse_private(self, control, name):
        """Use the bound private peer, after the caller qualified an incoming row."""
        if self.routes.get(name)!='friend' or self.current()!=name:
            raise RuntimeError('target-mismatch')
        from wxauto4.msgs.msg import parse_msg_type
        return parse_msg_type(control,self.wx._api._chat_api,'Friend',name)

    def transcribe_voice(self,message,name):
        # Preserve the original receive path while raw-audio acquisition is
        # integrated. The experimental visual group shim is not enabled.
        return message.to_text()

    def prepare_direction_read(self):
        """Acquire the current chat once for a real pending-message batch."""
        from wxauto4 import uia
        from native_voice_menus import close_avatar_context_menu
        root=self.wx._api.control
        if uia.GetForegroundControl().NativeWindowHandle!=root.NativeWindowHandle:
            root.SetActive(waitTime=.1)
        if uia.GetForegroundControl().NativeWindowHandle!=root.NativeWindowHandle:
            raise RuntimeError('message-direction-screen-foreground-required')
        if root.ClassName=='mmui::MainWindow':
            close_avatar_context_menu(root,uia)

    def message_direction(self, control):
        return read_message_direction(control,self.wx._api.control,
            msgbox=self.wx._api._chat_api.msgbox)

    def read(self, name):
        observed = self.sessions().get(name)
        startup = self.startup_pending.get(name) if self.recover_startup_unread else None
        if self.recover_startup_unread and name in self.startup_uncertain:
            if not self.unread(observed):
                if not self.fresh_current_tail(name):
                    raise RuntimeError('startup-arrival-boundary-unproven')
            else:
                startup = self.startup_pending[name] = dict(observed)
                self.startup_uncertain.remove(name)
        if startup and self.unread(observed):
            startup = self.startup_pending[name] = dict(observed)
        if startup:
            self.startup_last_attempt[name] = SessionGate.fingerprint(startup)
            # Validate the bound before selecting/clearing any badge.
            count = startup.get('new_count')
            if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 20:
                raise RuntimeError('startup-unread-count-unproven-or-over-limit')
            observed = startup
        self.select(name)
        controls = self.controls()
        changed = observed is not None and self.gate.changed(name, observed)
        expected = SessionGate.preview_text(observed or {})
        normalize = lambda value: ''.join(value.split())
        media_labels = {'图片', '[图片]', '表情', '[表情]', '动画表情', '[动画表情]'}
        voice_preview = re.fullmatch(r'\[语音\]\s*(?:(\d+)\s*(?:["″”]|秒)(?:秒)?)?', expected)
        def has_latest():
            if expected in media_labels:
                return any(control.Name in media_labels for control in controls)
            if voice_preview:
                # A session label such as [语音] 3" differs from the UIA
                # bubble name 语音3"秒未播放. Match only the final message,
                # never some older voice elsewhere in the viewport.
                rows = [c for c in controls if c.ClassName != 'mmui::ChatItemView']
                if not rows or rows[-1].ClassName not in ('mmui::ChatVoiceItemView', 'mmui::ChatVoiceMessageItemView'):
                    return False
                tail = rows[-1]
                duration = re.search(r'(?:\[语音\]|语音)\s*(\d+)\s*(?:["″”]|秒)', tail.Name)
                if voice_preview.group(1) is not None and (duration is None or int(duration.group(1)) != int(voice_preview.group(1))):
                    return False
                # A matching duration is not a unique message identity. An
                # already accepted voice must not authorize gap recovery for
                # an unseen newer preview (including when unread is now 0).
                return signature(tail) not in self.cursor.accepted.get(name, set())
            return bool(expected) and any(normalize(expected) in normalize(control.Name) for control in controls)
        # UIA exposes the current viewport, not the newest chat messages. A
        # long outgoing bubble can leave that viewport behind the live tail.
        # Move DOWN only for a changed preview, never read older history.
        if changed and expected and (observed.get('isnew') or not has_latest()):
            for _ in range(3):
                if self.current() != name:
                    raise RuntimeError('target-mismatch')
                self.wx._api._chat_api.msgbox.WheelDown(wheelTimes=8, api=False)
                controls = self.controls()
                if has_latest():
                    break
            if not has_latest():
                raise RuntimeError('new-message-not-visible')
        confirmed_new_tail = changed and has_latest()
        # Media APIs may scroll/recreate the UI. Retain immutable observations,
        # rather than dereferencing those old controls again after extraction.
        captured_rows = [(control_key(c), signature(c)) for c in controls]
        try:
            pending = self.startup_tail(name, controls, startup) if startup else self.cursor.pending(name, controls)
        except RuntimeError as error:
            if str(error) != 'message-history-gap' or not confirmed_new_tail:
                raise
            # Only a positively matched newer session preview permits recovery
            # across a virtualized viewport gap. Known rows are never replayed.
            accepted = self.cursor.accepted.get(name, set())
            pending = [control for control in controls if signature(control) not in accepted]
            self.recovered_tails += 1
        if not pending and confirmed_new_tail and observed.get('isnew') and observed.get('new_count', 0) > 0:
            # An actual unread count plus a verified newest viewport can prove a
            # newly arrived repeated text/photo even when its visible text is identical.
            message_rows = [control for control in controls if control.ClassName != 'mmui::ChatItemView']
            pending = message_rows[-min(int(observed['new_count']), len(message_rows), 20):]
        deferred = self.deferred_direction.setdefault(name, {})
        by_token = {(control_key(c), signature(c)): c for c in controls}
        pending_tokens = {(control_key(c), signature(c)) for c in pending}
        # A later accepted tail must not hide an earlier unclassified row.
        # Rebind only the exact runtime+fingerprint token, never a matching name
        # or a recycled runtime ID whose contents have changed.
        wanted = pending_tokens | (set(deferred) & set(by_token))
        if len(set(deferred) | wanted) > 256:
            raise RuntimeError('message-direction-deferred-budget')
        pending = [c for c in controls if (control_key(c), signature(c)) in wanted]
        records = []
        prepared_direction=False
        unresolved_keys=set()
        for control in pending:
            # Parse only known message rows. Downloads happen only after sender qualification.
            voice_row = control.ClassName in ('mmui::ChatVoiceItemView', 'mmui::ChatVoiceMessageItemView')
            media_bubble = control.ClassName in ('mmui::ChatBubbleItemView', 'mmui::ChatBubbleReferItemView')
            image_row = control.ClassName in ('mmui::ChatImageItemView', 'mmui::ChatImageMessageItemView') or (media_bubble and control.Name == '图片')
            # wxauto4 41.1.7 classifies the exact label [图片] as an emotion.
            emotion_row = control.ClassName in ('mmui::ChatEmotionItemView', 'mmui::ChatStickerItemView') or (media_bubble and control.Name in ('[图片]', '[表情]', '[动画表情]', '动画表情'))
            file_row = control.ClassName in ('mmui::ChatFileItemView', 'mmui::ChatFileMessageItemView') or media_bubble
            if control.ClassName != 'mmui::ChatTextItemView' and not ((self.receive_voice or self.native_voice_sources) and voice_row) and not (self.image_directory and (image_row or emotion_row)) and not (self.attachment_directory and file_row):
                if control.ClassName != 'mmui::ChatItemView':
                    media_trace(self.data_root, name, control, 'class-skipped')
                continue
            token=(control_key(control),signature(control))
            traced = control.ClassName != 'mmui::ChatTextItemView' and not voice_row
            if traced:
                media_trace(self.data_root, name, control, 'pending')
            if control.ClassName == 'mmui::ChatTextItemView':
                authority=self.group_text_state(name)
                if authority['mode']=='native':
                    deferred.pop(token,None)
                    records.append({'observation':'native-group-text','id':str(token),
                                    'nativeChatKey':authority['nativeChatKey'],'authorityEpoch':authority['authorityEpoch']})
                    continue
                if authority['mode']=='blocked':
                    deferred[token]=False
                    unresolved_keys.add(control_key(control))
                    records.append({'observation':'native-group-text-blocked','id':str(token)})
                    continue
            if voice_row and self.native_voice_sources:
                # This is a run-local observation, NOT an incoming message or
                # native ID. Do not infer sender/direction, parse, or transcribe.
                # The unchanged captured-row cursor commit consumes it once.
                deferred.pop(token,None)
                records.append({'observation':'native-voice','id':str(token)})
                continue
            try:
                try:
                    direction=self.message_direction(control)
                except RuntimeError as error:
                    if str(error)!='message-direction-screen-foreground-required':
                        raise
                    # Only a proven visible row needing the screen fallback may
                    # acquire foreground. One attempt per exact token prevents
                    # idle retries of a deferred row from repeatedly stealing it.
                    if not prepared_direction and not deferred.get(token,False):
                        deferred[token]=True
                        prepared_direction=True
                        self.prepare_direction_read()
                        direction=self.message_direction(control)
                    else:
                        raise
                if direction not in ('self','friend'):
                    raise RuntimeError('message-direction-unknown')
            except RuntimeError as error:
                if not (str(error).startswith('message-direction-')
                        or str(error).startswith('avatar-menu-')):
                    raise
                deferred.setdefault(token,False)
                unresolved_keys.add(token[0])
                if traced:
                    media_trace(self.data_root, name, control, 'direction-deferred', str(error)[:80])
                continue  # Do not invoke SDK, consume the row or block its peers.
            deferred.pop(token,None)
            if direction=='self':
                if traced:
                    media_trace(self.data_root, name, control, 'direction-self')
                continue  # Never let our own refreshed bubble reach SDK parsing.
            message = (self.parse_private(control,name) if self.routes.get(name)=='friend'
                else self.parse_incoming(control))
            if getattr(message, 'attr', '') != 'friend':
                if traced:
                    media_trace(self.data_root, name, control, 'sdk-attr', str(getattr(message, 'attr', ''))[:20])
                continue
            kind = getattr(message, 'type', '')
            if kind == 'other' and self.image_directory and control.ClassName in ('mmui::ChatImageItemView', 'mmui::ChatImageMessageItemView'):
                # wxauto4 classifies WeChat 4.x photo rows as 'other'. Rebuild the
                # SDK's own image message for this exact, already-qualified row.
                try:
                    from wxauto4.msgs.friend import FriendImageMessage
                    sender = getattr(message, 'sender', None)
                    message, kind = FriendImageMessage(control, self.wx._api._chat_api), 'image'
                    if sender and getattr(message, 'sender', None) != sender:
                        try:
                            message.sender = sender
                        except Exception:
                            pass
                    media_trace(self.data_root, name, control, 'rebuilt-image')
                except Exception as error:
                    media_trace(self.data_root, name, control, 'rebuild-failed', type(error).__name__)
            if kind not in ('text', 'quote', 'voice', 'image', 'emotion', 'file') or (kind == 'voice' and not self.receive_voice) or (kind in ('image','emotion') and not self.image_directory) or (kind == 'file' and not self.attachment_directory):
                if traced:
                    media_trace(self.data_root, name, control, 'sdk-kind', str(kind)[:20])
                continue
            if traced:
                media_trace(self.data_root, name, control, 'accepted', str(kind)[:20])
            identity = str((control_key(control), signature(control)))
            content = getattr(message, 'content', '')
            images = []
            files = []
            media_error = None
            media_diagnostic = None
            if kind in ('image','emotion'):
                try:
                    from incoming_images import download_image, capture_emotion
                    # Per-chat directories keep source identity attached across merged batches.
                    from pathlib import Path
                    chat_key = hashlib.sha256(name.encode()).hexdigest()[:16]
                    directory = Path(self.image_directory) / chat_key
                    if kind == 'emotion':
                        images = [capture_emotion(message, directory, ensure_chat=lambda: self.current() == name)]
                        content = '[收到一条微信表情包；附件为该条表情的可见帧采样]'
                    else:
                        images = [download_image(message, directory, ensure_chat=lambda: self.current() == name,
                                                 aim=self.routes.get(name) == 'group')]
                        media_trace(self.data_root, name, control, 'image-' + str(images[0].get('source')),
                                    (images[0].get('downloadDiagnostic') or {}).get('attribute'))
                        content = '[收到一张微信图片]'
                except Exception as error:
                    media_error = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                    media_diagnostic = error_diagnostic(error, 'receive-' + kind)
                    content = '[收到一条微信图片或表情，但取图失败；图片内容未知，不要猜测或假装看见。]'
            if kind == 'file':
                try:
                    from incoming_files import download_file
                    from attachment_registry import chat_key
                    from pathlib import Path
                    directory = Path(self.attachment_directory) / chat_key(name)
                    files = [download_file(message, directory,
                        ensure_chat=lambda: self.current() == name, media_root=self.data_root)]
                    content = '[收到一个微信附件：' + files[0]['name'] + '；已下载，尚未读取内容]'
                except Exception as error:
                    media_error = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                    media_diagnostic = error_diagnostic(error, 'receive-file')
                    content = '[收到一个微信附件，但下载失败；内容未知，不要猜测或假装已读取。]'
            if kind == 'voice':
                # wxauto's existing native transcription route obtains text
                # without decrypting databases or saving incoming voice files.
                voice_class = control.ClassName
                try:
                    transcript=self.transcribe_voice(message,name)
                    if not isinstance(transcript, str) or not transcript.strip():
                        raise ValueError('empty-voice-transcript')
                    content = '[微信语音转文字，可能有识别误差]\n' + transcript.strip()
                except Exception as error:
                    media_error=(str(error) if isinstance(error,RuntimeError) and str(error).startswith('voice-transcription-') else type(error).__name__)
                    media_diagnostic=error_diagnostic(error,'receive-voice')
                    content = '[收到一条微信语音，但转文字失败；内容未知，不要猜测或假装听清。]'
                finally:
                    # Only this synchronous SDK transcription may change this
                    # voice row's label. Capture immediately, never siblings or
                    # the whole mutable viewport at the end of a batch.
                    try:
                        if control_key(control) == token[0] and control.ClassName == voice_class and voice_class in (
                                'mmui::ChatVoiceItemView','mmui::ChatVoiceMessageItemView'):
                            index = next(i for i,c in enumerate(controls) if c is control)
                            if captured_rows[index][0] == token[0]:
                                captured_rows[index] = (token[0],signature(control))
                    except Exception:
                        pass  # Invalidated UIA control: retain its original snapshot.
            records.append({'id': identity,
                            'text': content,
                            'mediaType': kind, 'images': images, 'files': files, 'mediaError': media_error, 'mediaDiagnostic': media_diagnostic,
                            'sender': str(getattr(message, 'sender', '') or '未识别的群成员')})
        # Commit exactly the captured rows, never rows that arrived during parsing.
        committed_rows=[row for row in captured_rows if row[0] not in unresolved_keys]
        if committed_rows:
            self.cursor.rows[name] = committed_rows
            self.cursor.accepted.setdefault(name, set()).update(row[1] for row in committed_rows)
        self.cursor.delivery_anchors.pop(name, None)
        self.cursor.current = name
        # Do not acknowledge a newer preview that arrived during parsing, or
        # switching to another target immediately afterwards could lose it.
        blocked_text = any(r.get('observation')=='native-group-text-blocked' for r in records)
        if observed is not None and not blocked_text:
            latest = self.sessions().get(name) if startup else None
            acknowledged = latest if latest is not None and self.preview_identity(latest) == self.preview_identity(observed) else observed
            self.gate.acknowledge(name, acknowledged)
        if self.recover_startup_unread and not blocked_text:
            self.startup_pending.pop(name, None)
            self.startup_last_attempt.pop(name, None)
        return records

    def acknowledge(self, name):
        info = self.sessions().get(name)
        if info is not None:
            self.gate.acknowledge(name, info)
