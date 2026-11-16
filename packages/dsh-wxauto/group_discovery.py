"""Read group candidates from a validated detached native snapshot; no UI or writes.

Native keys are private routing data. Display names are aliases, never identities.
Positive local membership evidence is not a server-live membership guarantee.
"""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from vault.snapshot_import import _fingerprint, _validate, load_reader
from vault.store import VaultError, stable_id


def _json(reader, path, root):
    file = reader._secure_existing_path(path, label='discovery input', expected='file', allowed_root=root)
    if file.stat().st_size > 1024 * 1024:
        raise VaultError('group-discovery-json-too-large')
    value = json.loads(file.read_text(encoding='utf8'))
    if not isinstance(value, dict):
        raise VaultError('group-discovery-json-invalid')
    return value


def _require_columns(reader, db, table, required):
    if not reader.table_exists(db, table) or not set(required).issubset(reader.columns(db, table)):
        raise VaultError('group-discovery-schema-unsupported')


def _wire_fields(data):
    """Bounded wire decoder; field numbers are observations, not a .proto claim."""
    if not isinstance(data, bytes) or len(data) > 65536:
        raise ValueError('wire-budget')
    offset = 0
    def varint():
        nonlocal offset
        number = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                raise ValueError('wire-truncated')
            byte = data[offset]
            offset += 1
            number |= (byte & 127) << shift
            if byte < 128:
                return number
        raise ValueError('wire-varint')
    result = []
    while offset < len(data):
        key = varint()
        field, wire = key >> 3, key & 7
        if not field or len(result) >= 4096:
            raise ValueError('wire-field')
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else 8 if wire == 1 else 4
            if offset + length > len(data):
                raise ValueError('wire-truncated')
            value = data[offset:offset + length]
            offset += length
        else:
            raise ValueError('wire-type')
        result.append((field, wire, value))
    return result


def _derived_title(db, room_id, account, contacts):
    """Observed default-title candidate, never a native-ID-to-UI proof.

    Five real snapshots of one three-member room preserve field1 member order.
    Excluding self and joining contact.nick_name matches its observed title.
    Remark priority and UI truncation are unproven; never guess them.
    """
    try:
        blob = db.execute('SELECT ext_buffer FROM chat_room WHERE id=?', (room_id,)).fetchone()[0]
        ordered = []
        for field, wire, value in _wire_fields(blob):
            if field != 1:
                continue
            if wire != 2:
                return None
            identities = [value for field, wire, value in _wire_fields(value) if field == 1 and wire == 2]
            if len(identities) != 1:
                return None
            ordered.append(identities[0].decode('utf8'))
        mapped = [row[0] for row in db.execute('SELECT n.username FROM chatroom_member m JOIN name2id n ON n.rowid=m.member_id WHERE m.room_id=?', (room_id,))]
        total = db.execute('SELECT count(*) FROM chatroom_member WHERE room_id=?', (room_id,)).fetchone()[0]
        if (not ordered or len(ordered) != len(set(ordered)) or ordered.count(account) != 1
                or len(mapped) != total or set(ordered) != set(mapped)):
            return None
        names = []
        for native in ordered:
            if native == account:
                continue
            rows = contacts.get(native, [])
            if len(rows) != 1:
                return None
            row = rows[0]
            nick = row['nick_name']
            if (not isinstance(nick, str) or not nick.strip() or len(nick) > 128
                    or (row['remark'] and row['remark'] != nick)):
                return None
            names.append(nick)
        title = '、'.join(names)
        return title if title and len(title) <= 256 else None
    except (ValueError, UnicodeError, TypeError, IndexError):
        return None


def discover_groups(latest_path, account_id, *, expected_account_username=None,
                    max_age_seconds=900, now=None):
    """Return candidates, not permission to send. Caller supplies trusted account scope.

    `eligible` requires positive membership, a current visible local session, and
    a unique explicit or derived candidate title. It still needs the UI route's exact group
    confirmation. Missing/contradictory evidence never retires historical chats.
    """
    if not isinstance(account_id, str) or not account_id.strip():
        raise VaultError('group-discovery-account-required')
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or not 0 < max_age_seconds <= 86400:
        raise VaultError('group-discovery-age-invalid')
    reader = load_reader()
    latest_path = Path(latest_path)
    base = reader.secure_snapshot_root(latest_path.parent)
    latest = _json(reader, latest_path, base)
    if (latest.get('status') != 'complete' or latest.get('validation') != 'passed'
            or latest.get('read_only_source') is not True or latest.get('keys_persisted') is not False):
        raise VaultError('group-discovery-latest-unverified')
    snapshot = Path(latest.get('snapshot', ''))
    # UUID validity is independently checked by the pinned reader below.
    if snapshot != base / 'snapshots' / str(latest.get('snapshot_id', '')):
        raise VaultError('group-discovery-snapshot-location-invalid')
    root, manifest, _, fingerprints = _validate(reader, snapshot)
    raw = _json(reader, root / 'snapshot-manifest.json', root)
    if manifest['snapshot_id'] != latest.get('snapshot_id') or not isinstance(raw.get('databases'), list):
        raise VaultError('group-discovery-capture-manifest-required')
    account = manifest['account_username']
    if not account or (expected_account_username is not None and account != expected_account_username):
        raise VaultError('group-discovery-account-mismatch')
    try:
        captured = datetime.fromisoformat(raw['captured_at'].replace('Z', '+00:00'))
        if captured.tzinfo is None:
            raise ValueError()
        age = ((now or datetime.now(timezone.utc)) - captured).total_seconds()
    except (KeyError, TypeError, ValueError) as exc:
        raise VaultError('group-discovery-capture-time-invalid') from exc
    if age < -60 or age > max_age_seconds:
        raise VaultError('group-discovery-snapshot-stale')

    groups = []
    with reader.connect_read_only(root / 'contact/contact.db') as db:
        for table, fields in (
            ('contact', ('username', 'remark', 'nick_name', 'alias', 'delete_flag', 'is_in_chat_room')),
            ('chat_room', ('id', 'username')),
            ('name2id', ('username',)),
            ('chatroom_member', ('room_id', 'member_id')),
        ):
            _require_columns(reader, db, table, fields)
        rows = db.execute('SELECT username,remark,nick_name,alias,delete_flag,is_in_chat_room FROM contact').fetchall()
        if len(rows) > reader.MAX_CONTACTS:
            raise VaultError('group-discovery-contact-limit')
        # Check collisions against all local contacts, including a private chat
        # with the same displayed title. Never choose a group by a generic label.
        titles = Counter()
        for row in rows:
            title = next((row[k] for k in ('remark', 'nick_name', 'alias') if isinstance(row[k], str) and row[k].strip()), None)
            if title:
                titles[title] += 1
        native_counts = Counter(row['username'] for row in rows)
        contacts = {}
        for row in rows:
            contacts.setdefault(row['username'], []).append(row)
        has_room_buffer = 'ext_buffer' in reader.columns(db, 'chat_room')
        for row in rows:
            native = row['username']
            if not isinstance(native, str) or not native.endswith('@chatroom'):
                continue
            rooms = db.execute('SELECT id FROM chat_room WHERE username=?', (native,)).fetchall()
            self_rows = 0
            members = 0
            if len(rooms) == 1:
                room_id = rooms[0]['id']
                self_rows = db.execute('SELECT count(*) FROM chatroom_member m JOIN name2id n ON n.rowid=m.member_id WHERE m.room_id=? AND n.username=?', (room_id, account)).fetchone()[0]
                members = db.execute('SELECT count(DISTINCT member_id) FROM chatroom_member WHERE room_id=?', (room_id,)).fetchone()[0]
            title_field = next((k for k in ('remark', 'nick_name', 'alias') if isinstance(row[k], str) and row[k].strip()), None)
            title = row[title_field] if title_field else None
            if title is None and len(rooms) == 1 and has_room_buffer:
                title = _derived_title(db, rooms[0]['id'], account, contacts)
                if title:
                    title_field = 'derived-member-title'
                    titles[title] += 1
            reasons = []
            if native_counts[native] != 1:
                reasons.append('duplicate-native-contact')
            if row['delete_flag'] != 0 or row['is_in_chat_room'] != 1 or len(rooms) != 1 or self_rows != 1:
                reasons.append('current-membership-unconfirmed')
            if title is None:
                reasons.append('explicit-display-name-missing')
            chat_key = 'wechat:' + native  # Same identity as snapshot_import.
            groups.append({'nativeUsername': native, 'chatKey': chat_key,
                           'chatId': stable_id('chat-v1', account_id, chat_key),
                           'displayName': title, 'displayNameSource': title_field,
                           'displayCollisionCount': 0,
                           'requiresUiConfirmation': True,
                           'uiConfirmation': {'exactName': True, 'chatType': 'group'},
                           'membership': {'deleteFlag': row['delete_flag'], 'isInChatRoom': row['is_in_chat_room'],
                                          'roomRows': len(rooms), 'selfMembershipRows': self_rows, 'memberCount': members},
                           'unresolvedReasons': reasons})
        # All derived names must be known before checking collisions, including
        # explicit group/private names and other derived candidates.
        for group in groups:
            title = group['displayName']
            group['displayCollisionCount'] = titles[title] if title else 0
            if title and titles[title] != 1:
                group['unresolvedReasons'].append('display-name-ambiguous')
    with reader.connect_read_only(root / 'session/session.db') as db:
        _require_columns(reader, db, 'SessionTable', ('username', 'is_hidden', 'status', 'last_timestamp'))
        _require_columns(reader, db, 'SessionDeleteTable', ('username', 'delete_time'))
        for group in groups:
            sessions = db.execute('SELECT is_hidden,status,last_timestamp FROM SessionTable WHERE username=?', (group['nativeUsername'],)).fetchall()
            deleted = db.execute('SELECT count(*) FROM SessionDeleteTable WHERE username=?', (group['nativeUsername'],)).fetchone()[0]
            session = dict(sessions[0]) if len(sessions) == 1 else None
            group['session'] = {'rows': len(sessions), 'deleteRecordCount': deleted, 'local': session}
            # status 0 is observed on active sessions, not a proven server membership enum.
            if not session or session['is_hidden'] != 0 or session['status'] != 0 or deleted:
                group['unresolvedReasons'].append('active-local-session-unconfirmed')
            group['eligible'] = not group['unresolvedReasons']
    if _fingerprint([Path(p) for p in fingerprints]) != fingerprints:
        raise VaultError('group-discovery-snapshot-changed')
    if _json(reader, latest_path, base) != latest:
        raise VaultError('group-discovery-latest-changed')
    return {'schemaVersion': 1, 'source': 'validated-native-snapshot', 'accountId': account_id,
            'snapshotId': manifest['snapshot_id'], 'capturedAt': captured.isoformat(),
            'ageSeconds': max(0, age), 'membershipScope': 'local-snapshot-not-server-live',
            'groups': sorted(groups, key=lambda group: group['chatId'])}


if __name__ == '__main__':
    import argparse
    import sys
    parser=argparse.ArgumentParser(description='Read already-validated local group identities; no UI or capture.')
    parser.add_argument('--root',required=True)
    parser.add_argument('--account-id',required=True)
    parser.add_argument('--unread-chat-key')
    options=parser.parse_args()
    try:
        data_root=Path(options.root)
        config=json.loads((data_root/'config/wxauto.local.json').read_text('utf-8-sig'))
        latest=data_root/'snapshots/windows-live/latest.json'
        receipt=json.loads(latest.read_text('utf-8-sig'))
        capture=config.get('capture',{})
        if (config.get('accountId')!=options.account_id or not capture.get('accountUsername')
                or not capture.get('sourceId') or receipt.get('source_id')!=capture['sourceId']
                or receipt.get('import',{}).get('account_id')!=options.account_id):
            raise VaultError('group-discovery-configured-source-mismatch')
        if options.unread_chat_key:
            from group_unread import read_latest_group_unread
            value=read_latest_group_unread(data_root,native_chat_key=options.unread_chat_key,account_id=options.account_id,
                expected_account_username=capture['accountUsername'],expected_source_id=capture['sourceId'])
        else:
            value=discover_groups(latest,options.account_id,expected_account_username=capture['accountUsername'])
        print(json.dumps({'ok':True,'value':value},ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'ok':False,'code':str(error) if isinstance(error,VaultError) else type(error).__name__}))
        sys.exit(1)
