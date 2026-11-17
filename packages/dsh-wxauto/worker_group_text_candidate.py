"""Staged per-group text bridge. No model/social decisions or native approvals.

Only committed authority authorizes pre-SDK text observations. Deferred UI
messages stay exact; uncertain shutdown/abort retains private recovery evidence.
"""
import copy,hashlib,os,re,time,uuid
from itertools import islice
from pathlib import Path
from attachment_policy import local_path
from data_paths import absolute_data_path
from native_owner_text_claim import _read,_compact
from native_group_text_admission_authority import NativeGroupTextAdmissionAuthority as NativeGroupTextAuthority
from message_watch import SessionGate

VERSION=1
OPS=frozenset('group-text-'+x for x in ('status','prepare','abort','commit','admit','reset'))
KEY=re.compile(r'wechat:[A-Za-z0-9_-]{1,256}@chatroom')
HEX=re.compile('[a-f0-9]{32}')


class WorkerGroupTextBridge:
    def __init__(self,config,*,publish,emit,publish_batch=None,controller_factory=NativeGroupTextAuthority,route_provider=None):
        self.config,self.publish,self.emit=config,publish,emit
        self.publish_batch=publish_batch
        self.factory,self.route_provider=controller_factory,route_provider
        self.controllers={};self.targets={};self.deferred={};self.stopped=set();self.saved=set();self.closed=False
        self.reasons={}
        self.bindings={}
        self.previews={}
        self.suspended=set()
        self.registered_ui_handoffs={}
        self.instance_id=uuid.uuid4().hex
        self.inventory,self.inventory_unknown=self._inventory()
        self.protected_keys={r['nativeChatKey']for r in self.inventory}
        self.pending_recovery_keys={r['nativeChatKey']for r in self.inventory if r.get('pendingRecovery')}
        self.registry_failed=self.inventory_unknown
        for proof in self.inventory:
            key,target=proof['nativeChatKey'],proof.get('target')
            if target:self.targets[target]=key
        self.sync_routes()

    def _stop(self,key,reason):
        """Stop reads for one group and remember the first reason (shown to the owner)."""
        self.stopped.add(key);self.reasons.setdefault(key,reason)

    def _inventory(self):
        """One bounded passive inventory, distinguishing archives from uncertainty."""
        proofs={};unknown=False;registry_rows=[]
        try:
            registry=absolute_data_path('config/group-routes.local.json',root=self.config['dataRoot'])
            if os.path.lexists(registry):
                value,_=_read(registry)
                if value.get('version')==1 and value.get('accountId')==self.config.get('accountId') and type(value.get('groups'))is list and len(value['groups'])<=1000:
                    registry_rows=value['groups']
        except Exception:pass
        try:
            folder=absolute_data_path('config/native-group-text',root=self.config['dataRoot'])
            if not os.path.lexists(folder):return [],False
            folder=local_path(folder)
            if not folder.is_dir():return [],True
            children=list(islice(folder.iterdir(),1001))
            if len(children)>1000:return [],True
            for child in children:
                key=target=None
                try:
                    if not re.fullmatch('[a-f0-9]{32}',child.name):unknown=True;continue
                    child=local_path(child)
                    if not child.is_dir():unknown=True;continue
                    entries=list(islice(child.iterdir(),33))
                    if len(entries)>32:unknown=True;continue
                    key=target=None;has_authority=False;has_lock=False;transition_protected=False
                    pending=False;uncertain=False;admission_attempts=False
                    for file in entries:
                        if file.name=='admission-attempts':
                            if not local_path(file).is_dir():uncertain=True
                            elif next(iter(file.iterdir()),None)is not None:admission_attempts=True
                            # Coordinator verifies each journal. A committed
                            # source is restored independently of its ACK.
                            continue
                        if file.name=='native-group-text-cutover':
                            if not local_path(file).is_dir():uncertain=True
                            continue  # Legitimate publisher archive; never recursively read.
                        if file.name=='deferred':
                            if not local_path(file).is_dir():uncertain=True
                            elif next(iter(file.iterdir()),None)is not None:pending=True
                            continue  # No private envelope contents read by inventory.
                        if file.name in ('inbox.json','inbox.json.lock'):
                            checked=local_path(file)
                            if not checked.is_file()or checked.stat().st_nlink!=1:uncertain=True
                            continue
                        if file.name not in ('authority.json','transition.json','transition.lock'):
                            uncertain=True;continue
                        try:
                            value,_=_read(file);candidate=value.get('nativeChatKey')
                            if (value.get('accountId')!=self.config.get('accountId')or not isinstance(candidate,str)
                                    or not KEY.fullmatch(candidate)or hashlib.sha256(candidate.encode()).hexdigest()[:32]!=child.name):raise ValueError()
                            if file.name!='transition.lock':
                                body={k:v for k,v in value.items()if k!='digest'}
                                format='whale-native-group-text-authority'if file.name=='authority.json'else'whale-native-group-text-transition'
                                if (value.get('digest')!=hashlib.sha256(_compact(body)).hexdigest()or body.get('format')!=format
                                        or type(body.get('version'))is not int or body['version']!=1):raise ValueError()
                            elif not isinstance(value.get('ticket'),str)or not HEX.fullmatch(value['ticket']):raise ValueError()
                            if key is not None and candidate!=key:raise ValueError()
                            key=candidate
                            candidate_target=value.get('target')
                            if isinstance(candidate_target,str)and candidate_target and candidate_target!=self.config.get('target'):
                                if target is not None and target!=candidate_target:uncertain=True
                                target=candidate_target
                            if file.name=='authority.json':has_authority=True
                            if file.name=='transition.lock':has_lock=True
                            if file.name=='transition.json':transition_protected=value.get('mode')!='ui'
                        except Exception:uncertain=True
                    # A valid terminal UI transition by itself is not uncertainty.
                    if admission_attempts and not has_authority:pending=True
                    protected=has_authority or has_lock or transition_protected or pending or uncertain
                    if not protected:continue
                    matches=[r for r in registry_rows if type(r)is dict and r.get('accountId')==self.config.get('accountId')
                        and isinstance(r.get('nativeChatKey'),str)and KEY.fullmatch(r['nativeChatKey'])
                        and hashlib.sha256(r['nativeChatKey'].encode()).hexdigest()[:32]==child.name]
                    if len(matches)==1:
                        if key is not None and key!=matches[0]['nativeChatKey']:unknown=True;continue
                        key=matches[0]['nativeChatKey'];candidate_target=matches[0].get('target')
                        if isinstance(candidate_target,str)and candidate_target and candidate_target!=self.config.get('target'):target=candidate_target
                    if key is None:unknown=True;continue
                    proofs[key]={'nativeChatKey':key,'target':target,'pendingRecovery':pending or uncertain}
                except Exception:
                    # Even an unreadable/link residual can be guarded per key
                    # when the fixed directory hash has one trusted mapping.
                    matches=[r for r in registry_rows if type(r)is dict and r.get('accountId')==self.config.get('accountId')
                        and isinstance(r.get('nativeChatKey'),str)and KEY.fullmatch(r['nativeChatKey'])
                        and hashlib.sha256(r['nativeChatKey'].encode()).hexdigest()[:32]==child.name
                        and isinstance(r.get('target'),str)and r['target']and r['target']!=self.config.get('target')]
                    if len(matches)==1:key,target=matches[0]['nativeChatKey'],matches[0]['target']
                    if key and target:proofs[key]={'nativeChatKey':key,'target':target,'pendingRecovery':True}
                    else:unknown=True
            return list(proofs.values()),unknown
        except Exception:return [],True

    def _routes(self):
        if self.route_provider:return self.route_provider()
        file=absolute_data_path('config/group-routes.local.json',root=self.config['dataRoot'])
        if not os.path.lexists(file):return []
        value,_=_read(file)
        if value.get('version')!=1 or value.get('accountId')!=self.config.get('accountId') or type(value.get('groups'))is not list or len(value['groups'])>1000:
            raise RuntimeError('registry-invalid')
        return value['groups']

    def sync_routes(self):
        if self.closed:return False
        try:
            routes=self._routes();active={};targets={}
            for route in routes:
                if type(route)is not dict:raise RuntimeError('registry-invalid')
                if route.get('status')!='active':continue
                key=route.get('nativeChatKey');target=route.get('target')
                if (not isinstance(key,str) or not KEY.fullmatch(key) or route.get('accountId')!=self.config.get('accountId')
                        or not isinstance(target,str) or not target or target==self.config.get('target')
                        or not all(isinstance(route.get(k),str) and route[k] for k in ('sessionId','chatId'))
                        or key in active or target in targets):raise RuntimeError('registry-invalid')
                active[key]=route;targets[target]=key
            # A protected but inactive key can only be mapped by a unique exact
            # registry record, never by a display-name search or default target.
            for proof in self.inventory:
                key=proof['nativeChatKey']
                matches=[r for r in routes if type(r)is dict and r.get('nativeChatKey')==key and r.get('accountId')==self.config.get('accountId')]
                target=proof.get('target')
                if len(matches)==1 and isinstance(matches[0].get('target'),str) and matches[0]['target']!=self.config.get('target'):
                    target=matches[0]['target']
                if not target:self.inventory_unknown=True;continue
                self.targets[target]=key
                if key not in active and key not in self.controllers:
                    controller=self.factory(self.config,key);self.controllers[key]=controller;self.deferred[key]=[]
                    self.bindings[key]=None
                    try:controller.restore()
                    except Exception:pass
                    self._stop(key,'route-inactive-with-native-state')
            # Revoked routes remain blocked; never revert an uncertain native source to UI.
            for key in set(self.controllers)-set(active):
                if key in self.protected_keys or self.controllers[key].status().get('mode')!='ui':self._stop(key,'route-revoked')
            for key,route in active.items():
                if key not in self.controllers:
                    controller=self.factory(self.config,key);self.controllers[key]=controller;self.deferred[key]=[]
                    self.bindings[key]=(route['sessionId'],route['chatId'])
                    try:controller.restore()
                    except Exception:self._stop(key,self.controllers[key].status().get('blockedReason')or'restore-failed')
                if self.bindings[key]!=(route['sessionId'],route['chatId']):self._stop(key,'route-session-or-chat-changed')
                if self.controllers[key].status().get('mode')!='ui':self.protected_keys.add(key)
                if self.controllers[key].status().get('mode')=='blocked':self._stop(key,self.controllers[key].status().get('blockedReason')or'authority-blocked')
                if key in self.pending_recovery_keys:self._stop(key,'pending-recovery-files')
            # Publish the new name map together, retaining blocked revoked routes
            # but removing old labels of a trusted same-key/session/chat rename.
            updated={t:k for t,k in self.targets.items() if k not in active}
            updated.update(targets)
            self.targets=updated
            self.registry_failed=self.inventory_unknown
            return True
        except Exception:
            for proof in self.inventory:
                key=proof['nativeChatKey']
                if proof.get('target') and key not in self.controllers:
                    controller=self.factory(self.config,key);self.controllers[key]=controller;self.deferred[key]=[];self.bindings[key]=None
                    try:controller.restore()
                    except Exception:pass
                    self._stop(key,'registry-unverified')
            unmapped=any(r['nativeChatKey']not in self.targets.values()for r in self.inventory)
            self.registry_failed=self.inventory_unknown or unmapped
            for k in self.controllers:
                if k in self.protected_keys or self.controllers[k].status().get('mode')!='ui':self._stop(k,'registry-unverified')
            if self.registry_failed or self.stopped:self.emit({'event':'group-text-blocked','code':'group-text-registry-unverified'})
            return False

    def _status(self,key):
        value=copy.deepcopy(self.controllers[key].status())
        if not value.get('target'):
            targets=[t for t,k in self.targets.items()if k==key]
            value['target']=targets[0]if len(targets)==1 else None
        if key in self.suspended:value['uiMonitoringSuspended']=True
        reason=('registry-unverified'if self.registry_failed else'worker-closed'if self.closed else
            self.reasons.get(key,'stopped')if key in self.stopped else'route-inactive'if value.get('routeInactive')is True else value.get('blockedReason'))
        if (key in self.stopped or self.closed or self.registry_failed
                or value.get('routeInactive')is True and (key in self.protected_keys or value.get('mode')!='ui')):
            value.update(mode='blocked',readsStopped=True)
        if value.get('mode')=='blocked':value['blockedReason']=reason or 'unknown'
        else:value.pop('blockedReason',None)
        return {'version':VERSION,'accountId':self.config.get('accountId'),'nativeChatKey':key,
                **value,'workerInstanceId':self.instance_id}

    def status(self):return [self._status(key) for key in sorted(self.controllers)]

    def set_route_active(self,target,active):
        self.sync_routes();key=self.targets.get(target)
        if key:
            if active is True:self.suspended.discard(key)
            else:self.suspended.add(key)

    def monitor_authority(self,target):
        if self.registry_failed:return {'version':1,'mode':'blocked','target':target}
        key=self.targets.get(target)
        if not key:return {'version':1,'mode':'blocked' if self.registry_failed else 'ui','target':target}
        value=self._status(key)
        if value.get('target')!=target or key in self.suspended:value.update(mode='blocked',readsStopped=True)
        return {**value,'target':target}

    def remember_registered_handoff(self,handoff,result,*,routes,registry):
        from native_group_ui_binding import confirm_registered_handoff
        key=handoff.native_chat_key
        proof=confirm_registered_handoff(self.config,handoff,result,routes=routes,registry=registry,
            previous=self.registered_ui_handoffs.get(key))
        self.registered_ui_handoffs[key]=proof
        return proof

    def _persist(self,key,events):
        folder=(self.controllers[key].paths()[0] if key is not None else
                absolute_data_path('config/native-group-text/unmapped',root=self.config['dataRoot']))
        directory=local_path(folder/'deferred');directory.mkdir(parents=True,exist_ok=True)
        file=local_path(directory/(uuid.uuid4().hex+'.json'));staging=local_path(file.with_suffix('.json.tmp'))
        value={'format':'whale-native-group-text-deferred','version':1,'accountId':self.config.get('accountId'),
               'nativeChatKey':key,'authority':self._status(key) if key is not None else {'mode':'blocked','version':1},'events':copy.deepcopy(events)}
        value['digest']=hashlib.sha256(_compact(value)).hexdigest()
        with staging.open('xb')as stream:stream.write(_compact(value));stream.flush();os.fsync(stream.fileno())
        os.replace(staging,file)
        if _read(file)[0]!=value:raise RuntimeError('readback-failed')
        self.saved.add(key)
        return file.relative_to(Path(self.config['dataRoot'])).as_posix()

    def reset(self,key):
        """Owner-requested reset of one blocked group back to UI receiving.

        The group's whole native folder (authority, inbox, transition, deferred UI
        messages, admission journals) is moved aside, never deleted, then the
        route is rebuilt from the registry exactly like a first start.
        """
        if self.registry_failed:raise RuntimeError('registry-unverified')
        if self._status(key).get('mode')!='blocked':raise RuntimeError('reset-not-blocked')
        controller=self.controllers[key]
        if self.deferred.get(key) and key not in self.saved:self._persist(key,self.deferred[key])
        close=getattr(controller,'close',None)
        if callable(close):close()
        folder=local_path(controller.paths()[0])
        archived=None
        if os.path.lexists(folder):
            root=local_path(absolute_data_path('config/native-group-text-reset',root=self.config['dataRoot']))
            root.mkdir(parents=True,exist_ok=True)
            target=local_path(root/(folder.name+'-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8]))
            try:os.replace(folder,target)
            except OSError:raise RuntimeError('reset-failed') from None
            archived=target.relative_to(Path(self.config['dataRoot'])).as_posix()
        for values in (self.stopped,self.protected_keys,self.pending_recovery_keys,self.saved):values.discard(key)
        for values in (self.controllers,self.deferred,self.bindings,self.previews,self.registered_ui_handoffs,self.reasons):values.pop(key,None)
        self.inventory=[r for r in self.inventory if r['nativeChatKey']!=key]
        self.sync_routes()
        self.emit({'event':'group-text-reset','nativeChatKey':key,'archive':archived})
        return archived

    def _block(self,key,events):
        self._stop(key,'authority-blocked')
        self.deferred[key]=copy.deepcopy(events)
        self.saved.discard(key)
        try:
            relative=self._persist(key,events)
            self.emit({'event':'group-text-blocked','code':'group-text-authority-blocked','nativeChatKey':key,
                       'deferredCount':len(events),'recoveryFile':relative})
        except Exception:
            self.emit({'event':'group-text-blocked','code':'group-text-deferral-persistence-failed','nativeChatKey':key})
        return False

    def route(self,event,*,_publish=None):
        # Other media/private routes remain exactly in their old publication path.
        key=self.targets.get(event.get('target'))
        publish=_publish or self.publish
        is_text=event.get('kind')=='group' and event.get('mediaType')=='text' and not any(event.get(k)for k in('images','files','audio'))
        if self.registry_failed and is_text:return self._block(None,[*self.deferred.get(None,[]),event])
        if not key or not is_text:
            publish(event);return True
        if key in self.stopped or key in self.suspended:return self._block(key,[*self.deferred[key],event])
        try:result=self.controllers[key].route_event(event)
        except Exception:return self._block(key,[*self.deferred[key],event])
        action=result.get('action')
        if action=='pass':publish(result['event']);return True
        if action=='deferred':self.deferred[key].append(copy.deepcopy(event));return True
        if action=='hint':
            self.emit({'event':'native-group-text-observed','id':event['id'],'target':event['target'],'kind':'group',
                       'nativeChatKey':key,'authorityEpoch':result['authorityEpoch']});return True
        return self._block(key,[*self.deferred[key],event])

    def route_batch(self,events):
        passed=[]
        for event in events:self.route(event,_publish=passed.append)
        if self.publish_batch:self.publish_batch(passed)
        else:
            for event in passed:self.publish(event)

    def _quiet(self,target,monitor):
        # No select/navigation: observe unread badges BEFORE any possible clearing.
        if monitor.routes.get(target)!='group':raise RuntimeError('prepare-not-quiet')
        getter=getattr(getattr(monitor,'wx',None),'GetSession',None)
        if not callable(getter):raise RuntimeError('prepare-not-quiet')
        rows=getter()
        if not isinstance(rows,(list,tuple)) or len(rows)>1000 or sum(getattr(r,'name',None)==target for r in rows)!=1:
            raise RuntimeError('prepare-not-quiet')
        sessions=monitor.sessions();info=sessions.get(target)
        if (type(info)is not dict or info.get('isnew')is not False or type(info.get('new_count'))is not int
                or info['new_count']!=0):raise RuntimeError('prepare-not-quiet')
        fingerprint=SessionGate.fingerprint(info)
        handled=getattr(getattr(monitor,'gate',None),'handled',None)
        if type(handled)is not dict or handled.get(target)!=fingerprint:raise RuntimeError('prepare-not-quiet')
        current=getattr(monitor,'current',None)
        if not callable(current):raise RuntimeError('prepare-not-quiet')
        if current()==target:
            controls=getattr(monitor,'controls',None)
            cursor=getattr(monitor,'cursor',None)
            if not callable(controls) or not callable(getattr(cursor,'pending',None)):
                raise RuntimeError('prepare-not-quiet')
            try:
                if cursor.pending(target,controls()):raise RuntimeError('prepare-not-quiet')
            except Exception:raise RuntimeError('prepare-not-quiet') from None
        for name in ('startup_pending','startup_uncertain','deferred_direction'):
            value=getattr(monitor,name,None)
            if value is None:raise RuntimeError('prepare-not-quiet')
            if target in value and (name in ('startup_pending','startup_uncertain') or bool(value[target])):raise RuntimeError('prepare-not-quiet')
        return fingerprint

    def command(self,command,*,monitor,delivery_active=False,independent=False):
        result={'event':'group-text-result','id':command.get('id'),'ok':False}
        try:
            op=command.get('op');extras={'group-text-status':set(),'group-text-prepare':set(),
                'group-text-abort':{'ticket'},'group-text-commit':{'ticket','authorityEpoch'},
                'group-text-admit':{'claimId','claimHash','authorityEpoch'},'group-text-reset':set()}
            if op not in OPS or set(command)!={'op','id','target','nativeChatKey'}|extras[op]:raise RuntimeError('request-invalid')
            if not isinstance(command['id'],str) or not command['id']:raise RuntimeError('request-invalid')
            if any(not isinstance(command[k],str)or not re.fullmatch('[a-f0-9]{64}'if k=='claimHash'else'[a-f0-9]{32}',command[k])for k in extras[op]):raise RuntimeError('request-invalid')
            self.sync_routes();key=command['nativeChatKey'];target=command['target']
            if self.targets.get(target)!=key or key not in self.controllers:raise RuntimeError('route-invalid')
            if (key in self.stopped or self.registry_failed or key in self.suspended and op!='group-text-admit') and op not in ('group-text-status','group-text-reset'):raise RuntimeError('blocked')
            controller=self.controllers[key];archived=None
            if op=='group-text-reset':
                if delivery_active or independent:raise RuntimeError('worker-busy')
                archived=self.reset(key)
            elif op=='group-text-admit':
                if independent:raise RuntimeError('worker-busy')
                if delivery_active:
                    # A correlated negative acknowledgement, strictly before
                    # source/UI proof verification, lease or marker mutation.
                    fingerprint=hashlib.sha256(_compact([command[k]for k in
                        ('op','id','target','nativeChatKey','claimId','claimHash','authorityEpoch')])).hexdigest()
                    result.update(issuance='not-issued',phase='before-admit',
                        workerInstanceId=self.instance_id,requestFingerprint=fingerprint)
                    raise RuntimeError('worker-busy')
                if key not in self.suspended:raise RuntimeError('blocked')
                from native_group_text_admission_source import verify_pending_text_admission_source
                proof=verify_pending_text_admission_source(self.config,key,command['claimId'],command['claimHash'])
                controller.admit(proof,command['authorityEpoch'],self.registered_ui_handoffs.get(key))
                self.protected_keys.add(key)
            elif op=='group-text-prepare':
                if delivery_active or independent:raise RuntimeError('worker-busy')
                fingerprint=self._quiet(target,monitor);controller.prepare();self.previews[key]=copy.deepcopy(fingerprint);self.protected_keys.add(key)
            elif op=='group-text-commit':
                if delivery_active or independent:raise RuntimeError('worker-busy')
                if self._quiet(target,monitor)!=self.previews.get(key):raise RuntimeError('prepare-not-quiet')
                controller.commit(command['ticket'],command['authorityEpoch'])
                self.previews.pop(key,None)
            elif op=='group-text-abort':
                aborted=controller.abort(command['ticket']);events=aborted['events']
                if events!=self.deferred[key]:raise RuntimeError('deferrals-unverified')
                drained=0
                for index,event in enumerate(events):
                    try:self.publish(copy.deepcopy(event));drained+=1
                    except Exception:
                        self._block(key,events[index:]);raise RuntimeError('blocked')
                controller.finish_abort(command['ticket'],drained);self.deferred[key]=[]
                self.previews.pop(key,None)
            value=(self._status(key)if key in self.controllers else
                {'version':VERSION,'accountId':self.config.get('accountId'),'nativeChatKey':key,'mode':'ui','target':target,
                 'authorityEpoch':None,'ticket':None,'dirty':False,'deferred':0,'readsStopped':False,'workerInstanceId':self.instance_id})
            if op=='group-text-reset':value['resetArchive']=archived
            if op=='group-text-status':
                value['admitReady']=(not delivery_active and not independent and value.get('mode')=='ui'
                    and key in self.suspended and key not in self.stopped and not self.registry_failed
                    and key in self.registered_ui_handoffs)
            result.update(ok=True,value=value)
        except Exception as error:
            key=command.get('nativeChatKey')
            if key in self.controllers and self.controllers[key].status().get('mode')=='blocked' and key not in self.stopped:
                self._block(key,self.deferred[key])
            code=str(error)if type(error)is RuntimeError and str(error)in {'request-invalid','route-invalid','blocked','worker-busy','prepare-not-quiet','deferrals-unverified','reset-not-blocked','registry-unverified','reset-failed'}else 'operation-denied'
            result['code']='group-text-'+code
        self.emit(result);return result

    def close(self):
        if self.closed:return
        self.closed=True
        for key,events in self.deferred.items():
            if events and key not in self.saved:self._block(key,events)
        for controller in self.controllers.values():
            close=getattr(controller,'close',None)
            if callable(close):
                try:close()
                except Exception:self.emit({'event':'group-text-blocked','code':'group-text-close-unverified'})
