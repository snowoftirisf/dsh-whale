"""Native-only unread facts from one authenticated, detached chat snapshot.

Never captures, writes, merges UI IDs, sends, or constructs a Vault. Readiness
requires a complete cross-checked marker set; unsupported media blocks the batch.
"""
from contextlib import contextmanager
import hashlib,json,re
from pathlib import Path
from vault.snapshot_import import load_reader,_validate,_fingerprint,_rows,_server_id


class Pending(ValueError):pass


def _require(test,code):
    if not test:raise Pending(code)


def _positive(value):return type(value) is int and value>0


class _TargetReader:
    def __init__(self,reader,table,server_ids,limit):
        self.reader,self.table,self.ids,self.limit=reader,table,server_ids,limit
        self.MESSAGE_TABLE_RE=re.compile(re.escape(table))
    def __getattr__(self,name):return getattr(self.reader,name)
    @contextmanager
    def connect_read_only(self,path):
        with self.reader.connect_read_only(path) as con:
            table=self.table;mapping=self.reader.table_mapping(con,table) if self.reader.table_exists(con,table) else None
            server=mapping['server_id'] if mapping else None
            if mapping:_require(bool(server),'group-unread-server-column-missing')
            ids=self.ids;limit=self.limit
            class FilteredConnection:
                def execute(self,sql,args=()):
                    marker=f' FROM [{table}] ORDER BY '
                    if marker in sql:
                        head,order=sql.rsplit(' ORDER BY ',1)
                        # Identifiers originate in validated schema mappings;
                        # all server values remain parameterized.
                        sql=head+f' WHERE [{server}] IN ('+','.join('?' for _ in ids)+') ORDER BY '+order+' LIMIT ?'
                        return con.execute(sql,[*ids,limit+1])
                    return con.execute(sql,args)
            yield FilteredConnection()


def _read(snapshot,*,native_chat_key,account_id,expected_account_username,expected_source_id,max_count=20):
    _require(isinstance(account_id,str) and bool(account_id.strip()),'group-unread-account-required')
    _require(isinstance(expected_account_username,str) and bool(expected_account_username.strip()),'group-unread-own-identity-required')
    _require(isinstance(expected_source_id,str) and bool(expected_source_id.strip()),'group-unread-source-required')
    _require(type(max_count) is int and 1<=max_count<=20,'group-unread-invalid-budget')
    _require(isinstance(native_chat_key,str) and re.fullmatch(r'wechat:[A-Za-z0-9_-]{1,128}@chatroom',native_chat_key),'group-unread-native-key-invalid')
    talker=native_chat_key.removeprefix('wechat:');reader=load_reader()
    root,manifest,shards,fingerprints=_validate(reader,snapshot)
    raw=json.loads((root/'snapshot-manifest.json').read_text(encoding='utf-8-sig'))
    _require(raw.get('source_id')==expected_source_id,'group-unread-source-mismatch')
    _require(manifest['account_username']==expected_account_username,'group-unread-account-mismatch')
    records=raw.get('databases',[])
    authenticated={r.get('database') for r in records if r.get('authenticated') is True and re.fullmatch(r'[0-9a-f]{64}',r.get('plaintext_sha256',''))}
    _require(all(p.relative_to(root).as_posix() in authenticated for p in [root/'contact/contact.db',root/'session/session.db',*shards]),'group-unread-authentication-manifest-incomplete')
    _require(len(shards)<=32,'group-unread-shard-budget-exceeded')
    contacts=reader.contact_records(root)
    _require(contacts.get(talker,{}).get('kind')=='group','group-unread-contact-identity-unresolved')
    with reader.connect_read_only(root/'session/session.db') as con:
        required={'SessionTable':{'username','unread_count','unread_first_msg_srv_id','last_msg_locald_id','last_timestamp'},
                  'SessionUnreadListTable_1':{'username_id','server_id','create_time','local_id'},'Name2Id':{'user_name'}}
        for table,columns in required.items():
            _require(reader.table_exists(con,table) and columns<=reader.columns(con,table),'group-unread-session-schema-unsupported')
        sessions=con.execute('SELECT * FROM SessionTable WHERE username=?',(talker,)).fetchall()
        _require(len(sessions)==1,'group-unread-session-ambiguous')
        session=dict(sessions[0]);count=session['unread_count'];first=_server_id(session['unread_first_msg_srv_id'])
        _require(_positive(count),'group-unread-marker-not-present')
        _require(count<=max_count,'group-unread-count-budget-exceeded')
        _require(first is not None,'group-unread-first-server-missing')
        names=con.execute('SELECT rowid FROM Name2Id WHERE user_name=?',(talker,)).fetchall()
        _require(len(names)==1,'group-unread-session-name-map-ambiguous')
        markers=con.execute('SELECT server_id,create_time,local_id FROM SessionUnreadListTable_1 WHERE username_id=? LIMIT ?',
                            (names[0][0],max_count+1)).fetchall()
    _require(len(markers)==count,'group-unread-marker-count-conflict')
    ids=[_server_id(m['server_id']) for m in markers]
    _require(all(ids) and len(set(ids))==count and first in ids,'group-unread-server-set-conflict')
    _require(all(_positive(m['local_id']) and _positive(m['create_time']) for m in markers),'group-unread-marker-envelope-invalid')
    marker_by_id={ident:dict(m) for ident,m in zip(ids,markers)}
    table=reader.message_table(talker);restricted=_TargetReader(reader,table,ids,max_count)
    selected=list(_rows(restricted,root,manifest,shards,contacts))
    _require(len(selected)==count,'group-unread-native-row-count-conflict')
    by_id={}
    for row in selected:
        ident=(row['native_id'] or '').removeprefix('wechat-server:')
        _require(ident in marker_by_id and ident not in by_id,'group-unread-native-id-conflict')
        mark=marker_by_id[ident]
        _require(row['chat_key']==native_chat_key and row['sender_id'] and row['direction']=='incoming','group-unread-direction-unproven')
        _require(row['metadata']['local_id']==mark['local_id'] and row['timestamp']==mark['create_time'],'group-unread-envelope-conflict')
        _require(row['sender_id'] in contacts and contacts[row['sender_id']].get('display_name') not in (None,'','未命名会话'),'group-unread-sender-display-unresolved')
        by_id[ident]=row
    ordered=sorted(by_id.values(),key=lambda r:(r['metadata']['local_id'],r['timestamp']))
    _require(ordered[0]['native_id']=='wechat-server:'+first,'group-unread-first-marker-conflict')
    _require(_positive(session['last_msg_locald_id']) and all(r['metadata']['local_id']<=session['last_msg_locald_id'] for r in ordered),'group-unread-last-message-bound-conflict')
    _require(_positive(session['last_timestamp']) and all(r['timestamp']<=session['last_timestamp'] for r in ordered),'group-unread-last-time-bound-conflict')
    unsupported=sorted({r['type'] for r in ordered if r['type'] not in ('text','system','voice')})
    _require(not unsupported,'group-unread-media-not-supported:'+','.join(unsupported))
    _require(all(r['text'].strip() for r in ordered if r['type']=='text'),'group-unread-empty-text-unproven')
    _require(sum(len(r['text']) for r in ordered)<=32000,'group-unread-text-budget-exceeded')
    _require(reader.validate_snapshot(root)['valid'] and _fingerprint([Path(p) for p in fingerprints])==fingerprints,'group-unread-snapshot-changed')
    messages=[{'nativeId':r['native_id'],'serverId':r['native_id'].removeprefix('wechat-server:'),
        'snapshotSourceId':r['source_id'],'senderId':r['sender_id'],'senderDisplayName':r['sender'],
        'type':r['type'],'text':r['text'],'timestampSeconds':r['timestamp'],
        'contextOnly':r['type']=='system','direction':'incoming'} for r in ordered]
    return {'state':'ready','source':'authenticated-wechat-native-unread','snapshotId':manifest['snapshot_id'],
        'accountId':account_id,'nativeChatKey':native_chat_key,'sourceId':expected_source_id,
        'readOnly':True,'uiIdsInferred':False,'productionWritten':False,
        'unreadCount':count,'firstServerId':first,'serverIds':[m['serverId'] for m in messages],
        'messageCount':len(messages),'triggerReply':any(not m['contextOnly'] for m in messages),'messages':messages,
        'limits':['Snapshot native source only; no UI/native ID association or delivery acknowledgement.',
                  'Requires full positive unread list/count/first-marker agreement; no fallback to recent history.']}


def read_snapshot_group_unread(snapshot_path,**kwargs):
    try:return _read(snapshot_path,**kwargs)
    except Pending as error:return {'state':'pending','code':str(error),'messages':[],'readOnly':True,'triggerReply':False}
    except Exception:return {'state':'pending','code':'group-unread-snapshot-invalid','messages':[],'readOnly':True,'triggerReply':False}


def read_latest_group_unread(data_root,**kwargs):
    """Read only the unchanged committed latest pointer for the trusted account."""
    try:
        root=Path(data_root).resolve();file=root/'snapshots/windows-live/latest.json'
        _require(not file.is_symlink(),'group-unread-latest-link-denied')
        before=file.read_bytes();latest=json.loads(before)
        _require(latest.get('status')=='complete' and latest.get('scope')=='chat' and latest.get('source_id')==kwargs.get('expected_source_id'),'group-unread-latest-unverified')
        _require(latest.get('import',{}).get('complete') is True and latest['import'].get('account_id')==kwargs.get('account_id'),'group-unread-latest-account-unverified')
        snapshot=Path(latest['snapshot']).resolve(strict=True)
        _require(snapshot.parent==(root/'snapshots/windows-live/snapshots').resolve() and snapshot.name==latest['snapshot_id'],'group-unread-latest-path-invalid')
        value=read_snapshot_group_unread(snapshot,**kwargs)
        _require(file.read_bytes()==before,'group-unread-latest-changed')
        if value['state']=='ready':_require(value['snapshotId']==latest['snapshot_id'],'group-unread-latest-id-mismatch')
        return value
    except Pending as error:return {'state':'pending','code':str(error),'messages':[],'readOnly':True,'triggerReply':False}
    except Exception:return {'state':'pending','code':'group-unread-latest-invalid','messages':[],'readOnly':True,'triggerReply':False}
