"""Offline only: synthetic guard tests plus recorded UI structure replay."""
import json
from pathlib import Path
import unittest
from social_moments_privacy import (private_option_candidate, read_private_summary,
    private_layer_controls, public_option_candidate, read_visibility_summary)


class Control:
    def __init__(self, cls, name='', typ='GroupControl', children=(), rid=(42, 1)):
        self.ClassName, self.Name, self.ControlTypeName = cls, name, typ
        self.children, self.rid = list(children), rid
        self.AutomationId = ''
    def GetChildren(self): return self.children
    def GetRuntimeId(self): return self.rid
    def Click(self, **kwargs): raise AssertionError('reader must never click')


def text(value): return Control('mmui::XTextView', value, 'TextControl')
def option():
    return Control('mmui::PublishPrivacySelection', '私密', 'RadioButtonControl',
                   [text('私密'), text('仅自己可见')])
def panel(children): return Control('mmui::SnsPublishPanel', '朋友圈', 'WindowControl', children)


class PrivacyTests(unittest.TestCase):
    def test_restore_public_exact_option_and_summary(self):
        public = Control('mmui::PublishPrivacySelection', '公开', 'RadioButtonControl',
                         [text('公开'), text('所有朋友可见')])
        p = panel([Control('mmui::XLayerWindow', 'Weixin', 'WindowControl', [public])])
        self.assertIs(public_option_candidate(p, expected_panel_runtime_id=[42, 1]), public)
        public.children[-1].Name = '仅自己可见'
        with self.assertRaises(RuntimeError): public_option_candidate(p, expected_panel_runtime_id=[42, 1])
        p.children = [Control('mmui::PublishPrivacyView', children=[text('谁可以看'), text('公开')])]
        result = read_visibility_summary(p, expected_panel_runtime_id=[42, 1], expected_textSequence=['谁可以看', '公开'])
        self.assertFalse(result['privateSummaryObserved'])
        self.assertEqual(result['visibilitySummaryObserved'], '公开')
        with self.assertRaises(RuntimeError): read_private_summary(p, expected_panel_runtime_id=[42, 1])
        for expected in (['谁可以看', '部分可见'], ['公开'], None, '公开'):
            with self.assertRaisesRegex(RuntimeError, 'expectation-unsupported'):
                read_visibility_summary(p, expected_panel_runtime_id=[42, 1], expected_textSequence=expected)

    def test_exact_option_is_only_candidate(self):
        private = option()
        p = panel([Control('mmui::XLayerWindow', 'Weixin', 'WindowControl', [private])])
        self.assertIs(private_option_candidate(p, expected_panel_runtime_id=[42, 1]), private)
        with self.assertRaisesRegex(RuntimeError, 'layer-still-open'):
            read_private_summary(p, expected_panel_runtime_id=[42, 1])

    def test_missing_duplicate_and_misleading_labels(self):
        for options in ([], [option(), option()], [option()]):
            if len(options) == 1: options[0].children[-1].Name = '所有朋友可见'
            p = panel([Control('mmui::XLayerWindow', 'Weixin', 'WindowControl', options)])
            with self.assertRaisesRegex(RuntimeError, 'option-unverified'):
                private_option_candidate(p, expected_panel_runtime_id=[42, 1])

    def test_layer_buttons_never_resolve_composer_cancel(self):
        cancel = Control('mmui::XOutlineButton', '取消', 'ButtonControl')
        confirm = Control('mmui::XOutlineButton', '确定', 'ButtonControl')
        layer = Control('mmui::XLayerWindow', 'Weixin', 'WindowControl', [cancel, confirm])
        p = panel([layer, Control('mmui::XOutlineButton', '取消', 'ButtonControl'),
                   Control('mmui::XOutlineButton', '发表', 'ButtonControl')])
        result = private_layer_controls(p, expected_panel_runtime_id=[42, 1])
        self.assertIs(result['cancel'], cancel)
        self.assertIs(result['confirm'], confirm)
        for children in ([confirm], [cancel, confirm, confirm],
                         [cancel, Control('mmui::XOutlineButton', '确认', 'ButtonControl')]):
            layer.children = children
            with self.assertRaises(RuntimeError):
                private_layer_controls(p, expected_panel_runtime_id=[42, 1])

    def test_summary_only_explicit_private(self):
        for value in ('公开', '谁可以看', '', '私密'):
            p = panel([Control('mmui::PublishPrivacyView', '谁可以看  ', children=[text('谁可以看'), text(value)])])
            if value == '私密':
                result = read_private_summary(p, expected_panel_runtime_id=[42, 1])
                self.assertTrue(result['privateSummaryObserved'])
                self.assertFalse(result['publicationConfirmed'])
            else:
                with self.assertRaises(RuntimeError):
                    read_private_summary(p, expected_panel_runtime_id=[42, 1])
        with self.assertRaisesRegex(RuntimeError, 'owned-draft-mismatch'):
            read_private_summary(p, expected_panel_runtime_id=[42, 2])

    def test_duplicate_summary_and_extra_text_refused(self):
        summary = Control('mmui::PublishPrivacyView', children=[text('谁可以看'), text('私密')])
        for children in ([summary, summary], [Control('mmui::PublishPrivacyView', children=[text('谁可以看'), text('私密'), text('公开')])]):
            with self.assertRaises(RuntimeError):
                read_private_summary(panel(children), expected_panel_runtime_id=[42, 1])

    def test_recorded_structure_replay_not_live_acceptance(self):
        path = Path(__file__).resolve().parents[2] / 'artifacts/social-draft-probe-session-20261102/inspect-002.json'
        rows = json.loads(path.read_text(encoding='utf-8-sig'))['controls']
        stack = []; roots = []
        for row in rows:
            if row['window'] != 0: continue
            c = Control(row['class'], row['name'], row['type'], rid=row['runtimeId'])
            depth = row['depth']
            while stack and stack[-1][0] >= depth: stack.pop()
            if stack: stack[-1][1].children.append(c)
            else: roots.append(c)
            stack.append((depth, c))
        p = next(c for c in roots[0].children if c.ClassName == 'mmui::SnsPublishPanel')
        result = private_option_candidate(p, expected_panel_runtime_id=p.GetRuntimeId())
        self.assertEqual(result.Name, '私密')
        self.assertEqual(public_option_candidate(p, expected_panel_runtime_id=p.GetRuntimeId()).Name, '公开')
        buttons = private_layer_controls(p, expected_panel_runtime_id=p.GetRuntimeId())
        self.assertEqual(buttons['confirm'].Name, '确定')
        self.assertEqual(buttons['cancel'].Name, '取消')
        self.assertEqual(buttons['confirm'].GetRuntimeId(), [42, 29035164, 4, -2147471514])
        with self.assertRaisesRegex(RuntimeError, 'layer-still-open'):
            read_private_summary(p, expected_panel_runtime_id=p.GetRuntimeId())


if __name__ == '__main__': unittest.main()
