"""Explicit local, one-shot social acceptance. Never part of worker IPC/tools."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import sys


def assert_worker_stopped():
    import psutil
    for process in psutil.process_iter(['pid', 'cmdline']):
        if process.info['pid'] == os.getpid():
            continue
        args = process.info.get('cmdline') or []
        if any(Path(arg).name.lower() == 'worker.py' for arg in args):
            raise RuntimeError('social-acceptance-stop-worker-first')


def main(argv=None):
    from social_actions import ACTIONS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--hash', required=True)
    parser.add_argument('--action', required=True, choices=sorted(ACTIONS))
    parser.add_argument('--fingerprint', required=True)
    parser.add_argument('--confirm-local-execution', action='store_true', required=True,
                        help='Actually perform this one fixed plan; not a dry run.')
    args = parser.parse_args(argv)
    if not re.fullmatch('[0-9a-f]{32}', args.plan) or any(
            not re.fullmatch('[0-9a-f]{64}', value) for value in (args.hash, args.fingerprint)):
        raise RuntimeError('social-acceptance-invalid-identifiers')
    assert_worker_stopped()
    config = json.loads(Path(args.config).read_text('utf-8-sig'))
    from social_wxauto_backend import WxautoSocialBackend, load_observed_bindings, run_local_acceptance
    # Validate binding and plan before constructing wxauto, which may activate UI.
    bindings = load_observed_bindings(None, config)
    binding = bindings.get(args.action)
    if binding is None or binding.fingerprint() != args.fingerprint:
        raise RuntimeError('social-acceptance-binding-mismatch')
    from social_actions import ActionContext, REQUIRES_APPROVAL, SocialActions
    queue = SocialActions(config['dataRoot'], owner_target=config['target'], allowed_groups=config.get('groups', []))
    plan = queue.inspect(args.plan, ActionContext('friend', config['target'], True))
    if plan['hash'] != args.hash or plan['action'] != args.action:
        raise RuntimeError('social-acceptance-plan-mismatch')
    if plan['status'] not in ('ready', 'approved') or (args.action in REQUIRES_APPROVAL
            and (plan.get('approvedBy') != config['target'] or not plan.get('approvalMessageId'))):
        raise RuntimeError('social-acceptance-plan-not-authorized')
    with contextlib.redirect_stdout(sys.stderr):
        from wxauto_connection import connect
        wx = connect()
        backend = WxautoSocialBackend(wx, config, bindings=load_observed_bindings(wx, config))
        result = run_local_acceptance(backend, plan_id=args.plan, expected_hash=args.hash,
                                      action=args.action, fingerprint=args.fingerprint)
    # Deliberately omit parameters, old announcement, and control text from stdout.
    print(json.dumps({key: result[key] for key in ('id', 'action', 'status', 'resultCode', 'executionStatus')
                      if key in result}, ensure_ascii=False))
    return 0 if result['status'] == 'confirmed' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        code = str(error) if isinstance(error, RuntimeError) and str(error).startswith('social-') else type(error).__name__
        print(json.dumps({'status': 'not-confirmed', 'code': code}), file=sys.stderr)
        raise SystemExit(2)
