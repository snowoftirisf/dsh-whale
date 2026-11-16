import ctypes
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from PIL import Image
import wxauto_bitmap as bitmap


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.dll'
        self.path.write_bytes(b'not-executed-native-fixture')
        self.uia = NS(_DllClient=NS(_instance=None))
        self.loader = lambda path: NS(_name=path)
        self.patchers = [patch.object(bitmap, 'DLL_SHA256', bitmap.hashlib.sha256(self.path.read_bytes()).hexdigest()),
                         patch.dict(vars(ctypes.cdll), clear=False)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        vars(ctypes.cdll).pop(bitmap.DLL_NAME, None)

    def boot(self, **kwargs):
        return bitmap.bootstrap_bitmap(self.uia, dll_path=self.path, library_loader=self.loader, **kwargs)

    def test_pinned_explicit_load_is_reused_and_failed_singleton_reset(self):
        self.uia._DllClient._instance = NS(dll=None)
        library = self.boot()
        self.assertIsNone(self.uia._DllClient._instance)
        self.assertIs(vars(ctypes.cdll)[bitmap.DLL_NAME], library)
        self.loader = lambda path: self.fail('must not load twice')
        self.assertIs(self.boot(), library)

    def test_public_facade_resolves_private_singleton_from_implementation_module(self):
        facade=NS()
        self.uia._DllClient._instance=NS(dll=None)
        with patch.dict(sys.modules,{'wxauto4.uia.uiautomation':self.uia}):
            library=bitmap.bootstrap_bitmap(facade,dll_path=self.path,library_loader=self.loader)
        self.assertIsNone(self.uia._DllClient._instance)
        self.assertIs(vars(ctypes.cdll)[bitmap.DLL_NAME],library)

    def test_hash_architecture_and_unknown_loaded_library_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'architecture'):
            self.boot(arch_bits=32)
        self.path.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'hash-mismatch'):
            self.boot()

    def test_loaded_incompatible_library_never_replaced(self):
        old = NS(_name='unknown-loaded-library.dll')
        for cached in (True, False):
            with self.subTest(cached=cached):
                vars(ctypes.cdll).pop(bitmap.DLL_NAME, None)
                self.uia._DllClient._instance = NS(dll=old) if not cached else None
                if cached:
                    setattr(ctypes.cdll, bitmap.DLL_NAME, old)
                with self.assertRaisesRegex(RuntimeError, 'loaded-incompatible'):
                    self.boot()
                if cached:
                    self.assertIs(vars(ctypes.cdll)[bitmap.DLL_NAME], old)
                else:
                    self.assertIs(self.uia._DllClient._instance.dll, old)

    def test_existing_compatible_singleton_is_preserved(self):
        library = NS(_name=str(self.path))
        instance = NS(dll=library)
        self.uia._DllClient._instance = instance
        self.loader = lambda path:self.fail('live compatible library must be reused')
        self.assertIs(self.boot(), library)
        self.assertIs(self.uia._DllClient._instance, instance)


class RowCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rid = [2, 3]
        self.rect = NS(left=10, top=20, right=50, bottom=50)
        self.panel = NS(Exists=lambda timeout:False, IsOffscreen=False,
                        BoundingRectangle=NS(left=30, top=20, right=80, bottom=70))
        def group_control(**kwargs):
            self.assertEqual(kwargs, {'ClassName':'mmui::ChatRoomMemberInfoView'})
            return self.panel
        self.top = NS(NativeWindowHandle=20, IsOffscreen=False, GroupControl=group_control)
        self.capture_count = 0
        self.frame = Image.new('RGB', (40, 30), 'blue')
        self.after = lambda: None
        def capture(path):
            self.capture_count += 1
            self.frame.save(path)
            self.after()
            return True
        self.row = NS(GetRuntimeId=lambda:self.rid, GetTopLevelControl=lambda:self.top,
            BoundingRectangle=self.rect, IsOffscreen=False, CaptureToImage=capture)
        self.message = NS(control=self.row, exists=lambda:True)
        self.uia = NS(IsWindowVisible=lambda handle:True, GetForegroundWindow=lambda:20,
                      ControlFromPoint=lambda x,y:self.row)

    def capture(self, **kwargs):
        return bitmap.capture_message_row(self.message, self.root, uia=self.uia,
                                         bootstrap=lambda uia:None, **kwargs)

    def assert_clean(self):
        self.assertEqual(list(self.root.iterdir()), [])

    def test_only_exact_row_capture_and_temporary_file_removed(self):
        image = self.capture()
        self.assertEqual(image.size, (40, 30))
        self.assertEqual(image.getpixel((0,0)), (0,0,255))
        self.assertEqual(self.capture_count, 1)
        self.assert_clean()

    def test_foreground_offscreen_chat_and_occlusion_denied_before_capture(self):
        checks = [lambda:setattr(self.uia, 'GetForegroundWindow', lambda:999),
                  lambda:setattr(self.row, 'IsOffscreen', True),
                  lambda:setattr(self.uia, 'IsWindowVisible', lambda h:False),
                  lambda:setattr(self.uia, 'ControlFromPoint', lambda x,y:NS(
                      GetRuntimeId=lambda:[99], GetParentControl=lambda:None))]
        for change in checks:
            with self.subTest(change=change):
                self.setUp()
                change()
                with self.assertRaises(RuntimeError):
                    self.capture()
                self.assertEqual(self.capture_count, 0)
                self.assert_clean()
        with self.assertRaises(RuntimeError):
            self.capture(ensure_chat=lambda:False)

    def test_identity_or_foreground_change_after_capture_discards_file(self):
        for change in (lambda:setattr(self, 'rid', [3,4]),
                       lambda:setattr(self.uia, 'GetForegroundWindow', lambda:99)):
            with self.subTest(change=change):
                self.setUp()
                self.after = change
                with self.assertRaises(RuntimeError):
                    self.capture()
                self.assert_clean()

    def test_wrong_bounds_black_frame_and_limits_rejected(self):
        for frame, kwargs in [(Image.new('RGB',(40,30),'black'), {}),
                              (Image.new('RGB',(41,30),'blue'), {}),
                              (Image.new('RGB',(40,30),'blue'), {'max_bytes':3}),
                              (Image.new('RGB',(40,30),'blue'), {'max_pixels':10})]:
            with self.subTest(size=frame.size,kwargs=kwargs):
                self.frame = frame
                with self.assertRaises(RuntimeError):
                    self.capture(**kwargs)
                self.assert_clean()

    def test_hit_descendant_ancestry_must_reach_exact_row(self):
        child = NS(GetRuntimeId=lambda:[9], GetParentControl=lambda:self.row)
        self.uia.ControlFromPoint = lambda x,y:child
        self.assertEqual(self.capture().size, (40,30))
        self.assert_clean()

    def test_capture_failure_cleans_temporary_file(self):
        self.row.CaptureToImage = lambda path:False
        with self.assertRaisesRegex(RuntimeError, 'capture-failed'):
            self.capture()
        self.assert_clean()
        def fail(path):
            Path(path).write_bytes(b'partial')
            raise LookupError('UIA row disappeared')
        self.row.CaptureToImage = fail
        with self.assertRaises(LookupError):
            self.capture()
        self.assert_clean()

    def test_known_same_process_panel_overlap_denied_despite_underlying_hit_test(self):
        self.panel.Exists = lambda timeout:True
        # The UIA provider misleadingly still returns the underlying message.
        self.uia.ControlFromPoint = lambda x,y:self.row
        with self.assertRaisesRegex(RuntimeError, 'covered-by-group-info'):
            self.capture()
        self.assertEqual(self.capture_count, 0)
        self.assert_clean()

    def test_panel_absent_offscreen_nonoverlap_or_touching_edge_does_not_deny(self):
        self.assertEqual(self.capture().size, (40,30))  # absent
        self.panel.Exists = lambda timeout:True
        self.panel.IsOffscreen = True
        self.assertEqual(self.capture().size, (40,30))
        self.panel.IsOffscreen = False
        self.panel.BoundingRectangle = NS(left=50, top=20, right=80, bottom=70)
        self.assertEqual(self.capture().size, (40,30))  # touching only
        self.panel.BoundingRectangle = NS(left=51, top=20, right=80, bottom=70)
        self.assertEqual(self.capture().size, (40,30))
        self.assert_clean()

    def test_panel_opens_during_capture_discards_temporary_image(self):
        self.after = lambda:setattr(self.panel, 'Exists', lambda timeout:True)
        with self.assertRaisesRegex(RuntimeError, 'covered-by-group-info'):
            self.capture()
        self.assertEqual(self.capture_count, 1)
        self.assert_clean()

    def test_unreadable_panel_state_fails_closed_without_capture(self):
        def fail(timeout):
            raise LookupError('provider unavailable')
        self.panel.Exists = fail
        with self.assertRaisesRegex(RuntimeError, 'panel-state-unavailable'):
            self.capture()
        self.assertEqual(self.capture_count, 0)
        self.assert_clean()


if __name__ == '__main__':
    unittest.main()
