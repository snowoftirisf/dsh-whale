"""One-use native owner decision, exclusively local approve/reject, never execute.

Reservations are deliberately irreversible, including interrupted/unknown
decisions. There is no automatic retry, grant conversion or UI event relabeling.
"""
import hashlib
import math
import os
import re
import time

from attachment_policy import local_path
from data_paths import absolute_data_path
from native_owner_text_claim import inspect_submitted_owner_text, _compact, _read
from social_actions import ActionContext, REQUIRES_APPROVAL
from social_ipc import parse_owner_command


class _Denied(RuntimeError):
    pass


def _need(value, code):
    if not value:
        raise _Denied(code)


def _time(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _resolve(dispatcher, config, evidence, now):
    owner = config.get('target')
    _need(isinstance(owner, str) and owner and dispatcher.owner == owner and dispatcher.queue.owner == owner
          and local_path(dispatcher.queue.root) == local_path(config['dataRoot']), 'dispatcher-scope-invalid')
    command = parse_owner_command(evidence.raw_text)
    _need(command is not None, 'not-command')
    if 'code' in command:
        resolved = dispatcher.queue.resolve_approval_code(command['code'], operation=command.get('operation'))
        _need(resolved is not None, 'code-not-current')
    else:
        resolved = command
    _need(resolved.get('operation') in ('social-approve', 'social-reject'), 'operation-invalid')
    plan = dispatcher.queue.inspect(resolved['planId'], ActionContext('friend', owner, True))
    _need(plan.get('id') == resolved['planId'] and plan.get('hash') == resolved['hash']
          and plan.get('status') in ('pending', 'approved') and plan.get('action') in REQUIRES_APPROVAL
          and plan.get('origin') == {'kind': 'friend', 'target': owner}, 'plan-not-current')
    _need(_time(plan.get('createdAt')) and _time(plan.get('expiresAt')) and plan['expiresAt'] > now
          and math.floor(plan['createdAt']) <= evidence.timestamp_seconds <= now, 'timestamp-before-plan')
    if 'code' in command:
        notice = plan.get('approvalNotice')
        _need(type(notice) is dict and type(notice.get('version')) is int and notice['version'] == 2
              and notice.get('state') == 'claimed' and notice.get('originTarget') == owner
              and isinstance(notice.get('noticeId'), str) and re.fullmatch('[0-9a-f]{32}', notice['noticeId'])
              and _time(notice.get('claimedAt')) and notice['claimedAt'] <= now
              and evidence.timestamp_seconds >= math.floor(notice['claimedAt']), 'notice-not-fresh')
    return resolved, plan


def _reserve(config, evidence, command, now):
    file = absolute_data_path('config/native-owner-text-approvals/' + evidence.authority_epoch + '/' + evidence.claim_id,
                              root=config['dataRoot'])
    local_path(file.parent).mkdir(parents=True, exist_ok=True)
    local_path(file)
    try:
        file.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError()
    body = {'format': 'whale-native-owner-text-decision', 'version': 1,
        'state': 'reserved', 'authorityEpoch': evidence.authority_epoch, 'claimId': evidence.claim_id,
        'accountId': evidence.account_id, 'ownerNativeChatKey': evidence.native_chat_key,
        'nativeId': evidence.native_id, 'snapshotId': evidence.snapshot_id,
        'sourceInboxSha256': evidence.inbox_sha256,
        'sourceTextSha256': hashlib.sha256(evidence.raw_text.encode('utf8')).hexdigest(),
        'planId': command['planId'], 'planHash': command['hash'], 'operation': command['operation'],
        'reservedAtMs': int(now * 1000)}
    body['digest'] = hashlib.sha256(_compact(body)).hexdigest()
    # Never delete any created marker: fsync/close/readback failure is unknown,
    # not a permit to repeat the same decision on restart.
    with file.open('xb') as stream:
        stream.write(_compact(body))
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != 'nt':
        fd = os.open(file.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    actual, token = _read(file)
    _need(actual == body, 'reservation-readback-failed')
    return file, token


def apply_native_owner_decision(dispatcher, config, claim_id, authority_epoch, *,
                                 active_authority_provider=None, clock=time.time):
    """Decide one exact owner plan once. No backend, Harness or send invocation.

    clock returns epoch seconds. A reserved marker remains on every uncertain
    path. An existing, partial or invalid marker always denies another attempt.
    These local fsync/readback checks do not promise hardware power-loss safety.
    """
    reserved = False
    try:
        evidence = inspect_submitted_owner_text(config, claim_id, authority_epoch,
            active_authority_provider=active_authority_provider, clock=clock)
        now = clock()
        _need(_time(now), 'clock-invalid')
        command, _ = _resolve(dispatcher, config, evidence, now)
        # Mark uncertainty as soon as exclusive create might succeed. Existing
        # files are separately projected as denied, with no mutation or retry.
        try:
            reserved = True
            file, reservation_token = _reserve(config, evidence, command, now)
        except FileExistsError:
            return {'handled': False, 'decisionStatus': None, 'code': 'native-owner-decision-already-reserved'}
        again = inspect_submitted_owner_text(config, claim_id, authority_epoch,
            active_authority_provider=active_authority_provider, clock=clock)
        _need(again == evidence, 'source-changed-after-reservation')
        current, _ = _resolve(dispatcher, config, again, clock())
        _need(current == command and _read(file)[1] == reservation_token, 'decision-changed-after-reservation')
        context = ActionContext('friend', dispatcher.owner, True, evidence.native_id, command['planId'], command['hash'])
        operation = dispatcher.queue.approve if command['operation'] == 'social-approve' else dispatcher.queue.reject
        result = operation(command['planId'], command['hash'], context)
        expected = 'approved' if command['operation'] == 'social-approve' else 'rejected'
        _need(result.get('status') == expected and result.get('id') == command['planId'] and result.get('hash') == command['hash']
              and result.get('action') in REQUIRES_APPROVAL and result.get('origin') == {'kind': 'friend', 'target': dispatcher.owner}, 'decision-result-unknown')
        return {'handled': True, 'decisionStatus': expected, 'code': 'native-owner-decision-' + expected,
            'planId': result['id'], 'hash': result['hash'], 'action': result['action'], 'approvalMessageId': evidence.native_id}
    except Exception as error:
        if reserved:
            return {'handled': True, 'decisionStatus': 'unknown', 'code': 'native-owner-decision-unknown'}
        code = str(error) if isinstance(error, _Denied) else 'unverified'
        return {'handled': False, 'decisionStatus': None, 'code': 'native-owner-decision-' + code}
