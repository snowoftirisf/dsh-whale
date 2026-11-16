"""Read-only active group-text catalog IPC; no capture, baseline, approval or send."""
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
        raise RuntimeError('native-group-text-io-file-invalid')
    signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_nlink)
    with file.open('rb') as stream:
        if signature(os.fstat(stream.fileno()))!=signature(before):
            raise RuntimeError('native-group-text-io-file-changed')
        raw=stream.read()
        if signature(os.fstat(stream.fileno()))!=signature(before) or signature(local_path(file).stat())!=signature(before):
            raise RuntimeError('native-group-text-io-file-changed')
    value=json.loads(raw.decode('utf-8-sig'))
    if type(value) is not dict:raise RuntimeError('native-group-text-io-file-invalid')
    return value,hashlib.sha256(raw).hexdigest()


def request(data_root,args):
    if (type(args) is not dict or set(args)-{'op','scanKey','snapshotId','cursor','pageSize','nativeChatKey','admissionProof'}
            or args.get('op')!='catalog-page' or not isinstance(args.get('scanKey'),str)
            or not re.fullmatch('[a-f0-9]{64}',args['scanKey'])):
        raise RuntimeError('native-group-text-io-request-invalid')
    admission=args.get('admissionProof')
    if 'admissionProof'in args and (type(admission)is not dict or set(admission)!={'claimId','claimHash'}
            or not isinstance(admission.get('claimId'),str)or not re.fullmatch('[a-f0-9]{32}',admission['claimId'])
            or not isinstance(admission.get('claimHash'),str)or not re.fullmatch('[a-f0-9]{64}',admission['claimHash'])
            or not isinstance(args.get('snapshotId'),str)):
        raise RuntimeError('native-group-text-io-admission-invalid')
    root=local_path(data_root)
    config_file=absolute_data_path('config/wxauto.local.json',root=root)
    config,config_hash=_json_file(config_file)
    if (not isinstance(config.get('dataRoot'),str) or not os.path.isabs(config['dataRoot'])
            or local_path(config['dataRoot'])!=root):
        raise RuntimeError('native-group-text-io-root-mismatch')
    account=config.get('accountId');capture=config.get('capture')
    key=args.get('nativeChatKey')
    if (not isinstance(account,str) or not account or type(capture) is not dict
            or not all(isinstance(capture.get(k),str) and capture[k] for k in ('accountUsername','sourceId'))
            or not isinstance(key,str) or not re.fullmatch(r'wechat:[A-Za-z0-9_-]{1,128}@chatroom',key)):
        raise RuntimeError('native-group-text-io-group-config-invalid')
    registry_file=absolute_data_path('config/group-routes.local.json',root=root)
    registry,registry_hash=_json_file(registry_file)
    groups=registry.get('groups')
    if registry.get('version')!=1 or registry.get('accountId')!=account or not isinstance(groups,list) or len(groups)>1000:
        raise RuntimeError('native-group-text-io-route-invalid')
    matches=[r for r in groups if type(r) is dict and r.get('nativeChatKey')==key]
    if (len(matches)!=1 or matches[0].get('accountId')!=account or matches[0].get('status')not in (('pending','active')if admission else('active',))
            or not all(isinstance(matches[0].get(k),str) and matches[0][k] for k in ('target','sessionId'))
            or matches[0]['target']==config.get('target')):
        raise RuntimeError('native-group-text-io-route-inactive')
    source_proof=None
    if admission:
        from native_group_text_admission_source import verify_pending_text_admission_source
        source_proof=verify_pending_text_admission_source(config,key,admission['claimId'],admission['claimHash'])
        if source_proof.snapshot_id!=args['snapshotId']:
            raise RuntimeError('native-group-text-io-admission-snapshot-mismatch')
    snapshot_id=args.get('snapshotId');latest_file=None;latest_hash=None
    if snapshot_id is None:
        latest_file=absolute_data_path('snapshots/windows-live/latest.json',root=root)
        latest,latest_hash=_json_file(latest_file)
        imported=latest.get('import')
        if type(imported) is not dict or imported.get('account_id')!=account:
            raise RuntimeError('native-group-text-io-latest-account-mismatch')
        snapshot_id=latest.get('snapshot_id')
    try:
        if not isinstance(snapshot_id,str) or str(uuid.UUID(snapshot_id))!=snapshot_id:raise ValueError()
    except (ValueError,AttributeError):raise RuntimeError('native-group-text-io-snapshot-invalid') from None
    snapshot=absolute_data_path('snapshots/windows-live/snapshots/'+snapshot_id,root=root)
    from native_group_text_catalog import page_native_group_texts
    value=page_native_group_texts(snapshot,expected_account_username=capture['accountUsername'],
        expected_source_id=capture['sourceId'],expected_snapshot_id=snapshot_id,account_id=account,
        native_group_chat_key=key,scan_key=args['scanKey'],
        cursor=args.get('cursor'),page_size=args.get('pageSize',500))
    if source_proof is not None:
        from native_group_text_admission_source import revalidate_pending_text_admission_source
        revalidate_pending_text_admission_source(config,source_proof)
        value={**value,'admission':{'claimId':source_proof.claim_id,'claimHash':source_proof.claim_hash,
                                  'snapshotId':source_proof.snapshot_id,'verified':True}}
    if _json_file(registry_file)[1]!=registry_hash or _json_file(config_file)[1]!=config_hash or latest_file is not None and _json_file(latest_file)[1]!=latest_hash:
        raise RuntimeError('native-group-text-io-configuration-changed')
    return value


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);options=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(256*1024+1)
        if len(raw)>256*1024:raise RuntimeError('native-group-text-io-request-too-large')
        value=request(options.root,json.loads(raw))
        print(json.dumps({'ok':True,'value':value},ensure_ascii=False))
    except Exception as error:
        code=str(error) if isinstance(error,RuntimeError) and re.fullmatch(r'native-group-text-(?:io|catalog|admission)-[a-z-]{1,100}',str(error)) else 'native-group-text-io-failed'
        print(json.dumps({'ok':False,'code':code}));raise SystemExit(2)


if __name__=='__main__':main()
