import copy
from contextlib import closing
import json
from pathlib import Path
import subprocess
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import native_owner_text_authority as module
from native_owner_text_authority import NativeOwnerTextAuthority, NativeOwnerTextAuthorityError
from native_owner_text_claim import _compact, _hash
from native_owner_text_catalog import list_native_owner_texts
from vault.snapshot_import import load_reader
from vault.test_snapshot_import import make_snapshot,TABLE,OWNER,SENDER

HERE = Path(__file__).parent


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HERE);self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {'dataRoot':str(self.root),'accountId':'fixture-account','target':'Owner',
                       'capture':{'enabled':True,'accountUsername':'self','sourceId':'fixture-source'},
                       'nativeVoiceSources':{'ownerNativeChatKey':'wechat:peer'}}
        self.controller = NativeOwnerTextAuthority(self.config,clock=lambda:200)
        self.file = self.root/'config/native-owner-text-authority.json'
        self.inbox = self.root/'config/native-owner-text-inbox.json'
        self.event = {'event':'message','id':'synthetic','target':'Owner','kind':'friend','mediaType':'text',
                      'text':'synthetic fresh UI event','privateNested':{'fixture':True}}

    def baseline(self,empty=False):
        snapshot=make_snapshot(self.root/'prepared',rows=[] if empty else [(1,9,1,100,2,'synthetic baseline',0)],hash_manifest=True)
        table=load_reader().message_table(SENDER)
        with closing(sqlite3.connect(snapshot/'message/message_0.db')) as db:
            db.execute(f'ALTER TABLE [{TABLE}] RENAME TO [{table}]');db.commit()
        from vault.store import digest_file
        manifest_file=snapshot/'snapshot-manifest.json';manifest=json.loads(manifest_file.read_text('utf8'))
        manifest['source_id']='fixture-source'
        manifest['databases']=[{'database':p.relative_to(snapshot).as_posix(),'authenticated':True,'plaintext_sha256':digest_file(p)} for p in snapshot.rglob('*.db')]
        manifest_file.write_text(json.dumps(manifest),encoding='utf8')
        target=self.root/'snapshots/windows-live/snapshots'/manifest['snapshot_id'];target.parent.mkdir(parents=True);shutil.move(snapshot,target)
        self.config['nativeVoiceSources']['ownerNativeChatKey']='wechat:'+SENDER
        self.config['capture']['accountUsername']=OWNER
        catalog=list_native_owner_texts(target,expected_account_username=OWNER,expected_source_id='fixture-source',
            account_id='fixture-account',owner_native_chat_key='wechat:'+SENDER,expected_peer_username=SENDER)
        script="""
import fs from 'node:fs';
import {createNativeOwnerTextInbox,ownerTextScopeDigest} from './native-owner-text-inbox.mjs';
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const inbox=createNativeOwnerTextInbox({...input.config,now:()=>200000});
console.log(JSON.stringify(inbox.initialize(input.catalog,{cutoverAtMs:200000})));
"""
        settings={'dataRoot':str(self.root),'accountId':'fixture-account','ownerNativeChatKey':'wechat:'+SENDER,'expectedPeerUsername':SENDER}
        result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps({'config':settings,'catalog':catalog}),text=True,
            encoding='utf8',capture_output=True,cwd=HERE,check=True)
        return json.loads(result.stdout)['authorityEpoch']

    def change_inbox(self,mutate):
        value=json.loads(self.inbox.read_text('utf8'));value.pop('digest');mutate(value);value['digest']=_hash(value)
        self.inbox.write_bytes(_compact(value))

    def prepare(self):
        self.assertEqual(self.controller.restore()['mode'],'ui')
        state=self.controller.prepare();self.assertEqual(state['preparedAtMs'],200000)
        return state['ticket']

    def test_constructor_inert_explicit_baseline_commit_and_restart_native(self):
        self.assertEqual(list(self.root.iterdir()),[])
        epoch=self.baseline();ticket=self.prepare()
        self.assertEqual(self.controller.commit(ticket,epoch)['mode'],'native')
        marker=self.file.read_bytes();fresh=NativeOwnerTextAuthority(self.config)
        self.assertEqual(fresh.restore()['authorityEpoch'],epoch)
        routed=fresh.route_event(self.event);self.assertEqual(routed['action'],'hint')
        self.assertNotIn('synthetic fresh',json.dumps(routed));self.assertEqual(self.file.read_bytes(),marker)
        with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'ui-required'):fresh.prepare()
        with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'ticket-invalid'):fresh.abort(ticket)

    def test_preparing_defers_deepcopy_prevents_commit_and_abort_drains_once(self):
        epoch=self.baseline();ticket=self.prepare()
        self.assertEqual(self.controller.route_event(self.event),{'action':'deferred'})
        self.event['privateNested']['fixture']=False
        with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'not-quiet'):self.controller.commit(ticket,epoch)
        self.assertFalse(self.file.exists());drained=self.controller.abort(ticket)['events']
        self.assertTrue(drained[0]['privateNested']['fixture']);self.assertEqual(len(drained),1)
        with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'ticket-invalid'):self.controller.abort(ticket)
        self.assertEqual(self.controller.route_event(self.event)['action'],'pass')
        self.assertNotIn('preparedAtMs',self.controller.status())

    def test_nonowner_and_media_are_unchanged_in_every_mode(self):
        epoch=self.baseline();ticket=self.prepare()
        events=[{**self.event,'target':'Group','kind':'group'},
                *[{**self.event,'mediaType':kind} for kind in ('image','voice','file')],
                {**self.event,'images':[{'fixture':True}]}]
        for event in events:
            self.assertEqual(self.controller.route_event(event),{'action':'pass','event':event})
        self.controller.commit(ticket,epoch)
        for event in events:self.assertEqual(self.controller.route_event(event),{'action':'pass','event':event})

    def test_first_commit_refuses_queued_submitted_held_wrong_epoch_or_hash(self):
        epoch=self.baseline();ticket=self.prepare();original=self.inbox.read_bytes()
        for state in ('queued','submitted','held'):
            self.inbox.write_bytes(original)
            def mutate(value):
                value['claims'][0]['state']=state
                if state=='submitted':value['claims'][0]['submittedAt']=200000
                if state=='held':value['claims'][0]['holdReason']='late-history'
            self.change_inbox(mutate)
            with self.assertRaises(Exception):self.controller.commit(ticket,epoch)
            self.assertFalse(self.file.exists())
        self.inbox.write_bytes(original)
        with self.assertRaises(Exception):self.controller.commit(ticket,'f'*32)
        self.inbox.write_text('{}',encoding='utf8')
        with self.assertRaises(Exception):self.controller.commit(ticket,epoch)
        self.assertFalse(self.file.exists())

    def test_partial_write_blocks_and_retains_marker_never_returns_ui(self):
        epoch=self.baseline();ticket=self.prepare();original=module.os.fsync
        def failure(fd):
            original(fd);self.file.write_bytes(b'{');raise OSError('fixture unknown')
        with patch.object(module.os,'fsync',failure):
            with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'commit-unknown'):self.controller.commit(ticket,epoch)
        self.assertEqual(self.file.read_bytes(),b'{');self.assertEqual(self.controller.status()['mode'],'blocked')
        self.assertEqual(self.controller.route_event(self.event)['action'],'blocked')
        with self.assertRaises(Exception):self.controller.abort(ticket)
        fresh=NativeOwnerTextAuthority(self.config)
        with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'restore-unverified'):fresh.restore()
        self.assertEqual(fresh.status()['mode'],'blocked')

    def test_ack_loss_valid_record_restores_native_without_second_write(self):
        epoch=self.baseline();ticket=self.prepare();original=module._read
        def read(file):
            if Path(file)==self.file and self.file.exists():raise OSError('fixture lost readback')
            return original(file)
        with patch.object(module,'_read',read):
            with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'commit-unknown'):self.controller.commit(ticket,epoch)
        self.assertEqual(self.controller.status()['mode'],'blocked');marker=self.file.read_bytes()
        fresh=NativeOwnerTextAuthority(self.config);self.assertEqual(fresh.restore()['mode'],'native')
        self.assertEqual(self.file.read_bytes(),marker)

    def test_config_mismatch_source_off_and_queue_capacity_fail_closed(self):
        epoch=self.baseline();ticket=self.prepare();self.controller.commit(ticket,epoch)
        for changes in ({'accountId':'other'},{'target':'Other'},{'capture':{**self.config['capture'],'enabled':False}}):
            fresh=NativeOwnerTextAuthority({**self.config,**changes})
            with self.assertRaises(Exception):fresh.restore()
            self.assertEqual(fresh.status()['mode'],'blocked')
        self.file.unlink() # fixture only; no rollback API exists
        limited=NativeOwnerTextAuthority(self.config,max_deferred=1);limited.restore();limited.prepare()
        limited.route_event(self.event);overflow=limited.route_event(self.event)
        self.assertEqual(overflow['action'],'blocked');self.assertEqual(overflow['event'],self.event)
        self.assertTrue(overflow['requiresDurableDeferral']);self.assertTrue(overflow['stopOwnerTextReads'])
        self.event['privateNested']['fixture']=False
        self.assertTrue(overflow['event']['privateNested']['fixture'])
        self.assertTrue(limited._deferred[0]['privateNested']['fixture'])
        self.assertEqual(limited.status()['deferred'],1);self.assertEqual(limited.status()['mode'],'blocked')

    def test_absent_marker_keeps_ui_when_capture_disabled_or_never_native_configured(self):
        for config in ({**self.config,'capture':{'enabled':False}},
                       {'dataRoot':str(self.root),'target':'Owner'}):
            controller=NativeOwnerTextAuthority(config)
            self.assertEqual(controller.restore()['mode'],'ui')
            self.assertEqual(controller.route_event(self.event),{'action':'pass','event':self.event})
            with self.assertRaises(Exception):controller.prepare()
        self.assertFalse(self.file.exists())

    def test_local_baseline_shape_cannot_replace_authenticated_snapshot_and_legacy_pair_cannot_activate(self):
        epoch=self.baseline();ticket=self.prepare();original=self.inbox.read_bytes()
        def forged(value):
            claim=value['claims'][0];claim['rawText']='forged locally shaped text'
            claim['claimId']=_hash([value['accountId'],claim['nativeChatKey'],claim['nativeId'],claim['serverId'],claim['senderId'],
                                  claim['rawText'],claim['timestampSeconds'],claim['localId']])[:32]
        self.change_inbox(forged)
        with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'baseline-proof-mismatch'):self.controller.commit(ticket,epoch)
        self.assertFalse(self.file.exists());self.inbox.write_bytes(original)
        self.change_inbox(lambda value:(value.pop('baselineSnapshotId'),value.pop('baselineCatalogDigest')))
        with self.assertRaises(Exception):self.controller.commit(ticket,epoch)
        self.inbox.write_bytes(original);self.config['capture']['enabled']=False
        with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'source-proof-required'):self.controller.commit(ticket,epoch)
        self.assertFalse(self.file.exists())

    def test_authenticated_empty_baseline_can_commit_but_no_new_input_is_fabricated(self):
        epoch=self.baseline(empty=True);ticket=self.prepare()
        self.assertEqual(self.controller.commit(ticket,epoch)['mode'],'native')
        self.assertEqual(json.loads(self.inbox.read_text('utf8'))['claims'],[])
        self.assertEqual(NativeOwnerTextAuthority(self.config).restore()['mode'],'native')

    def test_restore_accepts_later_inbox_revision_but_never_changed_baseline_binding(self):
        epoch=self.baseline();ticket=self.prepare();self.controller.commit(ticket,epoch)
        self.change_inbox(lambda value:value.update(revision=2))
        self.assertEqual(NativeOwnerTextAuthority(self.config).restore()['mode'],'native')
        self.change_inbox(lambda value:value.update(baselineCatalogDigest='f'*64))
        fresh=NativeOwnerTextAuthority(self.config)
        with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'restore-unverified'):fresh.restore()
        self.assertEqual(fresh.status()['mode'],'blocked')

    def test_commit_refuses_arbitrary_earlier_cutoff_even_with_same_ceil_second(self):
        epoch=self.baseline();ticket=self.prepare()
        self.change_inbox(lambda value:value.update(cutoverAtMs=199999,cutoverFloorSeconds=200))
        with self.assertRaisesRegex(NativeOwnerTextAuthorityError,'prepare-boundary-mismatch'):self.controller.commit(ticket,epoch)
        self.assertFalse(self.file.exists());self.assertEqual(self.controller.status()['preparedAtMs'],200000)

    def test_late_written_preprepare_native_id_is_held_while_postboundary_queues(self):
        epoch=self.baseline();ticket=self.prepare();self.controller.commit(ticket,epoch)
        script="""
import fs from 'node:fs';
import {createNativeOwnerTextInbox,ownerTextScopeDigest} from './native-owner-text-inbox.mjs';
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const inbox=createNativeOwnerTextInbox({...input,now:()=>201000});
const row=(id,timestampSeconds)=>({nativeId:'wechat-server:'+id,serverId:String(id),nativeChatKey:input.ownerNativeChatKey,
 senderId:input.expectedPeerUsername,senderDisplayName:'Peer',rawText:'synthetic late ledger text',timestampSeconds,
 localId:id,snapshotSourceId:'synthetic/new/'+id,direction:'incoming'});
const result=inbox.enqueue({source:'authenticated-native-owner-text-catalog',accountId:input.accountId,nativeChatKey:input.ownerNativeChatKey,
 scopeDigest:ownerTextScopeDigest(input.accountId,input.ownerNativeChatKey,input.expectedPeerUsername),snapshotId:'synthetic-later',complete:true,
 messages:[row(10,199),row(11,200)]});
console.log(JSON.stringify({result,pending:inbox.pending().map(c=>c.nativeId),status:inbox.status()}));
"""
        config={'dataRoot':str(self.root),'accountId':self.config['accountId'],'ownerNativeChatKey':'wechat:'+SENDER,'expectedPeerUsername':SENDER}
        response=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(config),text=True,encoding='utf8',
            capture_output=True,cwd=HERE,check=True)
        value=json.loads(response.stdout);self.assertEqual(value['result']['held'],1);self.assertEqual(value['result']['queued'],1)
        self.assertEqual(value['pending'],['wechat-server:11']);self.assertEqual(value['status']['held'],1)


if __name__=='__main__':unittest.main()
