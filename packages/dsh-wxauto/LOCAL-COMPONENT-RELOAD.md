# Reloading the Python adapter without restarting Harness

## Observed empty Qt tree recovery, November 3

An SNS diagnostic left the existing WeChat main HWND/PID readable but with no
internal controls. Win32 Show/Restore and SetActive exposed only
MMUIRenderSubWindowHW; the visible screenshot still showed the normal logged-in
owner chat. One wxauto LegacyIAccessiblePattern.DoDefaultAction on the unique
visible SystemTray.NormalButton named ` WeChat` restored QWidget/MainView and
MainTabBar. No WeChat restart, login change, memory patch, voice or post was used.
The production listener then became ready with the original native epoch,
three group routes and selected warm voice. Evidence:
`artifacts/wechat-native-tray-tree-recovery-20261103.json` and
`artifacts/restore-tray-uia-tree-20261102T210517Z-83a8af28.json`.

The shared `ensure_main_tree` helper now implements only that observed recovery.
It uses the existing HWND/PID, refuses ambiguous icons, unknown tree shapes or
visible owned modals, and issues the tray default action at most once. Healthy
trees incur no tray operation. Connection, chat rebind and SNS draft preparation
use it; draft preparation no longer calls SDK Show. The 73 related plugin and
21 diagnostic fixture tests passed. These tests do not constitute a new SNS
publication or prove all future rendering failures recover automatically.

## Normal component reload

The installed Harness plugin detail page exposes a component-specific switch:
Plugins → local-dsh-wxauto → Enable component local-dsh-wxauto. Use current UI
observations, not saved accessibility indexes or coordinates.

This was used on2026-11-02 at07:01 UTC to load a Python-worker change. The
original whale workspace's ten session logs had no unterminated current turn.
After disabling only this component, all four observed Python launcher/child
PIDs belonging to the receiving worker and sole Vault writer disappeared.
Only then was the component enabled. Electron43332 and Host46932 kept their
original PIDs/start times. The restored component became ready, all three groups
were active and the approved voice model warmed up. No old voice was queued or
submitted, and no WeChat process or UI was externally controlled.

The plugin UI rewrote the profile patch. Semantic comparison found exactly one
removed entry: `{id: local-dsh-wxauto, disabled: false}`. This redundant override
is the bundle's default enabled state; every other patch entry remained exactly
equal. Original Bili14 and speak-up18 configuration keys were rechecked unchanged.
The prior profile patch is retained as a private local artifact.

Use this narrow method for Python worker source changes when idle. It does not
prove that disabling/enabling refreshes JavaScript's import cache, and it must
not be generalized to hot deployment of changed Node modules without separate
verification. Wait for the old writer to terminate before enabling another.
Pending real incoming messages must keep their existing durable identities;
never replay an old input to claim reload acceptance.

The deployed change updates attachment authorization only after validated
dynamic-group enable. Pending registration and suspension revoke a dynamic
group's attachment access while retaining receipts; reenable restores it.
Original fixed-chat authorization and cross-chat file isolation remain intact.
36 fixture regressions passed34 with2 host-link skips. They cover a real worker
and real AttachmentRegistry with synthetic file/send boundaries; no ordinary
file receive/send on the user's live WeChat was performed by those tests.
