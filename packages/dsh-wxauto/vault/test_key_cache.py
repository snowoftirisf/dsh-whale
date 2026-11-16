import tempfile, unittest
from pathlib import Path
from vault import capture


class KeyCacheTest(unittest.TestCase):
    def setUp(self):
        capture._KEY_CACHE.clear()
        self.dir = tempfile.TemporaryDirectory()
        self.db = Path(self.dir.name) / 'message_0.db'
        self.db.write_bytes(bytes(range(16)) + b'\x01' * (4096 - 16))
        self.salt = bytes(range(16)).hex()
        self.calls = 0

    def tearDown(self):
        self.dir.cleanup(); capture._KEY_CACHE.clear()

    def extract(self, files, pids, seconds):
        self.calls += 1
        return {self.salt: 'aa' * 32}, [{'pid': 1}]

    def test_second_capture_reuses_a_verified_key_without_scanning(self):
        wrapped = capture._cached_key_extractor(self.extract, lambda key, page: key == bytes.fromhex('aa' * 32))
        self.assertEqual(wrapped([self.db], [1])[0], {self.salt: 'aa' * 32})
        keys, stats = wrapped([self.db], [1])
        self.assertEqual(keys, {self.salt: 'aa' * 32})
        self.assertTrue(stats[0]['cached'])
        self.assertEqual(self.calls, 1)

    def test_key_that_no_longer_verifies_triggers_a_fresh_scan(self):
        ok = [True]
        wrapped = capture._cached_key_extractor(self.extract, lambda key, page: ok[0])
        wrapped([self.db], [1]); ok[0] = False
        wrapped([self.db], [1])
        self.assertEqual(self.calls, 2)


class SecureOnceTest(unittest.TestCase):
    def test_vault_acl_is_applied_once_per_process_while_the_directory_exists(self):
        capture._SECURED.clear()
        with tempfile.TemporaryDirectory() as root:
            calls = []
            secure = capture._secure_once(lambda p: (calls.append(p), Path(p))[1])
            self.assertEqual(secure(Path(root)), Path(root))
            secure(Path(root)); secure(Path(root))
            self.assertEqual(len(calls), 1)
        secure(Path(root))  # directory gone -> secured again
        self.assertEqual(len(calls), 2)
        capture._SECURED.clear()


if __name__ == '__main__':
    unittest.main()
