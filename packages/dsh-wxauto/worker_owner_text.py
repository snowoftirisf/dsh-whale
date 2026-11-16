"""Worker-only single owner-text ingress bridge. No activation on construction.

The explicit controller restore preserves legacy UI ingress with no marker.
Overflow envelopes are private durable recovery evidence, never auto-replayed.
"""
import copy
import hashlib
import os
import re
import uuid

from attachment_policy import local_path
from data_paths import absolute_data_path
from native_owner_text_authority import NativeOwnerTextAuthority
from native_owner_text_claim import _compact, _read
from native_owner_text_approval import apply_native_owner_decision

OPS = frozenset('owner-text-' + op for op in ('status', 'prepare', 'abort', 'commit', 'decision'))
VERSION = 1


class WorkerOwnerTextFatal(RuntimeError):
    pass


class WorkerOwnerTextBridge:
    def __init__(self, config, *, publish, emit, controller_factory=NativeOwnerTextAuthority):
        self.config, self.publish, self.emit = config, publish, emit
        self.controller = controller_factory(config)
        self.deferred = []
        self.stopped = False
        self.closed = False
        self.recovery_saved = False
        try:
            self.controller.restore()
        except Exception:
            self.stopped = True

    def status(self):
        return {'version': VERSION, **self.controller.status(), 'readsStopped': self.stopped}

    def active_authority(self):
        status = self.status()
        return {'active': status['mode'] == 'native' and not self.stopped,
            'authorityEpoch': status['authorityEpoch'], 'accountId': self.config.get('accountId'),
            'ownerNativeChatKey': self.config.get('nativeVoiceSources', {}).get('ownerNativeChatKey')}

    def _persist(self, events):
        identifier = uuid.uuid4().hex
        file = absolute_data_path('config/native-owner-text-deferred/' + identifier + '.json', root=self.config['dataRoot'])
        local_path(file.parent).mkdir(parents=True, exist_ok=True)
        local_path(file)
        value = {'format': 'whale-native-owner-text-deferred', 'version': 1,
            'accountId': self.config.get('accountId'), 'ownerTarget': self.config['target'],
            'authority': self.controller.status(), 'events': copy.deepcopy(events)}
        value['digest'] = hashlib.sha256(_compact(value)).hexdigest()
        # Publish the complete list by atomic rename only after file fsync.
        # Failed/partial staging files stay private recovery evidence; they never
        # authorize replay or continued reads and are not reported as durable.
        staging = local_path(file.with_suffix('.json.tmp'))
        with staging.open('xb') as stream:
            stream.write(_compact(value)); stream.flush(); os.fsync(stream.fileno())
        if file.exists():
            raise WorkerOwnerTextFatal('owner-text-deferral-path-conflict')
        os.rename(staging, file)
        if os.name != 'nt':
            fd = os.open(file.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        if _read(file)[0] != value:
            raise WorkerOwnerTextFatal('owner-text-deferral-readback-failed')
        return 'config/native-owner-text-deferred/' + identifier + '.json'

    def _block(self, events):
        self.stopped = True
        try:
            relative = self._persist(events)
        except Exception:
            self.emit({'event': 'connection-blocked', 'code': 'owner-text-deferral-persistence-failed', 'phase': 'owner-text-authority'})
            raise WorkerOwnerTextFatal('owner-text-deferral-persistence-failed') from None
        self.emit({'event': 'connection-blocked', 'code': 'owner-text-authority-blocked',
                   'phase': 'owner-text-authority', 'deferredCount': len(events), 'recoveryFile': relative})
        self.recovery_saved = True
        raise WorkerOwnerTextFatal('owner-text-authority-blocked')

    def close(self):
        """Save pending UI evidence on every worker exit, never replay or rollback."""
        if self.closed:
            return
        self.closed = True
        self.stopped = True
        if not self.deferred or self.recovery_saved:
            return
        try:
            relative = self._persist(self.deferred)
        except Exception:
            self.emit({'event': 'connection-blocked', 'code': 'owner-text-shutdown-persistence-failed',
                       'phase': 'owner-text-authority'})
            raise WorkerOwnerTextFatal('owner-text-shutdown-persistence-failed') from None
        self.recovery_saved = True
        self.emit({'event': 'owner-text-deferred-saved', 'deferredCount': len(self.deferred), 'recoveryFile': relative})

    def route(self, event, *, remaining=()):
        if self.stopped:
            self._block([*self.deferred, event, *remaining])
        try:
            result = self.controller.route_event(event)
        except Exception:
            self._block([*self.deferred, event, *remaining])
        action = result['action']
        if action == 'pass':
            self.publish(result['event'])
        elif action == 'deferred':
            self.deferred.append(copy.deepcopy(event))
        elif action == 'hint':
            self.emit({'event': 'native-owner-text-observed', 'id': event['id'], 'target': event['target'],
                       'kind': event['kind'], 'authorityEpoch': result['hint']['authorityEpoch']})
        else:
            self._block([*self.deferred, result.get('event', event), *remaining])

    def route_batch(self, events):
        for index, event in enumerate(events):
            self.route(event, remaining=events[index + 1:])

    def command(self, command, *, dispatcher, delivery_active=False, independent=False):
        identifier = command.get('id') if type(command) is dict else None
        safe_identifier = identifier if isinstance(identifier, str) and 0 < len(identifier) <= 256 else None
        result = {'event': 'owner-text-result', 'id': safe_identifier, 'ok': False}
        try:
            op = command.get('op')
            extra = {'owner-text-status': set(), 'owner-text-prepare': set(), 'owner-text-abort': {'ticket'},
                'owner-text-commit': {'ticket', 'authorityEpoch'}, 'owner-text-decision': {'claimId', 'authorityEpoch'}}
            if (op not in extra or set(command) != {'op', 'id', 'target'} | extra[op]
                    or command['target'] != self.config['target'] or not isinstance(identifier, str)
                    or not identifier or len(identifier) > 256):
                raise RuntimeError('request-invalid')
            for key in extra[op]:
                if not isinstance(command[key], str) or not re.fullmatch('[0-9a-f]{32}', command[key]):
                    raise RuntimeError('request-invalid')
            if self.stopped and op != 'owner-text-status':
                raise RuntimeError('blocked')
            if op in ('owner-text-prepare', 'owner-text-commit') and (delivery_active or independent):
                raise RuntimeError('worker-busy')
            if op == 'owner-text-status':
                value = self.status()
            elif op == 'owner-text-prepare':
                self.controller.prepare()
                value = self.status()
            elif op == 'owner-text-abort':
                aborted = self.controller.abort(command['ticket'])
                events, self.deferred = aborted['events'], []
                for index, event in enumerate(events):
                    try:
                        self.publish(event)
                    except Exception:
                        self._block(events[index:])
                value = {**self.status(), 'drained': len(events)}
            elif op == 'owner-text-commit':
                self.controller.commit(command['ticket'], command['authorityEpoch'])
                value = self.status()
            else:
                if dispatcher is None:
                    raise RuntimeError('social-unavailable')
                value = apply_native_owner_decision(dispatcher, self.config, command['claimId'], command['authorityEpoch'],
                    active_authority_provider=self.active_authority)
            result.update(ok=True, value=value)
        except WorkerOwnerTextFatal:
            raise
        except Exception as error:
            if self.controller.status()['mode'] == 'blocked':
                self._block(self.deferred)
            # Fixed codes only; never serialize paths, exception messages or text.
            known = {'blocked', 'request-invalid', 'worker-busy', 'social-unavailable'}
            code = str(error) if type(error) is RuntimeError and str(error) in known else 'operation-denied'
            result['code'] = 'owner-text-' + code
        self.emit(result)
        return result
