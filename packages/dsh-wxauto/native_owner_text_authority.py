"""Staged single-authority controller; no UI, history, approval or model APIs.

Commit is not a complete cutover protocol. Its parent must first settle existing
UI buffers, authenticate two equal-ID complete catalogs, and explicitly create
the baseline inbox using prepare's exact timestamp. That complete catalog is an
intentional historical baseline, not evidence that every old message was replied
to. Late-written IDs predating the prepare boundary remain ambiguous/held; no
automatic historical replay is authorized. There is no rollback or automatic activation.
"""
import copy
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from native_owner_text_claim import _read, _settings, _validate_state, _compact, _hash, _integer, _string
from data_paths import absolute_data_path
from attachment_policy import local_path
from native_owner_text_catalog import list_native_owner_texts


class NativeOwnerTextAuthorityError(RuntimeError):
    pass


MESSAGE_KEYS = ('nativeId','serverId','nativeChatKey','senderId','senderDisplayName','rawText',
                'timestampSeconds','localId','snapshotSourceId','direction')


def _normalized(messages):
    return sorted(({key: row[key] for key in MESSAGE_KEYS} for row in messages), key=lambda row:row['nativeId'])


def _need(value, code):
    if not value:
        raise NativeOwnerTextAuthorityError('native-owner-text-authority-' + code)


class NativeOwnerTextAuthority:
    def __init__(self, config, *, max_deferred=256, clock=time.time):
        _need(type(config) is dict and type(max_deferred) is int and 1 <= max_deferred <= 4096 and callable(clock), 'config-invalid')
        # No filesystem reads or initialization until restore/commit.
        self.config = config
        self.max_deferred, self.clock = max_deferred, clock
        self._mode, self._epoch, self._ticket = 'unrestored', None, None
        self._prepared_at_ms = None
        self._deferred, self._dirty = [], False
        self._lock = threading.RLock()

    def status(self):
        with self._lock:
            return {'mode': self._mode, 'authorityEpoch': self._epoch, 'ticket': self._ticket,
                    'deferred': len(self._deferred), 'dirty': self._dirty,
                    **({'preparedAtMs':self._prepared_at_ms} if self._mode == 'preparing' else {})}

    def _settings(self):
        root, account, key, peer, own, source = _settings(self.config)
        target = self.config.get('target')
        _need(_string(target) and self.config.get('capture', {}).get('enabled') is True, 'source-proof-required')
        return root, account, key, peer, own, source, target

    def _paths(self, root):
        return (absolute_data_path('config/native-owner-text-authority.json', root=root),
                absolute_data_path('config/native-owner-text-inbox.json', root=root))

    def _inbox(self, file, account, key, peer, epoch, *, baseline=False):
        value, token = _read(file)
        body = _validate_state(value, account, key, peer, epoch)
        if baseline:
            _need(body['revision'] == 1 and all(c['state'] == 'baseline' for c in body['claims']), 'baseline-required')
        return body, token

    def _record(self, value, settings):
        root, account, key, peer, own, source, target = settings
        fields = {'format', 'version', 'active', 'accountId', 'ownerTarget', 'ownerNativeChatKey',
                  'expectedPeerUsername', 'authorityEpoch', 'captureAccountUsername', 'captureSourceId',
                  'initialInboxSha256', 'initialInboxRevision', 'cutoverAtMs', 'createdAtMs'}
        fields |= {'baselineSnapshotId','baselineCatalogDigest','cutoverFloorSeconds'}
        _need(set(value) == fields | {'digest'}, 'record-invalid')
        body = {k: v for k, v in value.items() if k != 'digest'}
        _need(value['digest'] == _hash(body) and body['format'] == 'whale-native-owner-text-authority'
              and type(body['version']) is int and body['version'] == 1 and body['active'] is True
              and body['accountId'] == account and body['ownerTarget'] == target and body['ownerNativeChatKey'] == key
              and body['expectedPeerUsername'] == peer and body['captureAccountUsername'] == own
              and body['captureSourceId'] == source and isinstance(body['authorityEpoch'], str)
              and re.fullmatch('[a-f0-9]{32}', body['authorityEpoch'])
              and isinstance(body['initialInboxSha256'], str) and re.fullmatch('[a-f0-9]{64}', body['initialInboxSha256'])
              and type(body['initialInboxRevision']) is int and body['initialInboxRevision'] == 1
              and _integer(body['cutoverAtMs']) and _integer(body['createdAtMs'])
              and str(uuid.UUID(body['baselineSnapshotId'])) == body['baselineSnapshotId']
              and isinstance(body['baselineCatalogDigest'],str) and re.fullmatch('[a-f0-9]{64}',body['baselineCatalogDigest'])
              and body['cutoverFloorSeconds'] == (body['cutoverAtMs']+999)//1000
              and body['cutoverAtMs'] <= body['createdAtMs'], 'record-invalid')
        return body

    def restore(self):
        with self._lock:
            _need(self._mode == 'unrestored', 'restore-once')
            try:
                # An absent opt-in marker preserves legacy UI ingress even if
                # native capture is disabled or has never been configured.
                root = self.config.get('dataRoot')
                _need(isinstance(root,str) and Path(root).is_absolute(), 'config-invalid')
                root = local_path(root)
                record_file, inbox_file = self._paths(root)
                try:
                    value, token = _read(record_file)
                except FileNotFoundError:
                    self._mode = 'ui'
                    return self.status()
                settings = self._settings()
                _, account, key, peer, _, _, _ = settings
                body = self._record(value, settings)
                inbox, _ = self._inbox(inbox_file, account, key, peer, body['authorityEpoch'])
                _need(inbox['revision'] >= body['initialInboxRevision'] and inbox['cutoverAtMs'] == body['cutoverAtMs'], 'inbox-mismatch')
                _need(inbox.get('baselineSnapshotId') == body['baselineSnapshotId']
                      and inbox.get('baselineCatalogDigest') == body['baselineCatalogDigest']
                      and inbox['cutoverFloorSeconds'] == body['cutoverFloorSeconds'], 'inbox-mismatch')
                # Exact original hash while still the initial revision; legitimate
                # later inbox mutations retain epoch/schema and cannot reset it.
                if inbox['revision'] == 1:
                    _need(_read(inbox_file)[1][0] == body['initialInboxSha256'], 'inbox-mismatch')
                _need(_read(record_file)[1] == token and self._settings() == settings, 'record-changed')
                self._mode, self._epoch = 'native', body['authorityEpoch']
                return self.status()
            except Exception:
                self._mode = 'blocked'
                raise NativeOwnerTextAuthorityError('native-owner-text-authority-restore-unverified') from None

    def prepare(self):
        with self._lock:
            _need(self._mode == 'ui', 'ui-required')
            self._settings()
            now = self.clock()
            _need(type(now) in (int,float) and math.isfinite(now) and 0 < now <= 9007199254740.991
                  and _integer(int(now*1000)), 'clock-invalid')
            self._prepared_at_ms = int(now*1000)
            self._ticket, self._mode, self._dirty = uuid.uuid4().hex, 'preparing', False
            return self.status()

    def route_event(self, event):
        with self._lock:
            # This class does not interpret text or mint social grants. Other
            # routes and media retain their original ingress envelope.
            owner = (type(event) is dict and event.get('target') == self.config.get('target')
                     and event.get('kind') == 'friend' and event.get('mediaType') == 'text'
                     and not event.get('images') and not event.get('files') and not event.get('audio'))
            if not owner or self._mode == 'ui':
                return {'action': 'pass', 'event': copy.deepcopy(event)}
            if self._mode == 'preparing':
                self._dirty = True
                if len(self._deferred) >= self.max_deferred:
                    self._mode = 'blocked'
                    # The caller MUST durably defer this returned envelope and
                    # stop owner-text reads. Existing queued events stay owned
                    # here; no generic blocked/unknown rollback is authorized.
                    return {'action': 'blocked', 'code': 'native-owner-text-authority-deferred-capacity',
                            'event': copy.deepcopy(event), 'requiresDurableDeferral': True, 'stopOwnerTextReads': True}
                self._deferred.append(copy.deepcopy(event))
                return {'action': 'deferred'}
            if self._mode == 'native':
                return {'action': 'hint', 'hint': {'source': 'owner-text-ui-observation', 'authorityEpoch': self._epoch}}
            return {'action': 'blocked', 'code': 'native-owner-text-authority-unavailable'}

    def abort(self, ticket):
        with self._lock:
            _need(self._mode == 'preparing' and ticket == self._ticket, 'ticket-invalid')
            events, self._deferred = self._deferred, []
            self._mode, self._ticket, self._dirty = 'ui', None, False
            self._prepared_at_ms = None
            return {'mode': 'ui', 'events': events}

    def commit(self, ticket, epoch):
        with self._lock:
            _need(self._mode == 'preparing' and ticket == self._ticket, 'ticket-invalid')
            _need(not self._dirty and not self._deferred, 'not-quiet')
            _need(isinstance(epoch, str) and re.fullmatch('[a-f0-9]{32}', epoch), 'epoch-invalid')
            settings = self._settings()
            root, account, key, peer, own, source, target = settings
            record_file, inbox_file = self._paths(root)
            body, token = self._inbox(inbox_file, account, key, peer, epoch, baseline=True)
            _need(body['cutoverAtMs'] == self._prepared_at_ms, 'prepare-boundary-mismatch')
            snapshot_id = body.get('baselineSnapshotId')
            _need(isinstance(snapshot_id,str) and str(uuid.UUID(snapshot_id)) == snapshot_id, 'baseline-proof-required')
            snapshot = absolute_data_path('snapshots/windows-live/snapshots/'+snapshot_id, root=root)
            catalog = list_native_owner_texts(snapshot,expected_account_username=own,expected_source_id=source,
                account_id=account,owner_native_chat_key=key,expected_peer_username=peer,expected_snapshot_id=snapshot_id)
            _need(catalog.get('source') == 'authenticated-native-owner-text-catalog' and catalog.get('complete') is True
                  and catalog.get('accountId') == account and catalog.get('nativeChatKey') == key
                  and catalog.get('scopeDigest') == body['scopeDigest'] and catalog.get('snapshotId') == snapshot_id, 'baseline-proof-required')
            messages = _normalized(catalog['messages'])
            _need(body.get('baselineCatalogDigest') == _hash([snapshot_id,messages])
                  and _normalized(body['claims']) == messages, 'baseline-proof-mismatch')
            now = self.clock()
            _need(type(now) in (int, float) and math.isfinite(now) and now > 0, 'clock-invalid')
            at = int(now * 1000)
            _need(body['cutoverAtMs'] <= at, 'cutover-invalid')
            record = {'format': 'whale-native-owner-text-authority', 'version': 1, 'active': True,
                      'accountId': account, 'ownerTarget': target, 'ownerNativeChatKey': key, 'expectedPeerUsername': peer,
                      'authorityEpoch': epoch, 'captureAccountUsername': own, 'captureSourceId': source,
                      'initialInboxSha256': token[0], 'initialInboxRevision': body['revision'],
                      'baselineSnapshotId':snapshot_id,'baselineCatalogDigest':body['baselineCatalogDigest'],
                      'cutoverFloorSeconds':body['cutoverFloorSeconds'],
                      'cutoverAtMs': body['cutoverAtMs'], 'createdAtMs': at}
            record['digest'] = _hash(record)
            _need(_read(inbox_file)[1] == token and self._settings() == settings, 'inbox-changed')
            # Exclusive immutable marker, deliberately retained if a write or
            # readback is unknown. Never unlink/truncate to resume UI authority.
            try:
                with record_file.open('xb') as stream:
                    stream.write(_compact(record));stream.flush();os.fsync(stream.fileno())
                actual, _ = _read(record_file)
                _need(actual == record and _read(inbox_file)[1] == token, 'commit-readback-failed')
            except Exception:
                self._mode = 'blocked'
                raise NativeOwnerTextAuthorityError('native-owner-text-authority-commit-unknown') from None
            self._mode, self._epoch, self._ticket = 'native', epoch, None
            self._prepared_at_ms = None
            return self.status()
