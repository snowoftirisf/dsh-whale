import unittest
from PIL import Image, ImageDraw
from incoming_images import image_click_point


def row(nickname=True, picture=(70, 30, 330, 230)):
    frame = Image.new('RGB', (860, 260), (237, 237, 237))
    draw = ImageDraw.Draw(frame)
    draw.rectangle((12, 4, 52, 44), fill=(40, 120, 200))          # avatar
    if nickname:
        draw.rectangle((70, 6, 150, 18), fill=(150, 150, 150))   # nickname line
    draw.rectangle(picture, fill=(200, 60, 60))                  # picture
    return frame


class ImageClickPointTest(unittest.TestCase):
    def test_group_row_clicks_picture_not_avatar_or_nickname(self):
        x, y = image_click_point(row())
        self.assertTrue(70 < x < 330 and 30 < y < 230)

    def test_private_row_without_nickname(self):
        x, y = image_click_point(row(nickname=False, picture=(70, 4, 300, 200)))
        self.assertTrue(70 < x < 300 and 4 < y < 200)

    def test_no_picture_returns_none(self):
        frame = Image.new('RGB', (860, 260), (237, 237, 237))
        ImageDraw.Draw(frame).rectangle((12, 4, 52, 44), fill=(40, 120, 200))
        self.assertIsNone(image_click_point(frame))


if __name__ == '__main__':
    unittest.main()
