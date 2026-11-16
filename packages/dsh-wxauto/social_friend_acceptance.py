"""Owner-approved, initial-contact-only acceptance. No UI work on import."""
import re
import threading
import hashlib
import json
import math
import uuid

from social_actions import ActionContext
from social_wxauto_backend import ActionBinding, WxautoSocialBackend, run_local_acceptance, _atomic_json


def _integer(value, maximum=2**63-1):
    return type(value) is int and 0 <= value <= maximum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def _bounds(value):
    return type(value) in (list, tuple) and len(value) == 4 and all(_number(v) for v in value)


def _safe_before(value):
    if value is None:
        return None
    keys = {'source','action','phase','observedAt','clientVersion','parametersHash','controls',
            'identityBasis','publicWechatIdVerified','identityEvidence'}
    if (type(value) is not dict or set(value) - keys or value.get('source') != 'wxauto-uia'
            or value.get('action') != 'add_friend' or value.get('phase') != 'before'
            or not _number(value.get('observedAt')) or not _sha(value.get('parametersHash'))
            or not isinstance(value.get('clientVersion'), str)
            or not re.fullmatch(r'\d+(?:\.\d+){1,5}', value['clientVersion'])
            or value.get('identityBasis') != 'owner-reviewed-query-and-name'
            or type(value.get('publicWechatIdVerified')) is not bool
            or value.get('identityEvidence') not in ('matched','id-unavailable','id-ambiguous','id-invalid','id-mismatch','unsupported-profile')
            or type(value.get('controls')) is not list or not 1 <= len(value['controls']) <= 256):
        raise ValueError('unsafe diagnostic')
    fields = {'formRuntimeId','formHandle','buttonRuntimeId','buttonBounds','pid',
              'querySha256','expectedProfileNameSha256','parametersHash'}
    for node in value['controls']:
        if (type(node) is not dict or set(node) != fields
                or not all(_sha(node[k]) for k in ('querySha256','expectedProfileNameSha256','parametersHash'))
                or node['parametersHash'] != value['parametersHash']
                or not all(_integer(node[k]) for k in ('pid','formHandle'))
                or not _bounds(node['buttonBounds'])):
            raise ValueError('unsafe diagnostic')
        for key in ('formRuntimeId','buttonRuntimeId'):
            if (type(node[key]) not in (list,tuple) or not 1 <= len(node[key]) <= 64
                    or any(type(n) is not int or abs(n)>2**63-1 for n in node[key])):
                raise ValueError('unsafe diagnostic')
    return json.loads(json.dumps(value, allow_nan=False))


def _safe_after(value):
    keys = {'schemaVersion','kind','capturedAt','attempted','foregroundOwned','foregroundMain',
            'foregroundWindowHandle','ownershipStable','truncated','nodes','errorType','code'}
    if (type(value) is not dict or set(value)-keys or type(value.get('schemaVersion')) is not int or value.get('schemaVersion') != 1
            or value.get('kind') != 'unverified-add-friend-post-click-diagnostic'
            or not _number(value.get('capturedAt')) or value.get('attempted') is not True
            or any(type(value.get(k)) is not bool for k in ('foregroundOwned','foregroundMain','ownershipStable','truncated'))
            or not _integer(value.get('foregroundWindowHandle'))
            or type(value.get('nodes')) is not list or len(value['nodes']) > 256):
        raise ValueError('unsafe diagnostic')
    if 'errorType' in value and (not isinstance(value['errorType'], str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,79}',value['errorType'])):
        raise ValueError('unsafe diagnostic')
    if 'code' in value and (not isinstance(value['code'], str) or not re.fullmatch('social-[a-z-]{1,100}',value['code'])):
        raise ValueError('unsafe diagnostic')
    node_keys = {'index','parentIndex','depth','rootIndex','class','type','pid','windowHandle',
        'runtimeIdSha256','runtimeIdLength','idSha256','idLength','nameSha256','nameLength',
        'valueAvailable','valueSha256','valueLength','geometryAvailable','bounds','semanticRole'}
    for n,node in enumerate(value['nodes']):
        if (type(node) is not dict or set(node)-node_keys or type(node.get('index')) is not int or node.get('index') != n
                or not _integer(node.get('depth'),10) or not _integer(node.get('rootIndex'),255)
                or any(not _integer(node.get(k)) for k in ('pid','windowHandle'))
                or node.get('semanticRole') not in ('unknown','send','cancel','close','add-contact')
                or type(node.get('valueAvailable')) is not bool or type(node.get('geometryAvailable')) is not bool):
            raise ValueError('unsafe diagnostic')
        parent = node.get('parentIndex')
        if parent == -1 and type(parent) is int:
            if node['depth'] != 0:
                raise ValueError('unsafe diagnostic')
        elif parent is not None and not _integer(parent,n-1):
            raise ValueError('unsafe diagnostic')
        for k in ('class','type'):
            if not isinstance(node.get(k),str) or not re.fullmatch('[A-Za-z0-9_:.-]{0,160}',node[k]):
                raise ValueError('unsafe diagnostic')
        for k in ('runtimeId','id','name'):
            if not _sha(node.get(k+'Sha256')) or not _integer(node.get(k+'Length'),64000):
                raise ValueError('unsafe diagnostic')
        if node['valueAvailable']:
            if not _sha(node.get('valueSha256')) or not _integer(node.get('valueLength'),64000):
                raise ValueError('unsafe diagnostic')
        elif node.get('valueSha256') is not None or node.get('valueLength') not in (None,0):
            raise ValueError('unsafe diagnostic')
        if node['geometryAvailable']:
            if not _bounds(node.get('bounds')):
                raise ValueError('unsafe diagnostic')
        elif node.get('bounds') is not None:
            raise ValueError('unsafe diagnostic')
    return json.loads(json.dumps(value, allow_nan=False))


def _save_diagnostic(backend, candidate, plan_id, plan_hash, client_version):
    # Plain attributes only. Capturing/closing UI remains entirely the candidate's job.
    if getattr(candidate, 'attempted', False) is not True:
        return None
    observation = getattr(candidate, 'last_observation', None)
    if observation is None:
        return {'diagnosticError': 'social-initial-diagnostic-unavailable'}
    try:
        if not isinstance(client_version,str) or not re.fullmatch(r'\d+(?:\.\d+){1,5}',client_version):
            raise ValueError('unsafe diagnostic')
        value = {'kind':'unverified-ui-observation','version':1,'confirmed':False,
            'notAnAcceptanceReceipt':True,'planId':plan_id,'parametersHash':plan_hash,
            'clientVersion':client_version,'before':_safe_before(getattr(candidate,'last_before',None)),
            'after':_safe_after(observation)}
        if value['before'] is not None and value['before']['parametersHash'] != plan_hash:
            raise ValueError('unsafe diagnostic')
        file = backend.paths['backups'] / 'social-initial-contact' / plan_id / (uuid.uuid4().hex+'.json')
        _atomic_json(file,value)
        return {'diagnosticArtifact':{'path':file.relative_to(backend.root).as_posix(),
            'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'kind':'unverified-ui-observation'}}
    except Exception:
        return {'diagnosticError':'social-initial-diagnostic-save-failed'}


def _project(plan, recovery=None, *, dispatched=False):
    result = {key: plan[key] for key in ('id', 'hash', 'status', 'resultCode') if key in plan}
    result['executionStatus'] = plan.get('executionStatus', 'dispatched' if dispatched else 'not-dispatched')
    if isinstance(recovery, dict):
        result['chatRestored'] = recovery.get('restored') is True
    return result


def _execution_diagnostics(candidate, *, phase=None, primary=None, cleanup_failed=False):
    """Normal structured returns only; constructor/binding exceptions stay errors.

    Only owned plain instance attributes; missing/invalid is unknown, not false.
    These observations never grant permission, mark confirmed, or allow replay.
    """
    value = {}
    attributes = vars(candidate) if candidate is not None and hasattr(candidate, '__dict__') else {}
    if type(attributes.get('attempted')) is bool:
        value['addContactClickAttempted'] = attributes['attempted']
    if 'last_before' in attributes:
        value['preflightObserved'] = attributes['last_before'] is not None
    if phase in ('constructor','binding','preflight','perform','observe-after','acceptance'):
        value['failurePhase'] = phase
    allowed = {'social-chat-restore-failed','social-backend-plan-mismatch','social-backend-approval-required',
        'social-target-or-parameters-unverified','social-client-changed','social-authorization-expired',
        'social-backend-preflight-failed','social-post-action-evidence-missing','social-post-action-outcome-unknown',
        'social-acceptance-outcome-unknown','social-acceptance-binding-invalid'}
    if primary is not None:
        value['primaryFailureCode'] = primary if primary in allowed else 'social-initial-failure-unclassified'
    if cleanup_failed:
        value['cleanupFailureCode'] = 'social-chat-restore-failed'
    return value


def accept_friend_initial(backend, *, plan_id, plan_hash):
    if (not isinstance(plan_id, str) or not re.fullmatch('[a-f0-9]{32}', plan_id)
            or not isinstance(plan_hash, str) or not re.fullmatch('[a-f0-9]{64}', plan_hash)):
        raise RuntimeError('social-acceptance-plan-invalid')
    if threading.get_ident() != backend.ui_thread_id:
        raise RuntimeError('social-acceptance-ui-thread-required')
    if backend.chat_binding_failed or backend._navigation_context is not None:
        raise RuntimeError('social-acceptance-navigation-unavailable')
    backend.last_chat_recovery = None
    context = ActionContext('friend', backend.owner, True)
    plan = backend.plans.inspect(plan_id, context)
    if (plan.get('hash') != plan_hash or plan.get('action') != 'add_friend'
            or plan.get('origin') != {'kind': 'friend', 'target': backend.owner}):
        raise RuntimeError('social-acceptance-plan-mismatch')
    if backend.clock() >= plan['expiresAt'] or plan.get('status') != 'approved':
        return _project(plan)
    if (plan.get('approvedBy') != backend.owner
            or not isinstance(plan.get('approvalMessageId'), str) or not plan['approvalMessageId'].strip()
            or not isinstance(plan.get('parameters', {}).get('expectedProfileName'), str)
            or not plan['parameters']['expectedProfileName'].strip()):
        raise RuntimeError('social-acceptance-owner-profile-approval-required')
    # Delayed import and construction: rejected/unapproved plans cannot navigate.
    from social_add_friend_candidate import AddFriendCandidate
    refresh = backend.refresh_chat
    if refresh is None:
        from wxauto_connection import refresh_chat_binding
        refresh = refresh_chat_binding
    candidate = AddFriendCandidate(backend.wx, backend.config, plan_id=plan_id,
        plan_hash=plan_hash, plan_provider=lambda: backend.plans.inspect(plan_id, context),
        allowed_groups=backend.groups, client_probe=backend.client_probe,
        refresh_chat=refresh, clock=backend.clock)
    temporary = None
    close_attempted = False
    close_failed = False
    projected = None
    client_version = None
    failure_phase = None

    def close_once():
        nonlocal close_attempted, close_failed
        if close_attempted:
            if close_failed:
                raise RuntimeError('social-chat-restore-failed')
            return
        close_attempted = True
        try:
            candidate.close()
        except Exception:
            close_failed = True
            backend.chat_binding_failed = True
            if temporary is not None:
                temporary.chat_binding_failed = True
            raise RuntimeError('social-chat-restore-failed') from None

    def restore(wx, target, kind):
        close_once()
        return refresh(wx, target, kind)

    try:
        original = candidate.binding()
    except Exception:
        close_once()
        raise RuntimeError('social-acceptance-binding-invalid') from None
    if not isinstance(original, ActionBinding):
        close_once()
        raise RuntimeError('social-acceptance-binding-invalid')

    def after(wx, parameters, outcome, before):
        nonlocal failure_phase
        try:
            failure_phase = 'observe-after'
            observed = original.observe_after(wx, parameters, outcome, before)
            # This entrance cannot certify an actual friend request or friendship.
            return {**observed, 'confirmed': False} if isinstance(observed, dict) else {'confirmed': False}
        finally:
            close_once()

    def before(wx, parameters):
        nonlocal failure_phase
        failure_phase = 'preflight'
        return original.observe_before(wx, parameters)

    def perform(wx, parameters):
        nonlocal failure_phase
        failure_phase = 'perform'
        return original.perform(wx, parameters)

    binding = ActionBinding(original.implementation_id + ':initial-contact-only',
        perform, before, after)
    try:
        failure_phase = 'acceptance'
        temporary = WxautoSocialBackend(backend.wx, backend.config,
            bindings={'add_friend': binding}, client_probe=backend.client_probe,
            clock=backend.clock, refresh_chat=restore)
        temporary.sync_group_routes(backend.groups)
        client_version = temporary._client()['clientVersion']
        value = run_local_acceptance(temporary, plan_id=plan_id,
            expected_hash=plan_hash, action='add_friend', fingerprint=binding.fingerprint())
        projected = _project(value, temporary.last_chat_recovery, dispatched=True)
        return projected
    finally:
        try:
            try:
                close_once()
            except RuntimeError:
                # Preserve the durable external outcome; worker stops on the
                # fatal flag below instead of retrying or using stale caches.
                pass
        finally:
            if temporary is not None:
                backend.last_chat_recovery = temporary.last_chat_recovery
                if temporary.chat_binding_failed:
                    backend.chat_binding_failed = True
            diagnostic = _save_diagnostic(backend,candidate,plan_id,plan_hash,client_version)
            if projected is not None and diagnostic is not None:
                projected.update(diagnostic)
            if projected is not None:
                recovery = temporary.last_chat_recovery if temporary is not None else None
                projected.update(_execution_diagnostics(candidate,phase=failure_phase if projected.get('status') in ('failed','unknown') else None,
                    primary=projected.get('resultCode'),cleanup_failed=close_failed or isinstance(recovery,dict) and recovery.get('restored') is False))
