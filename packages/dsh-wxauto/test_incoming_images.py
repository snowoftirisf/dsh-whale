import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from incoming_images import download_image, capture_emotion
from test_message_watch import Row
import test_message_watch as fixtures


class ImageTests(fixtures.ReadFixtures):
    def test_missing_original_preview_uses_only_this_message_thumbnail_and_restores_settings(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            param=SimpleNamespace(DEFAULT_SAVE_PATH='unchanged')
            def fail(**kwargs): raise AttributeError("'NoneType' object has no attribute 'TextControl'")
            screenshot=SimpleNamespace(ScreenShot=lambda **kwargs:Image.new('RGB',(80,60),'blue'))
            message=SimpleNamespace(download=fail,control=screenshot,exists=lambda:True,roll_into_view=lambda:None)
            module=SimpleNamespace(crop_emotion_image=lambda frame,*args,**kwargs:frame)
            with patch.dict(sys.modules,{'wxauto4.param':SimpleNamespace(WxParam=param),'wxauto4.msgs.mtype':module}), \
                    patch('incoming_images.capture_message_row', return_value=Image.new('RGB',(80,60),'blue')):
                result=download_image(message,directory)
                self.assertEqual(result['source'],'visible-thumbnail')
                self.assertIn('不是完整原图',result['captureNote'])
                self.assertTrue(Path(result['path']).is_file())
                self.assertEqual(param.DEFAULT_SAVE_PATH,'unchanged')
                with self.assertRaisesRegex(RuntimeError,'target-mismatch'):
                    download_image(message,directory,ensure_chat=lambda:False)

    def test_media_ui_invalidation_cannot_discard_a_successfully_captured_new_image(self):
        invalid={'value':False}
        class OldRow(fixtures.Row):
            @property
            def Name(self):
                if invalid['value']:raise LookupError('UIA row recreated')
                return 'old'
            @Name.setter
            def Name(self,value):pass
        old=OldRow(1,'old');new=Row(2,'图片');new.ClassName='mmui::ChatBubbleReferItemView'
        monitor=fixtures.VoiceTests().monitor(new);monitor.image_directory='unused'
        monitor.controls=lambda:[old,new]
        monitor.cursor.seed('owner',[old])
        message=SimpleNamespace(attr='friend',type='image',sender='member')
        def download(*args,**kwargs):
            invalid['value']=True
            return {'path':'received.png'}
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:message)}),patch('incoming_images.download_image',side_effect=download):
            records=monitor.read('owner')
        self.assertEqual(records[0]['images'],[{'path':'received.png'}])

    def test_download_validates_bytes_and_restores_wxauto_setting(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root=Path(directory)
            param=SimpleNamespace(DEFAULT_SAVE_PATH='unchanged')
            def download(**kwargs):
                self.assertIs(kwargs.get('original'), True)
                output=Path(param.DEFAULT_SAVE_PATH)/'wechat.png'
                Image.new('RGB',(20,20),'red').save(output)
                return output
            message=SimpleNamespace(download=download)
            with patch.dict(sys.modules,{'wxauto4.param':SimpleNamespace(WxParam=param)}):
                first=download_image(message,root)
                second=download_image(message,root)
            self.assertEqual(param.DEFAULT_SAVE_PATH,'unchanged')
            self.assertNotEqual(first['path'],second['path'])
            self.assertEqual(first['sha256'],second['sha256'])
            self.assertTrue(first['originalRequested'])
            self.assertEqual((first['width'],first['height']),(20,20))
            self.assertTrue(Path(first['path']).is_file())
            self.assertEqual(list((root/'downloads').iterdir()),[])

    def test_outside_path_and_failed_download_are_not_accepted(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            param=SimpleNamespace(DEFAULT_SAVE_PATH='original')
            root=Path(directory)/'incoming'
            outside=Path(directory)/'private.png';outside.write_bytes(b'not an image')
            with patch.dict(sys.modules,{'wxauto4.param':SimpleNamespace(WxParam=param)}):
                for value in [None,False,outside]:
                    with self.assertRaises(RuntimeError):
                        download_image(SimpleNamespace(download=lambda **kwargs:value),root)
            self.assertEqual(param.DEFAULT_SAVE_PATH,'original')

    def test_new_image_is_received_once_and_self_images_never_download(self):
        row=Row(2,'图片');row.ClassName='mmui::ChatBubbleItemView'
        monitor=fixtures.VoiceTests().monitor(row);monitor.image_directory='unused'
        message=SimpleNamespace(attr='friend',type='image',sender='甲')
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:message)}), \
             patch('incoming_images.download_image',return_value={'path':'saved.png'}) as download:
            records=monitor.read('owner')
            self.assertEqual(records[0]['sender'],'甲')
            self.assertEqual(records[0]['images'],[{'path':'saved.png'}])
            self.assertEqual(monitor.read('owner'),[])
            download.assert_called_once()
        monitor=fixtures.VoiceTests().monitor(row);monitor.image_directory='unused';message.attr='self'
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:message)}), \
             patch('incoming_images.download_image') as download:
            self.assertEqual(monitor.read('owner'),[])
            download.assert_not_called()

    def test_actual_wechat_refer_bubbles_admit_images_and_sticker_picture_labels(self):
        for label,kind in [('图片','image'),('[图片]','emotion')]:
            with self.subTest(label=label):
                row=Row(2,label);row.ClassName='mmui::ChatBubbleReferItemView'
                monitor=fixtures.VoiceTests().monitor(row);monitor.image_directory='unused'
                message=SimpleNamespace(attr='friend',type=kind,sender='主人')
                method='download_image' if kind=='image' else 'capture_emotion'
                with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:message)}), \
                     patch('incoming_images.'+method,return_value={'path':'saved.png'}):
                    records=monitor.read('owner')
                    self.assertEqual(records[0]['images'],[{'path':'saved.png'}])
                    self.assertEqual(monitor.read('owner'),[])

    def test_image_download_failure_preserves_the_message_as_unknown(self):
        row=Row(2,'图片');row.ClassName='mmui::ChatBubbleItemView'
        monitor=fixtures.VoiceTests().monitor(row);monitor.image_directory='unused'
        message=SimpleNamespace(attr='friend',type='image',sender='甲')
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:message)}), \
             patch('incoming_images.download_image',side_effect=RuntimeError('download failed')):
            records=monitor.read('owner')
            self.assertIn('内容未知',records[0]['text'])
            self.assertEqual(records[0]['images'],[])
            self.assertEqual(monitor.read('owner'),[])

    def test_emotion_samples_one_message_and_stops_if_chat_changes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            frames=iter([Image.new('RGB',(30,30),color) for color in ['red','green','blue']])
            message=SimpleNamespace(exists=lambda:True,roll_into_view=lambda:None,
                control=SimpleNamespace(ScreenShot=lambda **kwargs:next(frames)))
            module=SimpleNamespace(crop_emotion_image=lambda image,*args,**kwargs:image)
            with patch.dict(sys.modules,{'wxauto4.msgs.mtype':module}),patch('incoming_images.time.sleep'):
                result=capture_emotion(message,directory,row_capture=lambda *args,**kwargs:next(frames))
                with Image.open(result['path']) as image:
                    self.assertEqual(image.size,(90,56))
                    self.assertEqual(image.getpixel((5,30)),(255,0,0))
                    self.assertEqual(image.getpixel((65,30)),(0,0,255))
                self.assertIn('不是完整原始GIF',result['captureNote'])
                with self.assertRaisesRegex(RuntimeError,'emotion-row-unavailable'):
                    capture_emotion(message,directory,ensure_chat=lambda:False)

    def test_emotion_row_keeps_sender_and_is_not_replayed(self):
        row=Row(2,'[动画表情]');row.ClassName='mmui::ChatBubbleItemView'
        monitor=fixtures.VoiceTests().monitor(row);monitor.image_directory='unused'
        message=SimpleNamespace(attr='friend',type='emotion',sender='乙')
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:message)}), \
             patch('incoming_images.capture_emotion',return_value={'path':'frames.png','captureNote':'采样'}) as capture:
            records=monitor.read('owner')
            self.assertEqual(records[0]['sender'],'乙')
            self.assertEqual(records[0]['mediaType'],'emotion')
            self.assertEqual(records[0]['images'][0]['path'],'frames.png')
            self.assertEqual(monitor.read('owner'),[])
            capture.assert_called_once()


if __name__=='__main__':unittest.main()
