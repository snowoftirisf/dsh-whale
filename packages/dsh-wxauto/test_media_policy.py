import tempfile
import unittest
from pathlib import Path
from media_policy import meme_file

class MediaPolicyTests(unittest.TestCase):
    def test_only_images_inside_enabled_pack_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pack = base / 'pack'; pack.mkdir()
            image = pack / 'meme.gif'; image.write_bytes(b'GIF89a')
            outside = base / 'private.png'; outside.write_bytes(b'private')
            document = pack / 'private.txt'; document.write_text('private')
            self.assertEqual(meme_file(image, [pack]), image.resolve())
            with self.assertRaisesRegex(RuntimeError, 'meme-file-denied'):
                meme_file(outside, [pack])
            with self.assertRaisesRegex(RuntimeError, 'meme-file-denied'):
                meme_file(document, [pack])
            with self.assertRaisesRegex(RuntimeError, 'meme-file-denied'):
                meme_file(image, [])
