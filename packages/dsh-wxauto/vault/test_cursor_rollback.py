import tempfile
import unittest
from .store import Vault


class CursorRollbackTests(unittest.TestCase):
    def test_initial_sync_failure_can_restore_absent_cursor_without_affecting_other_source(self):
        with tempfile.TemporaryDirectory() as root:
            v = Vault(root)
            try:
                v.set_cursor('other', {'row': 3})
                v.set_cursor('attempted-source', {'snapshot': 'candidate'})
                v.request('clear_cursor', {'source': 'attempted-source'})
                self.assertEqual(v.status()['cursors'], {'other': {'row': 3}})
                v.request('clear_cursor', {'source': 'attempted-source'})
                self.assertEqual(v.status()['cursors'], {'other': {'row': 3}})
            finally:
                v.close()
