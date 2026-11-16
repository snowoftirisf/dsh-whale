"""Click-panel transport selected only by explicit recorderMode configuration.

Player is an owned adapter for the existing continuous stream protocol:
wait_ready(timeout)->{'ready':True}, play(), wait_played(timeout)->{'played':True},
close(). Readback baseline()/confirm(before,timeout) must verify a NEW SELF voice
row on the same route. UI presence alone never proves microphone audio content.
Import/construction performs no UI/audio/process work. No retry is implemented.
"""
import math
import time
import re


class ClickPanelError(RuntimeError):
    def __init__(self,code,facts):super().__init__(code);self.facts=facts


def _need(value):
    if not value:raise RuntimeError('click-panel-unverified')


def _scan(root,pid):
    pending=[(root,0)];seen=set();panels=[];complete=True;visited=0
    while pending and visited<400:
        node,depth=pending.pop();visited+=1
        try:
            _need(node.ProcessId==pid);rid=tuple(node.GetRuntimeId());_need(rid and rid not in seen);seen.add(rid)
            cls=node.ClassName;off=node.IsOffscreen;_need(isinstance(cls,str) and type(off)in(bool,int) and off in(0,1))
            if off:continue
            if 'MessageList' in cls or cls=='mmui::MessageView' or 'TextItem' in cls or re.search(r'Chat.*(?:Item|Message).*View',cls):continue
            if cls=='mmui::ChatVoiceRecordView':panels.append(node)
            children=node.GetChildren()
            if children and depth>=26:complete=False;continue
            room=400-visited-len(pending)
            if len(children)>room:complete=False
            pending.extend((c,depth+1) for c in reversed(children[:max(0,room)]))
        except Exception:complete=False
    return {'complete':complete and not pending,'panels':panels}


def _controls(panel,pid,*,require_send=True):
    todo=[(panel,0)];seen=set();cancel=[];send=[];count=0
    while todo:
        node,depth=todo.pop();count+=1;_need(count<=80 and node.ProcessId==pid)
        rid=tuple(node.GetRuntimeId());_need(rid and rid not in seen);seen.add(rid)
        _need(type(node.IsOffscreen)in(bool,int) and node.IsOffscreen in (0,1))
        if node.IsOffscreen:continue
        if node.ClassName in ('mmui::XButton','mmui::XMouseEventView'):
            if node.ClassName=='mmui::XButton' and node.Name=='取消':cancel.append(node)
            if node.ClassName=='mmui::XMouseEventView' and node.Name=='发送语音':send.append(node)
        children=node.GetChildren();_need(not children or depth<8);todo.extend((c,depth+1) for c in children)
    _need(len(cancel)==1 and (not require_send or len(send)==1))
    return cancel[0],send[0] if len(send)==1 else None


def send_click_panel(instance,uia,context,*,player,readback,confirm_send=True,sleep=time.sleep,clock=time.monotonic):
    facts={'transportVersion':1,'phase':'preflight','recordingUiObserved':False,'audioCaptureVerified':False,'playRequested':False,
           'sendAttempted':False,'newSelfVoiceVerified':False,'cancelAttempted':False,'cancelPhaseStarted':False,'canceled':False,'playerClosed':False}
    panel_id=None;root=None;identity=None
    def guard(required=False):
        _need(instance.root() is root and (root.ProcessId,root.NativeWindowHandle,tuple(root.GetRuntimeId()))==identity
              and root.ClassName=='mmui::MainWindow' and uia.GetForegroundControl().NativeWindowHandle==identity[1])
        instance.check_target(context,required=required)
    def owned_panel():
        guard();value=_scan(root,identity[0]);_need(value['complete'] and len(value['panels'])==1)
        panel=value['panels'][0];_need(tuple(panel.GetRuntimeId())==panel_id);return panel
    def click(control,cls,name,before_input,*,validate_only=False):
        # Generalize only the actually observed Send XMouseEventView; never use
        # generic Invoke or click an unrecognized class/name/coordinate.
        guard();pid=identity[0]
        facts['lastClickRole']='cancel' if name=='取消' else 'send' if name=='发送语音' else 'open'
        def snapshot():
            _need(control.ProcessId==pid and control.ClassName==cls and control.Name==name
                  and type(control.IsEnabled)in(bool,int) and control.IsEnabled==1
                  and type(control.IsOffscreen)in(bool,int) and control.IsOffscreen==0)
            rect=control.BoundingRectangle;outer=root.BoundingRectangle
            box=tuple(getattr(rect,k) for k in ('left','top','right','bottom'))
            edge=tuple(getattr(outer,k) for k in ('left','top','right','bottom'))
            _need(all(type(v)in(int,float) and math.isfinite(v) for v in box+edge)
                and edge[0]<=box[0]<box[2]<=edge[2] and edge[1]<=box[1]<box[3]<=edge[3])
            return tuple(control.GetRuntimeId()),box
        original=snapshot();_need(original[0]);box=original[1];x=math.floor((box[0]+box[2])/2);y=math.floor((box[1]+box[3])/2)
        if cls=='mmui::XMouseEventView' and name=='发送语音':
            # In the observed 4.2.0.12 UI, ElementFromPoint returns the sibling
            # preview layer over the visible green arrow. Invoke was a live
            # no-confirmation attempt. Use a physical click only after checking
            # the exact observed layer ancestry to OUR recorder panel below.
            mouse=uia.IsKeyPressed(0x01);_need(type(mouse)in(bool,int) and mouse==0)
            guard();uia.SetCursorPos(x,y);sleep(.1);guard()
        facts['lastClickAction']='physical-hit-tested'
        hit=uia.ControlFromPoint(x,y);seen=set();matched=False;chain=[]
        if hit is not None:
            hit_class=hit.ClassName
            facts['lastHitClass']=hit_class if isinstance(hit_class,str) and re.fullmatch('[A-Za-z0-9_:.-]{1,120}',hit_class) else 'unavailable'
            facts['lastHitOwnerMatched']=hit.ProcessId==pid
        for _ in range(8):
            _need(hit is not None and hit.ProcessId==pid);rid=tuple(hit.GetRuntimeId());_need(rid and rid not in seen);seen.add(rid)
            chain.append((hit,rid))
            if rid==original[0]:matched=True;break
            if rid==panel_id and cls=='mmui::XMouseEventView' and name=='发送语音':break
            if rid==identity[2]:break
            hit=hit.GetParentControl()
        if not matched and cls=='mmui::XMouseEventView' and name=='发送语音' and len(chain)>=3:
            bottom,container,owner=chain[:3]
            matched=(bottom[0].ClassName=='mmui::XHBoxView'
                and container[0].ClassName=='mmui::XMouseEventView' and container[0].Name=='播放录音'
                and owner[0].ClassName=='mmui::ChatVoiceRecordView' and owner[1]==panel_id)
            if matched:facts['sendAction']='physical-observed-preview-layer'
        if not matched and cls=='mmui::XMouseEventView' and name=='发送语音' and chain and chain[0][0].ClassName=='mmui::XView':
            # Real early-recording screenshot e1629059 confirms the green arrow
            # is visible inside this exact small right-hand panel rectangle,
            # while Qt's ElementFromPoint reports the main-window XView.
            # Do not accept an arbitrary same-PID popup, other window or point.
            top=chain[0][0].GetTopLevelControl();panel=owned_panel();rect=panel.BoundingRectangle
            area=tuple(getattr(rect,k) for k in ('left','top','right','bottom'))
            matched=(instance.config.get('clientVersion')=='4.2.0.12'
                and top.ProcessId==pid and top.ClassName=='mmui::MainWindow' and top.NativeWindowHandle==identity[1]
                and all(type(v)in(int,float) and math.isfinite(v) for v in area)
                and area[0]+(area[2]-area[0])*.6<=box[0]<box[2]<=area[2]
                and area[1]<=box[1]<box[3]<=area[3]
                and 8<=box[2]-box[0]<=128 and 8<=box[3]-box[1]<=128)
            if matched:facts['sendAction']='physical-owned-panel-bounds'
        if matched and cls=='mmui::XMouseEventView' and name=='发送语音' and not facts.get('sendAction'):
            facts['sendAction']='physical-exact-control'
        _need(matched and snapshot()==original);guard()
        mouse=uia.IsKeyPressed(0x01);_need(type(mouse)in(bool,int) and mouse==0)
        if validate_only:
            return True
        before_input()  # MUST precede any possible native down/up input.
        try:uia.PressMouse(x,y,waitTime=0);sleep(.05)
        finally:uia.ReleaseMouse(waitTime=0)
    def cancel_owned():
        facts['cancelPhaseStarted']=True
        cancel,_=_controls(owned_panel(),identity[0],require_send=False)
        click(cancel,'mmui::XButton','取消',lambda:facts.update(cancelAttempted=True))
        deadline=clock()+1
        while True:
            guard();value=_scan(root,identity[0]);_need(value['complete'])
            if not value['panels']:facts['canceled']=True;return
            _need(clock()<deadline);sleep(.1)
    try:
        _need(type(confirm_send)is bool)
        _need(instance.ready() is True and player.wait_ready(timeout=15).get('ready') is True)
        root=instance.root();identity=(root.ProcessId,root.NativeWindowHandle,tuple(root.GetRuntimeId()))
        _need(type(identity[0])is int and identity[0]>0 and type(identity[1])is int and identity[1]>0 and identity[2]
              and root.ClassName=='mmui::MainWindow')
        instance.check_target(context,required=True);instance.focus_input(uia);guard(True)
        before_ui=_scan(root,identity[0]);_need(before_ui['complete'] and not before_ui['panels'])
        before=readback.baseline() if confirm_send else None
        button=root.ButtonControl(Name=instance.config['buttonName']);_need(button.Exists(0))
        click(button,'mmui::XButton',instance.config['buttonName'],lambda:None)
        deadline=clock()+5
        while True:
            guard();value=_scan(root,identity[0]);_need(value['complete'] and len(value['panels'])<=1)
            if value['panels']:
                panel=value['panels'][0];panel_id=tuple(panel.GetRuntimeId());break
            _need(clock()<deadline);sleep(.1)
        facts['phase']='recording-controls'
        cancel,send=_controls(owned_panel(),identity[0]);send_id=tuple(send.GetRuntimeId());facts['recordingUiObserved']=True
        facts['phase']='playback'
        guard();facts['playRequested']=True;player.play();played=player.wait_played(timeout=58);_need(played.get('played') is True)
        facts['phase']='post-playback-controls'
        cancel,send=_controls(owned_panel(),identity[0]);facts['sendControlSameRid']=tuple(send.GetRuntimeId())==send_id
        _need(facts['sendControlSameRid'])
        facts['phase']='send-hit-test'
        if not confirm_send:
            facts['sendActionValidated']=click(send,'mmui::XMouseEventView','发送语音',lambda:None,validate_only=True)
            cancel_owned()
            return {'canceled':True,'playback':played,'facts':facts}
        click(send,'mmui::XMouseEventView','发送语音',lambda:facts.update(sendAttempted=True))
        facts['phase']='sent-readback'
        _need(readback.confirm(before,timeout=8) is True)
        facts['newSelfVoiceVerified']=True
        return {'sent':True,'facts':facts}
    except Exception:
        facts['failureBeforeCleanup']={key:facts.get(key) for key in
            ('phase','sendControlSameRid','lastClickRole','lastHitClass','lastHitOwnerMatched','sendInvokeSupported','sendAction')}
        if facts['sendAttempted']:raise ClickPanelError('native-click-voice-send-outcome-unknown',facts) from None
        if facts['cancelPhaseStarted'] and not facts['canceled']:raise ClickPanelError('native-click-voice-cancel-outcome-unknown',facts) from None
        if panel_id is not None:
            try:
                cancel_owned()
            except Exception:raise ClickPanelError('native-click-voice-cancel-outcome-unknown',facts) from None
        raise ClickPanelError('native-click-voice-pre-send-failed',facts) from None
    finally:
        try:player.close();facts['playerClosed']=True
        except Exception:pass  # Never replace a confirmed/unknown external outcome.
