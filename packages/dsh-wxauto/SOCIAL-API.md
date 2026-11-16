# Social action policy and immutable local plans

`social_actions.py` contains no UI selectors, WeChat calls or network transport.
It is ready for later integration; its tests do not verify any real client
operation. The storage file is `<dataRoot>/config/social-actions.json`, guarded
by a cross-process OS lock and written through an exclusive temporary file plus
atomic replacement.

## Trusted context

```python
queue = SocialActions(data_root, owner_target=owner_name, allowed_groups=groups)
context = ActionContext(kind='friend', target=owner_name, is_owner=True)
plan = queue.draft(action, parameters, context)
```

Contexts, owner identity and allowed groups must be derived by the adapter from
its own routing/session state, never accepted from model arguments or a display
name within a group. Autonomous scheduled work uses the adapter's owner-private
context. The queue itself never sends notifications or approval messages.

## Actions and parameters

| Action | Complete stored parameters | Policy |
|---|---|---|
| `publish_moment` | `text`, `mediaFiles` (defaults `''`, `[]`; at least one nonempty) | Autonomous in owner-private context; group callers cannot publish |
| `set_group_announcement` | `group`, `text` | Autonomous only for current allowlisted group, or owner-private context |
| `create_group` | `contacts` list | Explicit owner-private approval required |
| `add_friend` | `keywords`; optional `addmsg`, `remark`, `tags` | Explicit owner-private approval required |
| `accept_friend` | `requestId`, `applicant`; optional `verificationMessage`, `remark`, `tags` | Explicit owner-private approval required |

Unknown fields are rejected, not silently ignored. `mediaFiles` are stored as
dataRoot-relative paths under `media/` or `outbox/`; the real backend must validate
their exact selected files before sending. The queue neither reads arbitrary
files nor runs attachment contents. No privacy/visibility parameter is accepted:
publishing must retain the account's existing effective visibility configuration.
If the actual WeChat composer requires a privacy change, the backend must fail
rather than choose a new setting.

## Plans and explicit approval

`draft` returns a deep-copied plan with `id`, SHA256 `hash`, complete normalized
`parameters`, origin, creation time, 24-hour expiry and status. The hash covers
action plus the full normalized parameter object. Stored parameters are immutable
through this API. Changing a recipient or text requires drafting a new plan and,
where required, a new approval. Direct on-disk parameter tampering is detected.

`approve(id, hash, context)` requires a trusted context with all of:

```python
ActionContext('friend', owner_name, True,
    approval_message_id=actual_owner_message_id,
    approval_plan_id=id,
    approval_plan_hash=hash)
```

These approval fields must be populated only after the adapter positively parses
an explicit owner's private-chat approval of this exact plan. **Do not register
`approve` as an unrestricted AI-callable tool.** Being in the owner chat is not
approval by itself; a normal owner context cannot approve. Group messages cannot
approve even when they claim to be the owner. The actual private message id is
retained in the approval audit record. `reject(id, hash, owner_context)` rejects a
pending/approved plan without execution.

## Verified backend contract

`execute(id, hash, context, backend)` takes no parameter overrides. Manual-action
execution requires owner-private context and a stored approval. Autonomous actions
use the same group/owner restrictions as drafting.

```python
backend.capabilities() -> {
  'clientVersion': '4.2.0.1000',
  'actions': {
    'publish_moment': {
      'clientVersion': '4.2.0.1000', 'liveVerified': True
    }
  }
}
```

Capability evidence belongs to trusted runtime code and actual live acceptance
records, never model claims or unit-test success. An absent, unverified or
different-version capability returns `executionStatus='unverified'`, leaves the
plan unclaimed and does not call any UI method.

For announcement updates, the backend must first provide:

```python
backend.get_group_announcement(group) -> {
  'text': 'exact old body', 'verified': True,
  'clientVersion': '4.2.0.1000'
}
```

The old body and its hash are persisted before dispatch. If reading or saving it
fails, no update is attempted. The old body is supplied to `execute` for a
compare-before-write guard; the backend must abort if the group or existing body
has changed. It must also verify the account has announcement permission.

The dispatch signature is:

```python
backend.execute(action, stored_parameters,
    idempotency_key=plan_id,
    expected_previous_announcement=old_text_or_none)
```

The adapter serializes actual wxauto UI activity and verifies exact targets before
acting. `accept_friend` must match both the current request identity and stored
applicant/verification details; an expired or changed request must fail rather
than select another applicant. Other contacts must resolve unambiguously too.

The backend returns either `{'status':'confirmed','receipt':nonempty_evidence}`,
`{'status':'failed','code':reason}` or an unknown result. A truthy object or empty
success claim is not confirmation. The queue persists `claimed` before backend
calls; exceptions and unconfirmed outcomes become `unknown`, with no automatic
retry. After a crash, an existing `claimed` plan also remains nonretryable. Already
pending/claimed/finished plans return `executionStatus='not-dispatched'` on repeat
execution. New attempts require a new plan and, where required, approval.

## Worker and Node integration (offline modules, no live backend)

`social_ipc.py` supplies `SocialDispatcher(config, backend=None)` and `OPS`. The
default backend returns no verified version or actions. It cannot manipulate
WeChat and always leaves autonomous execution unverified.

1. Construct the dispatcher once in the existing wxauto worker. For each freshly
   qualified incoming event, call `social.observe_incoming(event)` immediately
   before emitting it. This method is not exposed through IPC.
2. Dispatch only `op in OPS` using `emit(social.reply(command))`. Operations use
   `target` from Node's trusted session route; Python independently restricts it
   to the owner or configured groups. Do not dispatch `social-execute` while a
   reply transaction holds the UI lock: return an explicit busy/nonexecuted error
   instead of letting social work split outgoing message bubbles.
3. Node creates `socialRpcClient` with the worker stdin writer and readiness
   predicate. Its `handle(event)` consumes only matching `social-result` messages;
   call it before normal event handling. Close it when the worker/plugin exits.
4. Register `registerSocialTools(scope, {request, routeForSession})`. The model
   sees only status, draft, inspect and execute; no approve/reject tool exists.
5. Feed raw incoming events to `consumeOwnerSocialCommand` before ordinary
   batching. The accepted commands are exactly `批准操作 <planId> <hash>` and
   `拒绝操作 <planId> <hash>`. Only a new direct owner's private text qualifies;
   quoted text, image OCR, group messages and history never qualify.

Python remembers the actual incoming message id and exact plan/hash as a
single-use approval grant. Supplying an arbitrary `approvalMessageId`, actor
object or changed recipient through IPC cannot manufacture a grant. Node's
consumer asks Python to approve, then attempts execution of that exact plan;
with the current absent backend it reports approved-but-unverified, never sent.
It returns `{handled, decision, execution, reply}` but **does not send the reply**.
When `handled` is true the root adapter must not also send the approval command
to the model. Even transport errors remain consumed and report unknown outcome
without retry. Root integration may send the short owner-facing reply through
the existing wxauto path, preserving its normal destination and receipt checks.

IPC operations are `social-status`, `social-draft`, `social-inspect`,
`social-execute`, `social-approve`, `social-reject`. All responses use
`{event:'social-result', id, ok, value}` or `{event:'social-result', id, ok:false,
code}`. These are separate from ordinary message-send acknowledgements.
