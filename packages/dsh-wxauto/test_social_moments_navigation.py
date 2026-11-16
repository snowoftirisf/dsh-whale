from types import SimpleNamespace
import threading
import unittest

from social_friend_observe import OBSERVED_CLIENT_SHA
from social_moments_navigation import make_feed_navigation
from social_moments_observe import observe_moments
from test_social_moments_observe import Control


class FakeBackend:
    def __init__(self, wx):
        self.wx, self.ui_thread_id = wx, threading.get_ident()
        self._navigation_context, self.chat_binding_failed = None, False
        self.restored = 0
    def _start_navigation(self):
        self._navigation_context.update(started=True, target='original', kind='friend', fallback=False)
    def _with_chat_recovery(self, callback):
        self._navigation_context = {}
        try: return callback()
        finally:
            self.restored += 1
            self._navigation_context = None


class Fixture:
    def __init__(self):
        self.events, self.foreground, self.account_ok, self.client_ok = [], 10, True, True
        self.main = Control('mmui::MainWindow'); self.main.IsEnabled = True
        self.feed = Control('mmui::SNSContentView', children=[Control('mmui::TimeLineListView', children=[Control('mmui::TimelineCell')])])
        self.main.children = [self.feed]
        self.feed.IsEnabled = True
        self.feed.GetParentControl = lambda: self.main if self.feed in self.main.children else None
        self.wx = SimpleNamespace(_api=SimpleNamespace(control=self.main), Show=self.show)
        self.backend = FakeBackend(self.wx)
        self.owned = [self.main]
        self.hook = lambda event: None
        self.uia = SimpleNamespace(GetForegroundWindow=lambda: self.foreground)
        self.tree_scans, self.owned_scans = 0, 0
        def nodes(root):
            self.tree_scans += 1
            pending = [root]
            while pending:
                node = pending.pop()
                yield node
                pending.extend(node.GetChildren())
        def owned(u, w, p):
            self.owned_scans += 1
            return self.owned
        self.helpers = SimpleNamespace(nodes=nodes, visible_owned_roots=owned)
    def show(self):
        self.events.append('show'); self.foreground = 10
    def waiter(self, root, cls, name=None, **kwargs):
        self.events.append(('wait', cls, name))
        if cls == 'mmui::SNSContentView': return self.feed
        def click(**kw):
            self.events.append(('click', cls, name, kw))
            self.hook(name)
        return SimpleNamespace(Click=click)
    def context(self, **kwargs):
        return make_feed_navigation(self.wx, backend=self.backend,
            client={'pid': 7, 'clientVersion': '4.2.0.12', 'clientSha256': OBSERVED_CLIENT_SHA},
            identity_check=lambda: self.account_ok, client_unchanged=lambda: self.client_ok,
            uia=self.uia, windows=lambda: [], helpers=self.helpers, waiter=self.waiter, **kwargs)
    def run(self):
        return observe_moments(self.wx, recover_chat=self.backend._with_chat_recovery,
            navigation_context=self.context(), ui_thread_id=self.backend.ui_thread_id,
            foreground=self.uia.GetForegroundWindow)


class NavigationTests(unittest.TestCase):
    def test_build_is_inert_navigation_exactly_observed_two_clicks_and_recovers(self):
        f = Fixture(); f.context(); self.assertEqual(f.events, [])
        value = f.run()
        clicks = [x for x in f.events if isinstance(x, tuple) and x[0] == 'click']
        self.assertEqual([(x[1], x[2]) for x in clicks], [
            ('mmui::XTabBarItem', '发现'), ('mmui::ExtensionDiscoverContentCell', '朋友圈')])
        self.assertTrue(all(x[3] == {'move': True, 'show_window': True} for x in clicks))
        self.assertEqual(f.backend.restored, 1)
        self.assertEqual(value['state'], 'post-reader-unobserved')
        self.assertFalse(value['postIdentityVerified'])

    def test_only_one_foreground_acquisition_after_identity_checks(self):
        f = Fixture(); f.foreground = 99; f.run()
        self.assertEqual(f.events.count('show'), 1)
        f = Fixture(); f.foreground = 99; f.account_ok = False
        with self.assertRaisesRegex(RuntimeError, 'identity-changed'): f.run()
        self.assertNotIn('show', f.events)

    def test_initial_popup_or_composer_prevents_all_navigation(self):
        f = Fixture(); f.owned.append(Control('mmui::XPopover')); f.owned[-1].NativeWindowHandle = 22
        with self.assertRaisesRegex(RuntimeError, 'popup'): f.run()
        self.assertEqual(f.events, [])
        for cls in ('mmui::SnsPublishPanel', 'mmui::XDialog', 'mmui::XMenuView'):
            f = Fixture(); f.main.children.append(Control(cls))
            with self.assertRaisesRegex(RuntimeError, 'popup'): f.run()
            self.assertEqual(f.events, [])

    def test_account_client_route_or_root_change_stops_before_next_click_and_restores(self):
        changes = [lambda f: setattr(f, 'account_ok', False),
                   lambda f: setattr(f, 'client_ok', False),
                   lambda f: f.backend._navigation_context.update(target='changed'),
                   lambda f: setattr(f.main, 'rid', (42, 99999)),
                   lambda f: setattr(f, 'foreground', 99)]
        for change in changes:
            f = Fixture(); f.hook = lambda name, f=f: change(f)
            with self.assertRaises(RuntimeError): f.run()
            self.assertEqual(len([x for x in f.events if isinstance(x, tuple) and x[0] == 'click']), 1)
            self.assertEqual(f.backend.restored, 1)

    def test_new_popup_after_navigation_rejected_without_close_or_generic_keys(self):
        f = Fixture(); f.hook = lambda name: f.main.children.append(Control('mmui::XDialog'))
        with self.assertRaisesRegex(RuntimeError, 'popup'): f.run()
        self.assertEqual(f.backend.restored, 1)

    def test_requires_armed_recovery_and_thread_and_verified_client(self):
        f = Fixture()
        with self.assertRaisesRegex(RuntimeError, 'recovery-required'):
            with f.context()(f.wx): pass
        f = Fixture(); f.backend.ui_thread_id = -1
        with self.assertRaisesRegex(RuntimeError, 'ui-thread'):
            with f.context()(f.wx): pass
        f = Fixture()
        with self.assertRaisesRegex(RuntimeError, 'config-unverified'):
            make_feed_navigation(f.wx, backend=f.backend, client={}, identity_check=lambda: True,
                client_unchanged=lambda: True, uia=f.uia, windows=lambda: [])

    def test_guard_rejects_replaced_feed_and_outer_recovery_runs(self):
        f = Fixture()
        def collect():
            f.backend._start_navigation()
            with f.context()(f.wx) as binding:
                f.feed.rid = (99, 99)
                binding['guard']()
        with self.assertRaisesRegex(RuntimeError, 'feed-changed'):
            f.backend._with_chat_recovery(collect)
        self.assertEqual(f.backend.restored, 1)

    def test_detached_stale_feed_reference_is_not_current_root(self):
        f = Fixture()
        def collect():
            f.backend._start_navigation()
            with f.context()(f.wx) as binding:
                f.main.children = []
                binding['guard']()
        with self.assertRaisesRegex(RuntimeError, 'feed-changed'):
            f.backend._with_chat_recovery(collect)
        self.assertEqual(f.backend.restored, 1)

    def test_collection_many_nodes_does_not_repeat_whole_tree_scans(self):
        small = Fixture(); small.run()
        large = Fixture(); large.feed.children.extend(Control() for _ in range(60)); large.run()
        self.assertEqual(large.tree_scans, small.tree_scans)
        self.assertEqual(large.owned_scans, small.owned_scans)
        self.assertEqual(large.tree_scans, 8)  # Seven action/entry boundaries plus exit.

    def test_cheap_read_guard_detects_changes_without_scanning_tree(self):
        for change in [lambda f: setattr(f, 'foreground', 99),
                       lambda f: setattr(f, 'account_ok', False),
                       lambda f: setattr(f, 'client_ok', False),
                       lambda f: setattr(f.feed, 'IsEnabled', False),
                       lambda f: setattr(f.main, 'rid', (7, 7)),
                       lambda f: setattr(f.feed, 'GetParentControl', lambda: None)]:
            f = Fixture()
            def collect():
                f.backend._start_navigation()
                with f.context()(f.wx) as binding:
                    before = f.tree_scans
                    change(f)
                    with self.assertRaises(RuntimeError): binding['guard']()
                    self.assertEqual(f.tree_scans, before)
            # Context exit also detects the mutation and recovery still executes.
            with self.assertRaises(RuntimeError): f.backend._with_chat_recovery(collect)
            self.assertEqual(f.backend.restored, 1)

    def test_ancestor_cycle_and_excess_depth_are_bounded(self):
        for cyclic in (False, True):
            f = Fixture(); calls = []
            chain = [Control() for _ in range(40)]
            for i, node in enumerate(chain):
                def parent(i=i):
                    calls.append(i)
                    return chain[0] if cyclic else (chain[i + 1] if i + 1 < len(chain) else f.main)
                node.GetParentControl = parent
            f.feed.GetParentControl = lambda: chain[0]
            with self.assertRaisesRegex(RuntimeError, 'feed-changed'): f.run()
            self.assertLessEqual(len(calls), 31)
            self.assertEqual(f.backend.restored, 1)

    def test_each_wait_failure_records_only_fixed_phase_class_and_safe_count(self):
        cases = [('mmui::XTabBarItem', 'locating-discover'),
                 ('mmui::ExtensionDiscoverContentCell', 'locating-moments'),
                 ('mmui::SNSContentView', 'locating-feed')]
        for cls, phase in cases:
            f = Fixture(); stages = []; original = f.waiter
            def wait(root, expected_class, name=None, **kw):
                if expected_class == cls:
                    error = RuntimeError('social-moments-control-missing-or-ambiguous')
                    error.matches = 2
                    error.expectedName = 'private account text'
                    raise error
                return original(root, expected_class, name, **kw)
            f.waiter = wait
            context = f.context(on_stage=lambda phase, **facts: stages.append((phase, facts)))
            with self.assertRaisesRegex(RuntimeError, 'control-missing') as raised:
                observe_moments(f.wx, recover_chat=f.backend._with_chat_recovery,
                    navigation_context=context, ui_thread_id=f.backend.ui_thread_id,
                    foreground=f.uia.GetForegroundWindow)
            self.assertEqual(stages[-1], (phase, {'expected_class': cls, 'matches': 2}))
            self.assertEqual(raised.exception.momentsObservationPhase, phase)
            self.assertEqual(raised.exception.momentsExpectedClass, cls)
            self.assertEqual(raised.exception.momentsMatches, 2)
            self.assertNotIn('private', str(stages))
            self.assertEqual(f.backend.restored, 1)

    def test_diagnostic_callback_failure_cannot_stop_navigation_or_recovery(self):
        f = Fixture()
        def broken(*args, **kwargs): raise RuntimeError('private diagnostic failure')
        context = f.context(on_stage=broken)
        value = observe_moments(f.wx, recover_chat=f.backend._with_chat_recovery,
            navigation_context=context, ui_thread_id=f.backend.ui_thread_id,
            foreground=f.uia.GetForegroundWindow)
        self.assertEqual(value['state'], 'post-reader-unobserved')
        self.assertEqual(f.backend.restored, 1)
        self.assertEqual(f.tree_scans, 8)

    def test_invalid_exception_count_is_not_exported(self):
        f = Fixture(); stages = []
        def wait(*args, **kwargs):
            error = RuntimeError('social-moments-control-missing-or-ambiguous')
            error.matches = 'private name'
            raise error
        f.waiter = wait
        with self.assertRaises(RuntimeError):
            observe_moments(f.wx, recover_chat=f.backend._with_chat_recovery,
                navigation_context=f.context(on_stage=lambda phase, **facts: stages.append((phase, facts))),
                ui_thread_id=f.backend.ui_thread_id, foreground=f.uia.GetForegroundWindow)
        self.assertIsNone(stages[-1][1]['matches'])
        self.assertNotIn('private', str(stages))


if __name__ == '__main__': unittest.main()
