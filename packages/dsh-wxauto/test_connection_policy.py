import unittest
from connection_policy import connection_failure, error_diagnostic, ChatReadBackoff
from unittest.mock import patch

class ConnectionPolicyTests(unittest.TestCase):
    def test_read_backoff_is_per_chat_capped_and_never_permanently_pauses(self):
        with patch('connection_policy.time.monotonic') as clock:
            clock.return_value=0
            retry=ChatReadBackoff()
            self.assertEqual(retry.failed('A')['retryAfterSeconds'],10)
            self.assertFalse(retry.due('A'));self.assertTrue(retry.due('B'))
            self.assertFalse(retry.recovered('B'))
            clock.return_value=10
            self.assertTrue(retry.due('A'))
            self.assertEqual(retry.failed('A')['retryAfterSeconds'],20)
            clock.return_value=30
            self.assertEqual(retry.failed('A')['retryAfterSeconds'],30)
            clock.return_value=60
            self.assertTrue(retry.due('A'))
            self.assertEqual(retry.failed('A')['retryAfterSeconds'],30)
            self.assertTrue(retry.recovered('A'));self.assertTrue(retry.due('A'))

    def test_diagnostic_keeps_call_site_without_exception_text_or_locals(self):
        try:
            raise AttributeError('private chat body')
        except AttributeError as error:
            result=error_diagnostic(error,'read-chat')
        self.assertEqual(result['errorType'],'AttributeError')
        self.assertEqual(result['phase'],'read-chat')
        self.assertTrue(result['frames'])
        self.assertNotIn('private chat body',str(result))

    def test_control_failure_is_not_reported_as_logged_out(self):
        result = connection_failure(LookupError('Find Control Timeout'), 'initialize')
        self.assertEqual(result['code'], 'wechat-control-not-found')
        self.assertEqual(result['event'], 'connection-blocked')
        self.assertNotIn('message', result)

    def test_wrong_target_cannot_proceed(self):
        self.assertEqual(connection_failure(RuntimeError('target-mismatch'), 'select-target')['code'], 'target-mismatch')

    def test_unknown_exception_is_not_assumed_to_be_login_failure(self):
        self.assertEqual(connection_failure(Exception('private diagnostic'), 'initialize')['code'], 'initialization-failed')

if __name__ == '__main__':
    unittest.main()
