"""Owner-only read-only Moments metadata integration; import is inert.

Full executable/snapshot validation happens once at entry. Per-node guards only
check pinned filesystem metadata, account config identity and bound process.
This does not enable publishing or interpret author/post identity/time.
"""
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

PHASES=frozenset(('preflight','checking','locating-discover','discover-open','locating-moments','moments-open','locating-feed','collecting','exiting-feed','completed'))
EXPECTED_CLASSES=frozenset(('mmui::XTabBarItem','mmui::ExtensionDiscoverContentCell','mmui::SNSContentView'))


def _signature(file):
    from attachment_policy import local_path
    path=local_path(file);info=path.stat()
    if not path.is_file() or info.st_nlink!=1:raise RuntimeError('social-moments-source-unverified')
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns


def _identity(config):
    from attachment_policy import local_path
    capture=config.get('capture',{})
    value=(str(local_path(config['dataRoot'])),config.get('accountId'),config.get('target'),
           capture.get('accountUsername'),capture.get('sourceId'))
    if not all(isinstance(v,str) and v for v in value):raise RuntimeError('social-moments-source-unverified')
    return value


def _process(client):
    import psutil
    process=psutil.Process(client['pid'])
    return process.pid,process.create_time(),str(Path(process.exe()).resolve())


def observe_feed(backend,**kwargs):
    started=time.monotonic()
    diagnostic={'purpose':'moments-feed-metadata','attemptId':uuid.uuid4().hex,'phase':'preflight',
                'completed':False,'chatRestored':None,'durationMs':0}
    backend.last_moments_observation=diagnostic;backend.last_chat_recovery=None
    def on_stage(phase,*,expected_class=None,matches=None):
        phase='checking' if phase=='prechecks' else phase
        if phase not in PHASES:return
        diagnostic['phase']=phase
        if expected_class in EXPECTED_CLASSES:diagnostic['expectedClass']=expected_class
        if matches is None or type(matches)is int and 0<=matches<=4500:diagnostic['matches']=matches
    try:
        result=_observe_feed(backend,on_stage=on_stage,**kwargs)
        diagnostic.update(completed=True,phase='completed')
        return result
    except Exception as error:
        code=str(error) if isinstance(error,RuntimeError) else ''
        if code=='social-chat-restore-failed' or re.fullmatch('social-moments-[a-z-]{1,100}',code):
            diagnostic['code']=code;raise
        diagnostic['code']='social-moments-observation-unavailable'
        raise RuntimeError('social-moments-observation-unavailable') from None
    finally:
        recovery=backend.last_chat_recovery
        diagnostic['chatRestored']=recovery.get('restored') is True if isinstance(recovery,dict) else None
        diagnostic['durationMs']=max(0,round((time.monotonic()-started)*1000))


def _observe_feed(backend,*,uia=None,windows=None,discovery_fn=None,navigation_factory=None,collector=None,process_probe=None,on_stage=None):
    from social_friend_observe import OBSERVED_VERSION,OBSERVED_CLIENT_SHA
    from native_owner_text_io import _json_file
    from data_paths import absolute_data_path
    from social_moments_navigation import make_feed_navigation
    from social_moments_observe import observe_moments
    from group_discovery import discover_groups
    from attachment_policy import local_path
    client=backend._client()
    if client.get('clientVersion')!=OBSERVED_VERSION or client.get('clientSha256')!=OBSERVED_CLIENT_SHA:
        raise RuntimeError('social-moments-client-unverified')
    identity=_identity(backend.config);root,account,target,own,source=identity
    config_file=absolute_data_path('config/wxauto.local.json',root=root)
    stored,_=_json_file(config_file)
    if _identity(stored)!=identity:raise RuntimeError('social-moments-account-unverified')
    latest=absolute_data_path('snapshots/windows-live/latest.json',root=root)
    pins=[(config_file,_signature(config_file)),(latest,_signature(latest))]
    discovery=(discovery_fn or discover_groups)(latest,account,expected_account_username=own)
    snapshot_id=discovery.get('snapshotId')
    import uuid
    if not isinstance(snapshot_id,str) or str(uuid.UUID(snapshot_id))!=snapshot_id:raise RuntimeError('social-moments-source-unverified')
    manifest_file=absolute_data_path('snapshots/windows-live/snapshots/'+snapshot_id+'/snapshot-manifest.json',root=root)
    manifest,_=_json_file(manifest_file)
    if manifest.get('account_username')!=own or manifest.get('source_id')!=source:raise RuntimeError('social-moments-account-unverified')
    pins.append((manifest_file,_signature(manifest_file)))
    executable=local_path(client.get('executable',''))
    if executable.name.lower()!='weixin.exe':raise RuntimeError('social-moments-client-unverified')
    executable_pin=_signature(executable);probe=process_probe or _process;process_pin=probe(client)
    if type(client.get('pid'))is not int or client['pid']<=0 or process_pin[0]!=client['pid'] or Path(process_pin[2])!=executable:
        raise RuntimeError('social-moments-client-unverified')
    def identity_check():
        try:return _identity(backend.config)==identity and all(_signature(file)==sig for file,sig in pins)
        except Exception:return False
    def client_unchanged():
        try:return _signature(executable)==executable_pin and probe(client)==process_pin
        except Exception:return False
    if not identity_check() or not client_unchanged():raise RuntimeError('social-moments-source-unverified')
    if uia is None:
        from wxauto4 import uia
    if windows is None:
        from wxauto4.utils import GetAllWindows
        windows=GetAllWindows
    navigation=(navigation_factory or make_feed_navigation)(backend.wx,backend=backend,client=client,
        identity_check=identity_check,client_unchanged=client_unchanged,uia=uia,windows=windows,on_stage=on_stage)
    restore_target=None
    def context(wx):
        nonlocal restore_target
        started=backend._navigation_context
        if not isinstance(started,dict) or started.get('started') is not True:raise RuntimeError('social-moments-recovery-unverified')
        restore_target=hashlib.sha256(started['target'].encode('utf8')).hexdigest()
        return navigation(wx)
    backend.last_chat_recovery=None
    try:
        result=(collector or observe_moments)(backend.wx,recover_chat=backend._with_chat_recovery,
            navigation_context=context,ui_thread_id=backend.ui_thread_id,foreground=uia.GetForegroundWindow)
    except Exception as error:
        recovery=backend.last_chat_recovery
        if backend.chat_binding_failed or isinstance(recovery,dict) and recovery.get('restored') is not True:
            raise RuntimeError('social-chat-restore-failed') from None
        code=str(error) if isinstance(error,RuntimeError) else ''
        raise RuntimeError(code if re.fullmatch('social-moments-[a-z-]{1,100}',code) else 'social-moments-observation-unavailable') from None
    recovery=backend.last_chat_recovery
    if not isinstance(recovery,dict) or recovery.get('restored') is not True or backend.chat_binding_failed:
        backend.chat_binding_failed=True
        raise RuntimeError('social-chat-restore-failed')
    return {**result,'purpose':'moments-feed-metadata','readOnly':True,'sentMessages':False,'liveCapabilityGranted':False,
        'clientVersion':client['clientVersion'],'clientSha256':client['clientSha256'],
        'restoreTargetSha256':restore_target,'chatRestored':True}
