"""Real authenticated unread fixtures, no production or UI admission."""
import copy,json,unittest
from unittest.mock import patch
import native_group_text_admission_source as m
import native_group_text_io as io
import test_native_group_handoff as fixtures

class AdmissionTests(unittest.TestCase):
 def setUp(self):
  self.fixture=fixtures.NativeHandoffTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
  f=self.fixture;self.config=f.config;self.config['target']='owner'
  self.key=f.claim['nativeChatKey'];self.sid=f.claim['snapshotId']
  self.route={'accountId':self.config['accountId'],'nativeChatKey':self.key,'target':f.claim['target'],
       'sessionId':f.claim['sessionId'],'chatId':'canonical-group','status':'pending'}
  self.registry=f.data/'config/group-routes.local.json';self.save_registry()
  (f.data/'config/wxauto.local.json').write_text(json.dumps(self.config),encoding='utf8')
 def save_registry(self):self.registry.write_text(json.dumps({'version':1,'accountId':self.config['accountId'],'groups':[self.route]}),encoding='utf8')
 def proof(self,**kw):return m.verify_pending_text_admission_source(self.config,self.key,kw.get('claim_id',self.fixture.claim['claimId']),kw.get('claim_hash',self.fixture.claim['claimHash']))
 def args(self):return {'op':'catalog-page','nativeChatKey':self.key,'scanKey':'a'*64,'snapshotId':self.sid,
   'admissionProof':{'claimId':self.fixture.claim['claimId'],'claimHash':self.fixture.claim['claimHash']}}
 def test_real_source_proof_pending_catalog_exact_snapshot_and_no_writes(self):
  before={str(p):p.read_bytes()for p in self.fixture.data.rglob('*')if p.is_file()}
  proof=self.proof();self.assertEqual(proof.snapshot_id,self.sid);self.assertEqual(proof.target,self.route['target'])
  self.assertEqual(proof.catalog_arguments['native_group_chat_key'],self.key)
  changed=proof.queued;changed['state']='submitted';self.assertEqual(proof.queued['state'],'queued')
  value=io.request(self.fixture.data,self.args());self.assertEqual(len(value['messages']),2)
  self.assertEqual(value['admission'],{'claimId':proof.claim_id,'claimHash':proof.claim_hash,'snapshotId':self.sid,'verified':True})
  self.assertEqual(before,{str(p):p.read_bytes()for p in self.fixture.data.rglob('*')if p.is_file()})
 def test_claim_hash_account_key_and_pending_route_mismatch(self):
  with self.assertRaises(RuntimeError):self.proof(claim_id='b'*32)
  with self.assertRaises(RuntimeError):self.proof(claim_hash='b'*64)
  for field,value in [('target','wrong'),('sessionId','wrong'),('accountId','foreign')]:
   original=self.route[field];self.route[field]=value;self.save_registry()
   with self.assertRaises(RuntimeError):self.proof()
   self.route[field]=original;self.save_registry()
  with self.assertRaises(RuntimeError):m.verify_pending_text_admission_source(self.config,'wechat:other@chatroom',self.fixture.claim['claimId'],self.fixture.claim['claimHash'])
 def test_external_or_latest_snapshot_and_unproved_pending_are_denied(self):
  for args in ({k:v for k,v in self.args().items()if k!='snapshotId'},
     {**self.args(),'snapshotId':'22222222-2222-4222-8222-222222222222'},
     {**self.args(),'snapshotId':'../../other'},
     {k:v for k,v in self.args().items()if k!='admissionProof'}):
   with self.assertRaises(RuntimeError):io.request(self.fixture.data,args)
 def test_queued_becoming_submitted_during_validation_rejected(self):
  original=m.verify_queued_handoff
  def changed(*args,**kw):
   proof=original(*args,**kw);self.fixture.claim['state']='submitted';self.fixture.save();return proof
  with patch.object(m,'verify_queued_handoff',side_effect=changed):
   with self.assertRaisesRegex(RuntimeError,'source-changed'):self.proof()
 def test_tampered_unhashed_source_pointer_or_authenticated_source_mutation_rejected(self):
  original_hash=self.fixture.claim['claimHash']
  original_source=self.fixture.claim['messages'][0]['snapshotSourceId']
  self.fixture.claim['messages'][0]['snapshotSourceId']='["message/message_0.db","wrong",1]';self.fixture.save()
  self.assertEqual(original_hash,self.fixture.claim['claimHash'])
  with self.assertRaises(RuntimeError):self.proof()
  self.fixture.claim['messages'][0]['snapshotSourceId']=original_source;self.fixture.save()
  file=self.fixture.snapshot/'message/message_0.db';file.write_bytes(file.read_bytes()+b'changed')
  with self.assertRaises(RuntimeError):self.proof()
 def test_real_protocol_raw_vs_queued_presentation_is_authenticated_not_byte_identical(self):
  from vault.test_snapshot_import import SENDER
  self.fixture.raw_payloads(SENDER+':\nfirst actual body',SENDER+':\nlast actual body')
  value=io.request(self.fixture.data,self.args())
  self.assertTrue(value['admission']['verified'])
  self.assertNotEqual(value['messages'][0]['rawText'],self.fixture.claim['messages'][0]['text'])
  self.assertEqual(value['messages'][0]['renderedText'],'first actual body')
 def test_new_proof_after_receipt_addition_allowed_but_no_old_proof_relabel(self):
  proof=self.proof();self.fixture.claim['textDelegation']={'diagnostic':'fixture receipt only'};self.fixture.save()
  self.assertEqual(self.proof().claim_hash,proof.claim_hash)
  with self.assertRaisesRegex(RuntimeError,'source-changed'):m.revalidate_pending_text_admission_source(self.config,proof)
  with self.assertRaisesRegex(RuntimeError,'proof-invalid'):m.revalidate_pending_text_admission_source(self.config,object())
 def test_queue_changed_after_page_reader_never_returns_admission(self):
  from native_group_text_catalog import page_native_group_texts
  def reader(*args,**kw):
   value=page_native_group_texts(*args,**kw);self.fixture.claim['state']='submitted';self.fixture.save();return value
  with patch('native_group_text_catalog.page_native_group_texts',side_effect=reader):
   with self.assertRaises(RuntimeError):io.request(self.fixture.data,self.args())
 def test_active_without_proof_keeps_old_shape_and_does_not_require_queued_claim(self):
  self.route['status']='active';self.save_registry();self.fixture.file.unlink()
  args={k:v for k,v in self.args().items()if k!='admissionProof'}
  value=io.request(self.fixture.data,args);self.assertNotIn('admission',value)

if __name__=='__main__':unittest.main()
