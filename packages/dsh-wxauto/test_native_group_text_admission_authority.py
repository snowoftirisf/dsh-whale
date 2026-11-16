"""Actual Node admission inbox/receipt + authenticated SQLite, never live UI."""
from dataclasses import dataclass
import copy,json,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
import native_group_text_admission_authority as m
from native_group_text_admission_source import verify_pending_text_admission_source
from native_group_text_catalog import list_native_group_texts
from native_owner_text_claim import _hash
from native_group_text_authority import NativeGroupTextAuthority
import test_native_group_text_admission_source as source_fixture

@dataclass(frozen=True)
class FakeUi:
 account_id:str;key:str;target:str;session_id:str;claim_id:str;claim_hash:str;snapshot_id:str

class AdmissionAuthorityTests(unittest.TestCase):
 def setUp(self):
  self.source_case=source_fixture.AdmissionTests();self.source_case.setUp();self.addCleanup(self.source_case.doCleanups)
  self.config=self.source_case.config;self.config['capture']['enabled']=True
  self.key=self.source_case.key;self.clock=1772323210.123;self.root=self.source_case.fixture.data
  proof=self.source_case.proof()
  self.catalog=list_native_group_texts(proof.snapshot_path,**proof.catalog_arguments)
  self.catalog['admission']={'claimId':proof.claim_id,'claimHash':proof.claim_hash,'snapshotId':proof.snapshot_id,'verified':True}
  script="""import fs from 'node:fs';import {createNativeGroupInbox}from './native-group-inbox.text-candidate.mjs';import {createNativeGroupTextInbox}from './native-group-text-inbox.admission-candidate.mjs';import {createNativeGroupTextAdmission}from './native-group-text-admission.mjs';const v=JSON.parse(fs.readFileSync(0,'utf8'));const raw=createNativeGroupInbox({dataRoot:v.root,accountId:v.record.accountId,textDelegation:true});const inbox=createNativeGroupTextInbox({dataRoot:v.root,accountId:v.record.accountId,nativeChatKey:v.record.nativeChatKey,now:()=>v.now});const helper=createNativeGroupTextAdmission({groupInbox:raw,textInbox:inbox,readCatalog:async()=>v.catalog,now:()=>v.now});const q=await helper.prepare(v.record,v.claimId);process.stdout.write(JSON.stringify({epoch:q.textDelegation.authorityEpoch}));"""
  result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps({'root':str(self.root),'record':self.source_case.route,'catalog':self.catalog,'now':int(self.clock*1000),'claimId':proof.claim_id}),
    text=True,encoding='utf8',capture_output=True,cwd=Path(__file__).parent,timeout=20)
  self.assertEqual(result.returncode,0,result.stderr);self.epoch=json.loads(result.stdout)['epoch']
  self.source_case.route['status']='active';self.source_case.save_registry()
  self.source=verify_pending_text_admission_source(self.config,self.key,proof.claim_id,proof.claim_hash)
  self.ui=FakeUi(self.source.account_id,self.key,self.source.target,self.source.session_id,self.source.claim_id,self.source.claim_hash,self.source.snapshot_id)
  self.controllers=[]
 def authority(self,**kw):
  a=m.NativeGroupTextAdmissionAuthority(self.config,self.key,clock=lambda:self.clock,
      ui_proof_verifier=kw.pop('ui_proof_verifier',lambda config,ui,source:ui),**kw)
  self.controllers.append(a);self.addCleanup(a.close);return a
 def test_actual_node_adoption_receipt_authorized_once_and_restore(self):
  a=self.authority();value=a.admit(self.source,self.epoch,self.ui)
  self.assertEqual(value['mode'],'native');self.assertEqual(value['admission']['rawClaimId'],self.source.claim_id)
  marker=a.paths()[1].read_bytes();self.assertFalse((a.paths()[0]/'transition.lock').exists())
  with self.assertRaisesRegex(RuntimeError,'first-admission-only'):a.admit(self.source,self.epoch,self.ui)
  self.assertEqual(a.paths()[1].read_bytes(),marker)
  restored=self.authority();self.assertEqual(restored.restore()['mode'],'native');self.assertEqual(restored.status()['admission'],value['admission'])
 def test_actual_register_group_capability_default_verifier_node_admit_and_base_restore(self):
  from native_group_ui_binding import confirm_registered_handoff
  f=self.source_case.fixture;handoff=f.verify();ui=f.monitor();result=f.register(ui,handoff)
  self.assertTrue(result['ok'],result)
  registered=confirm_registered_handoff(self.config,handoff,result,routes=ui.routes,registry=ui.registry)
  a=self.authority(ui_proof_verifier=None)
  value=a.admit(self.source,self.epoch,registered);self.assertEqual(value['mode'],'native')
  self.assertEqual(value['admission']['rawClaimHash'],self.source.claim_hash)
  base=NativeGroupTextAuthority(self.config,self.key);self.addCleanup(base.close)
  self.assertEqual(base.restore()['authorityEpoch'],self.epoch)
  self.assertEqual(base.mode,'native')
 def test_status_never_exposes_admission_when_marker_changed_or_not_native(self):
  a=self.authority();a.admit(self.source,self.epoch,self.ui)
  marker=json.loads(a.paths()[1].read_text(encoding='utf8'));marker['admission']['rawClaimHash']='0'*64
  marker.pop('digest');marker['digest']=_hash(marker);a.paths()[1].write_text(json.dumps(marker),encoding='utf8')
  value=a.status();self.assertEqual(value['mode'],'blocked');self.assertNotIn('admission',value)
 def test_missing_default_ui_or_bool_verifier_cannot_mint_marker_or_lease(self):
  for verifier in (m.trusted_ui_verifier,lambda *args:True):
   a=self.authority(ui_proof_verifier=verifier)
   with self.assertRaises(RuntimeError):a.admit(self.source,self.epoch,self.ui)
   self.assertFalse(a.paths()[1].exists());self.assertFalse((a.paths()[0]/'transition.lock').exists())
 def test_wrong_actual_ui_scope_or_pending_route_refused(self):
  a=self.authority();wrong=FakeUi(self.ui.account_id,self.ui.key,'other',self.ui.session_id,self.ui.claim_id,self.ui.claim_hash,self.ui.snapshot_id)
  with self.assertRaisesRegex(RuntimeError,'ui-proof-scope-invalid'):a.admit(self.source,self.epoch,wrong)
  self.source_case.route['status']='pending';self.source_case.save_registry()
  proof=self.source_case.proof()
  with self.assertRaisesRegex(RuntimeError,'route-inactive'):a.admit(proof,self.epoch,self.ui)
  self.assertFalse(a.paths()[1].exists())
 def test_native_second_cutoff_cannot_be_shifted_even_with_valid_state_digest(self):
  a=self.authority();file=a.paths()[2];original=file.read_bytes();value=json.loads(original)
  expected=min(x['timestampSeconds'] for x in self.source.queued['messages'] if x['type']=='text')*1000
  self.assertEqual(value['cutoverAtMs'],expected)
  for offset in (-1,1,999):
   value=json.loads(original);value['cutoverAtMs']=expected+offset
   value.pop('digest');value['digest']=_hash(value);file.write_text(json.dumps(value),encoding='utf8')
   with self.assertRaisesRegex(RuntimeError,'native-cutoff-mismatch'):a._validate_adoption(self.source,self.epoch,value,self.catalog)
   # The shared schema may reject first; otherwise our native-boundary check
   # rejects. Neither validation layer is permitted to create a lease/marker.
   with self.assertRaisesRegex(RuntimeError,'inbox-invalid|native-cutoff-mismatch'):a.admit(self.source,self.epoch,self.ui)
   self.assertFalse(a.paths()[1].exists());self.assertFalse((a.paths()[0]/'transition.lock').exists())
  file.write_bytes(original)
 def test_tampered_inbox_unread_ids_or_already_submitted_do_not_gain_authority(self):
  a=self.authority();file=a.paths()[2];original=file.read_bytes();value=json.loads(original)
  value['admission']['unreadNativeIds']=[];value.pop('digest');value['digest']=_hash(value);file.write_text(json.dumps(value),encoding='utf8')
  with self.assertRaisesRegex(RuntimeError,'unread-admission-mismatch'):a.admit(self.source,self.epoch,self.ui)
  file.write_bytes(original);value=json.loads(original);value['claims'][0]['state']='submitted';value['claims'][0]['submittedAt']=int(self.clock*1000)
  value.pop('digest');value['digest']=_hash(value);file.write_text(json.dumps(value),encoding='utf8')
  with self.assertRaisesRegex(RuntimeError,'claim-envelope-invalid|unread-claim-not-queued'):a.admit(self.source,self.epoch,self.ui)
  self.assertFalse(a.paths()[1].exists())
 def test_raw_delegation_digest_and_bound_source_metadata_are_checked(self):
  raw=self.source_case.fixture.file;value=json.loads(raw.read_text(encoding='utf8'));value['textDelegation']['texts'][0]['rawSha256']='0'*64
  self.assertEqual(value['claimId'],self.source.claim_id);self.assertEqual(value['claimHash'],self.source.claim_hash)
  r=value['textDelegation'];r['digest']=_hash([value['accountId'],self.key,value['claimId'],value['claimHash'],self.epoch,'new-admission',r['texts'],r['newTextClaimIds']])
  raw.write_text(json.dumps(value),encoding='utf8')
  # Use the frozen actually-verified claim, not another test helper's mutable
  # claim template. This mutation changes only the receipt, never source scope.
  proof=verify_pending_text_admission_source(self.config,self.key,self.source.claim_id,self.source.claim_hash);a=self.authority()
  with self.assertRaisesRegex(RuntimeError,'delegation-source-invalid'):a.admit(proof,self.epoch,self.ui)
  self.assertFalse(a.paths()[1].exists())
 def test_old_handled_server_id_cannot_be_readmitted_by_stale_unread_receipt(self):
  file=self.source_case.fixture.file;value=json.loads(file.read_text(encoding='utf8'));value['handledServerIds']=['9001']
  file.write_text(json.dumps(value),encoding='utf8');proof=self.source_case.proof();a=self.authority()
  with self.assertRaisesRegex(RuntimeError,'unread-admission-mismatch'):a.admit(proof,self.epoch,self.ui)
  self.assertFalse(a.paths()[1].exists());self.assertFalse((a.paths()[0]/'transition.lock').exists())
 def test_catalog_then_queue_change_refuses_before_lock_or_marker(self):
  def reader(*args,**kw):
   result=list_native_group_texts(*args,**kw);value=json.loads(self.source_case.fixture.file.read_text(encoding='utf8'));value['state']='submitted'
   self.source_case.fixture.file.write_text(json.dumps(value),encoding='utf8');return result
  a=self.authority(catalog_reader=reader)
  with self.assertRaises(RuntimeError):a.admit(self.source,self.epoch,self.ui)
  self.assertFalse(a.paths()[1].exists());self.assertFalse((a.paths()[0]/'transition.lock').exists())
 def test_failed_exclusive_marker_write_preserves_unknown_lease_and_never_retries(self):
  a=self.authority();original=m.local_path
  def denied(path):
   if Path(path).name=='authority.json':raise OSError('fixture-private-path')
   return original(path)
  with patch.object(m,'local_path',side_effect=denied):
   with self.assertRaisesRegex(RuntimeError,'commit-unknown'):a.admit(self.source,self.epoch,self.ui)
  self.assertEqual(a.mode,'blocked');self.assertTrue((a.paths()[0]/'transition.lock').exists())
  with self.assertRaisesRegex(RuntimeError,'first-admission-only'):a.admit(self.source,self.epoch,self.ui)
  a.close();self.assertIsNone(a._lease_fd);self.assertTrue((a.paths()[0]/'transition.lock').exists())
 def test_generic_commit_remains_unmodified_and_rejects_admission_queued_baseline(self):
  a=self.authority();a.restore();a.prepare()
  with self.assertRaisesRegex(RuntimeError,'baseline-required'):a.commit(a.ticket,self.epoch)
  self.assertFalse(a.paths()[1].exists())

if __name__=='__main__':unittest.main()
