from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import native_owner_text_claim as module
from native_owner_text_claim import inspect_submitted_owner_text, NativeOwnerTextClaimError
from native_owner_text_catalog import list_native_owner_texts
from vault.snapshot_import import load_reader
from vault.test_snapshot_import import make_snapshot, TABLE, OWNER, SENDER


HERE = Path(__file__).parent


class OwnerTextClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        snapshot = make_snapshot(self.root / 'prepared', rows=[
            (1, 9001, 1, 1772323200, 2, 'fixture only 📩 中文', 0),
            (2, 9002, 1, 1772323201, 1, 'outgoing fixture', 0)], hash_manifest=True)
        self.table = load_reader().message_table(SENDER)
        with closing(sqlite3.connect(snapshot / 'message/message_0.db')) as db:
            db.execute(f'ALTER TABLE [{TABLE}] RENAME TO [{self.table}]'); db.commit()
        from vault.store import digest_file
        manifest_file = snapshot / 'snapshot-manifest.json'
        manifest = json.loads(manifest_file.read_text('utf8')); manifest['source_id'] = 'fixture'
        manifest['databases'] = [{'database': p.relative_to(snapshot).as_posix(), 'authenticated': True,
                                  'plaintext_sha256': digest_file(p)} for p in snapshot.rglob('*.db')]
        manifest_file.write_text(json.dumps(manifest), encoding='utf8')
        self.snapshot = self.root / 'snapshots/windows-live/snapshots' / manifest['snapshot_id']
        self.snapshot.parent.mkdir(parents=True); shutil.move(snapshot, self.snapshot)
        self.config = {'dataRoot': str(self.root), 'accountId': 'fixture-account',
                       'nativeVoiceSources': {'ownerNativeChatKey': 'wechat:' + SENDER},
                       'capture': {'accountUsername': OWNER, 'sourceId': 'fixture'}}
        catalog = list_native_owner_texts(self.snapshot, expected_account_username=OWNER, expected_source_id='fixture',
            account_id='fixture-account', owner_native_chat_key='wechat:' + SENDER, expected_peer_username=SENDER)
        script = """
import fs from 'node:fs';
import {createNativeOwnerTextInbox} from './native-owner-text-inbox.mjs';
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const inbox=createNativeOwnerTextInbox({...input.config,now:()=>1772323202000});
const init=inbox.initialize({...input.catalog,messages:[]},{cutoverAtMs:1772323199000});
inbox.enqueue(input.catalog);const claim=inbox.pending()[0];inbox.submit([claim.claimId]);
console.log(JSON.stringify({epoch:init.authorityEpoch,id:claim.claimId}));
"""
        node_config = {'dataRoot': str(self.root), 'accountId': 'fixture-account',
                       'ownerNativeChatKey': 'wechat:' + SENDER, 'expectedPeerUsername': SENDER}
        result = subprocess.run(['node', '--input-type=module', '-e', script], input=json.dumps({'config': node_config, 'catalog': catalog}),
                                text=True, encoding='utf8', capture_output=True, cwd=HERE, check=True)
        proof = json.loads(result.stdout)
        self.epoch, self.claim_id = proof['epoch'], proof['id']
        self.file = self.root / 'config/native-owner-text-inbox.json'
        self.active = lambda: {'active': True, 'authorityEpoch': self.epoch,
                               'ownerNativeChatKey': 'wechat:' + SENDER, 'accountId': 'fixture-account'}

    def inspect(self, **kwargs):
        return inspect_submitted_owner_text(self.config, kwargs.pop('claim_id', self.claim_id),
            kwargs.pop('authority_epoch', self.epoch), active_authority_provider=kwargs.pop('active_authority_provider', self.active),
            clock=kwargs.pop('clock', lambda: 1772323203), **kwargs)

    def mutate(self, edit):
        value = json.loads(self.file.read_text('utf8')); value.pop('digest'); edit(value)
        value['digest'] = module._hash(value)
        self.file.write_bytes(module._compact(value))

    def test_real_node_durable_submitted_state_and_exact_snapshot_verified(self):
        evidence = self.inspect()
        self.assertEqual(evidence.claim_id, self.claim_id)
        self.assertEqual(evidence.native_id, 'wechat-server:9001')
        self.assertEqual(evidence.raw_text, 'fixture only 📩 中文')
        self.assertNotIn(evidence.raw_text, repr(evidence))
        self.assertEqual(evidence.inbox_sha256, hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertEqual(self.inspect(), evidence)  # Evidence inspection is not one-use authorization.

    def test_baseline_proof_optional_pair_compatibility_and_malformed_rejection(self):
        self.mutate(lambda value: value.update(baselineSnapshotId=value['claims'][0]['snapshotId'], baselineCatalogDigest='a' * 64))
        self.assertEqual(self.inspect().claim_id, self.claim_id)
        self.mutate(lambda value: value.pop('baselineCatalogDigest'))
        with self.assertRaisesRegex(NativeOwnerTextClaimError, 'state-invalid'):
            self.inspect()
        self.mutate(lambda value: value.update(baselineCatalogDigest='not-a-hash'))
        with self.assertRaisesRegex(NativeOwnerTextClaimError, 'state-invalid'):
            self.inspect()
        self.mutate(lambda value: (value.pop('baselineSnapshotId'), value.pop('baselineCatalogDigest')))
        self.assertEqual(self.inspect().claim_id, self.claim_id)

    def test_default_denies_and_active_authority_epoch_owner_account_are_exact(self):
        for provider in [None, lambda: {}, lambda: {**self.active(), 'active': False},
                         lambda: {**self.active(), 'authorityEpoch': 'a' * 32},
                         lambda: {**self.active(), 'accountId': 'wrong'},
                         lambda: {**self.active(), 'ownerNativeChatKey': 'wechat:other'}]:
            with self.subTest(provider=provider), self.assertRaises(NativeOwnerTextClaimError):
                self.inspect(active_authority_provider=provider)
        with self.assertRaises(NativeOwnerTextClaimError):
            self.inspect(authority_epoch='b' * 32)

    def test_queued_baseline_held_and_unknown_claim_never_admitted(self):
        for state in ('queued', 'baseline', 'held'):
            self.mutate(lambda value: value['claims'][0].update(state=state, holdReason='late-history'))
            with self.subTest(state=state), self.assertRaisesRegex(NativeOwnerTextClaimError, 'claim-not-submitted'):
                self.inspect()
        with self.assertRaisesRegex(NativeOwnerTextClaimError, 'claim-not-submitted'):
            self.inspect(claim_id='a' * 32)

    def test_hash_damage_malformed_duplicate_keys_and_claim_id_damage(self):
        original = self.file.read_bytes()
        for data in [b'{', original.replace(b'fixture-account', b'wrong-account'),
                     b'{"digest":"a","digest":"b"}']:
            self.file.write_bytes(data)
            with self.subTest(data=data[:20]), self.assertRaises(NativeOwnerTextClaimError):
                self.inspect()
        self.file.write_bytes(original)
        self.mutate(lambda value: value['claims'][0].update(claimId='a' * 32))
        with self.assertRaisesRegex(NativeOwnerTextClaimError, 'claim-id-invalid'):
            self.inspect()

    def test_old_and_future_timestamp_and_invalid_submission_cannot_be_fresh(self):
        original = self.file.read_bytes()
        for field, stamp in [('timestampSeconds', 1772323198), ('timestampSeconds', 1772323300),
                             ('submittedAt', 0), ('submittedAt', 1772323300000), ('submittedAt', 1772323198000)]:
            self.file.write_bytes(original)
            def change(value):
                claim = value['claims'][0]; claim[field] = stamp
                claim['claimId'] = module._hash(['fixture-account', claim['nativeChatKey'], claim['nativeId'], claim['serverId'],
                    claim['senderId'], claim['rawText'], claim['timestampSeconds'], claim['localId']])[:32]
                self.changed_id = claim['claimId']
            self.mutate(change)
            with self.subTest(field=field, stamp=stamp), self.assertRaises(NativeOwnerTextClaimError):
                self.inspect(claim_id=self.changed_id)

    def test_same_body_not_enough_exact_source_row_required(self):
        self.mutate(lambda value: value['claims'][0].update(snapshotSourceId='["message/message_0.db","wrong",1]'))
        with self.assertRaisesRegex(NativeOwnerTextClaimError, 'native-source-mismatch'):
            self.inspect()

    def test_absent_native_id_cannot_be_replaced_by_same_text(self):
        def change(value):
            claim = value['claims'][0]
            claim.update(nativeId='wechat-server:9999', serverId='9999')
            claim['claimId'] = module._hash(['fixture-account', claim['nativeChatKey'], claim['nativeId'], claim['serverId'],
                claim['senderId'], claim['rawText'], claim['timestampSeconds'], claim['localId']])[:32]
            self.changed_id = claim['claimId']
        self.mutate(change)
        with self.assertRaisesRegex(NativeOwnerTextClaimError, 'native-source-mismatch'):
            self.inspect(claim_id=self.changed_id)

    def test_link_count_and_open_handle_identity_guards_without_os_link_privilege(self):
        actual_stat = Path.stat
        def fake_stat(path, *args, **kwargs):
            value = actual_stat(path, *args, **kwargs)
            if path == self.file:
                return SimpleNamespace(**{key: getattr(value, key) for key in
                    ('st_mode', 'st_size', 'st_dev', 'st_ino', 'st_mtime_ns')}, st_nlink=2)
            return value
        with patch.object(Path, 'stat', fake_stat), self.assertRaisesRegex(NativeOwnerTextClaimError, 'file-invalid'):
            self.inspect()
        actual_fstat = os.fstat
        def changed_handle(fd):
            value = actual_fstat(fd)
            return SimpleNamespace(**{key: getattr(value, key) for key in ('st_dev', 'st_size', 'st_mtime_ns', 'st_nlink')}, st_ino=value.st_ino + 1)
        with patch.object(module.os, 'fstat', side_effect=changed_handle), self.assertRaisesRegex(NativeOwnerTextClaimError, 'file-changed'):
            self.inspect()

    def test_authenticated_snapshot_mutation_rejected(self):
        with closing(sqlite3.connect(self.snapshot / 'message/message_0.db')) as db:
            db.execute(f'UPDATE [{self.table}] SET message_content=? WHERE server_id=9001', ('changed fixture',)); db.commit()
        with self.assertRaises(NativeOwnerTextClaimError):
            self.inspect()

    def test_authority_file_and_config_rechecked_after_snapshot(self):
        original = module.list_native_owner_texts
        def mutate_during(*args, **kwargs):
            result = original(*args, **kwargs)
            self.mutate(lambda value: value.update(revision=value['revision'] + 1))
            return result
        with patch.object(module, 'list_native_owner_texts', side_effect=mutate_during), self.assertRaisesRegex(NativeOwnerTextClaimError, 'file-changed'):
            self.inspect()
        calls = []
        def active_then_off():
            calls.append(True)
            return {**self.active(), 'active': len(calls) == 1}
        with self.assertRaisesRegex(NativeOwnerTextClaimError, 'authority-inactive'):
            self.inspect(active_authority_provider=active_then_off)
        def config_during(*args, **kwargs):
            result = original(*args, **kwargs); self.config['capture']['sourceId'] = 'different'; return result
        with patch.object(module, 'list_native_owner_texts', side_effect=config_during), self.assertRaisesRegex(NativeOwnerTextClaimError, 'config-changed'):
            self.inspect()

    def test_hardlink_and_symbolic_link_refused(self):
        alias = self.root / 'alias.json'
        try:
            os.link(self.file, alias)
        except OSError:
            self.skipTest('OS denies fixture hardlink creation')
        with self.assertRaisesRegex(NativeOwnerTextClaimError, 'file-invalid'):
            self.inspect()
        alias.unlink()
        copy = self.root / 'original.json'; self.file.rename(copy)
        try:
            self.file.symlink_to(copy)
        except OSError:
            copy.rename(self.file)
            return
        with self.assertRaises(NativeOwnerTextClaimError):
            self.inspect()


if __name__ == '__main__':
    unittest.main()
