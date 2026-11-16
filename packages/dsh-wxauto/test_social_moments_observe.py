import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace
import unittest

from social_moments_observe import observe_moments


class Control:
    sequence = 0

    def __init__(self, cls='mmui::UnknownObservedCell', *, name='', children=(), pid=7,
                 visible=True, rect=(0, 0, 100, 40), patterns=None):
        Control.sequence += 1
        self.rid = (42, Control.sequence)
        self.ClassName, self.Name = cls, name
        self.ControlTypeName, self.AutomationId = 'GroupControl', 'private-automation-id'
        self.ProcessId, self.NativeWindowHandle = pid, 10
        self.IsOffscreen = not visible
        self.children, self.patterns = list(children), patterns or {}
        self.BoundingRectangle = SimpleNamespace(**dict(zip(('left', 'top', 'right', 'bottom'), rect)))
        self.child_reads = 0

    def GetRuntimeId(self): return self.rid
    def GetChildren(self):
        self.child_reads += 1
        return self.children
    def GetLegacyIAccessiblePattern(self): return self.patterns.get('legacy')
    def GetValuePattern(self): return self.patterns.get('value')
    def GetTextPattern(self): return self.patterns.get('text')
    def Click(self, *a, **kw): raise AssertionError('input forbidden')
    RightClick = Click
    SendKeys = Click
    Invoke = Click


class Backend:
    def __init__(self, wx):
        self.wx, self.ui_thread_id = wx, threading.get_ident()
        self.recovered = 0
        self.navigation_started = False

    def _start_navigation(self): self.navigation_started = True

    def _with_chat_recovery(self, callback):
        try: return callback()
        finally: self.recovered += 1


class Fixture:
    def __init__(self, root=None):
        self.main = Control('mmui::MainWindow')
        self.wx = SimpleNamespace(_api=SimpleNamespace(control=self.main))
        self.backend = Backend(self.wx)
        self.root = root or Control('mmui::SNSContentView')
        self.closed, self.guard_calls = 0, 0
        self.foreground = 10
        self.guard_result = True

    def guard(self):
        self.guard_calls += 1
        return self.guard_result

    @contextmanager
    def navigate(self, wx):
        assert wx is self.wx
        assert self.backend.navigation_started
        try:
            yield {'roots': [self.root], 'guard': self.guard,
                   'clientVersion': '4.2.0.12', 'scope': 'moments-feed'}
        finally: self.closed += 1

    def run(self, **kwargs):
        return observe_moments(self.wx, recover_chat=self.backend._with_chat_recovery,
            navigation_context=self.navigate, ui_thread_id=self.backend.ui_thread_id,
            foreground=lambda: self.foreground, **kwargs)


class MomentsMetadataTests(unittest.TestCase):
    def test_metadata_hashes_names_values_and_text_and_never_claims_a_post(self):
        reads = []
        class Range:
            def GetText(self, limit):
                reads.append(limit)
                return 'private post body'
        leaf = Control(name='private display name', patterns={
            'legacy': SimpleNamespace(Name='private legacy name', Value='private legacy value', Description='private description'),
            'value': SimpleNamespace(Value='private value'), 'text': SimpleNamespace(DocumentRange=Range())})
        f = Fixture(Control('mmui::SNSContentView', children=[leaf]))
        value = f.run()
        serialized = json.dumps(value)
        self.assertNotIn('private ', serialized)
        self.assertEqual(reads, [2048])
        self.assertTrue(value['nodes'][1]['legacy']['available'])
        self.assertTrue(value['nodes'][1]['value']['available'])
        self.assertTrue(value['nodes'][1]['text']['available'])
        self.assertEqual(value['state'], 'post-reader-unobserved')
        for field in ('postIdentityVerified', 'authorVerified', 'headBoundaryVerified', 'emptyTimelineVerified'):
            self.assertIs(value[field], False)
        self.assertEqual(f.closed, 1)
        self.assertEqual(f.backend.recovered, 1)

    def test_empty_observed_tree_is_not_empty_timeline_proof(self):
        value = Fixture(Control('mmui::AlbumListView')).run()
        self.assertEqual(len(value['nodes']), 1)
        self.assertFalse(value['emptyTimelineVerified'])

    def test_requires_bound_existing_recovery_and_same_ui_thread_before_navigation(self):
        f = Fixture()
        for recovery, thread in [(lambda callback: callback(), threading.get_ident()),
                                 (f.backend._with_chat_recovery, -1),
                                 (Backend(SimpleNamespace())._with_chat_recovery, threading.get_ident())]:
            with self.assertRaisesRegex(RuntimeError, 'context-required'):
                observe_moments(f.wx, recover_chat=recovery, navigation_context=f.navigate,
                    ui_thread_id=thread, foreground=lambda: 10)
        self.assertEqual(f.closed, 0)

    def test_navigation_and_restoration_run_on_collector_error(self):
        f = Fixture(Control('mmui::SnsPublishPanel'))
        with self.assertRaisesRegex(RuntimeError, 'root-unobserved'):
            f.run()
        self.assertEqual((f.closed, f.backend.recovered), (1, 1))

    def test_composer_descendant_rejected_without_input(self):
        f = Fixture(Control('mmui::SNSContentView', children=[Control('mmui::SnsPublishPanel')]))
        with self.assertRaisesRegex(RuntimeError, 'composer-forbidden'): f.run()
        self.assertEqual(f.backend.recovered, 1)

    def test_cross_process_and_foreground_change_reject(self):
        f = Fixture(Control('mmui::SNSContentView', children=[Control(pid=8)]))
        with self.assertRaisesRegex(RuntimeError, 'owner-changed'): f.run()
        g = Fixture(); g.foreground = 20
        with self.assertRaisesRegex(RuntimeError, 'owner-changed'): g.run()

    def test_guard_failure_is_not_treated_as_success(self):
        for value in (False, None, {'verified': True}):
            f = Fixture(); f.guard_result = value
            with self.assertRaisesRegex(RuntimeError, 'guard-unverified'): f.run()

    def test_hidden_subtree_skipped_zero_geometry_and_pattern_errors_retained(self):
        hidden = Control(visible=False, children=[Control(name='hidden data')])
        class BadRange:
            def GetText(self, limit): raise RuntimeError('private error body')
        zero = Control(rect=(0, 0, 0, 0), patterns={'text': SimpleNamespace(DocumentRange=BadRange())})
        result = Fixture(Control('mmui::SNSContentView', children=[hidden, zero])).run()
        self.assertEqual(hidden.child_reads, 0)
        self.assertEqual(len(result['nodes']), 2)
        self.assertIsNone(result['nodes'][1]['bounds'])
        self.assertTrue(result['nodes'][1]['text']['readFailed'])
        self.assertNotIn('private', json.dumps(result))

    def test_node_and_depth_budget_are_bounded_and_explicit(self):
        value = Fixture(Control('mmui::SNSContentView', children=[Control() for _ in range(20)])).run(max_nodes=4)
        self.assertEqual(value['visitedNodes'], 4)
        self.assertEqual(len(value['nodes']), 4)
        self.assertTrue(value['truncated'])
        value = Fixture(Control('mmui::SNSContentView', children=[Control(children=[Control()])])).run(max_depth=1)
        self.assertEqual(len(value['nodes']), 2)
        self.assertTrue(value['truncated'])

    def test_late_guard_change_discards_result_but_restores(self):
        f = Fixture()
        original = f.guard
        def guard():
            answer = original()
            return answer if f.guard_calls < 3 else False
        f.guard = guard
        with self.assertRaisesRegex(RuntimeError, 'guard-unverified'): f.run()
        self.assertEqual(f.backend.recovered, 1)


if __name__ == '__main__': unittest.main()
