"""Actual isolated filesystem journal tests; no UI, production or plan writes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from social_actions import _digest
from social_friend_request_journal import FriendRequestJournal

HERE=Path(__file__).resolve().parent

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=HERE);self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        parameters={'keywords':'fixture_id','expectedProfileName':'fixture-person','addmsg':'approved greeting'}
        self.plan={'id':'a'*32,'hash':_digest('add_friend',parameters),'action':'add_friend','parameters':parameters,
            'origin':{'kind':'friend','target':'owner'},'status':'claimed','approvedBy':'owner','approvalMessageId':'fixture-grant','expiresAt':1000}
        self.journal=self.new();self.snapshot='b'*64
    def new(self):return FriendRequestJournal(self.root,lambda _:copy.deepcopy(self.plan),'owner',clock=lambda:100)
    def claim(self):return self.journal.claim(self.plan['id'],self.plan['hash'],self.snapshot)
    def folder(self):return self.root/'config/friend-request-journal'/self.plan['id']

    def test_inert_constructor_no_provider_or_filesystem(self):
        with patch('social_friend_request_journal.resolve_data_paths',side_effect=AssertionError('FS')):
            FriendRequestJournal(self.root,lambda _:self.fail('provider'),'owner')
        self.assertFalse((self.root/'config').exists())

    def test_actual_flush_fsync_and_immutable_two_stage_readback_after_reopen(self):
        original=os.fsync
        with patch('social_friend_request_journal.os.fsync',wraps=original) as fsync:
            claim=self.claim();mark=self.journal.mark(self.plan['id'],self.plan['hash'],claim['claimId'],self.snapshot)
            self.assertGreaterEqual(fsync.call_count,2)
        reopened=self.new()
        self.assertEqual(reopened.readback(self.plan['id'],self.plan['hash'],claim['claimId'],'claimed'),claim)
        self.assertEqual(reopened.readback(self.plan['id'],self.plan['hash'],claim['claimId'],'confirm-attempt'),mark)
        self.assertEqual(set(p.name for p in self.folder().iterdir()),{'claim.json','mark.json'})
        with self.assertRaisesRegex(RuntimeError,'already-exists'):reopened.claim(self.plan['id'],self.plan['hash'],self.snapshot)
        with self.assertRaisesRegex(RuntimeError,'claim-not-owned'):reopened.mark(self.plan['id'],self.plan['hash'],claim['claimId'],self.snapshot)
        with self.assertRaisesRegex(RuntimeError,'claim-not-owned'):self.journal.mark(self.plan['id'],self.plan['hash'],claim['claimId'],self.snapshot)

    def test_crash_after_claim_restart_cannot_mark_using_only_disk_identity(self):
        claim=self.claim();reopened=self.new()
        with self.assertRaisesRegex(RuntimeError,'claim-not-owned'):reopened.mark(self.plan['id'],self.plan['hash'],claim['claimId'],self.snapshot)
        self.assertFalse((self.folder()/'mark.json').exists())
        with self.assertRaisesRegex(RuntimeError,'already-exists'):reopened.claim(self.plan['id'],self.plan['hash'],self.snapshot)

    def test_failed_fsync_leaves_marker_no_new_permission_or_retry(self):
        with patch('social_friend_request_journal.os.fsync',side_effect=OSError('private path')):
            with self.assertRaisesRegex(RuntimeError,'write-unconfirmed'):self.claim()
        self.assertTrue((self.folder()/'claim.json').exists())
        with self.assertRaisesRegex(RuntimeError,'already-exists'):self.new().claim(self.plan['id'],self.plan['hash'],self.snapshot)
        self.assertFalse(self.journal._claims)

    def test_failed_mark_fsync_is_not_retried_or_unlinked(self):
        claim=self.claim()
        with patch('social_friend_request_journal.os.fsync',side_effect=OSError('private path')):
            with self.assertRaisesRegex(RuntimeError,'write-unconfirmed'):self.journal.mark(self.plan['id'],self.plan['hash'],claim['claimId'],self.snapshot)
        self.assertTrue((self.folder()/'mark.json').exists())
        with self.assertRaisesRegex(RuntimeError,'claim-not-owned'):self.journal.mark(self.plan['id'],self.plan['hash'],claim['claimId'],self.snapshot)

    def test_torn_corrupt_existing_records_are_not_removed_or_granted_again(self):
        self.folder().mkdir(parents=True);file=self.folder()/'claim.json';file.write_bytes(b'{torn')
        with self.assertRaisesRegex(RuntimeError,'already-exists'):self.claim()
        with self.assertRaisesRegex(RuntimeError,'record-unavailable'):self.journal.readback(self.plan['id'],self.plan['hash'],'c'*32,'claimed')
        self.assertEqual(file.read_bytes(),b'{torn')

    def test_plan_state_owner_hash_expiry_and_parameters_rechecked(self):
        changes=[('status','approved'),('status','unknown'),('status','confirmed'),('status','pending'),
            ('approvedBy','other'),('approvalMessageId',''),('expiresAt',99),('hash','c'*64)]
        for key,value in changes:
            original=copy.deepcopy(self.plan);self.plan[key]=value
            with self.assertRaises(RuntimeError):self.claim()
            self.assertFalse((self.root/'config').exists());self.plan=original
        self.plan['parameters']['addmsg']='unapproved changed greeting'
        with self.assertRaisesRegex(RuntimeError,'plan-mismatch'):self.claim()

    def test_mark_requires_same_claim_snapshot_and_still_claimed_plan(self):
        claim=self.claim()
        for claim_id,snapshot in [('c'*32,self.snapshot),(claim['claimId'],'d'*64)]:
            with self.assertRaisesRegex(RuntimeError,'claim-not-owned'):self.journal.mark(self.plan['id'],self.plan['hash'],claim_id,snapshot)
        self.plan['status']='unknown'
        with self.assertRaisesRegex(RuntimeError,'claimed-plan-required'):self.journal.mark(self.plan['id'],self.plan['hash'],claim['claimId'],self.snapshot)
        # Readback remains historical evidence, not a fresh execution grant.
        self.assertEqual(self.new().readback(self.plan['id'],self.plan['hash'],claim['claimId'],'claimed'),claim)

    def test_record_tamper_foreign_plan_and_read_failure_fail_closed(self):
        claim=self.claim();file=self.folder()/'claim.json'
        value=json.loads(file.read_text());value['receipt']['snapshotSha256']='f'*64;file.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError,'record-invalid'):self.journal.readback(self.plan['id'],self.plan['hash'],claim['claimId'],'claimed')
        with self.assertRaises(RuntimeError):self.journal.readback('d'*32,self.plan['hash'],claim['claimId'],'claimed')
        with patch.object(Path,'open',side_effect=PermissionError('private-file')):
            with self.assertRaisesRegex(RuntimeError,'record-unavailable'):self.journal.readback(self.plan['id'],self.plan['hash'],claim['claimId'],'claimed')

    def test_hardlinked_record_and_linked_journal_directory_are_rejected(self):
        claim=self.claim();file=self.folder()/'claim.json'
        try:os.link(file,self.root/'alias.json')
        except OSError:self.skipTest('host denies creating isolated hardlinks')
        with self.assertRaisesRegex(RuntimeError,'record-invalid'):self.journal.readback(self.plan['id'],self.plan['hash'],claim['claimId'],'claimed')
        other=self.root/'outside';other.mkdir()
        linked=self.root/'linked-data'
        try:linked.symlink_to(other,target_is_directory=True)
        except OSError:return  # Hardlink rejection above was still exercised.
        journal=FriendRequestJournal(linked,lambda _:copy.deepcopy(self.plan),'owner',clock=lambda:100)
        with self.assertRaises(RuntimeError):journal.claim(self.plan['id'],self.plan['hash'],self.snapshot)

    def test_reported_multiple_links_fail_before_open_even_on_link_restricted_host(self):
        claim=self.claim();file=self.folder()/'claim.json';original=Path.lstat
        def lstat(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            if path==file:return SimpleNamespace(st_mode=value.st_mode,st_nlink=2,st_size=value.st_size,
                st_file_attributes=getattr(value,'st_file_attributes',0))
            return value
        with patch.object(Path,'lstat',lstat):
            with self.assertRaisesRegex(RuntimeError,'record-invalid'):
                self.journal.readback(self.plan['id'],self.plan['hash'],claim['claimId'],'claimed')

    def test_full_fake_ui_candidate_uses_actual_two_stage_files_without_live_promotion(self):
        from test_social_friend_request_candidate import RequestTests
        fixture=RequestTests();fixture.setUp()
        fixture.candidate.journal=FriendRequestJournal(self.root,lambda _:copy.deepcopy(fixture.plan),'owner',clock=lambda:100)
        result=fixture.run_request()
        self.assertTrue(result['confirmed']);self.assertFalse(result['friendshipConfirmed'])
        folder=self.root/'config/friend-request-journal'/fixture.plan['id']
        claimed=json.loads((folder/'claim.json').read_text())['receipt']
        mark=json.loads((folder/'mark.json').read_text())['receipt']
        self.assertEqual(claimed['claimId'],mark['claimId'])
        self.assertEqual(claimed['snapshotSha256'],mark['snapshotSha256'])
        self.assertEqual(fixture.events.count('submit-request'),1)
        self.assertEqual(claimed['parametersHash'],fixture.hash)

    def test_two_actual_processes_have_exactly_one_exclusive_claim_winner(self):
        planfile=self.root/'plan.json';planfile.write_text(json.dumps(self.plan));gate=self.root/'start'
        command=[sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--claim-child',str(self.root),str(planfile),str(gate)]
        children=[subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf8') for _ in range(2)]
        gate.write_text('go')
        outcomes=[]
        for child in children:
            stdout,stderr=child.communicate(timeout=15)
            self.assertEqual(child.returncode,0,stderr)
            result=json.loads(stdout);outcomes.append(result['ok'])
            if not result['ok']:self.assertEqual(result['code'],'social-friend-journal-already-exists')
        self.assertEqual(sorted(outcomes),[False,True])
        self.assertEqual(len(list(self.folder().glob('claim.json'))),1)


def child():
    root,planfile,gate=map(Path,sys.argv[2:]);plan=json.loads(planfile.read_text())
    deadline=time.monotonic()+5
    while not gate.exists():
        if time.monotonic()>deadline:raise RuntimeError('fixture-gate-timeout')
        time.sleep(.01)
    journal=FriendRequestJournal(root,lambda _:json.loads(planfile.read_text()),'owner',clock=lambda:100)
    try:journal.claim(plan['id'],plan['hash'],'b'*64);result={'ok':True}
    except RuntimeError as error:result={'ok':False,'code':str(error)}
    print(json.dumps(result))

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--claim-child':child()
    else:unittest.main()
