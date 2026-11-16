"""Version/acceptance gate for observed wxauto social-action implementations.

Installed wxauto4 41.1.7 lacks the public social APIs. This module intentionally
does not invent 4.2.0 selectors. Bindings must be supplied after actual observation
and live acceptance; an empty binding set is an unavailable real backend.
"""
from dataclasses import dataclass
import hashlib
import importlib.metadata
import inspect
import re
import threading
import importlib
import marshal
import os
import uuid
import json
from pathlib import Path
import time

from attachment_policy import local_path
from data_paths import absolute_data_path, resolve_data_paths
from social_actions import ACTIONS, REQUIRES_APPROVAL, ActionContext, SocialActions, _digest

METHODS = {'publish_moment': 'PublishMoment', 'set_group_announcement': 'SetGroupAnnouncement',
           'create_group': 'CreateGroup', 'add_friend': 'AddNewFriend', 'accept_friend': 'GetNewFriends'}


def _sha256(file):
    result = hashlib.sha256()
    with file.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def installed_interfaces():
    """Inspect classes only; never construct or connect a WeChat instance."""
    from wxauto4.wx import WeChat
    result = {'packageVersion': importlib.metadata.version('wxauto4'), 'methods': {}}
    for action, name in METHODS.items():
        method = getattr(WeChat, name, None)
        result['methods'][action] = {'method': name, 'available': callable(method),
            'signature': str(inspect.signature(method)) if callable(method) else None}
    return result


def running_client(wx):
    """Read the executable of the process owning the already-bound wxauto UI."""
    import psutil
    import win32api
    pid = int(wx._api.control.ProcessId)
    process = psutil.Process(pid)
    exe = local_path(process.exe())
    if exe.name.lower() != 'weixin.exe' or not exe.is_file():
        raise RuntimeError('social-client-executable-invalid')
    before = exe.stat()
    info = win32api.GetFileVersionInfo(str(exe), '\\')
    version = '.'.join(map(str, [info['FileVersionMS'] >> 16, info['FileVersionMS'] & 65535,
                                info['FileVersionLS'] >> 16, info['FileVersionLS'] & 65535]))
    digest = _sha256(exe)
    after = exe.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError('social-client-executable-changed')
    return {'pid': pid, 'executable': str(exe), 'executableName': exe.name,
            'clientVersion': version, 'clientSha256': digest}


@dataclass(frozen=True)
class ActionBinding:
    implementation_id: str
    perform: object
    observe_before: object
    observe_after: object
    read_announcement: object = None
    bind_plan: object = None
    cleanup: object = None
    source_dependencies: tuple = ()

    def fingerprint(self):
        # Source changes invalidate a receipt; compiled/opaque callbacks cannot
        # become live-verified merely by assigning a friendly implementation id.
        chunks = [self.implementation_id]
        file_hashes = {}
        for callback in (self.perform, self.observe_before, self.observe_after, self.read_announcement,
                         self.bind_plan,self.cleanup,*self.source_dependencies):
            if callback is not None:
                function = callback.__func__ if inspect.ismethod(callback) else callback
                code = getattr(function, '__code__', None)
                if code is None:
                    raise TypeError('social-binding-loaded-code-unavailable')
                # inspect.getsource/file read the current disk contents. A live
                # process may still hold an older callback after a file update;
                # its receipt must identify the code that actually executes too.
                # Version 2 avoids reference-count-dependent object-ref flags
                # in newer marshal encodings; reading a binding must not alter
                # its fingerprint when callback constants acquire references.
                chunks.append(hashlib.sha256(marshal.dumps(code, 2)).hexdigest())
                chunks.append(inspect.getsource(callback))
                module_file = Path(inspect.getfile(callback))
                if module_file not in file_hashes:file_hashes[module_file]=_sha256(module_file)
                chunks.append(file_hashes[module_file])
        return hashlib.sha256('\n'.join(chunks).encode('utf8')).hexdigest()


class WxautoSocialBackend:
    def __init__(self, wx, config, *, bindings=None, client_probe=running_client, clock=time.time, refresh_chat=None):
        self.wx = wx
        self.config = config
        self.ui_thread_id = threading.get_ident()
        self.paths = resolve_data_paths(config)
        self.root = self.paths['root']
        self.receipt_file = self.paths['config'] / 'social-live-acceptance.json'
        self.owner = config['target']
        self.groups = frozenset(config.get('groups', ()))
        self.bindings = dict(bindings or {})
        self.client_probe, self.clock = client_probe, clock
        self.plans = SocialActions(self.root, owner_target=self.owner, allowed_groups=self.groups, clock=clock)
        self.refresh_chat = refresh_chat
        self.chat_binding_failed = False
        self.last_chat_recovery = None
        self._navigation_context = None

    def _lookup_plan(self, plan_id, plan_hash):
        if (not isinstance(plan_id,str) or not re.fullmatch('[a-f0-9]{32}',plan_id)
                or not isinstance(plan_hash,str) or not re.fullmatch('[a-f0-9]{64}',plan_hash)):
            raise RuntimeError('social-observe-plan-invalid')
        plan=self.plans.inspect(plan_id,ActionContext('friend',self.owner,True))
        terminal_read_only=(plan.get('status') in ('failed','unknown','confirmed')
            and plan.get('approvedBy')==self.owner and isinstance(plan.get('approvalMessageId'),str)
            and bool(plan['approvalMessageId'].strip()))
        if (plan.get('hash')!=plan_hash or plan.get('action')!='add_friend'
                or (plan.get('status') not in ('pending','approved') and not terminal_read_only) or self.clock()>=plan['expiresAt']
                or plan.get('origin')!={'kind':'friend','target':self.owner}):
            raise RuntimeError('social-observe-plan-unavailable')
        from social_friend_search import valid_search_query
        if not valid_search_query(plan['parameters'].get('keywords')):
            raise RuntimeError('social-observe-query-invalid')
        return plan

    def observe(self, purpose, *, plan_id=None, plan_hash=None):
        if purpose not in ('friend-search-owner','friend-search-plan','moments-feed-metadata'):raise RuntimeError('social-observe-purpose-denied')
        if threading.get_ident()!=self.ui_thread_id:raise RuntimeError('social-observe-ui-thread-required')
        if self.chat_binding_failed or self._navigation_context is not None:
            raise RuntimeError('social-observe-navigation-unavailable')
        if purpose=='moments-feed-metadata':
            if plan_id is not None or plan_hash is not None:raise RuntimeError('social-observe-plan-invalid')
            from social_moments_runtime import observe_feed
            return observe_feed(self)
        from social_friend_observe import observe_owner,observe_plan
        refresh=self.refresh_chat
        if refresh is None:
            from wxauto_connection import refresh_chat_binding
            refresh=refresh_chat_binding
        self._navigation_context={'observation':True}
        self.last_chat_recovery=None
        try:
            kwargs={'client':self._client(),'client_probe':self.client_probe,'refresh_chat':refresh}
            if purpose=='friend-search-plan':
                plan=self._lookup_plan(plan_id,plan_hash)
                result=observe_plan(self.wx,self.config,self.groups,plan=plan,
                    plan_check=lambda:self._lookup_plan(plan_id,plan_hash),**kwargs)
            else:
                if plan_id is not None or plan_hash is not None:raise RuntimeError('social-observe-plan-invalid')
                result=observe_owner(self.wx,self.config,self.groups,**kwargs)
            self.last_chat_recovery={'restored':result.get('chatRestored') is True,'usedOwnerFallback':False}
            return result
        except Exception as error:
            code=str(error) if isinstance(error,RuntimeError) else ''
            if code=='social-chat-restore-failed':
                self.chat_binding_failed=True
                raise
            if re.fullmatch(r'probe-[a-z0-9-]{1,100}',code):
                raise RuntimeError('social-observation-'+code.removeprefix('probe-')) from None
            if re.fullmatch(r'(?:contact|friend-search)-[a-z0-9-]{1,100}',code):
                raise RuntimeError('social-observation-'+code) from None
            if re.fullmatch(r'social-observe-[a-z0-9-]{1,100}',code):
                raise
            raise RuntimeError('social-observation-unavailable') from None
        finally:
            self._navigation_context=None

    def sync_group_routes(self, active_groups):
        """Private serialized worker hook; does not enable any action binding."""
        from social_ipc import _validated_group_routes
        groups=_validated_group_routes(active_groups,self.owner)
        if self._navigation_context is not None:
            raise RuntimeError('social-group-route-sync-during-navigation')
        self.groups=groups
        self.plans.groups=groups

    def accept_friend_initial(self, *, plan_id, plan_hash):
        # Explicit private maintenance operation, never a normal live binding.
        from social_friend_acceptance import accept_friend_initial
        return accept_friend_initial(self, plan_id=plan_id, plan_hash=plan_hash)

    def accept_friend_request(self, *, plan_id, plan_hash):
        from social_friend_request_acceptance import accept_friend_request
        return accept_friend_request(self,plan_id=plan_id,plan_hash=plan_hash)

    def _start_navigation(self):
        context = self._navigation_context
        if context is None:
            raise RuntimeError('social-navigation-context-required')
        if self.chat_binding_failed:
            raise RuntimeError('social-chat-restore-failed')
        if context.get('started'):
            return
        target, kind, fallback = self.owner, 'friend', True
        try:
            info = self.wx.ChatInfo()
            name, chat_kind = info.get('chat_name'), info.get('chat_type')
            if ((name == self.owner and chat_kind == 'friend')
                    or (name in self.groups and chat_kind == 'group')):
                target, kind, fallback = name, chat_kind, False
        except Exception:
            pass  # Stale/non-chat page: only the fixed allowlisted owner fallback.
        context.update(started=True, target=target, kind=kind, fallback=fallback)

    def _with_chat_recovery(self, callback):
        if self._navigation_context is not None:
            return callback()
        context = {}
        self._navigation_context = context
        self.last_chat_recovery = None
        result = None
        try:
            result = callback()
            return result
        finally:
            self._navigation_context = None
            if context.get('started'):
                recovery = {'restored': False, 'targetKind': context['kind'],
                            'usedOwnerFallback': context['fallback']}
                try:
                    if self.chat_binding_failed:
                        raise RuntimeError('social-chat-restore-failed')
                    refresh = self.refresh_chat
                    if refresh is None:
                        from wxauto_connection import refresh_chat_binding
                        refresh = refresh_chat_binding
                    refresh(self.wx, context['target'], context['kind'])
                    recovery['restored'] = True
                except Exception:
                    self.chat_binding_failed = True
                    recovery['code'] = 'social-chat-restore-failed'
                self.last_chat_recovery = recovery
                if isinstance(result, dict):
                    result['chatRecovery'] = copy_parameters(recovery)

    def _client(self):
        value = self.client_probe(self.wx)
        if (not isinstance(value, dict) or value.get('executableName', '').lower() != 'weixin.exe'
                or not isinstance(value.get('clientVersion'), str) or not value['clientVersion']
                or not isinstance(value.get('clientSha256'), str) or len(value['clientSha256']) != 64):
            raise RuntimeError('social-client-unverified')
        return value

    def _receipt(self, client):
        file = local_path(self.receipt_file)
        if not file.is_file() or file.stat().st_size > 1024 * 1024:
            return None
        value = json.loads(file.read_text('utf-8-sig'))
        if (not isinstance(value, dict) or value.get('format') != 'whale-social-live-acceptance'
                or value.get('version') != 1 or value.get('clientVersion') != client['clientVersion']
                or value.get('clientSha256') != client['clientSha256'] or not isinstance(value.get('actions'), dict)):
            return None
        return value

    def _artifact(self, entry, action, phase, client):
        try:
            file = absolute_data_path(entry['path'], root=self.root)
            if not file.is_file() or not 0 < file.stat().st_size <= 2 * 1024 * 1024 or _sha256(file) != entry['sha256']:
                return False
            data = json.loads(file.read_text('utf-8-sig'))
            return (data.get('source') == 'wxauto-uia' and data.get('action') == action
                    and data.get('phase') == phase and data.get('clientVersion') == client['clientVersion']
                    and isinstance(data.get('controls'), list) and bool(data['controls'])
                    and (phase != 'after' or data.get('confirmed') is True))
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            return False

    def _verified(self, action, client, receipt):
        binding = self.bindings.get(action)
        if action not in ACTIONS or not isinstance(binding, ActionBinding) or receipt is None:
            return False
        if not all(callable(callback) for callback in (binding.perform, binding.observe_before, binding.observe_after)):
            return False
        if action == 'set_group_announcement' and not callable(binding.read_announcement):
            return False
        entry = receipt['actions'].get(action, {})
        try:
            return (entry.get('status') == 'passed' and entry.get('bindingFingerprint') == binding.fingerprint()
                    and self._artifact(entry['before'], action, 'before', client)
                    and self._artifact(entry['after'], action, 'after', client))
        except (KeyError, TypeError, OSError):
            return False

    def capabilities(self):
        if self.chat_binding_failed:
            return {'clientVersion': None, 'actions': {}, 'reason': 'social-chat-restore-failed'}
        try:
            client = self._client()
            receipt = self._receipt(client)
            actions = {action: {'clientVersion': client['clientVersion'],
                                'liveVerified': self._verified(action, client, receipt)} for action in ACTIONS}
            return {'clientVersion': client['clientVersion'], 'clientSha256': client['clientSha256'], 'actions': actions}
        except Exception:
            return {'clientVersion': None, 'actions': {}, 'reason': 'social-client-or-acceptance-unverified'}

    def _gate(self, action):
        if self.chat_binding_failed:
            raise RuntimeError('social-chat-restore-failed')
        client = self._client()
        if not self._verified(action, client, self._receipt(client)):
            raise RuntimeError('social-backend-unverified')
        return client

    def _group(self, group):
        if group not in self.groups:
            raise RuntimeError('social-group-denied')
        self._start_navigation()
        self.wx.ChatWith(group, exact=True)
        for _ in range(2):
            info = self.wx.ChatInfo()
            if info.get('chat_name') != group or info.get('chat_type') != 'group':
                raise RuntimeError('social-target-mismatch')

    def _evidence(self, value, action, parameters, client, phase, minimum_time):
        if (not isinstance(value, dict) or value.get('source') != 'wxauto-uia'
                or value.get('action') != action or value.get('phase') != phase
                or value.get('clientVersion') != client['clientVersion']
                or value.get('parametersHash') != _digest(action, parameters)
                or not isinstance(value.get('controls'), list) or not value['controls']):
            return False
        observed = value.get('observedAt')
        if not isinstance(observed, (int, float)) or not minimum_time <= observed <= self.clock() + 5:
            return False
        if action == 'set_group_announcement' and value.get('target') != parameters['group']:
            return False
        return phase != 'after' or value.get('confirmed') is True

    def get_group_announcement(self, group):
        return self._with_chat_recovery(lambda: self._read_group_announcement(
            group, self._gate('set_group_announcement')))

    def _read_group_announcement(self, group, client):
        return self._with_chat_recovery(lambda: self._read_group_announcement_core(group, client))

    def _read_group_announcement_core(self, group, client):
        self._group(group)
        started = self.clock()
        value = self.bindings['set_group_announcement'].read_announcement(self.wx, group)
        if (not isinstance(value, dict) or value.get('verified') is not True or value.get('target') != group
                or not isinstance(value.get('text'), str) or value.get('source') != 'wxauto-uia'
                or value.get('clientVersion') != client['clientVersion']
                or not isinstance(value.get('observedAt'), (int, float))
                or not started <= value['observedAt'] <= self.clock() + 5
                or not isinstance(value.get('controls'), list) or not value['controls']):
            return {'text': '', 'verified': False, 'clientVersion': client['clientVersion']}
        return {**value, 'clientVersion': client['clientVersion']}

    def execute(self, action, parameters, *, idempotency_key, expected_previous_announcement=None):
        return self._execute(action, parameters, idempotency_key=idempotency_key,
                             expected_previous_announcement=expected_previous_announcement)

    def _execute(self, action, parameters, *, idempotency_key, expected_previous_announcement=None, acceptance=None):
        return self._with_chat_recovery(lambda: self._execute_core(action, parameters,
            idempotency_key=idempotency_key, expected_previous_announcement=expected_previous_announcement,
            acceptance=acceptance))

    def _execute_core(self, action, parameters, *, idempotency_key, expected_previous_announcement=None, acceptance=None):
        lifecycle={};result=None
        try:
            result=self._execute_core_operation(action,parameters,idempotency_key=idempotency_key,
                expected_previous_announcement=expected_previous_announcement,acceptance=acceptance,lifecycle=lifecycle)
            return result
        finally:
            binding=lifecycle.get('binding')
            if isinstance(binding,ActionBinding) and binding.cleanup is not None:
                try:binding.cleanup()
                except Exception:
                    # Preserve durable external outcome, stop using the UI, and
                    # never turn cleanup failure into a retry of the action.
                    self.chat_binding_failed=True
                    if isinstance(result,dict):result['cleanupFailureCode']='social-chat-restore-failed'

    def _execute_core_operation(self, action, parameters, *, idempotency_key, expected_previous_announcement=None, acceptance=None,lifecycle):
        def gate():
            if self.chat_binding_failed:
                raise RuntimeError('social-chat-restore-failed')
            if acceptance is None:
                return self._gate(action)
            client = self._client()
            binding = self.bindings.get(action)
            if (not isinstance(binding, ActionBinding) or acceptance !=
                    (action, binding.fingerprint(), client['clientSha256'])):
                raise RuntimeError('social-acceptance-binding-changed')
            return client
        try:
            client = gate()
            plan = self.plans.inspect(idempotency_key, ActionContext('friend', self.owner, True))
            if (plan['status'] != 'claimed' or plan['action'] != action or plan['hash'] != _digest(action, parameters)
                    or plan['parameters'] != parameters or self.clock() >= plan['expiresAt']):
                return {'status': 'failed', 'code': 'social-backend-plan-mismatch'}
            if action in REQUIRES_APPROVAL and (plan.get('approvedBy') != self.owner or not plan.get('approvalMessageId')):
                return {'status': 'failed', 'code': 'social-backend-approval-required'}
            template=self.bindings[action]
            binding=template
            if template.bind_plan is not None:
                if (plan.get('origin')!={'kind':'friend','target':self.owner}
                        or plan.get('approvedBy')!=self.owner
                        or not isinstance(plan.get('approvalMessageId'),str) or not plan['approvalMessageId'].strip()):
                    return {'status':'failed','code':'social-backend-approval-required'}
                if not callable(template.bind_plan):raise RuntimeError('social-plan-binding-invalid')
                binding=template.bind_plan(copy_parameters(plan))
                if not isinstance(binding,ActionBinding):raise RuntimeError('social-plan-binding-invalid')
                lifecycle['binding']=binding
                if binding.fingerprint()!=template.fingerprint():raise RuntimeError('social-plan-binding-changed')
            if binding.cleanup is not None:lifecycle['binding']=binding
            if action == 'set_group_announcement':
                self._group(parameters['group'])
                old = (self.get_group_announcement(parameters['group']) if acceptance is None
                       else self._read_group_announcement(parameters['group'], client))
                if not old.get('verified') or old['text'] != expected_previous_announcement:
                    return {'status': 'failed', 'code': 'social-announcement-changed'}
            started = self.clock()
            self._start_navigation()
            before = binding.observe_before(self.wx, copy_parameters(parameters))
            if not self._evidence(before, action, parameters, client, 'before', started):
                return {'status': 'failed', 'code': 'social-target-or-parameters-unverified'}
            # Changing the actual executable, receipt or binding invalidates the
            # action even after a navigation/preflight stage has succeeded.
            if gate()['clientSha256'] != client['clientSha256']:
                return {'status': 'failed', 'code': 'social-client-changed'}
            if self.clock() >= plan['expiresAt']:
                return {'status': 'failed', 'code': 'social-authorization-expired'}
        except Exception as error:
            return {'status': 'failed', 'code': str(error) if isinstance(error, RuntimeError) and str(error).startswith('social-') else 'social-backend-preflight-failed'}
        try:
            result = binding.perform(self.wx, copy_parameters(parameters))
            after = binding.observe_after(self.wx, copy_parameters(parameters), result, before)
            if self._evidence(after, action, parameters, client, 'after', started):
                return {'status': 'confirmed', 'receipt': {'clientVersion': client['clientVersion'],
                    'clientSha256': client['clientSha256'], 'bindingFingerprint': binding.fingerprint(), 'observation': after, 'beforeObservation': before}}
            return {'status': 'unknown', 'code': 'social-post-action-evidence-missing'}
        except Exception:
            return {'status': 'unknown', 'code': 'social-post-action-outcome-unknown'}


def copy_parameters(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def load_observed_bindings(wx, config):
    """Only locally observed, source-reviewable implementations; absence is normal."""
    try:
        module = importlib.import_module('social_observed_bindings')
    except ModuleNotFoundError as error:
        if error.name == 'social_observed_bindings':
            return {}
        raise
    bindings = module.load_bindings(wx, config)
    if not isinstance(bindings, dict) or any(key not in ACTIONS or not isinstance(value, ActionBinding)
                                             for key, value in bindings.items()):
        raise RuntimeError('social-observed-bindings-invalid')
    return bindings


def _atomic_json(file, value):
    file = local_path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf8') as output:
            json.dump(value, output, ensure_ascii=False, allow_nan=False)
            output.write('\n'); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, file)
    finally:
        temporary.unlink(missing_ok=True)


def run_local_acceptance(backend, *, plan_id, expected_hash, action, fingerprint):
    """Local acceptance core; never used by normal capabilities()/execution.

    The CLI owns its explicit action/fingerprint. The separate owner-only
    initial-contact IPC adapter fixes both server-side and cannot certify after.

    Claim before any navigation. Only successful actual observations create the
    first receipt. Pending approvals and terminal/unknown plans cannot run.
    """
    binding = backend.bindings.get(action)
    if not isinstance(binding, ActionBinding) or binding.fingerprint() != fingerprint:
        raise RuntimeError('social-acceptance-binding-mismatch')
    if not all(callable(c) for c in (binding.perform, binding.observe_before, binding.observe_after)):
        raise RuntimeError('social-acceptance-binding-incomplete')
    if action == 'set_group_announcement' and not callable(binding.read_announcement):
        raise RuntimeError('social-acceptance-binding-incomplete')
    client = backend._client()
    context = ActionContext('friend', backend.owner, True)
    queue = backend.plans
    queue._require_hash(expected_hash)
    with queue._locked() as state:
        plan = queue._plan(state, plan_id, expected_hash)
        queue._authorize(plan['action'], plan['parameters'], context, execution=True)
        if plan['action'] != action:
            raise RuntimeError('social-acceptance-action-mismatch')
        if queue._expired(state, plan) or plan['status'] not in ('ready', 'approved'):
            return {**copy_parameters(plan), 'executionStatus': 'not-dispatched'}
        if action in REQUIRES_APPROVAL and (plan.get('approvedBy') != backend.owner or not plan.get('approvalMessageId')):
            raise RuntimeError('social-backend-approval-required')
        plan.update(status='claimed', claimedAt=backend.clock(), clientVersion=client['clientVersion'],
                    localAcceptance=True, acceptanceFingerprint=fingerprint)
        queue._save(state)
        parameters = copy_parameters(plan['parameters'])
    try:
        previous = None
        if action == 'set_group_announcement':
            old = backend._read_group_announcement(parameters['group'], client)
            if not old.get('verified'):
                queue._finish(plan_id, status='failed', resultCode='announcement-backup-unverified')
                return queue.inspect(plan_id, context)
            previous = old['text']
            with queue._locked() as state:
                record = queue._plan(state, plan_id, expected_hash)
                record['previousAnnouncement'] = {'group': parameters['group'], 'text': previous,
                    'sha256': hashlib.sha256(previous.encode('utf8')).hexdigest(), 'savedAt': backend.clock()}
                queue._save(state)
        result = backend._execute(action, parameters, idempotency_key=plan_id,
            expected_previous_announcement=previous, acceptance=(action, fingerprint, client['clientSha256']))
        if result.get('status') != 'confirmed':
            queue._finish(plan_id, status='failed' if result.get('status') == 'failed' else 'unknown',
                          resultCode=result.get('code', 'social-acceptance-outcome-unknown'))
            return queue.inspect(plan_id, context)
        receipt = result['receipt']
        recovery=result.get('chatRecovery')
        if (backend.chat_binding_failed or result.get('cleanupFailureCode') is not None
                or not isinstance(recovery,dict) or recovery.get('restored') is not True):
            # The request may already be submitted. Preserve that terminal
            # fact, but incomplete recovery cannot certify normal availability.
            queue._finish(plan_id,status='confirmed',receipt=receipt,acceptancePromoted=False,
                acceptanceCode='social-acceptance-chat-recovery-unverified')
            return queue.inspect(plan_id,context)
        directory = backend.paths['backups'] / 'social-acceptance' / plan_id
        entry = {'status': 'passed', 'bindingFingerprint': fingerprint, 'planId': plan_id,
                 'parametersHash': expected_hash}
        for phase, observation in [('before', receipt['beforeObservation']), ('after', receipt['observation'])]:
            artifact = directory / (phase + '.json')
            _atomic_json(artifact, observation)
            entry[phase] = {'path': artifact.relative_to(backend.root).as_posix(), 'sha256': _sha256(artifact)}
        # Recheck after observation and before promoting runtime availability.
        if backend._client()['clientSha256'] != client['clientSha256'] or binding.fingerprint() != fingerprint:
            raise RuntimeError('social-acceptance-environment-changed')
        value = backend._receipt(client) or {'format': 'whale-social-live-acceptance', 'version': 1,
            'clientVersion': client['clientVersion'], 'clientSha256': client['clientSha256'], 'actions': {}}
        value['actions'][action] = entry
        _atomic_json(backend.receipt_file, value)
        queue._finish(plan_id, status='confirmed', receipt=receipt,acceptancePromoted=True)
    except Exception:
        queue._finish(plan_id, status='unknown', resultCode='social-acceptance-outcome-unknown')
    return queue.inspect(plan_id, context)
