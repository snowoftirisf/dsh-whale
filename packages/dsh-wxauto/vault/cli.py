"""Local maintenance CLI and private JSONL worker; no network service.

python -m vault.cli --root <DATA_ROOT> status
python -m vault.cli --root <DATA_ROOT> request '{"op":"search","args":{...}}'
python -m vault.cli --root <DATA_ROOT> worker
"""
import argparse
import json
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from .store import DEFAULT_ROOT, Vault, VaultError, restore
from .harness_import import iter_log, import_harness


def dispatch(vault, request):
    op=request.get('op');args=request.get('args') or {}
    if op=='import_harness':
        args=dict(args);file=args.pop('file')
        return import_harness(vault,iter_log(file),**args)
    if op=='import_snapshot':
        from .snapshot_import import import_snapshot
        return import_snapshot(vault,**args)
    return vault.request(op,args)


def response(vault, request):
    try:
        if not isinstance(request,dict):raise VaultError('request-must-be-object')
        return {'id':request.get('id'),'ok':True,'value':dispatch(vault,request)}
    except Exception as exc:
        # No exception locals, source paths, credentials, or message content.
        return {'id':request.get('id') if isinstance(request,dict) else None,'ok':False,
                'error':str(exc) if isinstance(exc,VaultError) else type(exc).__name__}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(DEFAULT_ROOT))
    sub=parser.add_subparsers(dest='command',required=True)
    for n in ('status','doctor','worker'):sub.add_parser(n)
    p=sub.add_parser('backup');p.add_argument('--destination')
    p=sub.add_parser('restore');p.add_argument('backup');p.add_argument('--destination',required=True)
    p=sub.add_parser('bind-chat');p.add_argument('source_chat_id');p.add_argument('target_chat_id')
    p=sub.add_parser('request');p.add_argument('json')
    options=parser.parse_args()
    if options.command=='restore':
        print(json.dumps(restore(options.backup,options.destination),ensure_ascii=False));return
    vault=Vault(options.root)
    try:
        if options.command=='worker':
            # Every SQLite write runs on one queue, regardless of IPC producers.
            with ThreadPoolExecutor(max_workers=1,thread_name_prefix='whale-vault-writer') as queue:
                for line in sys.stdin:
                    try:request=json.loads(line)
                    except ValueError:request={'op':'invalid-json'}
                    if isinstance(request,dict) and request.get('op')=='stop':break
                    value=queue.submit(response,vault,request).result()
                    print(json.dumps(value,ensure_ascii=False),flush=True)
        else:
            if options.command=='bind-chat':
                request={'op':'merge_chat','args':{'source_chat_id':options.source_chat_id,'target_chat_id':options.target_chat_id}}
            else:
                request=json.loads(options.json) if options.command=='request' else {'op':options.command,'args':{'destination':options.destination} if options.command=='backup' else {}}
            value=response(vault,request);print(json.dumps(value,ensure_ascii=False))
            if not value['ok']:raise SystemExit(1)
    finally:vault.close()


if __name__=='__main__':main()
