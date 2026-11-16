"""Authenticated, read-only owner-private text snapshot scan. No live capture.

The stable scope digest describes the trusted owner route, while private HMAC
scan cursors additionally bind the immutable snapshot, files and physical scan.
Only the full collector is suitable for inbox initialization/admission.
"""
from contextlib import contextmanager
from pathlib import Path
import base64
import hashlib
import hmac
import json
import re
import secrets

from vault.snapshot_import import load_reader, _validate, _fingerprint, _rows, _decode
from quote_render import QUOTE_BASE_TYPE, STICKER_BASE_TYPE, IMAGE_BASE_TYPE, admit_quote, admit_sticker, admit_image
from native_group_text_catalog import _sticker_source


class NativeOwnerTextCatalogError(RuntimeError):
    pass


def _require(condition, code):
    if not condition:
        raise NativeOwnerTextCatalogError('native-owner-text-catalog-' + code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')


def _positive(value):
    return type(value) is int and 0 < value <= 9007199254740991


class _PageReader:
    def __init__(self, reader, table, after, limit, own, peer):
        self.reader, self.table, self.after, self.limit = reader, table, after, limit
        self.own, self.peer = own, peer
        self.MESSAGE_TABLE_RE = re.compile(re.escape(table))

    def __getattr__(self, name):
        return getattr(self.reader, name)

    @contextmanager
    def connect_read_only(self, path):
        with self.reader.connect_read_only(path) as con:
            reader, table = self.reader, self.table
            mapping = reader.table_mapping(con, table)
            rowid = reader._source_rowid_name(con, table)
            names = reader.sender_map(con)
            owner = self

            class Connection:
                def execute(self, sql, args=()):
                    if f' FROM [{table}] ORDER BY ' not in sql:
                        return con.execute(sql, args)
                    head, order = sql.rsplit(' ORDER BY ', 1)
                    sql = head + f" WHERE ([{mapping['local_type']}] & 4294967295) IN (1,{QUOTE_BASE_TYPE},{STICKER_BASE_TYPE},{IMAGE_BASE_TYPE}) AND [{rowid}]>? ORDER BY " + order + ' LIMIT ?'
                    rows = con.execute(sql, (owner.after, owner.limit + 1)).fetchall()
                    # Verify actual Name2Id before _rows can use payload fallback.
                    for row in rows:
                        _require(_positive(row['_source_rowid']) and _positive(row['local_id']) and _positive(row['create_time']), 'envelope-invalid')
                        sender = row['sender_id']
                        _require(_positive(sender) and names.get(sender) in (owner.own, owner.peer), 'sender-unresolved')
                    return rows

            yield Connection()


def page_native_owner_texts(snapshot_path, *, expected_account_username,
                            expected_source_id, account_id, owner_native_chat_key,
                            expected_peer_username, scan_key, cursor=None,
                            expected_snapshot_id=None, page_size=500):
    """A physical type-1 row page; outgoing rows advance but are not inputs.

    scan_key is a caller-private random 32-byte hex capability, not a platform
    credential. It must be held for this one scan and never exposed to a model.
    """
    try:
        _require(isinstance(account_id, str) and 0 < len(account_id) <= 256, 'account-required')
        for identity in (expected_account_username, expected_peer_username):
            _require(isinstance(identity, str) and re.fullmatch(r'[^\s/\\\x00:]{1,256}', identity) and '@chatroom' not in identity, 'identity-invalid')
        _require(expected_peer_username != expected_account_username and owner_native_chat_key == 'wechat:' + expected_peer_username, 'owner-scope-invalid')
        _require(isinstance(expected_source_id, str) and 0 < len(expected_source_id) <= 256, 'source-required')
        _require(type(page_size) is int and 1 <= page_size <= 1000, 'budget-invalid')
        _require(isinstance(scan_key, str) and re.fullmatch('[0-9a-f]{64}', scan_key), 'scan-key-invalid')
        reader = load_reader()
        root, manifest, shards, fingerprints = _validate(reader, snapshot_path)
        _require(len(shards) <= 32, 'shard-budget')
        manifest_bytes = (root / 'snapshot-manifest.json').read_bytes()
        raw = json.loads(manifest_bytes)
        _require(isinstance(raw.get('databases'), list) and bool(raw['databases']), 'authentication-required')
        _require(manifest['account_username'] == expected_account_username, 'account-mismatch')
        _require(raw.get('source_id') == expected_source_id, 'source-mismatch')
        _require(expected_snapshot_id is None or expected_snapshot_id == manifest['snapshot_id'], 'snapshot-mismatch')
        contacts = reader.contact_records(root)
        _require(expected_peer_username in contacts, 'peer-unresolved')
        display = contacts[expected_peer_username].get('display_name')
        _require(isinstance(display, str) and 0 < len(display) <= 1024 and display != '未命名会话', 'peer-unresolved')
        table = reader.message_table(expected_peer_username)
        descriptors = []
        for shard in sorted(shards):
            with reader.connect_read_only(shard) as con:
                if table not in {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                    continue
                mapping = reader.table_mapping(con, table)
                rowid = reader._source_rowid_name(con, table)
                _require(reader._compatible_message_mapping(mapping) and bool(rowid), 'message-schema-unsupported')
                first = con.execute(f'SELECT [{rowid}] FROM [{table}] ORDER BY [{rowid}] LIMIT 1').fetchone()
                _require(first is None or _positive(first[0]), 'source-rowid-unsupported')
                descriptors.append([shard.relative_to(root).as_posix(), table, rowid, mapping])
        scope = hashlib.sha256(_json([account_id, owner_native_chat_key, expected_peer_username])).hexdigest()
        scan_scope = hashlib.sha256(_json([1, scope, manifest['snapshot_id'],
            hashlib.sha256(manifest_bytes).hexdigest(), fingerprints,
            expected_account_username, expected_source_id, descriptors, page_size])).hexdigest()
        index, after = 0, 0

        def token(i, row):
            payload = _json({'version': 1, 'scanScopeDigest': scan_scope, 'descriptorIndex': i, 'rowid': row})
            return base64.urlsafe_b64encode(payload).decode().rstrip('=') + '.' + hmac.new(bytes.fromhex(scan_key), payload, hashlib.sha256).hexdigest()

        if cursor is not None:
            _require(isinstance(cursor, str) and len(cursor) <= 4096, 'cursor-invalid')
            try:
                payload, signature = cursor.split('.')
                payload = base64.urlsafe_b64decode(payload + '=' * ((-len(payload)) % 4))
                _require(hmac.compare_digest(signature, hmac.new(bytes.fromhex(scan_key), payload, hashlib.sha256).hexdigest()), 'cursor-invalid')
                value = json.loads(payload)
                _require(value.get('version') == 1 and value.get('scanScopeDigest') == scan_scope, 'cursor-scope-mismatch')
                index, after = value['descriptorIndex'], value['rowid']
                _require(type(index) is int and 0 <= index < len(descriptors) and type(after) is int and 0 <= after <= 9007199254740991, 'cursor-invalid')
            except NativeOwnerTextCatalogError:
                raise
            except Exception:
                raise NativeOwnerTextCatalogError('native-owner-text-catalog-cursor-invalid') from None
        scanned = []
        while index < len(descriptors) and len(scanned) < page_size:
            relative, _, _, _ = descriptors[index]
            remaining = page_size - len(scanned)
            batch = list(_rows(_PageReader(reader, table, after, remaining, expected_account_username, expected_peer_username), root, manifest, [root / relative], contacts))
            take = batch[:remaining]
            scanned.extend(take)
            if len(batch) > remaining:
                after = take[-1]['metadata']['local_rowid']
                break
            index += 1
            after = 0
        def quote_text(row):
            # _rows only exposes the generic "[链接/文件]" label; re-read the
            # envelope of this exact physical row to render a quote reply.
            meta = row['metadata']
            descriptor = next((d for d in descriptors if d[0] == meta['shard']), None)
            if descriptor is None or meta.get('local_type') is None:
                return None
            _, _, rowid, mapping = descriptor
            column = lambda key: f'[{mapping[key]}]' if mapping.get(key) else 'NULL'
            budget = {'rows': 0, 'decoded_bytes': 0}
            with reader.connect_read_only(root / meta['shard']) as con:
                if row['type'] == 'image':
                    if 'packed_info_data' not in reader.columns(con, table):
                        return admit_image(meta['local_type'], None, row['timestamp'], hashlib.md5(expected_peer_username.encode()).hexdigest())
                    packed = con.execute(f"SELECT [packed_info_data] FROM [{table}] WHERE [{rowid}]=?", (meta['local_rowid'],)).fetchone()
                    return admit_image(meta['local_type'], packed[0] if packed else None, row['timestamp'],
                                       hashlib.md5(expected_peer_username.encode()).hexdigest())
                found = con.execute(f"SELECT {column('content')}, {column('compressed')}, {column('compression_flag')} FROM [{table}] WHERE [{rowid}]=?",
                    (meta['local_rowid'],)).fetchone()
                if found is None:
                    return None
                envelope = _decode(reader, found[0], found[2], budget) or _decode(reader, found[1], found[2], budget)
                if row['type'] == 'emotion':
                    return admit_sticker(meta['local_type'], envelope, row['timestamp'])
                return admit_quote(meta['local_type'], envelope, row['timestamp'],
                                   _sticker_source(reader, con, table, mapping, budget))

        messages, observed, seen = [], [], set()
        for row in scanned:
            _require(row['chat_key'] == owner_native_chat_key and row['type'] in ('text', 'link_or_file', 'emotion', 'image'), 'scope-invalid')
            if row['type'] in ('link_or_file', 'emotion', 'image'):
                # Only post-fix quote replies and stickers join the text stream.
                quote = quote_text(row)
                if quote is None:
                    continue
                row = {**row, 'text': quote}
            native = row.get('native_id')
            if native is not None:
                _require(isinstance(native, str) and re.fullmatch('wechat-server:[1-9][0-9]*', native), 'native-id-invalid')
                _require(native not in seen, 'native-id-ambiguous')
                seen.add(native)
                observed.append(native)
            sender = row.get('sender_id')
            _require(sender in (expected_account_username, expected_peer_username), 'sender-unresolved')
            if sender == expected_account_username:
                _require(row['direction'] == 'outgoing', 'direction-invalid')
                continue
            _require(row['direction'] == 'incoming' and native is not None, 'incoming-identity-invalid')
            text, stamp, local = row['text'], row['timestamp'], row['metadata'].get('local_id')
            _require(_positive(local) and _positive(stamp) and isinstance(text, str) and bool(text.strip()) and len(text.encode('utf8')) <= 65536, 'envelope-invalid')
            messages.append({'nativeId': native, 'serverId': native.removeprefix('wechat-server:'),
                'nativeChatKey': owner_native_chat_key, 'senderId': sender, 'senderDisplayName': display,
                'rawText': text, 'timestampSeconds': stamp, 'localId': local,
                'snapshotSourceId': row['source_id'], 'direction': 'incoming'})
        _require(_fingerprint([Path(path) for path in fingerprints]) == fingerprints, 'snapshot-changed')
        return {'source': 'authenticated-native-owner-text-catalog-page', 'accountId': account_id,
            'snapshotId': manifest['snapshot_id'], 'scopeDigest': scope, 'scanScopeDigest': scan_scope,
            'nativeChatKey': owner_native_chat_key, 'messages': messages, 'observedNativeIds': observed,
            'scanned': len(scanned), 'position': {'descriptorIndex': index, 'rowid': after},
            'complete': index >= len(descriptors), 'nextCursor': token(index, after) if index < len(descriptors) else None}
    except NativeOwnerTextCatalogError:
        raise
    except Exception:
        raise NativeOwnerTextCatalogError('native-owner-text-catalog-snapshot-invalid') from None


def list_native_owner_texts(snapshot_path, *, expected_account_username,
                            expected_source_id, account_id, owner_native_chat_key,
                            expected_peer_username, expected_snapshot_id=None,
                            page_size=500, max_pages=2048, max_identities=100000,
                            max_bytes=64 * 1024 * 1024):
    """Consume every authenticated page or fail without a partial catalog."""
    _require(type(max_pages) is int and 1 <= max_pages <= 2048 and
             type(max_identities) is int and 1 <= max_identities <= 100000 and
             type(max_bytes) is int and 1 <= max_bytes <= 64 * 1024 * 1024, 'total-budget-invalid')
    scan_key, cursor, messages, seen, cursors, total_bytes = secrets.token_hex(32), None, [], set(), set(), 0
    fixed_snapshot, fixed_scan_scope, fixed_scope = expected_snapshot_id, None, None
    for _ in range(max_pages):
        page = page_native_owner_texts(snapshot_path, expected_account_username=expected_account_username,
            expected_source_id=expected_source_id, account_id=account_id, owner_native_chat_key=owner_native_chat_key,
            expected_peer_username=expected_peer_username, scan_key=scan_key, cursor=cursor,
            expected_snapshot_id=fixed_snapshot, page_size=page_size)
        _require(page['source'] == 'authenticated-native-owner-text-catalog-page' and
                 page['accountId'] == account_id and page['nativeChatKey'] == owner_native_chat_key, 'page-scope-invalid')
        if fixed_scan_scope is None:
            fixed_snapshot, fixed_scan_scope, fixed_scope = page['snapshotId'], page['scanScopeDigest'], page['scopeDigest']
        _require(page['snapshotId'] == fixed_snapshot and page['scanScopeDigest'] == fixed_scan_scope and page['scopeDigest'] == fixed_scope, 'page-scope-invalid')
        total_bytes += len(_json(page))
        _require(total_bytes <= max_bytes, 'total-byte-limit')
        for native in page['observedNativeIds']:
            _require(native not in seen, 'native-id-ambiguous')
            seen.add(native)
        _require(len(seen) <= max_identities, 'identity-limit')
        messages.extend(page['messages'])
        if page['complete']:
            _require(page['nextCursor'] is None, 'cursor-invalid')
            return {'source': 'authenticated-native-owner-text-catalog', 'accountId': account_id,
                'snapshotId': fixed_snapshot, 'scopeDigest': fixed_scope, 'nativeChatKey': owner_native_chat_key,
                'complete': True, 'messages': sorted(messages, key=lambda row: (row['timestampSeconds'], row['localId'], row['nativeId']))}
        next_cursor = page['nextCursor']
        _require(isinstance(next_cursor, str) and next_cursor and next_cursor not in cursors, 'cursor-not-advancing')
        cursors.add(next_cursor)
        cursor = next_cursor
    raise NativeOwnerTextCatalogError('native-owner-text-catalog-page-limit')
