"""Unregistered, observed Discover -> Moments-feed navigation context.

SOURCE ONLY: no live acceptance is claimed. Use with observe_moments(wx,
recover_chat=backend._with_chat_recovery, navigation_context=make_feed_navigation(
wx, backend=backend, client=verified_client, identity_check=trusted_account_check,
client_unchanged=cheap_verified_client_metadata_check, uia=uia, windows=GetAllWindows),
ui_thread_id=backend.ui_thread_id, foreground=uia.GetForegroundWindow).

Both check callbacks are trusted zero-argument functions returning literal True.
identity_check verifies current authenticated account/route provenance;
client_unchanged checks the initially hash-verified client file identity/metadata
and process binding cheaply (not full executable hashing per collected node).
No caller/model-supplied selector, identity or URL is accepted. The collector arms
backend._start_navigation BEFORE entering this context; only the existing outer
backend recovery restores the original allowlisted chat. No close selector is
invented, and no composer/profile/own-album navigation is performed here.
Whole-tree/modal checks run only at navigation boundaries and context exit.
The yielded per-read guard rechecks identity/foreground/enabled state and a
maximum 32-control ancestor chain, without rescanning messages or owned windows.
"""
from contextlib import contextmanager
import threading

from social_friend_observe import OBSERVED_CLIENT_SHA, OBSERVED_VERSION, load_probe_helpers
from social_moments_binding import TextMomentCandidate


BLOCKED_CLASSES = frozenset(('mmui::SnsPublishPanel', 'mmui::XDialog',
    'mmui::XMenu', 'mmui::XMenuView', 'mmui::XPopover'))


def make_feed_navigation(wx, *, backend, client, identity_check, client_unchanged,
                         uia, windows, helpers=None, waiter=None, on_stage=None):
    """Build an inert context factory for the already-bound single UI thread.

    helpers defaults to the existing probe module's visible_owned_roots/nodes;
    waiter defaults to TextMomentCandidate.wait_control, never prepare_draft.
    Injected alternatives are fixture/trusted integration seams, not RPC data.
    """
    if (getattr(backend, 'wx', None) is not wx or not callable(identity_check)
            or not callable(client_unchanged) or not callable(windows)
            or not callable(getattr(uia, 'GetForegroundWindow', None))
            or (on_stage is not None and not callable(on_stage))
            or type(client) is not dict or client.get('clientVersion') != OBSERVED_VERSION
            or client.get('clientSha256') != OBSERVED_CLIENT_SHA):
        raise RuntimeError('social-moments-navigation-config-unverified')
    verified_client = dict(client)

    def stage(phase, **facts):
        # A private diagnostic sink is observational; failures never change UI
        # behavior, mask the actual exception, or prevent finally restoration.
        if on_stage is not None:
            try:
                on_stage(phase, **facts)
            except Exception:
                pass

    @contextmanager
    def navigation_context(bound_wx):
        stage('prechecks')
        if bound_wx is not wx or threading.get_ident() != getattr(backend, 'ui_thread_id', None):
            raise RuntimeError('social-moments-navigation-ui-thread-required')
        recovery = getattr(backend, '_navigation_context', None)
        if (type(recovery) is not dict or recovery.get('started') is not True
                or getattr(backend, 'chat_binding_failed', False)):
            raise RuntimeError('social-moments-navigation-recovery-required')
        route = tuple(recovery.get(k) for k in ('target', 'kind', 'fallback'))
        if not isinstance(route[0], str) or not route[0] or route[1] not in ('friend', 'group'):
            raise RuntimeError('social-moments-navigation-route-unverified')
        actual_helpers = helpers if helpers is not None else load_probe_helpers()
        actual_wait = waiter if waiter is not None else TextMomentCandidate().wait_control

        def wait_at(phase, root, expected_class, name=None, **kwargs):
            stage(phase, expected_class=expected_class)
            try:
                return actual_wait(root, expected_class, name, **kwargs)
            except RuntimeError as error:
                count = getattr(error, 'matches', None)
                # Counts are diagnostic facts, never arbitrary exception data.
                count = count if type(count) is int and 0 <= count <= 4000 else None
                stage(phase, expected_class=expected_class, matches=count)
                error.momentsObservationPhase = phase
                error.momentsExpectedClass = expected_class
                error.momentsMatches = count
                raise
        main = wx._api.control
        main_identity = (main.ProcessId, main.NativeWindowHandle, tuple(main.GetRuntimeId()))
        pid, handle, _ = main_identity
        if (main.ClassName != 'mmui::MainWindow' or type(pid) is not int or pid <= 0
                or type(handle) is not int or handle <= 0 or verified_client.get('pid') != pid):
            raise RuntimeError('social-moments-navigation-client-unverified')
        feed = None

        def feed_ancestor_is_main():
            # UIA objects may be recreated; compare actual runtime identities.
            # Never rescan the full chat tree for each metadata read.
            current, seen = feed, set()
            for _ in range(32):
                if current is None or current.ProcessId != pid:
                    return False
                rid = tuple(current.GetRuntimeId())
                if not rid or rid in seen:
                    return False
                seen.add(rid)
                if rid == main_identity[2]:
                    return (current.ClassName == 'mmui::MainWindow'
                            and current.NativeWindowHandle == handle)
                current = current.GetParentControl()
            return False

        def validate(*, require_foreground=True, full=False):
            if (wx._api.control is not main
                    or (main.ProcessId, main.NativeWindowHandle, tuple(main.GetRuntimeId())) != main_identity
                    or main.ClassName != 'mmui::MainWindow' or main.IsOffscreen != 0
                    or type(main.IsEnabled) not in (int, bool) or main.IsEnabled != 1
                    or getattr(backend, '_navigation_context', None) is not recovery
                    or tuple(recovery.get(k) for k in ('target', 'kind', 'fallback')) != route
                    or getattr(backend, 'chat_binding_failed', False)):
                raise RuntimeError('social-moments-navigation-binding-changed')
            if identity_check() is not True or client_unchanged() is not True:
                raise RuntimeError('social-moments-navigation-identity-changed')
            feed_present = feed is None
            if full:
                owned = actual_helpers.visible_owned_roots(uia, windows, pid)
                if any(c.NativeWindowHandle != handle for c in owned):
                    raise RuntimeError('social-moments-navigation-existing-popup')
                for control in actual_helpers.nodes(main):
                    if control.ProcessId != pid:
                        raise RuntimeError('social-moments-navigation-binding-changed')
                    if control.ClassName in BLOCKED_CLASSES and control.IsOffscreen == 0:
                        raise RuntimeError('social-moments-navigation-existing-popup')
                    if feed is not None and tuple(control.GetRuntimeId()) == feed_id:
                        feed_present = True
            if require_foreground and uia.GetForegroundWindow() != handle:
                raise RuntimeError('social-moments-navigation-foreground-changed')
            if feed is not None:
                if ((full and not feed_present) or feed.ProcessId != pid or feed.ClassName != 'mmui::SNSContentView'
                        or feed.IsOffscreen != 0 or type(feed.IsEnabled) not in (int, bool) or feed.IsEnabled != 1
                        or tuple(feed.GetRuntimeId()) != feed_id or not feed_ancestor_is_main()):
                    raise RuntimeError('social-moments-navigation-feed-changed')
            return True

        # Popup, identity and recovery checks precede the sole allowed acquisition.
        validate(require_foreground=False, full=True)
        if uia.GetForegroundWindow() != handle:
            wx.Show()
        validate(full=True)
        discover = wait_at('locating-discover', main, 'mmui::XTabBarItem', '发现', click_ready=True)
        validate(full=True)
        discover.Click(move=True, show_window=True)
        stage('discover-open')
        validate(full=True)
        entry = wait_at('locating-moments', main, 'mmui::ExtensionDiscoverContentCell', '朋友圈', click_ready=True)
        validate(full=True)
        entry.Click(move=True, show_window=True)
        stage('moments-open')
        validate(full=True)
        feed = wait_at('locating-feed', main, 'mmui::SNSContentView')
        feed_id = tuple(feed.GetRuntimeId())
        validate(full=True)
        try:
            stage('collecting')
            yield {'clientVersion': OBSERVED_VERSION, 'scope': 'moments-feed',
                   'roots': [feed], 'guard': validate}
        finally:
            stage('exiting-feed')
            # No guessed tab-close or generic Escape. Outer backend finally owns
            # restoration even when this final passive guard rejects a change.
            validate(full=True)

    return navigation_context
