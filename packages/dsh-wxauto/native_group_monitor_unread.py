"""Native-only unread qualification for restoring an already committed monitor.

No UI access, capture, submission, baseline mutation or first-admission grant.
Immutable snapshot evidence can be revalidated after selecting clears live badges.
"""
from dataclasses import dataclass,field
import json,re,subprocess
from pathlib import Path
from native_group_ui_binding import revalidate_native_monitor_binding
from native_owner_text_claim import _read,_compact,_hash
from group_unread import read_latest_group_unread,read_snapshot_group_unread
from native_group_handoff import _rendered_messages
from native_voice_source import read_native_voice_source
from message_watch import SessionGate

_TOKEN=object()
HERE=Path(__file__).resolve().parent


def need(ok,code):
    if not ok:raise RuntimeError('native-group-monitor-unread-'+code)


@dataclass(frozen=True)
class NativeMonitorUnreadProof:
    account_id:str
    native_chat_key:str
    target:str
    authority_epoch:str
    snapshot_id:str
    unread_count:int
    server_ids:tuple
    source:str
    last_kind:str
    last_voice_duration_ms:object
    _before_json:bytes=field(repr=False)
    _messages_json:bytes=field(repr=False)
    _rendered_json:bytes=field(repr=False)
    _token:object=field(repr=False,compare=False)

    @property
    def rendered_tail(self):return json.loads(self._rendered_json)[-1][1]
    @property
    def messages(self):return json.loads(self._messages_json)


def _voice_owned(config,key):
    need(type(config.get('nativeVoiceSources'))is dict and config['nativeVoiceSources'].get('enabled')is True,'voice-disabled')
    file=Path(config['dataRoot'])/'config/native-voice-inbox.json'
    value,token=_read(file)
    body={k:v for k,v in value.items()if k!='digest'}
    need(value.get('digest')==_hash(body)and body.get('format')=='whale-native-voice-inbox'and body.get('accountId')==config['accountId']
         and type(body.get('chatBaselines'))is list and key in body['chatBaselines'],'voice-baseline-required')
    script="import fs from 'node:fs';import {createNativeVoiceInbox}from './native-voice-inbox.mjs';const c=JSON.parse(fs.readFileSync(0,'utf8'));const i=createNativeVoiceInbox({dataRoot:c.root,accountId:c.account});const s=i.status();process.stdout.write(JSON.stringify({initialized:s.initialized,owned:i.hasChat(c.key),revision:s.revision}));"
    result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps({'root':config['dataRoot'],'account':config['accountId'],'key':key}),
        text=True,encoding='utf8',capture_output=True,cwd=HERE,timeout=15)
    need(result.returncode==0,'voice-inbox-unverified')
    try:status=json.loads(result.stdout)
    except Exception:raise RuntimeError('native-group-monitor-unread-voice-inbox-unverified')from None
    need(status.get('initialized')is True and status.get('owned')is True and status.get('revision')==body.get('revision')
         and _read(file)[1]==token,'voice-inbox-unverified')


def _binding(binding):
    try:
        verified=revalidate_native_monitor_binding(binding,target=binding.target,native_chat_key=binding.native_chat_key,account_id=binding.account_id)
        return verified,verified._config
    except Exception:raise RuntimeError('native-group-monitor-unread-binding-unverified')from None


def _qualify(binding,before,*,pinned=None):
    verified,config=_binding(binding);key=verified.native_chat_key
    need(type(before)is dict and before.get('isnew')is True and type(before.get('new_count'))is int
         and 1<=before['new_count']<=20,'before-unread-unverified')
    args={'native_chat_key':key,'account_id':verified.account_id,'expected_account_username':config['capture']['accountUsername'],
          'expected_source_id':config['capture']['sourceId']}
    if pinned is None:value=read_latest_group_unread(config['dataRoot'],**args)
    else:value=read_snapshot_group_unread(Path(config['dataRoot'])/'snapshots/windows-live/snapshots'/pinned,**args)
    if value.get('state')!='ready' and str(value.get('code','')).startswith('group-unread-media-not-supported'):
        raise RuntimeError('native-group-monitor-unread-unsupported-type')
    need(value.get('state')=='ready'and value.get('source')=='authenticated-wechat-native-unread'
         and value.get('nativeChatKey')==key and value.get('accountId')==verified.account_id
         and value.get('unreadCount')==before['new_count'],'native-source-unverified')
    messages=value.get('messages')
    need(type(messages)is list and len(messages)==before['new_count']and all(m.get('type')in('text','voice')
         and m.get('contextOnly')is False for m in messages),'unsupported-type')
    if pinned is not None:need(value.get('snapshotId')==pinned,'snapshot-mismatch')
    snapshot=Path(config['dataRoot'])/'snapshots/windows-live/snapshots'/value['snapshotId']
    rendered=_rendered_messages(snapshot,key,messages,args['expected_account_username'],args['expected_source_id'])
    need(len(rendered)==len(messages),'render-unverified')
    last=messages[-1];duration=None
    if any(m['type']=='voice'for m in messages):
        _voice_owned(config,key)
        # Only the tail duration qualifies the UI preview. Earlier voices have
        # authenticated IDs/types and inbox ownership, not a blob-decoding claim.
        if last['type']=='voice':
            audio=read_native_voice_source(snapshot,native_chat_key=key,server_id=last['serverId'],
                expected_account_username=args['expected_account_username'],expected_source_id=args['expected_source_id'],expected_snapshot_id=value['snapshotId'])
            duration=audio['metadata']['durationMilliseconds']
            need(type(duration)is int and 0<duration<=120000,'voice-duration-unverified')
    expected=SessionGate.preview_text(before)
    if last['type']=='text':
        compact=lambda s:''.join(s.split())
        need(bool(compact(expected))and compact(rendered[-1][1]).startswith(compact(expected)),'preview-mismatch')
    else:
        match=re.fullmatch(r'\[语音\]\s*(\d+)\s*(?:["″”]|秒)(?:秒)?',expected)
        need(match is not None and 1<=int(match[1])<=120 and abs(int(match[1])*1000-duration)<=1000,'voice-preview-mismatch')
    _binding(verified)
    return NativeMonitorUnreadProof(verified.account_id,key,verified.target,verified.authority_epoch,value['snapshotId'],value['unreadCount'],
        tuple(value['serverIds']),value['source'],last['type'],duration,_compact(before),_compact(messages),_compact(rendered),_TOKEN)


def qualify_native_monitor_unread(binding,before):
    try:return _qualify(binding,before)
    except RuntimeError as error:
        if re.fullmatch('native-group-monitor-unread-[a-z-]+',str(error)):raise
        raise RuntimeError('native-group-monitor-unread-source-unverified')from None
    except Exception:raise RuntimeError('native-group-monitor-unread-source-unverified')from None


def revalidate_native_monitor_unread(proof,binding):
    need(type(proof)is NativeMonitorUnreadProof and proof._token is _TOKEN,'proof-invalid')
    try:
        fresh=_qualify(binding,json.loads(proof._before_json),pinned=proof.snapshot_id)
        need(fresh==proof,'pinned-source-changed')
        return proof
    except RuntimeError as error:
        if re.fullmatch('native-group-monitor-unread-[a-z-]+',str(error)):raise
        raise RuntimeError('native-group-monitor-unread-source-unverified')from None
    except Exception:raise RuntimeError('native-group-monitor-unread-source-unverified')from None
