"""Pillow images and fake controls only; never import wxauto or capture a screen."""
import unittest
import sys
from unittest.mock import patch
from types import SimpleNamespace
from PIL import Image

from message_direction import read_message_direction, MessageDirectionError


class Control:
    def __init__(self, cls, bounds, rid, image=None):
        self.ClassName,self.Name=cls,'private message text never logged'
        self.ProcessId,self.NativeWindowHandle=100,123
        self.BoundingRectangle=SimpleNamespace(**dict(zip(('left','top','right','bottom'),bounds)))
        self.IsOffscreen=0;self.rid=rid;self.image=image;self.captures=[];self.after=lambda:None
    def GetRuntimeId(self):return self.rid
    def ScreenShot(self,**kwargs):
        self.captures.append(kwargs);self.after();return self.image


class DirectionTests(unittest.TestCase):
    def setUp(self):
        self.good=Image.new('RGB',(80,30),(20,80,120));self.black=Image.new('RGBA',(80,30),(0,0,0,255))
        self.root=Control('mmui::MainWindow',(0,0,500,500),[1])
        self.row=Control('mmui::ChatTextItemView',(10,50,90,80),[2],self.good)
        self.box=Control('mmui::RecyclerListView',(0,40,500,400),[3])
        self.front=SimpleNamespace(NativeWindowHandle=123,ProcessId=100)
        self.grabs=[];self.detector_calls=[];self.after_grab=lambda:None
        def grab(**kwargs):self.grabs.append(kwargs);self.after_grab();return self.good
        self.uia=SimpleNamespace(GetForegroundControl=lambda:self.front,ImageGrab=SimpleNamespace(grab=grab))
        def detect(image):self.detector_calls.append(image);return ('right',12.5)
        self.detector=detect
    def read(self,**kwargs):return read_message_direction(self.row,self.root,uia=self.uia,detector=self.detector,**kwargs)
    def refused(self,code,**kwargs):
        with self.assertRaisesRegex(MessageDirectionError,code) as raised:self.read(**kwargs)
        self.assertNotIn('private message',str(raised.exception))

    def test_good_sdk_capture_no_screen_grab_or_message_construction(self):
        self.assertEqual(self.read(),'self');self.assertEqual(self.grabs,[])
        self.assertEqual(self.row.captures,[{'return_img':True}])
        self.detector=lambda image:('left',0)
        self.assertEqual(self.read(),'friend')

    def test_default_installed_detector_has_measured_edge_noise_tolerance(self):
        calls=[]
        def detector(image,**kwargs):calls.append(kwargs);return ('right',33)
        with patch.dict(sys.modules,{'wxauto4.utils.tools':SimpleNamespace(detect_message_direction=detector)}):
            self.assertEqual(read_message_direction(self.row,self.root,uia=self.uia),'self')
        self.assertEqual(calls,[{'tolerance':8}])

    def test_nonblack_sdk_capture_can_work_without_activating_external_foreground(self):
        self.front=SimpleNamespace(NativeWindowHandle=456,ProcessId=200)
        self.assertEqual(self.read(),'self');self.assertEqual(self.grabs,[])

    def test_black_sdk_fallback_exact_visible_bbox_and_all_screens(self):
        self.row.image=self.black
        self.assertEqual(self.read(msgbox=self.box),'self')
        self.assertEqual(self.grabs,[{'bbox':(10,50,90,80),'all_screens':True}])

    def test_black_sdk_external_foreground_refuses_without_grab(self):
        self.row.image=self.black;self.front.NativeWindowHandle=456
        self.refused('screen-foreground-required');self.assertEqual(self.grabs,[])

    def test_foreground_switch_during_either_capture_refuses(self):
        self.row.after=lambda:setattr(self.front,'NativeWindowHandle',456)
        self.refused('capture-changed');self.assertEqual(self.detector_calls,[])
        self.front.NativeWindowHandle=123;self.row.after=lambda:None;self.row.image=self.black
        self.after_grab=lambda:setattr(self.front,'NativeWindowHandle',456)
        self.refused('capture-changed');self.assertEqual(self.detector_calls,[])

    def test_row_runtime_bounds_class_or_name_changes_refuse(self):
        for mutate in [lambda:setattr(self.row,'rid',[9]),lambda:setattr(self.row.BoundingRectangle,'right',100),
                       lambda:setattr(self.row,'ClassName','changed'),lambda:setattr(self.row,'Name','changed')]:
            self.setUp();self.row.after=mutate
            self.refused('capture-changed');self.assertEqual(self.detector_calls,[])

    def test_invalid_rect_and_offscreen_refuse_before_any_capture(self):
        for value in [0,float('nan'),True]:
            self.setUp();self.row.BoundingRectangle.right=value
            self.refused('invalid-rect');self.assertEqual(self.row.captures,[])
        for value in [1,None,'0']:
            self.setUp();self.row.IsOffscreen=value
            self.refused('not-visible');self.assertEqual(self.row.captures,[])

    def test_row_outside_root_or_trusted_viewport_refuses(self):
        self.row.BoundingRectangle.left=-1
        self.refused('outside-view');self.assertEqual(self.row.captures,[])
        self.row.BoundingRectangle.left=10;self.box.BoundingRectangle.top=60
        self.refused('outside-view',msgbox=self.box)

    def test_black_fallback_or_invalid_image_refuses(self):
        self.row.image=self.black;self.good=self.black
        self.refused('black-image')
        self.row.image=None
        self.refused('invalid-image')

    def test_unknown_direction_nonfinite_or_exception_is_fixed_error(self):
        for result in [('center',1),('left',float('nan')),('right',float('inf')),('right',True),None]:
            self.detector=lambda image,result=result:result
            self.refused('direction-unknown')
        def broken(image):raise RuntimeError('private message text')
        self.detector=broken;self.refused('unreadable')

    def test_detector_time_row_change_is_rejected(self):
        def detect(image):self.row.rid=[99];return ('right',1)
        self.detector=detect;self.refused('capture-changed')

    def test_real_numeric_distance_subclass_is_accepted_without_string_coercion(self):
        class Distance(float):pass
        self.detector=lambda image:('right',Distance(1.5))
        self.assertEqual(self.read(),'self')
        self.detector=lambda image:('right','1.5')
        self.refused('direction-unknown')


def edge_scan(image, tolerance=8):
    """Same left/right first-nonuniform-column scan as wxauto4's detector."""
    image=image.convert('RGB');w,h=image.size;px=image.load();y0=(h-int(h*.8))//2;y1=y0+int(h*.8)
    uniform=lambda x:all(max(abs(a-b) for a,b in zip(px[x,y],px[x,y0]))<=tolerance for y in range(y0,y1))
    left=next((x for x in range(w) if not uniform(x)),float('inf'))
    right=next((i for i,x in enumerate(range(w-1,-1,-1)) if not uniform(x)),float('inf'))
    return ('left',float(left)) if left<=right else ('right',float(right))


class ScrollbarTests(unittest.TestCase):
    """Measured at 150%: avatars 33px in from their edge, scrollbar 3-14px in from the right."""
    def row(self, incoming, scrollbar):
        from PIL import ImageDraw
        image=Image.new('RGB',(864,169),(250,250,250));draw=ImageDraw.Draw(image)
        if incoming:
            draw.rectangle((33,0,86,53),fill=(40,120,200));draw.rectangle((110,0,500,150),fill=(255,255,255))
        else:
            draw.rectangle((864-87,0,864-34,53),fill=(40,120,200));draw.rectangle((185,0,755,150),fill=(160,234,150))
        if scrollbar:
            draw.rectangle((864-15,30,864-4,140),fill=(190,190,190))
        return image

    def read(self, image, dpi=144):
        row=Control('mmui::ChatImageItemView',(0,0,864,169),[2],image)
        root=Control('mmui::MainWindow',(0,0,1000,1000),[1])
        uia=SimpleNamespace(GetForegroundControl=lambda:SimpleNamespace(NativeWindowHandle=123,ProcessId=100))
        with patch('message_direction._window_scale',return_value=dpi/96):
            return read_message_direction(row,root,uia=uia,detector=edge_scan)

    def test_scrollbar_no_longer_turns_incoming_row_into_self(self):
        self.assertEqual(edge_scan(self.row(True,True))[0],'right')  # the original failure
        self.assertEqual(self.read(self.row(True,True)),'friend')
        self.assertEqual(self.read(self.row(True,False)),'friend')

    def test_own_rows_stay_self_with_or_without_scrollbar(self):
        self.assertEqual(self.read(self.row(False,True)),'self')
        self.assertEqual(self.read(self.row(False,False)),'self')


if __name__=='__main__':unittest.main()
