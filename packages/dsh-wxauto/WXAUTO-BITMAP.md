# wxauto bitmap dependency and message-row capture

`wxauto_bitmap.py` imports without loading DLLs or inspecting UI. Explicit
`bootstrap_bitmap(uia)` verifies 64-bit Python and the audited DLL's fixed SHA-256,
then binds its absolute-path `ctypes.CDLL` to the library name wxauto expects.
It preserves an already compatible live `_DllClient` and refuses to replace any
unknown/incompatible loaded library. A prior failed singleton with `dll=None`
may be reset so wxauto can initialize its own declarations on its next API call.

The only accepted native artifact is the paired x64 DLL from official PyPI
uiautomation 2.0.20, SHA-256
`ee7b7d41b2fde000d02bb04cdc405772f9fc75a817cc467b988d32740bfa0f39`.
Its Apache 2.0 license is retained alongside the installed DLL at
`.venv-wxauto/Lib/site-packages/wxauto4/uia/bin/LICENSE.uiautomation-2.0.20.txt`.
Provenance, wheel publication hash and static exports are recorded separately in
`installers/wxauto-uia-compat/compatibility-manifest.json`.

`capture_message_row` uses **only** `message.control.CaptureToImage`. Before and
after capture it requires the same trusted chat predicate, existing source row,
RuntimeId, bounding rectangle, visible top-level HWND and foreground HWND. Nine
hit-tested points must lead through UIA ancestors to that exact message row.
This rejects occlusion reported by the UIA provider, not all pixel occlusion.
Actual WeChat group-info observation returned an underlying message row even
under its persistent opaque side panel. Therefore matching hit tests cannot
exclude either persistent same-process overlays or transient overlays. It never
substitutes a whole-window capture. Known panel states need an independent,
empirically observed open/closed predicate before row capture can be accepted
while those panels might be present.

The temporary PNG stays in the caller's chat directory, has bounded bytes and
pixels, must match the row dimensions, and is removed on success or failure.
Black frames are rejected as unknown rather than presented as visual evidence.
The Pillow dependency only decodes/validates a wxauto-produced PNG; it does not
capture the desktop. There is no ImageGrab/mss/dxcam acquisition fallback.

`incoming_images.py` now uses this guarded route for photo-thumbnail fallback and
all three animated-emotion sample frames. The original-image download branch is
unchanged, thumbnails remain explicitly labelled, and sampled emotions are not
represented as full GIFs. Guard failures leave the surrounding watcher message
with unknown content instead of claiming a successful observation.

The root agent separately collected successful full-window wxauto bitmap
acceptance in `artifacts/wechat-wxauto-bitmap-acceptance-20261102.json`. That
artifact does not validate these new per-row guards. This implementation was
verified with fake UIA controls only; no real capture or UI action was performed.
The root agent later recorded a real 864x84 voice-row capture in
`artifacts/native-row-capture-probe-20261102.json`; it validates that row's
technical capture path, not image-original availability or overlay safety.

## UIA hit testing limitation review

The installed `uiautomation.py` implements `ControlFromPoint(x,y)` with
`IUIAutomation.ElementFromPoint`, while `ControlFromPoint2(x,y)` calls Win32
`WindowFromPoint` followed by `IUIAutomation.ElementFromHandle`. The latter is a
native-window identity check, not a stronger semantic child hit test; an overlay
drawn within the same native window may still resolve to the main HWND.

`_AutomationClient.ViewWalker` is already `IUIAutomation.RawViewWalker`.
`GetParentControl`, child and sibling traversal use it. Changing from control
view to raw view therefore cannot fix this observation: the existing ancestry
test already uses raw view, and tree traversal does not alter point hit testing.

Other available signals are `IsOffscreen`, `IsWindowVisible`, foreground HWND,
`GetClickablePoint`, and `GetPattern(WindowPattern)` with `IsModal` and window
interaction state. They can supply positive reasons to refuse capture (a hidden
row, another foreground HWND, a reported modal), but none alone establishes that
WeChat's provider correctly reports its internally drawn side panel. A native
owned popup with a different HWND is distinct from the observed in-window panel.

The root agent then collected the wxauto-only A/B/A difference in
`artifacts/group-info-visibility-diff-20261102.json`: searching from MainRoot for
`GroupControl(ClassName='mmui::ChatRoomMemberInfoView')` found the visible panel
when open, did not find it when closed, and found the same RuntimeId again after
reopening. Its actual bounds overlapped the message area.

The guard now queries that exact observed class under the row's top-level
control before and after capture. An existing, non-offscreen panel whose **live**
bounds overlap any positive area of the row causes
`image-row-covered-by-group-info`, even when point hit testing reports the row.
Missing, offscreen or non-overlapping panels retain the existing path. Unreadable
panel state fails closed. No hardcoded coordinates, automatic panel closing,
or unobserved selectors are used. This addresses this one demonstrated panel;
it does not claim a universal same-process overlay detector.
