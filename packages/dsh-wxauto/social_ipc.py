"""Trusted worker boundary for social plans; no real WeChat backend by default."""
import copy
import re
import math

from social_actions import ACTIONS, REQUIRES_APPROVAL, ActionContext, SocialActions
from data_paths import resolve_data_paths

OPS = frozenset(('social-status', 'social-draft', 'social-inspect', 'social-execute', 'social-observe',
                 'social-approve', 'social-reject', 'social-approval-notice', 'social-acceptance'))
_SHORT_COMMAND = re.compile(r'^(批准操作|拒绝操作)\s+([0-9a-f]{8})$', re.I)
_BARE_COMMAND = re.compile(r'^[0-9a-f]{8}$', re.I)
_COMMAND = re.compile(r'^(批准操作|拒绝操作)\s+([0-9a-f]{32})\s+([0-9a-f]{64})$', re.I)


def _validated_group_routes(groups, owner):
    if (type(groups) is not frozenset or len(groups)>4096 or any(
            not isinstance(name,str) or not name or name!=name.strip() or len(name)>256
            or any(char in name for char in ('\r','\n','\x00')) or name==owner for name in groups)):
        raise RuntimeError('social-group-route-sync-invalid')
    return groups


def parse_owner_command(text):
    if not isinstance(text, str):
        return None
    if _BARE_COMMAND.fullmatch(text.strip()):
        return {'code':text.strip().lower()}
    short = _SHORT_COMMAND.fullmatch(text.strip())
    if short:
        return {'operation': 'social-approve' if short[1] == '批准操作' else 'social-reject', 'code': short[2].lower()}
    found = _COMMAND.fullmatch(text.strip())
    if not found:
        return None
    return {'operation': 'social-approve' if found[1] == '批准操作' else 'social-reject',
            'planId': found[2].lower(), 'hash': found[3].lower()}


class UnverifiedSocialBackend:
    """Intentionally has no UI execution methods or claimed client version."""
    def capabilities(self):
        return {'clientVersion': None, 'actions': {}}

    def sync_group_routes(self, groups):
        pass  # No group authorization state or UI implementation exists here.


class SocialDispatcher:
    def __init__(self, config, backend=None, *, clock=None):
        self.owner = config['target']
        self.groups = frozenset(config.get('groups', ()))
        paths = resolve_data_paths(config)
        kwargs = {} if clock is None else {'clock': clock}
        self.queue = SocialActions(paths['root'], owner_target=self.owner, allowed_groups=self.groups, **kwargs)
        self.backend = backend if backend is not None else UnverifiedSocialBackend()
        self._approvals = {}

    def sync_group_routes(self, active_groups):
        """Private worker-only authority update; never exposed through OPS.

        Caller must compute this from verified active worker routes. No plans,
        grants, owner identity, bindings or execution receipts are rewritten.
        """
        groups=_validated_group_routes(active_groups,self.owner)
        sync=getattr(self.backend,'sync_group_routes',None)
        if callable(sync):
            sync(groups)
        elif hasattr(self.backend,'groups') or hasattr(self.backend,'plans'):
            raise RuntimeError('social-group-route-sync-unavailable')
        self.groups=groups
        self.queue.groups=groups

    def _context(self, target):
        if target == self.owner:
            return ActionContext('friend', self.owner, True)
        if target in self.groups:
            return ActionContext('group', target)
        raise RuntimeError('social-target-denied')

    def observe_incoming(self, event):
        """Call ONLY on a fresh, sender-qualified worker event before emit().

        This is not an IPC operation: Node/model commands cannot manufacture
        approval grants. Image OCR, quotes, groups, history and bot outputs do
        not qualify even when they contain the same approval command text.
        """
        if (not isinstance(event, dict) or event.get('event') != 'message'
                or event.get('target') != self.owner or event.get('kind') != 'friend'
                or event.get('mediaType') != 'text'
                or event.get('direction') in ('outgoing', 'self')
                or event.get('source') in ('history', 'self-account', 'snapshot', 'ocr', 'image-ocr', 'vision-evidence', 'quote')
                or not isinstance(event.get('id'), str) or not event['id']):
            return None
        command = parse_owner_command(event.get('text'))
        source_code = command.get('code') if command else None
        if command and 'code' in command:
            resolved = self.queue.resolve_approval_code(command['code'],operation=command.get('operation'))
            if not resolved:
                return None
            command = resolved
        if command:
            self._approvals.setdefault(event['id'], {**command, **({'sourceCode':source_code} if source_code else {})})
            # The normal worker already caps dedup history at 10000 messages.
            if len(self._approvals) > 10000:
                self._approvals.pop(next(iter(self._approvals)))
        return command

    def _approval_context(self, command, context):
        if not context.is_owner or context.kind != 'friend':
            raise RuntimeError('social-owner-private-required')
        message_id = command.get('approvalMessageId')
        grant = self._approvals.get(message_id) if isinstance(message_id, str) else None
        if (not grant or grant['operation'] != command['op']
                or grant['planId'] != command.get('planId') or grant['hash'] != command.get('hash')):
            raise RuntimeError('social-approval-grant-missing')
        if grant.get('sourceCode'):
            current = self.queue.resolve_approval_code(grant['sourceCode'],operation=grant['operation'])
            if current != {k:grant[k] for k in ('operation','planId','hash')}:
                self._approvals.pop(message_id)
                raise RuntimeError('social-approval-grant-missing')
        # Consume before approval/rejection. Invalid ids do not create a reusable
        # general approval; the owner can send a corrected explicit command.
        self._approvals.pop(message_id)
        return ActionContext('friend', self.owner, True, message_id, grant['planId'], grant['hash'])

    def status(self, context):
        visible = {'set_group_announcement'} if context.kind == 'group' else set(ACTIONS)
        verified = {action: self.queue._capability(self.backend, action) is not None for action in visible}
        try:
            version = self.backend.capabilities().get('clientVersion')
        except Exception:
            version = None
        return {'clientVersion': version, 'actions': {action: {
            'liveVerified': verified[action], 'requiresApproval': action in REQUIRES_APPROVAL}
            for action in sorted(visible)}, 'nativeBackendReady': any(verified.values()),
            'approvalChannel': 'owner-private-explicit-plan-hash',
            'note': '未通过当前客户端真实验证的操作不可执行；工具存在不代表微信操作已就绪。'}

    def dispatch(self, command):
        if not isinstance(command, dict) or command.get('op') not in OPS:
            raise RuntimeError('social-operation-denied')
        operation = command['op']
        base = {'op', 'id', 'target'}
        fields = {'social-status': set(), 'social-draft': {'action', 'parameters'},
                  'social-observe': {'purpose','planId','hash'} if command.get('purpose')=='friend-search-plan' else {'purpose'},
                  'social-inspect': {'planId'}, 'social-execute': {'planId', 'hash'},
                  'social-approval-notice': {'planId', 'hash'},
                  'social-acceptance': {'purpose', 'planId', 'hash'},
                  'social-approve': {'planId', 'hash', 'approvalMessageId'},
                  'social-reject': {'planId', 'hash', 'approvalMessageId'}}
        optional = {'purpose'} if operation == 'social-approval-notice' else set()
        if set(command) - base - fields[operation] - optional or fields[operation] - set(command):
            raise RuntimeError('social-command-invalid')
        context = self._context(command.get('target'))
        if operation == 'social-acceptance':
            if not context.is_owner or context.kind != 'friend':
                raise RuntimeError('social-owner-private-required')
            if command['purpose'] not in ('add-friend-initial-contact','add-friend-request'):
                raise RuntimeError('social-acceptance-purpose-denied')
            accept = getattr(self.backend, 'accept_friend_initial' if command['purpose']=='add-friend-initial-contact'
                else 'accept_friend_request', None)
            if not callable(accept):
                raise RuntimeError('social-acceptance-unavailable')
            return accept(plan_id=command['planId'], plan_hash=command['hash'])
        if operation == 'social-observe':
            if not context.is_owner or context.kind != 'friend':
                raise RuntimeError('social-owner-private-required')
            if command['purpose'] not in ('friend-search-owner','friend-search-plan','moments-feed-metadata'):
                raise RuntimeError('social-observe-purpose-denied')
            observe=getattr(self.backend,'observe',None)
            if not callable(observe):raise RuntimeError('social-observe-unavailable')
            return (observe(command['purpose'],plan_id=command['planId'],plan_hash=command['hash'])
                if command['purpose']=='friend-search-plan' else observe(command['purpose']))
        if operation == 'social-status':
            return self.status(context)
        if operation == 'social-draft':
            return self.queue.draft(command['action'], command['parameters'], context)
        if operation == 'social-inspect':
            return self.queue.inspect(command['planId'], context)
        if operation == 'social-approval-notice':
            return self.queue.claim_approval_notice(command['planId'], command['hash'], context,
                **({'purpose':command['purpose']} if 'purpose' in command else {}))
        if operation == 'social-execute':
            if hasattr(self.backend, 'last_chat_recovery'):
                self.backend.last_chat_recovery = None
            result = self.queue.execute(command['planId'], command['hash'], context, self.backend)
            recovery = getattr(self.backend, 'last_chat_recovery', None)
            if isinstance(recovery, dict):
                result['chatRecovery'] = copy.deepcopy(recovery)
            return result
        context = self._approval_context(command, context)
        method = self.queue.approve if operation == 'social-approve' else self.queue.reject
        return method(command['planId'], command['hash'], context)

    def reply(self, command):
        """JSONL response envelope, with errors that never echo chat content."""
        eligible=(type(command)is dict and command.get('op')=='social-observe'
            and command.get('target')==self.owner and command.get('purpose')=='moments-feed-metadata'
            and isinstance(command.get('id'),str) and 0<len(command['id'])<=256 and all(ord(c)>=32 for c in command['id'])
            and {'op','target','purpose'}<=set(command)<= {'op','id','target','purpose'})
        previous=getattr(self.backend,'last_moments_observation',None)
        previous_id=previous.get('attemptId') if type(previous)is dict else None
        def attach(result):
            if not eligible:return result
            value=getattr(self.backend,'last_moments_observation',None)
            if type(value)is not dict or value.get('attemptId')==previous_id:return result
            from social_moments_runtime import PHASES,EXPECTED_CLASSES
            if (value.get('purpose')!='moments-feed-metadata' or not isinstance(value.get('attemptId'),str)
                or not re.fullmatch('[a-f0-9]{32}',value['attemptId']) or value.get('phase') not in PHASES
                or type(value.get('completed'))is not bool or type(value.get('chatRestored')) not in (bool,type(None))
                or type(value.get('durationMs')) not in (int,float) or not math.isfinite(value['durationMs']) or value['durationMs']<0):return result
            safe={k:value[k] for k in ('purpose','attemptId','phase','completed','chatRestored','durationMs')}
            if 'expectedClass' in value:
                if value['expectedClass'] is not None and value['expectedClass'] not in EXPECTED_CLASSES:return result
                safe['expectedClass']=value['expectedClass']
            if 'matches' in value:
                if value['matches'] is not None and not(type(value['matches'])is int and 0<=value['matches']<=4500):return result
                safe['matches']=value['matches']
            if 'code' in value:
                if not isinstance(value['code'],str) or not re.fullmatch('social-[a-z0-9-]{1,120}',value['code']):return result
                safe['code']=value['code']
            result['observation']=safe;return result
        try:
            value = self.dispatch(command)
            return attach({'event': 'social-result', 'id': command.get('id'), 'ok': True, 'value': copy.deepcopy(value)})
        except Exception as error:
            code = str(error) if isinstance(error, RuntimeError) and str(error).startswith('social-') else type(error).__name__
            return attach({'event': 'social-result', 'id': command.get('id') if isinstance(command, dict) else None,
                    'ok': False, 'code': code})
