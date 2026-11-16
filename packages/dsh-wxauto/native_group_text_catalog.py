"""Authenticated group text pages. No UI, capture, sender-prefix fallback or writes."""
from pathlib import Path
import base64, hashlib, hmac, json, re, secrets
from vault.snapshot_import import load_reader, _validate, _fingerprint, _decode, _server_id
from quote_render import QUOTE_BASE_TYPE, STICKER_BASE_TYPE, IMAGE_BASE_TYPE, admit_quote, admit_sticker, admit_image, sticker_link


def _sticker_source(reader, con, table, mapping, budget):
    """Look up a quoted sticker's original row in this shard (read-only)."""
    column = lambda key: f'[{mapping[key]}]' if mapping.get(key) else 'NULL'
    def source(server_id):
        if not mapping.get('server_id'):
            return None
        found = con.execute(f"SELECT {column('content')},{column('compressed')},{column('compression_flag')} FROM [{table}] "
                            f"WHERE {column('server_id')}=? AND ({column('local_type')} & 4294967295)=47 LIMIT 1", (server_id,)).fetchone()
        if found is None:
            return None
        return sticker_link(_decode(reader, found[0], found[2], budget) or _decode(reader, found[1], found[2], budget))
    return source


class NativeGroupTextCatalogError(RuntimeError): pass

def _require(ok, code):
    if not ok: raise NativeGroupTextCatalogError('native-group-text-catalog-'+code)

def _json(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')
def _positive(v): return type(v) is int and 0<v<=9007199254740991


def _presentation(reader, root, talker, sender, raw):
    # Presentation-only proof. Missing/stale membership NEVER invalidates author.
    match=re.match(r'^([^:\r\n]{1,128}):\r?\n',raw)
    if not match: return raw,'raw-preserved'
    try:
        with reader.connect_read_only(root/'contact/contact.db') as con:
            fields=[f for f in ('username','alias','encrypt_username') if f in reader.columns(con,'contact')]
            contacts=[dict(r) for r in con.execute('SELECT '+','.join('['+f+']' for f in fields)+' FROM contact')]
            declared=[r for r in contacts if r.get('username')==sender]
            prefixed=[r for r in contacts if match[1] in r.values()]
            rooms=con.execute('SELECT id FROM chat_room WHERE username=?',(talker,)).fetchall()
            if len(declared)!=1 or len(prefixed)!=1 or declared[0]['username']!=prefixed[0]['username'] or len(rooms)!=1:
                return raw,'raw-preserved'
            members=con.execute('SELECT n.username FROM chatroom_member m JOIN name2id n ON n.rowid=m.member_id WHERE m.room_id=?',(rooms[0][0],)).fetchall()
            if [r[0] for r in members].count(sender)!=1: return raw,'raw-preserved'
        body=raw[match.end():]
        if body.strip():return body,'verified-sender-prefix-stripped'
    except Exception: pass
    return raw,'raw-preserved'


def page_native_group_texts(snapshot_path, *, expected_account_username, expected_source_id,
        account_id, native_group_chat_key, scan_key, cursor=None, expected_snapshot_id=None, page_size=500):
    try:
        _require(isinstance(account_id,str) and 0<len(account_id)<=256,'account-required')
        _require(isinstance(expected_account_username,str) and re.fullmatch(r'[^\s/\\\x00:]{1,256}',expected_account_username) and '@chatroom' not in expected_account_username,'identity-invalid')
        _require(isinstance(expected_source_id,str) and 0<len(expected_source_id)<=256,'source-required')
        _require(isinstance(native_group_chat_key,str) and re.fullmatch(r'wechat:[A-Za-z0-9_-]{1,128}@chatroom',native_group_chat_key),'group-scope-invalid')
        _require(type(page_size) is int and 1<=page_size<=1000,'budget-invalid')
        _require(isinstance(scan_key,str) and re.fullmatch('[a-f0-9]{64}',scan_key),'scan-key-invalid')
        reader=load_reader();root,manifest,shards,fingerprints=_validate(reader,snapshot_path)
        raw_manifest=(root/'snapshot-manifest.json').read_bytes();raw=json.loads(raw_manifest)
        _require(len(shards)<=32,'shard-budget')
        records=raw.get('databases')
        _require(isinstance(records,list) and bool(records),'authentication-required')
        authenticated={r.get('database') for r in records if r.get('authenticated') is True and re.fullmatch('[a-f0-9]{64}',r.get('plaintext_sha256',''))}
        _require(all(p.relative_to(root).as_posix() in authenticated for p in [root/'contact/contact.db',root/'session/session.db',*shards]),'authentication-required')
        _require(manifest['account_username']==expected_account_username,'account-mismatch')
        _require(raw.get('source_id')==expected_source_id,'source-mismatch')
        _require(expected_snapshot_id is None or expected_snapshot_id==manifest['snapshot_id'],'snapshot-mismatch')
        talker=native_group_chat_key[7:];contacts=reader.contact_records(root)
        _require(contacts.get(talker,{}).get('kind')=='group','group-unresolved')
        table=reader.message_table(talker);descriptors=[]
        for shard in sorted(shards):
            with reader.connect_read_only(shard) as con:
                if not reader.table_exists(con,table):continue
                mapping=reader.table_mapping(con,table);rowid=reader._source_rowid_name(con,table)
                _require(reader._compatible_message_mapping(mapping) and bool(rowid),'message-schema-unsupported')
                first=con.execute(f'SELECT [{rowid}] FROM [{table}] ORDER BY [{rowid}] LIMIT 1').fetchone()
                _require(first is None or _positive(first[0]),'source-rowid-unsupported')
                descriptors.append([shard.relative_to(root).as_posix(),table,rowid,mapping])
        scope=hashlib.sha256(_json([account_id,native_group_chat_key])).hexdigest()
        scan_scope=hashlib.sha256(_json([1,scope,manifest['snapshot_id'],hashlib.sha256(raw_manifest).hexdigest(),fingerprints,expected_account_username,expected_source_id,descriptors,page_size])).hexdigest()
        index,after=0,0
        def token(i,r):
            body=_json({'version':1,'scanScopeDigest':scan_scope,'descriptorIndex':i,'rowid':r})
            return base64.urlsafe_b64encode(body).decode().rstrip('=')+'.'+hmac.new(bytes.fromhex(scan_key),body,hashlib.sha256).hexdigest()
        if cursor is not None:
            _require(isinstance(cursor,str) and len(cursor)<=4096,'cursor-invalid')
            try:
                body,sig=cursor.split('.');body=base64.urlsafe_b64decode(body+'='*((-len(body))%4))
                _require(hmac.compare_digest(sig,hmac.new(bytes.fromhex(scan_key),body,hashlib.sha256).hexdigest()),'cursor-invalid')
                pos=json.loads(body);_require(pos.get('version')==1 and pos.get('scanScopeDigest')==scan_scope,'cursor-scope-mismatch')
                index,after=pos['descriptorIndex'],pos['rowid']
                _require(type(index) is int and 0<=index<len(descriptors) and type(after) is int and 0<=after<=9007199254740991,'cursor-invalid')
            except NativeGroupTextCatalogError:raise
            except Exception:raise NativeGroupTextCatalogError('native-group-text-catalog-cursor-invalid') from None
        scanned=0;messages=[];unresolved=[];observed=[];seen=set();decoded_budget={'decoded_bytes':0}
        while index<len(descriptors) and scanned<page_size:
            relative,_,rowid,mapping=descriptors[index];remaining=page_size-scanned
            with reader.connect_read_only(root/relative) as con:
                names=reader.sender_map(con)
                keys=('local_id','server_id','local_type','create_time','sender_id','content','compressed','compression_flag')
                fields=[f'[{rowid}] AS [_physical]']+[f'[{mapping[k]}] AS [{k}]' if mapping[k] else f'NULL AS [{k}]' for k in keys]
                rows=con.execute(f'SELECT {",".join(fields)} FROM [{table}] WHERE ([{mapping["local_type"]}] & 4294967295) IN (1,{QUOTE_BASE_TYPE},{STICKER_BASE_TYPE},{IMAGE_BASE_TYPE}) AND [{rowid}]>? ORDER BY [{rowid}] LIMIT ?',(after,remaining+1)).fetchall()
                take=rows[:remaining]
                packed_ok='packed_info_data' in reader.columns(con,table)
                for row in take:
                    scanned+=1;local=row['local_id'];stamp=row['create_time'];physical=row['_physical'];numeric=row['sender_id']
                    _require(_positive(local) and _positive(stamp) and _positive(physical),'envelope-invalid')
                    quote=None
                    if (int(row['local_type'] or 0)&0xFFFFFFFF)==IMAGE_BASE_TYPE:
                        # Photos are decrypted from the local attachment store by the
                        # adapter; packed_info_data names the file. Older ones stay invisible.
                        packed=con.execute(f'SELECT [packed_info_data] FROM [{table}] WHERE [{rowid}]=?',(physical,)).fetchone()[0] if packed_ok else None
                        quote=admit_image(row['local_type'],packed,stamp,hashlib.md5(talker.encode()).hexdigest())
                        if quote is None:continue
                    elif (int(row['local_type'] or 0)&0xFFFFFFFF)==STICKER_BASE_TYPE:
                        # Stickers carry sender identity and a plain CDN copy here; the
                        # UI path misjudged their direction. Older ones stay invisible.
                        envelope=_decode(reader,row['content'],row['compression_flag'],decoded_budget) or _decode(reader,row['compressed'],row['compression_flag'],decoded_budget)
                        quote=admit_sticker(row['local_type'],envelope,stamp)
                        if quote is None:continue
                    elif (int(row['local_type'] or 0)&0xFFFFFFFF)==QUOTE_BASE_TYPE:
                        # Only post-fix quote replies join the text stream; other
                        # link/file envelopes stay exactly as invisible as before.
                        envelope=_decode(reader,row['content'],row['compression_flag'],decoded_budget) or _decode(reader,row['compressed'],row['compression_flag'],decoded_budget)
                        quote=admit_quote(row['local_type'],envelope,stamp,_sticker_source(reader,con,table,mapping,decoded_budget))
                        if quote is None:continue
                    numeric=numeric if _positive(numeric) else None
                    sender=names.get(numeric) if numeric is not None else None
                    native=_server_id(row['server_id'])
                    if native is not None:
                        native='wechat-server:'+native;_require(native not in seen,'native-id-ambiguous');seen.add(native);observed.append(native)
                    if sender==expected_account_username:continue
                    text=quote
                    if text is None:text=_decode(reader,row['content'],row['compression_flag'],decoded_budget)
                    if not text:text=_decode(reader,row['compressed'],row['compression_flag'],decoded_budget)
                    _require(isinstance(text,str) and bool(text.strip()) and '\x00' not in text and len(text)<=64000 and len(text.encode('utf8'))<=65536,'text-envelope-invalid')
                    common={'nativeId':native,'serverId':native[14:] if native else None,'nativeChatKey':native_group_chat_key,'senderNumericId':numeric,
                            'rawText':text,'timestampSeconds':stamp,'localId':local,'snapshotSourceId':json.dumps([relative,table,physical],separators=(',',':'))}
                    if native is None:
                        unresolved.append({**common,'reason':'server-id-unresolved','direction':'unknown'});continue
                    if not isinstance(sender,str) or not sender or '\x00' in sender or len(sender.encode('utf8'))>256:
                        unresolved.append({**common,'reason':'sender-unresolved','direction':'unknown'});continue
                    display=contacts.get(sender,{}).get('display_name')
                    if not isinstance(display,str) or not display or display=='未命名会话' or len(display)>4000 or len(display.encode('utf8'))>4096 or '\x00' in display:display='成员（显示名未解析）'
                    rendered,status=_presentation(reader,root,talker,sender,text)
                    messages.append({**common,'senderId':sender,'senderDisplayName':display,'renderedText':rendered,'renderStatus':status,'direction':'incoming'})
            if len(rows)>remaining:after=take[-1]['_physical'];break
            index+=1;after=0
        _require(_fingerprint([Path(p) for p in fingerprints])==fingerprints,'snapshot-changed')
        return {'source':'authenticated-native-group-text-catalog-page','accountId':account_id,'nativeChatKey':native_group_chat_key,
                'snapshotId':manifest['snapshot_id'],'scopeDigest':scope,'scanScopeDigest':scan_scope,'messages':messages,
                'observedNativeIds':observed,'unresolved':unresolved,'scanned':scanned,'position':{'descriptorIndex':index,'rowid':after},
                'complete':index>=len(descriptors),'nextCursor':token(index,after) if index<len(descriptors) else None}
    except NativeGroupTextCatalogError:raise
    except Exception:raise NativeGroupTextCatalogError('native-group-text-catalog-snapshot-invalid') from None


def list_native_group_texts(snapshot_path, *, expected_account_username,expected_source_id,account_id,
        native_group_chat_key,expected_snapshot_id=None,page_size=500,max_pages=2048,max_identities=100000,max_bytes=64*1024*1024):
    _require(type(max_pages) is int and 1<=max_pages<=2048 and type(max_identities) is int and 1<=max_identities<=100000
             and type(max_bytes) is int and 1<=max_bytes<=64*1024*1024,'total-budget-invalid')
    key=secrets.token_hex(32);cursor=None;tokens=set();seen=set();messages=[];unresolved=[];size=0;physical=0;fixed=expected_snapshot_id;scan=None;scope=None;position=(-1,-1)
    for _ in range(max_pages):
        page=page_native_group_texts(snapshot_path,expected_account_username=expected_account_username,expected_source_id=expected_source_id,
            account_id=account_id,native_group_chat_key=native_group_chat_key,scan_key=key,cursor=cursor,expected_snapshot_id=fixed,page_size=page_size)
        if scan is None:fixed,scan,scope=page['snapshotId'],page['scanScopeDigest'],page['scopeDigest']
        _require(page['snapshotId']==fixed and page['scanScopeDigest']==scan and page['scopeDigest']==scope,'page-scope-invalid')
        size+=len(_json(page));_require(size<=max_bytes,'total-byte-limit')
        physical+=page['scanned'];_require(physical<=max_identities,'identity-limit')
        for ident in page['observedNativeIds']:_require(ident not in seen,'native-id-ambiguous');seen.add(ident)
        _require(len(seen)<=max_identities,'identity-limit')
        messages.extend(page['messages']);unresolved.extend(page['unresolved'])
        if page['complete']:
            return {'source':'authenticated-native-group-text-catalog','accountId':account_id,'nativeChatKey':native_group_chat_key,
                    'snapshotId':fixed,'scopeDigest':scope,'complete':True,'messages':sorted(messages,key=lambda r:(r['timestampSeconds'],r['localId'],r['nativeId'])),
                    'observedNativeIds':sorted(seen),'unresolved':unresolved}
        pos=(page['position']['descriptorIndex'],page['position']['rowid'])
        _require(pos>position and page['nextCursor'] not in tokens,'cursor-not-advancing');position=pos
        cursor=page['nextCursor'];tokens.add(cursor)
    raise NativeGroupTextCatalogError('native-group-text-catalog-page-limit')
