"""Explicit full friend-request acceptance, separate from initial-contact-only.

No UI work on import. An old initial-only approval can never authorize this
entry. The observed binding owns its exclusive journal and UI cleanup.
"""
import math
import re
import threading

from social_actions import ActionContext
from social_wxauto_backend import ActionBinding, WxautoSocialBackend, run_local_acceptance


def _notice_valid(plan, owner, now):
    notice=plan.get('approvalNotice')
    if (not isinstance(notice,dict) or notice.get('purpose')!='add-friend-request'
            or notice.get('state')!='claimed' or type(notice.get('version')) is not int or notice.get('version')!=2
            or notice.get('originTarget')!=owner or not isinstance(notice.get('noticeId'),str)
            or not re.fullmatch('[a-f0-9]{32}',notice['noticeId'])):
        return False
    claimed,approved=notice.get('claimedAt'),plan.get('approvedAt')
    return (type(claimed) in (int,float) and type(approved) in (int,float) and type(now) in (int,float)
            and math.isfinite(claimed) and math.isfinite(approved) and math.isfinite(now)
            and claimed<=approved<=now)


def _project(plan, recovery=None, *, dispatched=False, notice_valid=False):
    result={key:plan[key] for key in ('id','hash','status','resultCode') if key in plan}
    result['executionStatus']=plan.get('executionStatus','dispatched' if dispatched else 'not-dispatched')
    if isinstance(recovery,dict):result['chatRestored']=recovery.get('restored') is True
    if notice_valid:result['approvalPurpose']='add-friend-request'
    if type(plan.get('acceptancePromoted')) is bool:result['acceptancePromoted']=plan['acceptancePromoted']
    if plan.get('acceptanceCode')=='social-acceptance-chat-recovery-unverified':
        result['acceptanceCode']=plan['acceptanceCode']
    receipt=plan.get('receipt');after=receipt.get('observation') if isinstance(receipt,dict) else None
    if (notice_valid and plan.get('status')=='confirmed' and isinstance(after,dict)
            and after.get('confirmed') is True and after.get('confirmationKind')=='friend-request-submission'
            and after.get('applicationSubmittedVerified') is True and after.get('friendshipConfirmed') is False):
        result.update(confirmationKind='friend-request-submission',applicationSubmittedVerified=True,friendshipConfirmed=False)
    return result


def accept_friend_request(backend, *, plan_id, plan_hash):
    if (not isinstance(plan_id,str) or not re.fullmatch('[a-f0-9]{32}',plan_id)
            or not isinstance(plan_hash,str) or not re.fullmatch('[a-f0-9]{64}',plan_hash)):
        raise RuntimeError('social-acceptance-plan-invalid')
    if threading.get_ident()!=backend.ui_thread_id:raise RuntimeError('social-acceptance-ui-thread-required')
    if backend.chat_binding_failed or backend._navigation_context is not None:
        raise RuntimeError('social-acceptance-navigation-unavailable')
    backend.last_chat_recovery=None
    owner=ActionContext('friend',backend.owner,True)
    plan=backend.plans.inspect(plan_id,owner)
    if (plan.get('hash')!=plan_hash or plan.get('action')!='add_friend'
            or plan.get('origin')!={'kind':'friend','target':backend.owner}):
        raise RuntimeError('social-acceptance-plan-mismatch')
    valid_notice=_notice_valid(plan,backend.owner,backend.clock())
    if backend.clock()>=plan['expiresAt'] or plan.get('status')!='approved':
        return _project(plan,notice_valid=valid_notice)
    if not valid_notice:raise RuntimeError('social-friend-request-purpose-approval-required')
    if (plan.get('approvedBy')!=backend.owner or not isinstance(plan.get('approvalMessageId'),str)
            or not plan['approvalMessageId'].strip()
            or not isinstance(plan.get('parameters',{}).get('expectedProfileName'),str)
            or not plan['parameters']['expectedProfileName'].strip()):
        raise RuntimeError('social-acceptance-owner-profile-approval-required')
    from social_friend_request_binding import create_friend_request_binding
    refresh=backend.refresh_chat
    if refresh is None:
        from wxauto_connection import refresh_chat_binding
        refresh=refresh_chat_binding
    temporary=None;binding=None;cleanup_done=False;cleanup_failed=False

    def cleanup_once():
        nonlocal cleanup_done,cleanup_failed
        if cleanup_done:
            if cleanup_failed:raise RuntimeError('social-chat-restore-failed')
            return
        cleanup_done=True
        try:
            # Core cleanup runs before _with_chat_recovery. If it already
            # failed, never retry UI cleanup or navigate an unresolved modal.
            if temporary is not None and temporary.chat_binding_failed:
                raise RuntimeError('social-chat-restore-failed')
            if binding is not None and callable(binding.cleanup):binding.cleanup()
        except Exception:
            cleanup_failed=True;backend.chat_binding_failed=True
            if temporary is not None:temporary.chat_binding_failed=True
            raise RuntimeError('social-chat-restore-failed') from None

    def restore(wx,target,kind):
        if temporary is not None and temporary.chat_binding_failed:
            backend.chat_binding_failed=True
            raise RuntimeError('social-chat-restore-failed')
        cleanup_once()
        return refresh(wx,target,kind)

    try:
        temporary=WxautoSocialBackend(backend.wx,backend.config,bindings={},client_probe=backend.client_probe,
            clock=backend.clock,refresh_chat=restore)
        temporary.sync_group_routes(backend.groups)
        binding=create_friend_request_binding(temporary,lookup_refresh=refresh)
        if not isinstance(binding,ActionBinding) or not callable(binding.bind_plan) or not callable(binding.cleanup):
            raise RuntimeError('social-acceptance-binding-invalid')
        temporary.bindings['add_friend']=binding
        value=run_local_acceptance(temporary,plan_id=plan_id,expected_hash=plan_hash,
            action='add_friend',fingerprint=binding.fingerprint())
        return _project(value,temporary.last_chat_recovery,dispatched=True,notice_valid=True)
    finally:
        try:cleanup_once()
        except RuntimeError:pass  # Preserve the durable external outcome; worker blocks on fatal flag.
        if temporary is not None:
            backend.last_chat_recovery=temporary.last_chat_recovery
            if temporary.chat_binding_failed:backend.chat_binding_failed=True
