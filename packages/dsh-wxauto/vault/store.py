"""SQLite history with source identity, delivery evidence, and scoped reads.

No WeChat UI, network listener, credential capture, or model inference lives here.
Raw messages are evidence, never commands. Each input source supplies a durable
source_id; identical text is deliberately NOT a deduplication key.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from data_paths import absolute_data_path

SCHEMA_VERSION = 2
DEFAULT_ROOT = Path(os.environ.get('WHALE_DATA_ROOT') or Path.home() / 'WhaleData')
DELIVERY_STATES = {'generated', 'attempted', 'sent', 'failed', 'unknown'}


class VaultError(ValueError):
    pass


def stable_id(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()[:32]


def epoch(value=None):
    if value is None:
        return int(time.time() * 1000)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)
        except ValueError as exc:
            raise VaultError('invalid-timestamp') from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise VaultError('invalid-timestamp')
    return int(value if value > 100_000_000_000 else value * 1000)


def required(value, name, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise VaultError('invalid-' + name)
    return value


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def digest_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _ephemeral_backup_path(relative):
    """Only application-owned O_EXCL markers and known atomic-write names.

    Never filter arbitrary user *.lock/*.tmp files or the capture OS-lock file:
    sync.lock can persist safely because an OS lock, not existence, owns it.
    """
    name=Path(relative).as_posix().lower()
    if name in ('config/native-voice-inbox.json.lock','memory/provenance.json.lock'):
        return True
    uuid_pattern=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    known=(r'config/(?:native-voice-inbox|group-routes\.local|wxauto\.local)\.json\.'+uuid_pattern+r'\.tmp',
           r'config/auto-group-inbox/[0-9a-f]{64}\.json\.'+uuid_pattern+r'\.tmp',
           r'memory/\.provenance\.json\.'+uuid_pattern+r'\.tmp',
           r'snapshots/realtime-(?:inbox|outgoing|evidence)/'+uuid_pattern+r'\.tmp')
    return any(re.fullmatch(pattern,name) for pattern in known)


class Vault:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.directory = self.root / 'vault'
        self.directory.mkdir(exist_ok=True)
        self.file = self.directory / 'whale.sqlite'
        manifest = self.root / 'manifest.json'
        if manifest.exists():
            value = json.loads(manifest.read_text(encoding='utf-8-sig'))
            if value.get('format') != 'whale-data' or value.get('schemaVersion', 0) > SCHEMA_VERSION:
                raise VaultError('unsupported-data-manifest')
        else:
            value = None
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.file, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=15000')
        self._migrate()
        if value is None:
            value = {'format': 'whale-data', 'schemaVersion': SCHEMA_VERSION,
                     'createdAt': datetime.now(timezone.utc).isoformat(), 'paths': {'database': 'vault/whale.sqlite'}}
            atomic_json(manifest, value)
        elif value.get('schemaVersion',0)<SCHEMA_VERSION:
            value['schemaVersion']=SCHEMA_VERSION
            atomic_json(manifest,value)

    def _migrate(self):
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise VaultError('newer-schema-not-supported')
        if version == 0:
            self.db.executescript('''
            BEGIN IMMEDIATE;
            CREATE TABLE chats (
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL, chat_key TEXT NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('friend','group','unknown')), display_name TEXT NOT NULL,
              created_at INTEGER NOT NULL, UNIQUE(account_id,chat_key));
            CREATE TABLE aliases (chat_id TEXT NOT NULL REFERENCES chats(id), name TEXT NOT NULL,
              PRIMARY KEY(chat_id,name));
            CREATE TABLE messages (
              id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chats(id), source TEXT NOT NULL,
              source_id TEXT NOT NULL, native_id TEXT, sender TEXT NOT NULL, sender_id TEXT,
              direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing','unknown')),
              type TEXT NOT NULL, text TEXT NOT NULL, timestamp INTEGER NOT NULL,
              timestamp_quality TEXT NOT NULL, observed_at INTEGER NOT NULL,
              delivery_state TEXT NOT NULL, metadata TEXT NOT NULL,
              UNIQUE(chat_id,source,source_id));
            CREATE UNIQUE INDEX native_message ON messages(chat_id,native_id) WHERE native_id IS NOT NULL;
            CREATE INDEX message_time ON messages(chat_id,timestamp,id);
            CREATE TABLE sources (chat_id TEXT NOT NULL REFERENCES chats(id), source TEXT NOT NULL,
              source_id TEXT NOT NULL, message_id TEXT NOT NULL REFERENCES messages(id),
              PRIMARY KEY(chat_id,source,source_id));
            CREATE TABLE attachments (id TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES messages(id),
              path TEXT NOT NULL, sha256 TEXT NOT NULL,size INTEGER NOT NULL, metadata TEXT NOT NULL);
            CREATE TABLE deliveries (operation_id TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES messages(id),
              state TEXT NOT NULL,updated_at INTEGER NOT NULL,detail TEXT NOT NULL);
            CREATE TABLE summaries (chat_id TEXT NOT NULL REFERENCES chats(id),id TEXT PRIMARY KEY,
              text TEXT NOT NULL,source_ids TEXT NOT NULL,through_timestamp INTEGER NOT NULL,created_at INTEGER NOT NULL);
            CREATE TABLE sync_state (source TEXT PRIMARY KEY, cursor TEXT NOT NULL,updated_at INTEGER NOT NULL);
            PRAGMA user_version=1;
            COMMIT;
            ''')
        if version < 2:
            self.db.executescript('''
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS chat_redirects (
              old_id TEXT PRIMARY KEY REFERENCES chats(id), target_id TEXT NOT NULL REFERENCES chats(id),
              created_at INTEGER NOT NULL, CHECK(old_id<>target_id));
            PRAGMA user_version=2;
            COMMIT;
            ''')
        self.fts = False
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(id UNINDEXED,text,tokenize='trigram')")
            self.db.execute('INSERT INTO message_fts(id,text) SELECT id,text FROM messages WHERE id NOT IN (SELECT id FROM message_fts)')
            self.db.commit()
            self.fts = True
        except sqlite3.OperationalError:
            self.db.rollback()

    def close(self):
        if self.db:
            self.db.close()
            self.db = None

    def ensure_chat(self, account_id, chat_key, kind, display_name):
        required(account_id, 'account-id'); required(chat_key, 'chat-key')
        required(display_name, 'display-name')
        if kind not in ('friend', 'group', 'unknown'):
            raise VaultError('invalid-chat-kind')
        ident = stable_id('chat-v1', account_id, chat_key)
        with self.lock, self.db:
            canonical=self.resolve_chat_id(ident)
            if canonical!=ident:
                existing=self.db.execute('SELECT * FROM chats WHERE id=?',(canonical,)).fetchone()
                if kind!='unknown' and existing['kind'] not in ('unknown',kind):
                    raise VaultError('chat-kind-conflict')
                self.db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)',(canonical,display_name))
                return dict(existing)
            existing = self.db.execute('SELECT * FROM chats WHERE id=?', (ident,)).fetchone()
            if existing and existing['kind'] != kind and kind != 'unknown' and existing['kind'] != 'unknown':
                raise VaultError('chat-kind-conflict')
            self.db.execute('INSERT INTO chats VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name, kind=CASE WHEN chats.kind=\'unknown\' THEN excluded.kind ELSE chats.kind END',
                            (ident, account_id, chat_key, kind, display_name, epoch()))
            self.db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)', (ident, display_name))
        return dict(self.db.execute('SELECT * FROM chats WHERE id=?', (ident,)).fetchone())

    def resolve_chat_id(self,chat_id):
        required(chat_id,'chat-id')
        seen=set()
        while True:
            if chat_id in seen or len(seen)>100:raise VaultError('chat-redirect-cycle')
            seen.add(chat_id)
            row=self.db.execute('SELECT target_id FROM chat_redirects WHERE old_id=?',(chat_id,)).fetchone()
            if not row:return chat_id
            chat_id=row[0]

    def _scope(self, chat_id, allowed_chat_ids=None):
        required(chat_id, 'chat-id')
        chat_id=self.resolve_chat_id(chat_id)
        if allowed_chat_ids is not None and chat_id not in {self.resolve_chat_id(item) for item in allowed_chat_ids}:
            raise VaultError('chat-scope-denied')
        if not self.db.execute('SELECT 1 FROM chats WHERE id=?', (chat_id,)).fetchone():
            raise VaultError('chat-not-found')
        return chat_id

    def list_chats(self, account_id=None):
        query='SELECT * FROM chats WHERE id NOT IN (SELECT old_id FROM chat_redirects)';args=[]
        if account_id is not None:
            query+=' AND account_id=?';args.append(account_id)
        return [dict(r) for r in self.db.execute(query+' ORDER BY display_name,id',args)]

    def merge_chat(self,source_chat_id,target_chat_id):
        """Trusted local maintenance only; never expose as an autonomous AI tool.

        Keep all messages, IDs, attachments, deliveries and summary references.
        Colliding source/native keys are namespaced, explicitly marked, not lost.
        Old chat IDs redirect permanently so existing session routing still works.
        """
        with self.lock,self.db:
            source=self._scope(source_chat_id);target=self._scope(target_chat_id)
            if source==target:return {'source_chat_id':source_chat_id,'target_chat_id':target,'moved':0,'conflicts':0,'already_bound':True}
            src=self.db.execute('SELECT * FROM chats WHERE id=?',(source,)).fetchone()
            dst=self.db.execute('SELECT * FROM chats WHERE id=?',(target,)).fetchone()
            if src['account_id']!=dst['account_id']:raise VaultError('merge-account-mismatch')
            if src['kind']!=dst['kind']:raise VaultError('merge-kind-mismatch')
            rows=self.db.execute('SELECT * FROM messages WHERE chat_id=?',(source,)).fetchall()
            conflicts=0
            for row in rows:
                metadata=json.loads(row['metadata']);source_id=row['source_id'];native_id=row['native_id']
                collided_source=self.db.execute('SELECT 1 FROM messages WHERE chat_id=? AND source=? AND source_id=?',(target,row['source'],source_id)).fetchone()
                collided_native=self.db.execute('SELECT 1 FROM messages WHERE chat_id=? AND native_id=?',(target,native_id)).fetchone() if native_id else None
                if collided_source or collided_native:
                    conflicts+=1
                    metadata['chat_merge_conflict']={'from_chat_id':source,'source_id':source_id,'native_id':native_id}
                    if collided_source:source_id='merged:'+source+':'+source_id
                    if collided_native:native_id=None
                self.db.execute('UPDATE messages SET chat_id=?,source_id=?,native_id=?,metadata=? WHERE id=?',
                    (target,source_id,native_id,json.dumps(metadata,ensure_ascii=False),row['id']))
            for row in self.db.execute('SELECT * FROM sources WHERE chat_id=?',(source,)).fetchall():
                source_id=row['source_id']
                if self.db.execute('SELECT 1 FROM sources WHERE chat_id=? AND source=? AND source_id=?',(target,row['source'],source_id)).fetchone():
                    source_id='merged:'+source+':'+source_id
                self.db.execute('UPDATE sources SET chat_id=?,source_id=? WHERE chat_id=? AND source=? AND source_id=?',
                    (target,source_id,source,row['source'],row['source_id']))
            self.db.execute('UPDATE summaries SET chat_id=? WHERE chat_id=?',(target,source))
            self.db.execute('INSERT OR IGNORE INTO aliases SELECT ?,name FROM aliases WHERE chat_id=?',(target,source))
            self.db.execute('INSERT INTO chat_redirects VALUES(?,?,?)',(source,target,epoch()))
            self.db.execute('UPDATE chat_redirects SET target_id=? WHERE target_id=? AND old_id<>?',(target,source,source))
        return {'source_chat_id':source_chat_id,'target_chat_id':target,'moved':len(rows),'conflicts':conflicts,'already_bound':False}

    def record_message(self, chat_id, source, source_id, text='', sender='', sender_id=None,
                       direction='incoming', type='text', timestamp=None, timestamp_quality=None,
                       native_id=None, delivery_state=None, metadata=None, **unused):
        chat_id=self._scope(chat_id)
        required(source, 'source'); required(source_id, 'source-id', 4000)
        if not isinstance(text, str) or len(text) > 8_000_000:
            raise VaultError('invalid-message-text')
        if direction not in ('incoming', 'outgoing', 'unknown'):
            raise VaultError('invalid-direction')
        state = delivery_state or ('received' if direction == 'incoming' else 'unknown')
        if state not in DELIVERY_STATES | {'received'}:
            raise VaultError('invalid-delivery-state')
        if native_id is not None:
            required(native_id, 'native-id')
        stamp = epoch(timestamp)
        with self.lock, self.db:
            previous = self.db.execute('SELECT message_id FROM sources WHERE chat_id=? AND source=? AND source_id=?', (chat_id, source, source_id)).fetchone()
            if previous:
                return {'id': previous[0], 'inserted': False}
            linked = self.db.execute('SELECT id FROM messages WHERE chat_id=? AND native_id=?', (chat_id, native_id)).fetchone() if native_id else None
            ident = linked[0] if linked else stable_id('message-v1', chat_id, source, source_id)
            if not linked:
                self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, chat_id, source, source_id, native_id, str(sender), sender_id, direction, str(type), text,
                     stamp, timestamp_quality or ('source' if timestamp is not None else 'observed'), epoch(), state,
                     json.dumps(metadata or {}, ensure_ascii=False)))
                if self.fts:
                    self.db.execute('INSERT INTO message_fts(id,text) VALUES(?,?)', (ident, text))
            self.db.execute('INSERT INTO sources VALUES(?,?,?,?)', (chat_id, source, source_id, ident))
        return {'id': ident, 'inserted': not bool(linked), 'linked': bool(linked)}

    def record_native_voice_bundle(self, chat_id, account_id, source_id, native_id,
                                   sender, sender_id, timestamp, raw_text, transcript,
                                   backend, audio):
        """One writer transaction: immutable native voice, audio, derived ASR.

        Files must already exist under DataRoot; this operation never decodes,
        invokes a model or sends/replays a message.
        """
        chat_id=self._scope(chat_id)
        required(account_id,'account-id');required(source_id,'source-id',4000)
        required(sender_id,'sender-id');required(sender,'sender')
        if not isinstance(native_id,str) or not re.fullmatch(r'wechat-server:[1-9][0-9]*',native_id):
            raise VaultError('voice-native-id-invalid')
        if not isinstance(raw_text,str) or len(raw_text)>8_000_000:raise VaultError('voice-raw-text-invalid')
        required(transcript,'voice-transcript',1_000_000)
        if not isinstance(backend,(str,dict)) or not backend:raise VaultError('voice-backend-invalid')
        try:backend_json=json.dumps(backend,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        except (TypeError,ValueError) as exc:raise VaultError('voice-backend-invalid') from exc
        if len(backend_json)>16000:raise VaultError('voice-backend-invalid')
        if not isinstance(audio,list) or not 1<=len(audio)<=2:raise VaultError('voice-audio-invalid')
        files=[];roles=set()
        def checked_file(item):
            if not isinstance(item,dict) or item.get('role') not in ('source','decoded') or item['role'] in roles:raise VaultError('voice-audio-role-invalid')
            relative=item.get('path');expected=item.get('sha256')
            if not isinstance(relative,str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts or '\\' in relative or ':' in relative:raise VaultError('voice-audio-path-invalid')
            if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise VaultError('voice-audio-sha-invalid')
            file=self.root/relative
            try:
                for p in [file,*file.parents]:
                    if p==self.root:break
                    if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise VaultError('voice-audio-link-invalid')
                resolved=file.resolve(strict=True)
                if not resolved.is_relative_to(self.root):raise VaultError('voice-audio-path-invalid')
                before=file.stat()
                if not file.is_file() or before.st_nlink!=1:raise VaultError('voice-audio-link-invalid')
                if digest_file(file)!=expected:raise VaultError('voice-audio-hash-mismatch')
                after=file.stat()
                fingerprint=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_nlink)
                if fingerprint(before)!=fingerprint(after):raise VaultError('voice-audio-changed')
            except OSError as exc:raise VaultError('voice-audio-unavailable') from exc
            metadata=item.get('metadata',{})
            if not isinstance(metadata,dict):raise VaultError('voice-audio-metadata-invalid')
            try:json.dumps(metadata,allow_nan=False)
            except (ValueError,TypeError) as exc:raise VaultError('voice-audio-metadata-invalid') from exc
            roles.add(item['role'])
            return {'path':resolved.relative_to(self.root).as_posix(),'sha256':expected,'size':after.st_size,'role':item['role'],'metadata':metadata,'file':file,'fingerprint':fingerprint(after)}
        for item in audio:files.append(checked_file(item))
        if 'source' not in roles:raise VaultError('voice-source-audio-required')
        files.sort(key=lambda f:f['role'])
        if timestamp is None:raise VaultError('voice-timestamp-required')
        stamp=epoch(timestamp);transcript_sha=hashlib.sha256(transcript.encode()).hexdigest()
        signature=stable_id('voice-bundle-v1',native_id,sender_id,stamp,raw_text,backend_json,transcript_sha,
                            [(f['role'],f['path'],f['sha256'],f['metadata']) for f in files])
        evidence_source=stable_id('voice-asr-v1',native_id,backend_json,[(f['role'],f['sha256']) for f in files],transcript_sha)
        event_key=stable_id('voice-asr-event-v1',source_id,backend_json)
        with self.lock,self.db:
            chat=self.db.execute('SELECT account_id FROM chats WHERE id=?',(chat_id,)).fetchone()
            if not chat or chat['account_id']!=account_id:raise VaultError('voice-account-conflict')
            native=self.db.execute('SELECT * FROM messages WHERE chat_id=? AND native_id=?',(chat_id,native_id)).fetchone()
            if native and (native['type']!='voice' or native['direction']!='incoming' or native['sender_id']!=sender_id or native['timestamp']!=stamp or native['text']!=raw_text):
                raise VaultError('voice-native-message-conflict')
            old_event=self.db.execute("SELECT m.* FROM sources s JOIN messages m ON m.id=s.message_id WHERE s.chat_id=? AND s.source='voice-asr-event' AND s.source_id=?",(chat_id,event_key)).fetchone()
            if old_event and json.loads(old_event['metadata']).get('bundleSignature')!=signature:raise VaultError('voice-event-source-conflict')
            primary_source=self.db.execute("SELECT message_id FROM sources WHERE chat_id=? AND source='wechat-native-voice' AND source_id=?",(chat_id,source_id)).fetchone()
            raw_id=native['id'] if native else stable_id('message-v1',chat_id,'wechat-native-voice',source_id)
            if primary_source and primary_source[0]!=raw_id:raise VaultError('voice-event-source-conflict')
            def insert(ident,source,sid,typ,text,direction,nid,meta):
                self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,chat_id,source,sid,nid,sender,sender_id,direction,typ,text,stamp,'reported',epoch(),'received' if direction=='incoming' else 'unknown',json.dumps(meta,ensure_ascii=False)))
                if self.fts:self.db.execute('INSERT INTO message_fts(id,text) VALUES(?,?)',(ident,text))
            if not native:insert(raw_id,'wechat-native-voice',source_id,'voice',raw_text,'incoming',native_id,{})
            self.db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?)',(chat_id,'wechat-native-voice',source_id,raw_id))
            attachment_ids=[]
            for f in files:
                ident=stable_id(raw_id,f['path'],f['sha256']);attachment_ids.append(ident)
                metadata={**f['metadata'],'role':f['role'],'nativeId':native_id}
                self.db.execute('INSERT OR IGNORE INTO attachments VALUES(?,?,?,?,?,?)',(ident,raw_id,f['path'],f['sha256'],f['size'],json.dumps(metadata,ensure_ascii=False)))
            evidence_id=stable_id('message-v1',chat_id,'voice-asr-evidence',evidence_source)
            old=self.db.execute('SELECT * FROM messages WHERE id=?',(evidence_id,)).fetchone()
            if old and (old['chat_id']!=chat_id or old['text']!=transcript or json.loads(old['metadata']).get('parentMessageId')!=raw_id or json.loads(old['metadata']).get('bundleSignature')!=signature):raise VaultError('voice-evidence-conflict')
            if not old:insert(evidence_id,'voice-asr-evidence',evidence_source,'voice-evidence',transcript,'unknown',None,{'parentMessageId':raw_id,'nativeId':native_id,'backend':backend,'transcriptSha256':transcript_sha,'audio':[{'attachmentId':i,'sha256':f['sha256'],'role':f['role']} for i,f in zip(attachment_ids,files)],'bundleSignature':signature})
            self.db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?)',(chat_id,'voice-asr-evidence',evidence_source,evidence_id))
            self.db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?)',(chat_id,'voice-asr-event',event_key,evidence_id))
            for f in files:
                for p in [f['file'],*f['file'].parents]:
                    if p==self.root:break
                    if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise VaultError('voice-audio-changed')
                s=f['file'].stat()
                if (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_nlink)!=f['fingerprint'] or digest_file(f['file'])!=f['sha256']:raise VaultError('voice-audio-changed')
        return {'id':raw_id,'evidenceId':evidence_id,'messageIds':[raw_id,evidence_id],'attachmentIds':attachment_ids,'inserted':not bool(old)}

    def record_delivery(self, message_id, operation_id, state, detail=None):
        required(operation_id, 'operation-id')
        if state not in DELIVERY_STATES:
            raise VaultError('invalid-delivery-state')
        with self.lock, self.db:
            m = self.db.execute('SELECT direction FROM messages WHERE id=?', (message_id,)).fetchone()
            if not m or m[0] != 'outgoing':
                raise VaultError('delivery-message-invalid')
            prev = self.db.execute('SELECT * FROM deliveries WHERE operation_id=?', (operation_id,)).fetchone()
            if prev and prev['message_id'] != message_id:
                raise VaultError('delivery-operation-conflict')
            if prev and prev['state'] in ('sent', 'failed', 'unknown') and prev['state'] != state:
                raise VaultError('delivery-outcome-already-final')
            self.db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?) ON CONFLICT(operation_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at,detail=excluded.detail',
                (operation_id, message_id, state, epoch(), json.dumps(detail or {}, ensure_ascii=False)))
            rows = [r[0] for r in self.db.execute('SELECT state FROM deliveries WHERE message_id=?', (message_id,))]
            aggregate = 'unknown' if 'unknown' in rows else 'failed' if 'failed' in rows else 'attempted' if 'attempted' in rows else 'generated' if 'generated' in rows else 'sent'
            self.db.execute('UPDATE messages SET delivery_state=? WHERE id=?', (aggregate, message_id))
        return {'message_id': message_id, 'state': aggregate}

    def record_delivery_observation(self, message_id, operation_id, state, detail=None):
        """Import durable observations out of order without regressing outcomes."""
        required(operation_id,'operation-id')
        if state not in DELIVERY_STATES:raise VaultError('invalid-delivery-state')
        with self.lock:
            previous=self.db.execute('SELECT * FROM deliveries WHERE operation_id=?',(operation_id,)).fetchone()
            if previous and previous['message_id']!=message_id:raise VaultError('delivery-operation-conflict')
            rank=lambda s:0 if s=='generated' else 1 if s=='attempted' else 2
            if previous and rank(previous['state'])>rank(state):
                message=self.db.execute('SELECT direction,delivery_state FROM messages WHERE id=?',(message_id,)).fetchone()
                if not message or message['direction']!='outgoing':raise VaultError('delivery-message-invalid')
                return {'message_id':message_id,'state':message['delivery_state'],'superseded':True}
            return self.record_delivery(message_id,operation_id,state,detail)

    def attach(self, message_id, path, metadata=None):
        path = Path(path).resolve(strict=True)
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise VaultError('attachment-outside-data-root') from exc
        if not path.is_file() or path.is_symlink():
            raise VaultError('attachment-invalid')
        sha = digest_file(path)
        ident = stable_id(message_id, relative.as_posix(), sha)
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO attachments VALUES(?,?,?,?,?,?)',
                (ident, message_id, relative.as_posix(), sha, path.stat().st_size, json.dumps(metadata or {}, ensure_ascii=False)))
        return {'id': ident, 'path': relative.as_posix(), 'sha256': sha}

    def _format(self, row):
        value = dict(row)
        value['metadata'] = json.loads(value['metadata'])
        if value['source']=='vision-evidence':
            value['parent_message_id']=self._evidence_parent(value['chat_id'],value['metadata'])
        if value['source']=='voice-asr-evidence':
            parent=self.db.execute("SELECT id FROM messages WHERE id=? AND chat_id=? AND type='voice' AND direction='incoming' AND native_id=?",(value['metadata'].get('parentMessageId'),value['chat_id'],value['metadata'].get('nativeId'))).fetchone()
            value['parent_message_id']=parent[0] if parent else None
        value['attachments'] = [dict(r) for r in self.db.execute('SELECT id,path,sha256,size,metadata FROM attachments WHERE message_id=?', (value['id'],))]
        for a in value['attachments']:
            a['metadata'] = json.loads(a['metadata'])
            # Stored metadata is historical evidence and may contain the old
            # machine's absolute path. Only the canonical relative DB column
            # locates this attachment on the current root. This is not a send
            # grant: the worker still checks current-chat receipts and hashes.
            metadata = a['metadata'] if isinstance(a['metadata'], dict) else {}
            metadata.pop('path', None)
            metadata.pop('relativePath', None)
            a['metadata'] = metadata
            a['absolutePath'] = None
            a['pathStatus'] = 'invalid'
            try:
                current = absolute_data_path(a['path'], root=self.root)
                canonical = current.relative_to(self.root).as_posix()
                if canonical != a['path']:
                    raise ValueError('noncanonical')
                metadata['relativePath'] = canonical
            except (RuntimeError, ValueError, OSError):
                a['path'] = None
                continue
            try:
                info = current.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    a['pathStatus'] = 'unavailable'
                    continue
            except FileNotFoundError:
                a['pathStatus'] = 'missing'
                continue
            except OSError:
                a['pathStatus'] = 'unavailable'
                continue
            a['absolutePath'] = str(current)
            metadata['path'] = str(current)
            a['pathStatus'] = 'available'
        return value

    def _evidence_parent(self, chat_id, metadata):
        source_id=metadata.get('parentSourceId') if isinstance(metadata,dict) else None
        if not isinstance(source_id,str) or not source_id:return None
        parent=self.db.execute('SELECT message_id FROM sources WHERE chat_id=? AND source=? AND source_id=?',
            (chat_id,'wxauto-incoming',source_id)).fetchone()
        return parent[0] if parent else None

    def search(self, chat_id, query='', start=None, end=None, limit=50, offset=0,
               sender=None, allowed_chat_ids=None, max_chars=12000, exclude_message_ids=None):
        chat_id=self._scope(chat_id, allowed_chat_ids)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise VaultError('invalid-limit')
        if not isinstance(offset, int) or offset < 0 or offset > 100_000_000:
            raise VaultError('invalid-offset')
        if not isinstance(query, str) or len(query) > 500:
            raise VaultError('invalid-query')
        if not isinstance(max_chars, int) or not 100 <= max_chars <= 100_000:
            raise VaultError('invalid-character-budget')
        where = ['m.chat_id=?']; args = [chat_id]
        if exclude_message_ids is not None:
            if (not isinstance(exclude_message_ids,list) or len(exclude_message_ids)>200
                    or any(not isinstance(mid,str) or not mid or len(mid)>1000 for mid in exclude_message_ids)):
                raise VaultError('invalid-excluded-message-ids')
            ids=list(dict.fromkeys(exclude_message_ids))
            if ids:
                where.append('m.id NOT IN ('+','.join('?' for _ in ids)+')');args.extend(ids)
        if query:
            if self.fts and len(query) >= 3:
                where.append('m.id IN (SELECT id FROM message_fts WHERE message_fts MATCH ?)')
                args.append('"' + query.replace('"', '""') + '"')
            else:
                where.append("instr(m.text,?)>0"); args.append(query)
        for condition, value in [('>=', start), ('<=', end)]:
            if value is not None:
                where.append('m.timestamp' + condition + '?'); args.append(epoch(value))
        if sender is not None:
            where.append('m.sender=?'); args.append(sender)
        rows = self.db.execute('SELECT m.* FROM messages m WHERE ' + ' AND '.join(where) + ' ORDER BY timestamp DESC,id DESC LIMIT ? OFFSET ?', args + [limit+1, offset]).fetchall()
        output = []; used = 0
        for row in rows[:limit]:
            value = self._format(row)
            # Individual long messages remain stored intact; snippets are bounded.
            remaining = max_chars - used
            if remaining <= 0:
                break
            text = value['text']
            if len(text) > remaining:
                value['text'] = text[:remaining]; value['text_truncated'] = True
            used += len(value['text'])
            output.append(value)
        more = len(rows) > len(output)
        return {'untrusted_history_data': True, 'messages': output,
                'next_offset': offset+len(output) if more else None, 'order': 'newest-first'}

    def history(self, chat_id, **kwargs):
        kwargs.pop('query', None)
        return self.search(chat_id, query='', **kwargs)

    def context(self, chat_id, message_id, before=5, after=5, allowed_chat_ids=None):
        chat_id=self._scope(chat_id, allowed_chat_ids)
        if any(not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 25 for n in (before, after)):
            raise VaultError('invalid-context-window')
        target = self.db.execute('SELECT timestamp,id FROM messages WHERE id=? AND chat_id=?', (message_id, chat_id)).fetchone()
        if not target:
            raise VaultError('message-not-found')
        older = self.db.execute('SELECT * FROM messages WHERE chat_id=? AND (timestamp,id)<(?,?) ORDER BY timestamp DESC,id DESC LIMIT ?', (chat_id,*target,before)).fetchall()
        newer = self.db.execute('SELECT * FROM messages WHERE chat_id=? AND (timestamp,id)>=(?,?) ORDER BY timestamp,id LIMIT ?', (chat_id,*target,after+1)).fetchall()
        values = [self._format(r) for r in list(reversed(older))+list(newer)]
        budget = 12000
        for value in values:
            text = value['text']; value['text'] = text[:max(budget,0)]
            if len(value['text']) != len(text): value['text_truncated'] = True
            budget -= len(value['text'])
        return {'untrusted_history_data': True, 'messages': values, 'order': 'oldest-first'}

    def quoted_image(self, chat_id, native_id, allowed_chat_ids=None):
        """Locate the image a quote reply points at: its downloaded file and vision evidence.

        The snapshot row carries the server ID; the UI download that received
        the same picture is the nearest incoming image just after it.
        """
        chat_id=self._scope(chat_id,allowed_chat_ids)
        if not isinstance(native_id,str) or not re.fullmatch(r'wechat-server:[1-9][0-9]{0,19}',native_id):
            raise VaultError('invalid-native-id')
        original=self.db.execute("SELECT id,timestamp FROM messages WHERE chat_id=? AND native_id=? AND type IN ('image','emotion') ORDER BY timestamp LIMIT 1",
                                 (chat_id,native_id)).fetchone()
        if not original:
            return {'found':False}
        row=self.db.execute("""SELECT a.path FROM messages m JOIN attachments a ON a.message_id=m.id
            WHERE m.chat_id=? AND m.direction='incoming' AND m.type IN ('image','emotion') AND (m.id=? OR (m.source='wxauto-incoming' AND m.timestamp BETWEEN ? AND ?))
            ORDER BY m.id=? DESC, ABS(m.timestamp-?) LIMIT 1""",
            (chat_id,original['id'],original['timestamp']-5000,original['timestamp']+180000,original['id'],original['timestamp'])).fetchone()
        if not row:
            return {'found':True,'received':False}
        evidence=self.db.execute("SELECT text FROM messages WHERE chat_id=? AND source='vision-evidence' AND json_extract(metadata,'$.path')=? ORDER BY timestamp DESC LIMIT 1",
                                 (chat_id,row['path'])).fetchone()
        summary=None
        if evidence:
            try:summary=json.loads(evidence['text']).get('summary')
            except (ValueError,AttributeError):summary=None
        return {'found':True,'received':True,'path':row['path'],
                'summary':summary[:800] if isinstance(summary,str) else None}

    def get_message_text(self, chat_id, message_id, offset=0, max_chars=6000, allowed_chat_ids=None):
        """Read exact original text in bounded chunks; offsets are code points."""
        chat_id=self._scope(chat_id,allowed_chat_ids)
        required(message_id,'message-id')
        if type(offset) is not int or offset<0:raise VaultError('invalid-text-offset')
        if type(max_chars) is not int or not 1<=max_chars<=6000:raise VaultError('invalid-text-budget')
        row=self.db.execute('SELECT * FROM messages WHERE id=? AND chat_id=?',(message_id,chat_id)).fetchone()
        if not row:raise VaultError('message-not-found')
        text=row['text'];total=len(text)
        if offset>total:raise VaultError('text-offset-out-of-range')
        message={k:row[k] for k in ('id','chat_id','source','source_id','native_id','timestamp','direction','delivery_state')}
        if row['source']=='vision-evidence':message['parent_message_id']=self._evidence_parent(chat_id,json.loads(row['metadata']))
        if row['source']=='voice-asr-evidence':
            metadata=json.loads(row['metadata'])
            parent=self.db.execute("SELECT id FROM messages WHERE id=? AND chat_id=? AND type='voice' AND direction='incoming' AND native_id=?",(metadata.get('parentMessageId'),chat_id,metadata.get('nativeId'))).fetchone()
            message['parent_message_id']=parent[0] if parent else None
        for field,budget in (('sender',256),('type',100),('timestamp_quality',100)):
            message[field]=row[field][:budget]
            if len(row[field])>budget:message[field+'_truncated']=True
        result={'untrusted_history_data':True,'message':message,'text_offset':offset,
            'total_characters':total,'offset_unit':'unicode-code-points',
            'attachments_count':self.db.execute('SELECT count(*) FROM attachments WHERE message_id=?',(message_id,)).fetchone()[0]}
        def render(count):
            message['text']=text[offset:offset+count]
            result['next_offset']=offset+count if offset+count<total else None
            message['text_truncated']=offset>0 or count<total
            return len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf-16-le'))//2
        low,high=0,min(max_chars,total-offset)
        while low<high:
            middle=(low+high+1)//2
            if render(middle)<=12000:low=middle
            else:high=middle-1
        if render(low)>12000 or (low==0 and offset<total):raise VaultError('message-envelope-exceeds-budget')
        return result

    def save_summary(self, chat_id, text, source_ids, through_timestamp, id=None):
        chat_id=self._scope(chat_id)
        required(text, 'summary', 12000)
        if not isinstance(source_ids, list) or not source_ids or len(source_ids)>500:
            raise VaultError('invalid-summary-sources')
        for mid in source_ids:
            if not self.db.execute('SELECT 1 FROM messages WHERE id=? AND chat_id=?', (mid,chat_id)).fetchone():
                raise VaultError('summary-source-scope-denied')
        ident = id or uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute('INSERT INTO summaries VALUES(?,?,?,?,?,?)', (chat_id,ident,text,json.dumps(source_ids),epoch(through_timestamp),epoch()))
        return {'id':ident}

    def get_summaries(self, chat_id, limit=3, allowed_chat_ids=None):
        chat_id=self._scope(chat_id,allowed_chat_ids)
        if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=10:
            raise VaultError('invalid-limit')
        rows=self.db.execute('SELECT * FROM summaries WHERE chat_id=? ORDER BY through_timestamp DESC,created_at DESC,id DESC LIMIT ?', (chat_id,limit)).fetchall()
        result=[dict(r) for r in rows]
        for item in result:item['source_ids']=json.loads(item['source_ids'])
        return {'untrusted_history_data':True,'summaries':result}

    def save_checkpoint(self, chat_id, text, source_ids, allowed_chat_ids=None):
        """Model-written continuity note; coverage derives from actual citations.

        This is not proof of semantic accuracy or authorization to execute a
        quoted request. The caller cannot invent the covered timestamp.
        """
        chat_id=self._scope(chat_id,allowed_chat_ids)
        required(text,'checkpoint',6000)
        if (not isinstance(source_ids,list) or not 1<=len(source_ids)<=50
                or any(not isinstance(mid,str) or not mid for mid in source_ids)):
            raise VaultError('invalid-checkpoint-sources')
        ids=sorted(set(source_ids))
        rows=self.db.execute('SELECT id,timestamp FROM messages WHERE chat_id=? AND id IN ('+
            ','.join('?' for _ in ids)+')',[chat_id,*ids]).fetchall()
        if len(rows)!=len(ids):raise VaultError('checkpoint-source-scope-denied')
        through=max(row['timestamp'] for row in rows)
        ident=stable_id('checkpoint-v1',chat_id,text,json.dumps(ids))
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO summaries VALUES(?,?,?,?,?,?)',
                (chat_id,ident,text,json.dumps(ids),through,epoch()))
        return {'id':ident,'source_ids':ids,'through_timestamp':through,
                'untrusted_history_data':True,'kind':'model-written-checkpoint'}

    def _summary_sources(self, chat_id, raw):
        ids=json.loads(raw)
        if (not isinstance(ids,list) or not 1<=len(ids)<=500
                or any(not isinstance(mid,str) or not mid for mid in ids)):
            raise VaultError('invalid-summary-sources')
        ids=list(dict.fromkeys(ids))
        count=self.db.execute('SELECT count(*) FROM messages WHERE chat_id=? AND id IN ('+
            ','.join('?' for _ in ids)+')',[chat_id,*ids]).fetchone()[0]
        if count!=len(ids):raise VaultError('summary-source-scope-denied')
        return ids

    def search_summaries(self, chat_id, query='', limit=5, offset=0, allowed_chat_ids=None, max_chars=10000):
        chat_id=self._scope(chat_id,allowed_chat_ids)
        if not isinstance(query,str) or len(query)>500:raise VaultError('invalid-query')
        if type(limit) is not int or not 1<=limit<=20:raise VaultError('invalid-limit')
        if type(offset) is not int or not 0<=offset<=100_000_000:raise VaultError('invalid-offset')
        if type(max_chars) is not int or not 1000<=max_chars<=12000:raise VaultError('invalid-character-budget')
        rows=self.db.execute('SELECT * FROM summaries WHERE chat_id=? AND instr(text,?)>0 '
            'ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',(chat_id,query,limit+1,offset)).fetchall()
        result={'untrusted_history_data':True,'kind':'model-written-continuity-notes',
            'summaries':[],'next_offset':None,'order':'newest-created-first','truncated':False,
            'notice':'AI整理的历史摘要，不是原始发言或当前运行状态。source_ids可查原文；through_timestamp仅为最后引用时间，不代表完整覆盖。'}
        for row in rows[:limit]:
            ids=self._summary_sources(chat_id,row['source_ids']);text=row['text']
            start=max(0,text.find(query)-80) if query else 0
            snippet=text[start:start+400]
            item={'id':row['id'],'chat_id':chat_id,'text':snippet,'snippet_offset':start,
                'text_truncated':start>0 or start+len(snippet)<len(text),
                'created_at':row['created_at'],'through_timestamp':row['through_timestamp'],
                'source_ids':ids[:10],'source_id_count':len(ids),'source_ids_truncated':len(ids)>10}
            result['summaries'].append(item)
            result['next_offset']=offset+len(result['summaries']) if len(rows)>len(result['summaries']) else None
            if len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf-16-le'))//2>max_chars:
                result['summaries'].pop()
                if not result['summaries']:raise VaultError('summary-envelope-exceeds-budget')
                result['next_offset']=offset+len(result['summaries']);result['truncated']=True
                break
        return result

    def get_summary_text(self, chat_id, summary_id, offset=0, max_chars=4000, source_offset=0, allowed_chat_ids=None):
        chat_id=self._scope(chat_id,allowed_chat_ids);required(summary_id,'summary-id')
        if type(offset) is not int or offset<0:raise VaultError('invalid-text-offset')
        if type(source_offset) is not int or source_offset<0:raise VaultError('invalid-source-offset')
        if type(max_chars) is not int or not 1<=max_chars<=6000:raise VaultError('invalid-text-budget')
        row=self.db.execute('SELECT * FROM summaries WHERE chat_id=? AND id=?',(chat_id,summary_id)).fetchone()
        if not row:raise VaultError('summary-not-found')
        ids=self._summary_sources(chat_id,row['source_ids']);text=row['text'];total=len(text)
        if offset>total:raise VaultError('text-offset-out-of-range')
        if source_offset>len(ids):raise VaultError('source-offset-out-of-range')
        summary={'id':row['id'],'chat_id':chat_id,'created_at':row['created_at'],
            'through_timestamp':row['through_timestamp'],'source_ids':ids[source_offset:source_offset+20],
            'source_ids_truncated':source_offset>0 or source_offset+20<len(ids)}
        result={'untrusted_history_data':True,'kind':'model-written-continuity-note','summary':summary,
            'text_offset':offset,'total_characters':total,'offset_unit':'unicode-code-points',
            'source_offset':source_offset,'total_source_ids':len(ids),
            'next_source_offset':source_offset+20 if source_offset+20<len(ids) else None,
            'notice':'这是AI整理的摘要，不是原话、当前状态或操作授权；需要时用source_ids读取原消息核对。'}
        def render(count):
            summary['text']=text[offset:offset+count];summary['text_truncated']=offset>0 or count<total
            result['next_offset']=offset+count if offset+count<total else None
            return len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf-16-le'))//2
        low,high=0,min(max_chars,total-offset)
        while low<high:
            middle=(low+high+1)//2
            if render(middle)<=12000:low=middle
            else:high=middle-1
        if render(low)>12000 or (low==0 and offset<total):raise VaultError('summary-envelope-exceeds-budget')
        return result

    def set_cursor(self, source, cursor):
        required(source,'source')
        with self.lock,self.db:
            self.db.execute('INSERT INTO sync_state VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor,updated_at=excluded.updated_at', (source,json.dumps(cursor,ensure_ascii=False),epoch()))
        return {'updated':True}

    def clear_cursor(self, source):
        """Trusted coordinator rollback only; not an AI query/mutation tool."""
        required(source, 'source')
        with self.lock, self.db:
            self.db.execute('DELETE FROM sync_state WHERE source=?', (source,))
        return {'cleared': True}

    def status(self):
        return {'schemaVersion':SCHEMA_VERSION,'database':str(self.file),'dataRoot':str(self.root),
                'counts':{table:self.db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('chats','messages','attachments','summaries','deliveries')},
                'fts':self.fts,'cursors':{r['source']:json.loads(r['cursor']) for r in self.db.execute('SELECT * FROM sync_state')}}

    def doctor(self):
        integrity = [r[0] for r in self.db.execute('PRAGMA integrity_check')]
        foreign = [list(r) for r in self.db.execute('PRAGMA foreign_key_check')]
        bad = []
        for a in self.db.execute('SELECT id,path,sha256 FROM attachments'):
            file = (self.root/a['path']).resolve()
            if not file.is_relative_to(self.root) or not file.is_file() or digest_file(file)!=a['sha256']:
                bad.append(a['id'])
        return {'ok':integrity==['ok'] and not foreign and not bad,'integrity':integrity,'foreignKeyErrors':foreign,'badAttachments':bad}

    def backup(self, destination=None):
        sections = ('config','memory','persona','voice','media','outbox','snapshots')
        destination = Path(destination).resolve() if destination else self.root/'backups'/('backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
        if destination.exists():
            raise VaultError('backup-destination-exists')
        if destination == self.root or self.root.is_relative_to(destination):
            raise VaultError('backup-destination-invalid')
        # A backup inside a copied tree would recursively discover its own
        # newly written files. Reject before creating any destination files.
        if any(destination.is_relative_to((self.root/section).resolve()) for section in sections):
            raise VaultError('backup-destination-inside-source')
        destination.mkdir(parents=True)
        records = []
        # Serialize writes for the consistent DB + attachment catalog snapshot.
        with self.lock:
            target = destination/'vault/whale.sqlite';target.parent.mkdir()
            with closing(sqlite3.connect(target)) as db:
                self.db.backup(db)
            for section in sections:
                folder = self.root/section
                if folder.is_symlink() or folder.is_junction():
                    raise VaultError('backup-link-denied')
                if folder.exists():
                    if not folder.is_dir():
                        raise VaultError('backup-section-not-directory')
                    for source in folder.rglob('*'):
                        if source.is_symlink() or source.is_junction():
                            raise VaultError('backup-link-denied')
                        if source.is_file():
                            relative=source.relative_to(self.root)
                            if _ephemeral_backup_path(relative):continue
                            out=destination/relative
                            out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,out)
            shutil.copyfile(self.root/'manifest.json',destination/'manifest.json')
        for source in destination.rglob('*'):
            if source.is_file():records.append({'path':source.relative_to(destination).as_posix(),'size':source.stat().st_size,'sha256':digest_file(source)})
        atomic_json(destination/'backup-manifest.json',{'format':'whale-backup','version':1,'complete':True,'files':records})
        return {'directory':str(destination),'files':len(records),'complete':True}

    def request(self, op, args=None):
        methods = {'ensure_chat':self.ensure_chat,'record_message':self.record_message,'record_native_voice_bundle':self.record_native_voice_bundle,'record_delivery':self.record_delivery,
                   'record_delivery_observation':self.record_delivery_observation,
                   'attach':self.attach,'search':self.search,'quoted_image':self.quoted_image,'history':self.history,'context':self.context,'get_message_text':self.get_message_text,
                   'save_summary':self.save_summary,'save_checkpoint':self.save_checkpoint,'get_summaries':self.get_summaries,
                   'search_summaries':self.search_summaries,'get_summary_text':self.get_summary_text,'list_chats':self.list_chats,
                   'merge_chat':self.merge_chat,'resolve_chat_id':self.resolve_chat_id,
                   'set_cursor':self.set_cursor,'clear_cursor':self.clear_cursor,'status':self.status,
                   'backup':self.backup,'doctor':self.doctor}
        if op not in methods:
            raise VaultError('unsupported-operation')
        with self.lock:
            return methods[op](**(args or {}))


def restore(backup, destination):
    backup=Path(backup).resolve(strict=True);destination=Path(destination).resolve()
    manifest=json.loads((backup/'backup-manifest.json').read_text(encoding='utf-8-sig'))
    if manifest.get('format')!='whale-backup' or manifest.get('version')!=1 or manifest.get('complete') is not True:
        raise VaultError('invalid-backup-manifest')
    if not isinstance(manifest.get('files'),list) or not {'manifest.json','vault/whale.sqlite'}.issubset({r.get('path') for r in manifest['files'] if isinstance(r,dict)}):
        raise VaultError('incomplete-backup-manifest')
    if destination.exists():raise VaultError('restore-destination-exists')
    if backup==destination or backup.is_relative_to(destination) or destination.is_relative_to(backup):
        raise VaultError('restore-destination-invalid')
    files=[];skipped_ephemeral=0
    for record in manifest['files']:
        relative=Path(record['path'])
        source=(backup/relative).resolve(strict=True)
        if relative.is_absolute() or '..' in relative.parts or not source.is_relative_to(backup) or source.is_symlink() or not source.is_file():
            raise VaultError('backup-path-invalid')
        if source.stat().st_size!=record['size'] or digest_file(source)!=record['sha256']:
            raise VaultError('backup-checksum-mismatch')
        # Older backups may include a lock captured during another process's
        # transaction. Authenticate its manifest record, but never revive the
        # process's O_EXCL marker or half-written atomic publication candidate.
        if _ephemeral_backup_path(relative):
            skipped_ephemeral+=1;continue
        files.append((relative,source))
    destination.mkdir(parents=True)
    for relative,source in files:
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    vault=Vault(destination)
    try:
        result=vault.doctor()
        if not result['ok']:raise VaultError('restored-vault-validation-failed')
        return {'dataRoot':str(destination),'files':len(files),'skippedEphemeralFiles':skipped_ephemeral,'doctor':result}
    finally:vault.close()
