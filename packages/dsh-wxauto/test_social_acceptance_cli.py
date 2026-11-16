"""CLI admission checks only: never connect to WeChat."""
import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import social_acceptance_cli as cli


class AcceptanceCliTests(unittest.TestCase):
    def test_explicit_local_confirmation_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['--config', 'unused', '--plan', 'a' * 32, '--hash', 'b' * 64,
                      '--action', 'publish_moment', '--fingerprint', 'c' * 64])

    def test_invalid_identifiers_never_probe_processes_or_connect(self):
        with patch.object(cli, 'assert_worker_stopped') as stopped:
            with self.assertRaisesRegex(RuntimeError, 'invalid-identifiers'):
                cli.main(['--config', 'unused', '--plan', 'bad', '--hash', 'b' * 64,
                          '--action', 'publish_moment', '--fingerprint', 'c' * 64,
                          '--confirm-local-execution'])
            stopped.assert_not_called()

    def test_running_worker_blocks_local_acceptance(self):
        process = SimpleNamespace(info={'pid': 123, 'cmdline': ['python.exe', 'C:/local/worker.py', 'config.json']})
        with patch('psutil.process_iter', return_value=[process]), patch('os.getpid', return_value=999):
            with self.assertRaisesRegex(RuntimeError, 'stop-worker-first'):
                cli.assert_worker_stopped()


if __name__ == '__main__':
    unittest.main()
