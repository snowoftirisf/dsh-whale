# Read-only Moments metadata callback

`social_moments_observe.observe_moments` is an unregistered metadata collector,
not a post reader, navigation implementation or publish capability. It imports
no wxauto module and performs no input, capture, network, file write or model call.

The integration caller must already be admitted as the owner on the existing
single UI queue, outside any active delivery. Pass the same bound `wx` and
`recover_chat=backend._with_chat_recovery`, `ui_thread_id=backend.ui_thread_id`,
plus the wxauto foreground-window reader. A foreign/unbound recovery function
or another thread is refused before navigation. Recovery exceptions propagate;
the caller must preserve the backend's stop-on-restoration-failure behavior.
Inside that recovery callback the collector calls the existing
`backend._start_navigation()` **before** entering the navigation context, so the
original chat is captured and finally restoration is armed even if navigation
fails while opening. No new recovery implementation is introduced.

`navigation_context(wx)` must be a reviewed context manager using the already
observed navigation: `XTabBarItem` 发现 → `ExtensionDiscoverContentCell` 朋友圈.
It must not enter 发表/composer, operate a post, like, comment or publish. It yields:

```python
{'clientVersion': '4.2.0.12', 'scope': 'moments-feed',  # or own-album
 'roots': [actual_sns_content_view],
 'guard': verify_current_bound_client_account_foreground_and_root_identity}
```

The callback must return literal `True` only after verifying the existing client
version/hash, current account identity, same PID/HWND/runtime identity and the
navigation-owned roots. Valid roots are the actually observed
`mmui::SNSContentView` or `mmui::AlbumListView`. The context must close only the
navigation it owns and let the outer backend restore the exact original chat.
This repository does **not** yet supply that Moments navigation context; existing
draft helpers must not be used because they enter a composer. The collector
does not trust a model-supplied root, selector, account, callback or path.

The collector rechecks guard/foreground between reads, refuses cross-process
nodes and composers, skips hidden subtrees, and bounds traversal to 256 visited
controls and depth 10. It reads actual AutomationId, Name, ValuePattern,
LegacyIAccessible Name/Value/Description and at most 2048 TextPattern characters;
only hashes, lengths, class/type, geometry and availability leave the callback.
Pattern errors are fixed booleans, never exception bodies. No `Invoke`, `Click`,
`RightClick`, `SendKeys` or value write occurs here.

There is no evidence-backed post class or labelled post creation-time field yet.
Accordingly the result explicitly says `post-reader-unobserved`, all post/author/
head/empty verification booleans are false, and time granularity is `unobserved`.
An empty tree or unavailable pattern cannot certify an empty remote timeline.
Next live use should preserve this bounded sanitized observation as a unique
artifact via the caller, then review actual field semantics before implementing
a reader. Current fixtures prove collection and recovery behavior only.
