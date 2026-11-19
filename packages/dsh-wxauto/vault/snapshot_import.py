"""Read validated, detached plaintext snapshots into the private history vault.

This module never captures keys, decrypts a database, reads a live database,
operates WeChat, or sends messages. Validation, decoding and safe envelope
rendering are reused from the explicitly supplied yichen reference checkout.
Only successful whole-snapshot scans advance the account's import cursor;
partial rows are harmless/idempotent historical evidence on retry.
"""
import json
import re
from pathlib import Path
from .store import VaultError, digest_file, required
from .vendor import ROOT as REFERENCE, reader as vendored_reader

SOURCE = 'wechat-snapshot'
TYPES = {1:'text',3:'image',34:'voice',42:'contact',43:'video',47:'emotion',48:'location',
         49:'link_or_file',50:'call',10000:'system',10002:'recalled'}


def load_reader(reference_dir=None):
    """Only package-local, hash-verified vendor code can execute."""
    if reference_dir is not None and Path(reference_dir).resolve()!=REFERENCE:
        raise VaultError('external-snapshot-reader-denied')
    return vendored_reader()


def _fingerprint(paths):
    return {str(p):(p.stat().st_size,p.stat().st_mtime_ns,digest_file(p)) for p in paths}


def _validate(reader,snapshot):
    # Preserve lexical path so the reference rejects links/reparse/UNC instead
    # of resolving them away before validation.
    root=reader.secure_snapshot_root(Path(snapshot))
    report=reader.validate_snapshot(root)
    if not report['valid']:raise VaultError('snapshot-validation-failed')
    manifest=reader._read_manifest(root)
    shards=reader.message_databases(root)
    core=[root/p for p in reader.required_databases_for(manifest)]
    files=list(dict.fromkeys([root/'snapshot-manifest.json',*core,*shards]))
    fingerprints=_fingerprint(files)
    # An actual capture manifest carries authenticated plaintext file hashes.
    # Check those when present, rather than treating its "authenticated" text
    # as proof. The acquisition layer remains responsible for page HMAC checks.
    raw=json.loads((root/'snapshot-manifest.json').read_text(encoding='utf8'))
    records=raw.get('databases')
    if records is not None:
        if not isinstance(records,list) or len(records)>258:
            raise VaultError('snapshot-database-manifest-invalid')
        by_name={}
        for entry in records:
            if not isinstance(entry,dict) or not isinstance(entry.get('database'),str):
                raise VaultError('snapshot-database-manifest-invalid')
            name=entry['database']
            if name in by_name:raise VaultError('snapshot-database-manifest-duplicate')
            by_name[name]=entry
        for file in [*core,*shards]:
            name=file.relative_to(root).as_posix();entry=by_name.get(name)
            if not entry or entry.get('authenticated') is not True:
                raise VaultError('snapshot-authentication-manifest-incomplete')
            expected=entry.get('plaintext_sha256')
            if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected) or fingerprints[str(file)][2]!=expected:
                raise VaultError('snapshot-plaintext-checksum-mismatch')
    # Validate each recognized core DB completely, not only first quick_check row.
    for file in [*core,*shards]:
        with reader.connect_read_only(file) as con:
            if [r[0] for r in con.execute('PRAGMA integrity_check')]!=['ok']:
                raise VaultError('snapshot-database-integrity-failed')
    return root,manifest,shards,fingerprints


def _server_id(value):
    # Floats, negative / zero placeholders, arbitrary strings are not reliable IDs.
    if isinstance(value,bool) or not isinstance(value,(int,str)):return None
    text=str(value)
    if not re.fullmatch(r'[0-9]{1,20}',text):return None
    number=int(text)
    return str(number) if 0<number<=0xFFFFFFFFFFFFFFFF else None


def _decode(reader,value,flag,budget):
    text=reader.decode(value,flag,budget)
    if isinstance(value,(bytes,bytearray,memoryview)) and value and (bytes(value).startswith(reader.ZSTD_MAGIC) or flag==4):
        # The reference's bounded stream_reader can yield an empty string for a
        # truncated frame without raising. Verify frame termination *after* its
        # decompressed-size/window checks so incomplete text never becomes valid.
        remaining=bytes(value)
        try:
            while remaining:
                decoder=reader.zstandard.ZstdDecompressor(max_window_size=reader.MAX_ZSTD_WINDOW_KIB).decompressobj()
                decoder.decompress(remaining)
                if not decoder.eof:raise VaultError('snapshot-compressed-message-truncated')
                following=decoder.unused_data
                if len(following)>=len(remaining):raise VaultError('snapshot-compressed-message-invalid')
                remaining=following
        except reader.zstandard.ZstdError as exc:
            raise VaultError('snapshot-compressed-message-invalid') from exc
    return text


def _rows(reader,root,manifest,shards,contacts):
    talkers={reader.message_table(username):username for username in contacts}
    account_username=manifest['account_username']
    for shard in shards:
        relative=shard.relative_to(root).as_posix()
        with reader.connect_read_only(shard) as con:
            names=reader.sender_map(con)
            tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name") if reader.MESSAGE_TABLE_RE.fullmatch(r[0])]
            budget={'rows':0,'decoded_bytes':0}
            for table in tables:
                mapping=reader.table_mapping(con,table);rowid=reader._source_rowid_name(con,table)
                if not reader._compatible_message_mapping(mapping) or not rowid:
                    raise VaultError('snapshot-message-schema-unsupported')
                talker=talkers.get(table)
                info=contacts.get(talker,{})
                chat_key='wechat:'+talker if talker else 'wechat-unresolved-table:'+table
                kind='group' if talker and '@chatroom' in talker else 'friend' if talker else 'unknown'
                display=info.get('display_name') or '未解析会话 '+table[4:12]
                fields=[f'[{rowid}] AS [_source_rowid]']
                for key in ('local_id','server_id','local_type','create_time','sender_id','content','compressed','compression_flag'):
                    column=mapping[key]
                    fields.append(f'[{column}] AS [{key}]' if column else f'NULL AS [{key}]')
                for record in con.execute(f"SELECT {','.join(fields)} FROM [{table}] ORDER BY [{rowid}]"):
                    # Resource budgets reset per batch so full local history can
                    # stream without loading it all or silently truncating at 200k.
                    if budget['rows']>=1000 or budget['decoded_bytes']>=4*1024*1024:
                        budget={'rows':0,'decoded_bytes':0}
                    budget['rows']+=1
                    content=_decode(reader,record['content'],record['compression_flag'],budget)
                    if not content:content=_decode(reader,record['compressed'],record['compression_flag'],budget)
                    sender_id=names.get(int(record['sender_id']),'') if record['sender_id'] is not None else ''
                    if not sender_id and ':\n' in content:
                        prefix,rest=content.split(':\n',1)
                        if prefix.startswith('wxid_'):sender_id,content=prefix,rest
                    raw_type=int(record['local_type'] or 0);base=raw_type&0xFFFFFFFF
                    direction=('outgoing' if sender_id==account_username else 'incoming') if sender_id and account_username else 'unknown'
                    sender=contacts.get(sender_id,{}).get('display_name') or ('系统' if base in (10000,10002) else '我' if direction=='outgoing' else '未知成员')
                    timestamp=record['create_time']
                    if isinstance(timestamp,bool) or not isinstance(timestamp,(int,float)) or timestamp<0:
                        raise VaultError('snapshot-message-time-invalid')
                    native=_server_id(record['server_id'])
                    text=reader.format_message_content(raw_type,content)
                    yield {'chat_key':chat_key,'kind':kind,'display_name':display,'text':text,'sender':sender,
                           'sender_id':sender_id or None,'direction':direction,'type':TYPES.get(base,'unknown'),
                           'timestamp':int(timestamp),'source_id':json.dumps([relative,table,int(record['_source_rowid'])],separators=(',',':')),
                           'native_id':'wechat-server:'+native if native else None,
                           'metadata':{'snapshot_id':manifest['snapshot_id'],'shard':relative,'table':table,
                              'local_rowid':int(record['_source_rowid']),'local_id':record['local_id'],
                              'local_type':raw_type,'historical_import':True,'media_available':False if base in (3,34,43,47,49) else None,
                              'talker_resolved':talker is not None,'source_timestamp_zero':timestamp==0,
                              'sent_unverified':direction=='outgoing'}}


def import_snapshot(vault,snapshotPath,account_id,*,reference_dir=None):
    """Import an already decrypted snapshot; returns counts, never raw messages.

    account_id is selected by the trusted account mapping, never inferred from
    filesystem names. Native sender direction comes only from manifest identity.
    Stable chat_key uses talker, source_id uses shard/table/local-rowid, native_id
    uses a positive integer server ID. snapshot UUID is provenance only.
    """
    required(account_id,'account-id')
    reader=load_reader(reference_dir)
    root,manifest,shards,fingerprints=_validate(reader,snapshotPath)
    contacts=reader.contact_records(root)
    stats={'snapshot_id':manifest['snapshot_id'],'account_id':account_id,'scanned':0,'inserted':0,
           'linked':0,'duplicates':0,'unresolved_chats':0,'chats':0,'types':{},'historical_only':True,
           'media_restored':0,'integrity_gaps':manifest['integrity_gaps']}
    chat_ids={};candidates={};minimum=None;maximum=None;resolutions=[]
    # Each message commit is durable; a decoding failure does not advertise a
    # successful snapshot. Retrying reuses the same source/native IDs.
    for row in _rows(reader,root,manifest,shards,contacts):
        key=row.pop('chat_key');kind=row.pop('kind');display=row.pop('display_name')
        if key not in chat_ids:
            chat_ids[key]=vault.ensure_chat(account_id,key,kind,display)['id']
            candidates[key]={'chat_id':chat_ids[key],'chat_key':key,'kind':kind,'display_name':display}
            if kind=='unknown':stats['unresolved_chats']+=1
        stamp=row['timestamp'];minimum=stamp if minimum is None else min(minimum,stamp);maximum=stamp if maximum is None else max(maximum,stamp)
        row['timestamp_quality']='database'
        row['delivery_state']='received' if row['direction']=='incoming' else 'unknown'
        # Timestamp zero is an unknown source time, not the current observation.
        row['timestamp']=stamp
        with vault.lock:
            previous=vault.db.execute('SELECT m.* FROM messages m JOIN sources s ON s.message_id=m.id WHERE s.chat_id=? AND s.source=? AND s.source_id=?',
                                     (chat_ids[key],SOURCE,row['source_id'])).fetchone()
            if previous and (previous['text']!=row['text'] or previous['native_id']!=row['native_id']):
                # Say which part differs and the stored/new message kinds (fixed
                # labels only, never content), so a recalled or re-sent row can
                # be told apart from a reused row position without reading chats.
                differs='-and-'.join(label for label,key in (('text','text'),('native-id','native_id')) if previous[key]!=row[key])
                kind=lambda value:value if isinstance(value,str) and re.fullmatch('[a-z_]{1,16}',value) else 'other'
                raise VaultError(f"snapshot-source-identity-conflict:{differs}:{kind(previous['type'])}-to-{kind(row['type'])}")
            if previous and row['direction']!='unknown' and re.fullmatch(r'wechat-server:[1-9][0-9]*',row['native_id'] or ''):
                if previous['sender_id'] and row['sender_id'] and previous['sender_id']!=row['sender_id']:
                    raise VaultError('snapshot-source-sender-conflict')
                # A missing old sender is not evidence of identity continuity.
                if previous['sender_id'] and previous['sender_id']==row['sender_id']:
                    if previous['timestamp']!=stamp*1000 or previous['type']!=row['type']:
                        raise VaultError('snapshot-source-envelope-conflict')
                    if previous['direction'] not in ('unknown',row['direction']):
                        raise VaultError('snapshot-source-direction-conflict')
                    if previous['direction']=='unknown':
                        resolutions.append((dict(previous),dict(row),chat_ids[key]))
        result=vault.record_message(chat_ids[key],SOURCE,row.pop('source_id'),**row)
        stats['scanned']+=1
        stats['types'][row['type']]=stats['types'].get(row['type'],0)+1
        stats['inserted' if result['inserted'] else 'linked' if result.get('linked') else 'duplicates']+=1
    # Revalidate paths/sidecars and source bytes before advancing any cursor.
    final=reader.validate_snapshot(root)
    if not final['valid'] or _fingerprint([Path(p) for p in fingerprints])!=fingerprints:
        raise VaultError('snapshot-changed-during-import')
    # Promote only after the complete immutable snapshot has been validated.
    # No delivery state or original observation metadata is overwritten.
    with vault.lock,vault.db:
        promoted=0
        for before,row,chat_id in resolutions:
            current=vault.db.execute('SELECT m.* FROM messages m JOIN sources s ON s.message_id=m.id WHERE s.chat_id=? AND s.source=? AND s.source_id=?',
                                     (chat_id,SOURCE,row['source_id'])).fetchone()
            if not current or any(current[k]!=before[k] for k in ('id','native_id','sender_id','text','timestamp','type','direction','metadata')):
                raise VaultError('snapshot-direction-observation-changed')
            metadata=json.loads(current['metadata'])
            if 'direction_resolution' in metadata:raise VaultError('snapshot-direction-evidence-conflict')
            metadata['direction_resolution']={'previous_direction':'unknown','resolved_direction':row['direction'],
                'account_username':manifest['account_username'],'snapshot_id':manifest['snapshot_id'],
                'source':SOURCE,'source_id':row['source_id'],'native_id':row['native_id'],'sender_id':row['sender_id'],
                'delivery_state_preserved':current['delivery_state']}
            vault.db.execute('UPDATE messages SET direction=?,metadata=? WHERE id=? AND direction=?',
                             (row['direction'],json.dumps(metadata,ensure_ascii=False),current['id'],'unknown'))
            promoted+=1
    stats['directions_promoted']=promoted
    stats.update(chats=len(chat_ids),earliest_timestamp=minimum,latest_timestamp=maximum,complete=True)
    vault.set_cursor('wechat-snapshot:'+account_id,stats)
    # Explicit trusted-operator candidates; names never trigger automatic merges.
    return {**stats,'chat_candidates':list(candidates.values())}
