# Local repair: 0.2.7-local.1

Adds an optional `IM投递` setting with `sessionId`, `botId`, and `targetId`.
The plugin sends only completed `speak-up` replies through the installed dsh-im
service, preserving the original session, source marker, dice algorithm,
30-minute timer, cooldown, quiet hours and personality. It does not echo the
internal trigger. Normal GUI, IM and schedule replies remain on their existing
delivery paths. Failed/ambiguous sends are logged without automatic retry.

The configured target is an existing dsh-im delivery target; credentials stay in
dsh-im. Updating this local package to the upstream npm version removes this fix.

Validation: upstream host/client tests, plus `node --test tests/im-delivery.test.mjs`.
