import unittest
import quote_render as q


class ImageAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.floor = q.IMAGE_ADMISSION_FLOOR_SECONDS
        q.IMAGE_ADMISSION_FLOOR_SECONDS = 1000

    def tearDown(self):
        q.IMAGE_ADMISSION_FLOOR_SECONDS = self.floor

    def test_photo_after_floor_becomes_marker_with_file_md5(self):
        packed = b'\x12\x22"" 01c02553e51032df1d9a6084009ead07X'
        text = q.admit_image(3, packed, 2000, 'a' * 32)
        self.assertEqual(text, '[收到一张微信图片]' + q.IMAGE_MARK + 'a' * 32 + '|01c02553e51032df1d9a6084009ead07）')

    def test_older_rows_other_types_and_bad_dirs_stay_invisible(self):
        self.assertIsNone(q.admit_image(3, b'01c02553e51032df1d9a6084009ead07', 999, 'a' * 32))
        self.assertIsNone(q.admit_image(1, b'01c02553e51032df1d9a6084009ead07', 2000, 'a' * 32))
        self.assertIsNone(q.admit_image(3, b'01c02553e51032df1d9a6084009ead07', 2000, 'not-a-dir'))

    def test_missing_file_record_is_stated_not_guessed(self):
        self.assertIn('内容未知', q.admit_image(3, None, 2000, 'a' * 32))


if __name__ == '__main__':
    unittest.main()
