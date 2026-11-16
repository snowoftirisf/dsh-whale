"""Read-only readers for observed 4.2 contact header, picker and search state.

No wxauto initialization, action methods, bindings or acceptance receipts.
Runtime IDs establish observation stability only, never contact identity.
"""
import re
import time
import hashlib

DISPLAY_ID='right_v_view.nickname_button_view.display_name_text'
ROW_ID='right_v_view.user_info_center_view.basic_line_view'
KEY_ID=ROW_ID+'.basic_line.key_text'
VALUE_ID=ROW_ID+'.ProfileTextView'
PICKER_TITLE='WeChat发起群聊'


def _snapshot(root,extra=None):
    count=0
    def read(control,depth):
        nonlocal count
        count+=1
        if count>6000 or depth>45:raise RuntimeError('contact-observation-tree-budget')
        runtime=control.GetRuntimeId()
        if not isinstance(runtime,(list,tuple)) or not runtime:raise RuntimeError('contact-observation-runtime-unavailable')
        node={'class':control.ClassName,'type':control.ControlTypeName,'name':control.Name,
              'id':control.AutomationId,'runtimeId':list(runtime)}
        if extra:node.update(extra(control))
        node['children']=[read(child,depth+1) for child in control.GetChildren()]
        return node
    return read(root,0)


def _stable(root):
    first=_snapshot(root)
    if first!=_snapshot(root):raise RuntimeError('contact-observation-controls-changed')
    return first


def _walk(node):
    yield node
    for child in node['children']:yield from _walk(child)


def _one(nodes,**fields):
    found=[n for n in nodes if all(n.get(k)==v for k,v in fields.items())]
    if len(found)!=1:raise RuntimeError('contact-observation-control-missing-or-ambiguous')
    return found[0]


def _describe(node):return {k:v for k,v in node.items() if k!='children'}


def _base(replay,clock):
    return {'source':'wxauto-uia-offline-replay' if replay else 'wxauto-uia',
            'readOnly':True,'liveAcceptance':False,'actionAuthorized':False,'observedAt':clock()}


def read_contact_profile(root,expected_display_name,*,expected_wechat_id=None,replay=False,clock=time.time):
    return _read_contact_profile(root,expected_display_name,expected_wechat_id=expected_wechat_id,
        replay=replay,clock=clock,trusted_identity=True)


def read_candidate_profile(root,expected_query,*,replay=False,clock=time.time):
    """Candidate display comes from this actual header, never request/nickname guesses."""
    from social_friend_search import valid_search_query
    if not valid_search_query(expected_query):raise ValueError('contact-search-account-invalid')
    snapshot=_stable(root)
    def result(evidence):
        return {**_base(replay,clock),'identityEvidence':evidence,'identityMatched':evidence=='matched'}
    headers=[n for n in _walk(snapshot) if (n['class'],n['type'])==('mmui::ProfileHeaderUi','GroupControl')]
    if len(headers)!=1:return result('unsupported-profile')
    nodes=list(_walk(headers[0]))
    displays=[n for n in nodes if (n['class'],n['type'],n['id'])==('mmui::XTextView','TextControl',DISPLAY_ID)]
    avatars=[n for n in nodes if (n['class'],n['type'])==('mmui::ContactHeadView','ButtonControl')]
    if (len(displays)!=1 or len(avatars)!=1 or not displays[0]['name'].strip()
            or avatars[0]['name']!=displays[0]['name']):return result('unsupported-profile')
    labels=[n for n in nodes if n['class']=='mmui::XTextView' and n['name'].strip()=='WeChat ID:']
    if not labels:return result('id-unavailable')
    if len(labels)!=1:return result('id-ambiguous')
    values=[]
    for row in nodes:
        if row['class']!='mmui::XHBoxView' or row['id']!=ROW_ID:continue
        keys=[n for n in row['children'] if (n['class'],n['type'],n['id'])==('mmui::XTextView','TextControl',KEY_ID)]
        if any(n['name'].strip()=='WeChat ID:' for n in keys):
            if len(keys)!=1:return result('id-ambiguous')
            values.extend(n for n in row['children'] if (n['class'],n['type'],n['id'])==('mmui::ProfileTextView','TextControl',VALUE_ID))
    if not values:return result('id-unavailable')
    if len(values)!=1:return result('id-ambiguous')
    if not valid_search_query(values[0]['name']):return result('id-invalid')
    return result('matched' if values[0]['name']==expected_query else 'id-mismatch')


def _read_contact_profile(root,expected_display_name,*,expected_wechat_id=None,replay=False,clock=time.time,
                          trusted_identity=True):
    """Return locally observed identity, never an approval or actionable binding.

    An optional trusted expected ID allows exact identity matching. Without it,
    the value is only a candidate requiring separate trusted resolution/approval.
    """
    if not isinstance(expected_display_name,str) or not expected_display_name.strip():raise ValueError('contact-expected-display-required')
    if expected_wechat_id is not None and (not isinstance(expected_wechat_id,str) or not expected_wechat_id.strip()):raise ValueError('contact-expected-id-invalid')
    snapshot=_stable(root)
    header=_one(_walk(snapshot),**{'class':'mmui::ProfileHeaderUi','type':'GroupControl'})
    display=_one(_walk(header),**{'class':'mmui::XTextView','type':'TextControl','id':DISPLAY_ID})
    if display['name']!=expected_display_name:raise RuntimeError('contact-display-mismatch')
    avatar=_one(_walk(header),**{'class':'mmui::ContactHeadView','type':'ButtonControl'})
    if avatar['name']!=expected_display_name:raise RuntimeError('contact-avatar-display-mismatch')
    # Region and ID rows reuse the same automation IDs. Only the actual parent
    # row's direct key/value pair can bind the ID, not a flat descendant search.
    rows=[n for n in _walk(header) if n['class']=='mmui::XHBoxView' and n['id']==ROW_ID]
    labels=[n for n in _walk(header) if n['class']=='mmui::XTextView' and n['name'].strip()=='WeChat ID:']
    if len(labels)!=1:raise RuntimeError('contact-identity-label-missing-or-ambiguous')
    matched=[]
    for row in rows:
        keys=[n for n in row['children'] if n['class']=='mmui::XTextView' and n['type']=='TextControl' and n['id']==KEY_ID]
        if any(n['name'].strip()=='WeChat ID:' for n in keys):
            if len(keys)!=1:raise RuntimeError('contact-identity-label-ambiguous')
            value=_one(row['children'],**{'class':'mmui::ProfileTextView','type':'TextControl','id':VALUE_ID})
            matched.append((row,keys[0],value))
    if len(matched)!=1:raise RuntimeError('contact-identity-row-missing-or-ambiguous')
    row,key,value=matched[0];wechat_id=value['name']
    from social_friend_search import valid_search_query
    valid_id=(isinstance(wechat_id,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{4,127}',wechat_id)) if trusted_identity else valid_search_query(wechat_id)
    if not valid_id:raise RuntimeError('contact-identity-value-invalid')
    if expected_wechat_id is not None and wechat_id!=expected_wechat_id:raise RuntimeError('contact-identity-mismatch')
    return {**_base(replay,clock),'kind':'contact-profile-identity','displayName':display['name'],
        'wechatId':wechat_id,'identityMatched':expected_wechat_id is not None,
        'controls':[_describe(n) for n in (header,display,avatar,row,key,value)],
        'missingEvidence':['No friendship/request status or remote action completion verified.',
                           'Without a trusted expected ID this is an identity candidate, not authority.']}


def read_group_creation_picker(root,*,replay=False,clock=time.time):
    snapshot=_stable(root)
    picker=_one(_walk(snapshot),**{'class':'mmui::SessionPickerWindow','type':'WindowControl'})
    if picker['name']!=PICKER_TITLE:raise RuntimeError('contact-picker-purpose-mismatch')
    nodes=list(_walk(picker))
    search=_one(nodes,**{'class':'mmui::XValidatorTextEdit','type':'EditControl','name':'搜索'})
    selected=_one(nodes,**{'class':'mmui::XTableView','type':'TextControl','id':'sp_choice_contact_list'})
    if selected['name']!='已选择0个联系人':raise RuntimeError('contact-picker-not-empty-or-count-unavailable')
    cancel=_one(nodes,**{'class':'mmui::XOutlineButton','type':'ButtonControl','name':'取消','id':'cancel_btn'})
    confirm=_one(nodes,**{'class':'mmui::XButton','type':'ButtonControl','name':'完成','id':'confirm_btn'})
    checkboxes=[n for n in nodes if n['type']=='CheckBoxControl']
    return {**_base(replay,clock),'kind':'group-creation-picker','title':picker['name'],
        'selectedCount':0,'contacts':[],'candidateCheckboxCount':len(checkboxes),
        'contactsResolved':False,'searchValueRead':False,
        'controls':[_describe(n) for n in (picker,search,selected,cancel,confirm)],
        'missingEvidence':['Checkbox names/runtime IDs are not stable contact identities.',
            'No approved selected identity set or created group/member readback.',
            'Control presence does not prove click/invoke/cancel/submit behavior.']}


def read_friend_search_result(window,expected_account,*,client_pid,window_handle,foreground,
                              replay=False,clock=time.time):
    """Observe the currently displayed search state, never new-search completion.

    Even an unchanged old negative result with matching input remains only
    no-match-observed. No click token, elapsed time or RuntimeID is promoted
    into proof that a new remote lookup has completed.
    """
    from social_friend_search import valid_search_query
    if not valid_search_query(expected_account):
        raise ValueError('contact-search-account-invalid')
    if type(client_pid) is not int or client_pid<=0 or type(window_handle) is not int or window_handle<=0 or not callable(foreground):
        raise ValueError('contact-search-window-identity-required')
    def identity():
        if (window.ClassName,window.ControlTypeName,window.Name,window.AutomationId)!=('mmui::AddFriendWindow','WindowControl','添加朋友','AddFriendWindow'):
            raise RuntimeError('contact-search-window-mismatch')
        value=(window.ProcessId,window.NativeWindowHandle,tuple(window.GetRuntimeId()))
        if value[:2]!=(client_pid,window_handle) or not value[2] or foreground()!=window_handle:
            raise RuntimeError('contact-search-window-changed-or-not-foreground')
        return value
    initial=identity()
    def extra(control):
        values={'processId':control.ProcessId,'offscreen':control.IsOffscreen}
        if control.ClassName=='mmui::XValidatorTextEdit' and control.Name=='搜索':
            values['queryValue']=control.GetValuePattern().Value
        return values
    tree=_snapshot(window,extra)
    if identity()!=initial or tree!=_snapshot(window,extra) or identity()!=initial:
        raise RuntimeError('contact-search-controls-changed')
    nodes=list(_walk(tree))
    edit=_one(nodes,**{'class':'mmui::XValidatorTextEdit','type':'EditControl','name':'搜索'})
    if edit.get('queryValue')!=expected_account:raise RuntimeError('contact-search-query-mismatch')
    negative=[n for n in nodes if (n['class'],n['type'],n['name'])==('mmui::XTextView','TextControl','找不到相关账号或内容')]
    profiles=[n for n in nodes if n['class']=='mmui::ProfileHeaderUi']
    if len(negative)>1 or len(profiles)>1 or (negative and profiles):raise RuntimeError('contact-search-result-ambiguous')
    if profiles and profiles[0]['type']!='GroupControl':raise RuntimeError('contact-search-profile-type-mismatch')
    evidence=[tree,edit,*negative,*profiles]
    if any(n['processId']!=client_pid or type(n['offscreen']) not in (bool,int) or n['offscreen']!=0 for n in evidence):
        raise RuntimeError('contact-search-evidence-not-visible-or-owned')
    # A profile's presence alone is not a resolved identity. The separately
    # observed profile reader must be used with a trusted target before acting.
    state='no-match-observed' if negative else 'profile-observed' if profiles else 'unknown'
    return {**_base(replay,clock),'kind':'friend-search-result-observation','state':state,
        'querySha256':hashlib.sha256(expected_account.encode()).hexdigest(),
        'windowIdentity':{'processId':client_pid,'windowHandle':window_handle,'runtimeId':list(initial[2])},
        'queryValueMatched':True,'newSearchCompleted':False,'responseFreshnessVerified':False,
        'accountMatched':False,'applicationSubmitted':False,'friendshipConfirmed':False,
        'controls':[{'class':n['class'],'type':n['type'],'id':n['id'],'runtimeId':n['runtimeId']} for n in evidence],
        'missingEvidence':['Current result may predate this query; no response transition or dispatch correlation is certified.',
                           'Profile presence is not target identity or accepted friendship; final social actions remain unavailable.']}
