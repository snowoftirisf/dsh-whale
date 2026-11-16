import unittest
from unittest.mock import patch
from delivery_window import DeliveryWindow


class DeliveryTests(unittest.TestCase):
    def test_only_the_owner_can_send_or_release_and_other_replies_wait(self):
        window = DeliveryWindow()
        window.begin('reply-A', 'group-A')
        self.assertEqual(window.active(), ('reply-A', 'group-A'))
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            window.begin('reply-B', 'group-B')
        with self.assertRaisesRegex(RuntimeError, 'mismatch'):
            window.check('reply-A', 'group-B')
        with self.assertRaisesRegex(RuntimeError, 'mismatch'):
            window.end('reply-B', 'group-B')
        window.check('reply-A', 'group-A')
        window.end('reply-A', 'group-A')
        self.assertIsNone(window.active())
        window.begin('reply-B', 'group-B')

    def test_abandoned_reply_expires_and_does_not_permanently_pause_receiving(self):
        with patch('delivery_window.time.monotonic') as clock:
            clock.return_value = 0
            window = DeliveryWindow()
            window.begin('reply-A', 'group-A')
            clock.return_value = 119
            window.check('reply-A', 'group-A')
            clock.return_value = 121
            self.assertIsNotNone(window.active())
            clock.return_value = 240
            self.assertIsNone(window.active())
            with self.assertRaisesRegex(RuntimeError, 'mismatch'):
                window.check('reply-A', 'group-A')


if __name__ == '__main__':
    unittest.main()
