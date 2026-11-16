"""Bound-instance, owner-only search observation. No final social actions.

All input methods remain the previously observed probe workflow. Import is inert.
"""
import hashlib
from contextlib import contextmanager
import importlib.util
import json
import re
from pathlib import Path
import time

ROOT=Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])
OBSERVED_VERSION='4.2.0.12'
OBSERVED_CLIENT_SHA='996c917dece7f99e167a6fbfb69af969247e5de14a994f0505243abb4f021a4c'


def load_probe_helpers():
    # Fixed local source, not a caller-supplied Python/plugin path.
    file=ROOT/'tools/probe_friend_search_readonly.py'
    spec=importlib.util.spec_from_file_location('_whale_friend_observe_helpers',file)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lookup_observed_ui(wx,account,display,**kwargs):
    """Existing read-only API: always close the caller-owned form on return."""
    with owned_lookup_context(wx,account,display,**kwargs):
        pass
    return kwargs['report']


@contextmanager
def owned_lookup_context(wx, account, display, *, routes, client, uia, windows,
                       client_probe, refresh_chat, helpers, report, strict_restore=False,
                       activate_bound_main=False,candidate_profile=False,plan_check=None):
    """Use only this already-bound wx object on the caller's existing UI thread."""
    from social_friend_search import FriendSearch
    from social_contact_observation import read_friend_search_result,read_contact_profile
    main=wx._api.control;pid=main.ProcessId;handle=main.NativeWindowHandle
    if main.ClassName!='mmui::MainWindow':raise RuntimeError('probe-bound-main-changed')
    unique,nodes,wait_for=helpers.unique,helpers.nodes,helpers.wait_for
    find_entry,find_form=helpers.find_entry,helpers.find_form
    verified_quick_menu,close_quick_menu=helpers.verified_quick_menu,helpers.close_quick_menu
    redact_observation,sha=helpers.redact_observation,helpers.sha
    running_client,refresh_chat_binding=client_probe,refresh_chat
    def owned_roots():return helpers.visible_owned_roots(uia,windows,pid)
    if any(c.NativeWindowHandle!=handle for c in owned_roots()):
        raise RuntimeError('probe-existing-owned-popup-must-be-closed')
    original=wx.ChatInfo();target=original.get('chat_name');kind=original.get('chat_type')
    if routes.get(target)!=kind:raise RuntimeError('probe-original-chat-not-allowlisted')
    report['restoreTargetSha256']=sha(target)
    form=None;form_identity=None;quick_opened=False;quick_identity=None
    def guard(control,expected_handle):
        if (control.ProcessId!=pid or control.NativeWindowHandle!=expected_handle
                or uia.GetForegroundWindow()!=expected_handle or control.IsOffscreen!=0):
            raise RuntimeError('probe-foreground-or-ownership-changed')
    def form_guard():
        guard(form,form_identity[0])
        if (tuple(form.GetRuntimeId())!=form_identity[1] or
                (form.ClassName,form.ControlTypeName,form.Name,form.AutomationId)!=
                ('mmui::AddFriendWindow','WindowControl','添加朋友','AddFriendWindow')):
            raise RuntimeError('probe-owned-form-changed')
    def close_button():
        button=unique([form],'mmui::XButton','关闭')
        if (button.ProcessId!=pid or button.ControlTypeName!='ButtonControl' or button.IsOffscreen!=0
                or button.IsEnabled not in (True,1)):
            raise RuntimeError('probe-owned-close-not-usable')
        return button
    try:
        if plan_check:plan_check()
        if activate_bound_main and uia.GetForegroundWindow()!=handle:
            identity=(pid,handle,tuple(main.GetRuntimeId()))
            wx.Show()  # Existing bound SDK instance; once, never global input.
            current=wx._api.control
            if (current.ProcessId,current.NativeWindowHandle,tuple(current.GetRuntimeId()))!=identity:
                raise RuntimeError('probe-bound-main-changed')
        guard(main,handle)
        report['navigationStarted']=True
        quick_opened=True
        unique([main],'mmui::XButton','快捷操作').Click(move=True,show_window=True)
        wait_for(lambda:find_entry(owned_roots(),pid))
        _,entry,quick_identity=verified_quick_menu(uia,owned_roots(),main,pid)
        entry.Click(move=True,show_window=True)
        form=wait_for(lambda:find_form(owned_roots(),pid))
        form_identity=(form.NativeWindowHandle,tuple(form.GetRuntimeId()));form_guard();quick_opened=False
        close=close_button()
        report['formIdentity']={'class':form.ClassName,'type':form.ControlTypeName,
            'handle':form_identity[0],'runtimeIdSha256':sha(json.dumps(form_identity[1])),
            'ownedByVerifiedClient':True,'closeButtonObserved':close.IsOffscreen==0}
        helper=FriendSearch(form,client_pid=pid,foreground=uia.GetForegroundWindow)
        if plan_check:plan_check()
        report['dispatch']=helper.dispatch(account)  # Exactly one search; no Enter/retry.
        result=None;stable_read=False
        report['identityEvidence']='unsupported-profile'
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            form_guard()
            stable_read=False
            try:
                current=read_friend_search_result(form,account,client_pid=pid,
                    window_handle=form_identity[0],foreground=uia.GetForegroundWindow)
                form_guard()
                if current['state']=='profile-observed':
                    profiles=[c for c in nodes(form) if c.ClassName=='mmui::ProfileHeaderUi']
                    if len(profiles)!=1:raise RuntimeError('contact-search-profile-missing-or-ambiguous')
                    if candidate_profile:
                        from social_contact_observation import read_candidate_profile
                        identity=read_candidate_profile(profiles[0],account)
                        report['identityEvidence']=identity['identityEvidence']
                    else:
                        identity=read_contact_profile(profiles[0],display,expected_wechat_id=account)
                    form_guard()
                    report['profileIdentityMatched']=identity['identityMatched']
                else:
                    report['profileIdentityMatched']=False
                    report['identityEvidence']='unsupported-profile'
                result=current;stable_read=True
                if report.get('profileIdentityMatched') or not candidate_profile and current['state']!='unknown':break
            except RuntimeError as error:
                if str(error) not in ('contact-search-controls-changed','contact-observation-controls-changed'):raise
                # Only content instability is retried, inside the original
                # deadline and exact same owned form. Never dispatch again.
                form_guard()
            time.sleep(.1)
        if not stable_read:raise RuntimeError('contact-search-observation-timeout')
        report['observation']=redact_observation(result)
        if candidate_profile:
            form_guard()
            report['profileButtons']=profile_button_metadata(form,pid,helpers.nodes)
            form_guard()
        report['helperReadPassed']=True
        yield {'wx':wx,'form':form,'formGuard':form_guard,'ownedRoots':owned_roots,
            'main':main,'helpers':helpers,'uia':uia,'pid':pid,'client':client,'account':account,
            'report':report}
    finally:
        if form is not None and form_identity is not None:
            try:
                form_guard()
                close_button().Click(move=True,show_window=False)
                wait_for(lambda:not any(c.NativeWindowHandle==form_identity[0] for c in owned_roots()))
                report['ownedFormClosed']=True
            except Exception as error:report['cleanupErrorType']=type(error).__name__
        elif quick_opened:
            # Only the observed quick-action menu may receive Esc, never an
            # unrecognized popup or a global keyboard event.
            try:
                report['ownedQuickMenuClosed']=close_quick_menu(uia,owned_roots(),main,pid,quick_identity)
            except Exception as error:report['cleanupErrorType']=type(error).__name__
        if report.get('ownedFormClosed') or report.get('ownedQuickMenuClosed'):
            try:
                if any(c.NativeWindowHandle!=handle for c in owned_roots()):raise RuntimeError('probe-popup-still-open')
                if running_client(wx)['clientSha256']!=client['clientSha256']:raise RuntimeError('probe-client-changed')
                refresh_chat_binding(wx,target,kind)
                if wx.ChatInfo()!=original: # Extra informational keys may change; compare exact route below.
                    current=wx.ChatInfo()
                    if current.get('chat_name')!=target or current.get('chat_type')!=kind:raise RuntimeError('probe-chat-restore-failed')
                report['originalChatRestored']=True
            except Exception as error:report['restoreErrorType']=type(error).__name__
        if strict_restore and report.get('navigationStarted') and not report.get('originalChatRestored'):
            raise RuntimeError('social-chat-restore-failed')
    return report


def profile_button_metadata(form,pid,nodes):
    """Read actual button metadata twice; semantic labels never invoke actions."""
    def read():
        values=[]
        for node in nodes(form):
            if node.ControlTypeName!='ButtonControl' or node.ProcessId!=pid or node.IsOffscreen!=0:continue
            cls,name=node.ClassName,node.Name
            if not isinstance(cls,str) or not re.fullmatch(r'[\w:]{1,120}',cls) or not isinstance(name,str):
                raise RuntimeError('probe-profile-button-unreadable')
            values.append({'class':cls,'type':'ButtonControl',
                'nameSha256':hashlib.sha256(name.encode('utf8')).hexdigest(),
                'label':{'添加到通讯录':'add-contact','发消息':'send-message'}.get(name,'unknown')})
            if len(values)>128:raise RuntimeError('probe-profile-button-budget')
        return values
    before=read()
    if read()!=before:raise RuntimeError('probe-profile-buttons-changed')
    return before


def observe_owner(wx,config,groups,**kwargs):
    return _observe(wx,config,groups,**kwargs)


def observe_plan(wx,config,groups,*,plan,plan_check,**kwargs):
    return _observe(wx,config,groups,plan=plan,plan_check=plan_check,**kwargs)


def owned_plan_lookup(wx,config,groups,*,plan,plan_check,**kwargs):
    """Unregistered candidate seam; caller must close it in an outer finally."""
    return _observe(wx,config,groups,plan=plan,plan_check=plan_check,context_only=True,**kwargs)


def _observe(wx, config, groups, *, client, client_probe, refresh_chat, helpers=None,
                  uia=None, windows=None,plan=None,plan_check=None,context_only=False):
    if (client.get('clientVersion')!=OBSERVED_VERSION
            or client.get('clientSha256')!=OBSERVED_CLIENT_SHA):
        raise RuntimeError('social-observe-client-unverified')
    helpers=helpers or load_probe_helpers()
    from data_paths import resolve_data_paths
    from group_discovery import discover_groups
    paths=resolve_data_paths(config)
    discovery=discover_groups(paths['root']/'snapshots/windows-live/latest.json',config['accountId'],
        expected_account_username=config.get('capture',{}).get('accountUsername'))
    manifest=json.loads((paths['root']/'snapshots/windows-live/snapshots'/discovery['snapshotId']/'snapshot-manifest.json').read_text(encoding='utf8'))
    if manifest.get('source_id')!=config.get('capture',{}).get('sourceId'):
        raise RuntimeError('social-observe-source-mismatch')
    if plan is None:
        account,display=helpers.owner_identity(config,current_account_username=manifest.get('account_username'))
    else:
        if not callable(plan_check):raise RuntimeError('social-observe-plan-invalid')
        plan_check()
        account,display=plan['parameters']['keywords'],None
    purpose='friend-search-plan' if plan is not None else 'friend-search-owner'
    if uia is None:
        from wxauto4 import uia
    if windows is None:
        from wxauto4.utils import GetAllWindows
        windows=GetAllWindows
    routes={config['target']:'friend',**{name:'group' for name in groups}}
    report={'purpose':purpose,'readOnly':True,'applicationSubmitted':False,
            'friendAccepted':False,'sentMessages':False,'liveCapabilityGranted':False,
            'querySha256':hashlib.sha256(account.encode('utf8')).hexdigest()}
    lookup_args={'routes':routes,'client':client,'uia':uia,'windows':windows,'client_probe':client_probe,
        'refresh_chat':refresh_chat,'helpers':helpers,'report':report,'strict_restore':True,
        'activate_bound_main':True,'candidate_profile':plan is not None,'plan_check':plan_check}
    if context_only:return owned_lookup_context(wx,account,display,**lookup_args)
    lookup_observed_ui(wx,account,display,**lookup_args)
    if plan_check:plan_check()
    observation=report.get('observation') or {}
    return {'purpose':purpose,'readOnly':True,
        **({'planId':plan['id'],'planHash':plan['hash'],'profileButtons':report.get('profileButtons',[]),
            'identityEvidence':report.get('identityEvidence','unsupported-profile')} if plan is not None else {}),
        'querySha256':report['querySha256'],
        'clientVersion':client['clientVersion'],'clientSha256':client['clientSha256'],
        'state':observation.get('state') if observation.get('state') in ('unknown','no-match-observed','profile-observed') else 'unknown',
        'queryValueMatched':observation.get('queryValueMatched') is True,
        'profileIdentityMatched':report.get('profileIdentityMatched') is True,
        'responseFreshnessVerified':False,'newSearchCompleted':False,
        'accountMatched':False,'applicationSubmitted':False,'friendAccepted':False,
        'sentMessages':False,'liveCapabilityGranted':False,
        'chatRestored':report.get('originalChatRestored') is True,
        'restoreTargetSha256':report.get('restoreTargetSha256'),
        'controls':observation.get('controls',[])}
