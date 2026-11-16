"""Read-only admission source for one durable queued native group claim.

This is native source evidence only: no UI identity, authority activation,
submission, baseline initialization or one-use execution permission is granted.
"""
from dataclasses import dataclass,field
import copy,hashlib,json,re,uuid
from pathlib import Path
from attachment_policy import local_path
from data_paths import absolute_data_path
from native_owner_text_claim import _read,_compact
from native_group_handoff import _file,claim_hash,verify_queued_handoff

_TOKEN=object()


def _need(value,code):
    if not value:raise RuntimeError('native-group-text-admission-'+code)


@dataclass(frozen=True)
class PendingTextAdmissionSource:
    data_root:str
    account_id:str
    native_chat_key:str
    account_username:str
    source_id:str
    target:str
    session_id:str
    chat_id:str
    claim_id:str
    claim_hash:str
    snapshot_id:str
    _queued_json:bytes=field(repr=False)
    _route_json:bytes=field(repr=False)
    _token:object=field(repr=False,compare=False)

    @property
    def queued(self):return json.loads(self._queued_json)
    @property
    def route(self):return json.loads(self._route_json)
    @property
    def snapshot_path(self):return Path(self.data_root)/'snapshots/windows-live/snapshots'/self.snapshot_id
    @property
    def catalog_arguments(self):
        return {'expected_account_username':self.account_username,'expected_source_id':self.source_id,
            'account_id':self.account_id,'native_group_chat_key':self.native_chat_key,'expected_snapshot_id':self.snapshot_id}


def verify_pending_text_admission_source(config,native_chat_key,claim_id,claim_hash_expected):
    try:
        _need(type(config)is dict and isinstance(config.get('dataRoot'),str)and Path(config['dataRoot']).is_absolute(),'configuration-invalid')
        root=local_path(config['dataRoot']);account=config.get('accountId');capture=config.get('capture')
        _need(isinstance(account,str)and account and type(capture)is dict and all(isinstance(capture.get(k),str)and capture[k]for k in('accountUsername','sourceId'))
              and all(isinstance(config.get(k),str)and config[k]for k in('target','sessionId')),'configuration-invalid')
        _need(isinstance(native_chat_key,str)and re.fullmatch(r'wechat:[A-Za-z0-9_-]{1,128}@chatroom',native_chat_key),'group-invalid')
        _need(isinstance(claim_id,str)and re.fullmatch('[a-f0-9]{32}',claim_id)and isinstance(claim_hash_expected,str)and re.fullmatch('[a-f0-9]{64}',claim_hash_expected),'claim-invalid')
        registry_file=absolute_data_path('config/group-routes.local.json',root=root)
        registry,registry_token=_read(registry_file)
        _need(type(registry.get('version'))is int and registry['version']==1 and registry.get('accountId')==account and type(registry.get('groups'))is list and len(registry['groups'])<=1000,'registry-invalid')
        matches=[r for r in registry['groups']if type(r)is dict and r.get('nativeChatKey')==native_chat_key]
        _need(len(matches)==1,'route-ambiguous');route=matches[0]
        _need(route.get('accountId')==account and route.get('status')in('pending','active')
            and all(isinstance(route.get(k),str)and route[k]for k in('target','sessionId','chatId'))
            and route['target']!=config.get('target')and route['sessionId']!=config.get('sessionId'),'route-invalid')
        _need(sum(r.get('target')==route['target']for r in registry['groups']if type(r)is dict)==1
             and sum(r.get('sessionId')==route['sessionId']for r in registry['groups']if type(r)is dict)==1,'route-ambiguous')
        suffix=hashlib.sha256((account+'\0'+native_chat_key).encode('utf8')).hexdigest()+'.json'
        queued_file=absolute_data_path('config/auto-group-inbox/'+suffix,root=root)
        queued,queued_token=_read(queued_file)
        _need(queued.get('format')=='whale-native-group-inbox'and type(queued.get('version'))is int and queued['version']==1
            and queued.get('state')=='queued'and queued.get('accountId')==account and queued.get('nativeChatKey')==native_chat_key
            and queued.get('target')==route['target']and queued.get('sessionId')==route['sessionId']
            and queued.get('claimId')==claim_id and queued.get('claimHash')==claim_hash_expected
            and claim_hash(queued)==claim_hash_expected,'queued-claim-invalid')
        sid=queued.get('snapshotId');_need(isinstance(sid,str)and str(uuid.UUID(sid))==sid,'snapshot-invalid')
        # Retain the original existing verifier's secure file + authenticated
        # unread/raw row/member checks, without touching the UI or live DB.
        proof=verify_queued_handoff(config,target=route['target'],native_chat_key=native_chat_key,claim_id=claim_id)
        _need(proof.snapshot_id==sid and proof.claim_hash==claim_hash_expected and proof.session_id==route['sessionId'],'source-mismatch')
        _need(_read(registry_file)[1]==registry_token and _read(queued_file)[1]==queued_token,'source-changed')
        _need(_file(queued_file,root)==queued,'source-changed')
        return PendingTextAdmissionSource(str(root),account,native_chat_key,capture['accountUsername'],capture['sourceId'],
            route['target'],route['sessionId'],route['chatId'],claim_id,claim_hash_expected,sid,_compact(queued),_compact(route),_TOKEN)
    except RuntimeError as error:
        if re.fullmatch('native-group-text-admission-[a-z-]+',str(error)):raise
        raise RuntimeError('native-group-text-admission-source-unverified')from None
    except Exception:raise RuntimeError('native-group-text-admission-source-unverified')from None


def revalidate_pending_text_admission_source(config,proof):
    _need(isinstance(proof,PendingTextAdmissionSource)and proof._token is _TOKEN,'proof-invalid')
    fresh=verify_pending_text_admission_source(config,proof.native_chat_key,proof.claim_id,proof.claim_hash)
    _need(fresh==proof,'source-changed')
    return fresh
