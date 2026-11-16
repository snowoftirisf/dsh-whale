"""First native-text authority for a verified new-group unread handoff.

No UI calls, capture, model, normal binding registration or generic commit change.
The default UI verifier accepts only the worker's registered-handoff capability.
"""
import copy,hashlib,re,uuid
from attachment_policy import local_path
from native_owner_text_claim import _read,_compact,_hash,_integer
from native_group_text_authority import NativeGroupTextAuthority,NativeGroupTextAuthorityError,normalized_catalog,MESSAGE_FIELDS,UNRESOLVED_FIELDS
from native_group_text_admission_source import revalidate_pending_text_admission_source


def need(ok,code):
    if not ok:raise NativeGroupTextAuthorityError('native-group-text-admit-'+code)

def text_sha(value):return hashlib.sha256(value.encode('utf8')).hexdigest()
def claim_id(account,key,server):return _hash([account,key,server])[:32]


def trusted_ui_verifier(config,ui_proof,source_proof):
    try:
        from native_group_ui_binding import verify_native_group_text_ui_handoff
        return verify_native_group_text_ui_handoff(config,ui_proof,source_proof)
    except Exception:raise NativeGroupTextAuthorityError('native-group-text-admit-ui-proof-required')from None


class NativeGroupTextAdmissionAuthority(NativeGroupTextAuthority):
    def __init__(self,config,native_chat_key,*,ui_proof_verifier=None,**kwargs):
        super().__init__(config,native_chat_key,**kwargs)
        need(ui_proof_verifier is None or callable(ui_proof_verifier),'ui-verifier-invalid')
        self.ui_proof_verifier=ui_proof_verifier or trusted_ui_verifier
        self._admission=None

    def _verify_ui(self,ui_proof,source):
        verified=self.ui_proof_verifier(self.config,ui_proof,source)
        need(verified is not None and type(verified)is not bool and not isinstance(verified,dict),'ui-proof-required')
        actual_key=getattr(verified,'key',getattr(verified,'native_chat_key',None))
        need(all(getattr(verified,field,None)==value for field,value in (
            ('account_id',source.account_id),('target',source.target),('session_id',source.session_id),
            ('claim_id',source.claim_id),('claim_hash',source.claim_hash),('snapshot_id',source.snapshot_id)))
            and actual_key==source.native_chat_key,'ui-proof-scope-invalid')
        return verified

    def _admission_record(self,marker,inbox):
        a=marker.get('admission')
        if a is None:return None
        expected=inbox.get('admission')
        need(type(a)is dict and type(expected)is dict and a==expected,'marker-admission-mismatch')
        need(type(a.get('version'))is int and a['version']==1 and re.fullmatch('[a-f0-9]{32}',a.get('rawClaimId',''))
             and re.fullmatch('[a-f0-9]{64}',a.get('rawClaimHash',''))and a.get('snapshotId')==marker.get('baselineSnapshotId')
             and a.get('authorityEpoch')==marker.get('authorityEpoch'),'marker-admission-invalid')
        return copy.deepcopy(a)

    def restore(self):
        value=super().restore()
        if self.mode=='native':
            try:
                marker=self._signed(_read(self.paths()[1])[0],'whale-native-group-text-authority')
                inbox,_=self._inbox(self.epoch)
                self._admission=self._admission_record(marker,inbox)
            except Exception:
                self.mode='blocked'
                raise NativeGroupTextAuthorityError('native-group-text-admit-restore-unverified')from None
        return self.status()

    def status(self):
        value=super().status()
        if value['mode']=='native':
            try:
                marker=self._signed(_read(self.paths()[1])[0],'whale-native-group-text-authority')
                need(marker.get('authorityEpoch')==self.epoch,'status-marker-mismatch')
                a=marker.get('admission')
                if a is not None:
                    need(type(a)is dict and re.fullmatch('[a-f0-9]{32}',a.get('rawClaimId',''))
                        and re.fullmatch('[a-f0-9]{64}',a.get('rawClaimHash',''))and a.get('snapshotId')==marker.get('baselineSnapshotId')
                        and a.get('authorityEpoch')==self.epoch,'status-admission-invalid')
                    need(self._admission is None or a==self._admission,'status-admission-changed')
                    self._admission=copy.deepcopy(a)
                    value['admission']={k:a[k]for k in ('rawClaimId','rawClaimHash','snapshotId')}
            except Exception:
                self.mode='blocked';self._admission=None;value=super().status()
        return value

    def _validate_adoption(self,source,epoch,inbox,catalog):
        q=source.queued;key=self.key;account=self.config['accountId'];a=inbox.get('admission')
        need(type(inbox.get('revision'))is int and inbox['revision']==1,'initial-inbox-required')
        need(type(a)is dict and type(a.get('version'))is int and a['version']==1 and a.get('rawClaimId')==source.claim_id
             and a.get('rawClaimHash')==source.claim_hash and a.get('snapshotId')==source.snapshot_id and a.get('authorityEpoch')==epoch,'inbox-admission-invalid')
        handled=q.get('handledServerIds',[])
        need(type(handled)is list and len(handled)==len(set(handled))and all(isinstance(x,str)and re.fullmatch('[1-9][0-9]*',x)for x in handled),'handled-ids-invalid')
        texts=[m for m in q['messages']if m.get('type')=='text']
        fresh=[m for m in texts if m['serverId']not in handled]
        need(bool(fresh)and all(m.get('contextOnly')is False for m in fresh),'no-new-text-source')
        ids=[m['nativeId']for m in fresh];new_ids=[claim_id(account,key,m['serverId'])for m in fresh]
        need(a.get('unreadNativeIds')==ids and a.get('newTextClaimIds')==new_ids,'unread-admission-mismatch')
        # Native timestamps have whole-second precision. Known catalog rows are
        # already baseline; later unseen rows in this same second must survive.
        need(inbox.get('cutoverAtMs')==min(m['timestampSeconds'] for m in fresh)*1000,'native-cutoff-mismatch')
        need(catalog.get('source')=='authenticated-native-group-text-catalog'and catalog.get('complete')is True
             and catalog.get('accountId')==account and catalog.get('nativeChatKey')==key and catalog.get('snapshotId')==source.snapshot_id
             and catalog.get('scopeDigest')==inbox['scopeDigest'],'catalog-unverified')
        normal=normalized_catalog(catalog)
        need(_hash(normal)==inbox.get('baselineCatalogDigest')and inbox.get('baselineSnapshotId')==source.snapshot_id,'baseline-proof-mismatch')
        resolved=[{k:r[k]for k in MESSAGE_FIELDS}for r in inbox['claims']if r.get('resolution')=='resolved']
        unknown=[{k:r[k]for k in UNRESOLVED_FIELDS}for r in inbox['claims']if r.get('resolution')=='unresolved']
        unknown +=[{k:r[k]for k in UNRESOLVED_FIELDS}for r in inbox['unresolvedSourceRows']]
        need(normalized_catalog({'snapshotId':source.snapshot_id,'messages':resolved,'unresolved':unknown,'observedNativeIds':inbox['observedNativeIds']})==normal,'baseline-claims-mismatch')
        by_id={m['nativeId']:m for m in catalog['messages']};claims={c['nativeId']:c for c in inbox['claims']}
        need(len(claims)==len(inbox['claims']),'duplicate-inbox-identity')
        for c in inbox['claims']:
            need(c.get('claimId')==claim_id(account,key,c['serverId'])and c.get('snapshotId')==source.snapshot_id
                 and _integer(c.get('createdAt'))and c['createdAt']<=self._now()and 'submittedAt'not in c,'claim-envelope-invalid')
            if c['nativeId']in ids:
                need(c.get('resolution')=='resolved'and c.get('state')=='queued'and c.get('firstSeenMode')=='delegated-unread','unread-claim-not-queued')
                need(c['timestampSeconds']<=self._now()//1000+30,'future-source')
            else:
                need(c.get('firstSeenMode')=='baseline'and c.get('state')==('baseline'if c.get('resolution')=='resolved'else'held'),'old-source-not-baseline')
        need(all(c.get('firstSeenMode')=='baseline'for c in inbox['unresolvedSourceRows']),'source-diagnostics-invalid')
        receipt=q.get('textDelegation')
        need(type(receipt)is dict and receipt.get('format')=='whale-native-group-text-delegation'and type(receipt.get('version'))is int and receipt['version']==1
             and receipt.get('accountId')==account and receipt.get('nativeChatKey')==key and receipt.get('claimId')==source.claim_id
             and receipt.get('claimHash')==source.claim_hash and receipt.get('authorityEpoch')==epoch and receipt.get('ownershipMode')=='new-admission'
             and receipt.get('newTextClaimIds')==new_ids and type(receipt.get('texts'))is list and len(receipt['texts'])==len(texts),'delegation-required')
        need(receipt.get('digest')==_hash([account,key,source.claim_id,source.claim_hash,epoch,'new-admission',receipt['texts'],new_ids]),'delegation-digest-invalid')
        seen=set()
        for item in receipt['texts']:
            native=item.get('nativeId');original=next((m for m in texts if m['nativeId']==native),None);actual=by_id.get(native);owned=claims.get(native)
            need(original is not None and actual is not None and owned is not None and native not in seen,'delegation-source-invalid');seen.add(native)
            expected={'claimId':owned['claimId'],'serverId':actual['serverId'],'nativeId':native,'nativeChatKey':key,'state':owned['state'],
                'senderId':actual['senderId'],'timestampSeconds':actual['timestampSeconds'],'rawSha256':text_sha(actual['rawText']),
                'queuedTextSha256':text_sha(original['text']),'snapshotSourceId':actual['snapshotSourceId'],'localId':actual['localId']}
            need(item==expected and actual['serverId']==original['serverId']and actual['senderId']==original['senderId']
                and actual['timestampSeconds']==original['timestampSeconds']and actual['snapshotSourceId']==original['snapshotSourceId']
                and ('localId'not in original or original['localId']==actual['localId']),'delegation-source-invalid')
        return copy.deepcopy(a)

    def admit(self,source_proof,epoch,ui_proof):
        with self._lock:
            if self.mode=='unrestored':self.restore()
            need(self.mode=='ui'and not self.paths()[1].exists(),'first-admission-only')
            need(isinstance(epoch,str)and re.fullmatch('[a-f0-9]{32}',epoch),'epoch-invalid')
            source=revalidate_pending_text_admission_source(self.config,source_proof)
            need(source.native_chat_key==self.key and source.account_id==self.config['accountId'],'source-scope-invalid')
            self._verify_ui(ui_proof,source)
            settings=self.settings()  # Must already be active in the durable registry.
            need(settings['target']==source.target and settings['sessionId']==source.session_id and settings['chatId']==source.chat_id,'route-changed')
            inbox,token=self._inbox(epoch)
            need(_integer(inbox.get('cutoverAtMs'))and 0<inbox['cutoverAtMs']<=self._now(),'cutoff-invalid')
            reader=self.catalog_reader
            if reader is None:
                from native_group_text_catalog import list_native_group_texts
                reader=list_native_group_texts
            catalog=reader(source.snapshot_path,**source.catalog_arguments)
            admission=self._validate_adoption(source,epoch,inbox,catalog)
            revalidate_pending_text_admission_source(self.config,source);self._verify_ui(ui_proof,source)
            need(self.settings()==settings and _read(self.paths()[2])[1]==token,'source-changed')
            self.ticket=uuid.uuid4().hex;self.prepared_at_ms=inbox['cutoverAtMs'];self._prepared_settings=settings
            try:self._acquire_lease();self._transition('preparing');self.mode='preparing'
            except Exception:
                self.mode='blocked';raise NativeGroupTextAuthorityError('native-group-text-admit-lease-unknown')from None
            try:
                revalidate_pending_text_admission_source(self.config,source);self._verify_ui(ui_proof,source)
                need(self.settings()==settings and _read(self.paths()[2])[1]==token,'source-changed')
                value={'format':'whale-native-group-text-authority','version':1,'active':True,**settings,'authorityEpoch':epoch,
                    'initialInboxSha256':token[0],'initialInboxRevision':1,'baselineSnapshotId':source.snapshot_id,
                    'baselineCatalogDigest':inbox['baselineCatalogDigest'],'cutoverAtMs':inbox['cutoverAtMs'],
                    'cutoverFloorSeconds':inbox['cutoverFloorSeconds'],'createdAtMs':self._now(),'admission':admission}
                value['digest']=_hash(value)
                with local_path(self.paths()[1]).open('xb')as stream:
                    stream.write(_compact(value));stream.flush()
                    import os
                    os.fsync(stream.fileno())
                need(_read(self.paths()[1])[0]==value and _read(self.paths()[2])[1]==token,'commit-readback-failed')
                self._release_lease()
            except Exception:
                self.mode='blocked';raise NativeGroupTextAuthorityError('native-group-text-admit-commit-unknown')from None
            self._admission=admission;self._active_settings=settings;self._prepared_settings=None
            self.mode,self.epoch,self.ticket='native',epoch,None;self.prepared_at_ms=None
            return self.status()
