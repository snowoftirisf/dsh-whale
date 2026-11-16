"""Actual candidate worker.main JSONL boundary, fake wxauto only."""
import unittest
from unittest.mock import patch
import worker_candidate
import dynamic_group_watch_candidate  # Load real helpers before SDK boundary fakes.
import test_worker_social as fixtures


class NewGroupWorkerMainTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkerSocialTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def run_worker(self, *args, **kwargs):
        with patch.object(fixtures, 'worker', worker_candidate):
            return self.f.run_worker(*args, **kwargs)

    def test_original_private_and_group_polling_and_heartbeat_are_preserved(self):
        events, operations, _ = self.run_worker([None,None], clock_step=11, inbound={
            'owner':[{'id':'owner-test','text':'private fixture','sender':'owner'}],
            'group-a':[{'id':'group-test','text':'group fixture','sender':'member'}]})
        messages = [e for e in events if e.get('event')=='message']
        self.assertEqual({e['text'] for e in messages}, {'private fixture','group fixture'})
        self.assertTrue(any(e.get('event')=='heartbeat' for e in events))
        generation=next(e['groupTextWorkerInstanceId']for e in events if e.get('event')=='ready')
        self.assertRegex(generation,r'^[a-f0-9]{32}$')
        self.assertTrue(all(e['groupTextWorkerInstanceId']==generation for e in events if e.get('event')=='heartbeat'))
        self.assertFalse(any(op[0]=='send' for op in operations))

    def test_unverified_native_restore_and_mixed_capabilities_never_navigate(self):
        self.f.config.update(autoGroups={'enabled':True}, accountId='fixture-account')
        base={'op':'auto-group-register','id':'native-restore','target':'new group',
              'accountId':'fixture-account','nativeChatKey':'wechat:123@chatroom',
              'nativeTextAuthorityEpoch':'a'*32}
        for command in (base, {**base,'nativeClaimId':'b'*32}):
            events, operations, _ = self.run_worker([command])
            results=[e for e in events if e.get('event')=='group-route-result']
            self.assertEqual(len(results),1)
            self.assertFalse(results[0]['ok'])
            self.assertEqual(results[0]['code'],'dynamic-group-native-monitor-binding-unverified')
            self.assertFalse(any(op[0] in ('send','select') for op in operations))

    def test_private_admit_without_trusted_registry_is_denied_by_real_main(self):
        command={'op':'group-text-admit','id':'admit','target':'new group',
            'nativeChatKey':'wechat:123@chatroom','claimId':'a'*32,'claimHash':'b'*64,'authorityEpoch':'c'*32}
        events, operations, _ = self.run_worker([command])
        result=next(e for e in events if e.get('event')=='group-text-result')
        self.assertFalse(result['ok'])
        self.assertFalse(any(op[0] in ('send','select') for op in operations))


if __name__=='__main__': unittest.main()
