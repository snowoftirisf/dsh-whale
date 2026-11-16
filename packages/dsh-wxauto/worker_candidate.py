"""Allowlisted private/group chats; wxauto only; private JSON-lines IPC."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import uuid
from connection_policy import connection_failure, error_diagnostic, ChatReadBackoff
from media_policy import meme_file
from delivery_window import DeliveryWindow
from attachment_policy import attachment_file
from attachment_registry import AttachmentRegistry, chat_key
from data_paths import resolve_data_paths
from worker_owner_text import WorkerOwnerTextBridge, WorkerOwnerTextFatal, OPS as OWNER_TEXT_OPS
from worker_group_text_candidate import WorkerGroupTextBridge, OPS as GROUP_TEXT_OPS

OUT = sys.stdout

def emit(value):
    OUT.write(json.dumps(value, ensure_ascii=False) + '\n')
    OUT.flush()

def sdk_result_diagnostic(result):
    """Describe a non-confirming SDK return without serializing its contents."""
    kind = type(result).__name__
    facts = {'type': kind if kind.isidentifier() and len(kind) <= 64 else 'other'}
    if isinstance(result, dict):
        status = dict.get(result, 'status')
        known = {'成功': 'success', '失败': 'failure', '错误': 'error',
                 'success': 'success', 'failure': 'failure', 'error': 'error'}
        if isinstance(status, str) and status in known:
            facts['knownStatus'] = known[status]
        message = dict.get(result, 'message')
        if isinstance(message, str):
            facts['messageLength'] = len(message)
            facts['messageSha256'] = hashlib.sha256(message.encode('utf-8')).hexdigest()
    return facts

def main():
    try:
        with contextlib.ExitStack() as shutdown:
            return _main(shutdown)
    except WorkerOwnerTextFatal:
        emit({'event':'fatal','code':'owner-text-shutdown-unverified'})


def _main(shutdown):
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
    target = config['target']
    routes = {target: 'friend', **{group: 'group' for group in config.get('groups', [])}}
    fixed_attachment_routes=frozenset(routes)
    state_path = Path(config['statePath'])
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        state = {'claims': []}
    claims = set(state.get('claims', []))
    from social_ipc import SocialDispatcher, OPS as SOCIAL_OPS
    social = SocialDispatcher(config) if config.get('dataRoot') else None
    owner_text = WorkerOwnerTextBridge(config, publish=lambda event: publish_event(event), emit=emit) if config.get('dataRoot') else None
    if owner_text:
        shutdown.callback(owner_text.close)
    if owner_text and owner_text.stopped:
        emit({'event':'connection-blocked','code':'owner-text-authority-restore-blocked','phase':'owner-text-authority'})
        return
    group_text = WorkerGroupTextBridge(config,publish=lambda event: route_owner_batch([event]),
        publish_batch=lambda events: route_owner_batch(events),emit=emit) if config.get('dataRoot') else None
    if group_text:shutdown.callback(group_text.close)
    group_text_diagnostics=set()
    attachment_registry = None
    attachment_directory = None
    data_paths = None
    attachment_setup_error = None
    if config.get('receiveFiles'):
        try:
            data_paths = resolve_data_paths(config)
            attachment_directory = config.get('attachmentDirectory') or str(data_paths['media'] / 'attachments')
            attachment_registry = AttachmentRegistry(data_paths['config'] / 'attachment-receipts.local.json',
                data_paths['root'], attachment_directory, routes)
        except Exception as error:
            attachment_directory = None
            attachment_setup_error = error_diagnostic(error, 'initialize-file-receive')

    def claim(key):
        if key in claims:
            return False
        state['claims'] = (state.get('claims', []) + [key])[-2000:]
        claims.clear()
        claims.update(state['claims'])
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state), encoding='utf-8')
        os.replace(temporary, state_path)
        return True

    # Keep library banners out of the IPC channel. Do not write chat content to logs.
    phase = 'import-library'
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from wxauto_connection import connect
            phase = 'initialize'
            wx = connect()
            if social:
                from social_wxauto_backend import WxautoSocialBackend, load_observed_bindings
                social.backend = WxautoSocialBackend(wx, config, bindings=load_observed_bindings(wx, config))
            phase = 'select-target'
            from message_watch import MainWindowMonitor, event_key
            import message_watch as message_watch_module
            image_directory = config.get('imageDirectory') if config.get('receiveImages', False) else None
            from native_voice import NativeVoice
            native_data = Path(config['dataRoot']) / 'voice' if config.get('dataRoot') else Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2]) / 'voice-data'
            native_config_path = native_data / 'native-wechat.local.json'
            native_config = json.loads(native_config_path.read_text('utf-8-sig')) if native_config_path.exists() else {}
            if config.get('dataRoot'):
                native_config['dataRoot'] = config['dataRoot']
            native_voice = NativeVoice(wx, native_config)
            monitor_options = dict(receive_voice=config.get('receiveVoice', True), image_directory=image_directory,
                attachment_directory=attachment_directory, data_root=data_paths['root'] if data_paths else None)
            if group_text:monitor_options['native_group_text_authority']=group_text.monitor_authority
            native_source_config=config.get('nativeVoiceSources')
            independent_requested=isinstance(config.get('independentWindows'),dict) and config['independentWindows'].get('enabled') is True
            native_voice_observation_enabled=(isinstance(native_source_config,dict)
                and native_source_config.get('enabled') is True
                and os.environ.get('WHALE_NATIVE_VOICE_READY')=='1' and not independent_requested)
            if native_voice_observation_enabled:
                monitor_options['native_voice_sources']=True
            window_selection = None
            if isinstance(config.get('independentWindows'), dict) and config['independentWindows'].get('enabled') is True:
                from independent_windows import create_window_monitor
                from social_wxauto_backend import running_client
                import importlib.metadata
                receipt_path = (data_paths['config'] if data_paths else state_path.parent) / 'independent-live-acceptance.json'
                try:
                    receipt = json.loads(receipt_path.read_text('utf-8-sig'))
                except (OSError, ValueError):
                    receipt = None
                try:
                    current_version = running_client(wx)['clientVersion']
                    wxauto_version = importlib.metadata.version('wxauto4')
                except Exception:
                    current_version = wxauto_version = None
                window_selection = create_window_monitor(wx, routes, config=config['independentWindows'],
                    receipt=receipt, current_version=current_version, wxauto_version=wxauto_version,
                    require_native_voice=native_voice.ready(), **monitor_options)
                monitor = window_selection.initialize()
            else:
                monitor = MainWindowMonitor(wx, routes, **monitor_options)
                monitor.initialize()
    except Exception as error:
        # Constructor is NOT a passive online check: it can activate/click UI.
        # Never repeat it automatically after an arbitrary lookup failure.
        emit({**connection_failure(error, phase), 'diagnostic': error_diagnostic(error, phase)})
        return

    # Temporary, explicitly local diagnostics. No extra mouse/keyboard actions.
    avatar_trace_enabled=False
    try:
        from avatar_input_trace import install_configured_trace
        from wxauto4 import uia
        avatar_trace_enabled=install_configured_trace(uia,wx._api.control,
            lambda value:emit({'event':'avatar-input-trace','value':value}))
    except Exception:
        pass  # A diagnostic failure must never change receive/send behavior.

    incoming = queue.Queue()
    def read_commands():
        for line in sys.stdin:
            try:
                incoming.put(json.loads(line))
            except ValueError:
                emit({'event': 'error', 'code': 'invalid-command'})
        incoming.put({'op': 'stop'})
    threading.Thread(target=read_commands, daemon=True).start()

    seen = {name: set() for name in routes}
    auto_group_registry={}
    auto_group_suspended=set()
    def sync_active_social_groups():
        if social is None:return True
        groups=frozenset(name for name,kind in routes.items() if kind=='group'
            and name not in auto_group_suspended
            and (name in fixed_attachment_routes or name in auto_group_registry))
        try:
            social.sync_group_routes(groups)
            return True
        except Exception:
            # Never acknowledge a route mutation while any social scope may
            # still be stale. Stop this serialized worker; no action is retried.
            emit({'event':'connection-blocked','code':'social-group-route-sync-failed',
                  'phase':'social-group-route-sync'})
            return False
    if not sync_active_social_groups():return
    known_session_names=set()
    if config.get('autoGroups',{}).get('enabled') is True:
        try:
            with contextlib.redirect_stdout(sys.stderr):known_session_names={row.name for row in wx.GetSession()}
        except Exception:pass
    retries = ChatReadBackoff()
    delivery = DeliveryWindow()
    run_id = str(uuid.uuid4())
    last_heartbeat = time.monotonic()
    last_session_probe = last_heartbeat
    poll_count = 0

    def emit_due_heartbeat():
        # This is main-loop progress, not proof that receive polling advanced.
        # Never call wxauto from here or add a timer thread to mask a stuck SDK call.
        nonlocal last_heartbeat
        current = time.monotonic()
        if current - last_heartbeat < 10:
            return
        deferred = vars(monitor).get('deferred_direction') if hasattr(monitor, '__dict__') else None
        deferred_counts = {name:len(rows) for name,rows in deferred.items()
                           if name in routes and type(rows) is dict} if type(deferred) is dict else {}
        size = getattr(incoming, 'qsize', None)
        queued = size() if callable(size) else 0  # Legacy isolated queue fixtures.
        if group_text:group_text.sync_routes()
        emit({'event':'heartbeat','polls':poll_count,'deliveryActive':delivery.active() is not None,
              'queuedCommands':queued if type(queued) is int and queued >= 0 else 0,
              'deferredDirectionRows':deferred_counts,
              'ownerTextAuthority':owner_text.status() if owner_text else {'version':1,'mode':'ui','reason':'data-root-unconfigured'},
              'groupTextAuthorities':group_text.status() if group_text else [],
              'groupTextWorkerInstanceId':group_text.instance_id if group_text else None,
              'recoveredTails':getattr(monitor,'recovered_tails',0),
              'retryingChats':[name for name in retries.states if name is not None],
              'windowMonitor':window_selection.status() if window_selection else {'mode':'main','reason':'independent-disabled'}})
        last_heartbeat = current

    def publish_event(event):
        if social:
            social_command = social.observe_incoming(event)
            if social_command:
                event['socialCommand'] = social_command
        emit(event)

    def route_owner_batch(events):
        if owner_text:owner_text.route_batch(events)
        else:
            for event in events:publish_event(event)

    def publish_incoming(name):
        records = monitor.read(name)
        events = []
        for record in records:
            key = event_key(run_id, name, record['id'])
            if key in seen[name]:
                continue
            if record.get('observation') in ('native-group-text','native-group-text-blocked'):
                blocked=record['observation']=='native-group-text-blocked'
                if blocked:
                    if key not in group_text_diagnostics:
                        group_text_diagnostics.add(key)
                        emit({'event':'native-group-text-blocked','id':key,'target':name,'kind':routes[name]})
                else:
                    proof=group_text.monitor_authority(name) if group_text else {}
                    if (proof.get('mode')!='native' or proof.get('nativeChatKey')!=record.get('nativeChatKey')
                            or proof.get('authorityEpoch')!=record.get('authorityEpoch')):
                        emit({'event':'native-group-text-blocked','id':key,'target':name,'kind':routes[name]})
                        continue
                    seen[name].add(key)
                    emit({'event':'native-group-text-observed','id':key,'target':name,'kind':routes[name],
                        'nativeChatKey':record['nativeChatKey'],'authorityEpoch':record['authorityEpoch']})
                continue
            if record.get('observation')=='native-voice':
                if native_voice_observation_enabled:
                    seen[name].add(key)
                    emit({'event':'native-voice-observed','id':key,'target':name,'kind':routes[name]})
                continue
            text = record['text']
            if not isinstance(text, str) or not text.strip():
                continue
            seen[name].add(key)
            files = record.get('files', [])
            if files and attachment_registry:
                try:
                    for file in files:
                        attachment_registry.register(name, file)
                except Exception as error:
                    # A file that failed durable registration cannot be offered
                    # to the model as an authorized resend attachment.
                    files = []
                    record = {**record, 'mediaError': 'attachment-registration-failed',
                        'mediaDiagnostic': error_diagnostic(error, 'register-incoming-file')}
                    text = '[收到一个微信附件，但保存接收记录失败；本次附件不可读取或发送。]'
            event = {'event': 'message', 'id': key, 'target': name, 'kind': routes[name], 'sender': record['sender'], 'text': text,
                  'images': record.get('images', []), 'files': files, 'mediaType': record.get('mediaType', 'text'), 'mediaError': record.get('mediaError'),
                  'mediaDiagnostic': record.get('mediaDiagnostic')}
            events.append(event)
        if group_text:group_text.route_batch(events)
        else:route_owner_batch(events)
        if len(seen[name]) > 10000:
            raise RuntimeError('message-dedup-capacity')

    emit({'event': 'ready', 'target': target, 'groups': list(config.get('groups', [])),
          'groupTextAuthorities':group_text.status() if group_text else [],
          'groupTextWorkerInstanceId':group_text.instance_id if group_text else None,
          'groupTextObservationVersion':1 if group_text else 0,
          'ownerTextAuthority':owner_text.status() if owner_text else {'version':1,'mode':'ui','reason':'data-root-unconfigured'},
          'capabilities': {'voiceReceive': config.get('receiveVoice', True), 'imageReceive': bool(image_directory),
                           'fileReceive': bool(attachment_registry), 'fileSend': bool(attachment_registry), 'nativeVoice': native_voice.ready(),
                           'nativeVoiceSourceObservationEnabled':native_voice_observation_enabled,
                           'nativeVoiceRouteVersion': 1,
                           'nativeVoiceRecorderMode':native_config.get('recorderMode','right-alt'),
                           'nativeVoiceKeyVersion': 1,
                           'nativeVoiceMenuVersion': 1,
                           'messageDirectionVersion': 4,'messageCursorSnapshotVersion':getattr(message_watch_module,'MESSAGE_CURSOR_SNAPSHOT_VERSION',0),'avatarInputTrace':avatar_trace_enabled,
                           'windowMonitor': window_selection.status() if window_selection else {'mode':'main','reason':'independent-disabled'}}})
    if attachment_setup_error:
        emit({'event': 'error', 'code': 'file-capability-disabled', 'diagnostic': attachment_setup_error})
    while True:
        # Every continue below returns here. An active reply or continuously
        # queued IPC may defer polling but must not suppress worker heartbeat.
        emit_due_heartbeat()
        try:
            command = incoming.get(timeout=1)
        except queue.Empty:
            command = None
        if command:
            if command.get('op') == 'stop':
                return
            command_id = command.get('id')
            destination = command.get('target')
            diagnostic_probe = command.get('op') == 'probe-native-voice'
            diagnostic_facts = {}
            sdk_result_facts = None
            rendered_file_evidence = None
            if command.get('op') in GROUP_TEXT_OPS:
                if group_text:
                    group_text.command(command,monitor=monitor,delivery_active=delivery.active() is not None,
                                       independent=window_selection is not None)
                else:emit({'event':'group-text-result','id':command_id,'ok':False,'code':'group-text-unavailable'})
                continue
            if command.get('op') in OWNER_TEXT_OPS:
                try:
                    if owner_text:
                        owner_text.command(command, dispatcher=social, delivery_active=delivery.active() is not None,
                                           independent=window_selection is not None)
                    else:
                        emit({'event':'owner-text-result','id':command_id,'ok':False,'code':'owner-text-unavailable'})
                except WorkerOwnerTextFatal:
                    return
                continue
            if command.get('op') in ('auto-group-register','auto-group-enable','auto-group-suspend'):
                if config.get('autoGroups',{}).get('enabled') is not True or window_selection is not None or (command.get('op')=='auto-group-register' and delivery.active() is not None):
                    emit({'event':'group-route-result','id':command_id,'ok':False,'code':'dynamic-group-admission-unavailable'})
                    continue
                if command['op']!='auto-group-register':
                    valid=(routes.get(destination)=='group' and auto_group_registry.get(destination)==command.get('nativeChatKey')
                        and command.get('accountId')==config.get('accountId'))
                    if valid:
                        if command['op']=='auto-group-enable':
                            auto_group_suspended.discard(destination)
                            if attachment_registry:attachment_registry.routes.add(destination)
                        else:
                            auto_group_suspended.add(destination)
                            if attachment_registry and destination not in fixed_attachment_routes:
                                attachment_registry.routes.discard(destination)
                        if group_text:group_text.set_route_active(destination,command['op']=='auto-group-enable')
                        if not sync_active_social_groups():return
                    emit({'event':'group-route-result','id':command_id,'ok':valid,
                          **({'value':{'enabled':destination not in auto_group_suspended}} if valid else {'code':'dynamic-group-binding-mismatch'})})
                    continue
                from dynamic_group_watch_candidate import register_group
                handoff=None
                native_binding=None
                if command.get('nativeTextAuthorityEpoch') is not None:
                    try:
                        if command.get('nativeClaimId'):raise RuntimeError('mixed admission capabilities')
                        from native_group_ui_binding import verify_native_monitor_binding
                        native_binding=verify_native_monitor_binding(config,command.get('nativeChatKey'),
                            command['nativeTextAuthorityEpoch'],destination)
                    except Exception:
                        emit({'event':'group-route-result','id':command_id,'ok':False,
                              'code':'dynamic-group-native-monitor-binding-unverified'})
                        continue
                if command.get('nativeClaimId'):
                    try:
                        from native_group_handoff import verify_queued_handoff
                        handoff=verify_queued_handoff(config,target=destination,native_chat_key=command.get('nativeChatKey'),claim_id=command['nativeClaimId'])
                    except Exception as error:
                        emit({'event':'group-route-result','id':command_id,'ok':False,
                              'code':str(error) if isinstance(error,RuntimeError) and str(error).startswith('native-group-') else 'native-group-handoff-invalid'})
                        continue
                with contextlib.redirect_stdout(sys.stderr):
                    result=register_group(wx,monitor,routes,seen,auto_group_registry,target=destination,
                        native_chat_key=command.get('nativeChatKey'),account_id=command.get('accountId'),expected_account_id=config.get('accountId'),
                        **({'native_handoff':handoff} if handoff is not None else {}),
                        **({'native_monitor_binding':native_binding} if native_binding is not None else {}))
                if result['ok']:
                    auto_group_suspended.add(destination)
                    if group_text:
                        group_text.set_route_active(destination,False)
                        if handoff is not None:
                            try:group_text.remember_registered_handoff(handoff,result,routes=routes,registry=auto_group_registry)
                            except Exception:
                                emit({'event':'group-text-ui-proof-unavailable','target':destination})
                    if attachment_registry and destination not in fixed_attachment_routes:
                        attachment_registry.routes.discard(destination)
                    if not sync_active_social_groups():return
                emit({'event':'group-route-result','id':command_id,'ok':result['ok'],
                      **({'handoffObservation':result['handoffObservation']} if 'handoffObservation' in result else {}),
                      **({'value':{**result,**({'nativeMonitorRestored':True,
                          'nativeTextAuthorityEpoch':native_binding.authority_epoch,
                          'accountId':native_binding.account_id,'nativeChatKey':native_binding.native_chat_key}if native_binding is not None else {}),**({'nativeClaimId':handoff.claim_id,
                          'nativeClaimHash':handoff.claim_hash,
                          'nativeRenderedMessages':[{'nativeId':ident,'text':text} for ident,text in handoff.rendered_messages]}
                          if handoff is not None else {})}} if result['ok'] else {'code':result['code']})})
                if result.get('stopWorker'):
                    emit({'event':'connection-blocked','code':result['code'],'phase':'dynamic-group-recovery'})
                    return
                continue
            if diagnostic_probe and (os.environ.get('WHALE_NATIVE_CANCEL_DIAGNOSTIC') != '1'
                                     or command.get('diagnosticCancelOnly') is not True):
                emit({'event':'ack','id':command_id,'ok':False,'code':'native-cancel-diagnostic-disabled'})
                continue
            if command.get('op') in SOCIAL_OPS:
                if social is None or (command['op'] in ('social-execute','social-observe','social-acceptance') and (delivery.active() is not None or window_selection is not None)):
                    emit({'event':'social-result','id':command_id,'ok':False,
                          'code':('social-unavailable' if social is None else
                                  'social-independent-window-unverified' if window_selection is not None else
                                  'social-reply-in-progress')})
                else:
                    with contextlib.redirect_stdout(sys.stderr):
                        result = social.reply(command)
                    emit(result)
                    if getattr(social.backend, 'chat_binding_failed', False):
                        emit({'event':'connection-blocked','code':'social-chat-restore-failed',
                              'phase':'social-chat-recovery'})
                        return
                continue
            if command.get('op') not in ('begin-reply', 'end-reply', 'send', 'send-file', 'send-attachment', 'send-voice', 'probe-native-voice') or destination not in routes or (destination in auto_group_suspended and command.get('op')!='end-reply'):
                emit({'event': 'ack', 'id': command_id, 'ok': False, 'code': 'target-or-operation-denied'})
                continue
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    reply_id = command.get('replyId')
                    if not isinstance(reply_id, str) or not reply_id:
                        raise RuntimeError('missing-reply-id')
                    if command['op'] == 'end-reply':
                        # Receiving (including downloads/preview navigation) is
                        # outside the sending transaction, and retries separately.
                        delivery.end(reply_id, destination)
                        emit({'event': 'ack', 'id': command_id, 'ok': True})
                        continue
                    if command['op'] != 'begin-reply':
                        delivery.check(reply_id, destination)
                    if window_selection:
                        chat = window_selection.chat_for(destination)
                    else:
                        monitor.select(destination)
                        chat = wx
                    info = chat.ChatInfo()
                    if info.get('chat_name') != destination or info.get('chat_type') != routes[destination]:
                        raise RuntimeError('target-mismatch')
                    if command['op'] == 'begin-reply':
                        delivery.begin(reply_id, destination)
                        emit({'event': 'ack', 'id': command_id, 'ok': True})
                        continue
                    mentions = command.get('at', [])
                    if not isinstance(mentions, list) or (mentions and routes[destination] != 'group') or any(not isinstance(m, str) or not m.strip() for m in mentions):
                        raise RuntimeError('mention-not-allowed')
                    if command['op'] == 'send-file':
                        image = meme_file(command['filepath'], config.get('memeRoots', []))
                    if command['op'] == 'send-attachment':
                        if not attachment_registry or mentions:
                            raise RuntimeError('attachment-send-unavailable')
                        image = attachment_file(command['filepath'],
                            received_root=attachment_registry.chat_root(destination),
                            outbox_root=data_paths['outbox'] / chat_key(destination),
                            received_paths=attachment_registry.received_paths(destination, command['filepath']))
                    if command['op'] in ('send-voice','probe-native-voice') and (mentions or not native_voice.ready()):
                        raise RuntimeError('native-voice-unavailable')
                    # Claim BEFORE performing the external action; uncertain outcomes never retry.
                    if not claim(command_id):
                        raise RuntimeError('already-claimed')
                    if diagnostic_probe:
                        digest = command.get('inspectIncomingTextSha256')
                        if digest is not None:
                            if (routes[destination] != 'group' or not isinstance(digest,str) or len(digest)!=64
                                    or any(c not in '0123456789abcdef' for c in digest)):
                                raise RuntimeError('native-cancel-inspection-invalid')
                            try:
                                from wxauto4.msgs.msg import parse_msg
                                controls = chat._api._chat_api.msgbox.GetChildren()
                                if len(controls)>4000:
                                    raise ValueError('budget')
                                matches=[]
                                for control in controls:
                                    if control.ClassName!='mmui::ChatTextItemView':continue
                                    message=parse_msg(control,chat._api._chat_api)
                                    if (getattr(message,'type',None)=='text' and getattr(message,'attr',None)=='friend'
                                            and hashlib.sha256(str(getattr(message,'content','')).strip().encode('utf8')).hexdigest()==digest):
                                        matches.append(message)
                                diagnostic_facts['inspection']={'matchedCount':len(matches),'sha256':digest}
                                if len(matches)!=1:
                                    raise RuntimeError('native-cancel-inspection-not-unique')
                                # Simulate the existing receive-side sender load once.
                                # Do not emit, commit a cursor, or inject a chat turn.
                                getattr(matches[0],'sender')
                            except Exception as error:
                                if isinstance(error,RuntimeError) and str(error)=='native-cancel-inspection-not-unique':raise
                                raise RuntimeError('native-cancel-inspection-failed') from None
                        if command.get('dismissAvatarMenu') is True:
                            from wxauto4 import uia
                            from native_voice_menus import close_avatar_context_menu, MenuCleanupError
                            try:
                                diagnostic_facts['menuCleanup']=close_avatar_context_menu(chat._api.control,uia)
                            except MenuCleanupError as error:
                                if str(error)=='avatar-menu-count-unverified' and error.facts.get('menuCount')==0:
                                    diagnostic_facts['menuCleanup']={'closed':False,'menuCount':0,'status':'not-found'}
                                else:raise
                        transport = native_voice if chat is wx else NativeVoice(chat, native_config)
                        result = transport._record(command['filepath'],confirm_send=False,
                            context={'target':destination,'kind':routes[destination],'commandId':command_id})
                        if not isinstance(result,dict) or result.get('canceled') is not True:
                            raise RuntimeError('native-cancel-outcome-unknown')
                        if isinstance(result.get('menuCleanup'),dict) and 'menuCleanup' not in diagnostic_facts:
                            diagnostic_facts['menuCleanup']=result['menuCleanup']
                    elif command['op'] == 'send-voice':
                        result = (native_voice if chat is wx else NativeVoice(chat, native_config)).send(
                            command['filepath'], target=destination, kind=routes[destination], command_id=command_id)
                    else:
                        observation = None
                        if command['op'] == 'send-attachment':
                            from file_send_readback import FileReadback, FileReadbackError, FILE_ROW_CLASSES, observed_file_basename
                            try:
                                observation = FileReadback(chat, destination, routes[destination], image.name,
                                    file_row_classes=FILE_ROW_CLASSES, filename_reader=observed_file_basename)
                            except FileReadbackError as error:
                                if str(error) == 'file-readback-target-mismatch':
                                    raise
                                # Missing optional passive evidence cannot grant
                                # success; existing transaction checks still hold.
                                pass
                        result = chat.SendFiles(str(image)) if command['op'] in ('send-file', 'send-attachment') else chat.SendMsg(command['text'], at=mentions or None)
                        if command['op'] == 'send-attachment' and result is None and observation is not None:
                            rendered_file_evidence = observation.confirm()
                    if not result and rendered_file_evidence is None:
                        try:
                            sdk_result_facts = sdk_result_diagnostic(result)
                        except Exception:
                            pass  # Diagnostics cannot change the unknown outcome.
                        raise RuntimeError('send-not-confirmed')
                    if not diagnostic_probe:
                        try:
                            monitor.note_outbound(destination)
                        except Exception as error:
                            # The external send succeeded: do not turn a bookkeeping
                            # failure into an uncertain send or retry the message.
                            emit({'event': 'error', 'code': 'outbound-observation-failed', 'diagnostic': error_diagnostic(error, 'observe-outbound')})
                emit({'event': 'ack', 'id': command_id, 'ok': True,
                      **({'renderedFileEvidence': rendered_file_evidence} if rendered_file_evidence else {}),
                      **({'canceled':True,**diagnostic_facts} if diagnostic_probe else {})})
            except Exception as error:
                emit({'event': 'ack', 'id': command_id, 'ok': False,
                      'code': str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                      **({'sdkResult': sdk_result_facts} if sdk_result_facts is not None else {}),
                      **(diagnostic_facts if diagnostic_probe else {})})
        # The IPC reader may enqueue commands concurrently, but only this thread
        # touches wxauto. Do not switch chats between bubbles of an active reply.
        if delivery.active() is not None or not incoming.empty():
            continue
        if retries.due(None):
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    candidates = monitor.candidates()
                if retries.recovered(None):
                    emit({'event': 'monitor-list-recovered'})
            except Exception as error:
                emit({'event': 'monitor-list-retrying', **retries.failed(None),
                      'code': str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                      'diagnostic': error_diagnostic(error, 'list-chats')})
                candidates = []
            for name in candidates:
                if name in auto_group_suspended:
                    continue
                if not retries.due(name):
                    continue
                try:
                    with contextlib.redirect_stdout(sys.stderr):
                        publish_incoming(name)
                    if retries.recovered(name):
                        emit({'event': 'chat-read-recovered', 'target': name})
                except WorkerOwnerTextFatal:
                    return
                except Exception as error:
                    emit({'event': 'chat-read-retrying', 'target': name, **retries.failed(name),
                          'code': str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                          'diagnostic': error_diagnostic(error, 'read-chat')})
            poll_count += 1
        if time.monotonic() - last_session_probe >= 10:
            if config.get('autoGroups',{}).get('enabled') is True:
                try:
                    with contextlib.redirect_stdout(sys.stderr):names={row.name for row in wx.GetSession()}
                    new=names-known_session_names;known_session_names.update(names)
                    if new:emit({'event':'new-session-visible','count':len(new)})
                except Exception:pass
            last_session_probe = time.monotonic()
        emit_due_heartbeat()

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        emit({'event': 'fatal', 'code': type(error).__name__})
        sys.exit(1)
