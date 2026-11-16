"""Download only a fresh, already-qualified wxauto image message."""
import hashlib
import shutil
import uuid
import time
from pathlib import Path
from PIL import Image
from wxauto_bitmap import capture_message_row

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000


def capture_image_message(message, directory, ensure_chat=lambda: True, *, row_capture=None):
    """Capture only the received photo row when its original preview is unavailable."""
    from wxauto4.msgs.mtype import crop_emotion_image
    if not ensure_chat() or not message.exists():
        raise RuntimeError('image-row-unavailable')
    message.roll_into_view()
    if not ensure_chat() or not message.exists():
        raise RuntimeError('image-row-unavailable')
    frame = (row_capture or capture_message_row)(message, directory, ensure_chat,
                                                max_bytes=MAX_BYTES, max_pixels=MAX_PIXELS)
    if not isinstance(frame, Image.Image) or frame.width * frame.height > MAX_PIXELS:
        raise RuntimeError('image-capture-failed')
    frame = crop_emotion_image(frame, 'left', top=True).convert('RGB')
    if frame.width < 2 or frame.height < 2:
        raise RuntimeError('image-capture-empty')
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / (uuid.uuid4().hex + '.png')
    frame.save(target)
    data = target.read_bytes()
    if len(data) > MAX_BYTES:
        target.unlink()
        raise RuntimeError('image-size-limit')
    return {'path': str(target), 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
            'captureNote': '微信原图下载失败；附件仅为这条图片消息当前可见缩略图的截图，不是完整原图。只能依据清晰可见部分回答，不能臆测被缩小、模糊或裁掉的细节。',
            'source': 'visible-thumbnail'}


def image_click_point(frame, threshold=24):
    """Center of the picture inside a captured incoming row, or None.

    Group rows put the sender nickname above the picture, and wxauto's own
    click lands on the avatar there. Skip the leftmost content run (avatar),
    take the widest remaining column run, then its tallest vertical run
    (the picture, not the nickname line above it).
    """
    width, height = frame.size
    if width < 40 or height < 20:
        return None
    pixels = frame.convert('RGB').load()
    background = pixels[width - 2, height // 2]
    differs = lambda x, y: max(abs(a - b) for a, b in zip(pixels[x, y], background)) > threshold
    def runs(flags):
        found, start = [], None
        for index, on in enumerate(list(flags) + [False]):
            if on and start is None:
                start = index
            elif not on and start is not None:
                found.append((start, index))
                start = None
        return found
    columns = runs(any(differs(x, y) for y in range(0, height, 2)) for x in range(width))
    if len(columns) < 2:
        return None
    picture = max(columns[1:], key=lambda run: run[1] - run[0])
    if picture[1] - picture[0] < 20:
        return None
    lines = runs(any(differs(x, y) for x in range(picture[0], picture[1], 3)) for y in range(height))
    if not lines:
        return None
    top, bottom = max(lines, key=lambda run: run[1] - run[0])
    if bottom - top < 20:
        return None
    return (picture[0] + picture[1]) // 2, (top + bottom) // 2


def aim_image_click(message, directory, ensure_chat, *, row_capture=None):
    """Point the SDK's click at the picture itself; False keeps wxauto's own click."""
    try:
        from wxauto4.uia import uiautomation as uia
        top = message.control.GetTopLevelControl()
        if top is not None and uia.GetForegroundWindow() != top.NativeWindowHandle:
            top.SetActive(waitTime=0.1)
        frame = (row_capture or capture_message_row)(message, directory, ensure_chat,
                                                    max_bytes=MAX_BYTES, max_pixels=MAX_PIXELS)
        point = image_click_point(frame)
        if point is None:
            return False
        rect = message.control.BoundingRectangle
        x, y = int(rect.left) + point[0], int(rect.top) + point[1]
        # ImageMessage.download opens the viewer through self.click().
        message.click = lambda *args, **kwargs: uia.Click(x, y, waitTime=0.3)
        return True
    except Exception:
        return False


def download_image(message, directory, ensure_chat=lambda: True, *, aim=False):
    from wxauto4.param import WxParam
    from connection_policy import error_diagnostic
    if not ensure_chat():
        raise RuntimeError('target-mismatch')
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    staging = root / 'downloads'
    staging.mkdir(exist_ok=True)
    if aim and hasattr(message, 'control'):
        aim_image_click(message, root, ensure_chat)
    previous = WxParam.DEFAULT_SAVE_PATH
    try:
        WxParam.DEFAULT_SAVE_PATH = str(staging)
        try:
            # wxauto4 defaults to original=False; explicitly request the full
            # image rather than letting WeChat save its compressed preview.
            source = message.download(original=True)
        except (AttributeError, LookupError) as error:
            if not hasattr(message, 'control'):
                raise
            # Group viewers often have no "view original" button, which the SDK
            # treats as fatal. The viewer's own image still beats a thumbnail.
            source = None
            try:
                if ensure_chat():
                    source = message.download(original=False)
            except Exception:
                source = None
            if not isinstance(source, (str, Path)):
                result = capture_image_message(message, root, ensure_chat)
                result['downloadDiagnostic'] = error_diagnostic(error, 'download-original-image')
                return result
    finally:
        WxParam.DEFAULT_SAVE_PATH = previous
    if not ensure_chat():
        raise RuntimeError('target-mismatch')
    if not isinstance(source, (str, Path)):
        if hasattr(message, 'control'):
            return capture_image_message(message, root, ensure_chat)
        raise RuntimeError('image-download-failed')
    source = Path(source).resolve(strict=True)
    if not source.is_relative_to(staging) or not source.is_file():
        raise RuntimeError('image-download-path-denied')
    if not 0 < source.stat().st_size <= MAX_BYTES:
        raise RuntimeError('image-size-limit')
    with Image.open(source) as image:
        if image.width * image.height > MAX_PIXELS:
            raise RuntimeError('image-pixel-limit')
        kind = image.format
        width, height = image.size
        image.verify()
    extension = {'JPEG': '.jpg', 'PNG': '.png', 'GIF': '.gif', 'WEBP': '.webp', 'BMP': '.bmp'}.get(kind)
    if extension is None:
        raise RuntimeError('image-format-unsupported')
    target = root / (uuid.uuid4().hex + extension)
    shutil.copyfile(source, target)
    if kind == 'BMP':
        converted = target.with_suffix('.png')
        with Image.open(target) as image:
            image.save(converted, 'PNG')
        target.unlink()
        target = converted
    data = target.read_bytes()
    if len(data) > MAX_BYTES:
        target.unlink()
        raise RuntimeError('image-size-limit')
    # Delete only the downloaded staging copy; the retained original has a unique name.
    source.unlink()
    return {'path': str(target), 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
            'source': 'wechat-download', 'originalRequested': True, 'width': width, 'height': height}


def capture_emotion(message, directory, ensure_chat=lambda: True, *, row_capture=None):
    """Free wxauto exposes emotion rows but no GIF download; capture that row only."""
    from wxauto4.msgs.mtype import crop_emotion_image
    from PIL import ImageDraw
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    frames = []
    for index in range(3):
        if not ensure_chat() or not message.exists():
            raise RuntimeError('emotion-row-unavailable')
        message.roll_into_view()
        if not ensure_chat():
            raise RuntimeError('target-mismatch')
        frame = (row_capture or capture_message_row)(message, root, ensure_chat,
                                                    max_bytes=MAX_BYTES, max_pixels=MAX_PIXELS)
        if not isinstance(frame, Image.Image) or frame.width * frame.height > MAX_PIXELS:
            raise RuntimeError('emotion-capture-failed')
        frame = crop_emotion_image(frame, 'left', top=True).convert('RGB')
        frame.thumbnail((600,600))
        if frame.width < 2 or frame.height < 2:
            raise RuntimeError('emotion-capture-empty')
        frames.append(frame.copy())
        if index < 2:
            time.sleep(0.3)
    width=max(frame.width for frame in frames)
    height=max(frame.height for frame in frames)
    sheet=Image.new('RGB',(width*len(frames),height+26),'white')
    drawing=ImageDraw.Draw(sheet)
    for index,frame in enumerate(frames):
        sheet.paste(frame,(index*width,26))
        drawing.text((index*width+4,5),f'Visible sample {index+1}',fill='black')
    target=root/(uuid.uuid4().hex+'.png')
    sheet.save(target)
    data=target.read_bytes()
    return {'path':str(target),'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),
            'captureNote':'这是同一条微信表情在约0.6秒内的三张可见采样帧，按左到右排序；不是三条消息，也不是完整原始GIF。可理解可见文字、形象和情绪，不要臆测未捕获的动作。'}
