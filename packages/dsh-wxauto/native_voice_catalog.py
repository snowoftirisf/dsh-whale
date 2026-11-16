"""Read-only authenticated snapshot voice envelopes. Never reads media blobs."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import hashlib,hmac,base64
from vault.snapshot_import import load_reader, _validate, _fingerprint, _rows


class NativeVoiceCatalogError(RuntimeError):
    pass


def _require(value, code):
    if not value: raise NativeVoiceCatalogError('native-voice-catalog-' + code)


class _VoiceReader:
    def __init__(self, reader, tables, limit):
        self.reader,self.tables,self.limit=reader,tables,limit
        self.MESSAGE_TABLE_RE=re.compile('(?:'+ '|'.join(re.escape(t) for t in tables) +')' if tables else r'(?!)')
    def __getattr__(self,name):return getattr(self.reader,name)
    @contextmanager
    def connect_read_only(self,path):
        with self.reader.connect_read_only(path) as con:
            reader,tables,limit=self.reader,self.tables,self.limit
            class Connection:
                def execute(self,sql,args=()):
                    for table in tables:
                        if f' FROM [{table}] ORDER BY ' not in sql:continue
                        mapping=reader.table_mapping(con,table)
                        typ,server=mapping['local_type'],mapping['server_id']
                        _require(bool(typ and server),'message-schema-unsupported')
                        head,order=sql.rsplit(' ORDER BY ',1)
                        sql=head+f' WHERE ([{typ}] & 4294967295)=34 AND [{server}]>0 ORDER BY '+order+' LIMIT ?'
                        return con.execute(sql,(limit+1,))
                    return con.execute(sql,args)
            yield Connection()


class _PageReader(_VoiceReader):
    def __init__(self,reader,table,after,limit):
        super().__init__(reader,{table},limit);self.after=after
    @contextmanager
    def connect_read_only(self,path):
        with self.reader.connect_read_only(path) as con:
            reader,table,after,limit=self.reader,next(iter(self.tables)),self.after,self.limit
            class Connection:
                def execute(self,sql,args=()):
                    if f' FROM [{table}] ORDER BY ' in sql:
                        mapping=reader.table_mapping(con,table);rowid=reader._source_rowid_name(con,table)
                        head,order=sql.rsplit(' ORDER BY ',1)
                        sql=head+f" WHERE ([{mapping['local_type']}] & 4294967295)=34 AND [{mapping['server_id']}]>0 AND [{rowid}]>? ORDER BY "+order+' LIMIT ?'
                        return con.execute(sql,(after,limit+1))
                    return con.execute(sql,args)
            yield Connection()


def page_native_voices(snapshot_path,*,expected_account_username,expected_source_id,account_id,
                       allowed_chat_keys,scan_key,cursor=None,expected_snapshot_id=None,page_size=500):
    """One authenticated physical-row keyset page, including outgoing progress.

    scan_key is a fresh private Node scan capability, never a WeChat credential;
    it is sent only via stdin, never returned or persisted. Callers must consume
    every page before baseline/enqueue. Cursor is invalid across snapshots/scopes.
    """
    try:
        _require(isinstance(account_id,str) and bool(account_id),'account-required')
        _require(type(page_size) is int and 1<=page_size<=1000,'budget-invalid')
        _require(isinstance(scan_key,str) and re.fullmatch('[0-9a-f]{64}',scan_key),'scan-key-invalid')
        _require(isinstance(allowed_chat_keys,list) and len(allowed_chat_keys)<=1000 and len(set(allowed_chat_keys))==len(allowed_chat_keys)
                 and all(isinstance(k,str) and re.fullmatch(r'wechat:[^\s/\\\x00]{1,256}',k) for k in allowed_chat_keys),'allowlist-invalid')
        reader=load_reader();root,manifest,shards,fingerprints=_validate(reader,snapshot_path)
        _require(len(shards)<=32,'shard-budget')
        raw_bytes=(root/'snapshot-manifest.json').read_bytes();raw=json.loads(raw_bytes)
        _require(manifest['account_username']==expected_account_username and bool(expected_account_username),'account-mismatch')
        _require(raw.get('source_id')==expected_source_id and bool(expected_source_id),'source-mismatch')
        _require(expected_snapshot_id is None or expected_snapshot_id==manifest['snapshot_id'],'snapshot-mismatch')
        audio=raw.get('audio_capture',{});names=audio.get('databases')
        _require(audio.get('enabled') is True and audio.get('scope')=='chat' and audio.get('schemaVersion')==1
                 and isinstance(names,list) and 1<=len(names)<=64 and len(set(names))==len(names),'audio-capture-required')
        for name in names:
            records=[r for r in raw.get('databases',[]) if r.get('database')==name]
            _require(isinstance(name,str) and re.fullmatch(r'message/media_[0-9]+\.db',name) and len(records)==1
                     and records[0].get('authenticated') is True and re.fullmatch('[0-9a-f]{64}',records[0].get('plaintext_sha256','')),'audio-metadata-invalid')
        contacts=reader.contact_records(root);tables={}
        for key in sorted(allowed_chat_keys):
            _require(key[7:] in contacts,'chat-unresolved');table=reader.message_table(key[7:])
            _require(table not in tables,'chat-ambiguous');tables[table]=key
        descriptors=[]
        for shard in sorted(shards):
            with reader.connect_read_only(shard) as con:
                present={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in sorted(set(tables)&present):
                    mapping=reader.table_mapping(con,table);rowid=reader._source_rowid_name(con,table)
                    _require(reader._compatible_message_mapping(mapping) and bool(rowid),'message-schema-unsupported')
                    first=con.execute(f'SELECT [{rowid}] FROM [{table}] ORDER BY [{rowid}] LIMIT 1').fetchone()
                    _require(first is None or type(first[0]) is int and first[0]>0,'source-rowid-unsupported')
                    descriptors.append([shard.relative_to(root).as_posix(),table,rowid,mapping])
        encoded=lambda v:json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        scope=hashlib.sha256(encoded([1,account_id,manifest['snapshot_id'],hashlib.sha256(raw_bytes).hexdigest(),expected_account_username,expected_source_id,sorted(allowed_chat_keys),descriptors,page_size])).hexdigest()
        index,after=0,0
        def token(i,row):
            payload=encoded({'version':1,'scopeDigest':scope,'descriptorIndex':i,'rowid':row})
            return base64.urlsafe_b64encode(payload).decode().rstrip('=')+'.'+hmac.new(bytes.fromhex(scan_key),payload,hashlib.sha256).hexdigest()
        if cursor is not None:
            _require(isinstance(cursor,str) and len(cursor)<=4096,'cursor-invalid')
            try:
                payload,signature=cursor.split('.');payload=base64.urlsafe_b64decode(payload+'='*((-len(payload))%4))
                _require(hmac.compare_digest(signature,hmac.new(bytes.fromhex(scan_key),payload,hashlib.sha256).hexdigest()),'cursor-invalid')
                value=json.loads(payload)
                _require(value.get('version')==1 and value.get('scopeDigest')==scope,'cursor-scope-mismatch')
                index,after=value['descriptorIndex'],value['rowid']
                _require(type(index) is int and 0<=index<len(descriptors) and type(after) is int and after>=0,'cursor-invalid')
            except NativeVoiceCatalogError:raise
            except Exception:raise NativeVoiceCatalogError('native-voice-catalog-cursor-invalid') from None
        scanned=[]
        while index<len(descriptors) and len(scanned)<page_size:
            relative,table,_,_=descriptors[index];remaining=page_size-len(scanned)
            batch=list(_rows(_PageReader(reader,table,after,remaining),root,manifest,[root/relative],contacts))
            take=batch[:remaining];scanned.extend(take)
            _require(all(type(r['metadata']['local_rowid']) is int and 0<r['metadata']['local_rowid']<=9007199254740991 for r in take),'source-rowid-unsupported')
            if len(batch)>remaining:
                after=take[-1]['metadata']['local_rowid'];break
            index+=1;after=0
        voices=[];seen=set();observed=[]
        for row in scanned:
            native=row.get('native_id');_require(isinstance(native,str) and re.fullmatch('wechat-server:[1-9][0-9]*',native),'native-id-invalid')
            _require(native not in seen,'native-id-ambiguous');seen.add(native);observed.append(native)
            _require(row['chat_key'] in allowed_chat_keys and row['type']=='voice','scope-invalid')
            sender=row.get('sender_id')
            if row['direction']!='incoming' or sender==expected_account_username:continue
            _require(sender in contacts and contacts[sender].get('display_name') not in (None,'','未命名会话'),'sender-unresolved')
            local=row['metadata'].get('local_id');stamp=row['timestamp']
            _require(type(local) is int and local>0 and type(stamp) is int and stamp>0 and isinstance(row['text'],str) and 0<len(row['text'])<=65536,'envelope-invalid')
            voices.append({'nativeId':native,'serverId':native.removeprefix('wechat-server:'),'nativeChatKey':row['chat_key'],'senderId':sender,'senderDisplayName':contacts[sender]['display_name'],'rawText':row['text'],'timestampSeconds':stamp,'localId':local,'snapshotSourceId':row['source_id'],'direction':'incoming'})
        _require(_fingerprint([Path(p) for p in fingerprints])==fingerprints,'snapshot-changed')
        return {'source':'authenticated-native-voice-catalog-page','accountId':account_id,'snapshotId':manifest['snapshot_id'],'scopeDigest':scope,
                'voices':voices,'observedServerIds':observed,'scanned':len(scanned),'position':{'descriptorIndex':index,'rowid':after},
                'complete':index>=len(descriptors),'nextCursor':token(index,after) if index<len(descriptors) else None}
    except NativeVoiceCatalogError:raise
    except Exception:raise NativeVoiceCatalogError('native-voice-catalog-snapshot-invalid') from None


def list_native_voices(snapshot_path,*,expected_account_username,expected_source_id,
                       allowed_chat_keys,expected_snapshot_id=None,max_messages=1000):
    """Compatibility collector: max_messages is the bounded per-page scan size.

    Every authenticated page is consumed before exposing the old catalog shape.
    Resource failures never return a truncated catalog for baseline/adoption.
    """
    import secrets
    key=secrets.token_hex(32);cursor=None;voices=[];seen=set();bytes_seen=0
    fixed_snapshot=expected_snapshot_id
    for _ in range(2048):
        page=page_native_voices(snapshot_path,expected_account_username=expected_account_username,
            expected_source_id=expected_source_id,account_id=expected_account_username,
            allowed_chat_keys=allowed_chat_keys,scan_key=key,cursor=cursor,
            expected_snapshot_id=fixed_snapshot,page_size=max_messages)
        fixed_snapshot=page['snapshotId']
        bytes_seen+=len(json.dumps(page,ensure_ascii=False).encode())
        _require(bytes_seen<=64*1024*1024,'total-byte-limit')
        for native in page['observedServerIds']:
            _require(native not in seen,'native-id-ambiguous');seen.add(native)
        voices.extend(page['voices']);_require(len(voices)<=100000,'identity-limit')
        if page['complete']:
            return {'source':'authenticated-native-voice-catalog','snapshotId':fixed_snapshot,
                'voices':sorted(voices,key=lambda v:(v['timestampSeconds'],v['nativeChatKey'],v['localId']))}
        _require(page['nextCursor']!=cursor,'cursor-not-advancing');cursor=page['nextCursor']
    raise NativeVoiceCatalogError('native-voice-catalog-page-limit')
