"""Staged bridge with actual Node inbox and authenticated native SQLite fixtures."""
import json
from pathlib import Path
import unittest
import hashlib
import subprocess
from unittest.mock import patch
from native_owner_text_claim import _compact
from worker_group_text_candidate import WorkerGroupTextBridge
from native_group_text_authority import NativeGroupTextAuthority
import test_native_group_text_admission_authority as fixtures


class WorkerAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AdmissionAuthorityTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.events = []; self.messages = []
        self.bridge = WorkerGroupTextBridge(self.f.config, publish=self.messages.append, emit=self.events.append,
            controller_factory=lambda config,key: fixtures.m.NativeGroupTextAdmissionAuthority(config,key,clock=lambda:self.f.clock))
        self.addCleanup(self.bridge.close)
        self.source = self.f.source
        handoff_fixture = self.f.source_case.fixture
        self.handoff = handoff_fixture.verify()
        self.ui = handoff_fixture.monitor()
        self.result = handoff_fixture.register(self.ui, self.handoff)
        self.assertTrue(self.result['ok'], self.result)

    def command(self, op='admit', **extra):
        return {'op':'group-text-'+op, 'id':'test-admission', 'target':self.source.target,
            'nativeChatKey':self.source.native_chat_key,
            **({'claimId':self.source.claim_id, 'claimHash':self.source.claim_hash,
                'authorityEpoch':self.f.epoch} if op=='admit' else {}), **extra}

    def call(self, op='admit', **extra):
        return self.bridge.command(self.command(op, **extra), monitor=self.ui.monitor)

    def remember(self):
        self.bridge.remember_registered_handoff(self.handoff, self.result,
            routes=self.ui.routes, registry=self.ui.registry)
        self.bridge.set_route_active(self.source.target, False)

    def test_first_admit_actual_ui_capability_node_receipt_and_python_catalog(self):
        self.remember()
        result = self.call()
        self.assertTrue(result['ok'], result)
        state = result['value']
        self.assertEqual(state['mode'], 'native')
        self.assertTrue(state['uiMonitoringSuspended'])
        self.assertEqual(state['admission']['rawClaimId'], self.source.claim_id)
        self.assertEqual(self.bridge.monitor_authority(self.source.target)['mode'], 'blocked')
        self.assertEqual(self.call('status')['value']['mode'], 'native')
        original = NativeGroupTextAuthority(self.f.config, self.source.native_chat_key)
        self.assertEqual(original.restore()['authorityEpoch'], self.f.epoch)
        original.close()
        self.assertFalse(self.messages)
        self.bridge.set_route_active(self.source.target, True)
        self.assertEqual(self.bridge.monitor_authority(self.source.target)['mode'], 'native')

    def test_missing_ui_capability_or_not_suspended_cannot_admit(self):
        self.assertFalse(self.call()['ok'])
        self.bridge.set_route_active(self.source.target, False)
        self.assertFalse(self.call()['ok'])
        controller = self.bridge.controllers[self.source.native_chat_key]
        self.assertFalse(controller.paths()[1].exists())
        self.assertFalse(self.messages)

    def test_existing_quiet_ops_do_not_run_during_suspended_admission(self):
        self.remember()
        self.assertEqual(self.call('prepare')['code'], 'group-text-blocked')
        self.assertEqual(self.call(claimHash='bad')['code'], 'group-text-request-invalid')
        cmd = self.command()
        result = self.bridge.command(cmd, monitor=self.ui.monitor, delivery_active=True)
        self.assertEqual(result['code'], 'group-text-worker-busy')
        self.assertFalse(self.messages)

    def test_busy_ack_is_correlated_and_precedes_all_admission_side_effects(self):
        self.remember()
        before={str(p):p.read_bytes() for p in self.f.root.rglob('*') if p.is_file()}
        cmd=self.command(id='busy-请求-🙂')
        with patch('native_group_text_admission_source.verify_pending_text_admission_source',
                   side_effect=AssertionError('busy must precede source verification')):
            result=self.bridge.command(cmd,monitor=self.ui.monitor,delivery_active=True)
        self.assertEqual(result['code'],'group-text-worker-busy')
        self.assertEqual(result['issuance'],'not-issued')
        self.assertEqual(result['phase'],'before-admit')
        self.assertEqual(result['workerInstanceId'],self.bridge.instance_id)
        fields=[cmd[k]for k in ('op','id','target','nativeChatKey','claimId','claimHash','authorityEpoch')]
        expected=hashlib.sha256(_compact(fields)).hexdigest()
        self.assertEqual(result['requestFingerprint'],expected)
        js="import fs from 'node:fs';import {createHash}from 'node:crypto';const v=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(createHash('sha256').update(JSON.stringify(v)).digest('hex'));"
        check=subprocess.run(['node','--input-type=module','-e',js],input=json.dumps(fields),
            text=True,encoding='utf8',capture_output=True,timeout=10)
        self.assertEqual(check.returncode,0,check.stderr)
        self.assertEqual(check.stdout,expected)
        self.assertEqual(before,{str(p):p.read_bytes()for p in self.f.root.rglob('*')if p.is_file()})
        self.assertFalse((self.bridge.controllers[self.source.native_chat_key].paths()[0]/'transition.lock').exists())
        self.assertFalse(self.messages)
        busy=self.bridge.command(self.command('status'),monitor=self.ui.monitor,delivery_active=True)
        self.assertFalse(busy['value']['admitReady'])
        idle=self.call('status')
        self.assertTrue(idle['value']['admitReady'])
        self.assertEqual(idle['value']['workerInstanceId'],result['workerInstanceId'])
        self.assertTrue(self.call()['ok'])
        self.assertFalse(self.call('status')['value']['admitReady'])

    def test_other_denials_and_status_do_not_issue_not_executed_evidence(self):
        self.remember()
        cases=[(self.command(),{'independent':True,'delivery_active':True}),
               (self.command('prepare'),{'delivery_active':True}),
               (self.command(claimHash='bad'),{'delivery_active':True})]
        for cmd,options in cases:
            result=self.bridge.command(cmd,monitor=self.ui.monitor,**options)
            self.assertFalse(result['ok'])
            self.assertNotIn('issuance',result)
            self.assertNotIn('requestFingerprint',result)
        self.assertNotIn('issuance',self.call('status'))

    def test_committed_marker_and_coordinator_journal_survive_restart(self):
        self.remember(); self.assertTrue(self.call()['ok'])
        folder = self.bridge.controllers[self.source.native_chat_key].paths()[0]
        journal = folder/'admission-attempts'; journal.mkdir()
        (journal/(self.source.claim_id+'.json')).write_text('{"privateCoordinatorJournal":true}',encoding='utf8')
        other = WorkerGroupTextBridge(self.f.config,publish=self.messages.append,emit=self.events.append)
        self.addCleanup(other.close)
        state = other.status()[0]
        self.assertEqual(state['mode'], 'native', state)
        self.assertEqual(state['admission']['rawClaimHash'], self.source.claim_hash)
        self.assertFalse(other.inventory_unknown)
        self.assertFalse(other.pending_recovery_keys)
        self.assertFalse(self.messages)


if __name__ == '__main__': unittest.main()
