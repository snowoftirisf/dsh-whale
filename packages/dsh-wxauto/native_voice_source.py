"""Exact incoming SILK bytes from authenticated detached WeChat snapshots.

Never captures/decrypts, reads live DBs, opens a Vault, operates UI or downloads.
No matching by text, approximate time, or local ID alone. Outgoing svr_id=0
VoiceInfo entries are intentionally unsupported even if other fields coincide.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from vault.snapshot_import import load_reader, _validate, _fingerprint, _server_id, _decode


class NativeVoiceSourceError(RuntimeError):
    pass


def _require(test, code):
    if not test:
        raise NativeVoiceSourceError(code)


def _positive(value):
    return type(value) is int and value > 0


def read_native_voice_source(snapshot_path, *, native_chat_key, server_id,
                             expected_account_username, expected_source_id,
                             expected_snapshot_id=None, max_bytes=2 * 1024 * 1024):
    """Return {'blob': bytes, 'metadata': ...}; do not serialize/log raw result.

    Account/source/chat/server identifiers must come from the trusted route and
    native message envelope. Audio and message must match exact chat, positive
    server ID, create_time, and local_id, and identify an incoming sender.
    """
    try:
        return _read(snapshot_path, native_chat_key=native_chat_key, server_id=server_id,
                     expected_account_username=expected_account_username, expected_source_id=expected_source_id,
                     expected_snapshot_id=expected_snapshot_id, max_bytes=max_bytes)
    except NativeVoiceSourceError:
        raise
    except Exception:
        # SDK/XML/SQLite exceptions may contain paths or content. Export no text.
        raise NativeVoiceSourceError('native-voice-snapshot-invalid') from None


def _read(snapshot_path, *, native_chat_key, server_id, expected_account_username,
          expected_source_id, expected_snapshot_id, max_bytes):
    _require(isinstance(native_chat_key, str) and native_chat_key.startswith('wechat:')
             and 1 <= len(native_chat_key[7:]) <= 256
             and not any(c.isspace() or c in '/\\\x00' for c in native_chat_key[7:]), 'native-voice-chat-invalid')
    ident = _server_id(server_id)
    _require(ident is not None, 'native-voice-server-id-invalid')
    _require(isinstance(expected_account_username, str) and bool(expected_account_username), 'native-voice-account-required')
    _require(isinstance(expected_source_id, str) and bool(expected_source_id), 'native-voice-source-required')
    _require(type(max_bytes) is int and 1 <= max_bytes <= 20 * 1024 * 1024, 'native-voice-byte-limit-invalid')
    reader = load_reader()
    root, manifest, shards, fingerprints = _validate(reader, snapshot_path)
    _require(len(shards) <= 32, 'native-voice-shard-budget')
    raw = json.loads((root / 'snapshot-manifest.json').read_text(encoding='utf8'))
    _require(manifest['account_username'] == expected_account_username, 'native-voice-account-mismatch')
    _require(raw.get('source_id') == expected_source_id, 'native-voice-source-mismatch')
    _require(expected_snapshot_id is None or manifest['snapshot_id'] == expected_snapshot_id, 'native-voice-snapshot-mismatch')
    audio = raw.get('audio_capture', {})
    names = audio.get('databases') if isinstance(audio, dict) else None
    _require(audio.get('enabled') is True and audio.get('scope') == 'chat' and audio.get('schemaVersion') == 1
             and isinstance(names, list) and 1 <= len(names) <= 64 and len(set(names)) == len(names), 'native-voice-audio-capture-required')
    records = raw.get('databases')
    _require(isinstance(records, list), 'native-voice-authentication-required')
    files = []
    for name in names:
        _require(isinstance(name, str) and re.fullmatch(r'message/media_[0-9]+\.db', name), 'native-voice-media-path-invalid')
        file = reader._secure_existing_path(root / name, label='voice media DB', expected='file', allowed_root=root)
        _require(file.stat().st_nlink == 1 and file.stat().st_size <= 512 * 1024 * 1024, 'native-voice-media-file-invalid')
        _require(not any(os.path.lexists(str(file) + suffix) for suffix in ('-wal', '-shm', '-journal')), 'native-voice-media-sidecar-denied')
        entries = [entry for entry in records if isinstance(entry, dict) and entry.get('database') == name]
        _require(len(entries) == 1 and entries[0].get('authenticated') is True, 'native-voice-authentication-required')
        current = _fingerprint([file])
        expected = entries[0].get('plaintext_sha256')
        _require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected)
                 and current[str(file)][2] == expected, 'native-voice-media-checksum-mismatch')
        fingerprints.update(current)
        with reader.connect_read_only(file) as db:
            _require([row[0] for row in db.execute('PRAGMA integrity_check')] == ['ok'], 'native-voice-media-integrity-failed')
        files.append(file)
    talker = native_chat_key[7:]
    contacts = reader.contact_records(root)
    _require(talker in contacts, 'native-voice-chat-unresolved')
    table = reader.message_table(talker)
    messages = []
    for file in shards:
        with reader.connect_read_only(file) as db:
            if not reader.table_exists(db, table):
                continue
            columns = reader.columns(db, table)
            required = {'server_id', 'local_id', 'create_time', 'local_type', 'real_sender_id', 'message_content', 'WCDB_CT_message_content'}
            _require(required <= columns, 'native-voice-message-schema-unsupported')
            names_map = reader.sender_map(db)
            for row in db.execute(f'SELECT server_id,local_id,create_time,local_type,real_sender_id,message_content,WCDB_CT_message_content FROM [{table}] WHERE server_id=? LIMIT 2', (ident,)):
                messages.append((dict(row), names_map))
    _require(len(messages) == 1, 'native-voice-message-missing-or-ambiguous')
    message, senders = messages[0]
    _require(_server_id(message['server_id']) == ident and type(message['local_type']) is int
             and message['local_type'] & 0xFFFFFFFF == 34, 'native-voice-message-type-mismatch')
    _require(_positive(message['local_id']) and _positive(message['create_time']), 'native-voice-message-envelope-invalid')
    sender = senders.get(message['real_sender_id'])
    _require(bool(sender) and sender != expected_account_username, 'native-voice-incoming-required')
    text = _decode(reader, message['message_content'], message['WCDB_CT_message_content'], {'rows': 0, 'decoded_bytes': 0})
    _require(len(text) <= 65536 and '<msg' in text, 'native-voice-envelope-invalid')
    node = ET.fromstring(text[text.index('<msg'):]).find('voicemsg')
    _require(node is not None, 'native-voice-envelope-invalid')
    _require(node.get('voiceformat') == '4', 'native-voice-codec-unsupported')
    duration = node.get('voicelength', '')
    _require(re.fullmatch('[0-9]{1,6}', duration) and 0 < int(duration) <= 120000, 'native-voice-duration-invalid')
    matches = []
    for file in files:
        with reader.connect_read_only(file) as db:
            _require(reader.table_exists(db, 'VoiceInfo') and {'chat_name_id', 'create_time', 'local_id', 'svr_id', 'voice_data'} <= reader.columns(db, 'VoiceInfo')
                     and reader.table_exists(db, 'Name2Id') and 'user_name' in reader.columns(db, 'Name2Id'), 'native-voice-media-schema-unsupported')
            rows = db.execute('SELECT rowid,chat_name_id,create_time,local_id,svr_id,length(voice_data) size FROM VoiceInfo WHERE svr_id=? LIMIT 2', (ident,)).fetchall()
            for row in rows:
                chat = db.execute('SELECT user_name FROM Name2Id WHERE rowid=?', (row['chat_name_id'],)).fetchall()
                _require(len(chat) == 1 and chat[0][0] == talker, 'native-voice-media-chat-mismatch')
                _require(row['create_time'] == message['create_time'] and row['local_id'] == message['local_id'], 'native-voice-media-envelope-mismatch')
                _require(_positive(row['size']) and row['size'] <= max_bytes, 'native-voice-blob-size-invalid')
                blob = db.execute('SELECT voice_data FROM VoiceInfo WHERE rowid=?', (row['rowid'],)).fetchone()[0]
                _require(isinstance(blob, bytes) and len(blob) == row['size'], 'native-voice-blob-invalid')
                _require(blob.startswith((b'\x02#!SILK_V3', b'#!SILK_V3')), 'native-voice-silk-header-invalid')
                declared = node.get('length', '')
                _require(re.fullmatch('[0-9]{1,9}', declared) and int(declared) == len(blob), 'native-voice-blob-length-mismatch')
                matches.append((blob, file.relative_to(root).as_posix()))
    _require(len(matches) == 1, 'native-voice-blob-missing-or-ambiguous')
    _require(_fingerprint([Path(path) for path in fingerprints]) == fingerprints, 'native-voice-snapshot-changed')
    blob, relative = matches[0]
    return {'blob': blob, 'metadata': {'schemaVersion': 1, 'codec': 'silk-v3', 'voiceFormat': 4,
        'durationMilliseconds': int(duration), 'size': len(blob), 'sha256': hashlib.sha256(blob).hexdigest(),
        'snapshotId': manifest['snapshot_id'], 'sourceRelativeDatabase': relative,
        'nativeChatKey': native_chat_key, 'serverId': ident, 'senderId': sender,
        'localId': message['local_id'], 'timestampSeconds': message['create_time'],
        'direction': 'incoming', 'identityMatch': 'exact-server-chat-local-time', 'readOnly': True}}
