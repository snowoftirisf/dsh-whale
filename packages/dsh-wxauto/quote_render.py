"""Render WeChat quote replies (appmsg type 57) as plain text for native catalogs.

Pure transformation: no database, filesystem, network or UI access. Anything
that is not a well-formed quote reply returns None so callers keep the old
text-only behavior for every other link/file envelope.
"""
import html
import re
from xml.etree import ElementTree as ET

QUOTE_BASE_TYPE = 49
# Quote rows were never admitted before this fix (2026-11-06 18:30 +08:00).
# Older rows stay invisible so a restart cannot replay days of quoted chat.
QUOTE_ADMISSION_FLOOR_SECONDS = 1793961003
# Sticker CDN links joined quote text at 2026-11-06 19:20 +08:00. Any future
# change to rendered quote text needs its own floor like this one.
STICKER_LINK_FLOOR_SECONDS = 1793964000
# Received stickers moved from the UI path to the native catalogs on
# 2026-11-06; the floor admits the 19:38:57 sticker that the UI path dropped.
STICKER_BASE_TYPE = 47
STICKER_ADMISSION_FLOOR_SECONDS = 1793965130
# Must match STICKER_MARK in quoted-images.mjs.
STICKER_MARK = '（微信表情原图 wx-emoji:'
MAX_QUOTED_CHARS = 300
# Received photos moved from the UI download path to native decryption of the
# local .dat (2026-11-15 22:20 +08:00). Older photos stay invisible: no replay.
IMAGE_BASE_TYPE = 3
IMAGE_ADMISSION_FLOOR_SECONDS = 1794752469
# Must match IMAGE_MARK in native-images.mjs.
IMAGE_MARK = '（微信图片原图 wx-image:'
_FILE_MD5 = re.compile(rb'(?i)(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])')
# Must match QUOTED_IMAGE_MARK in quoted-images.mjs.
QUOTED_IMAGE_MARK = '（被引用图片编号 wx-img:'
# Plain sticker copies only; must match STICKER_HOST in quoted-images.mjs.
STICKER_URL = re.compile(r'https?://[A-Za-z0-9.-]+\.qq\.com/[^\s|）<>"]{1,2000}')
LABELS = {'3': '图片', '34': '语音', '42': '名片', '43': '视频', '47': '表情', '48': '位置', '50': '通话'}
PREFIX = re.compile(r'\A\s*[A-Za-z0-9_.-]+(?:@(?:chatroom|openim))?:\r?\n')
INTERNAL_ID = re.compile(r'(?i)(?<![\w])wxid_[A-Za-z0-9_-]+')
ROOM_ID = re.compile(r'(?i)(?<![\w/\\.-])[A-Za-z0-9_.-]+@(?:chatroom|openim)\b')


def _redact(text):
    return ROOM_ID.sub('[内部标识已隐藏]', INTERNAL_ID.sub('[内部标识已隐藏]', text))


def _leaf(node):
    return (node.text or '').strip() if node is not None else ''


def _parse(text):
    text = PREFIX.sub('', text.lstrip('﻿').strip(), count=1)
    if not text.startswith('<') or len(text) > 1_000_000 or re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', text, re.I):
        return None
    try:
        return ET.fromstring(text)
    except (ET.ParseError, ValueError):
        return None


def _appmsg(root):
    if root is None:
        return None
    return root if root.tag == 'appmsg' else root.find('.//appmsg')


def _clip(text):
    return text[:MAX_QUOTED_CHARS] + '…' if len(text) > MAX_QUOTED_CHARS else text


def sticker_link(raw):
    """(md5, cdnurl) from an original sticker envelope when WeChat stored a plain CDN copy."""
    if not isinstance(raw, str):
        return None
    root = _parse(raw)
    emoji = root if root is not None and root.tag == 'emoji' else (root.find('.//emoji') if root is not None else None)
    if emoji is None:
        return None
    md5, url = emoji.get('md5', '').lower(), emoji.get('cdnurl', '')
    if not re.fullmatch(r'[0-9a-f]{32}', md5) or not STICKER_URL.fullmatch(url):
        return None
    return md5, url


def _quoted(refer, sticker_source=None):
    kind, content = _leaf(refer.find('type')), _leaf(refer.find('content'))
    if kind == '1':
        return _clip(PREFIX.sub('', content, count=1).strip() or '[空消息]')
    if kind == '49':
        app = _appmsg(_parse(html.unescape(content)))
        title = _leaf(app.find('title')) if app is not None else ''
        return _clip(title or '[链接/文件]')
    label = '[' + LABELS.get(kind, '消息') + ']'
    svrid = _leaf(refer.find('svrid'))
    if kind in ('3', '47') and re.fullmatch(r'[1-9][0-9]{0,19}', svrid):
        # Stable marker; the adapter swaps it for the quoted picture's evidence.
        # A sticker also carries its original CDN copy for a late download.
        link = None
        if kind == '47' and sticker_source is not None:
            try:
                link = sticker_source(int(svrid))
            except Exception:
                link = None
        extra = f'|{link[0]}|{link[1]}' if link else ''
        return f'{label}{QUOTED_IMAGE_MARK}{svrid}{extra}）'
    return label


def render_quote(local_type, raw, sticker_source=None):
    """Return 'reply\\n[引用 X 的消息：quoted]' for a quote row, else None."""
    try:
        if (int(local_type or 0) & 0xFFFFFFFF) != QUOTE_BASE_TYPE or not isinstance(raw, str):
            return None
        app = _appmsg(_parse(raw))
        if app is None or _leaf(app.find('type')) != '57':
            return None
        reply = _leaf(app.find('title'))
        if not reply:
            return None
        refer = app.find('refermsg')
        if refer is None:
            return _redact(reply)
        name = _leaf(refer.find('displayname')) or '某人'
        quoted = _quoted(refer, sticker_source)
        return _redact(f'{reply}\n[引用 {name} 的消息：{quoted}]')
    except Exception:
        return None


def admit_sticker(local_type, raw, timestamp_seconds):
    """Text for a received sticker row (type 47) sent after the sticker floor, else None.

    The adapter downloads the plain CDN copy named in the marker and attaches it.
    """
    try:
        if (int(local_type or 0) & 0xFFFFFFFF) != STICKER_BASE_TYPE:
            return None
    except (TypeError, ValueError):
        return None
    if type(timestamp_seconds) is not int or timestamp_seconds < STICKER_ADMISSION_FLOOR_SECONDS:
        return None
    link = sticker_link(raw)
    if not link:
        return '[收到一个微信表情；这个表情没有可下载的原图，内容未知，不要猜]'
    return f'[收到一个微信表情]{STICKER_MARK}{link[0]}|{link[1]}）'


def image_file_md5(packed):
    """The local attachment name (md5) WeChat stores in packed_info_data."""
    if isinstance(packed, str):
        packed = packed.encode('utf8', 'ignore')
    if not isinstance(packed, (bytes, bytearray)):
        return None
    match = _FILE_MD5.search(bytes(packed))
    return match.group(1).decode('ascii').lower() if match else None


def admit_image(local_type, packed, timestamp_seconds, chat_dir):
    """Text for a received photo (type 3) after the image floor, else None.

    chat_dir is md5(chat username), WeChat's attachment folder for this chat.
    The adapter decrypts msg/attach/<chat_dir>/*/Img/<md5>*.dat and attaches it.
    """
    try:
        if (int(local_type or 0) & 0xFFFFFFFF) != IMAGE_BASE_TYPE:
            return None
    except (TypeError, ValueError):
        return None
    if type(timestamp_seconds) is not int or timestamp_seconds < IMAGE_ADMISSION_FLOOR_SECONDS:
        return None
    if not isinstance(chat_dir, str) or not re.fullmatch(r'[0-9a-f]{32}', chat_dir):
        return None
    md5 = image_file_md5(packed)
    if not md5:
        return '[收到一张微信图片；本地没有这张图的文件记录，内容未知，不要猜]'
    return f'[收到一张微信图片]{IMAGE_MARK}{chat_dir}|{md5}）'


def admit_quote(local_type, raw, timestamp_seconds, sticker_source=None):
    """Rendered quote text only for quotes sent after the admission floor.

    sticker_source(server_id) -> (md5, cdnurl) | None reads the quoted sticker's
    original row from the same snapshot.
    """
    if type(timestamp_seconds) is not int or timestamp_seconds < QUOTE_ADMISSION_FLOOR_SECONDS:
        return None
    # Inboxes pin each admitted row's text; a quote rendered before sticker
    # links existed must keep rendering byte-identically, or the whole scan fails.
    if timestamp_seconds < STICKER_LINK_FLOOR_SECONDS:
        sticker_source = None
    return render_quote(local_type, raw, sticker_source)
