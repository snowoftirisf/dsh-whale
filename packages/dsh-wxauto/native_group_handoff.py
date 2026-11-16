"""Read-only verification of a durable native-unread queue, never submits it."""
from dataclasses import dataclass,field
import hashlib,json,os,stat,uuid
import re
from pathlib import Path
from group_unread import read_snapshot_group_unread
from vault.snapshot_import import load_reader,_validate,_fingerprint,_decode,_server_id

_TOKEN=object()


def _fail():raise RuntimeError('native-group-handoff-unverified')


def _file(path,root):
    path=Path(os.path.abspath(path));root=Path(root).resolve()
    for parent in (path,*path.parents):
        if parent.is_symlink() or (hasattr(parent,'is_junction') and parent.is_junction()):_fail()
    resolved=path.resolve(strict=True)
    if not resolved.is_relative_to(root):_fail()
    before=resolved.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 1<=before.st_size<=4*1024*1024:_fail()
    with resolved.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink!=1 or (opened.st_ino,opened.st_dev)!=(before.st_ino,before.st_dev):_fail()
        data=stream.read(4*1024*1024+1);after=os.fstat(stream.fileno())
    if after.st_nlink!=1 or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or len(data)!=before.st_size:_fail()
    return json.loads(data.decode('utf-8-sig'))


def claim_hash(value):
    messages=[[m[k] for k in ('nativeId','serverId','senderId','senderDisplayName','type','text','timestampSeconds','contextOnly')] for m in value['messages']]
    fields=[value[k] for k in ('accountId','nativeChatKey','target','sessionId','claimId','snapshotId','serverIds')]+[messages]
    return hashlib.sha256(json.dumps(fields,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _rendered_messages(snapshot,native_key,messages,own_id,source_id):
    """Strip one protocol prefix only after checking the original native row.

    Queued text is presentation text and may contain an identifier placeholder.
    It is never used to infer a sender or decide which prefix is removable.
    """
    reader=load_reader();root,manifest,shards,fingerprints=_validate(reader,snapshot)
    raw_manifest=json.loads((root/'snapshot-manifest.json').read_text(encoding='utf8'))
    if manifest['account_username']!=own_id or raw_manifest.get('source_id')!=source_id:_fail()
    talker=native_key.removeprefix('wechat:');expected_table=reader.message_table(talker);result=[]
    membership=None;contacts=None
    def identities():
        nonlocal membership,contacts
        if contacts is not None:return
        with reader.connect_read_only(root/'contact/contact.db') as con:
            columns=reader.columns(con,'contact');fields=[f for f in ('username','alias','encrypt_username') if f in columns]
            contacts=[dict(r) for r in con.execute('SELECT '+','.join('['+f+']' for f in fields)+' FROM contact')]
            rooms=con.execute('SELECT id FROM chat_room WHERE username=?',(talker,)).fetchall()
            if len(rooms)!=1:_fail()
            membership=[r[0] for r in con.execute('SELECT n.username FROM chatroom_member m JOIN name2id n ON n.rowid=m.member_id WHERE m.room_id=?',(rooms[0][0],))]
    for message in messages:
        if message['type']!='text':
            result.append((message['nativeId'],message['text']));continue
        relative,table,rowid=json.loads(message['snapshotSourceId']);file=root/relative
        if file not in shards or table!=expected_table or type(rowid) is not int or rowid<1:_fail()
        with reader.connect_read_only(file) as con:
            mapping=reader.table_mapping(con,table);rowname=reader._source_rowid_name(con,table)
            keys=('server_id','sender_id','local_type','content','compressed','compression_flag')
            fields=[f'[{mapping[k]}] AS [{k}]' if mapping[k] else f'NULL AS [{k}]' for k in keys]
            records=con.execute('SELECT '+','.join(fields)+f' FROM [{table}] WHERE [{rowname}]=?',(rowid,)).fetchall()
            if len(records)!=1:_fail()
            record=records[0];native=_server_id(record['server_id'])
            if native!=message['serverId'] or message['nativeId']!='wechat-server:'+native:_fail()
            mapped=reader.sender_map(con).get(int(record['sender_id'])) if record['sender_id'] is not None else None
            if mapped!=message['senderId']:raise RuntimeError('native-group-raw-sender-conflict')
            budget={'decoded_bytes':0}
            raw=_decode(reader,record['content'],record['compression_flag'],budget)
            if not raw:raw=_decode(reader,record['compressed'],record['compression_flag'],budget)
            kind=int(record['local_type'] or 0)
            if kind&0xFFFFFFFF!=1 or reader.format_message_content(kind,raw)!=message['text']:_fail()
        prefix=re.match(r'^([^:\r\n]{1,128}):\r?\n',raw)
        body=message['text']
        if prefix:
            identities();name=prefix[1];sender=message['senderId']
            declared=[c for c in contacts if sender in c.values()]
            prefixed=[c for c in contacts if name in c.values()]
            if len(declared)!=1 or membership.count(declared[0]['username'])!=1:_fail()
            if len(prefixed)>1:raise RuntimeError('native-group-payload-sender-ambiguous')
            if prefixed and membership.count(prefixed[0]['username'])==1:
                if prefixed[0]['username']!=declared[0]['username']:raise RuntimeError('native-group-payload-sender-conflict')
                # Both canonical IDs and verified aliases of this same member
                # qualify. A second ordinary user heading remains untouched.
                body=reader.format_message_content(kind,raw[prefix.end():])
        result.append((message['nativeId'],body))
    if _fingerprint([Path(p) for p in fingerprints])!=fingerprints:_fail()
    return tuple(result)


@dataclass(frozen=True)
class NativeQueuedHandoff:
    data_root:str
    account_id:str
    account_username:str
    source_id:str
    target:str
    native_chat_key:str
    claim_id:str
    claim_hash:str
    session_id:str
    snapshot_id:str
    unread_count:int
    server_ids:tuple
    rendered_messages:tuple=field(repr=False)
    last_text:str=field(repr=False)
    _token:object=field(repr=False,compare=False)
    last_kind:str='text'
    last_voice_duration_ms:int|None=None


def verify_queued_handoff(config,*,target,native_chat_key,claim_id):
    """Return an internal proof only for an unchanged queued, authentic source.

    Original snapshot is revalidated, not latest: selecting a real group may
    clear its live unread badge while the durable request is still queued.
    """
    try:
        account=config['accountId'];root=Path(config['dataRoot']);capture=config['capture']
        if not root.is_absolute():_fail()
        if not all(isinstance(v,str) and v.strip() for v in (account,str(root),capture['accountUsername'],capture['sourceId'],target,native_chat_key)):_fail()
        if not re.fullmatch(r'[0-9a-f]{32}',claim_id):_fail()
        filename=hashlib.sha256((account+'\0'+native_chat_key).encode()).hexdigest()+'.json'
        value=_file(root/'config/auto-group-inbox'/filename,root)
        if (value.get('format')!='whale-native-group-inbox' or type(value.get('version')) is not int or value.get('version')!=1 or value.get('state')!='queued'
                or value.get('accountId')!=account or value.get('nativeChatKey')!=native_chat_key
                or value.get('target')!=target or value.get('claimId')!=claim_id):_fail()
        session=value['sessionId']
        if not isinstance(session,str) or not session.strip() or session==config.get('sessionId'):_fail()
        snapshot=value['snapshotId']
        if str(uuid.UUID(snapshot))!=snapshot:_fail()
        if type(value.get('unreadCount')) is not int or value.get('claimHash')!=claim_hash(value):_fail()
        original=read_snapshot_group_unread(root/'snapshots/windows-live/snapshots'/snapshot,
            native_chat_key=native_chat_key,account_id=account,expected_account_username=capture['accountUsername'],
            expected_source_id=capture['sourceId'])
        if (original['state']!='ready' or original['snapshotId']!=snapshot or original['unreadCount']!=value['unreadCount']
                or original['serverIds']!=value['serverIds']
                or json.dumps(original['messages'],ensure_ascii=False,sort_keys=True)!=json.dumps(value['messages'],ensure_ascii=False,sort_keys=True)):_fail()
        last=value['messages'][-1]
        if last['type'] not in ('text','voice') or last['contextOnly'] is not False:_fail()
        duration=None
        if last['type']=='voice':
            from native_voice_source import read_native_voice_source
            audio=read_native_voice_source(root/'snapshots/windows-live/snapshots'/snapshot,
                native_chat_key=native_chat_key,server_id=last['serverId'],
                expected_account_username=capture['accountUsername'],expected_source_id=capture['sourceId'],
                expected_snapshot_id=snapshot)
            duration=audio['metadata']['durationMilliseconds']
            if type(duration) is not int or not 0<duration<=120000:_fail()
        rendered=_rendered_messages(root/'snapshots/windows-live/snapshots'/snapshot,native_chat_key,value['messages'],capture['accountUsername'],capture['sourceId'])
        # Detect changes to queued/submitted state while validating the source.
        if _file(root/'config/auto-group-inbox'/filename,root)!=value:_fail()
        return NativeQueuedHandoff(str(root.resolve()),account,capture['accountUsername'],capture['sourceId'],target,
            native_chat_key,claim_id,value['claimHash'],session,snapshot,value['unreadCount'],tuple(value['serverIds']),rendered,
            rendered[-1][1] if last['type']=='text' else '',_TOKEN,last_kind=last['type'],last_voice_duration_ms=duration)
    except Exception as error:
        if isinstance(error,RuntimeError) and str(error) in ('native-group-handoff-unverified','native-group-raw-sender-conflict','native-group-payload-sender-conflict','native-group-payload-sender-ambiguous'):raise
        raise RuntimeError('native-group-handoff-unverified') from None


def revalidate_handoff(proof,*,target,native_chat_key):
    if not isinstance(proof,NativeQueuedHandoff) or proof._token is not _TOKEN or proof.target!=target or proof.native_chat_key!=native_chat_key:_fail()
    config={'dataRoot':proof.data_root,'accountId':proof.account_id,
            'capture':{'accountUsername':proof.account_username,'sourceId':proof.source_id}}
    fresh=verify_queued_handoff(config,target=target,native_chat_key=native_chat_key,claim_id=proof.claim_id)
    if fresh!=proof:_fail()
    return fresh
