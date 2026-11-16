"""Fake controls only: no installed SDK connection, screenshot or send."""
import unittest
from types import SimpleNamespace
from file_send_readback import send_file_once, FileReadbackError, observed_file_basename


class Row:
    def __init__(self, rid, name='fixture.txt', direction='self'):
        self.rid, self.filename, self.direction = rid, name, direction
        self.ClassName = 'fixture-observed-file-class'
    def GetRuntimeId(self): return [self.rid]


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.rows = [Row(1)]
        self.now = 0
        self.name, self.kind = 'fixture-owner', 'friend'
        self.calls = 0
        self.after_send = lambda: self.rows.append(Row(2))
        self.result = None
        self.root = SimpleNamespace(GetRuntimeId=lambda: [10], ProcessId=100, NativeWindowHandle=20)
        self.box = SimpleNamespace(GetRuntimeId=lambda: [11], ProcessId=100, IsOffscreen=0,
                                   GetChildren=lambda: list(self.rows))
        def send(path):
            self.calls += 1
            self.after_send()
            return self.result
        self.wx = SimpleNamespace(_api=SimpleNamespace(control=self.root, _chat_api=SimpleNamespace(msgbox=self.box)),
            ChatInfo=lambda: {'chat_name': self.name, 'chat_type': self.kind}, SendFiles=send)

    def run_send(self):
        def sleep(seconds): self.now += seconds
        return send_file_once(self.wx, 'fixture.txt', target='fixture-owner', kind='friend',
            file_row_classes=('fixture-observed-file-class',), filename_reader=lambda row: row.filename,
            direction_reader=lambda row, root, **kw: row.direction,
            clock=lambda: self.now, sleep=sleep, timeout=.5)

    def test_none_requires_new_stable_self_row_and_returns_redacted_local_evidence(self):
        result = self.run_send()
        self.assertEqual(self.calls, 1)
        self.assertIsNone(result.sdk_result)
        self.assertEqual(result.rendered_evidence['kind'], 'rendered-file-row')
        self.assertEqual(result.rendered_evidence['stableObservations'], 2)
        self.assertFalse(result.rendered_evidence['recipientBytesVerified'])
        self.assertNotIn('fixture.txt', str(result.rendered_evidence))

    def test_observed_four_line_binding_requires_complete_file_structure(self):
        row = SimpleNamespace(ClassName='mmui::ChatBubbleItemView', ControlTypeName='ListItemControl',
            Name='文件\nfixture.txt\n6B\n微信电脑版', GetChildren=lambda: [])
        self.assertEqual(observed_file_basename(row), 'fixture.txt')
        for invalid in ('文件\nfixture.txt\n6B\n微信电脑版\nextra',
                        '文件\nfixture.txt\nnot-a-size\n微信电脑版',
                        '文本\nfixture.txt\n6B\n微信电脑版'):
            row.Name = invalid
            self.assertIsNone(observed_file_basename(row))
        row.Name = '文件\nfixture.txt\n6B\n微信电脑版'
        row.GetChildren = lambda: [object()]
        self.assertIsNone(observed_file_basename(row))

    def test_old_same_name_or_rid_reuse_is_not_new_send_evidence(self):
        self.after_send = lambda: self.rows.__setitem__(0, Row(1))
        self.assertIsNone(self.run_send().rendered_evidence)
        self.assertEqual(self.calls, 1)

    def test_full_name_and_self_direction_are_required(self):
        for row in (Row(2, 'fixture.txt.partial'), Row(2, 'fixture.txt', 'friend')):
            with self.subTest(name=row.filename, direction=row.direction):
                self.rows = [Row(1)]
                self.after_send = lambda row=row: self.rows.append(row)
                self.assertIsNone(self.run_send().rendered_evidence)

    def test_two_matching_new_rows_are_ambiguous(self):
        self.after_send = lambda: self.rows.extend([Row(2), Row(3)])
        self.assertIsNone(self.run_send().rendered_evidence)
        self.assertEqual(self.calls, 1)

    def test_target_or_viewport_change_stays_unknown_after_single_send(self):
        for mutate in (lambda: setattr(self, 'name', 'different-chat'),
                       lambda: setattr(self.box, 'GetRuntimeId', lambda: [99])):
            with self.subTest(mutation=mutate):
                self.setUp()
                self.after_send = mutate
                self.assertIsNone(self.run_send().rendered_evidence)
                self.assertEqual(self.calls, 1)

    def test_explicit_false_sdk_result_is_never_overridden(self):
        self.result = False
        result = self.run_send()
        self.assertIs(result.sdk_result, False)
        self.assertIsNone(result.rendered_evidence)
        self.assertEqual(self.calls, 1)

    def test_preflight_wrong_target_prevents_send(self):
        self.name = 'different-chat'
        with self.assertRaisesRegex(FileReadbackError, 'target-mismatch'):
            self.run_send()
        self.assertEqual(self.calls, 0)

    def test_unavailable_passive_viewport_preserves_explicit_sdk_success(self):
        self.box.IsOffscreen = 1
        self.result = True
        result = self.run_send()
        self.assertIs(result.sdk_result, True)
        self.assertIsNone(result.rendered_evidence)
        self.assertEqual(self.calls, 1)

    def test_unstable_or_unreadable_direction_does_not_retry(self):
        original = self.box.GetChildren
        def children():
            if self.calls: self.rows = [Row(1), Row(2 + int(self.now * 100))]
            return original()
        self.box.GetChildren = children
        self.assertIsNone(self.run_send().rendered_evidence)
        self.assertEqual(self.calls, 1)


if __name__ == '__main__': unittest.main()
