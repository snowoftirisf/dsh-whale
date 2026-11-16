"""Locate a safe point inside one incoming voice bubble, using PIL pixels only.

No OCR, UI access, actions, SDK imports or guessed fallback coordinates. The
caller must qualify the image as the same visible incoming voice row separately.
"""
from collections import Counter, deque
from statistics import median


class VoiceGeometryError(RuntimeError):
    pass


def locate_voice_bubble(image):
    """Return row-relative x/y and non-text facts; raise on missing/ambiguous.

    Right edge estimates row background. Connected low-chroma shapes differing
    from that background must have voice-sized height/area/rectangular fill.
    Nickname glyphs, square avatars and the smaller transcription pill are not
    voice candidates. An aligned lower transcript selects that lower bubble for
    the SDK's later copy menu. Whole-row height is not a scale estimate.
    """
    if image is None or not hasattr(image, 'convert'):
        raise VoiceGeometryError('voice-geometry-image-invalid')
    width, height = image.size
    if (type(width) is not int or type(height) is not int or not 100 <= width <= 4096
            or not 40 <= height <= 2048 or width * height > 2_000_000):
        raise VoiceGeometryError('voice-geometry-image-size')
    rgb = image.convert('RGB')
    pixels = rgb.load()
    edge = [pixels[x, y] for x in range(width - max(3, width // 50), width)
            for y in range(height)]
    background = tuple(int(median(color[channel] for color in edge)) for channel in range(3))
    stable = sum(max(abs(color[c] - background[c]) for c in range(3)) <= 4 for color in edge) / len(edge)
    if stable < .95 or max(background) - min(background) > 18:
        raise VoiceGeometryError('voice-geometry-background-unverified')
    mask = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            color = pixels[x, y]
            if max(color) - min(color) <= 14 and max(abs(color[c] - background[c]) for c in range(3)) >= 6:
                mask[y * width + x] = 1
    candidates = []
    components = 0
    for first in range(len(mask)):
        if mask[first] != 1:
            continue
        components += 1
        if components > 20000:
            raise VoiceGeometryError('voice-geometry-component-budget')
        queue = deque([first])
        mask[first] = 2
        members = []
        left = right = first % width
        top = bottom = first // width
        while queue:
            index = queue.popleft()
            members.append(index)
            x, y = index % width, index // width
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            for neighbor in ((index - 1 if x else -1), (index + 1 if x + 1 < width else -1),
                             (index - width if y else -1), (index + width if y + 1 < height else -1)):
                if neighbor >= 0 and mask[neighbor] == 1:
                    mask[neighbor] = 2
                    queue.append(neighbor)
        bw, bh = right - left + 1, bottom - top + 1
        if not (bh >= 14 and bw >= max(14, .35 * bh) and left >= 8
                and right < width - 4 and top >= 4 and bottom < height - 4):
            continue
        fill = len(members) / (bw * bh)
        colors = Counter(pixels[index % width, index // width] for index in members)
        dominant, _ = colors.most_common(1)[0]
        # Near-uniform interior excludes mottled avatars and text-only regions.
        uniform = sum(count for color, count in colors.items()
                      if max(abs(color[c] - dominant[c]) for c in range(3)) <= 4) / (bw * bh)
        if fill < .72 or uniform < .65:
            continue
        safe = None
        inset = max(4, int(min(bw, bh) * .12))
        for fx in (.72, .82, .6, .5, .4, .3):
            for fy in (.5, .4, .6):
                x, y = round(left + fx * (bw - 1)), round(top + fy * (bh - 1))
                if not (left + inset <= x <= right - inset and top + inset <= y <= bottom - inset):
                    continue
                patch = [pixels[xx, yy] for yy in range(y - 2, y + 3) for xx in range(x - 2, x + 3)]
                if all(max(abs(color[c] - dominant[c]) for c in range(3)) <= 3 for color in patch):
                    safe = (x, y)
                    break
            if safe:
                break
        candidates.append({'x': safe[0] if safe else None, 'y': safe[1] if safe else None,
                           'bubbleBounds': [left, top, right + 1, bottom + 1],
                           'fillRatio': round(fill, 6), 'uniformRatio': round(uniform, 6)})
    def size(candidate):
        left, top, right, bottom = candidate['bubbleBounds']
        return right - left, bottom - top
    voices = []
    for candidate in candidates:
        left, top, _, _ = candidate['bubbleBounds']
        bw, bh = size(candidate)
        # Observed group-row layout: upper voice under the nickname, beside
        # avatar. Relative dimensions survive expanded/long transcript height.
        if 1.6 <= bw / bh <= 12 and .8 <= left / bh <= 3 and .4 <= top / bh <= 1.2:
            voices.append(candidate)
    if len(voices) != 1:
        raise VoiceGeometryError('voice-geometry-missing' if not voices else 'voice-geometry-ambiguous')
    voice = voices[0]
    vl, vt, vr, vb = voice['bubbleBounds']
    vw, vh = size(voice)
    # A second body-sized gray region cannot silently be ignored as "not the
    # largest". The one allowed pair is vertically adjacent and left aligned.
    bodies = [c for c in candidates if c is not voice and size(c)[1] >= .72 * vh
              and size(c)[0] >= .6 * vh]
    selected, kind = voice, 'voice'
    if bodies:
        if len(bodies) != 1:
            raise VoiceGeometryError('voice-geometry-ambiguous')
        lower = bodies[0]
        ll, lt, lr, lb = lower['bubbleBounds']
        if not (0 <= lt - vb <= .35 * vh and abs(ll - vl) <= .25 * vh
                and lb > vb and .6 * vh <= lr - ll <= max(12 * vh, vw * 4)):
            raise VoiceGeometryError('voice-geometry-ambiguous')
        selected, kind = lower, 'expanded-transcript'
    if selected['x'] is None:
        raise VoiceGeometryError('voice-geometry-safe-patch-missing')
    return {**selected, 'imageWidth': width, 'imageHeight': height,
            'voiceBounds': voice['bubbleBounds'], 'targetKind': kind,
            'backgroundRgb': list(background), 'candidateCount': 1, 'safePatchSize': 5,
            'method': 'low-chroma-connected-bubble-v2'}
