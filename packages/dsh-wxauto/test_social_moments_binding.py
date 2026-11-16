"""Synthetic UIA controls only. These tests are NOT live acceptance receipts."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from social_moments_binding import TextMomentCandidate, candidate_binding


class Control:
    serial = 0
    def __init__(self, class_name, name='', children=(), click=None, right=None, automation_id=''):
        Control.serial += 1
        self.ClassName, self.Name, self.AutomationId = class_name, name, automation_id
        self.children, self.click, self.right, self.identity = children, click, right, Control.serial
        self.Value = ''
        self.ControlTypeName = 'TextControl' if class_name == 'mmui::XTextView' else 'ButtonControl'
        self.IsOffscreen, self.IsEnabled = False, True
        self.BoundingRectangle = SimpleNamespace(left=10, top=20, right=100, bottom=60)
    def GetChildren(self): return self.children() if callable(self.children) else self.children
    def GetRuntimeId(self): return [99, self.identity]
    def GetValuePattern(self): return self
    def SetValue(self, text): self.Value = text
    def Click(self, **kwargs):
        if kwargs != {'move': True, 'show_window': True}: raise AssertionError('must activate real Qt click')
        if self.click: self.click()
    def RightClick(self, **kwargs):
        if kwargs != {'move': True, 'show_window': True}: raise AssertionError('must activate right click')
        if self.right: self.right()


class MomentCandidateTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.panel_open, self.menu_open = [], False, False
        self.show_calls=0
        self.dialog_open, self.discard_effect = False, True
        self.after_cancel = None
        self.now = 1000
        self.top_boundary = None
        def record(name):
            def call(): self.calls.append(name)
            return call
        def open_menu(): self.calls.append('right-publish'); self.menu_open = True
        def open_editor(): self.calls.append('open-editor'); self.panel_open = True; self.menu_open = False
        def publish(): self.calls.append('PUBLISH'); self.panel_open = False
        def cancel():
            self.calls.append('CANCEL'); self.dialog_open = True
            if self.after_cancel: self.after_cancel()
        def discard():
            self.calls.append('DISCARD')
            if self.discard_effect: self.panel_open = False; self.dialog_open = False
        self.edit = Control('mmui::XValidatorTextEdit')
        self.privacy_value = Control('mmui::XTextView', '公开')
        self.privacy = Control('mmui::PublishPrivacyView', '谁可以看  ', children=[
            Control('mmui::XTextView', '谁可以看'), self.privacy_value])
        self.panel = Control('mmui::SnsPublishPanel', children=[self.edit, self.privacy,
            Control('mmui::XOutlineButton', '发表', click=publish),
            Control('mmui::XOutlineButton', '取消', click=cancel)])
        self.dialog = Control('mmui::XDialog', children=[
            Control('mmui::XOutlineButton', '保留', click=record('KEEP')),
            Control('mmui::XOutlineButton', '不保留', click=discard)])
        toolbar = Control('mmui::SNSWindowToolBar', children=[Control('mmui::XTabBarItem', '发表', right=open_menu)])
        # Mimic observed SNS nesting beyond shallow window scans.
        nested = toolbar
        for _ in range(18): nested = Control('synthetic-container', children=[nested])
        self.feed=Control('mmui::SNSContentView',children=[nested])
        self.menu = Control('mmui::XMenuView', '发表文字', click=open_editor, automation_id='XMenuItem')
        self.discover = Control('mmui::XTabBarItem', '发现', click=record('discover'))
        self.moments = Control('mmui::ExtensionDiscoverContentCell', '朋友圈', click=record('moments'))
        self.root = Control('mmui::MainWindow', children=lambda: [
            self.discover, self.moments, self.feed,
            *([self.menu] if self.menu_open else []), *([self.panel] if self.panel_open else []),
            *([self.dialog] if self.dialog_open else [])])
        def show():self.show_calls+=1
        self.wx = SimpleNamespace(_api=SimpleNamespace(control=self.root),Show=show)
        recovery=patch('wxauto_connection.ensure_main_tree',side_effect=lambda root:{'restored':False,'root':root})
        self.ensure_tree=recovery.start();self.addCleanup(recovery.stop)
        self.entries = [{'author': 'observed-own-author', 'text': 'old post', 'runtimeId': [1, 2]}]
        self.candidate = TextMomentCandidate(own_author='observed-own-author', read_own_timeline=self.timeline,
                                            clock=lambda: self.now, timeout=.08)
        self.parameters = {'text': 'exact draft\nsecond line', 'mediaFiles': []}

    def timeline(self, wx):
        return {'source': 'wxauto-uia', 'clientVersion': '4.2.0.12', 'scope': 'own-timeline',
                'author': 'observed-own-author', 'observedAt': self.now, 'topBoundary': self.top_boundary,
                'controls': [{'className': 'synthetic-own-timeline'}], 'entries': [dict(e) for e in self.entries]}

    def test_unobserved_author_reader_refuses_before_any_ui_or_publish(self):
        binding = candidate_binding()
        with self.assertRaisesRegex(RuntimeError, 'own-timeline-unobserved'):
            binding.observe_before(self.wx, self.parameters)
        self.assertEqual(self.calls, [])

    def test_media_refused_before_any_ui(self):
        with self.assertRaisesRegex(RuntimeError, 'media-unobserved'):
            self.candidate.before(self.wx, {'text': 'hello', 'mediaFiles': ['photo.jpg']})
        self.assertEqual(self.calls, [])

    def test_actual_path_exact_readback_and_new_own_entry_required(self):
        before = self.candidate.before(self.wx, self.parameters)
        self.assertEqual(self.edit.Value, self.parameters['text'])
        self.assertEqual(self.calls, ['discover', 'moments', 'right-publish', 'open-editor'])
        result = self.candidate.perform(self.wx, self.parameters)
        self.assertEqual(self.calls[-1], 'PUBLISH')
        self.assertFalse(self.candidate.after(self.wx, self.parameters, result, before)['confirmed'])
        self.entries.append({'author': 'observed-own-author', 'text': self.parameters['text'], 'runtimeId': [1, 3]})
        self.assertFalse(self.candidate.after(self.wx, self.parameters, result, before)['confirmed'])
        self.now = 1002
        self.entries[-1]['newPostEvidence'] = self.creation(1001, 1001.5)
        self.assertTrue(self.candidate.after(self.wx, self.parameters, result, before)['confirmed'])
        with self.assertRaisesRegex(RuntimeError, 'before-evidence-required'):
            self.candidate.perform(self.wx, self.parameters)

    def test_prepare_acquires_window_and_uses_toolbar_inside_the_actual_feed(self):
        original=self.root.children
        wrong=[]
        decoy=Control('mmui::SNSWindowToolBar',children=[Control('mmui::XTabBarItem','发表',right=lambda:wrong.append(True))])
        self.root.children=lambda:[*original(),decoy]
        self.candidate.prepare_draft(self.wx,self.parameters)
        self.assertEqual(self.show_calls,0)
        self.ensure_tree.assert_called_once_with(self.root)
        self.assertEqual(wrong,[])
        self.assertEqual(self.edit.Value,self.parameters['text'])

    def test_recovered_main_control_is_used_without_sdk_show(self):
        fresh=Control('mmui::MainWindow',children=self.root.children)
        self.ensure_tree.side_effect=None;self.ensure_tree.return_value={'restored':True,'root':fresh}
        self.candidate.prepare_draft(self.wx,self.parameters)
        self.assertIs(self.wx._api.control,fresh);self.assertEqual(self.show_calls,0)

    def test_editor_disappearance_old_runtime_or_wrong_author_never_confirms(self):
        before = self.candidate.before(self.wx, self.parameters)
        result = self.candidate.perform(self.wx, self.parameters)
        self.entries = [{'author': 'observed-own-author', 'text': self.parameters['text'], 'runtimeId': [1, 2]}]
        self.assertFalse(self.candidate.after(self.wx, self.parameters, result, before)['confirmed'])
        self.entries[0]['author'] = 'another author'
        with self.assertRaisesRegex(RuntimeError, 'entry-unverified'):
            self.candidate.after(self.wx, self.parameters, result, before)

    def test_changed_draft_or_privacy_refuses_final_click(self):
        self.candidate.before(self.wx, self.parameters)
        self.edit.Value = 'changed'
        with self.assertRaisesRegex(RuntimeError, 'draft-readback-mismatch'):
            self.candidate.perform(self.wx, self.parameters)
        self.edit.Value = self.parameters['text']; self.privacy.Name = 'changed privacy'
        with self.assertRaisesRegex(RuntimeError, 'privacy-changed'):
            self.candidate.perform(self.wx, self.parameters)
        self.assertNotIn('PUBLISH', self.calls)

    def test_same_privacy_container_identity_but_changed_semantic_value_refuses(self):
        before = self.candidate.before(self.wx, self.parameters)
        self.assertEqual(before['draft']['privacy']['textSequence'], ['谁可以看', '公开'])
        original_identity = self.privacy.GetRuntimeId()
        self.privacy_value.Name = '私密'
        self.assertEqual(self.privacy.GetRuntimeId(), original_identity)
        self.assertEqual(self.privacy.Name, '谁可以看  ')
        with self.assertRaisesRegex(RuntimeError, 'privacy-changed'):
            self.candidate.perform(self.wx, self.parameters)
        self.assertNotIn('PUBLISH', self.calls)

    def test_missing_visibility_value_refuses_but_written_draft_remains_owned(self):
        self.privacy.children = [Control('mmui::XTextView', '谁可以看')]
        with self.assertRaisesRegex(RuntimeError, 'privacy-value-unavailable'):
            self.candidate.before(self.wx, self.parameters)
        self.assertIsNone(self.candidate.prepared)
        self.assertNotIn('PUBLISH', self.calls)
        self.assertTrue(self.candidate.cancel_owned_draft(self.wx)['discarded'])

    def test_zero_rect_offscreen_or_disabled_never_clicked(self):
        for field, value in [('BoundingRectangle', SimpleNamespace(left=0, top=0, right=0, bottom=0)),
                             ('IsOffscreen', True), ('IsEnabled', False)]:
            with self.subTest(field=field):
                with patch.object(self.discover, field, value):
                    with self.assertRaisesRegex(RuntimeError, 'not-click-ready'):
                        self.candidate.prepare_draft(self.wx, self.parameters)
                self.assertEqual(self.calls, [])

    def test_real_com_integer_bool_readiness(self):
        self.discover.IsOffscreen=0
        self.discover.IsEnabled=1
        self.assertIs(self.candidate.wait_control(self.root,'mmui::XTabBarItem','发现',click_ready=True),
                      self.discover)

    def test_unknown_or_untyped_bool_values_never_clicked(self):
        for field, values in [('IsOffscreen',(None,'0',0.0,2)),('IsEnabled',(None,'1',1.0,2))]:
            for value in values:
                with self.subTest(field=field,value=value),patch.object(self.discover,field,value):
                    with self.assertRaisesRegex(RuntimeError,'not-click-ready'):
                        self.candidate.prepare_draft(self.wx,self.parameters)
                self.assertEqual(self.calls,[])

    def test_layout_must_stabilize_in_two_samples_before_click(self):
        self.discover.BoundingRectangle = SimpleNamespace(left=0, top=0, right=0, bottom=0)
        sleeps = []
        def layout(_):
            sleeps.append(1)
            self.assertEqual(self.calls, [])
            self.discover.BoundingRectangle = SimpleNamespace(left=10, top=20, right=100, bottom=60)
        with patch('social_moments_binding.time.sleep', side_effect=layout):
            self.candidate.wait_control(self.root, 'mmui::XTabBarItem', '发现', click_ready=True)
        self.assertGreaterEqual(len(sleeps), 2)

    def test_continuously_moving_rect_times_out_without_click(self):
        def layout(_): self.discover.BoundingRectangle.right += 1
        with patch('social_moments_binding.time.sleep', side_effect=layout):
            with self.assertRaisesRegex(RuntimeError, 'control-not-stable'):
                self.candidate.prepare_draft(self.wx, self.parameters)
        self.assertEqual(self.calls, [])

    def test_existing_user_draft_and_identical_post_refuse_overwrite_or_duplicate(self):
        self.panel_open = True
        with self.assertRaisesRegex(RuntimeError, 'existing-draft'):
            self.candidate.before(self.wx, self.parameters)
        self.assertEqual(self.calls, [])
        self.panel_open = False
        self.entries[0]['text'] = self.parameters['text']
        with self.assertRaisesRegex(RuntimeError, 'identical-post-ambiguous'):
            self.candidate.before(self.wx, self.parameters)
        self.assertEqual(self.calls, [])


    @staticmethod
    def creation(earliest, latest):
        return {'kind': 'creation-time', 'source': 'wxauto-uia', 'clientVersion': '4.2.0.12',
                'controls': [{'name': 'synthetic observed creation interval'}],
                'earliestCreatedAt': earliest, 'latestCreatedAt': latest}

    def test_unseen_old_post_with_new_runtime_and_stable_id_is_not_new_post_evidence(self):
        before = self.candidate.before(self.wx, self.parameters)
        result = self.candidate.perform(self.wx, self.parameters)
        self.entries = [{'author': 'observed-own-author', 'text': self.parameters['text'],
                         'runtimeId': [88, 99], 'stablePostId': 'unseen-old-post'}]
        self.assertFalse(self.candidate.after(self.wx, self.parameters, result, before)['confirmed'])
        self.now = 1002
        for earliest, latest in [(999, 1001), (1001, 1003), (True, 1001), (1002, 1001)]:
            self.entries[0]['newPostEvidence'] = self.creation(earliest, latest)
            self.assertFalse(self.candidate.after(self.wx, self.parameters, result, before)['confirmed'])

    def test_verified_top_boundary_uses_stable_semantics_not_virtualized_runtime_ids(self):
        self.entries[0]['stablePostId'] = 'old-head'
        self.entries.append({'author': 'observed-own-author', 'text': 'older', 'runtimeId': [1, 5], 'stablePostId': 'old-second'})
        self.top_boundary = {'atHead': True, 'ordering': 'newest-first', 'controls': [{'name': 'synthetic head proof'}]}
        before = self.candidate.before(self.wx, self.parameters)
        result = self.candidate.perform(self.wx, self.parameters)
        self.entries.insert(0, {'author': 'observed-own-author', 'text': self.parameters['text'],
                              'runtimeId': [9, 9], 'stablePostId': 'new-first'})
        observation = self.candidate.after(self.wx, self.parameters, result, before)
        self.assertTrue(observation['confirmed']); self.assertEqual(observation['semanticProof'], 'top-boundary')
        self.top_boundary = {'atHead': False, 'ordering': 'newest-first', 'controls': [{'name': 'not at head'}]}
        self.assertFalse(self.candidate.after(self.wx, self.parameters, result, before)['confirmed'])
        self.top_boundary['atHead'] = True
        self.entries[1], self.entries[2] = self.entries[2], self.entries[1]
        self.assertFalse(self.candidate.after(self.wx, self.parameters, result, before)['confirmed'])

    def test_new_post_reusing_old_row_runtime_id_can_pass_strict_head_proof(self):
        self.entries[0]['stablePostId']='old-head'
        self.top_boundary={'atHead':True,'ordering':'newest-first','controls':[{'name':'synthetic head proof'}]}
        before=self.candidate.before(self.wx,self.parameters)
        result=self.candidate.perform(self.wx,self.parameters)
        recycled=list(self.entries[0]['runtimeId'])
        self.entries[0]['runtimeId']=[8,9]  # Retained old post now has another row.
        self.entries.insert(0,{'author':'observed-own-author','text':self.parameters['text'],
            'runtimeId':recycled,'stablePostId':'new-first'})
        observation=self.candidate.after(self.wx,self.parameters,result,before)
        self.assertTrue(observation['confirmed'])
        self.assertEqual(observation['semanticProof'],'top-boundary')

    def test_reused_runtime_id_and_unseen_stable_id_alone_do_not_prove_new_post(self):
        before=self.candidate.before(self.wx,self.parameters)
        result=self.candidate.perform(self.wx,self.parameters)
        self.entries=[{'author':'observed-own-author','text':self.parameters['text'],
            'runtimeId':list(self.entries[0]['runtimeId']),'stablePostId':'unproven-post'}]
        observation=self.candidate.after(self.wx,self.parameters,result,before)
        self.assertFalse(observation['confirmed'])
        self.assertIsNone(observation['semanticProof'])

    def test_empty_old_head_does_not_gain_first_post_proof(self):
        self.entries=[]
        self.top_boundary={'atHead':True,'ordering':'newest-first','controls':[{'name':'synthetic head proof'}]}
        before=self.candidate.before(self.wx,self.parameters)
        result=self.candidate.perform(self.wx,self.parameters)
        self.entries=[{'author':'observed-own-author','text':self.parameters['text'],
            'runtimeId':[8,9],'stablePostId':'first-ever-unproven'}]
        observation=self.candidate.after(self.wx,self.parameters,result,before)
        self.assertFalse(observation['confirmed'])
        self.assertIsNone(observation['semanticProof'])

    def test_cancel_owned_sentinel_exact_draft_then_confirmed_editor_close(self):
        self.candidate.prepare_draft(self.wx, self.parameters)
        result = self.candidate.cancel_owned_draft(self.wx, expected_text=self.parameters['text'])
        self.assertTrue(result['discarded']); self.assertTrue(result['editorClosed'])
        self.assertEqual(self.calls[-2:], ['CANCEL', 'DISCARD'])
        self.assertNotIn('KEEP', self.calls); self.assertNotIn('PUBLISH', self.calls)
        with self.assertRaisesRegex(RuntimeError, 'no-owned-draft'):
            self.candidate.cancel_owned_draft(self.wx)

    def test_never_discard_user_draft_unowned_wrong_sentinel_changed_text_or_runtime(self):
        self.panel_open = True; self.edit.Value = self.parameters['text']
        with self.assertRaisesRegex(RuntimeError, 'no-owned-draft'):
            TextMomentCandidate().cancel_owned_draft(self.wx)
        self.panel_open = False; self.edit.Value = ''
        self.candidate.prepare_draft(self.wx, self.parameters); self.calls.clear()
        with self.assertRaisesRegex(RuntimeError, 'sentinel-mismatch'):
            self.candidate.cancel_owned_draft(self.wx, expected_text='another draft')
        self.edit.Value = 'user changed this draft'
        with self.assertRaisesRegex(RuntimeError, 'owned-draft-mismatch'):
            self.candidate.cancel_owned_draft(self.wx)
        self.edit.Value = self.parameters['text']
        for control in [self.edit, self.panel]:
            control.identity += 1
            with self.assertRaisesRegex(RuntimeError, 'owned-draft-mismatch'):
                self.candidate.cancel_owned_draft(self.wx)
            control.identity -= 1
        self.assertEqual(self.calls, [])

    def test_draft_replaced_during_cancel_does_not_click_generic_discard_dialog(self):
        self.candidate.prepare_draft(self.wx, self.parameters)
        self.after_cancel = lambda: setattr(self.edit, 'Value', 'now belongs to user')
        with self.assertRaisesRegex(RuntimeError, 'owned-draft-mismatch'):
            self.candidate.cancel_owned_draft(self.wx)
        self.assertEqual(self.calls[-1], 'CANCEL'); self.assertNotIn('DISCARD', self.calls)
        self.assertTrue(self.panel_open)

    def test_existing_dialog_and_unknown_discard_do_not_trigger_another_destructive_click(self):
        self.candidate.prepare_draft(self.wx, self.parameters); self.calls.clear(); self.dialog_open = True
        with self.assertRaisesRegex(RuntimeError, 'existing-dialog'):
            self.candidate.cancel_owned_draft(self.wx)
        self.assertEqual(self.calls, [])
        self.dialog_open = False; self.discard_effect = False
        with self.assertRaisesRegex(RuntimeError, 'discard-outcome-unknown'):
            self.candidate.cancel_owned_draft(self.wx)
        self.assertEqual(self.calls, ['CANCEL', 'DISCARD'])
        with self.assertRaisesRegex(RuntimeError, 'no-owned-draft'):
            self.candidate.cancel_owned_draft(self.wx)
        self.assertEqual(self.calls, ['CANCEL', 'DISCARD'])

    def test_missing_publish_control_keeps_exact_owned_draft_available_for_cleanup(self):
        self.candidate.before(self.wx, self.parameters)
        self.panel.children = [c for c in self.panel.children if c.Name != '发表']
        with self.assertRaisesRegex(RuntimeError, 'control-missing-or-ambiguous'):
            self.candidate.perform(self.wx, self.parameters)
        self.assertNotIn('PUBLISH', self.calls)
        self.assertTrue(self.candidate.cancel_owned_draft(self.wx)['discarded'])
        with self.assertRaisesRegex(RuntimeError, 'no-owned-draft'):
            self.candidate.cancel_owned_draft(self.wx)
        self.assertEqual(self.calls[-2:], ['CANCEL', 'DISCARD'])


if __name__ == '__main__': unittest.main()
