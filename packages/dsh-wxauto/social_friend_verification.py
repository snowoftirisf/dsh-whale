"""Unregistered, single-shot continuation of an already verified friend form.

No UI imports or actions on import/construction. The trusted caller supplies the
original plan-bound snapshot and durable claim/attempt hooks. Form closure is
never proof of request delivery or friendship. No automatic retries or cleanup.
"""
import copy
import hashlib
import math
import re
import threading
from social_actions import _digest


def _fail(code):
    raise RuntimeError('social-verification-'+code)


def _sha(value):
    return hashlib.sha256(value.encode('utf8')).hexdigest()


def _rid(control):
    value=control.GetRuntimeId()
    if type(value) not in (tuple,list) or not 1<=len(value)<=64 or any(type(n)is not int for n in value):
        _fail('runtime-invalid')
    return tuple(value)


def _bounds(control):
    rect=control.BoundingRectangle
    value=tuple(getattr(rect,k) for k in ('left','top','right','bottom'))
    if any(type(n) not in (float,int) or not math.isfinite(n) for n in value) or value[0]>=value[2] or value[1]>=value[3]:
        _fail('bounds-invalid')
    return value


def _visible(control,pid):
    if (not control.Exists(0) or control.ProcessId!=pid
            or type(control.IsEnabled) not in (int,bool) or control.IsEnabled!=1
            or type(control.IsOffscreen) not in (int,bool) or control.IsOffscreen!=0):
        _fail('control-unavailable')


def _nodes(root):
    stack=[(root,0)];result=[]
    while stack:
        node,depth=stack.pop();result.append(node)
        if len(result)>128 or depth>14:_fail('tree-budget')
        # UIA semantic controls are leaves for selector discovery. Their virtual
        # text/editor internals can be arbitrarily deep and are not candidates.
        if node.ControlTypeName in ('TextControl','EditControl','ButtonControl','CheckBoxControl'):
            continue
        stack.extend((c,depth+1) for c in node.GetChildren())
    return result


class FriendVerificationContinuation:
    """snapshot keys are explicit; no discovery by account name or hash alone.

    source='wxauto-uia-friend-verification', schemaVersion=1, planId,
    parametersHash, querySha256, expectedProfileNameSha256, pid, formHandle,
    formRuntimeId/formBounds, greetingRuntimeId/greetingBounds,
    confirmRuntimeId/confirmBounds. All bounds/RIDs come from a separately
    reviewed live observation, not this helper or a model-generated argument.

    guard(), claimOnce(), markAttempt() must each return plain True. The caller
    owns approval, original-plan association, persistence and crash recovery.
    A failing hook never authorizes input; a throwing attempt hook may have
    persisted a claim and is reported unknown without clicking.
    """
    def __init__(self,form,snapshot,parameters,*,uia,claimOnce,markAttempt,guard):
        fields={'source','schemaVersion','planId','parametersHash','querySha256','expectedProfileNameSha256',
            'pid','formHandle','formRuntimeId','formBounds','greetingRuntimeId','greetingBounds','confirmRuntimeId','confirmBounds'}
        if (type(snapshot)is not dict or set(snapshot)!=fields or type(snapshot.get('schemaVersion'))is not int or snapshot.get('schemaVersion')!=1
                or snapshot.get('source')!='wxauto-uia-friend-verification'
                or not isinstance(snapshot.get('planId'),str) or not re.fullmatch('[a-f0-9]{32}',snapshot['planId'])
                or type(parameters)is not dict or set(parameters)!={'keywords','expectedProfileName','addmsg'}
                or any(not isinstance(v,str) or '\0' in v or len(v)>4000 for v in parameters.values())
                or not parameters['keywords'].strip() or not parameters['expectedProfileName'].strip()
                or _digest('add_friend',parameters)!=snapshot.get('parametersHash')
                or _sha(parameters['keywords'])!=snapshot.get('querySha256')
                or _sha(parameters['expectedProfileName'])!=snapshot.get('expectedProfileNameSha256')
                or any(not callable(v) for v in (claimOnce,markAttempt,guard))):
            _fail('configuration-invalid')
        for key in ('pid','formHandle'):
            if type(snapshot[key])is not int or snapshot[key]<=0:_fail('configuration-invalid')
        for key in ('formRuntimeId','greetingRuntimeId','confirmRuntimeId'):
            value=snapshot[key]
            if type(value) not in (tuple,list) or not 1<=len(value)<=64 or any(type(n)is not int for n in value):_fail('configuration-invalid')
        for key in ('formBounds','greetingBounds','confirmBounds'):
            value=snapshot[key]
            if type(value) not in (tuple,list) or len(value)!=4 or any(type(n)not in (int,float) or not math.isfinite(n) for n in value) or value[0]>=value[2] or value[1]>=value[3]:_fail('configuration-invalid')
        self.form,self.snapshot,self.parameters=form,copy.deepcopy(snapshot),copy.deepcopy(parameters)
        self.uia,self.claimOnce,self.markAttempt,self.guard=uia,claimOnce,markAttempt,guard
        self.thread=threading.get_ident();self.started=False;self.inputAttempted=False;self.attempted=False
        self.claimed=False;self.attemptMarked=False;self.attemptMarkOutcome='not-called';self.phase='not-started'

    def _validate(self):
        if threading.get_ident()!=self.thread:_fail('thread-mismatch')
        if self.guard() is not True:_fail('guard-rejected')
        form,s=self.form,self.snapshot;pid=s['pid']
        _visible(form,pid)
        if form.ClassName!='mmui::VerifyFriendWindow' or form.NativeWindowHandle!=s['formHandle']:_fail('form-mismatch')
        foreground=self.uia.GetForegroundControl()
        if foreground.NativeWindowHandle!=s['formHandle'] or foreground.ProcessId!=pid:_fail('foreground-mismatch')
        nodes=_nodes(form)
        def unique(klass,name,kind):
            matches=[n for n in nodes if n.ClassName==klass and n.Name==name and n.ControlTypeName==kind]
            if len(matches)!=1:_fail('control-not-unique')
            return matches[0]
        edit=unique('mmui::XValidatorTextEdit','发送添加朋友申请','EditControl')
        confirm=unique('mmui::XOutlineButton','确定','ButtonControl')
        for node in (edit,confirm):
            _visible(node,pid)
            bounds,outer=_bounds(node),_bounds(form)
            if not (outer[0]<=bounds[0]<bounds[2]<=outer[2] and outer[1]<=bounds[1]<bounds[3]<=outer[3]):_fail('outside-form')
        actual={'formRuntimeId':_rid(form),'formBounds':_bounds(form),'greetingRuntimeId':_rid(edit),
            'greetingBounds':_bounds(edit),'confirmRuntimeId':_rid(confirm),'confirmBounds':_bounds(confirm)}
        if any(tuple(s[k])!=v for k,v in actual.items()):_fail('snapshot-changed')
        return edit,confirm,actual

    def observe(self):
        """Passive post-action observation only; does not confirm a send."""
        if threading.get_ident()!=self.thread:_fail('thread-mismatch')
        closed=not self.form.Exists(0)
        if not closed and (self.form.NativeWindowHandle!=self.snapshot['formHandle'] or self.form.ProcessId!=self.snapshot['pid']):
            _fail('form-mismatch')
        return {'formClosed':closed,'confirmed':False,'deliveryConfirmed':False}

    def perform(self):
        if self.started:_fail('already-used')
        self.started=True;code=None;observation={'formClosed':None,'confirmed':False,'deliveryConfirmed':False}
        try:
            self.phase='preflight'
            first=self._validate()[2];second=self._validate()[2]
            if first!=second:_fail('snapshot-changed')
            self.phase='claim'
            if self.claimOnce() is not True:_fail('claim-not-confirmed')
            self.claimed=True
            self.phase='greeting-write'
            edit,_,_=self._validate();self.inputAttempted=True
            edit.GetValuePattern().SetValue(self.parameters['addmsg'])
            self.phase='greeting-readback'
            for _ in range(2):
                edit,_,_=self._validate()
                if edit.GetValuePattern().Value!=self.parameters['addmsg']:_fail('greeting-readback-mismatch')
            self.phase='attempt-mark'
            self._validate();self.attemptMarkOutcome='unknown'
            if self.markAttempt() is not True:_fail('attempt-mark-not-confirmed')
            self.attemptMarked=True;self.attemptMarkOutcome='persisted'
            self.phase='confirm'
            edit,confirm,_=self._validate()
            if edit.GetValuePattern().Value!=self.parameters['addmsg']:_fail('greeting-readback-mismatch')
            self.attempted=True  # Exceptions may follow a completed external click.
            confirm.Click(move=True,show_window=False)
            self.phase='observe';observation=self.observe()
        except Exception as error:
            allowed={'configuration-invalid','thread-mismatch','guard-rejected','control-unavailable','form-mismatch',
                'foreground-mismatch','tree-budget','control-not-unique','outside-form','snapshot-changed','runtime-invalid',
                'bounds-invalid','claim-not-confirmed','greeting-readback-mismatch','attempt-mark-not-confirmed'}
            prefix='social-verification-';suffix=str(error)[len(prefix):] if str(error).startswith(prefix) else ''
            code=prefix+suffix if suffix in allowed else prefix+'operation-outcome-unknown'
        return {**observation,'outcome':'unknown' if self.inputAttempted or self.attempted else 'not-dispatched',
            'failurePhase':self.phase if code else None,'code':code,'claimConfirmed':self.claimed,
            'inputAttempted':self.inputAttempted,'confirmClickAttempted':self.attempted,
            'attemptMarked':self.attemptMarked,'attemptMarkOutcome':self.attemptMarkOutcome,'automaticRetry':False}
