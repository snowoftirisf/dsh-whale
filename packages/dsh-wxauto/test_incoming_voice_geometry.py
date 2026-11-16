import unittest
from pathlib import Path

from PIL import Image, ImageDraw
from incoming_voice_geometry import locate_voice_bubble, VoiceGeometryError


def fixture(*, dark=False, bubble=True, second=False, pill=True):
    background, gray, ink = ((28, 28, 30), (58, 58, 61), (225, 225, 225)) if dark else ((250, 250, 250), (237, 237, 240), (30, 30, 30))
    image = Image.new('RGB', (864, 115), background)
    draw = ImageDraw.Draw(image)
    # Avatar and nickname: not a rectangular voice interior.
    draw.rounded_rectangle((30, 12, 83, 66), radius=7, fill=(195, 110, 150))
    for x in range(105, 225, 10):
        draw.rectangle((x, 19, x + 4, 32), fill=ink)
    if bubble:
        draw.rounded_rectangle((104, 44, 257, 97), radius=10, fill=gray)
        draw.polygon(((104, 65), (98, 71), (104, 77)), fill=gray)
        draw.line((129, 65, 137, 71, 129, 79), fill=ink, width=3)
        draw.line((140, 62, 146, 71, 140, 82), fill=ink, width=3)
        draw.rectangle((157, 65, 161, 77), fill=ink)
    if pill:
        draw.rounded_rectangle((293, 55, 370, 84), radius=14, fill=gray)
        for x in range(307, 355, 10):
            draw.line((x, 63, x + 3, 76), fill=ink, width=1)
    if second:
        draw.rounded_rectangle((480, 44, 633, 97), radius=10, fill=gray)
    return image


def expanded_fixture(*, lower_left=105, lower_top=104, lower_width=351, lower_height=54, dark=False):
    background, gray, ink = ((28, 28, 30), (58, 58, 61), (225, 225, 225)) if dark else ((250, 250, 250), (237, 237, 240), (30, 30, 30))
    image = Image.new('RGB', (864, lower_top + lower_height + 11), background)
    image.paste(fixture(dark=dark), (0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((lower_left, lower_top, lower_left + lower_width - 1, lower_top + lower_height - 1), radius=9, fill=gray)
    for y in range(lower_top + 18, lower_top + lower_height - 10, 28):
        for x in range(lower_left + 18, lower_left + lower_width - 20, 18):
            draw.line((x, y, x + 4, y + 10), fill=ink, width=2)
    return image


class IncomingVoiceGeometryTests(unittest.TestCase):
    def assert_safe(self, image, result):
        x, y = result['x'], result['y']
        left, top, right, bottom = result['bubbleBounds']
        self.assertGreaterEqual(x - 2, left)
        self.assertLess(x + 2, right)
        self.assertGreaterEqual(y - 2, top)
        self.assertLess(y + 2, bottom)
        rgb = image.convert('RGB')
        colors = [rgb.getpixel((xx, yy)) for yy in range(y - 2, y + 3) for xx in range(x - 2, x + 3)]
        self.assertTrue(all(max(c[i] for c in colors) - min(c[i] for c in colors) <= 6 for i in range(3)))

    def test_light_and_dark_point_inside_voice_not_nickname_or_pill(self):
        for dark in (False, True):
            with self.subTest(dark=dark):
                image = fixture(dark=dark)
                result = locate_voice_bubble(image)
                self.assert_safe(image, result)
                self.assertTrue(104 < result['x'] < 257)
                self.assertTrue(44 < result['y'] < 97)
                self.assertEqual(result['candidateCount'], 1)

    def test_scaled_images_keep_relative_voice_location(self):
        image = fixture()
        for scale in (.5, 1.5, 2, 3):
            with self.subTest(scale=scale):
                scaled = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
                result = locate_voice_bubble(scaled)
                self.assert_safe(scaled, result)
                self.assertTrue(104 * scale < result['x'] < 257 * scale)
                self.assertTrue(44 * scale < result['y'] < 97 * scale)

    def test_avatar_nickname_and_transcription_pill_without_voice_rejected(self):
        with self.assertRaisesRegex(VoiceGeometryError, 'missing'):
            locate_voice_bubble(fixture(bubble=False))

    def test_two_voice_sized_gray_shapes_ambiguous_not_largest_wins(self):
        with self.assertRaisesRegex(VoiceGeometryError, 'ambiguous'):
            locate_voice_bubble(fixture(second=True))

    def test_missing_black_frame_and_cropped_bubble_give_no_point(self):
        for image in (Image.new('RGB', (864, 115), 'white'), Image.new('RGB', (864, 115), 'black'),
                      fixture().crop((0, 0, 864, 80))):
            with self.subTest(size=image.size):
                with self.assertRaises(VoiceGeometryError):
                    locate_voice_bubble(image)

    def test_tinted_avatar_or_large_texture_not_uniform_gray_bubble(self):
        image = fixture(bubble=False)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((104, 44, 257, 97), radius=10, fill=(210, 160, 160))
        with self.assertRaisesRegex(VoiceGeometryError, 'missing'):
            locate_voice_bubble(image)

    def test_background_edge_occluded_rejected(self):
        image = fixture()
        ImageDraw.Draw(image).rectangle((840, 0, 863, 60), fill=(80, 160, 200))
        with self.assertRaisesRegex(VoiceGeometryError, 'background-unverified'):
            locate_voice_bubble(image)

    def test_invalid_size_and_object_rejected(self):
        for image in (None, object(), Image.new('RGB', (99, 115)), Image.new('RGB', (864, 39))):
            with self.subTest(image=type(image).__name__):
                with self.assertRaises(VoiceGeometryError):
                    locate_voice_bubble(image)

    def test_existing_real_friend_voice_row_is_pure_pixel_acceptance(self):
        # The initial 115px capture was overwritten during actual transcription.
        # This uniquely saved artifact is the real expanded 169px row, not a
        # reconstructed "original". Collapsed geometry is covered synthetically.
        path = Path(__file__).resolve().parents[2] / 'artifacts/group-voice-row-layout-20261102/incoming-voice-expanded-20261102.local.png'
        if not path.exists():
            self.skipTest('local real-row acceptance artifact unavailable')
        with Image.open(path) as image:
            result = locate_voice_bubble(image)
            self.assert_safe(image, result)
        self.assertEqual(result['voiceBounds'], [98, 44, 258, 98])
        self.assertEqual(result['bubbleBounds'], [105, 104, 456, 158])
        self.assertEqual(result['targetKind'], 'expanded-transcript')
        self.assertTrue(105 < result['x'] < 456)
        self.assertTrue(104 < result['y'] < 158)
        self.assertNotEqual(result['y'], 30)

    def test_expanded_short_long_and_dark_transcript_selects_lower_safe_patch(self):
        for options in ({}, {'lower_width': 50}, {'lower_height': 320}, {'dark': True}):
            with self.subTest(options=options):
                image = expanded_fixture(**options)
                result = locate_voice_bubble(image)
                self.assertEqual(result['targetKind'], 'expanded-transcript')
                self.assertGreaterEqual(result['y'], 104)
                self.assertEqual(result['voiceBounds'], [98, 44, 258, 98])
                self.assert_safe(image, result)

    def test_expanded_scaling_uses_upper_component_not_whole_row_height(self):
        image = expanded_fixture(lower_height=250)
        for scale in (.5, 1.5, 2):
            with self.subTest(scale=scale):
                scaled = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
                result = locate_voice_bubble(scaled)
                self.assertEqual(result['targetKind'], 'expanded-transcript')
                self.assertGreaterEqual(result['y'], 104 * scale)
                self.assert_safe(scaled, result)

    def test_unaligned_disjoint_and_three_body_regions_rejected(self):
        for options in ({'lower_left': 200}, {'lower_top': 140}):
            with self.subTest(options=options):
                with self.assertRaisesRegex(VoiceGeometryError, 'ambiguous'):
                    locate_voice_bubble(expanded_fixture(**options))
        image = expanded_fixture(lower_height=140)
        ImageDraw.Draw(image).rounded_rectangle((500, 104, 700, 157), radius=8, fill=(237, 237, 240))
        with self.assertRaisesRegex(VoiceGeometryError, 'ambiguous'):
            locate_voice_bubble(image)


if __name__ == '__main__':
    unittest.main()
