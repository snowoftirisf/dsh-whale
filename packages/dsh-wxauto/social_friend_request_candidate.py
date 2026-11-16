"""UNREGISTERED staged friend-request candidate; no live work on import.

The caller must close in finally, persist the original claimed owner plan, and
provide a journal with claim/mark/readback methods. Receipt equality checks the
hook contract, not actual fsync: the production caller must prove durability.
No old unknown plan is reset, adopted or granted new approval by this module.
"""
import copy
import hashlib
import json
import re
import math
import time
from social_add_friend_candidate import AddFriendCandidate,_rid,_rect,_sha
from social_contact_observation import _stable,_walk,DISPLAY_ID,read_candidate_profile
from social_friend_verification import FriendVerificationContinuation,_nodes
from social_wxauto_backend import ActionBinding


def snapshot_sha256(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def _profile_nodes(root):
    """Discover only search/header/action surfaces, not unrelated profile details.

    Header is a compound leaf here and is independently read with _stable below.
    Detail's Qt scroll/decoration internals are not identity or action evidence.
    VerifyFriendWindow continues using its separate generic helper unchanged.
    """
    result=[];stack=[(root,0)]
    while stack:
        node,depth=stack.pop();result.append(node)
        if len(result)>128 or depth>14:raise RuntimeError('social-friend-request-profile-tree-budget')
        if (node.ClassName in ('mmui::ProfileHeaderUi','mmui::ProfileDetailNormalUi')
                or node.ControlTypeName in ('TextControl','EditControl','ButtonControl','CheckBoxControl')):
            continue
        stack.extend((c,depth+1) for c in node.GetChildren())
    return result


class FriendRequestCandidate(AddFriendCandidate):
    """journal contract (explicit constructor argument, no default True hooks):

    claim(planId,parametersHash,snapshotSha256) -> receipt
    mark(planId,parametersHash,claimId,snapshotSha256) -> receipt
    readback(planId,parametersHash,claimId,stage) -> persisted receipt

    Exact receipt keys: kind='friend-request-verification-journal', planId,
    parametersHash, claimId=32hex, snapshotSha256, stage='claimed'|'confirm-attempt',
    persisted=True. The caller owns real fsync, exclusive claiming, replay policy
    and authority. These hooks may not be selected by a model or receipt file.
    """
    def __init__(self,*args,journal,closure_timeout=3.0,passive_clock=time.monotonic,passive_sleep=time.sleep,**kwargs):
        super().__init__(*args,**kwargs)
        if not all(callable(getattr(journal,k,None)) for k in ('claim','mark','readback')):
            raise RuntimeError('social-friend-request-journal-required')
        if type(closure_timeout) not in (int,float) or not math.isfinite(closure_timeout) or not 0<=closure_timeout<=5 or not callable(passive_clock) or not callable(passive_sleep):
            raise RuntimeError('social-friend-request-close-budget-invalid')
        self.closure_timeout,self.passive_clock,self.passive_sleep=closure_timeout,passive_clock,passive_sleep
        self.journal=journal;self.verification=None;self.verification_result=None
        self.request_phase='not-started';self.request_started=False;self.request_pending=False
        self.restorationUnavailable=False;self.request_diagnostic=None;self._verification_root_identity=None
        self._known_root_identities=None
        self._verification_claim=None;self._verification_mark=None;self._verification_snapshot_hash=None
        self.postClosureObservation=None

    def _profile_state(self,parameters):
        self._plan(parameters)
        session=self._session
        if session is None:raise RuntimeError('social-friend-request-profile-unavailable')
        session['formGuard']();form,pid=session['form'],session['pid']
        nodes=_profile_nodes(form)
        edits=[n for n in nodes if n.ClassName=='mmui::XValidatorTextEdit' and n.Name=='搜索' and n.ControlTypeName=='EditControl']
        if len(edits)!=1 or edits[0].ProcessId!=pid or edits[0].GetValuePattern().Value!=parameters['keywords']:
            raise RuntimeError('social-friend-request-profile-query-mismatch')
        profiles=[n for n in nodes if n.ClassName=='mmui::ProfileHeaderUi']
        if len(profiles)!=1 or profiles[0].ProcessId!=pid:raise RuntimeError('social-friend-request-profile-ambiguous')
        tree=list(_walk(_stable(profiles[0])))
        displays=[n for n in tree if (n['class'],n['type'],n['id'])==('mmui::XTextView','TextControl',DISPLAY_ID)]
        avatars=[n for n in tree if (n['class'],n['type'])==('mmui::ContactHeadView','ButtonControl')]
        if len(displays)!=1 or len(avatars)!=1 or displays[0]['name']!=parameters['expectedProfileName'] or avatars[0]['name']!=parameters['expectedProfileName']:
            raise RuntimeError('social-friend-request-profile-name-mismatch')
        if read_candidate_profile(profiles[0],parameters['keywords']).get('identityEvidence') not in ('matched','id-unavailable'):
            raise RuntimeError('social-friend-request-profile-id-contradiction')
        buttons=[n for n in nodes if n.ClassName=='mmui::XOutlineButton' and n.ControlTypeName=='ButtonControl' and n.Name=='等待验证']
        if len(buttons)>1:raise RuntimeError('social-friend-request-pending-ambiguous')
        button=buttons[0] if buttons else None
        if button is not None:
            bounds,outer=_rect(button),_rect(form)
            if button.ProcessId!=pid or button.IsOffscreen!=0 or not (outer[0]<=bounds[0]<bounds[2]<=outer[2] and outer[1]<=bounds[1]<bounds[3]<=outer[3]):
                raise RuntimeError('social-friend-request-pending-unavailable')
            if any(n.ClassName=='mmui::XOutlineButton' and n.Name=='添加到通讯录' for n in nodes):
                raise RuntimeError('social-friend-request-pending-ambiguous')
        session['formGuard']()
        return {'pending':button is not None,'querySha256':_sha(parameters['keywords']),
            'expectedProfileNameSha256':_sha(parameters['expectedProfileName']),
            'profileRuntimeId':_rid(profiles[0]),'buttonRuntimeId':_rid(button) if button is not None else None,
            'buttonBounds':_rect(button) if button is not None else None,'formHandle':form.NativeWindowHandle,'pid':pid}

    def _snapshot(self,parameters):
        # Original preflight/perform call this before any AddContact click.
        if self._profile_state(parameters)['pending']:
            raise RuntimeError('social-friend-request-already-pending')
        return super()._snapshot(parameters)

    def _owned_verification(self,parameters):
        self._plan(parameters);s=self._session
        if s is None:raise RuntimeError('social-friend-request-session-unavailable')
        roots=s['ownedRoots']();pid=s['pid']
        known={s['main'].NativeWindowHandle,s['form'].NativeWindowHandle}
        visible=[r for r in roots if r.IsOffscreen==0]
        if any(r.ProcessId!=pid for r in visible):raise RuntimeError('social-friend-request-ownership-changed')
        forms=[r for r in visible if r.ClassName=='mmui::VerifyFriendWindow']
        if len(forms)!=1:raise RuntimeError('social-friend-request-verification-not-unique')
        form=forms[0]
        if len({r.NativeWindowHandle for r in visible})!=len(visible) or any(r.NativeWindowHandle not in known and r is not form for r in visible):
            raise RuntimeError('social-friend-request-unknown-modal')
        current_known={(r.NativeWindowHandle,_rid(r),r.ClassName,pid) for r in visible if r.NativeWindowHandle in known}
        if self._known_root_identities is None or current_known!=self._known_root_identities:
            raise RuntimeError('social-friend-request-ownership-changed')
        foreground=s['uia'].GetForegroundControl()
        identity=(form.NativeWindowHandle,_rid(form),_rect(form),pid)
        if foreground.NativeWindowHandle!=form.NativeWindowHandle or foreground.ProcessId!=pid:
            raise RuntimeError('social-friend-request-foreground-changed')
        if self._verification_root_identity is not None and self._verification_root_identity!=identity:
            raise RuntimeError('social-friend-request-verification-changed')
        self._verification_root_identity=identity
        return form

    def _verification_snapshot(self,form,parameters):
        s=self._session;nodes=_nodes(form)
        def unique(klass,name,kind):
            found=[n for n in nodes if n.ClassName==klass and n.Name==name and n.ControlTypeName==kind]
            if len(found)!=1:raise RuntimeError('social-friend-request-verification-control-not-unique')
            return found[0]
        edit=unique('mmui::XValidatorTextEdit','发送添加朋友申请','EditControl')
        button=unique('mmui::XOutlineButton','确定','ButtonControl')
        return {'source':'wxauto-uia-friend-verification','schemaVersion':1,'planId':self.plan_id,
            'parametersHash':self.expected_hash,'querySha256':_sha(parameters['keywords']),
            'expectedProfileNameSha256':_sha(parameters['expectedProfileName']),'pid':s['pid'],
            'formHandle':form.NativeWindowHandle,'formRuntimeId':_rid(form),'formBounds':_rect(form),
            'greetingRuntimeId':_rid(edit),'greetingBounds':_rect(edit),'confirmRuntimeId':_rid(button),'confirmBounds':_rect(button)}

    def _receipt(self,value,stage,snapshot_hash,claim_id=None):
        keys={'kind','planId','parametersHash','claimId','snapshotSha256','stage','persisted'}
        if (type(value)is not dict or set(value)!=keys or value.get('kind')!='friend-request-verification-journal'
                or value.get('planId')!=self.plan_id or value.get('parametersHash')!=self.expected_hash
                or value.get('stage')!=stage or value.get('snapshotSha256')!=snapshot_hash or value.get('persisted') is not True
                or not isinstance(value.get('claimId'),str) or not re.fullmatch('[a-f0-9]{32}',value['claimId'])
                or claim_id is not None and value['claimId']!=claim_id):raise RuntimeError('social-friend-request-journal-receipt-invalid')
        readback=self.journal.readback(self.plan_id,self.expected_hash,value['claimId'],stage)
        if type(readback)is not dict or readback!=value:raise RuntimeError('social-friend-request-journal-readback-mismatch')
        return copy.deepcopy(value)

    def perform(self,wx,parameters):
        parameters=self.parameters(parameters)
        if self.request_started:raise RuntimeError('social-friend-request-already-used')
        self.request_started=True
        self.request_phase='add-contact'
        try:
            self._plan(parameters)
            if wx is not self.wx or self._session is None:raise RuntimeError('social-friend-request-session-unavailable')
            s=self._session;known={s['main'].NativeWindowHandle,s['form'].NativeWindowHandle}
            roots=[r for r in s['ownedRoots']() if r.IsOffscreen==0]
            if len(roots)!=2 or {r.NativeWindowHandle for r in roots}!=known or any(r.ProcessId!=s['pid'] for r in roots):
                raise RuntimeError('social-friend-request-unknown-modal')
            self._known_root_identities={(r.NativeWindowHandle,_rid(r),r.ClassName,s['pid']) for r in roots}
            # Reuse actual original claimed-plan/target/binding rechecks and its
            # exactly-one AddContact call. No alternative click path is added.
            super().perform(wx,parameters)
            self.request_phase='verification-discovery'
            form=self._owned_verification(parameters)
            snapshot=self._verification_snapshot(form,parameters);snapshot_hash=snapshot_sha256(snapshot)
            self._verification_snapshot_hash=snapshot_hash
            holder={}
            def guard():
                current=self._owned_verification(parameters)
                return (current.NativeWindowHandle,_rid(current),_rect(current),current.ProcessId)==(
                    form.NativeWindowHandle,_rid(form),_rect(form),form.ProcessId)
            def claim():
                receipt=self.journal.claim(self.plan_id,self.expected_hash,snapshot_hash)
                holder['claim']=self._receipt(receipt,'claimed',snapshot_hash)
                self._verification_claim=copy.deepcopy(holder['claim'])
                return True  # Only after structured receipt + independent readback.
            def mark():
                claim_id=holder['claim']['claimId']
                receipt=self.journal.mark(self.plan_id,self.expected_hash,claim_id,snapshot_hash)
                self._verification_mark=self._receipt(receipt,'confirm-attempt',snapshot_hash,claim_id)
                return True
            self.verification=FriendVerificationContinuation(form,snapshot,parameters,uia=self._session['uia'],
                claimOnce=claim,markAttempt=mark,guard=guard)
            self.request_phase='verification'
            self.verification_result=self.verification.perform()
        except Exception:
            self.request_diagnostic='social-friend-request-stage-outcome-unknown'
        return {'outcome':'unknown','addContactClickAttempted':self.attempted,
            'verification':copy.deepcopy(self.verification_result),'failurePhase':self.request_phase if self.request_diagnostic else None,
            'code':self.request_diagnostic,'automaticRetry':False}

    def observe_after(self,wx,parameters,result,before):
        parameters=self.parameters(parameters);state=None;client_version=None;self.request_pending=False
        try:
            self._plan(parameters)
            if wx is not self.wx or self._session is None or not self.attempted:
                raise RuntimeError('social-friend-request-not-attempted')
            v=self.verification;vr=self.verification_result
            if (v is None or not vr or vr.get('confirmClickAttempted') is not True
                    or any(vr.get(k) is not True for k in ('claimConfirmed','inputAttempted','attemptMarked'))
                    or vr.get('attemptMarkOutcome')!='persisted' or vr.get('code') is not None or vr.get('failurePhase') is not None
                    or not (v.claimed is True and v.inputAttempted is True and v.attempted is True and v.attemptMarked is True)
                    or self._verification_claim is None or self._verification_mark is None):
                raise RuntimeError('social-friend-request-submission-unproven')
            # Validate this execution's exact journal lineage again; an unrelated
            # preexisting pending button or a forged boolean is not submission proof.
            claim=self._receipt(self._verification_claim,'claimed',self._verification_snapshot_hash)
            self._receipt(self._verification_mark,'confirm-attempt',self._verification_snapshot_hash,claim['claimId'])
            self.request_phase='verification-close-observation'
            deadline=self.passive_clock()+self.closure_timeout
            for attempt in range(int(self.closure_timeout/.1)+2):
                self._plan(parameters)
                observation=v.observe()
                self.postClosureObservation={**observation,'observations':attempt+1,'timedOut':False}
                if observation.get('formClosed') is True:break
                self._owned_verification(parameters)
                if self.passive_clock()>=deadline:
                    self.postClosureObservation['timedOut']=True
                    raise RuntimeError('social-friend-request-verification-close-timeout')
                self.passive_sleep(.1)
            else:
                self.postClosureObservation['timedOut']=True
                raise RuntimeError('social-friend-request-verification-close-timeout')
            self.request_phase='pending-readback'
            first=self._profile_state(parameters);second=self._profile_state(parameters)
            if first!=second or first['pending'] is not True:raise RuntimeError('social-friend-request-pending-unproven')
            state=second;self.request_pending=True;client_version=self._session['client']['clientVersion']
        except Exception:
            self.request_diagnostic='social-friend-request-submission-unproven'
        finally:
            try:self.close()
            except Exception:self.restorationUnavailable=True
        return {'source':'wxauto-uia','action':'add_friend','phase':'after','observedAt':self.clock(),
            'clientVersion':client_version,'controls':[copy.deepcopy(state)] if state is not None else [],
            'parametersHash':self.expected_hash,'confirmed':self.request_pending,'confirmationKind':'friend-request-submission',
            'greetingReadbackVerified':self.request_pending,'applicationSubmittedVerified':self.request_pending,
            'requestPending':self.request_pending,'friendshipConfirmed':False,'pendingObservation':state,
            'postClosureObservation':copy.deepcopy(self.postClosureObservation),
            'addContactClickAttempted':self.attempted,'verification':copy.deepcopy(self.verification_result),
            'restorationUnavailable':self.restorationUnavailable,'code':self.request_diagnostic,'automaticRetry':False}

    def close(self):
        if self._session is not None:
            try:
                s=self._session;known={s['main'].NativeWindowHandle,s['form'].NativeWindowHandle}
                if any(r.IsOffscreen==0 and (r.ProcessId!=s['pid'] or r.NativeWindowHandle not in known) for r in s['ownedRoots']()):
                    raise RuntimeError('social-friend-request-unknown-modal')
            except Exception:
                self.restorationUnavailable=True
                raise RuntimeError('social-friend-request-restoration-unavailable') from None
        try:super().close()
        except Exception:
            self.restorationUnavailable=True
            raise

    def binding(self):
        return ActionBinding('unregistered-owner-claimed-friend-request-candidate-v1',self.perform,self.observe_before,self.observe_after)
