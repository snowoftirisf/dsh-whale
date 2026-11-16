"""One-shot manual acceptance using producer + sole queued vault writer.

Run only with Harness/sync stopped. Does not enable capture or merge chats.
No source message bodies or process memory are included in the report.
"""
import argparse
import json
import queue
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path


class Channel:
    def __init__(self, module, root):
        command = [sys.executable, '-u', '-X', 'utf8', '-m', module, '--root', str(root)]
        if module == 'vault.cli':
            command.append('worker')
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding='utf8',
                                        cwd=Path(__file__).resolve().parent.parent)
        self.lines = queue.Queue()
        def read():
            for line in self.process.stdout:
                self.lines.put(line)
            self.lines.put(None)
        threading.Thread(target=read, daemon=True).start()
        self.sequence = 0

    def request(self, op, args=None):
        self.sequence += 1
        self.process.stdin.write(json.dumps({'id': self.sequence, 'op': op, 'args': args or {}}) + '\n')
        self.process.stdin.flush()
        line = self.lines.get(timeout=420)
        if line is None:
            raise RuntimeError('worker-eof')
        response = json.loads(line)
        if response.get('id') != self.sequence:
            raise RuntimeError('worker-response-id-mismatch')
        if not response.get('ok'):
            raise RuntimeError(response.get('error', 'worker-failed'))
        return response['value']

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()  # Dedicated child only, never WeChat.
            self.process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--source-id', required=True)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--client-version', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    root = Path(args.root)
    config = json.loads((root / 'config' / 'wxauto.local.json').read_text(encoding='utf-8-sig'))
    report = {'at': datetime.now(timezone.utc).isoformat(), 'client_version': args.client_version,
              'source_id': args.source_id, 'account_id': config['accountId'],
              'automatic_capture_enabled': config.get('capture', {}).get('enabled', False),
              'account_username_verified': False, 'phase': 'starting', 'complete': False}
    writer = producer = None
    old_cursor = None
    candidate = None
    import_started = committed = False
    try:
        producer = Channel('vault.capture_worker', root)
        report['phase'] = 'collect'
        candidate = producer.request('collect', {'db_root': config['capture']['dbRoot'],
            'source_id': args.source_id, 'account_id': config['accountId'], 'pids': [args.pid],
            'seconds': 90, 'scope': 'chat'})
        report['candidate_path'] = candidate['candidatePath']
        report['capture_receipt'] = candidate['captureReceipt']
        report['cursor_source'] = candidate['cursorSource']
        writer = Channel('vault.cli', root)
        before = writer.request('status')
        old_cursor = before['cursors'].get(candidate['cursorSource'])
        report['phase'] = 'import'
        import_started = True
        result = writer.request('import_snapshot', {'snapshotPath': candidate['candidatePath'],
                                                   'account_id': candidate['accountId']})
        report['import'] = result
        report['phase'] = 'commit'
        report['commit'] = producer.request('commit', {'jobId': candidate['jobId'], 'importResult': result})
        committed = True
        report['doctor'] = writer.request('doctor')
        report['phase'] = 'complete'
        report['complete'] = True
    except Exception as exc:
        report['error'] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        if import_started and not committed and writer:
            try:
                if old_cursor is None:
                    writer.request('clear_cursor', {'source': candidate['cursorSource']})
                else:
                    writer.request('set_cursor', {'source': candidate['cursorSource'], 'cursor': old_cursor})
                report['cursor_rollback'] = True
            except Exception:
                report['cursor_rollback'] = False
        if producer and not committed:
            try:
                producer.request('abort')
            except Exception:
                pass
    finally:
        for channel in (producer, writer):
            if channel:
                channel.close()
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    # Explicit safe summary: detailed report contains identifiers, never text.
    print(json.dumps({k: report[k] for k in ('phase', 'complete', 'error') if k in report}))
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
