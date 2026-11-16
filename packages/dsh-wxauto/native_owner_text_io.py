"""Read-only owner-text catalog IPC; no capture, baseline, approval or send."""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from attachment_policy import local_path
from data_paths import absolute_data_path


def _json_file(file):
    file=local_path(file);before=file.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=4*1024*1024:
        raise RuntimeError('native-owner-text-io-file-invalid')
    signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_nlink)
    with file.open('rb') as stream:
        if signature(os.fstat(stream.fileno()))!=signature(before):
            raise RuntimeError('native-owner-text-io-file-changed')
        raw=stream.read()
        if signature(os.fstat(stream.fileno()))!=signature(before) or signature(local_path(file).stat())!=signature(before):
            raise RuntimeError('native-owner-text-io-file-changed')
    value=json.loads(raw.decode('utf-8-sig'))
    if type(value) is not dict:raise RuntimeError('native-owner-text-io-file-invalid')
    return value,hashlib.sha256(raw).hexdigest()


def request(data_root,args):
    if (type(args) is not dict or set(args)-{'op','scanKey','snapshotId','cursor','pageSize'}
            or args.get('op')!='catalog-page' or not isinstance(args.get('scanKey'),str)
            or not re.fullmatch('[a-f0-9]{64}',args['scanKey'])):
        raise RuntimeError('native-owner-text-io-request-invalid')
    root=local_path(data_root)
    config_file=absolute_data_path('config/wxauto.local.json',root=root)
    config,config_hash=_json_file(config_file)
    if (not isinstance(config.get('dataRoot'),str) or not os.path.isabs(config['dataRoot'])
            or local_path(config['dataRoot'])!=root):
        raise RuntimeError('native-owner-text-io-root-mismatch')
    account=config.get('accountId');capture=config.get('capture')
    sources=config.get('nativeVoiceSources')
    owner=sources.get('ownerNativeChatKey') if type(sources) is dict else None
    if (not isinstance(account,str) or not account or type(capture) is not dict
            or not all(isinstance(capture.get(k),str) and capture[k] for k in ('accountUsername','sourceId'))
            or not isinstance(owner,str) or not re.fullmatch('wechat:[A-Za-z0-9_-]{1,256}',owner)
            or owner[7:]==capture['accountUsername']):
        raise RuntimeError('native-owner-text-io-owner-config-invalid')
    snapshot_id=args.get('snapshotId');latest_file=None;latest_hash=None
    if snapshot_id is None:
        latest_file=absolute_data_path('snapshots/windows-live/latest.json',root=root)
        latest,latest_hash=_json_file(latest_file)
        imported=latest.get('import')
        if type(imported) is not dict or imported.get('account_id')!=account:
            raise RuntimeError('native-owner-text-io-latest-account-mismatch')
        snapshot_id=latest.get('snapshot_id')
    try:
        if not isinstance(snapshot_id,str) or str(uuid.UUID(snapshot_id))!=snapshot_id:raise ValueError()
    except (ValueError,AttributeError):raise RuntimeError('native-owner-text-io-snapshot-invalid') from None
    snapshot=absolute_data_path('snapshots/windows-live/snapshots/'+snapshot_id,root=root)
    from native_owner_text_catalog import page_native_owner_texts
    value=page_native_owner_texts(snapshot,expected_account_username=capture['accountUsername'],
        expected_source_id=capture['sourceId'],expected_snapshot_id=snapshot_id,account_id=account,
        owner_native_chat_key=owner,expected_peer_username=owner[7:],scan_key=args['scanKey'],
        cursor=args.get('cursor'),page_size=args.get('pageSize',500))
    if _json_file(config_file)[1]!=config_hash or latest_file is not None and _json_file(latest_file)[1]!=latest_hash:
        raise RuntimeError('native-owner-text-io-configuration-changed')
    return value


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);options=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(256*1024+1)
        if len(raw)>256*1024:raise RuntimeError('native-owner-text-io-request-too-large')
        value=request(options.root,json.loads(raw))
        print(json.dumps({'ok':True,'value':value},ensure_ascii=False))
    except Exception as error:
        code=str(error) if isinstance(error,RuntimeError) and re.fullmatch(r'native-owner-text-(?:io|catalog)-[a-z-]{1,100}',str(error)) else 'native-owner-text-io-failed'
        print(json.dumps({'ok':False,'code':code}));raise SystemExit(2)


if __name__=='__main__':main()
