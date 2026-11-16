import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from .capture import SyncLock
from .capture_worker import CandidateWorker,serve
from .store import Vault,VaultError,atomic_json
from .test_capture import FakeCollector


class CandidateWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.base=Path(self.temp.name)
        self.source=self.base/'db_storage';self.source.mkdir()
        self.fake=FakeCollector(self.source);self.worker=CandidateWorker(self.base/'data',collector=self.fake)
        self.args={'db_root':str(self.source),'source_id':'fixture-source','account_id':'trusted-account'}
    def tearDown(self):self.worker.abort();self.temp.cleanup()

    def collect(self):
        response=self.worker.request({'id':'collect-1','op':'collect','args':self.args})
        self.assertTrue(response['ok'],response)
        return response['value']

    def test_collect_holds_lock_and_never_opens_or_writes_main_database(self):
        with patch('vault.capture.Vault',side_effect=AssertionError('collector cannot open writer')):
            candidate=self.collect()
        self.assertEqual(candidate['cursorSource'],'wechat-snapshot:trusted-account')
        self.assertFalse((self.worker.root/'vault/whale.sqlite').exists())
        self.assertFalse((self.worker.output/'latest.json').exists())
        with self.assertRaisesRegex(VaultError,'sync-busy'):
            with SyncLock(self.worker.root):pass

    def test_commit_requires_exact_job_and_successful_import_then_publishes(self):
        candidate=self.collect();receipt=candidate['captureReceipt']
        result=self.worker.request({'id':'commit-1','op':'commit','args':{'jobId':candidate['jobId'],
            'importResult':{'complete':True,'snapshot_id':receipt['snapshot_id'],'account_id':'trusted-account','scanned':2}}})
        self.assertTrue(result['ok'],result);self.assertTrue(result['value']['committed'])
        latest=json.loads((self.worker.output/'latest.json').read_text(encoding='utf8'))
        self.assertEqual(latest['sync_job_id'],candidate['jobId'])
        with SyncLock(self.worker.root):pass
        self.assertFalse((self.worker.root/'vault/whale.sqlite').exists())

    def test_collect_failure_keeps_old_latest_and_releases_lock(self):
        self.worker.output.mkdir(parents=True);old={'snapshot_id':'old'};atomic_json(self.worker.output/'latest.json',old)
        self.fake.fail=True
        result=self.worker.request({'id':'one','op':'collect','args':self.args})
        self.assertFalse(result['ok']);self.assertEqual(json.loads((self.worker.output/'latest.json').read_text()),old)
        with SyncLock(self.worker.root):pass

    def test_abort_after_import_failure_preserves_old_latest(self):
        self.worker.output.mkdir(parents=True);old={'snapshot_id':'old'};atomic_json(self.worker.output/'latest.json',old)
        candidate=self.collect()
        result=self.worker.request({'op':'abort','args':{'jobId':candidate['jobId']}})
        self.assertTrue(result['ok']);self.assertTrue(result['value']['aborted'])
        self.assertEqual(json.loads((self.worker.output/'latest.json').read_text()),old)
        with SyncLock(self.worker.root):pass

    def test_eof_after_candidate_never_publishes_and_releases_lock(self):
        lines=io.StringIO(json.dumps({'id':'one','op':'collect','args':self.args})+'\n');responses=[]
        serve(lines,responses.append,self.worker)
        self.assertTrue(responses[0]['ok']);self.assertFalse((self.worker.output/'latest.json').exists())
        with SyncLock(self.worker.root):pass

    def test_wrong_commit_or_reentrant_request_aborts_candidate(self):
        candidate=self.collect()
        response=self.worker.request({'op':'commit','args':{'jobId':'wrong','importResult':{'complete':True}}})
        self.assertFalse(response['ok']);self.assertEqual(response['error'],'candidate-job-mismatch')
        self.assertFalse((self.worker.output/'latest.json').exists())
        candidate=self.collect()
        response=self.worker.request({'op':'collect','args':self.args})
        self.assertFalse(response['ok']);self.assertEqual(response['error'],'candidate-already-pending')
        with SyncLock(self.worker.root):pass


if __name__=='__main__':unittest.main()
