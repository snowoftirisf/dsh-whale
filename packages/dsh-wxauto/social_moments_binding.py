"""Unregistered text-only candidate for observed WeChat 4.2.0.12 controls.

No load_bindings entry, receipts or import-time UI operations. Own-author and
timeline extraction remain unobserved; publishing is refused until a real,
source-reviewable reader is supplied. Tests use explicit synthetic controls.
"""
import hashlib
import copy
import inspect
import math
from pathlib import Path
import time

from social_actions import _digest
from social_wxauto_backend import ActionBinding

VERSION = '4.2.0.12'
ACTION = 'publish_moment'


def walk(root, max_depth=26, max_nodes=4000):
    pending = [(root, 0)]; count = 0
    while pending:
        control, depth = pending.pop(); count += 1
        if count > max_nodes:
            raise RuntimeError('social-moments-tree-budget')
        yield control
        if depth < max_depth:
            pending.extend((child, depth + 1) for child in reversed(control.GetChildren()))


def unique(root, class_name, name=None, *, automation_id=None):
    matches = [control for control in walk(root) if control.ClassName == class_name
               and (name is None or control.Name == name)
               and (automation_id is None or control.AutomationId == automation_id)]
    if len(matches) != 1:
        error=RuntimeError('social-moments-control-missing-or-ambiguous')
        error.expectedClass=class_name
        error.expectedName=name
        error.matches=len(matches)
        raise error
    return matches[0]


def describe(control):
    return {'className': control.ClassName, 'name': control.Name,
            'automationId': control.AutomationId, 'runtimeId': list(control.GetRuntimeId())}


def privacy_state(control):
    """Read the observed draft summary, never infer a radio selection.

    Evidence: artifacts/social-draft-probe-session-20261102/inspect-002.json.
    PublishPrivacyView.Name stays '谁可以看  '; descendant XTextView text
    supplies the label and actual current value (observed: 公开).
    """
    texts = [c.Name for c in walk(control) if c is not control
             and c.ClassName == 'mmui::XTextView' and c.ControlTypeName == 'TextControl']
    if (len(texts) < 2 or texts[0] != '谁可以看'
            or any(not isinstance(t, str) or not t.strip() for t in texts)
            or not any(t != '谁可以看' for t in texts[1:])):
        raise RuntimeError('social-moments-privacy-value-unavailable')
    return {**describe(control), 'textSequence': texts}


class TextMomentCandidate:
    def __init__(self, *, own_author=None, read_own_timeline=None, clock=time.time, timeout=4):
        self.own_author, self.read_own_timeline = own_author, read_own_timeline
        self.clock, self.timeout = clock, timeout
        self.prepared = None
        self.owned_draft = None

    @staticmethod
    def parameters(parameters):
        if not isinstance(parameters, dict) or set(parameters) - {'text', 'mediaFiles'}:
            raise RuntimeError('social-moments-invalid-parameters')
        if parameters.get('mediaFiles', []) != []:
            raise RuntimeError('social-moments-media-unobserved')
        text = parameters.get('text')
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError('social-moments-empty-text')
        return text

    def main(self, wx):
        control = wx._api.control
        if control.ClassName != 'mmui::MainWindow':
            raise RuntimeError('social-moments-main-window-mismatch')
        return control

    def wait_control(self, root, class_name, name=None, *, click_ready=False, **kwargs):
        deadline = time.monotonic() + self.timeout
        previous = None
        while True:
            try:
                control = unique(root, class_name, name, **kwargs)
                if not click_ready:
                    return control
                rect = control.BoundingRectangle
                bounds = tuple(getattr(rect, part) for part in ('left', 'top', 'right', 'bottom'))
                offscreen, enabled = control.IsOffscreen, control.IsEnabled
                if (type(offscreen) not in (bool, int) or offscreen != 0
                        or type(enabled) not in (bool, int) or enabled != 1
                        or any(not isinstance(n, (int, float)) or not math.isfinite(n) for n in bounds)
                        or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]):
                    raise RuntimeError('social-moments-control-not-click-ready')
                sample = (tuple(control.GetRuntimeId()), bounds)
                if sample == previous:
                    return control
                previous = sample
            except (RuntimeError, AttributeError):
                previous = None
                if time.monotonic() >= deadline:
                    raise
            if time.monotonic() >= deadline:
                raise RuntimeError('social-moments-control-not-stable')
            time.sleep(.02)

    def editor(self, wx):
        return unique(self.main(wx), 'mmui::SnsPublishPanel')

    def prepare_draft(self, wx, parameters):
        """Actual observed navigation, draft only. Never presses final publish."""
        text = self.parameters(parameters)  # Reject media before any UI call.
        root = self.main(wx)
        # Never overwrite an existing user draft.
        if any(c.ClassName == 'mmui::SnsPublishPanel' for c in walk(root)):
            raise RuntimeError('social-moments-existing-draft')
        # SDK Show/SetActive can leave only a render canvas. Use the narrowly
        # observed shared recovery, without toggling an already healthy tree.
        from wxauto_connection import ensure_main_tree
        recovered=ensure_main_tree(root)
        root=recovered['root']
        if recovered['restored']:
            try:wx._api.control=root
            except (AttributeError,TypeError)as error:raise RuntimeError('social-moments-restored-control-unassignable')from error
        self.wait_control(root, 'mmui::XTabBarItem', '发现', click_ready=True).Click(move=True,show_window=True)
        self.wait_control(root, 'mmui::ExtensionDiscoverContentCell', '朋友圈', click_ready=True).Click(move=True,show_window=True)
        feed = self.wait_control(root, 'mmui::SNSContentView')
        # The actual feed metadata places its toolbar under SNSContentView.
        # Do not select a cached/other-page toolbar from the entire main tree.
        toolbar = self.wait_control(feed, 'mmui::SNSWindowToolBar')
        self.wait_control(toolbar, 'mmui::XTabBarItem', '发表', click_ready=True).RightClick(move=True,show_window=True)
        self.wait_control(root, 'mmui::XMenuView', '发表文字', automation_id='XMenuItem', click_ready=True).Click(move=True,show_window=True)
        panel = self.wait_control(root, 'mmui::SnsPublishPanel')
        edit = unique(panel, 'mmui::XValidatorTextEdit')
        value = edit.GetValuePattern()
        if value is None or value.Value not in ('', None):
            raise RuntimeError('social-moments-draft-value-unavailable-or-not-empty')
        value.SetValue(text)
        if value.Value != text:
            raise RuntimeError('social-moments-draft-readback-mismatch')
        # Ownership is acquired only after an exact actual write/readback. A
        # later failure (e.g. privacy lookup) can safely offer explicit cleanup.
        self.owned_draft = {'panelRuntimeId': list(panel.GetRuntimeId()),
                            'editRuntimeId': list(edit.GetRuntimeId()), 'text': text}
        privacy = unique(panel, 'mmui::PublishPrivacyView')
        visibility = privacy_state(privacy)
        return {'panelRuntimeId': list(panel.GetRuntimeId()), 'privacy': visibility,
                'controls': [describe(panel), {**describe(edit), 'draftText': value.Value}, visibility]}

    def _owned_editor(self, wx, owned):
        panel = self.editor(wx)
        edit = unique(panel, 'mmui::XValidatorTextEdit')
        pattern = edit.GetValuePattern()
        if (list(panel.GetRuntimeId()) != owned['panelRuntimeId']
                or list(edit.GetRuntimeId()) != owned['editRuntimeId']
                or pattern is None or pattern.Value != owned['text']):
            raise RuntimeError('social-moments-owned-draft-mismatch')
        return panel, edit

    def cancel_owned_draft(self, wx, *, expected_text=None):
        """Cancel/discard ONLY this instance's exact written draft, once.

        XDialog and its 保留/不保留 buttons were actually observed. No generic
        dialog dismissal, guessed selectors or deletion of another draft.
        """
        owned = self.owned_draft
        if owned is None:
            raise RuntimeError('social-moments-no-owned-draft')
        if expected_text is not None and expected_text != owned['text']:
            raise RuntimeError('social-moments-owned-sentinel-mismatch')
        root = self.main(wx)
        if any(c.ClassName == 'mmui::XDialog' for c in walk(root)):
            raise RuntimeError('social-moments-existing-dialog')
        panel, edit = self._owned_editor(wx, owned)
        self.prepared = None
        cancel = self.wait_control(panel, 'mmui::XOutlineButton', '取消', click_ready=True)
        self._owned_editor(wx, owned)
        cancel.Click(move=True, show_window=True)
        dialog = self.wait_control(root, 'mmui::XDialog')
        # Re-read behind the modal. If Qt makes that unavailable, fail closed;
        # do not infer ownership from a generic discard prompt alone.
        self._owned_editor(wx, owned)
        keep = unique(dialog, 'mmui::XOutlineButton', '保留')
        discard = self.wait_control(dialog, 'mmui::XOutlineButton', '不保留', click_ready=True)
        self._owned_editor(wx, owned)
        controls = [describe(panel), describe(edit), describe(dialog), describe(keep), describe(discard)]
        # Destructive click outcomes are never automatically retried.
        self.owned_draft = None
        discard.Click(move=True, show_window=True)
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = [c.ClassName for c in walk(root)]
            if 'mmui::SnsPublishPanel' not in remaining and 'mmui::XDialog' not in remaining:
                return {'source': 'wxauto-uia', 'discarded': True, 'editorClosed': True,
                        'draftSha256': hashlib.sha256(owned['text'].encode('utf8')).hexdigest(),
                        'controls': controls}
            if time.monotonic() >= deadline:
                raise RuntimeError('social-moments-discard-outcome-unknown')
            time.sleep(.1)

    def timeline(self, wx):
        if not isinstance(self.own_author, str) or not self.own_author or not callable(self.read_own_timeline):
            raise RuntimeError('social-moments-own-timeline-unobserved')
        started = self.clock()
        value = self.read_own_timeline(wx)
        if (not isinstance(value, dict) or value.get('source') != 'wxauto-uia'
                or value.get('clientVersion') != VERSION or value.get('scope') != 'own-timeline'
                or value.get('author') != self.own_author or not isinstance(value.get('controls'), list)
                or not value['controls']
                or not isinstance(value.get('observedAt'), (int, float))
                or not started <= value['observedAt'] <= self.clock() + 5
                or not isinstance(value.get('entries'), list)):
            raise RuntimeError('social-moments-own-timeline-unverified')
        for entry in value['entries']:
            if (not isinstance(entry, dict) or entry.get('author') != self.own_author
                    or not isinstance(entry.get('text'), str)
                    or not isinstance(entry.get('runtimeId'), list) or not entry['runtimeId']
                    or any(not isinstance(n, int) or isinstance(n, bool) for n in entry['runtimeId'])):
                raise RuntimeError('social-moments-timeline-entry-unverified')
        return copy.deepcopy(value)

    def before(self, wx, parameters):
        text = self.parameters(parameters)
        # Fail BEFORE navigation/publication when author/timeline APIs lack evidence.
        timeline = self.timeline(wx)
        if any(entry['text'] == text for entry in timeline['entries']):
            raise RuntimeError('social-moments-existing-identical-post-ambiguous')
        draft = self.prepare_draft(wx, parameters)
        self.prepared = {'hash': _digest(ACTION, parameters), 'draft': draft}
        return {'source': 'wxauto-uia', 'action': ACTION, 'phase': 'before', 'clientVersion': VERSION,
                'parametersHash': _digest(ACTION, parameters), 'observedAt': self.clock(),
                'controls': draft['controls'], 'draft': draft, 'previousTimeline': timeline}

    def perform(self, wx, parameters):
        text = self.parameters(parameters)
        # Require a usable observer even if called directly outside the backend.
        if not self.own_author or not callable(self.read_own_timeline):
            raise RuntimeError('social-moments-own-timeline-unobserved')
        if not self.prepared or self.prepared['hash'] != _digest(ACTION, parameters):
            raise RuntimeError('social-moments-before-evidence-required')
        panel = self.editor(wx)
        publish = self.wait_control(panel, 'mmui::XOutlineButton', '发表', click_ready=True)
        if list(panel.GetRuntimeId()) != self.prepared['draft']['panelRuntimeId']:
            raise RuntimeError('social-moments-editor-changed')
        edit = unique(panel, 'mmui::XValidatorTextEdit')
        value = edit.GetValuePattern()
        if value is None or value.Value != text:
            raise RuntimeError('social-moments-draft-readback-mismatch')
        privacy = unique(panel, 'mmui::PublishPrivacyView')
        if privacy_state(privacy) != self.prepared['draft']['privacy']:
            raise RuntimeError('social-moments-privacy-changed')
        self.prepared = None  # Once dispatched/uncertain, never click again.
        self.owned_draft = None  # Publishing relinquishes cleanup ownership.
        dispatched_at = self.clock()
        publish.Click(move=True, show_window=True)
        return {'dispatched': True, 'dispatchedAt': dispatched_at}  # Never confirmation.

    @staticmethod
    def _semantic_key(entry):
        value = entry.get('stablePostId')
        if isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 for c in value):
            return ('post', value)
        value = entry.get('semanticKey')
        if isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value):
            return ('semantic', value)
        return None

    @classmethod
    def _top_keys(cls, timeline):
        boundary = timeline.get('topBoundary')
        if (not isinstance(boundary, dict) or boundary.get('atHead') is not True
                or boundary.get('ordering') != 'newest-first' or not isinstance(boundary.get('controls'), list)
                or not boundary['controls']):
            return None
        keys = [cls._semantic_key(entry) for entry in timeline['entries']]
        return keys if keys and None not in keys and len(set(keys)) == len(keys) else None

    @classmethod
    def _semantic_proof(cls, entry, timeline, previous, dispatched_at):
        """Reader evidence protocol, NOT claims about unobserved Qt fields.

        A stable id absent from an old viewport is not enough. Require a real
        creation interval entirely within this action, or two proven head views
        with stable semantic ids and the old head still behind the new entry.
        """
        number = lambda value: type(value) in (int, float) and math.isfinite(value)
        if not number(dispatched_at):
            return None
        evidence = entry.get('newPostEvidence')
        if (isinstance(evidence, dict) and evidence.get('kind') == 'creation-time'
                and evidence.get('source') == 'wxauto-uia' and evidence.get('clientVersion') == VERSION
                and isinstance(evidence.get('controls'), list) and evidence['controls']):
            earliest, latest = evidence.get('earliestCreatedAt'), evidence.get('latestCreatedAt')
            if (number(earliest) and number(latest) and dispatched_at <= earliest <= latest
                    and latest <= timeline['observedAt']):
                return 'creation-time'
        old_keys, new_keys = cls._top_keys(previous), cls._top_keys(timeline)
        key = cls._semantic_key(entry)
        if old_keys and new_keys and key is not None and key not in old_keys and old_keys[0] in new_keys:
            anchor = new_keys.index(old_keys[0])
            retained = min(len(old_keys), len(new_keys) - anchor)
            if (anchor > 0 and new_keys.index(key) < anchor and retained > 0
                    and not any(old_key in old_keys for old_key in new_keys[:anchor])
                    and new_keys[anchor:anchor + retained] == old_keys[:retained]):
                return 'top-boundary'
        return None

    def after(self, wx, parameters, result, before):
        text = self.parameters(parameters)
        if (not isinstance(before, dict) or before.get('parametersHash') != _digest(ACTION, parameters)
                or not isinstance(result, dict) or result.get('dispatched') is not True):
            raise RuntimeError('social-moments-before-evidence-required')
        # The supplied observed reader must independently navigate/read OWN
        # timeline. Editor disappearance and click return values are insufficient.
        timeline = self.timeline(wx)
        # UIA may recycle a row RuntimeId for a different post. Runtime identity
        # identifies the observed control; only the strict semantic proof below
        # can establish a new post. Reuse neither proves nor disproves freshness.
        matches = [e for e in timeline['entries'] if e['text'] == text]
        proofs = [(entry, self._semantic_proof(entry, timeline, before['previousTimeline'], result.get('dispatchedAt')))
                  for entry in matches]
        proven = [(entry, proof) for entry, proof in proofs if proof]
        return {'source': 'wxauto-uia', 'action': ACTION, 'phase': 'after', 'clientVersion': VERSION,
                'parametersHash': _digest(ACTION, parameters), 'observedAt': self.clock(),
                'controls': timeline['controls'], 'confirmed': len(matches) == 1 and len(proven) == 1,
                'author': self.own_author, 'matchingNewEntries': matches,
                'semanticProof': proven[0][1] if len(matches) == 1 and len(proven) == 1 else None}


def candidate_binding(*, own_author=None, read_own_timeline=None):
    """Explicit candidate builder; NOT a registry or a live-ready declaration."""
    reader_id = 'unobserved'
    if read_own_timeline is not None:
        source = inspect.getsource(read_own_timeline)
        module = Path(inspect.getfile(read_own_timeline)).read_bytes()
        reader_id = hashlib.sha256(source.encode('utf8') + module).hexdigest()
    candidate = TextMomentCandidate(own_author=own_author, read_own_timeline=read_own_timeline)
    return ActionBinding('wechat-4.2.0.12-text-moment-candidate:' + reader_id + ':' + str(own_author),
                         candidate.perform, candidate.before, candidate.after)
