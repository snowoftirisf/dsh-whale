from types import SimpleNamespace
import unittest
from native_voice_click_panel import send_click_panel,ClickPanelError


class Control:
    def __init__(self,rid,cls,name='',bounds=(0,0,150,100),children=()):
        self.rid=rid;self.ClassName=cls;self.Name=name;self.ProcessId=7;self.NativeWindowHandle=10
        self.IsEnabled=1;self.IsOffscreen=0;self.children=list(children);self.parent=None
        self.BoundingRectangle=SimpleNamespace(**dict(zip(('left','top','right','bottom'),bounds)))
    def GetRuntimeId(self):return [self.rid]
    def GetChildren(self):return self.children
    def GetParentControl(self):return self.parent
    def Exists(self,_):return True


def fixture(*,play_ok=True,readback_ok=True,missing_send=False,throw_send=False,cancel_stuck=False):
    events=[];state={'clock':0,'pressed':None,'send':0,'cancel':0,'foreground':10}
    button=Control(2,'mmui::XButton','发语音 ( 按住右 Alt )',(20,20,40,40))
    cancel=Control(4,'mmui::XButton','取消',(60,20,80,40))
    send=Control(5,'mmui::XMouseEventView','发送语音',(100,20,120,40))
    panel=Control(3,'mmui::ChatVoiceRecordView',children=[cancel]+([] if missing_send else [send]))
    root=Control(1,'mmui::MainWindow',children=[button]);root.ButtonControl=lambda **_:button
    def point(x,y):return button if x==30 else cancel if x==70 else send
    def press(x,y,waitTime):
        state['pressed']=point(x,y);events.append('down:'+state['pressed'].ClassName+':'+str(x))
        if state['pressed'] is send:
            state['send']+=1
            if throw_send:raise RuntimeError('partial send down')
        if state['pressed'] is cancel:state['cancel']+=1
    def release(**_):
        events.append('up')
        root.children=[button,panel] if state['pressed'] is button or state['pressed'] is cancel and cancel_stuck else [button]
    uia=SimpleNamespace(GetForegroundControl=lambda:SimpleNamespace(NativeWindowHandle=state['foreground']),IsKeyPressed=lambda _:False,SetCursorPos=lambda x,y:events.append('move'),
        ControlFromPoint=point,PressMouse=press,ReleaseMouse=release)
    instance=SimpleNamespace(root=lambda:root,ready=lambda:True,config={'verified':True,'buttonName':button.Name,'clientVersion':'4.2.0.12'},
        check_target=lambda *a,**kw:'matched',focus_input=lambda _:events.append('focus'))
    class Player:
        def wait_ready(self,timeout):events.append('ready');return {'ready':True}
        def play(self):
            assert panel in root.children;assert not missing_send;events.append('PLAY')
        def wait_played(self,timeout):events.append('played');return {'played':play_ok}
        def close(self):events.append('close')
    def confirm(before,timeout):events.append('readback');return readback_ok
    readback=SimpleNamespace(baseline=lambda:events.append('baseline') or {'old-self-rid'},confirm=confirm)
    def sleep(seconds):state['clock']+=seconds
    return SimpleNamespace(instance=instance,uia=uia,context={'target':'fixture','kind':'friend','commandId':'fixture'},
        player=Player(),readback=readback,sleep=sleep,clock=lambda:state['clock'],events=events,state=state,root=root)


class ClickPanelTests(unittest.TestCase):
    def run_candidate(self,f,**options):return send_click_panel(f.instance,f.uia,f.context,player=f.player,readback=f.readback,sleep=f.sleep,clock=f.clock,**options)
    def test_continuous_protocol_ready_then_panel_played_exact_send_and_new_self_readback(self):
        f=fixture();value=self.run_candidate(f)
        self.assertTrue(value['sent']);self.assertTrue(value['facts']['playerClosed']);self.assertTrue(value['facts']['sendAttempted'])
        self.assertTrue(value['facts']['newSelfVoiceVerified']);self.assertFalse(value['facts']['audioCaptureVerified'])
        self.assertLess(f.events.index('ready'),f.events.index('focus'));self.assertLess(f.events.index('PLAY'),f.events.index('played'))
        self.assertEqual(f.state['send'],1);self.assertEqual(f.state['cancel'],0);self.assertEqual(f.events[-1],'close')
    def test_missing_send_before_play_cancels_only_owned_panel_and_never_plays(self):
        f=fixture(missing_send=True)
        with self.assertRaises(ClickPanelError) as caught:self.run_candidate(f)
        self.assertEqual(str(caught.exception),'native-click-voice-pre-send-failed');self.assertTrue(caught.exception.facts['canceled'])
        self.assertNotIn('PLAY',f.events);self.assertEqual(f.state['cancel'],1);self.assertEqual(f.state['send'],0)
    def test_playback_failure_cancels_before_send_and_closes_owned_player(self):
        f=fixture(play_ok=False)
        with self.assertRaises(ClickPanelError) as caught:self.run_candidate(f)
        self.assertTrue(caught.exception.facts['canceled']);self.assertFalse(caught.exception.facts['sendAttempted'])
        self.assertEqual(f.state['cancel'],1);self.assertEqual(f.state['send'],0);self.assertEqual(f.events[-1],'close')
    def test_send_attempt_flag_precedes_partial_input_exception_and_never_cancels(self):
        f=fixture(throw_send=True)
        with self.assertRaises(ClickPanelError) as caught:self.run_candidate(f)
        self.assertEqual(str(caught.exception),'native-click-voice-send-outcome-unknown');self.assertTrue(caught.exception.facts['sendAttempted'])
        self.assertFalse(caught.exception.facts['cancelAttempted']);self.assertEqual(f.state['send'],1);self.assertEqual(f.state['cancel'],0)
        self.assertEqual(f.events[-1],'close')
    def test_unknown_readback_never_retries_send_or_cancels_after_input(self):
        f=fixture(readback_ok=False)
        with self.assertRaisesRegex(ClickPanelError,'send-outcome-unknown'):self.run_candidate(f)
        self.assertEqual(f.state['send'],1);self.assertEqual(f.state['cancel'],0);self.assertEqual(f.events.count('PLAY'),1)
    def test_bad_initial_hit_never_plays_or_cancels_unowned_panel(self):
        f=fixture();f.uia.ControlFromPoint=lambda *a:Control(99,'mmui::Other')
        with self.assertRaises(ClickPanelError) as caught:self.run_candidate(f)
        self.assertFalse(caught.exception.facts['sendAttempted']);self.assertFalse(caught.exception.facts['cancelAttempted'])
        self.assertNotIn('PLAY',f.events);self.assertEqual(f.state['send'],0);self.assertEqual(f.state['cancel'],0)
    def test_cancel_acceptance_mode_plays_once_then_cancels_without_any_readback_or_send(self):
        f=fixture();f.readback.baseline=lambda:(_ for _ in ()).throw(AssertionError('no sent readback in cancel mode'))
        value=self.run_candidate(f,confirm_send=False)
        self.assertTrue(value['canceled']);self.assertEqual(value['playback'],{'played':True});self.assertTrue(value['facts']['playerClosed'])
        self.assertEqual(f.events.count('PLAY'),1);self.assertEqual(f.state['cancel'],1);self.assertEqual(f.state['send'],0)
        self.assertFalse(value['facts']['sendAttempted']);self.assertNotIn('readback',f.events)
    def test_cancel_acceptance_unknown_never_reclicks_cancel_or_attempts_send(self):
        f=fixture(cancel_stuck=True)
        with self.assertRaisesRegex(ClickPanelError,'cancel-outcome-unknown') as caught:self.run_candidate(f,confirm_send=False)
        self.assertTrue(caught.exception.facts['cancelAttempted']);self.assertFalse(caught.exception.facts['canceled'])
        self.assertEqual(f.state['cancel'],1);self.assertEqual(f.state['send'],0);self.assertEqual(f.events[-1],'close')
    def test_false_mode_never_takes_send_branch_even_when_send_readback_is_available(self):
        f=fixture(throw_send=True)
        self.assertTrue(self.run_candidate(f,confirm_send=False)['canceled']);self.assertEqual(f.state['send'],0)

    def coarse_hit(self,f,*,wrong_label=False,foreign_panel=False):
        old=f.uia.ControlFromPoint
        def hit(x,y):
            if x!=110:return old(x,y)
            panel=f.root.children[1] if not foreign_panel else Control(999,'mmui::ChatVoiceRecordView')
            container=Control(90,'mmui::XMouseEventView','wrong action' if wrong_label else '播放录音')
            layer=Control(91,'mmui::XHBoxView');layer.parent=container;container.parent=panel
            return layer
        f.uia.ControlFromPoint=hit

    def test_observed_coarse_layer_requires_exact_owned_panel_and_never_invokes(self):
        f=fixture();send=f.uia.ControlFromPoint(110,30)
        send.GetInvokePattern=lambda:(_ for _ in ()).throw(AssertionError('no Invoke fallback'))
        self.coarse_hit(f)
        result=self.run_candidate(f)
        self.assertTrue(result['sent']);self.assertEqual(f.state['send'],1);self.assertEqual(f.state['cancel'],0)
        self.assertEqual(result['facts']['sendAction'],'physical-observed-preview-layer')

    def test_same_pid_coarse_layer_with_wrong_role_or_panel_never_sends(self):
        for options in ({'wrong_label':True},{'foreign_panel':True}):
            f=fixture();self.coarse_hit(f,**options)
            with self.assertRaisesRegex(ClickPanelError,'pre-send-failed') as caught:self.run_candidate(f)
            self.assertFalse(caught.exception.facts['sendAttempted']);self.assertTrue(caught.exception.facts['canceled'])
            self.assertEqual(f.state['send'],0);self.assertEqual(f.state['cancel'],1)

    def test_cancel_probe_validates_observed_layer_without_sending(self):
        f=fixture();self.coarse_hit(f)
        result=self.run_candidate(f,confirm_send=False)
        self.assertTrue(result['canceled']);self.assertTrue(result['facts']['sendActionValidated'])
        self.assertEqual(f.state['send'],0);self.assertEqual(f.state['cancel'],1)

    def test_early_recording_coarse_main_view_requires_pinned_window_and_panel_bounds(self):
        f=fixture();old=f.uia.ControlFromPoint
        view=Control(88,'mmui::XView');view.parent=f.root;view.GetTopLevelControl=lambda:f.root
        f.uia.ControlFromPoint=lambda x,y:view if x==110 else old(x,y)
        result=self.run_candidate(f)
        self.assertTrue(result['sent']);self.assertEqual(f.state['send'],1)
        self.assertEqual(result['facts']['sendAction'],'physical-owned-panel-bounds')

    def test_coarse_main_view_other_window_or_unpinned_version_never_sends(self):
        for mode in ('other-window','other-version'):
            f=fixture();old=f.uia.ControlFromPoint
            view=Control(88,'mmui::XView');view.parent=f.root
            top=Control(89,'mmui::MainWindow');top.NativeWindowHandle=99
            view.GetTopLevelControl=lambda:top if mode=='other-window' else f.root
            if mode=='other-version':f.instance.config['clientVersion']='different'
            f.uia.ControlFromPoint=lambda x,y:view if x==110 else old(x,y)
            with self.assertRaisesRegex(ClickPanelError,'pre-send-failed') as caught:self.run_candidate(f)
            self.assertFalse(caught.exception.facts['sendAttempted']);self.assertEqual(f.state['send'],0)
            self.assertTrue(caught.exception.facts['canceled'])


if __name__=='__main__':unittest.main()
