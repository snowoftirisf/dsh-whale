"""Version-gated wxauto bound-chat monitors, without a second media parser.

No UI operation occurs on import or for the default/unverified factory path.
Only a trusted *live* receipt for the exact client/library/routes enables this
mode. Tests use fake windows; they never create a production verification flag.
"""
from datetime import datetime
import re
from message_watch import MainWindowMonitor


def receipt_matches(receipt,routes,*,current_version,wxauto_version,monitor_options=None,require_native_voice=False):
    options=monitor_options or {}
    if not isinstance(receipt,dict) or receipt.get('verified') is not True:
        return False,'independent-live-verification-missing'
    if receipt.get('verificationKind')!='live' or receipt.get('mode')!='independent-windows':
        return False,'independent-live-verification-missing'
    if not isinstance(current_version,str) or not re.fullmatch(r'\d+\.\d+\.\d+\.\d+',current_version):
        return False,'independent-client-version-unknown'
    if receipt.get('clientVersion')!=current_version or not wxauto_version or receipt.get('wxautoVersion')!=wxauto_version:
        return False,'independent-version-not-verified'
    try:
        stamp=datetime.fromisoformat(str(receipt.get('verifiedAt','')).replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError('timezone missing')
    except ValueError:return False,'independent-live-verification-missing'
    if not isinstance(receipt.get('evidence'),list) or not receipt['evidence'] or not all(isinstance(x,str) and x for x in receipt['evidence']):
        return False,'independent-live-evidence-missing'
    classes=receipt.get('windowClasses')
    if not isinstance(classes,list) or not classes or any(not isinstance(x,str) or not x or x=='mmui::MainWindow' for x in classes):
        return False,'independent-window-class-unverified'
    verified_routes=receipt.get('routes')
    if not isinstance(verified_routes,list):return False,'independent-routes-unverified'
    verified={(x.get('target'),x.get('kind')) for x in verified_routes if isinstance(x,dict) and x.get('passed') is True}
    if any((target,kind) not in verified for target,kind in routes.items()):return False,'independent-routes-unverified'
    required={'passiveRead','targetBinding','textReceive','textSend','fileSend','closedWindowStopsSend','identityChangeStopsSend'}
    if options.get('receive_voice'):required.add('voiceReceive')
    if options.get('image_directory'):required.add('imageReceive')
    if options.get('attachment_directory'):required.add('fileReceive')
    if require_native_voice:required.add('nativeVoiceSend')
    checks=receipt.get('checks')
    if not isinstance(checks,dict) or not all(checks.get(key) is True for key in required):
        return False,'independent-enabled-capability-unverified'
    return True,'independent-live-verified'


def _control_identity(control):
    return (int(control.NativeWindowHandle),int(control.ProcessId),str(control.ClassName),tuple(control.GetRuntimeId()))


class WindowBinding:
    """A concrete Chat object, not a name lookup that might silently retarget."""
    def __init__(self,chat,target,kind,main_identity,window_classes):
        self.chat=chat;self.target=target;self.kind=kind
        self.control=chat._api.control;self.main_identity=main_identity;self.window_classes=set(window_classes)
        self.identity=_control_identity(self.control)
        if not self.identity[0] or self.identity[0]==main_identity[0] or self.identity[1]!=main_identity[1] or self.identity[2] not in self.window_classes:
            raise RuntimeError('independent-window-binding-invalid')
        self.check()

    def check(self):
        try:
            if not self.control.Exists(0):raise RuntimeError('independent-window-closed')
            if _control_identity(self.control)!=self.identity:raise RuntimeError('independent-window-replaced')
            info=self.chat.ChatInfo()
            if info.get('chat_name')!=self.target or info.get('chat_type')!=self.kind:
                raise RuntimeError('independent-window-target-mismatch')
            box=self.chat._api._chat_api.msgbox
            if not box.Exists(0):raise RuntimeError('independent-message-list-unavailable')
        except RuntimeError:raise
        except Exception as exc:raise RuntimeError('independent-window-binding-unavailable') from exc
        return self.chat


class BoundWindowMonitor(MainWindowMonitor):
    """Reuse MainWindowMonitor read/cursor/media code, changing only its window."""
    def __init__(self,binding,*,preview_provider=None,**options):
        super().__init__(binding.chat,{binding.target:binding.kind},**options)
        self.binding=binding;self.preview_provider=preview_provider

    def current(self):
        self.binding.check();return self.binding.target

    def select(self,name):
        if name!=self.binding.target:raise RuntimeError('target-or-operation-denied')
        self.binding.check() # Never ChatWith, Show, focus, or main-session switch.

    def controls(self):
        self.binding.check();result=super().controls();self.binding.check();return result

    def sessions(self):
        self.binding.check()
        if self.preview_provider is None:return {}
        value=self.preview_provider(self.binding.target)
        return {self.binding.target:dict(value)} if isinstance(value,dict) else {}

    def initialize(self):
        name=self.binding.target;self.binding.check()
        self.cursor.seed(name,self.controls())
        observed=self.sessions().get(name)
        if observed is not None:self.gate.acknowledge(name,observed)

    def read(self,name):
        self.binding.check();records=super().read(name);self.binding.check();return records

    def prepare_direction_read(self):
        # Independent windows are deliberately passive. If their own window
        # capture is unavailable, defer; never activate one to grab the screen.
        self.binding.check()

    def transcribe_voice(self,message,name):
        # The main-window geometry correction has not been accepted for a
        # bound child window. Preserve its existing version-gated SDK route.
        if name!=self.binding.target:raise RuntimeError('target-or-operation-denied')
        self.binding.check()
        result=message.to_text()
        self.binding.check()
        return result

    def note_outbound(self,name):
        self.binding.check();super().note_outbound(name);self.binding.check()


class IndependentWindowMonitor:
    mode='independent'
    def __init__(self,wx,routes,receipt,config,options,*,preview_provider=None,chat_factory=None):
        self.wx=wx;self.routes=dict(routes);self.receipt=receipt;self.config=config;self.options=options
        self.preview_provider=preview_provider;self.chat_factory=chat_factory
        self.bindings={};self.monitors={};self.window_errors={};self.open_attempts=set();self.initialized=False
        self.selected=None

    def initialize(self):
        if self.initialized:return
        main=self.wx._api.control
        if not main.Exists(0):raise RuntimeError('independent-main-window-unavailable')
        main_identity=_control_identity(main)
        windows=self.wx.GetAllSubWindow()
        if not isinstance(windows,list):raise RuntimeError('independent-window-enumeration-failed')
        matches={name:[] for name in self.routes}
        for chat in windows:
            try:info=chat.ChatInfo()
            except Exception:continue
            target=info.get('chat_name')
            if target in matches and info.get('chat_type')==self.routes[target]:matches[target].append(chat)
        identities=set()
        for target,kind in self.routes.items():
            choices=matches[target]
            if len(choices)>1:raise RuntimeError('independent-window-ambiguous')
            chat=choices[0] if choices else None
            if chat is None:
                if not self.config.get('openMissingAtStartup') or self.receipt.get('checks',{}).get('startupOpen') is not True:
                    raise RuntimeError('independent-window-missing')
                if target in self.open_attempts:raise RuntimeError('independent-window-open-already-attempted')
                self.open_attempts.add(target)
                # Explicit one-shot initialization only. Runtime reads never
                # repeat this call or silently reopen a closed window.
                core=self.wx._api.open_separate_window(target,resize=False)
                if core is None:raise RuntimeError('independent-window-open-failed')
                if self.chat_factory is None:
                    from wxauto4.wx import Chat
                    chat=Chat(core)
                else:chat=self.chat_factory(core)
            binding=WindowBinding(chat,target,kind,main_identity,self.receipt['windowClasses'])
            if binding.identity[0] in identities:raise RuntimeError('independent-window-reused-for-two-targets')
            identities.add(binding.identity[0]);self.bindings[target]=binding
            self.monitors[target]=BoundWindowMonitor(binding,preview_provider=self.preview_provider,**self.options)
        # Bind all identities before seeding, so a failed startup cannot report a
        # partly enabled independent capability set.
        for monitor in self.monitors.values():monitor.initialize()
        self.selected=next(iter(self.routes),None);self.initialized=True

    def _bound(self,name):
        if name not in self.routes:raise RuntimeError('target-or-operation-denied')
        if not self.initialized:raise RuntimeError('independent-monitor-not-initialized')
        binding=self.bindings[name]
        try:binding.check()
        except RuntimeError as exc:
            self.window_errors[name]=str(exc);raise
        self.window_errors.pop(name,None)
        return self.monitors[name]

    def select(self,name):
        self._bound(name).select(name);self.selected=name

    def current(self):
        if self.selected is None:return None
        try:self._bound(self.selected)
        except RuntimeError:return None
        return self.selected

    def candidates(self):
        result=[]
        for name in self.routes:
            try:self._bound(name)
            except RuntimeError:continue # One closed chat must not stop others.
            result.append(name)
        return result

    def read(self,name):return self._bound(name).read(name)
    def note_outbound(self,name):return self._bound(name).note_outbound(name)
    def acknowledge(self,name):return self._bound(name).acknowledge(name)
    def chat_for(self,name):
        self._bound(name);return self.bindings[name].check()
    @property
    def recovered_tails(self):return sum(m.recovered_tails for m in self.monitors.values())


class MonitorSelection:
    def __init__(self,wx,routes,options,monitor,reason):
        self.wx=wx;self.routes=dict(routes);self.options=options;self.monitor=monitor;self.reason=reason;self.started=False
    @property
    def mode(self):return 'independent' if isinstance(self.monitor,IndependentWindowMonitor) else 'main'
    def initialize(self):
        if self.started:return self.monitor
        try:self.monitor.initialize()
        except Exception as exc:
            if not isinstance(self.monitor,IndependentWindowMonitor):raise
            self.reason=str(exc) if isinstance(exc,RuntimeError) and str(exc).startswith('independent-') else 'independent-initialize-failed'
            self.monitor=MainWindowMonitor(self.wx,self.routes,**self.options)
            self.monitor.initialize()
        self.started=True;return self.monitor
    def chat_for(self,name):
        if not self.started:raise RuntimeError('window-monitor-not-initialized')
        if self.mode=='independent':return self.monitor.chat_for(name)
        if name not in self.routes:raise RuntimeError('target-or-operation-denied')
        self.monitor.select(name);info=self.wx.ChatInfo()
        if info.get('chat_name')!=name or info.get('chat_type')!=self.routes[name]:raise RuntimeError('target-mismatch')
        return self.wx
    def status(self):
        return {'mode':self.mode,'reason':self.reason,'initialized':self.started,
                'windowErrors':dict(getattr(self.monitor,'window_errors',{})),
                'openAttempts':sorted(getattr(self.monitor,'open_attempts',set()))}


def create_window_monitor(wx,routes,*,config=None,receipt=None,current_version=None,wxauto_version=None,
                          require_native_voice=False,preview_provider=None,chat_factory=None,**monitor_options):
    """Return a selection; call initialize(), then use its monitor + chat_for.

    current_version/wxauto_version must be observed by the trusted root process,
    not copied from the receipt. No live receipt is authored by this module.
    """
    config=config or {}
    verified,reason=receipt_matches(receipt,routes,current_version=current_version,wxauto_version=wxauto_version,
                                    monitor_options=monitor_options,require_native_voice=require_native_voice)
    if config.get('enabled') is not True:verified=False;reason='independent-disabled'
    monitor=(IndependentWindowMonitor(wx,routes,receipt,config,monitor_options,preview_provider=preview_provider,chat_factory=chat_factory)
             if verified else MainWindowMonitor(wx,routes,**monitor_options))
    return MonitorSelection(wx,routes,monitor_options,monitor,reason)
