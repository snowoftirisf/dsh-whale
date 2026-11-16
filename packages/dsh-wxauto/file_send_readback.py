"""Passive file-row evidence for a void SDK return, not recipient delivery proof.

No SDK message parsing, sender lookup, input, activation, scrolling or thread.
The filename reader and class binding require observed client-specific evidence;
this module deliberately has no guessed production selector.
"""
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from message_direction import read_message_direction


class FileReadbackError(RuntimeError):
    pass


FILE_ROW_CLASSES = ('mmui::ChatBubbleItemView',)


def observed_file_basename(row):
    """Binding observed in file-row-readonly-9bc44... on client 4.2.

    The generic bubble class alone is insufficient. Only the complete observed
    file label, four-line structure and childless ListItem qualify.
    """
    if (row.ClassName != FILE_ROW_CLASSES[0] or row.ControlTypeName != 'ListItemControl'
            or row.GetChildren() != []):
        return None
    name = row.Name
    if not isinstance(name, str):
        return None
    parts = name.split('\n')
    if len(parts) != 4 or parts[0] != '文件' or parts[3] != '微信电脑版':
        return None
    basename, size = parts[1:3]
    if (not basename or basename != basename.strip() or len(basename) > 255
            or any(c in basename for c in ('/', '\\', '\x00', '\r'))
            or not re.fullmatch(r'(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?(?:B|KB|MB|GB)', size)):
        return None
    return basename


def _rid(control):
    value = control.GetRuntimeId()
    if not isinstance(value, (list, tuple)) or not value or any(type(x) is not int for x in value):
        raise FileReadbackError('file-readback-runtime-id-unavailable')
    return tuple(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class FileSendOutcome:
    sdk_result: object
    rendered_evidence: dict | None = None


class FileReadback:
    def __init__(self, wx, target, kind, filename, *, file_row_classes, filename_reader,
                 direction_reader=None, clock=time.monotonic, sleep=time.sleep):
        if (not isinstance(target, str) or not target or kind not in ('friend', 'group')
                or not isinstance(filename, str) or not filename or Path(filename).name != filename
                or '/' in filename or '\\' in filename
                or not isinstance(file_row_classes, (tuple, frozenset)) or not file_row_classes
                or any(not isinstance(x, str) or not x for x in file_row_classes)
                or not callable(filename_reader)):
            raise FileReadbackError('file-readback-binding-unavailable')
        self.wx, self.target, self.kind, self.filename = wx, target, kind, filename
        self.classes, self.filename_reader = frozenset(file_row_classes), filename_reader
        self.direction, self.clock, self.sleep = direction_reader or read_message_direction, clock, sleep
        try:
            self.root = wx._api.control
            self.box = wx._api._chat_api.msgbox
            self.identity = self._state()
            rows = self._rows()
            # All pre-existing row IDs are excluded, including virtualized reuse.
            self.before_ids = frozenset(_rid(row) for row in rows)
            self.before_matching = frozenset(_rid(row) for row in rows if self._filename(row) == filename)
            self._check()
        except FileReadbackError:
            raise
        except Exception:
            raise FileReadbackError('file-readback-preflight-unavailable') from None

    def _state(self):
        info = self.wx.ChatInfo()
        if (not isinstance(info, dict) or info.get('chat_name') != self.target
                or info.get('chat_type') != self.kind):
            raise FileReadbackError('file-readback-target-mismatch')
        root = self.wx._api.control
        box = self.wx._api._chat_api.msgbox
        pid, hwnd = root.ProcessId, root.NativeWindowHandle
        if (type(pid) is not int or pid <= 0 or type(hwnd) is not int or hwnd <= 0
                or box.ProcessId != pid or box.IsOffscreen not in (0, False)):
            raise FileReadbackError('file-readback-viewport-unavailable')
        return (_rid(root), pid, hwnd, _rid(box))

    def _check(self):
        if self._state() != self.identity:
            raise FileReadbackError('file-readback-viewport-changed')

    def _rows(self):
        rows = self.box.GetChildren()
        if not isinstance(rows, (list, tuple)) or len(rows) > 4000:
            raise FileReadbackError('file-readback-row-budget')
        return rows

    def _filename(self, row):
        if row.ClassName not in self.classes:
            return None
        value = self.filename_reader(row)
        if value is not None and not isinstance(value, str):
            raise FileReadbackError('file-readback-filename-unavailable')
        return value

    def confirm(self, *, timeout=3.0, interval=.15):
        """Return stable unique self-row evidence, or None. Never sends/retries."""
        if type(timeout) not in (int, float) or not 0 < timeout <= 8:
            raise FileReadbackError('file-readback-timeout-invalid')
        deadline, previous = self.clock() + timeout, None
        try:
            while self.clock() < deadline:
                self._check()
                candidates = []
                for row in self._rows():
                    rid = _rid(row)
                    if rid in self.before_ids or self._filename(row) != self.filename:
                        continue
                    if self.direction(row, self.root, msgbox=self.box) == 'self':
                        candidates.append((rid, row.ClassName))
                self._check()
                if len(candidates) > 1:
                    return None
                current = candidates[0] if candidates else None
                if current is not None and current == previous:
                    return {'kind': 'rendered-file-row', 'direction': 'self',
                        'rowRuntimeIdSha256': _digest(current[0]), 'rowClass': current[1],
                        'filenameSha256': hashlib.sha256(self.filename.encode('utf8')).hexdigest(),
                        'filenameLength': len(self.filename), 'stableObservations': 2,
                        'recipientBytesVerified': False}
                previous = current
                self.sleep(interval)
        except Exception:
            # Readback cannot convert an already attempted send into a retry.
            return None
        return None


def send_file_once(wx, filepath, *, target, kind, file_row_classes, filename_reader,
                   direction_reader=read_message_direction, clock=time.monotonic, sleep=time.sleep,
                   timeout=3.0):
    """Preflight before exactly one SendFiles; only None may use passive readback.

    The caller must apply attachment policy and claim/reply transaction first.
    An explicit SDK false/failure is preserved even if a row is visible.
    """
    observation = None
    try:
        observation = FileReadback(wx, target, kind, Path(filepath).name,
            file_row_classes=file_row_classes, filename_reader=filename_reader,
            direction_reader=direction_reader, clock=clock, sleep=sleep)
    except FileReadbackError as error:
        if str(error) == 'file-readback-target-mismatch':
            raise
    result = wx.SendFiles(str(filepath))
    evidence = observation.confirm(timeout=timeout) if result is None and observation is not None else None
    return FileSendOutcome(result, evidence)
