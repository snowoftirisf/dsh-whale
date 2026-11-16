"""Keep SDK transcription, replacing its fixed group-voice menu click only."""
import math
import numbers
from message_direction import _state,_image_status


def transcribe_group_voice(message,wx,*,target,uia=None,locator=None,detector=None):
    if uia is None:
        from wxauto4 import uia
    if locator is None:
        from incoming_voice_geometry import locate_voice_bubble
        locator=locate_voice_bubble
    if detector is None:
        from wxauto4.utils.tools import detect_message_direction
        detector=lambda image:detect_message_direction(image,tolerance=8)
    root=wx._api.control;box=wx._api._chat_api.msgbox;control=message.control
    if (message.attr!='friend' or message.type!='voice' or root.ClassName!='mmui::MainWindow'
            or control.ClassName not in ('mmui::ChatVoiceItemView','mmui::ChatVoiceMessageItemView')):
        raise RuntimeError('voice-transcription-row-unverified')
    def same_chat():
        info=wx.ChatInfo()
        if info.get('chat_name')!=target or info.get('chat_type')!='group':
            raise RuntimeError('voice-transcription-chat-changed')
    same_chat();attempts=0
    def right_click():
        nonlocal attempts
        # SDK transcription opens the voice menu, then opens the expanded
        # transcription's copy menu. Revalidate every SDK-requested step.
        if attempts>=3:raise RuntimeError('voice-transcription-click-budget-exceeded')
        same_chat();before=_state(control,root,uia,box)
        if before[-1]!=(root.NativeWindowHandle,root.ProcessId):
            raise RuntimeError('voice-transcription-foreground-required')
        image=control.ScreenShot(return_img=True)
        if _state(control,root,uia,box)!=before:raise RuntimeError('voice-transcription-row-changed')
        if not _image_status(image):image=uia.ImageGrab.grab(bbox=before[0][1],all_screens=True)
        if not _image_status(image) or _state(control,root,uia,box)!=before:
            raise RuntimeError('voice-transcription-capture-unverified')
        bounds=before[0][1]
        if image.size!=(bounds[2]-bounds[0],bounds[3]-bounds[1]):
            raise RuntimeError('voice-transcription-image-size-mismatch')
        direction=detector(image)
        if (not isinstance(direction,(list,tuple)) or len(direction)!=2 or direction[0]!='left'
                or isinstance(direction[1],bool) or not isinstance(direction[1],numbers.Real)
                or not math.isfinite(direction[1])):
            raise RuntimeError('voice-transcription-incoming-unverified')
        point=locator(image);x,y=point['x'],point['y']
        if type(x) is not int or type(y) is not int or not 0<=x<image.width or not 0<=y<image.height:
            raise RuntimeError('voice-transcription-point-invalid')
        same_chat()
        if _state(control,root,uia,box)!=before:raise RuntimeError('voice-transcription-row-changed')
        attempts+=1
        control.RightClick(pos='lefttop',x=x,y=y)
    previous=message.right_click
    attributes=getattr(message,'__dict__',{})
    had_override='right_click' in attributes
    message.right_click=right_click
    try:
        # Existing SDK chooses the menu action, waits for transcription and
        # reads its result. Already-transcribed messages need no menu click.
        return message.to_text()
    finally:
        if getattr(message,'right_click',None) is right_click:
            if had_override:message.right_click=previous
            else:del message.right_click
