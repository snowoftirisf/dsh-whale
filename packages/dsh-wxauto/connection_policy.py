"""Classify wxauto failures without triggering any UI operations."""
from pathlib import Path
import traceback
import time


class ChatReadBackoff:
    """Independent, bounded retry delays; no chat is permanently paused."""
    def __init__(self):
        self.states = {}

    def due(self, target):
        return time.monotonic() >= self.states.get(target, {}).get('after', 0)

    def failed(self, target):
        count = self.states.get(target, {}).get('failures', 0) + 1
        delay = min(count * 10, 30)
        self.states[target] = {'failures': count, 'after': time.monotonic() + delay}
        return {'failures': count, 'retryAfterSeconds': delay}

    def recovered(self, target):
        return self.states.pop(target, None) is not None


def error_diagnostic(error, phase):
    """Return call sites and attribute names without chat text or exception locals."""
    return {'errorType': type(error).__name__, 'phase': phase,
            'attribute': getattr(error, 'name', None),
            'frames': [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(error.__traceback__)[-6:]]}
def connection_failure(error, phase):
    if isinstance(error, LookupError):
        return {'event': 'connection-blocked', 'code': 'wechat-control-not-found', 'phase': phase}
    if isinstance(error, RuntimeError) and str(error) == 'target-mismatch':
        return {'event': 'connection-blocked', 'code': 'target-mismatch', 'phase': phase}
    return {'event': 'connection-blocked', 'code': 'initialization-failed', 'phase': phase}
