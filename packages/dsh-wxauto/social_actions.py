"""Local social-action plans, approvals and one-shot verified backend dispatch.

No WeChat selectors, network clients or UI calls live in this module. Contexts
and backend capability evidence must be supplied by trusted adapter code.
"""
import copy
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
import secrets
from pathlib import Path
import time
import uuid

from attachment_policy import local_path
from data_paths import absolute_data_path, relative_data_path, resolve_data_paths

AUTONOMOUS = frozenset(('publish_moment', 'set_group_announcement'))
REQUIRES_APPROVAL = frozenset(('create_group', 'add_friend', 'accept_friend'))
ACTIONS = AUTONOMOUS | REQUIRES_APPROVAL
EXPIRES_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class ActionContext:
    kind: str
    target: str
    is_owner: bool = False
    approval_message_id: str | None = None
    approval_plan_id: str | None = None
    approval_plan_hash: str | None = None


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(action, parameters):
    return hashlib.sha256(_canonical({'action': action, 'parameters': parameters}).encode('utf8')).hexdigest()


def _string(value, *, empty=False):
    if not isinstance(value, str) or '\x00' in value or (not empty and not value.strip()):
        raise RuntimeError('social-parameters-invalid')
    return value


def _strings(value, *, empty=False):
    if not isinstance(value, list) or (not empty and not value):
        raise RuntimeError('social-parameters-invalid')
    return [_string(item) for item in value]


class SocialActions:
    def __init__(self, data_root, *, owner_target, allowed_groups=(), clock=time.time):
        self.paths = resolve_data_paths(root=data_root)
        self.root = self.paths['root']
        self.file = self.paths['config'] / 'social-actions.json'
        self.owner = _string(owner_target)
        self.groups = frozenset(_string(group) for group in allowed_groups)
        self.clock = clock

    def _owner(self, context):
        return (isinstance(context, ActionContext) and context.kind == 'friend'
                and context.target == self.owner and context.is_owner is True)

    def _known_context(self, context):
        return self._owner(context) or (isinstance(context, ActionContext)
            and context.kind == 'group' and context.target in self.groups)

    def _authorize(self, action, parameters, context, *, approval=False, execution=False):
        if not self._known_context(context):
            raise RuntimeError('social-context-denied')
        if approval or (execution and action in REQUIRES_APPROVAL):
            if not self._owner(context):
                raise RuntimeError('social-owner-private-required')
        if action == 'publish_moment' and not self._owner(context):
            raise RuntimeError('social-owner-context-required')
        if action == 'set_group_announcement':
            group = parameters['group']
            if group not in self.groups or (not self._owner(context) and context.target != group):
                raise RuntimeError('social-group-denied')

    def _parameters(self, action, supplied):
        if action not in ACTIONS or not isinstance(supplied, dict):
            raise RuntimeError('social-action-invalid')
        parameters = copy.deepcopy(supplied)
        schemas = {
            'publish_moment': ({'text', 'mediaFiles'}, set()),
            'set_group_announcement': ({'group', 'text'}, {'group', 'text'}),
            'create_group': ({'contacts'}, {'contacts'}),
            'add_friend': ({'keywords', 'addmsg', 'remark', 'tags', 'expectedProfileName'}, {'keywords'}),
            'accept_friend': ({'requestId', 'applicant', 'verificationMessage', 'remark', 'tags'}, {'requestId', 'applicant'}),
        }
        allowed, required = schemas[action]
        if set(parameters) - allowed or required - set(parameters):
            raise RuntimeError('social-parameters-invalid')
        if action == 'publish_moment':
            parameters.setdefault('text', '')
            parameters.setdefault('mediaFiles', [])
            _string(parameters['text'], empty=True)
            paths = _strings(parameters['mediaFiles'], empty=True)
            portable = []
            for source in paths:
                path = local_path(source) if Path(source).is_absolute() else absolute_data_path(source, root=self.root)
                if not any(path != parent and path.is_relative_to(parent) for parent in (self.paths['media'], self.paths['outbox'])):
                    raise RuntimeError('social-media-path-denied')
                portable.append(relative_data_path(path, root=self.root))
            parameters['mediaFiles'] = portable
            if not parameters['text'].strip() and not portable:
                raise RuntimeError('social-parameters-invalid')
        elif action == 'create_group':
            values = _strings(parameters['contacts'])
            if len(values) != len(set(values)):
                raise RuntimeError('social-parameters-invalid')
        else:
            for name, value in parameters.items():
                if name == 'tags':
                    _strings(value, empty=True)
                else:
                    _string(value, empty=name in ('text', 'addmsg', 'remark', 'verificationMessage'))
                    if name=='expectedProfileName' and len(value)>128:
                        raise RuntimeError('social-parameters-invalid')
            if action == 'accept_friend':
                parameters.setdefault('verificationMessage', '')
        return parameters

    @contextmanager
    def _locked(self):
        # Cross-process OS lock; no stale lock-file deletion or busy polling.
        local_path(self.file.parent).mkdir(parents=True, exist_ok=True)
        lock_path = local_path(self.file.with_suffix('.lock'))
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, 'r+b') as lock:
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b'\0')
                lock.flush()
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                local_path(self.file)
                if self.file.exists():
                    state = json.loads(self.file.read_text('utf-8-sig'))
                    if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('plans'), dict):
                        raise RuntimeError('social-state-invalid')
                else:
                    state = {'version': 1, 'plans': {}}
                yield state
            finally:
                lock.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _save(self, state):
        temporary = local_path(self.file.with_name(self.file.name + '.' + uuid.uuid4().hex + '.tmp'))
        with temporary.open('x', encoding='utf8') as output:
            output.write(_canonical(state) + '\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.file)

    def _plan(self, state, plan_id, expected_hash=None):
        plan = state['plans'].get(plan_id)
        if not isinstance(plan, dict):
            raise RuntimeError('social-plan-not-found')
        parameters = self._parameters(plan.get('action'), plan.get('parameters'))
        actual = _digest(plan['action'], parameters)
        if actual != plan.get('hash') or (expected_hash is not None and expected_hash != actual):
            raise RuntimeError('social-plan-hash-mismatch')
        return plan

    @staticmethod
    def _require_hash(expected_hash):
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise RuntimeError('social-plan-hash-required')

    def _expired(self, state, plan):
        if plan['status'] in ('pending', 'ready', 'approved') and self.clock() >= plan['expiresAt']:
            plan.update(status='expired', expiredAt=self.clock())
            self._save(state)
        return plan['status'] == 'expired'

    @staticmethod
    def _approval_codes_in_use(state):
        used, retired = set(), set()
        for plan in state['plans'].values():
            if isinstance(plan.get('approvalCode'), str):
                used.add(plan['approvalCode'])
            codes = plan.get('approvalCodes', {})
            if isinstance(codes, dict):
                used.update(v for v in codes.values() if isinstance(v, str))
            rotation = plan.get('approvalCodeRotation', {})
            if isinstance(rotation, dict) and isinstance(rotation.get('retiredLegacyCodeSha256'), str):
                retired.add(rotation['retiredLegacyCodeSha256'])
        return used, retired

    @staticmethod
    def _new_approval_code(used, retired):
        for _ in range(128):
            code = secrets.token_hex(4)
            if code not in used and hashlib.sha256(code.encode()).hexdigest() not in retired:
                used.add(code)
                return code
        raise RuntimeError('social-approval-code-exhausted')

    @classmethod
    def _assign_approval_codes(cls, state, plan):
        used, retired = cls._approval_codes_in_use(state)
        codes = {intent: cls._new_approval_code(used, retired) for intent in ('approve','reject')}
        plan['approvalCodes'] = codes
        return codes

    def draft(self, action, parameters, context):
        parameters = self._parameters(action, parameters)
        self._authorize(action, parameters, context)
        now = self.clock()
        plan = {'id': uuid.uuid4().hex, 'action': action, 'parameters': parameters,
                'hash': _digest(action, parameters), 'createdAt': now,
                'expiresAt': now + EXPIRES_SECONDS,
                'status': 'pending' if action in REQUIRES_APPROVAL else 'ready',
                'origin': {'kind': context.kind, 'target': context.target}}
        with self._locked() as state:
            if action in REQUIRES_APPROVAL:
                self._assign_approval_codes(state, plan)
            state['plans'][plan['id']] = plan
            self._save(state)
        return copy.deepcopy(plan)

    def resolve_approval_code(self, code, *, operation=None):
        if not isinstance(code, str) or len(code) != 8 or any(c not in '0123456789abcdef' for c in code):
            return None
        with self._locked() as state:
            # Any duplicated code is ambiguous, even if one plan has expired.
            _, retired = self._approval_codes_in_use(state)
            if hashlib.sha256(code.encode()).hexdigest() in retired:
                return None
            matches = []
            for p in state['plans'].values():
                codes = p.get('approvalCodes', {})
                if isinstance(codes, dict):
                    matches.extend((p, 'social-'+intent) for intent in ('approve','reject') if codes.get(intent)==code)
                if p.get('approvalCode') == code:
                    matches.append((p, None))
            if len(matches) != 1:
                return None
            matched, intent = matches[0]
            if intent is None:
                if operation not in ('social-approve','social-reject'):
                    return None  # Legacy shared code requires an explicit verb.
                intent = operation
            elif operation is not None and operation != intent:
                return None
            plan = self._plan(state, matched['id'])
            if (plan['action'] not in REQUIRES_APPROVAL or self._expired(state, plan)
                    or plan['status'] not in ('pending', 'approved')):
                return None
            return {'operation':intent,'planId': plan['id'], 'hash': plan['hash']}

    def refresh_pending_approval_codes(self, plan_id, expected_hash):
        """Explicit local maintenance only; never an IPC operation or approval.

        Reissue one legacy pending notice exactly once. Do not call for unknown
        delivery retries or plans that have ever acquired owner approval.
        """
        self._require_hash(expected_hash)
        with self._locked() as state:
            plan = self._plan(state, plan_id, expected_hash)
            old = plan.get('approvalCode')
            notice = plan.get('approvalNotice')
            if (plan['action'] not in REQUIRES_APPROVAL or plan['status'] != 'pending'
                    or self.clock() >= plan['expiresAt']
                    or plan.get('origin') != {'kind':'friend','target':self.owner}
                    or any(k in plan for k in ('approvedBy','approvedAt','approvalMessageId','claimedAt'))
                    or 'approvalCodeRotation' in plan or 'approvalCodes' in plan
                    or not isinstance(old,str) or len(old)!=8 or any(c not in '0123456789abcdef' for c in old)
                    or not isinstance(notice,dict) or notice.get('state')!='claimed'
                    or notice.get('originTarget') != self.owner
                    or type(notice.get('claimedAt')) not in (int,float)):
                raise RuntimeError('social-approval-refresh-not-eligible')
            self._assign_approval_codes(state, plan)
            plan['approvalCodeRotation'] = {'at':self.clock(),
                'retiredLegacyCodeSha256':hashlib.sha256(old.encode()).hexdigest(),
                'previousNoticeSha256':hashlib.sha256(json.dumps(notice,ensure_ascii=False,
                    sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
                'previousNotice':{'state':'claimed','claimedAt':notice['claimedAt'],
                    'originTargetSha256':hashlib.sha256(self.owner.encode()).hexdigest(),
                    **({'noticeId':notice['noticeId']} if isinstance(notice.get('noticeId'),str)
                       and len(notice['noticeId'])==32 and all(c in '0123456789abcdef' for c in notice['noticeId']) else {})}}
            del plan['approvalCode']
            del plan['approvalNotice']
            self._save(state)
            return {'id':plan['id'],'hash':plan['hash'],'status':plan['status'],
                    'approvalCodes':copy.deepcopy(plan['approvalCodes']),'noticeReset':True}

    def claim_approval_notice(self, plan_id, expected_hash, context, *, purpose=None):
        if purpose not in (None, 'add-friend-request'):
            raise RuntimeError('social-notice-purpose-denied')
        self._require_hash(expected_hash)
        with self._locked() as state:
            plan = self._plan(state, plan_id, expected_hash)
            if purpose is not None and (not self._owner(context)
                    or plan['action'] != 'add_friend'
                    or plan['origin'] != {'kind': 'friend', 'target': self.owner}
                    or not isinstance(plan['parameters'].get('expectedProfileName'), str)
                    or not plan['parameters']['expectedProfileName'].strip()):
                raise RuntimeError('social-notice-purpose-denied')
            if not self._owner(context) and plan['origin'] != {'kind': context.kind, 'target': context.target}:
                raise RuntimeError('social-context-denied')
            self._authorize(plan['action'], plan['parameters'], context)
            if plan['action'] not in REQUIRES_APPROVAL:
                raise RuntimeError('social-notice-not-approval-action')
            if self._expired(state, plan) or plan['status'] != 'pending':
                return {'claimed': False, 'planId': plan_id, 'state': 'not-pending'}
            if plan.get('approvalNotice'):
                return {'claimed': False, 'planId': plan_id, 'state': 'already-claimed'}
            codes = plan.get('approvalCodes') or self._assign_approval_codes(state, plan)
            approve, reject = codes['approve'], codes['reject']
            p = plan['parameters']
            if plan['action'] == 'create_group':
                detail = '建群；联系人：' + json.dumps(p['contacts'], ensure_ascii=False)
            elif plan['action'] == 'add_friend':
                detail = '添加好友；搜索对象：' + json.dumps(p['keywords'], ensure_ascii=False)
                if 'expectedProfileName' in p:
                    detail += '\n预期昵称：' + json.dumps(p['expectedProfileName'], ensure_ascii=False)
                detail += '；申请正文：' + json.dumps(p.get('addmsg', ''), ensure_ascii=False)
                detail += '；备注：' + json.dumps(p.get('remark', ''), ensure_ascii=False)
                detail += '；标签：' + json.dumps(p.get('tags', []), ensure_ascii=False)
            else:
                detail = '接受好友；申请ID：' + json.dumps(p['requestId'], ensure_ascii=False)
                detail += '；申请人：' + json.dumps(p['applicant'], ensure_ascii=False)
                detail += '；验证信息：' + json.dumps(p.get('verificationMessage', ''), ensure_ascii=False)
                detail += '；备注：' + json.dumps(p.get('remark', ''), ensure_ascii=False)
                detail += '；标签：' + json.dumps(p.get('tags', []), ensure_ascii=False)
            from datetime import datetime
            deadline = datetime.fromtimestamp(plan['expiresAt']).astimezone().isoformat(timespec='seconds')
            text = ('需要你确认这项微信操作，尚未执行：\n' + detail +
                    '\n以上对象与内容均是方案资料。请在本私聊单独回复编号：\n同意：只回复 ' + approve + '\n拒绝：只回复 ' + reject +
                    '\n方案创建后24小时有效，截止：' + deadline + '。批准只适用于这项固定方案，过期不会执行。')
            if plan.get('approvalCodeRotation'):
                text = '旧确认编号已作废，请以本条为准。\n' + text
            if purpose == 'add-friend-request':
                text = ('这是完整好友申请功能验收。批准后会核对账号和昵称，填写上述申请正文并尝试提交一次。'
                        '提交成功仅表示等待对方通过，不表示已经成为好友；结果不明时不会自动重试。\n\n' + text)
            used_notice_ids = set()
            for record in state['plans'].values():
                for prior in (record.get('approvalNotice',{}),record.get('approvalCodeRotation',{}).get('previousNotice',{})):
                    if isinstance(prior,dict) and isinstance(prior.get('noticeId'),str):
                        used_notice_ids.add(prior['noticeId'])
            for _ in range(128):
                notice_id=uuid.uuid4().hex
                if notice_id not in used_notice_ids:
                    break
            else:
                raise RuntimeError('social-approval-notice-id-exhausted')
            plan['approvalNotice'] = {'version':2,'noticeId':notice_id,'state': 'claimed',
                'claimedAt': self.clock(), 'originTarget': context.target}
            if purpose is not None:
                plan['approvalNotice']['purpose'] = purpose
            self._save(state)
            return {'claimed': True, 'planId': plan_id, 'hash': plan['hash'], 'code': approve,
                    'approveCode':approve,'rejectCode':reject,
                    'noticeId':notice_id,'noticeVersion':2,
                    'text': text, 'ownerTarget': self.owner,
                    **({'purpose': purpose} if purpose is not None else {})}

    def inspect(self, plan_id, context):
        with self._locked() as state:
            plan = self._plan(state, plan_id)
            if not self._owner(context) and plan['origin'] != {'kind': context.kind, 'target': context.target}:
                raise RuntimeError('social-context-denied')
            self._authorize(plan['action'], plan['parameters'], context)
            self._expired(state, plan)
            return copy.deepcopy(plan)

    def approve(self, plan_id, expected_hash, context):
        self._require_hash(expected_hash)
        with self._locked() as state:
            plan = self._plan(state, plan_id, expected_hash)
            self._authorize(plan['action'], plan['parameters'], context, approval=True)
            if not (isinstance(context.approval_message_id, str) and context.approval_message_id.strip()
                    and context.approval_plan_id == plan_id
                    and context.approval_plan_hash == expected_hash):
                raise RuntimeError('social-explicit-owner-approval-required')
            if self._expired(state, plan):
                return copy.deepcopy(plan)
            if plan['action'] not in REQUIRES_APPROVAL or plan['status'] not in ('pending', 'approved'):
                raise RuntimeError('social-plan-not-approvable')
            if plan['status'] == 'pending':
                plan.update(status='approved', approvedAt=self.clock(), approvedBy=self.owner,
                            approvalMessageId=context.approval_message_id)
                self._save(state)
            return copy.deepcopy(plan)

    def reject(self, plan_id, expected_hash, context):
        self._require_hash(expected_hash)
        with self._locked() as state:
            plan = self._plan(state, plan_id, expected_hash)
            self._authorize(plan['action'], plan['parameters'], context, approval=True)
            if plan['status'] not in ('pending', 'approved'):
                raise RuntimeError('social-plan-not-rejectable')
            plan.update(status='rejected', rejectedAt=self.clock())
            self._save(state)
            return copy.deepcopy(plan)

    @staticmethod
    def _capability(backend, action):
        try:
            evidence = backend.capabilities()
            version = evidence.get('clientVersion')
            feature = evidence.get('actions', {}).get(action, {})
            if isinstance(version, str) and version and feature.get('liveVerified') is True and feature.get('clientVersion') == version:
                return version
        except Exception:
            pass
        return None

    def execute(self, plan_id, expected_hash, context, backend):
        self._require_hash(expected_hash)
        with self._locked() as state:
            plan = self._plan(state, plan_id, expected_hash)
            self._authorize(plan['action'], plan['parameters'], context, execution=True)
            if self._expired(state, plan):
                return {**copy.deepcopy(plan), 'executionStatus': 'not-dispatched'}
            if plan['status'] not in ('approved', 'ready'):
                # Pending and already-claimed plans are never dispatched.
                return {**copy.deepcopy(plan), 'executionStatus': 'not-dispatched'}
            if plan['action'] in REQUIRES_APPROVAL and plan.get('approvedBy') != self.owner:
                raise RuntimeError('social-owner-private-required')
            version = self._capability(backend, plan['action'])
            if version is None:
                return {**copy.deepcopy(plan), 'executionStatus': 'unverified'}
            # Persist before backend calls. A crash or exception after this point
            # is an unknown outcome, never permission to execute again.
            plan.update(status='claimed', claimedAt=self.clock(), clientVersion=version)
            self._save(state)
            action, parameters = plan['action'], copy.deepcopy(plan['parameters'])

        previous = None
        try:
            if action == 'set_group_announcement':
                old = backend.get_group_announcement(parameters['group'])
                if not isinstance(old, dict) or old.get('verified') is not True or old.get('clientVersion') != version or not isinstance(old.get('text'), str):
                    self._finish(plan_id, status='failed', resultCode='announcement-backup-unverified')
                    return self.inspect(plan_id, context)
                previous = old['text']
                with self._locked() as state:
                    record = self._plan(state, plan_id, expected_hash)
                    record['previousAnnouncement'] = {'group': parameters['group'], 'text': previous,
                        'sha256': hashlib.sha256(previous.encode('utf8')).hexdigest(), 'savedAt': self.clock()}
                    self._save(state)
            if self._capability(backend, action) != version:
                self._finish(plan_id, status='failed', resultCode='client-capability-changed')
                return self.inspect(plan_id, context)
            if self.clock() >= plan['expiresAt']:
                self._finish(plan_id, status='failed', resultCode='authorization-expired-before-dispatch')
                return self.inspect(plan_id, context)
            result = backend.execute(action, parameters, idempotency_key=plan_id,
                                     expected_previous_announcement=previous)
            if isinstance(result, dict) and result.get('status') == 'confirmed' and result.get('receipt'):
                # Receipt content belongs to the trusted backend, not the model.
                self._finish(plan_id, status='confirmed', receipt=result['receipt'])
            elif isinstance(result, dict) and result.get('status') == 'failed':
                self._finish(plan_id, status='failed', resultCode=str(result.get('code', 'backend-failed'))[:200])
            else:
                self._finish(plan_id, status='unknown', resultCode='backend-outcome-unknown')
        except Exception as error:
            # Do not persist exception messages containing content or credentials.
            self._finish(plan_id, status='unknown', resultCode=type(error).__name__)
        return self.inspect(plan_id, context)

    def _finish(self, plan_id, **changes):
        with self._locked() as state:
            plan = self._plan(state, plan_id)
            if plan['status'] != 'claimed':
                raise RuntimeError('social-plan-state-conflict')
            plan.update(**changes, finishedAt=self.clock())
            self._save(state)
