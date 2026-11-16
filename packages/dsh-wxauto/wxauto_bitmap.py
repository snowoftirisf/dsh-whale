"""Pinned native dependency bootstrap and guarded wxauto message-row capture.

Capturing always uses wxauto's own Control.CaptureToImage API. No independent
screen acquisition backend is used here. Merely importing this module is inert.
"""
import ctypes
import hashlib
import importlib
import struct
import threading
import uuid
from pathlib import Path

from PIL import Image
from attachment_policy import local_path

DLL_NAME = 'UIAutomationClient_VC140_X64'
DLL_SHA256 = 'ee7b7d41b2fde000d02bb04cdc405772f9fc75a817cc467b988d32740bfa0f39'
_LOCK = threading.RLock()


def _check_library(library, expected):
    try:
        name = Path(library._name)
        if not name.is_absolute() or local_path(name) != expected:
            raise ValueError('different-library')
        if hashlib.sha256(name.read_bytes()).hexdigest() != DLL_SHA256:
            raise ValueError('different-content')
    except (AttributeError, TypeError, ValueError, OSError) as error:
        raise RuntimeError('wxauto-bitmap-loaded-incompatible') from error


def bootstrap_bitmap(uia, *, dll_path=None, library_loader=None, arch_bits=None):
    """Use only the audited x64 DLL; never replace a different loaded library."""
    if (arch_bits if arch_bits is not None else struct.calcsize('P') * 8) != 64:
        raise RuntimeError('wxauto-bitmap-architecture')
    path = local_path(dll_path or Path(uia.__file__).parent / 'bin' / (DLL_NAME + '.dll'))
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != DLL_SHA256:
            raise RuntimeError('wxauto-bitmap-hash-mismatch')
    except OSError as error:
        raise RuntimeError('wxauto-bitmap-unavailable') from error
    # wxauto4.uia re-exports public symbols with import *, which excludes the
    # private singleton. Captures themselves resolve it in uiautomation.py.
    implementation=uia if hasattr(uia,'_DllClient') else importlib.import_module('wxauto4.uia.uiautomation')
    with _LOCK:
        instance = implementation._DllClient._instance
        existing = vars(ctypes.cdll).get(DLL_NAME)
        if instance is not None and instance.dll is not None:
            _check_library(instance.dll, path)
        if existing is not None:
            _check_library(existing, path)
        library = existing or (instance.dll if instance is not None and instance.dll is not None else None)
        if library is None:
            library = (library_loader or ctypes.CDLL)(str(path))
            _check_library(library, path)
        setattr(ctypes.cdll, DLL_NAME, library)
        # A failed previous attempt caches dll=None. Let wxauto initialize its
        # own declarations on next CaptureToImage; no replacement of live DLLs.
        if instance is not None and instance.dll is None:
            implementation._DllClient._instance = None
        return library


def _identity(control):
    identity = tuple(control.GetRuntimeId())
    if not identity:
        raise RuntimeError('image-row-identity-unavailable')
    return identity


def _reject_known_panel_overlap(top, row_bounds):
    """Observed A/B/A selector; use live bounds, never a screen-coordinate guess.

    Evidence: artifacts/group-info-visibility-diff-20261102.json. This in-window
    panel can hide rows while ElementFromPoint still returns the underlying row.
    """
    try:
        panel = top.GroupControl(ClassName='mmui::ChatRoomMemberInfoView')
        if not panel.Exists(0) or panel.IsOffscreen:
            return
        rect = panel.BoundingRectangle
        bounds = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise ValueError('visible-panel-has-invalid-bounds')
    except Exception as error:
        raise RuntimeError('image-panel-state-unavailable') from error
    left, up, right, bottom = row_bounds
    if max(left, bounds[0]) < min(right, bounds[2]) and max(up, bounds[1]) < min(bottom, bounds[3]):
        raise RuntimeError('image-row-covered-by-group-info')


def _guard(message, ensure_chat, uia):
    if not ensure_chat() or not message.exists():
        raise RuntimeError('image-row-unavailable')
    row = message.control
    top = row.GetTopLevelControl()
    if row.IsOffscreen or top is None or top.IsOffscreen or not uia.IsWindowVisible(top.NativeWindowHandle):
        raise RuntimeError('image-row-not-visible')
    if uia.GetForegroundWindow() != top.NativeWindowHandle:
        raise RuntimeError('image-row-not-foreground')
    rect = row.BoundingRectangle
    bounds = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    left, up, right, bottom = bounds
    if right - left < 2 or bottom - up < 2:
        raise RuntimeError('image-capture-empty')
    _reject_known_panel_overlap(top, bounds)
    wanted = _identity(row)
    # Verify hit-tested controls belong to this exact row, never just to WeChat.
    # Reject only occlusion reported by the UIA provider. WeChat has actually
    # returned underlying rows beneath its persistent group-info panel, so even
    # repeated matching hit tests cannot prove pixel visibility. The separately
    # observed group-info overlap guard above handles that one known panel.
    for x in (left + 1, (left + right) // 2, right - 1):
        for y in (up + 1, (up + bottom) // 2, bottom - 1):
            hit = uia.ControlFromPoint(x, y)
            for _ in range(32):
                if hit is None:
                    break
                if _identity(hit) == wanted:
                    break
                hit = hit.GetParentControl()
            else:
                hit = None
            if hit is None:
                raise RuntimeError('image-row-occluded')
    return wanted, top.NativeWindowHandle, bounds


def capture_message_row(message, directory, ensure_chat=lambda: True, *, uia=None,
                        bootstrap=None, max_bytes=20 * 1024 * 1024, max_pixels=40_000_000):
    """Capture precisely one visible message control, then remove the temp PNG."""
    if uia is None:
        from wxauto4.uia import uiautomation as uia
    before = _guard(message, ensure_chat, uia)
    bounds = before[2]
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    if width * height > max_pixels:
        raise RuntimeError('image-pixel-limit')
    (bootstrap or bootstrap_bitmap)(uia)
    root = local_path(directory)
    root.mkdir(parents=True, exist_ok=True)
    temporary = local_path(root / ('row-capture-' + uuid.uuid4().hex + '.png'))
    temporary.touch(exist_ok=False)
    try:
        if not message.control.CaptureToImage(str(temporary)):
            raise RuntimeError('image-capture-failed')
        if _guard(message, ensure_chat, uia) != before:
            raise RuntimeError('image-row-changed')
        if not 0 < temporary.stat().st_size <= max_bytes:
            raise RuntimeError('image-size-limit')
        with Image.open(temporary) as image:
            if image.format != 'PNG' or image.width * image.height > max_pixels:
                raise RuntimeError('image-capture-format')
            if image.size != (width, height):
                raise RuntimeError('image-capture-bounds-mismatch')
            frame = image.convert('RGB')
            if max(high for low, high in frame.getextrema()) <= 3:
                raise RuntimeError('image-capture-black-frame')
            return frame.copy()
    finally:
        temporary.unlink(missing_ok=True)
