"""Read-only submitted owner-text evidence, never an approval or send permit.

A durable submitted claim, an active caller-owned authority and an authentic
exact native snapshot row are all necessary. The returned evidence is NOT
one-use authorization: a future dispatcher still needs its own durable ledger.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid

from attachment_policy import local_path
from data_paths import absolute_data_path
from native_owner_text_catalog import list_native_owner_texts


class NativeOwnerTextClaimError(RuntimeError):
    pass


def _need(value, code='unverified'):
    if not value:
        raise NativeOwnerTextClaimError('native-owner-text-claim-' + code)


def _compact(value):
    # All accepted numeric fields are safe integers; preserve JSON insertion
    # order to reproduce the Node writer's JSON.stringify(body) digest.
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf8')


def _hash(value):
    return hashlib.sha256(_compact(value)).hexdigest()


def _integer(value, minimum=1):
    return type(value) is int and minimum <= value <= 9007199254740991


def _string(value, maximum=4000):
    return isinstance(value, str) and 0 < len(value) <= maximum and '\0' not in value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, 'json-duplicate-key')
        result[key] = value
    return result


def _signature(info):
    # Windows path-stat and handle-fstat can disagree on creation/ctime after
    # Node atomic rename. Identity, length, mtime and link count must agree;
    # the final CAS additionally compares the complete file's SHA256.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)


def _read(file):
    file = local_path(file)
    before = file.stat()
    _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= 64 * 1024 * 1024, 'file-invalid')
    with file.open('rb') as stream:
        _need(_signature(os.fstat(stream.fileno())) == _signature(before), 'file-changed')
        raw = stream.read(64 * 1024 * 1024 + 1)
        _need(_signature(os.fstat(stream.fileno())) == _signature(before), 'file-changed')
    _need(_signature(local_path(file).stat()) == _signature(before) and len(raw) == before.st_size, 'file-changed')
    value = json.loads(raw.decode('utf8'), object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    _need(type(value) is dict, 'state-invalid')
    return value, (hashlib.sha256(raw).hexdigest(), _signature(before))


def _settings(config):
    _need(type(config) is dict, 'config-invalid')
    root = config.get('dataRoot')
    _need(isinstance(root, str) and Path(root).is_absolute(), 'config-invalid')
    root = local_path(root)
    account, capture, sources = config.get('accountId'), config.get('capture'), config.get('nativeVoiceSources')
    _need(_string(account) and type(capture) is dict and type(sources) is dict, 'config-invalid')
    owner = sources.get('ownerNativeChatKey')
    own, source = capture.get('accountUsername'), capture.get('sourceId')
    _need(isinstance(owner, str) and re.fullmatch('wechat:[A-Za-z0-9_-]{1,256}', owner)
          and _string(own, 256) and _string(source, 256) and owner[7:] != own, 'config-invalid')
    return root, account, owner, owner[7:], own, source


def _active(provider, account, owner, epoch):
    _need(callable(provider), 'authority-inactive')
    value = provider()
    _need(type(value) is dict and value.get('active') is True and value.get('accountId') == account
          and value.get('ownerNativeChatKey') == owner and value.get('authorityEpoch') == epoch, 'authority-inactive')


_MESSAGE_KEYS = ('nativeId', 'serverId', 'nativeChatKey', 'senderId', 'senderDisplayName',
                 'rawText', 'timestampSeconds', 'localId', 'snapshotSourceId', 'direction')


def _validate_state(value, account, owner, peer, epoch):
    body_keys = {'format', 'version', 'accountId', 'ownerNativeChatKey', 'expectedPeerUsername',
                 'scopeDigest', 'authorityEpoch', 'cutoverAtMs', 'cutoverFloorSeconds', 'revision', 'claims'}
    baseline_keys = {'baselineSnapshotId', 'baselineCatalogDigest'}
    _need(set(value) in (body_keys | {'digest'}, body_keys | baseline_keys | {'digest'}), 'state-invalid')
    body = {key: item for key, item in value.items() if key != 'digest'}
    _need(value['digest'] == _hash(body), 'digest-invalid')
    if 'baselineSnapshotId' in body:
        _need(_string(body['baselineSnapshotId']) and isinstance(body['baselineCatalogDigest'], str)
              and re.fullmatch('[0-9a-f]{64}', body['baselineCatalogDigest']), 'state-invalid')
    _need(body['format'] == 'whale-native-owner-text-inbox' and type(body['version']) is int and body['version'] == 1
          and body['accountId'] == account and body['ownerNativeChatKey'] == owner and body['expectedPeerUsername'] == peer
          and body['scopeDigest'] == _hash([account, owner, peer]) and body['authorityEpoch'] == epoch
          and _integer(body['cutoverAtMs']) and body['cutoverFloorSeconds'] == (body['cutoverAtMs'] + 999) // 1000
          and _integer(body['cutoverFloorSeconds']) and _integer(body['revision'])
          and type(body['claims']) is list and len(body['claims']) <= 100000, 'state-invalid')
    seen = set()
    for claim in body['claims']:
        _need(type(claim) is dict and set(claim) <= set(_MESSAGE_KEYS) | {'snapshotId', 'claimId', 'state', 'createdAt', 'holdReason', 'submittedAt'}
              and set(_MESSAGE_KEYS) | {'snapshotId', 'claimId', 'state', 'createdAt'} <= set(claim), 'state-invalid')
        server, native = claim['serverId'], claim['nativeId']
        _need(isinstance(server, str) and re.fullmatch('[1-9][0-9]{0,29}', server) and native == 'wechat-server:' + server
              and native not in seen and claim['nativeChatKey'] == owner and claim['senderId'] == peer
              and claim['direction'] == 'incoming' and _string(claim['senderDisplayName'])
              and _string(claim['rawText'], 64000) and bool(claim['rawText'].strip())
              and _integer(claim['timestampSeconds']) and _integer(claim['localId']) and _string(claim['snapshotSourceId'])
              and _string(claim['snapshotId']) and _integer(claim['createdAt'])
              and claim['state'] in ('baseline', 'held', 'queued', 'submitted'), 'state-invalid')
        identity = [account, owner, native, server, peer, claim['rawText'], claim['timestampSeconds'], claim['localId']]
        _need(claim['claimId'] == _hash(identity)[:32], 'claim-id-invalid')
        if claim['state'] == 'held':
            _need(claim.get('holdReason') in ('late-history', 'future-timestamp'), 'state-invalid')
        if claim['state'] == 'submitted':
            _need(_integer(claim.get('submittedAt')) and claim['submittedAt'] >= claim['createdAt'], 'state-invalid')
        seen.add(native)
    return body


@dataclass(frozen=True)
class SubmittedOwnerTextEvidence:
    claim_id: str
    authority_epoch: str
    account_id: str
    native_chat_key: str
    native_id: str
    server_id: str
    sender_id: str
    raw_text: str = field(repr=False)
    timestamp_seconds: int
    snapshot_id: str
    snapshot_source_id: str
    submitted_at_ms: int
    cutover_floor_seconds: int
    inbox_sha256: str
    revision: int
    source: str = 'authenticated-submitted-native-owner-text-claim'


def inspect_submitted_owner_text(config, claim_id, authority_epoch, *,
                                 active_authority_provider=None, clock=time.time):
    """Validate evidence only. No claim, approval, reset, reply or send mutation.

    clock returns epoch seconds. Authority provider is a trusted application
    callback, not request/model input. Missing/inactive authority always denies.
    File and authority are rechecked after exact original snapshot verification.
    """
    try:
        _need(isinstance(claim_id, str) and re.fullmatch('[0-9a-f]{32}', claim_id)
              and isinstance(authority_epoch, str) and re.fullmatch('[0-9a-f]{32}', authority_epoch), 'request-invalid')
        settings = _settings(config)
        root, account, owner, peer, own, source = settings
        _active(active_authority_provider, account, owner, authority_epoch)
        now = clock()
        _need(type(now) in (int, float) and math.isfinite(now) and now > 0, 'clock-invalid')
        now_ms = int(now * 1000)
        file = absolute_data_path('config/native-owner-text-inbox.json', root=root)
        value, token = _read(file)
        body = _validate_state(value, account, owner, peer, authority_epoch)
        found = [claim for claim in body['claims'] if claim['claimId'] == claim_id]
        _need(len(found) == 1 and found[0]['state'] == 'submitted', 'claim-not-submitted')
        claim = found[0]
        _need(body['cutoverAtMs'] <= now_ms and body['cutoverFloorSeconds'] <= claim['timestampSeconds'] <= int(now) + 30
              and body['cutoverAtMs'] <= claim['createdAt'] <= claim['submittedAt'] <= now_ms + 30000, 'claim-not-fresh')
        snapshot_id = claim['snapshotId']
        _need(str(uuid.UUID(snapshot_id)) == snapshot_id, 'snapshot-invalid')
        snapshot = absolute_data_path('snapshots/windows-live/snapshots/' + snapshot_id, root=root)
        catalog = list_native_owner_texts(snapshot, expected_account_username=own, expected_source_id=source,
            account_id=account, owner_native_chat_key=owner, expected_peer_username=peer, expected_snapshot_id=snapshot_id)
        _need(catalog.get('source') == 'authenticated-native-owner-text-catalog' and catalog.get('complete') is True
              and catalog.get('accountId') == account and catalog.get('nativeChatKey') == owner
              and catalog.get('snapshotId') == snapshot_id and catalog.get('scopeDigest') == body['scopeDigest'], 'snapshot-mismatch')
        exact = [message for message in catalog['messages'] if message['nativeId'] == claim['nativeId']]
        _need(len(exact) == 1 and all(exact[0].get(key) == claim[key] for key in _MESSAGE_KEYS if key != 'senderDisplayName'), 'native-source-mismatch')
        _need(_settings(config) == settings, 'config-changed')
        _active(active_authority_provider, account, owner, authority_epoch)
        _need(_read(file)[1] == token, 'file-changed')
        return SubmittedOwnerTextEvidence(claim_id, authority_epoch, account, owner, claim['nativeId'], claim['serverId'], peer,
            claim['rawText'], claim['timestampSeconds'], snapshot_id, claim['snapshotSourceId'], claim['submittedAt'],
            body['cutoverFloorSeconds'], token[0], body['revision'])
    except NativeOwnerTextClaimError:
        raise
    except Exception:
        raise NativeOwnerTextClaimError('native-owner-text-claim-unverified') from None
