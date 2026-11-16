"""Passive rendered-row direction using the installed detector, not SDK messages.

No parse_msg, avatar action, activation, focus, scrolling, keys or saved images.
Importing this module does not import wxauto or inspect a live control.
"""
import hashlib
import math
import numbers


class MessageDirectionError(RuntimeError):
    pass


def _bounds(control):
    rect = control.BoundingRectangle
    value = tuple(getattr(rect, k) for k in ('left', 'top', 'right', 'bottom'))
    if (any(type(n) not in (int, float) or not math.isfinite(n) or int(n) != n for n in value)
            or value[2] <= value[0] or value[3] <= value[1]):
        raise MessageDirectionError('message-direction-invalid-rect')
    return tuple(int(n) for n in value)


def _visible(control):
    value = control.IsOffscreen
    if type(value) not in (bool, int) or value != 0:
        raise MessageDirectionError('message-direction-not-visible')


def _identity(control):
    rid = control.GetRuntimeId()
    if not isinstance(rid, (list, tuple)) or not rid or any(type(n) is not int for n in rid):
        raise MessageDirectionError('message-direction-unreadable')
    name, cls = control.Name, control.ClassName
    if not isinstance(name, str) or not isinstance(cls, str):
        raise MessageDirectionError('message-direction-unreadable')
    return (tuple(rid), _bounds(control), cls,
            hashlib.sha256(name.encode('utf8')).digest(), control.ProcessId)


def _contains(outer, inner):
    return outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]


def _state(control, root, uia, msgbox):
    _visible(control)
    row, main = _identity(control), _identity(root)
    hwnd = root.NativeWindowHandle
    if type(hwnd) is not int or hwnd <= 0 or type(main[-1]) is not int or main[-1] <= 0 or row[-1] != main[-1]:
        raise MessageDirectionError('message-direction-root-unverified')
    if not _contains(main[1], row[1]):
        raise MessageDirectionError('message-direction-row-outside-view')
    viewport = None
    if msgbox is not None:
        _visible(msgbox)
        viewport = _identity(msgbox)
        if viewport[-1] != main[-1] or not _contains(main[1], viewport[1]) or not _contains(viewport[1], row[1]):
            raise MessageDirectionError('message-direction-row-outside-view')
    front = uia.GetForegroundControl()
    return (row, main, hwnd, viewport, (front.NativeWindowHandle, front.ProcessId))


def _image_status(image):
    # RGB avoids treating a fully black image with opaque alpha as non-black.
    if image is None or not hasattr(image, 'convert'):
        raise MessageDirectionError('message-direction-invalid-image')
    width, height = image.size
    if (type(width) is not int or type(height) is not int or min(width, height) <= 0
            or width * height > 16_000_000):
        raise MessageDirectionError('message-direction-invalid-image')
    return image.convert('RGB').getbbox() is not None


# WeChat 4.x overlays the message list's scrollbar on the right edge of every
# row for about a second after any scroll (measured: 2-10 logical px in from
# the edge). The edge-scanning detector saw it before the left avatar of an
# incoming row and called the row 'self'. Our own avatar starts ~22 logical px
# in, so dropping 16 logical px removes the scrollbar and keeps that avatar.
SCROLLBAR_STRIP_LOGICAL_PX = 16


def _window_scale(root):
    try:
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForWindow(root.NativeWindowHandle)
        return dpi / 96 if 96 <= dpi <= 480 else 1.0
    except Exception:
        return 1.0


def _without_scrollbar(image, scale):
    strip = round(SCROLLBAR_STRIP_LOGICAL_PX * scale)
    width, height = image.size
    if width <= strip * 4:
        return image
    return image.crop((0, 0, width - strip, height))


def read_message_direction(control, root, *, uia=None, detector=None, msgbox=None):
    """Return self/friend from one stable row, or raise a fixed non-text code.

    Optional msgbox is the trusted live viewport control, not a model-supplied
    rectangle. Without it, the row must still be visible and contained in root.
    No textual content or image is returned/logged. This is rendered direction,
    not server identity or proof that a particular send generated a row.
    """
    try:
        if uia is None:
            from wxauto4 import uia
        before = _state(control, root, uia, msgbox)
        image = control.ScreenShot(return_img=True)
        if _state(control, root, uia, msgbox) != before:
            raise MessageDirectionError('message-direction-capture-changed')
        if not _image_status(image):
            # Window capture is black on this Qt build; visible-screen fallback
            # is allowed only when this exact root is already foreground.
            if before[-1] != (root.NativeWindowHandle, root.ProcessId):
                raise MessageDirectionError('message-direction-screen-foreground-required')
            image = uia.ImageGrab.grab(bbox=before[0][1], all_screens=True)
            if _state(control, root, uia, msgbox) != before:
                raise MessageDirectionError('message-direction-capture-changed')
            if not _image_status(image):
                raise MessageDirectionError('message-direction-black-image')
        if detector is None:
            from wxauto4.utils.tools import detect_message_direction
            # Real Qt captures have 1–3-level edge noise. With zero tolerance
            # the installed detector mistook an empty left border for an avatar
            # and classified a verified self voice row as incoming. Eight was
            # checked against the same captured row, without changing pixels.
            detector = lambda image: detect_message_direction(image,tolerance=8)
        result = detector(_without_scrollbar(image, _window_scale(root)))
        if (not isinstance(result, (tuple, list)) or len(result) != 2
                or result[0] not in ('left', 'right')
                or isinstance(result[1], bool) or not isinstance(result[1], numbers.Real)
                or not math.isfinite(result[1])):
            raise MessageDirectionError('message-direction-unknown')
        # Reject row/foreground changes during a slow detector as well.
        if _state(control, root, uia, msgbox) != before:
            raise MessageDirectionError('message-direction-capture-changed')
        return 'self' if result[0] == 'right' else 'friend'
    except MessageDirectionError:
        raise
    except Exception:
        # SDK/image exceptions can contain names, paths or row text.
        raise MessageDirectionError('message-direction-unreadable') from None
