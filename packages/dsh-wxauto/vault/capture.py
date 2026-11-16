"""Explicit local Windows capture coordinator, with an OS-held sync lock.

Importing this module never scans processes or reads keys. Real capture requires
an explicit source selection and zstandard==0.25.0. UI/send operations do not
exist here. Tests inject a fake collector and never run production acquisition.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime,timezone
from .store import Vault,VaultError,DEFAULT_ROOT,atomic_json,required
from .snapshot_import import import_snapshot,_fingerprint
from .capture_selection import validate_audio_flag,select_capture_files,finalize_audio_capture
from .vendor import module_context,COMMIT

_HELD=set()
_HELD_LOCK=threading.Lock()


class SyncLock:
    """A persistent lock file, OS released on crash; never delete a PID lock."""
    def __init__(self,root):
        self.root=Path(root).resolve();self.path=self.root/'vault/sync.lock';self.handle=None

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with _HELD_LOCK:
            if str(self.path) in _HELD:raise VaultError('sync-busy')
            _HELD.add(str(self.path))
        try:
            if self.path.is_symlink():raise VaultError('sync-lock-link-denied')
            self.handle=self.path.open('a+b')
            self.handle.seek(0,2)
            if not self.handle.tell():self.handle.write(b'\0');self.handle.flush()
            self.handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            return self
        except BaseException as exc:
            if self.handle:self.handle.close();self.handle=None
            with _HELD_LOCK:_HELD.discard(str(self.path))
            if isinstance(exc,VaultError):raise
            raise VaultError('sync-busy') from exc

    def __exit__(self,*_):
        try:
            if self.handle:
                self.handle.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(),fcntl.LOCK_UN)
                self.handle.close();self.handle=None
        finally:
            with _HELD_LOCK:_HELD.discard(str(self.path))


def _now():return datetime.now(timezone.utc).isoformat()


def dependency_status():
    try:version=importlib.metadata.version('zstandard')
    except importlib.metadata.PackageNotFoundError:version=None
    return {'required':'0.25.0','installed':version,'ready':version=='0.25.0'}


# Database keys found by the last memory scan, kept only in this worker's memory
# (never written anywhere, so receipts still report keys_persisted=False).
# A full scan of the Weixin.exe process took 8-15s on every capture and was most
# of the delay before the whale saw a new message (2026-11-15).
_KEY_CACHE={}
_PAGE=4096


def _cached_key_extractor(extract,verify):
    def extract_keys(files,pids,seconds=90):
        def _scan():
            keys,statistics=extract(files,pids,seconds)
            _KEY_CACHE.clear();_KEY_CACHE.update(keys)
            return keys,statistics
        pages={}
        for path in files:
            with Path(path).open('rb') as handle:
                page=handle.read(_PAGE)
            if len(page)!=_PAGE:return _scan()
            pages[page[:16].hex()]=page
        cached={}
        for salt,page in pages.items():
            key=_KEY_CACHE.get(salt)
            if key is None or not verify(bytes.fromhex(key),page):
                return _scan()
            cached[salt]=key
        return cached,[{'cached':True,'matched_databases':len(cached)}]
    return extract_keys


# secure_output_root re-applies a protected, inheritable DACL to the whole vault
# on every capture; Windows then re-propagates it to every past snapshot (36k
# directories, 14.8s per capture by 2026-11-15). New children inherit the DACL
# anyway, so apply it once per worker process and only re-check the directory.
_SECURED={}


def _secure_once(secure):
    def secure_output_root(path):
        key=str(Path(path))
        done=_SECURED.get(key)
        if done is not None and Path(done).is_dir() and not Path(done).is_symlink():
            return done
        result=secure(path)
        _SECURED[key]=result
        return result
    return secure_output_root


class CaptureService:
    def __init__(self,data_root=DEFAULT_ROOT,*,collector=None,importer=import_snapshot):
        self.root=Path(data_root).expanduser().resolve()
        self.output=self.root/'snapshots/windows-live'
        self.collector=collector
        self.importer=importer

    @contextmanager
    def backend(self):
        if self.collector is not None:
            yield self.collector;return
        with module_context(capture=True) as modules:
            live=modules['windows_live'];native=modules['windows_native']
            # No source file is patched; runtime imports stay inside this context.
            original=getattr(live,'process_ids',None)
            live.process_ids=native.process_ids
            original_extract=live.extract_keys
            live.extract_keys=_cached_key_extractor(original_extract,modules['windows_crypto'].verify_enc_key)
            original_secure=native.secure_output_root
            native.secure_output_root=_secure_once(original_secure)
            try:yield live
            finally:
                live.extract_keys=original_extract
                native.secure_output_root=original_secure
                if original is None:del live.process_ids
                else:live.process_ids=original

    def discover(self,db_root=None):
        with self.backend() as collector:
            candidates=collector.discover(db_root)
            pids=collector.process_ids()
            return {'accounts':[{'source_id':collector.source_id(root),'db_root':str(root),'chat_databases':len(files)} for root,files in candidates],
                    'weixin_processes':pids,'memory_read':False,'source_commit':COMMIT}

    def status(self):
        latest=self.output/'latest.json';state=self.output/'sync-state.json'
        def read(file):
            if not file.exists():return None
            if file.is_symlink():raise VaultError('sync-state-link-denied')
            return json.loads(file.read_text(encoding='utf-8-sig'))
        with _HELD_LOCK:busy=str(self.root/'vault/sync.lock') in _HELD
        # busy is only this process's live lock state, never inferred from a file.
        return {'dataRoot':str(self.root),'snapshotRoot':str(self.output),'latest':read(latest),
                'lastAttempt':read(state),'busyInThisProcess':busy,'dependencies':dependency_status(),'source_commit':COMMIT}

    def capture_then_import(self,*,db_root,source_id,account_id,account_username=None,pids=None,
                            max_db_mib=512,seconds=90,scope='chat',include_audio=False):
        validate_audio_flag(include_audio,scope)
        required(source_id,'source-id');required(account_id,'account-id')
        if not isinstance(db_root,(str,Path)) or not str(db_root):raise VaultError('explicit-db-root-required')
        if not isinstance(max_db_mib,int) or isinstance(max_db_mib,bool) or not 1<=max_db_mib<=8192:
            raise VaultError('invalid-capture-byte-limit')
        if not isinstance(seconds,int) or isinstance(seconds,bool) or not 1<=seconds<=300:
            raise VaultError('invalid-capture-time-limit')
        if scope not in ('chat','all'):raise VaultError('invalid-capture-scope')
        if account_username is not None:required(account_username,'account-username')
        if self.collector is None and not dependency_status()['ready']:
            raise VaultError('zstandard-0.25.0-required')
        with SyncLock(self.root),self.backend() as collector:
            self.output.mkdir(parents=True,exist_ok=True)
            old_latest_path=self.output/'latest.json'
            previous_bytes=old_latest_path.read_bytes() if old_latest_path.exists() else None
            job=uuid.uuid4().hex
            state={'job_id':job,'state':'running','started_at':_now(),'source_id':source_id,'account_id':account_id}
            atomic_json(self.output/'sync-state.json',state)
            original_root=collector.private_root;original_json=collector.atomic_json
            vault=Vault(self.root)
            cursor_key='wechat-snapshot:'+account_id
            previous_cursor=vault.db.execute('SELECT cursor,updated_at FROM sync_state WHERE source=?',(cursor_key,)).fetchone()
            importer_ran=False
            staged=[]
            def publish_later(path,value):
                if Path(path).resolve()==old_latest_path.resolve():
                    staged.append(dict(value));return
                return original_json(path,value)
            collector.private_root=lambda:self.output
            collector.atomic_json=publish_later
            try:
                candidates=collector.discover(str(db_root))
                root,files=collector.choose_source(candidates,source_id)
                if collector.source_id(root)!=source_id:raise VaultError('capture-source-mismatch')
                files,audio_names=select_capture_files(collector,root,files,scope=scope,include_audio=include_audio)
                available=collector.process_ids()
                chosen=pids or available
                if not chosen or any(isinstance(pid,bool) or not isinstance(pid,int) or pid not in available for pid in chosen):
                    raise VaultError('authorized-weixin-process-required')
                receipt=collector.capture(root,files,chosen,max_bytes=max_db_mib*1024**2,seconds=seconds,scope=scope)
                if not isinstance(receipt,dict) or receipt.get('status') not in ('complete','complete_with_index_gaps'):
                    raise VaultError('capture-did-not-complete')
                snapshot=Path(receipt['snapshot']).resolve(strict=True)
                if not snapshot.is_relative_to(self.output.resolve()) or not snapshot.is_dir():
                    raise VaultError('capture-output-path-denied')
                if receipt.get('source_id')!=source_id or receipt.get('keys_persisted') is not False or receipt.get('read_only_source') is not True:
                    raise VaultError('capture-receipt-invalid')
                audio_files=finalize_audio_capture(snapshot,receipt,audio_names,original_json)
                audio_fingerprints=_fingerprint(audio_files)
                if account_username is not None:
                    # Identity comes from trusted routing, not path/name guessing.
                    manifest_file=snapshot/'snapshot-manifest.json'
                    manifest=json.loads(manifest_file.read_text(encoding='utf8'))
                    manifest['account_username']=account_username
                    original_json(manifest_file,manifest)
                importer_ran=True
                imported=self.importer(vault,snapshot,account_id)
                if not isinstance(imported,dict) or imported.get('complete') is not True:
                    raise VaultError('snapshot-import-did-not-complete')
                if _fingerprint(audio_files)!=audio_fingerprints:
                    raise VaultError('capture-audio-changed-during-import')
                value={**receipt,'import':{key:value for key,value in imported.items() if key!='chat_candidates'},'imported_at':_now()}
                # The upstream latest write was intercepted. Publish only after
                # capture AND import success; a failed attempt retains old latest.
                original_json(old_latest_path,value)
                state.update(state='complete',completed_at=_now(),snapshot_id=receipt.get('snapshot_id'))
                atomic_json(self.output/'sync-state.json',state)
                return {**value,'chat_candidates':imported.get('chat_candidates',[])}
            except BaseException as exc:
                if importer_ran:
                    # Publish/import is one logical checkpoint. Message rows may
                    # remain for idempotent retry, but a failed job never advances
                    # this account's success cursor (including publish failure).
                    with vault.lock,vault.db:
                        if previous_cursor is None:
                            vault.db.execute('DELETE FROM sync_state WHERE source=?',(cursor_key,))
                        else:
                            vault.db.execute('INSERT INTO sync_state VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor,updated_at=excluded.updated_at',
                                (cursor_key,previous_cursor['cursor'],previous_cursor['updated_at']))
                # Defensive guard for injected/custom collectors that publish an
                # unexpected pointer instead of using their atomic_json helper.
                current=old_latest_path.read_bytes() if old_latest_path.exists() else None
                if current!=previous_bytes:
                    if previous_bytes is None:
                        # Remove only this job's newly created success pointer;
                        # validated snapshots/failed artifacts themselves remain.
                        old_latest_path.unlink()
                    else:
                        temporary=old_latest_path.with_name('.latest-restore-'+job+'.tmp')
                        temporary.write_bytes(previous_bytes);os.replace(temporary,old_latest_path)
                state.update(state='failed',completed_at=_now(),error=type(exc).__name__)
                atomic_json(self.output/'sync-state.json',state)
                raise
            finally:
                collector.private_root=original_root;collector.atomic_json=original_json
                vault.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',default=str(DEFAULT_ROOT))
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('discover');p.add_argument('--db-root')
    sub.add_parser('status')
    p=sub.add_parser('capture-then-import');p.add_argument('--db-root',required=True);p.add_argument('--source-id',required=True)
    p.add_argument('--account-id',required=True);p.add_argument('--account-username');p.add_argument('--pid',type=int,action='append',dest='pids')
    p.add_argument('--scope',choices=['chat','all'],default='chat');p.add_argument('--seconds',type=int,default=90);p.add_argument('--max-db-mib',type=int,default=512)
    p.add_argument('--include-audio',action='store_true')
    options=parser.parse_args();service=CaptureService(options.root)
    try:
        if options.command=='status':value=service.status()
        elif options.command=='discover':value=service.discover(options.db_root)
        else:
            args=vars(options).copy();args.pop('root');args.pop('command');value=service.capture_then_import(**args)
        print(json.dumps({'ok':True,'value':value},ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc) if isinstance(exc,VaultError) else type(exc).__name__},ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
