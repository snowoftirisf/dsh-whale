"""Per-group text authority. Inert construction; no UI, sends or approvals.

Preparing retains the original SDK envelopes. A durable transition prevents a
worker restart from silently falling back to UI after an uncertain cutover.
Only committed native authority permits the monitor to skip SDK parsing.
"""
import copy
import hashlib
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from attachment_policy import local_path
from data_paths import absolute_data_path
from native_owner_text_claim import _read, _compact, _hash, _integer, _string

MESSAGE_FIELDS = ('nativeId','serverId','nativeChatKey','senderId','senderDisplayName',
    'senderNumericId','rawText','renderedText','renderStatus','timestampSeconds',
    'localId','snapshotSourceId','direction')
UNRESOLVED_FIELDS = ('nativeId','serverId','nativeChatKey','rawText','timestampSeconds',
    'localId','snapshotSourceId','senderNumericId','reason','direction')
KEY_RE = re.compile(r'wechat:[A-Za-z0-9_-]{1,256}@chatroom')


class NativeGroupTextAuthorityError(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise NativeGroupTextAuthorityError('native-group-text-authority-' + code)


def group_text_directory(config, key):
    need(isinstance(key,str) and KEY_RE.fullmatch(key), 'group-key-invalid')
    suffix=hashlib.sha256(key.encode('utf8')).hexdigest()[:32]
    return absolute_data_path('config/native-group-text/'+suffix,root=config['dataRoot'])


def trusted_group_route(config, key):
    """Current exact native route, never an owner/private or display-name lookup."""
    need(type(config)is dict and isinstance(key,str) and KEY_RE.fullmatch(key),'settings-invalid')
    need(_string(config.get('accountId')) and Path(config.get('dataRoot','')).is_absolute(),'settings-invalid')
    value,_=_read(absolute_data_path('config/group-routes.local.json',root=config['dataRoot']))
    need(type(value.get('version'))is int and value['version']==1 and value.get('accountId')==config['accountId'] and type(value.get('groups'))is list,'registry-invalid')
    rows=[r for r in value['groups'] if type(r)is dict and r.get('nativeChatKey')==key]
    need(len(rows)==1,'route-ambiguous')
    route=rows[0]
    need(route.get('accountId')==config['accountId'] and route.get('status')=='active'
         and all(_string(route.get(k)) for k in ('target','sessionId','chatId')),'route-inactive')
    need(sum(r.get('target')==route['target'] for r in value['groups'] if type(r)is dict and r.get('status')=='active')==1,'target-ambiguous')
    return copy.deepcopy(route)


def normalized_catalog(catalog):
    """Match the Node contract's fixed field order and ASCII identity ordering."""
    messages=[{k:r[k] for k in MESSAGE_FIELDS} for r in catalog['messages']]
    unresolved=[{k:r[k] for k in UNRESOLVED_FIELDS} for r in catalog['unresolved']]
    unresolved_key=lambda r:r['nativeId'] if r['nativeId'] is not None else _compact([r['snapshotSourceId'],r['localId']]).decode('utf8')
    return [catalog['snapshotId'],sorted(messages,key=lambda r:r['nativeId']),
            sorted(unresolved,key=unresolved_key),sorted(catalog['observedNativeIds'])]


class NativeGroupTextAuthority:
    def __init__(self, config, native_chat_key, *, route_reader=trusted_group_route,
                 catalog_reader=None, clock=time.time, max_deferred=256):
        need(type(config)is dict and isinstance(native_chat_key,str) and KEY_RE.fullmatch(native_chat_key) and callable(clock)
             and type(max_deferred)is int and 1<=max_deferred<=4096,'config-invalid')
        self.config,self.key,self.route_reader=config,native_chat_key,route_reader
        self.catalog_reader,self.clock,self.max_deferred=catalog_reader,clock,max_deferred
        self.mode,self.epoch,self.ticket='unrestored',None,None
        self.prepared_at_ms=None
        self.deferred=[]
        self.dirty=False
        self._lock=threading.RLock()
        self._abort_pending=False
        self._prepared_settings=None
        self._active_settings=None
        self._lease_fd=None
        self._lease_token=None

    def paths(self):
        folder=group_text_directory(self.config,self.key)
        return folder,folder/'authority.json',folder/'inbox.json',folder/'transition.json'

    def settings(self):
        capture=self.config.get('capture',{})
        need(capture.get('enabled')is True and _string(capture.get('accountUsername'),256)
             and _string(capture.get('sourceId'),256),'capture-required')
        route=self.route_reader(self.config,self.key)
        return {'accountId':self.config['accountId'],'nativeChatKey':self.key,
            'captureAccountUsername':capture['accountUsername'],'captureSourceId':capture['sourceId'],
            'sessionId':route['sessionId'],'chatId':route['chatId'],'target':route['target']}

    def status(self):
        if self.mode=='native':
            try:
                current=self.settings()
                need(self._active_settings is not None and all(current[k]==self._active_settings[k]
                    for k in('accountId','nativeChatKey','captureAccountUsername','captureSourceId','sessionId','chatId')),'route-changed')
            except Exception:self.mode='blocked'
        value={'version':1,'accountId':self.config.get('accountId'),'nativeChatKey':self.key,
            'mode':self.mode,'authorityEpoch':self.epoch,'ticket':self.ticket,
            'dirty':self.dirty,'deferred':len(self.deferred),'readsStopped':self.mode=='blocked'}
        try:value['target']=self.route_reader(self.config,self.key)['target']
        except Exception:value['routeInactive']=True
        if self.mode=='preparing':value['preparedAtMs']=self.prepared_at_ms
        return value

    def _now(self):
        now=self.clock()
        need(type(now)in(int,float) and math.isfinite(now) and _integer(int(now*1000)),'clock-invalid')
        return int(now*1000)

    def _signed(self, value, format):
        need(type(value)is dict,'record-invalid')
        body={k:v for k,v in value.items()if k!='digest'}
        need(value.get('digest')==_hash(body) and body.get('format')==format and type(body.get('version'))is int and body['version']==1
             and body.get('accountId')==self.config.get('accountId') and body.get('nativeChatKey')==self.key,'record-invalid')
        return body

    def _transition(self, mode, *, drained=0):
        folder,_,_,file=self.paths()
        local_path(folder).mkdir(parents=True,exist_ok=True)
        before=None
        if file.exists():
            old,before=_read(file);old=self._signed(old,'whale-native-group-text-transition')
            need(old.get('mode')=='ui' if mode=='preparing' else old.get('mode')=='preparing' and old.get('ticket')==self.ticket,
                'transition-state-conflict')
        value={'format':'whale-native-group-text-transition','version':1,'accountId':self.config['accountId'],
            'nativeChatKey':self.key,'mode':mode,'ticket':self.ticket,'preparedAtMs':self.prepared_at_ms,
            'dirty':self.dirty,'drained':drained,'atMs':self._now()}
        value['digest']=_hash(value)
        staging=local_path(folder/(uuid.uuid4().hex+'.transition.tmp'))
        with staging.open('xb')as stream:stream.write(_compact(value));stream.flush();os.fsync(stream.fileno())
        need((_read(file)[1]if file.exists()else None)==before,'transition-changed')
        os.replace(staging,file)
        need(_read(file)[0]==value,'transition-readback-failed')

    def _acquire_lease(self):
        folder=self.paths()[0];local_path(folder).mkdir(parents=True,exist_ok=True)
        file=local_path(folder/'transition.lock')
        need(self._lease_fd is None,'lease-already-held')
        fd=os.open(file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        self._lease_fd=fd
        payload=_compact({'ticket':self.ticket,'accountId':self.config['accountId'],'nativeChatKey':self.key})
        with os.fdopen(os.dup(fd),'wb')as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
        self._lease_token=_read(file)[1]

    def _release_lease(self):
        need(self._lease_fd is not None and self._lease_token is not None,'lease-unverified')
        file=self.paths()[0]/'transition.lock'
        need(_read(file)[1]==self._lease_token,'lease-changed')
        os.close(self._lease_fd);self._lease_fd=None
        need(_read(file)[1]==self._lease_token,'lease-changed')
        local_path(file).unlink();self._lease_token=None

    def close(self):
        """An unknown preparation keeps its lock and journal for recovery."""
        if self._lease_fd is not None:os.close(self._lease_fd);self._lease_fd=None
        if self.mode=='preparing':self.mode='blocked'

    def _inbox(self, epoch, *, baseline=False):
        _,_,file,_=self.paths();value,token=_read(file)
        body=self._signed(value,'whale-native-group-text-inbox')
        need(body.get('authorityEpoch')==epoch and body.get('scopeDigest')==_hash([self.config['accountId'],self.key])
            and _integer(body.get('revision')) and _integer(body.get('cutoverAtMs'))
            and body.get('cutoverFloorSeconds')==(body['cutoverAtMs']+999)//1000
            and type(body.get('claims'))is list and type(body.get('unresolvedSourceRows'))is list
            and type(body.get('observedNativeIds'))is list,'inbox-invalid')
        if baseline:
            need(body['revision']==1 and all(c.get('firstSeenMode')=='baseline' and c.get('state')in('baseline','held')for c in body['claims'])
                and all(c.get('firstSeenMode')=='baseline'for c in body['unresolvedSourceRows']),'baseline-required')
            need(all(c.get('resolution')in('resolved','unresolved')for c in body['claims'])
                and all(c.get('state')=='baseline' if c.get('resolution')=='resolved' else c.get('state')=='held'for c in body['claims']),
                'baseline-required')
        return body,token

    def restore(self):
        with self._lock:
            need(self.mode=='unrestored','restore-once')
            try:
                _,authority,_,transition=self.paths()
                if not authority.exists():
                    need(not (self.paths()[0]/'transition.lock').exists(),'pending-lease')
                    if transition.exists():
                        state=self._signed(_read(transition)[0],'whale-native-group-text-transition')
                        need(state.get('mode')=='ui','pending-transition')
                    self.mode='ui';return self.status()
                settings=self.settings();body=self._signed(_read(authority)[0],'whale-native-group-text-authority')
                need(body.get('active')is True and re.fullmatch('[a-f0-9]{32}',body.get('authorityEpoch','')),'record-invalid')
                need(all(body.get(k)==settings[k] for k in('accountId','nativeChatKey','captureAccountUsername','captureSourceId','sessionId','chatId')),'route-changed')
                inbox,_=self._inbox(body['authorityEpoch'])
                need(all(inbox.get(k)==body.get(k)for k in('baselineSnapshotId','baselineCatalogDigest','cutoverAtMs','cutoverFloorSeconds')),'inbox-mismatch')
                need(inbox['revision']>=body.get('initialInboxRevision',0) and type(body.get('initialInboxRevision'))is int and body['initialInboxRevision']==1,'inbox-mismatch')
                if inbox['revision']==1:need(_read(self.paths()[2])[1][0]==body['initialInboxSha256'],'inbox-mismatch')
                self._active_settings=settings
                self.mode,self.epoch='native',body['authorityEpoch'];return self.status()
            except Exception:
                self.mode='blocked'
                raise NativeGroupTextAuthorityError('native-group-text-authority-restore-unverified')from None

    def prepare(self):
        with self._lock:
            need(self.mode=='ui','ui-required');self._prepared_settings=self.settings()
            self.ticket,self.prepared_at_ms,self.dirty=uuid.uuid4().hex,self._now(),False
            try:self._acquire_lease();self._transition('preparing')
            except Exception:self.mode='blocked';raise
            self.mode='preparing';return self.status()

    def route_event(self,event):
        with self._lock:
            # Default UI and all other modalities retain the old receive path,
            # including installations without native capture configured.
            if self.mode=='ui' or type(event)is not dict or event.get('kind')!='group' or event.get('mediaType')!='text' or any(event.get(k)for k in('images','files','audio')):
                return {'action':'pass','event':copy.deepcopy(event)}
            if self.mode=='native':self.status()
            settings=self.settings()
            if self.mode=='preparing':need(settings==self._prepared_settings,'route-changed')
            scoped=(type(event)is dict and event.get('target')==settings['target'] and event.get('kind')=='group'
                and event.get('mediaType')=='text' and not any(event.get(k)for k in('images','files','audio')))
            if not scoped or self.mode=='ui':return {'action':'pass','event':copy.deepcopy(event)}
            if self.mode=='native':return {'action':'hint','authorityEpoch':self.epoch}
            if self.mode=='preparing':
                self.dirty=True
                if len(self.deferred)>=self.max_deferred:self.mode='blocked';return {'action':'blocked'}
                self.deferred.append(copy.deepcopy(event));return {'action':'deferred'}
            return {'action':'blocked'}

    def abort(self,ticket):
        with self._lock:
            need(self.mode=='preparing' and ticket==self.ticket,'ticket-invalid')
            self._abort_pending=True
            return {'events':copy.deepcopy(self.deferred),'dirty':self.dirty}

    def finish_abort(self,ticket,drained):
        with self._lock:
            need(self.mode=='preparing' and self._abort_pending and ticket==self.ticket and drained==len(self.deferred),'abort-unverified')
            try:self._transition('ui',drained=drained);self._release_lease()
            except Exception:self.mode='blocked';raise
            self.mode,self.ticket,self.prepared_at_ms,self.dirty='ui',None,None,False
            self.deferred=[];self._abort_pending=False
            self._prepared_settings=None
            return self.status()

    def commit(self,ticket,epoch):
        with self._lock:
            need(self.mode=='preparing' and ticket==self.ticket and not self._abort_pending,'ticket-invalid')
            need(not self.dirty and not self.deferred,'not-quiet')
            need(isinstance(epoch,str) and re.fullmatch('[a-f0-9]{32}',epoch),'epoch-invalid')
            settings=self.settings();need(settings==self._prepared_settings,'route-changed')
            transition=self._signed(_read(self.paths()[3])[0],'whale-native-group-text-transition')
            need(transition.get('mode')=='preparing' and transition.get('ticket')==ticket,'transition-state-conflict')
            need(self._lease_token is not None and _read(self.paths()[0]/'transition.lock')[1]==self._lease_token,'lease-unverified')
            inbox,token=self._inbox(epoch,baseline=True)
            need(inbox['cutoverAtMs']==self.prepared_at_ms,'prepare-boundary-mismatch')
            sid=inbox.get('baselineSnapshotId');need(isinstance(sid,str) and str(uuid.UUID(sid))==sid,'baseline-proof-required')
            reader=self.catalog_reader
            if reader is None:
                from native_group_text_catalog import list_native_group_texts
                reader=list_native_group_texts
            catalog=reader(absolute_data_path('snapshots/windows-live/snapshots/'+sid,root=self.config['dataRoot']),
                expected_account_username=settings['captureAccountUsername'],expected_source_id=settings['captureSourceId'],
                account_id=settings['accountId'],native_group_chat_key=self.key,expected_snapshot_id=sid)
            need(catalog.get('source')=='authenticated-native-group-text-catalog' and catalog.get('complete')is True
                and catalog.get('accountId')==settings['accountId'] and catalog.get('nativeChatKey')==self.key
                and catalog.get('scopeDigest')==inbox['scopeDigest'] and catalog.get('snapshotId')==sid,'catalog-unverified')
            normalized=normalized_catalog(catalog)
            need(_hash(normalized)==inbox.get('baselineCatalogDigest'),'baseline-proof-mismatch')
            resolved=[{k:r[k]for k in MESSAGE_FIELDS}for r in inbox['claims']if r.get('resolution')=='resolved']
            unknown=[{k:r[k]for k in UNRESOLVED_FIELDS}for r in inbox['claims']if r.get('resolution')=='unresolved']
            unknown += [{k:r[k]for k in UNRESOLVED_FIELDS}for r in inbox['unresolvedSourceRows']]
            proof={'snapshotId':sid,'messages':resolved,'unresolved':unknown,'observedNativeIds':inbox['observedNativeIds']}
            need(normalized_catalog(proof)==normalized,'baseline-claims-mismatch')
            folder,file,inbox_file,_=self.paths()
            value={'format':'whale-native-group-text-authority','version':1,'active':True,**settings,
                'authorityEpoch':epoch,'initialInboxSha256':token[0],'initialInboxRevision':1,
                'baselineSnapshotId':sid,'baselineCatalogDigest':inbox['baselineCatalogDigest'],
                'cutoverAtMs':inbox['cutoverAtMs'],'cutoverFloorSeconds':inbox['cutoverFloorSeconds'],'createdAtMs':self._now()}
            need(value['createdAtMs']>=value['cutoverAtMs'] and self.settings()==settings and _read(inbox_file)[1]==token,'source-changed')
            value['digest']=_hash(value)
            try:
                with local_path(file).open('xb')as stream:stream.write(_compact(value));stream.flush();os.fsync(stream.fileno())
                need(_read(file)[0]==value and _read(inbox_file)[1]==token,'commit-readback-failed')
                self._release_lease()
            except Exception:
                self.mode='blocked';raise NativeGroupTextAuthorityError('native-group-text-authority-commit-unknown')from None
            self._active_settings=settings;self._prepared_settings=None
            self.mode,self.epoch,self.ticket='native',epoch,None;self.prepared_at_ms=None
            return self.status()
