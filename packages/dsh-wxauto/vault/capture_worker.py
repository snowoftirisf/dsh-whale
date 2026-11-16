"""Private JSONL, two-phase snapshot producer; never opens the history database.

collect holds the OS sync lock after returning a validated candidate. The parent
imports it through the sole vault writer, then commit publishes latest.json.
abort, EOF, exceptions and worker death release the lock without publishing.
"""
import json
from pathlib import Path
import sys
import uuid
import argparse
from .capture import CaptureService,SyncLock,dependency_status,_now
from .snapshot_import import _validate,load_reader,SOURCE,_fingerprint
from .store import DEFAULT_ROOT,VaultError,required,atomic_json
from .capture_selection import validate_audio_flag,select_capture_files,finalize_audio_capture


class CandidateWorker:
    def __init__(self,data_root=DEFAULT_ROOT,*,collector=None):
        self.service=CaptureService(data_root,collector=collector)
        self.root=self.service.root;self.output=self.service.output
        self.lock=None;self.pending=None;self.last_commit=None

    def _release(self):
        lock=self.lock;self.lock=None
        if lock:lock.__exit__(None,None,None)

    def collect(self,*,db_root,source_id,account_id,account_username=None,pids=None,
                seconds=90,max_db_mib=512,scope='chat',include_audio=False):
        validate_audio_flag(include_audio,scope)
        if self.pending or self.lock:raise VaultError('candidate-already-pending')
        required(source_id,'source-id');required(account_id,'account-id')
        if not isinstance(db_root,(str,Path)) or not str(db_root):raise VaultError('explicit-db-root-required')
        if account_username is not None:required(account_username,'account-username')
        if not isinstance(seconds,int) or isinstance(seconds,bool) or not 1<=seconds<=300:raise VaultError('invalid-capture-time-limit')
        if not isinstance(max_db_mib,int) or isinstance(max_db_mib,bool) or not 1<=max_db_mib<=8192:raise VaultError('invalid-capture-byte-limit')
        if scope not in ('chat','all'):raise VaultError('invalid-capture-scope')
        if self.service.collector is None and not dependency_status()['ready']:raise VaultError('zstandard-0.25.0-required')
        lock=SyncLock(self.root);lock.__enter__();self.lock=lock
        job=uuid.uuid4().hex
        self.output.mkdir(parents=True,exist_ok=True)
        latest=self.output/'latest.json';previous=latest.read_bytes() if latest.exists() else None
        try:
            with self.service.backend() as collector:
                original_root=collector.private_root;original_json=collector.atomic_json
                def defer_publish(path,value):
                    if Path(path).resolve()==latest.resolve():return
                    return original_json(path,value)
                collector.private_root=lambda:self.output;collector.atomic_json=defer_publish
                try:
                    candidates=collector.discover(str(db_root));root,files=collector.choose_source(candidates,source_id)
                    if collector.source_id(root)!=source_id:raise VaultError('capture-source-mismatch')
                    files,audio_names=select_capture_files(collector,root,files,scope=scope,include_audio=include_audio)
                    available=collector.process_ids();chosen=pids or available
                    if not chosen or any(isinstance(pid,bool) or not isinstance(pid,int) or pid not in available for pid in chosen):raise VaultError('authorized-weixin-process-required')
                    receipt=collector.capture(root,files,chosen,max_bytes=max_db_mib*1024**2,seconds=seconds,scope=scope)
                    if not isinstance(receipt,dict) or receipt.get('status') not in ('complete','complete_with_index_gaps'):
                        raise VaultError('capture-did-not-complete')
                    snapshot=Path(receipt['snapshot']).resolve(strict=True)
                    if not snapshot.is_relative_to(self.output.resolve()) or not snapshot.is_dir():raise VaultError('capture-output-path-denied')
                    if receipt.get('source_id')!=source_id or receipt.get('keys_persisted') is not False or receipt.get('read_only_source') is not True:
                        raise VaultError('capture-receipt-invalid')
                    audio_files=finalize_audio_capture(snapshot,receipt,audio_names,original_json)
                    if account_username is not None:
                        manifest_file=snapshot/'snapshot-manifest.json';manifest=json.loads(manifest_file.read_text(encoding='utf8'))
                        manifest['account_username']=account_username;original_json(manifest_file,manifest)
                finally:
                    collector.private_root=original_root;collector.atomic_json=original_json
            # Upstream capture's finally has cleared its key dictionary before
            # this response. Only pure reader verification follows.
            _,manifest,_,fingerprints=_validate(load_reader(),snapshot)
            fingerprints.update(_fingerprint(audio_files))
            if manifest['snapshot_id']!=receipt.get('snapshot_id'):raise VaultError('candidate-snapshot-id-mismatch')
            if (latest.read_bytes() if latest.exists() else None)!=previous:raise VaultError('collector-published-before-commit')
            self.pending={'jobId':job,'candidatePath':str(snapshot),'captureReceipt':receipt,
                          'cursorSource':SOURCE+':'+account_id,'accountId':account_id,
                          'fingerprints':fingerprints,'previousLatest':previous}
            atomic_json(self.output/'pending-candidate.json',{'jobId':job,'snapshot_id':manifest['snapshot_id'],
                         'state':'candidate-ready','created_at':_now(),'keys_persisted':False})
            return {k:v for k,v in self.pending.items() if k not in ('fingerprints','previousLatest')}
        except BaseException:
            self.pending=None;self._release();raise

    def commit(self,*,jobId,importResult):
        pending=self.pending
        if not pending or jobId!=pending['jobId']:raise VaultError('candidate-job-mismatch')
        if not isinstance(importResult,dict) or importResult.get('complete') is not True or importResult.get('snapshot_id')!=pending['captureReceipt']['snapshot_id']:
            raise VaultError('candidate-import-not-confirmed')
        if importResult.get('account_id')!=pending['accountId']:raise VaultError('candidate-import-account-mismatch')
        try:
            # Verify the exact candidate is still intact. The history writer has
            # already completed; this process never creates a Vault connection.
            _validate(load_reader(),pending['candidatePath'])
            if _fingerprint([Path(p) for p in pending['fingerprints']])!=pending['fingerprints']:
                raise VaultError('candidate-changed-before-commit')
            latest=self.output/'latest.json'
            if (latest.read_bytes() if latest.exists() else None)!=pending['previousLatest']:
                raise VaultError('latest-changed-during-sync')
            value={**pending['captureReceipt'],'sync_job_id':jobId,'import':{
                k:v for k,v in importResult.items() if k!='chat_candidates'},'imported_at':_now()}
            atomic_json(latest,value)
            self.last_commit={'jobId':jobId,'snapshot_id':value['snapshot_id'],'committed':True}
            self.pending=None
            # latest is the commit record. Ancillary status must not undo it.
            try:atomic_json(self.output/'pending-candidate.json',{**self.last_commit,'state':'committed','at':_now()})
            except OSError:pass
            return self.last_commit
        finally:self._release()

    def abort(self,*,jobId=None):
        if self.pending and jobId is not None and jobId!=self.pending['jobId']:
            raise VaultError('candidate-job-mismatch')
        pending=self.pending;self.pending=None
        try:
            if pending:
                try:atomic_json(self.output/'pending-candidate.json',{'jobId':pending['jobId'],'state':'aborted','at':_now()})
                except OSError:pass
            return {'aborted':bool(pending),'committed':False if pending else bool(self.last_commit)}
        finally:self._release()

    def request(self,request):
        try:
            if not isinstance(request,dict):raise VaultError('request-must-be-object')
            op=request.get('op');args=request.get('args') or {}
            if op not in ('collect','commit','abort'):raise VaultError('unsupported-operation')
            value=getattr(self,op)(**args)
            return {'id':request.get('id'),'ok':True,'value':value}
        except Exception as exc:
            self.abort()
            return {'id':request.get('id') if isinstance(request,dict) else None,'ok':False,
                    'error':str(exc) if isinstance(exc,VaultError) else type(exc).__name__}


def serve(lines,emit,worker):
    try:
        for line in lines:
            if len(line)>1024*1024:
                worker.abort();emit({'id':None,'ok':False,'error':'request-too-large'});continue
            try:request=json.loads(line)
            except ValueError:
                worker.abort();emit({'id':None,'ok':False,'error':'invalid-json'});continue
            emit(worker.request(request))
    finally:worker.abort()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',default=str(DEFAULT_ROOT));args=parser.parse_args()
    worker=CandidateWorker(args.root)
    serve(sys.stdin,lambda v:print(json.dumps(v,ensure_ascii=False),flush=True),worker)


if __name__=='__main__':main()
