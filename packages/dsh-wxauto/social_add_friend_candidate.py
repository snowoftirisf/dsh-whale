"""UNREGISTERED one-click add-contact candidate; no unknown follow-up actions.

Requires the existing owner-approved, durably claimed plan before navigation.
The single worker acceptance caller must use ``finally: candidate.close()`` and
mark backend.chat_binding_failed on restoration failure, including when backend
preflight rejects after observe_before. Never add this to load_observed_bindings
without real acceptance. observe_after deliberately cannot confirm friendship.
"""
import copy
import hashlib
import math
import re
import threading
import time
from social_actions import _digest
from social_wxauto_backend import ActionBinding,running_client
from social_friend_observe import owned_plan_lookup
from social_contact_observation import _stable,_walk,DISPLAY_ID,read_candidate_profile
from social_friend_search import valid_search_query


def _sha(value):
    return hashlib.sha256(value.encode('utf8')).hexdigest()


def _rect(control):
    r=control.BoundingRectangle
    values=tuple(getattr(r,key) for key in ('left','top','right','bottom'))
    if (any(type(n) not in (int,float) or not math.isfinite(n) for n in values)
            or values[2]<=values[0] or values[3]<=values[1]):
        raise RuntimeError('social-add-friend-invalid-geometry')
    return values


def _rid(control):
    value=control.GetRuntimeId()
    if not isinstance(value,(list,tuple)) or not value or any(type(n)is not int for n in value):
        raise RuntimeError('social-add-friend-runtime-unavailable')
    return tuple(value)


class AddFriendCandidate:
    def __init__(self,wx,config,*,plan_id,plan_hash,plan_provider,allowed_groups=(),client_probe=running_client,
                 refresh_chat=None,clock=time.time,open_lookup=owned_plan_lookup,lookup_options=None):
        # Inert: no provider invocation, SDK read, window discovery or I/O here.
        self.wx=wx
        self.config,self.owner=config,config['target']
        self.plan_provider=plan_provider
        self.plan_id,self.expected_hash=plan_id,plan_hash
        self.groups,self.client_probe,self.refresh_chat=tuple(allowed_groups),client_probe,refresh_chat
        self.clock,self.open_lookup,self.options=clock,open_lookup,dict(lookup_options or {})
        self.ui_thread=threading.get_ident()
        self._context,self._session,self._prepared=None,None,None
        self.attempted=False
        self.restoration_failed=False
        self.last_before=None
        self.last_observation=None
        self._identity_evidence=None

    @staticmethod
    def parameters(value):
        if (type(value)is not dict or set(value)!={'keywords','expectedProfileName','addmsg'}
                or not valid_search_query(value.get('keywords'))
                or not isinstance(value.get('expectedProfileName'),str)
                or not value['expectedProfileName'].strip() or len(value['expectedProfileName'])>128
                or '\x00'in value['expectedProfileName'] or not isinstance(value.get('addmsg'),str)
                or '\x00'in value['addmsg']):
            raise RuntimeError('social-add-friend-parameters-unavailable')
        return copy.deepcopy(value)

    def _plan(self,parameters):
        if threading.get_ident()!=self.ui_thread:raise RuntimeError('social-add-friend-ui-thread-required')
        if self.restoration_failed:raise RuntimeError('social-chat-restore-failed')
        if _digest('add_friend',parameters)!=self.expected_hash:
            raise RuntimeError('social-add-friend-parameters-changed')
        plan=copy.deepcopy(self.plan_provider())
        if (plan.get('id')!=self.plan_id or plan.get('action')!='add_friend' or plan.get('hash')!=self.expected_hash
                or plan.get('parameters')!=parameters or plan.get('status')!='claimed'
                or plan.get('origin')!={'kind':'friend','target':self.owner}
                or plan.get('approvedBy')!=self.owner or not isinstance(plan.get('approvalMessageId'),str)
                or not plan['approvalMessageId'] or self.clock()>=plan['expiresAt']):
            raise RuntimeError('social-add-friend-approved-claimed-plan-required')
        return plan

    def _snapshot(self,parameters):
        session=self._session
        if session is None:raise RuntimeError('social-add-friend-no-owned-form')
        session['formGuard']()
        form,pid,helpers=session['form'],session['pid'],session['helpers']
        edit=helpers.unique([form],'mmui::XValidatorTextEdit','搜索')
        if edit.GetValuePattern().Value!=parameters['keywords']:
            raise RuntimeError('social-add-friend-query-mismatch')
        profiles=[c for c in helpers.nodes(form) if c.ClassName=='mmui::ProfileHeaderUi']
        if len(profiles)!=1 or profiles[0].ProcessId!=pid:raise RuntimeError('social-add-friend-profile-ambiguous')
        tree=_stable(profiles[0]);nodes=list(_walk(tree))
        displays=[n for n in nodes if (n['class'],n['type'],n['id'])==('mmui::XTextView','TextControl',DISPLAY_ID)]
        avatars=[n for n in nodes if (n['class'],n['type'])==('mmui::ContactHeadView','ButtonControl')]
        name=parameters['expectedProfileName']
        if len(displays)!=1 or len(avatars)!=1 or displays[0]['name']!=name or avatars[0]['name']!=name:
            raise RuntimeError('social-add-friend-reviewed-name-mismatch')
        identity=read_candidate_profile(profiles[0],parameters['keywords'])
        evidence=identity.get('identityEvidence')
        if evidence not in ('matched','id-unavailable'):
            raise RuntimeError('social-add-friend-public-id-contradiction')
        self._identity_evidence=evidence
        button=helpers.unique([form],'mmui::XOutlineButton','添加到通讯录')
        if (button.ControlTypeName!='ButtonControl' or button.ProcessId!=pid
                or type(button.IsOffscreen)not in (bool,int) or button.IsOffscreen!=0
                or type(button.IsEnabled)not in (bool,int) or button.IsEnabled!=1):
            raise RuntimeError('social-add-friend-button-unavailable')
        bounds,outer=_rect(button),_rect(form)
        if not (outer[0]<=bounds[0]<bounds[2]<=outer[2] and outer[1]<=bounds[1]<bounds[3]<=outer[3]):
            raise RuntimeError('social-add-friend-button-outside-form')
        session['formGuard']()
        return button,{'formRuntimeId':_rid(form),'formHandle':form.NativeWindowHandle,
            'buttonRuntimeId':_rid(button),'buttonBounds':bounds,'pid':pid,
            'querySha256':_sha(parameters['keywords']),'expectedProfileNameSha256':_sha(name),
            'parametersHash':self.expected_hash}

    def observe_before(self,wx,parameters):
        parameters=self.parameters(parameters)
        if wx is not self.wx:raise RuntimeError('social-add-friend-bound-instance-mismatch')
        if self._context is not None or self.attempted:raise RuntimeError('social-add-friend-already-attempted')
        plan=self._plan(parameters)
        client=self.client_probe(wx)
        from wxauto_connection import refresh_chat_binding
        context=self.open_lookup(wx,self.config,self.groups,plan=plan,plan_check=lambda:self._plan(parameters),
            client=client,client_probe=self.client_probe,refresh_chat=self.refresh_chat or refresh_chat_binding,**self.options)
        try:
            session=context.__enter__()
            self._context,self._session=context,session
            if session.get('wx')is not wx:raise RuntimeError('social-add-friend-bound-instance-mismatch')
            first=self._snapshot(parameters)[1];second=self._snapshot(parameters)[1]
            if first!=second:raise RuntimeError('social-add-friend-controls-changed')
            self._prepared=second
            evidence={'source':'wxauto-uia','action':'add_friend','phase':'before',
                'observedAt':self.clock(),'clientVersion':client['clientVersion'],
                'parametersHash':self.expected_hash,'controls':[copy.deepcopy(second)],
                'identityBasis':'owner-reviewed-query-and-name',
                'publicWechatIdVerified':self._identity_evidence=='matched',
                'identityEvidence':self._identity_evidence}
            self.last_before=copy.deepcopy(evidence)
            return evidence
        except Exception as error:
            if isinstance(error,RuntimeError)and str(error)=='social-chat-restore-failed':self.restoration_failed=True
            self.close()
            raise

    def perform(self,wx,parameters):
        parameters=self.parameters(parameters)
        self._plan(parameters)
        if self.attempted or self._session is None or self._session['wx']is not wx:
            raise RuntimeError('social-add-friend-already-attempted')
        button,state=self._snapshot(parameters)
        if state!=self._prepared:raise RuntimeError('social-add-friend-controls-changed')
        self.attempted=True  # Before input: exceptions can follow a completed click.
        button.Click(move=True,show_window=False)
        return {'clickAttempted':True,'outcome':'unknown','finalApplicationFormImplemented':False}

    def observe_after(self,wx,parameters,result,before):
        parameters=self.parameters(parameters)
        try:
            if not self.attempted or self._session is None or self._session['wx']is not wx:
                raise RuntimeError('social-add-friend-not-attempted')
            session=self._session
            self._capture_once()
            return {'source':'wxauto-uia','action':'add_friend','phase':'after','observedAt':self.clock(),
                'clientVersion':session['client']['clientVersion'],'parametersHash':self.expected_hash,
                'controls':copy.deepcopy(self.last_observation['nodes']),'confirmed':False,'outcome':'unknown',
                'applicationSubmittedVerified':False,'friendshipConfirmed':False}
        finally:
            self.close()

    def _capture_once(self):
        """Unverified metadata only, once per attempted click; never read main chat."""
        if not self.attempted or self.last_observation is not None:return
        value={'schemaVersion':1,'kind':'unverified-add-friend-post-click-diagnostic',
            'capturedAt':self.clock(),'attempted':True,'foregroundOwned':False,'foregroundMain':False,
            'foregroundWindowHandle':0,'ownershipStable':False,'truncated':False,'nodes':[]}
        self.last_observation=value  # Seal before any SDK read, even on failure.
        try:
            session=self._session
            if session is None:raise RuntimeError('social-add-friend-diagnostic-session-unavailable')
            pid=session['pid'];main_handle=session['main'].NativeWindowHandle
            roots=session['ownedRoots']()
            foreground=session['uia'].GetForegroundWindow()
            value['foregroundWindowHandle']=foreground if type(foreground)is int and foreground>0 else 0
            owned=[r for r in roots if r.ProcessId==pid and r.IsOffscreen==0]
            value['foregroundOwned']=any(r.NativeWindowHandle==foreground for r in owned)
            value['foregroundMain']=foreground==main_handle and value['foregroundOwned']
            if not value['foregroundOwned']:
                raise RuntimeError('social-add-friend-diagnostic-foreground-unverified')
            identities=tuple((r.NativeWindowHandle,_rid(r)) for r in owned)
            stack=[]
            for ri,root in reversed(list(enumerate(owned))):
                if root.NativeWindowHandle!=main_handle:stack.append((root,-1,0,ri))
            seen=set();visited=0
            while stack:
                if visited>=256:value['truncated']=True;break
                node,parent,depth,ri=stack.pop();visited+=1
                if node.ProcessId!=pid:
                    raise RuntimeError('social-add-friend-diagnostic-owner-changed')
                if type(node.IsOffscreen)not in (int,bool) or node.IsOffscreen!=0:continue
                rid=_rid(node)
                if rid in seen:continue
                seen.add(rid)
                index=len(value['nodes'])
                cls,typ=node.ClassName,node.ControlTypeName
                hwnd=getattr(node,'NativeWindowHandle',0)
                item={'index':index,'parentIndex':parent,'depth':depth,'rootIndex':ri,
                    'class':cls if isinstance(cls,str) and re.fullmatch(r'[A-Za-z0-9_:.-]{1,120}',cls) else 'UnknownControl',
                    'type':typ if isinstance(typ,str) and re.fullmatch(r'[A-Za-z0-9_:.-]{1,80}',typ) else 'UnknownControl','pid':pid,
                    'windowHandle':hwnd if type(hwnd)is int and hwnd>=0 else 0,
                    'runtimeIdSha256':_sha(str(rid)),'runtimeIdLength':len(rid),
                    'idSha256':_sha(''),'idLength':0,'nameSha256':_sha(''),'nameLength':0,
                    'valueAvailable':False,'geometryAvailable':False,'bounds':None,'semanticRole':'unknown'}
                value['nodes'].append(item)
                for attr,prefix in (('AutomationId','id'),('Name','name')):
                    try:text=getattr(node,attr,'')
                    except Exception as error:
                        value['errorType']=type(error).__name__
                        value['code']='social-add-friend-diagnostic-unavailable'
                        continue
                    if isinstance(text,str):
                        item[prefix+'Sha256']=hashlib.sha256(text.encode('utf8',errors='surrogatepass')).hexdigest()
                        item[prefix+'Length']=len(text)
                        if attr=='Name':item['semanticRole']={'发送':'send','取消':'cancel','关闭':'close',
                            '添加到通讯录':'add-contact'}.get(text,'unknown')
                try:item['bounds']=list(_rect(node));item['geometryAvailable']=True
                except Exception:pass  # Zero/hidden Qt containers do not erase siblings.
                try:
                    pattern=node.GetValuePattern()
                    text=pattern.Value if pattern is not None else None
                    if isinstance(text,str):
                        item.update(valueAvailable=True,valueLength=len(text),
                            valueSha256=hashlib.sha256(text.encode('utf8',errors='surrogatepass')).hexdigest())
                except Exception:pass
                if depth>=10:
                    value['truncated']=True
                    continue
                children=node.GetChildren()
                if len(children)>256:value['truncated']=True;children=children[:256]
                stack.extend((c,index,depth+1,ri) for c in reversed(children))
            final_roots=session['ownedRoots']()
            final=tuple((r.NativeWindowHandle,_rid(r)) for r in final_roots if r.ProcessId==pid and r.IsOffscreen==0)
            value['ownershipStable']=session['uia'].GetForegroundWindow()==foreground and final==identities
            if not value['ownershipStable']:value['code']='social-add-friend-diagnostic-owner-changed'
        except Exception as error:
            value['errorType']=type(error).__name__
            code=str(error) if isinstance(error,RuntimeError) else ''
            value['code']=code if code in ('social-add-friend-diagnostic-session-unavailable',
                'social-add-friend-diagnostic-foreground-unverified','social-add-friend-diagnostic-owner-changed') else 'social-add-friend-diagnostic-unavailable'

    def close(self):
        self._capture_once()
        context=self._context
        self._context,self._session,self._prepared=None,None,None
        if context is not None:
            try:context.__exit__(None,None,None)
            except Exception:
                self.restoration_failed=True
                raise RuntimeError('social-chat-restore-failed') from None
        if self.restoration_failed:raise RuntimeError('social-chat-restore-failed')

    def binding(self):
        return ActionBinding('unregistered-owner-approved-add-contact-candidate-v1',
            self.perform,self.observe_before,self.observe_after)
