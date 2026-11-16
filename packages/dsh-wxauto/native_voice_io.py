"""Private one-request native voice catalog/cache process; no UI or writer."""
import argparse,hashlib,json,os,sys,uuid
from pathlib import Path
from data_paths import absolute_data_path
from native_voice_catalog import list_native_voices,page_native_voices
from native_voice_source import read_native_voice_source


def request(data_root,args):
    root=Path(data_root).resolve();config=json.loads((root/'config/wxauto.local.json').read_text('utf-8-sig'))
    if Path(config['dataRoot']).resolve()!=root:raise RuntimeError('native-voice-io-root-mismatch')
    capture=config['capture'];account=config['accountId']
    registry=json.loads((root/'config/group-routes.local.json').read_text('utf-8-sig'))
    if registry['accountId']!=account:raise RuntimeError('native-voice-io-registry-account-mismatch')
    allowed={record['nativeChatKey'] for record in registry['groups'] if record.get('nativeChatKey')}
    owner=config.get('nativeVoiceSources',{}).get('ownerNativeChatKey')
    if owner:allowed.add(owner)
    keys=args.get('allowedChatKeys')
    if not isinstance(keys,list) or not keys or len(keys)>1000 or len(set(keys))!=len(keys) or any(k not in allowed for k in keys):
        raise RuntimeError('native-voice-io-chat-not-authorized')
    snapshot_id=args.get('snapshotId')
    if snapshot_id is None:
        latest=json.loads((root/'snapshots/windows-live/latest.json').read_text('utf8'))
        if latest.get('import',{}).get('account_id')!=account:raise RuntimeError('native-voice-io-latest-account-mismatch')
        snapshot_id=latest['snapshot_id']
    if not isinstance(snapshot_id,str) or str(uuid.UUID(snapshot_id))!=snapshot_id:raise RuntimeError('native-voice-io-snapshot-invalid')
    snapshot=root/'snapshots/windows-live/snapshots'/snapshot_id
    common=dict(expected_account_username=capture['accountUsername'],expected_source_id=capture['sourceId'],expected_snapshot_id=snapshot_id)
    if args.get('op')=='catalog':
        return {**list_native_voices(snapshot,allowed_chat_keys=keys,**common),'accountId':account}
    if args.get('op')=='catalog-page':
        return page_native_voices(snapshot,allowed_chat_keys=keys,account_id=account,scan_key=args.get('scanKey'),
            cursor=args.get('cursor'),page_size=args.get('pageSize',500),**common)
    if args.get('op')!='extract' or args.get('nativeChatKey') not in keys:
        raise RuntimeError('native-voice-io-operation-denied')
    value=read_native_voice_source(snapshot,native_chat_key=args['nativeChatKey'],server_id=args['serverId'],**common)
    meta=value['metadata'];folder=hashlib.sha256((account+'\0'+meta['nativeChatKey']).encode()).hexdigest()[:24]
    relative='voice/incoming-native/'+folder+'/'+meta['serverId']+'-'+meta['sha256'][:16]+'.silk'
    file=absolute_data_path(relative,config);file.parent.mkdir(parents=True,exist_ok=True)
    file=absolute_data_path(relative,config)
    if file.exists():
        if file.stat().st_nlink!=1 or hashlib.sha256(file.read_bytes()).hexdigest()!=meta['sha256']:
            raise RuntimeError('native-voice-io-cache-conflict')
    else:
        temporary=file.with_name(file.name+'.'+uuid.uuid4().hex+'.tmp')
        with temporary.open('xb') as stream:stream.write(value['blob']);stream.flush();os.fsync(stream.fileno())
        os.rename(temporary,file)
    return {'file':str(file),'relativeFile':relative,'metadata':meta}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(256*1024+1)
        if len(raw)>256*1024:raise RuntimeError('native-voice-io-request-too-large')
        value=request(args.root,json.loads(raw))
        print(json.dumps({'ok':True,'value':value},ensure_ascii=False))
    except Exception as error:
        code=str(error) if isinstance(error,RuntimeError) and str(error).startswith(('native-voice-','native-voice-catalog-')) else 'native-voice-io-failed'
        print(json.dumps({'ok':False,'code':code}));raise SystemExit(2)


if __name__=='__main__':main()
