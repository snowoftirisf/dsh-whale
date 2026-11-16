"""Exclusive, immutable local claims for the unregistered full friend candidate.

Construction is inert. Only claim/mark grant a one-shot hook result after actual
file flush/fsync and verification; readback is evidence, never new permission.
The trusted plan_provider(plan_id) must read the original durable plan store.
No UI, plan transition, migration, retry, external send or supplied file path.
File fsync is not an absolute hardware/power-loss guarantee, particularly for
Windows directory metadata. An interrupted record is retained and fails closed.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid
from attachment_policy import local_path
from data_paths import resolve_data_paths
from social_actions import _digest

def _fail(code):raise RuntimeError('social-friend-journal-'+code)
def _sha(value):return hashlib.sha256(value.encode('utf8')).hexdigest()
def _bytes(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')
def _token(value,length):return isinstance(value,str) and re.fullmatch('[a-f0-9]{'+str(length)+'}',value) is not None

class FriendRequestJournal:
    def __init__(self,dataRoot,plan_provider,owner_target,*,clock=time.time):
        # Lexical checks only: no resolve/lstat/provider calls or mkdir here.
        if (not isinstance(dataRoot,(str,Path)) or not Path(dataRoot).is_absolute()
                or str(dataRoot).startswith(('\\\\','//')) or '..' in Path(dataRoot).parts or '\0' in str(dataRoot)
                or not callable(plan_provider) or not callable(clock) or not isinstance(owner_target,str)
                or not owner_target.strip() or '\0' in owner_target or len(owner_target)>512):_fail('configuration-invalid')
        self.dataRoot=Path(dataRoot);self.provider=plan_provider;self.owner=owner_target;self.clock=clock
        self._claims={};self._mark_used=set();self._lock=threading.RLock()

    def _plan(self,plan_id,parameters_hash,*,require_claim):
        if not _token(plan_id,32) or not _token(parameters_hash,64):_fail('identity-invalid')
        try:first=copy.deepcopy(self.provider(plan_id));second=copy.deepcopy(self.provider(plan_id))
        except Exception:_fail('plan-unavailable')
        if type(first)is not dict or first!=second:_fail('plan-unstable')
        p=first;parameters=p.get('parameters')
        if (p.get('id')!=plan_id or p.get('hash')!=parameters_hash or p.get('action')!='add_friend'
                or p.get('origin')!={'kind':'friend','target':self.owner} or p.get('approvedBy')!=self.owner
                or not isinstance(p.get('approvalMessageId'),str) or not p['approvalMessageId'].strip()
                or type(parameters)is not dict or set(parameters)!={'keywords','expectedProfileName','addmsg'}
                or any(not isinstance(v,str) or '\0' in v for v in parameters.values())
                or not parameters['keywords'].strip() or not parameters['expectedProfileName'].strip()
                or _digest('add_friend',parameters)!=parameters_hash):_fail('plan-mismatch')
        if require_claim:
            expires=p.get('expiresAt');now=self.clock()
            if (p.get('status')!='claimed' or type(expires) not in (int,float) or not math.isfinite(expires)
                    or type(now) not in (int,float) or not math.isfinite(now) or now>=expires):_fail('claimed-plan-required')
        return {'ownerTargetSha256':_sha(self.owner),'approvalMessageIdSha256':_sha(p['approvalMessageId'])}

    def _path(self,plan_id,filename,*,create=False):
        paths=resolve_data_paths(root=self.dataRoot)
        directory=paths['config']/'friend-request-journal'/plan_id
        file=local_path(directory/filename)
        if not file.is_relative_to(paths['config']):_fail('path-denied')
        if create:
            directory.mkdir(parents=True,exist_ok=True)
            local_path(directory)
            if not directory.is_dir():_fail('path-denied')
        return file

    @staticmethod
    def _signature(s):return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_nlink)

    def _read(self,file,plan_id,hash_,stage,approval):
        try:
            file=local_path(file);before=file.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=32768:_fail('record-invalid')
            with file.open('rb') as stream:
                opened=os.fstat(stream.fileno())
                if self._signature(opened)!=self._signature(before):_fail('record-changed')
                data=stream.read(32769);after=os.fstat(stream.fileno())
            if self._signature(before)!=self._signature(after) or self._signature(before)!=self._signature(file.lstat()):_fail('record-changed')
            value=json.loads(data.decode('utf8'))
            if type(value)is not dict or set(value)!={'format','version','receipt','approval','checksum'}:_fail('record-invalid')
            body={k:v for k,v in value.items() if k!='checksum'}
            if value['format']!='whale-friend-request-journal' or type(value['version'])is not int or value['version']!=1 or _sha(_bytes(body).decode('utf8'))!=value['checksum']:_fail('record-invalid')
            r=value['receipt'];keys={'kind','planId','parametersHash','claimId','snapshotSha256','stage','persisted'}
            if (type(r)is not dict or set(r)!=keys or r.get('kind')!='friend-request-verification-journal'
                    or r.get('planId')!=plan_id or r.get('parametersHash')!=hash_ or r.get('stage')!=stage
                    or not _token(r.get('claimId'),32) or not _token(r.get('snapshotSha256'),64)
                    or r.get('persisted') is not True or value['approval']!=approval):_fail('record-identity-mismatch')
            return copy.deepcopy(r)
        except RuntimeError as error:
            if str(error).startswith('social-friend-journal-'):raise
            _fail('record-unavailable')
        except Exception:_fail('record-unavailable')

    def _create(self,file,receipt,approval):
        body={'format':'whale-friend-request-journal','version':1,'receipt':receipt,'approval':approval}
        data=_bytes({**body,'checksum':hashlib.sha256(_bytes(body)).hexdigest()})+b'\n'
        fd=None
        try:
            local_path(file)
            fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:
                fd=None;stream.write(data);stream.flush();os.fsync(stream.fileno())
            # POSIX can sync directory metadata; Windows does not offer the same
            # portable API. Do not claim absolute power-loss durability there.
            if os.name!='nt':
                dfd=os.open(file.parent,os.O_RDONLY)
                try:os.fsync(dfd)
                finally:os.close(dfd)
            return self._read(file,receipt['planId'],receipt['parametersHash'],receipt['stage'],approval)
        except FileExistsError:_fail('already-exists')
        except Exception as error:
            if isinstance(error,RuntimeError) and str(error).startswith('social-friend-journal-'):raise
            _fail('write-unconfirmed')
        finally:
            if fd is not None:os.close(fd)
            # Never unlink a partial/uncertain claim or mark and permit a retry.

    def claim(self,planId,parametersHash,snapshotSha256):
        with self._lock:
            if not _token(snapshotSha256,64):_fail('snapshot-invalid')
            approval=self._plan(planId,parametersHash,require_claim=True)
            file=self._path(planId,'claim.json',create=True)
            mark=self._path(planId,'mark.json')
            if mark.exists():_fail('already-exists')
            if self._plan(planId,parametersHash,require_claim=True)!=approval:_fail('plan-changed')
            receipt={'kind':'friend-request-verification-journal','planId':planId,'parametersHash':parametersHash,
                'claimId':uuid.uuid4().hex,'snapshotSha256':snapshotSha256,'stage':'claimed','persisted':True}
            saved=self._create(file,receipt,approval)
            if self._plan(planId,parametersHash,require_claim=True)!=approval:_fail('plan-changed')
            self._claims[(planId,parametersHash)]=copy.deepcopy(saved)
            return saved

    def mark(self,planId,parametersHash,claimId,snapshotSha256):
        with self._lock:
            approval=self._plan(planId,parametersHash,require_claim=True)
            owned=self._claims.get((planId,parametersHash));key=(planId,parametersHash)
            if (owned is None or key in self._mark_used or owned['claimId']!=claimId
                    or owned['snapshotSha256']!=snapshotSha256):_fail('claim-not-owned')
            self._mark_used.add(key)  # Failed mark attempts never receive another grant.
            disk=self._read(self._path(planId,'claim.json'),planId,parametersHash,'claimed',approval)
            if disk!=owned:_fail('claim-changed')
            if self._plan(planId,parametersHash,require_claim=True)!=approval:_fail('plan-changed')
            receipt={**disk,'stage':'confirm-attempt'}
            saved=self._create(self._path(planId,'mark.json'),receipt,approval)
            if self._plan(planId,parametersHash,require_claim=True)!=approval:_fail('plan-changed')
            return saved

    def readback(self,planId,parametersHash,claimId,stage):
        with self._lock:
            if stage not in ('claimed','confirm-attempt') or not _token(claimId,32):_fail('identity-invalid')
            approval=self._plan(planId,parametersHash,require_claim=False)
            claim=self._read(self._path(planId,'claim.json'),planId,parametersHash,'claimed',approval)
            if claim['claimId']!=claimId:_fail('claim-mismatch')
            if stage=='claimed':return claim
            mark=self._read(self._path(planId,'mark.json'),planId,parametersHash,'confirm-attempt',approval)
            if mark!={**claim,'stage':'confirm-attempt'}:_fail('claim-mismatch')
            return mark
