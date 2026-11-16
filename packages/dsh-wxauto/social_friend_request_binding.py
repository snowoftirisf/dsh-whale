"""Explicit, unregistered exact-plan full-request binding factory.

Creating the template is inert. The backend invokes bind_plan only after its
claimed-plan gate. No registry, acceptance receipt or runtime capability changes.
"""
import copy
import inspect
import math
import re
from pathlib import Path
from social_actions import ActionContext,_digest
from social_wxauto_backend import ActionBinding,WxautoSocialBackend
from social_friend_request_candidate import FriendRequestCandidate
from social_friend_request_journal import FriendRequestJournal


def require_full_plan(backend,plan):
    if (type(plan)is not dict or plan.get('action')!='add_friend' or plan.get('status')!='claimed'
            or plan.get('origin')!={'kind':'friend','target':backend.owner} or plan.get('approvedBy')!=backend.owner
            or not isinstance(plan.get('approvalMessageId'),str) or not plan['approvalMessageId'].strip()
            or type(plan.get('approvalNotice'))is not dict or plan['approvalNotice'].get('purpose')!='add-friend-request'):
        raise RuntimeError('social-full-request-scope-required')
    notice=plan['approvalNotice'];now=backend.clock()
    if (notice.get('version')!=2 or type(notice.get('version'))is not int or notice.get('state')!='claimed'
            or not isinstance(notice.get('noticeId'),str) or not re.fullmatch('[a-f0-9]{32}',notice['noticeId'])
            or notice.get('originTarget')!=backend.owner
            or any(type(v) not in (int,float) or not math.isfinite(v) for v in (notice.get('claimedAt'),plan.get('approvedAt'),now))
            or not notice['claimedAt']<=plan['approvedAt']<=now
            or not isinstance(plan.get('id'),str) or not re.fullmatch('[a-f0-9]{32}',plan['id'])
            or not isinstance(plan.get('hash'),str) or not re.fullmatch('[a-f0-9]{64}',plan['hash'])):
        raise RuntimeError('social-full-request-scope-required')
    parameters=FriendRequestCandidate.parameters(plan.get('parameters'))
    expires=plan.get('expiresAt')
    if (type(expires) not in (int,float) or not math.isfinite(expires) or type(now) not in (int,float)
            or not math.isfinite(now) or now>=expires or plan.get('hash')!=_digest('add_friend',parameters)):
        raise RuntimeError('social-full-request-plan-invalid')
    return plan


def _dependencies(candidate_factory,journal_factory):
    # Capture both loaded Python bytecode and source hashes, including helpers
    # outside the small delegating callback module. Caller-supplied fake factory
    # code changes the fingerprint too; its tests cannot certify production code.
    import social_friend_request_candidate, social_friend_request_journal
    import social_friend_verification,social_add_friend_candidate
    import social_contact_observation,social_friend_observe,social_friend_search
    import social_actions,attachment_policy,data_paths
    values=[require_full_plan,_dependencies,create_friend_request_binding,ActionBinding.fingerprint,WxautoSocialBackend._execute_core,
        WxautoSocialBackend._execute_core_operation,WxautoSocialBackend._with_chat_recovery,WxautoSocialBackend._evidence]
    modules=(social_friend_request_candidate,social_friend_request_journal,social_friend_verification,
        social_add_friend_candidate,social_contact_observation,social_friend_observe,social_friend_search,
        social_actions,attachment_policy,data_paths)
    for module in modules:
        for _,value in sorted(vars(module).items()):
            if getattr(value,'__module__',None)!=module.__name__:continue
            if inspect.isfunction(value):values.append(value)
            elif inspect.isclass(value):values.extend(v for _,v in inspect.getmembers(value,inspect.isfunction))
    for factory in (candidate_factory,journal_factory):
        if inspect.isclass(factory):values.extend(v for _,v in inspect.getmembers(factory,inspect.isfunction))
        elif inspect.isfunction(factory):values.append(factory)
        else:raise RuntimeError('social-full-request-factory-invalid')
    # Dataclasses generate boilerplate (__init__/__repr__/...) with <string>
    # filenames and no inspectable source. Owned handwritten callbacks remain
    # mandatory; source module hashes cover those dataclass declarations too.
    return tuple(v for v in values if getattr(v,'__code__',None) is not None and not v.__code__.co_filename.startswith('<')
        and Path(v.__code__.co_filename).resolve().is_relative_to(Path(__file__).resolve().parent))


def create_friend_request_binding(backend,*,candidate_factory=FriendRequestCandidate,journal_factory=FriendRequestJournal,lookup_refresh=None):
    """Return ActionBinding template; bind_plan(plan) returns an exact-plan instance.

    cleanup() is idempotent and belongs to that execution. The caller/backend
    must invoke it in finally before any outer chat restoration. The current
    owner-approved notice purpose must remain add-friend-request throughout.
    """
    dependencies=_dependencies(candidate_factory,journal_factory)
    refresh=backend.refresh_chat if lookup_refresh is None else lookup_refresh
    if refresh is not None:
        if not callable(refresh):raise RuntimeError('social-full-request-refresh-invalid')
        dependencies=(*dependencies,refresh)
    def build(candidate=None):
        state={'candidate':candidate,'closed':False,'cleanup_failed':False}
        def bound():
            if state['candidate'] is None:raise RuntimeError('social-plan-bound-instance-required')
            return state['candidate']
        def before(wx,parameters):return bound().observe_before(wx,parameters)
        def perform(wx,parameters):return bound().perform(wx,parameters)
        def after(wx,parameters,outcome,observation):
            value=bound().observe_after(wx,parameters,outcome,observation)
            if not isinstance(value,dict):return {'confirmed':False}
            value={**value}
            value['confirmed']=(value.get('confirmed') is True and value.get('confirmationKind')=='friend-request-submission'
                and value.get('applicationSubmittedVerified') is True and value.get('friendshipConfirmed') is False)
            if value.get('restorationUnavailable') is True or vars(bound()).get('restorationUnavailable') is True:
                state['closed']=True;state['cleanup_failed']=True;backend.chat_binding_failed=True
            return value
        def cleanup():
            if state['closed']:
                if state['cleanup_failed']:raise RuntimeError('social-chat-restore-failed')
                return
            state['closed']=True
            if state['candidate'] is not None:
                try:
                    state['candidate'].close()
                    if vars(state['candidate']).get('restorationUnavailable') is True:
                        raise RuntimeError('social-chat-restore-failed')
                except Exception:
                    state['cleanup_failed']=True
                    backend.chat_binding_failed=True
                    raise RuntimeError('social-chat-restore-failed') from None
        def bind_plan(plan):
            plan=require_full_plan(backend,copy.deepcopy(plan))
            plan_id,hash_=plan['id'],plan['hash']
            context=ActionContext('friend',backend.owner,True)
            def current():
                actual=require_full_plan(backend,backend.plans.inspect(plan_id,context))
                if actual['id']!=plan_id or actual['hash']!=hash_ or actual['parameters']!=plan['parameters']:
                    raise RuntimeError('social-full-request-plan-changed')
                return actual
            current()
            def journal_plan(requested_id):
                if requested_id!=plan_id:raise RuntimeError('social-full-request-plan-changed')
                return current()
            journal=journal_factory(backend.root,journal_plan,backend.owner,clock=backend.clock)
            owned=candidate_factory(backend.wx,backend.config,plan_id=plan_id,plan_hash=hash_,plan_provider=current,
                journal=journal,allowed_groups=backend.groups,client_probe=backend.client_probe,
                refresh_chat=refresh,clock=backend.clock)
            return build(owned)
        return ActionBinding('unregistered-full-friend-request-plan-binding-v1',perform,before,after,
            bind_plan=bind_plan,cleanup=cleanup,source_dependencies=dependencies)
    return build()
