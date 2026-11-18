window.__ModuleLoader__.load({id:'local-dsh-wxauto',factory:require=>{
  const React=require('react');
  const {createElement:h,useState,useRef,useEffect}=React;
  const path='/api/local-dsh-wxauto/social/observe';
  const planPath='/api/local-dsh-wxauto/social/friend-lookup-plan';
  const approvalPath=planPath+'/approval',acceptancePath=planPath+'/acceptance';
  const cutoverPath='/api/local-dsh-wxauto/owner-text-cutover';
  const groupCutoverPath='/api/local-dsh-wxauto/group-text-cutover',groupRoutesPath='/api/local-dsh-wxauto/group-text-routes';
  const validGroupKey=key=>typeof key==='string'&&/^wechat:[A-Za-z0-9_-]{1,256}@chatroom$/.test(key);
  // Plain-language reason for a blocked group; the code is kept for logs and issues.
  const blockedReasons=[
    [/route-(session-or-chat-)?changed/,'这个群的会话或聊天编号和切换时登记的不一致（常见原因：手改过 group-routes.local.json、groupSessions 或会话目录）'],
    [/registry-unverified/,'群登记文件 group-routes.local.json 或 native-group-text 目录读不懂'],
    [/pending-recovery-files/,'上次切换中途留下了待恢复的消息文件'],
    [/route-(revoked|inactive)/,'这个群已不在授权列表里，但还留着原生接收记录'],
    [/commit-unknown|lease-unknown/,'切换写入的结果不确定'],
    [/restore|marker-changed|inbox-unverified|native-(epoch-changed|state-regressed|authority-invalid)/,'重启后核对原生接收记录失败'],
    [/closed-while-preparing/,'切换进行到一半时插件被关闭'],
    [/too-many-deferred/,'切换期间积压的群消息太多'],
    [/authority-blocked/,'读取已停止，暂存的群消息已保存到恢复文件'],
    [/cutover-outcome-unknown/,'切换结果未知'],
  ];
  const blockedReasonText=code=>{if(typeof code!=='string'||!code)return '';
    const found=blockedReasons.find(([pattern])=>pattern.test(code));return '原因：'+(found?found[1]:'未归类')+'（'+code+'）。';};
  function createObservationTransport(globals=globalThis){
    const location=globals.location;
    const local=['http:','https:'].includes(location?.protocol)&&['127.0.0.1','localhost','[::1]'].includes(location.hostname);
    const desktop=['dsh-app:','dsh:'].includes(location?.protocol)&&location.hostname==='app';
    const carrier=globals.__DSH_TRANSPORT__;
    const fetcher=(local||desktop)&&(typeof carrier?.fetch==='function'?carrier.fetch.bind(carrier):globals.fetch?.bind(globals));
    let pending=false;
    async function request(url,args,signal){
      if(!fetcher)throw Error('social-native-transport-unavailable');
      if(pending)throw Error('social-observation-in-progress');
      pending=true;
      try{
        const response=await fetcher(url,{method:args?'POST':'GET',credentials:'same-origin',cache:'no-store',
          signal:signal?AbortSignal.any([signal,AbortSignal.timeout(55000)]):AbortSignal.timeout(55000),headers:{'content-type':'application/json'},
          ...(args?{body:JSON.stringify(args)}:{})});
        const value=await response.json();
        if(!response.ok||value?.ok!==true)throw Error(/^(?:social|owner-text|group-text)-[a-z0-9-]{1,100}$/.test(value?.code??'')?value.code:'social-observation-failed');
        return value.value;
      }finally{pending=false;}
    }
    const planArgs=plan=>{
      if(!/^[0-9a-f]{32}$/.test(plan?.planId??'')||!/^[0-9a-f]{64}$/.test(plan?.hash??''))throw Error('social-invalid-request');
      return {planId:plan.planId,hash:plan.hash};
    };
    const groupArgs=key=>{if(!validGroupKey(key))throw Error('group-text-invalid-request');return {nativeChatKey:key};};
    return {observe:()=>request(path,{purpose:'friend-search-owner'}),observeMoments:()=>request(path,{purpose:'moments-feed-metadata'}),
      cutoverStatus:({signal}={})=>request(cutoverPath,undefined,signal),activateNativeText:()=>request(cutoverPath+'/activate',{}),
      groupTextRoutes:async()=>{
        const routes=await request(groupRoutesPath),keys=new Set(),targets=new Set();
        if(!Array.isArray(routes)||routes.length>1000)throw Error('group-text-routes-invalid');
        return routes.map(row=>{
          if(!row||Object.keys(row).length!==2||!validGroupKey(row.nativeChatKey)||typeof row.target!=='string'||!row.target.trim()
            ||row.target.length>512||row.target.includes('\0')||keys.has(row.nativeChatKey)||targets.has(row.target))throw Error('group-text-routes-invalid');
          keys.add(row.nativeChatKey);targets.add(row.target);return {nativeChatKey:row.nativeChatKey,target:row.target};
        });
      },
      groupTextStatus:async(key,{signal}={})=>request(groupCutoverPath,groupArgs(key),signal),
      activateGroupText:async key=>request(groupCutoverPath+'/activate',groupArgs(key)),
      resetGroupText:async key=>request(groupCutoverPath+'/reset',groupArgs(key)),
      preparedPlan:()=>request(planPath),
      requestApproval:async plan=>request(approvalPath,planArgs(plan)),
      initialAcceptance:async plan=>request(acceptancePath,planArgs(plan)),
      requestFullApproval:async plan=>request(planPath+'/request-approval',planArgs(plan)),
      fullAcceptance:async plan=>request(planPath+'/request-acceptance',planArgs(plan)),
      observePlan:plan=>{
        if(!/^[0-9a-f]{32}$/.test(plan?.planId??'')||!/^[0-9a-f]{64}$/.test(plan?.hash??''))return Promise.reject(Error('social-invalid-request'));
        return request(path,{purpose:'friend-search-plan',planId:plan.planId,hash:plan.hash});
      }};
  }
  async function pollCutoverJob({transport,attemptId,signal,wait=ms=>new Promise(resolve=>setTimeout(resolve,ms)),now=Date.now,timeoutMs=15*60*1000}){
    if(!/^[a-f0-9]{32}$/.test(attemptId??''))throw Error('owner-text-cutover-result-invalid');
    const started=now();
    while(!signal?.aborted&&now()-started<timeoutMs){
      await wait(1000);if(signal?.aborted)throw Error('owner-text-cutover-poll-cancelled');
      if(now()-started>=timeoutMs)throw Error('owner-text-cutover-outcome-unknown');
      const value=await transport.cutoverStatus({signal});
      if(value?.attemptId!==attemptId||!['working','active','aborted','refused','unknown'].includes(value.outcome))throw Error('owner-text-cutover-result-invalid');
      if(value.outcome!=='working')return value;
    }
    throw Error(signal?.aborted?'owner-text-cutover-poll-cancelled':'owner-text-cutover-outcome-unknown');
  }
  async function pollGroupCutoverJob({transport,nativeChatKey,attemptId,signal,wait=ms=>new Promise(resolve=>setTimeout(resolve,ms)),now=Date.now,timeoutMs=15*60*1000}){
    if(!validGroupKey(nativeChatKey)||!/^[a-f0-9]{32}$/.test(attemptId??''))throw Error('group-text-cutover-result-invalid');
    const started=now();
    while(!signal?.aborted&&now()-started<timeoutMs){
      await wait(1000);if(signal?.aborted)throw Error('group-text-cutover-poll-cancelled');
      if(now()-started>=timeoutMs)throw Error('group-text-cutover-outcome-unknown');
      const value=await transport.groupTextStatus(nativeChatKey,{signal});
      if(value?.attemptId!==attemptId||(value.nativeChatKey!==undefined&&value.nativeChatKey!==nativeChatKey)
        ||!['working','active','aborted','refused','unknown'].includes(value.outcome))throw Error('group-text-cutover-result-invalid');
      if(value.outcome!=='working')return value;
    }
    throw Error(signal?.aborted?'group-text-cutover-poll-cancelled':'group-text-cutover-outcome-unknown');
  }
  function SettingsPage(){
    const transport=useRef(null);if(!transport.current)transport.current=createObservationTransport();
    const [busy,setBusy]=useState(false),[message,setMessage]=useState('尚未检查。');
    const [unknown,setUnknown]=useState(false);
    const [plan,setPlan]=useState(null),[planMessage,setPlanMessage]=useState('');
    const [cutover,setCutover]=useState(null),[cutoverMessage,setCutoverMessage]=useState('尚未读取接收状态。'),[cutoverUnknown,setCutoverUnknown]=useState(false);
    const [momentsMessage,setMomentsMessage]=useState('尚未检查朋友圈读取。');
    const pollAbort=useRef(null),mounted=useRef(true);
    if(typeof useEffect==='function')useEffect(()=>{mounted.current=true;return ()=>{mounted.current=false;pollAbort.current?.abort();};},[]);
    // Append new hooks after the original page hooks; old settings keep their state.
    const [groupRoutes,setGroupRoutes]=useState([]),[groupKey,setGroupKey]=useState(''),[groupState,setGroupState]=useState(null);
    const [groupMessage,setGroupMessage]=useState('尚未读取群接收状态。'),[groupUnknown,setGroupUnknown]=useState([]);
    const groupPollAbort=useRef(null),groupAction=useRef(false),groupUnsafe=useRef(null);
    if(!groupUnsafe.current)groupUnsafe.current=new Set();
    if(typeof useEffect==='function')useEffect(()=>()=>groupPollAbort.current?.abort(),[]);
    const trustedGroups=Array.isArray(groupRoutes)?groupRoutes:[];
    const groupLocked=key=>groupUnsafe.current.has(key)||(Array.isArray(groupUnknown)&&groupUnknown.includes(key));
    function unknownGroup(key){groupUnsafe.current.add(key);setGroupUnknown(old=>[...new Set([...(Array.isArray(old)?old:[]),key])]);}
    async function finishGroup(key,value){
      if(!mounted.current)return;
      if(value?.outcome==='working'){
        setGroupMessage('正在切换，继续读取同一次任务状态，不会重复提交。');
        groupPollAbort.current=new AbortController();
        value=await pollGroupCutoverJob({transport:transport.current,nativeChatKey:key,attemptId:value.attemptId,signal:groupPollAbort.current.signal});
      }
      if(!mounted.current||groupPollAbort.current?.signal.aborted)return;
      if(!value||!['ui','native','blocked'].includes(value.authorityMode))throw Error('group-text-cutover-result-invalid');
      setGroupState({...value,nativeChatKey:key});
      if(value.outcome==='unknown'){
        unknownGroup(key);setGroupMessage('该群切换结果未知，不会自动重试。'+blockedReasonText(value.blockedReason)+'确认原因后可以点「复位该群」回到界面接收。');
      }else if(value.authorityMode==='blocked'){
        setGroupMessage('该群接收状态受阻，已停止读取。'+blockedReasonText(value.blockedReason)+'处理好原因后可以点「复位该群」回到界面接收。');
      }else if(value.authorityMode==='native'&&value.nativeActive===false&&value.blockedReason){
        setGroupMessage('后台已是原生接收，但插件这边没有接上。'+blockedReasonText(value.blockedReason)+'可以点「复位该群」回到界面接收。');
      }else if(value.authorityMode==='native')setGroupMessage('该群已使用原生文字接收；仍需一条新消息验证实际收发。');
      else if(value.outcome==='aborted'||value.outcome==='refused')setGroupMessage('本次未确认启用，请先重新读取该群状态。'+(value.cause?'（失败原因：'+value.cause+'）':''));
      else setGroupMessage('该群当前由界面接收文字；状态允许时可减少头像取名操作。');
    }
    async function resetGroupText(){
      const key=groupKey;
      if(groupAction.current||!trustedGroups.some(row=>row.nativeChatKey===key))return;
      groupAction.current=true;setBusy(true);groupPollAbort.current=null;
      setGroupMessage('正在复位该群：原生接收记录会移到 WhaleData 下的 config/native-group-text-reset 备份，不会删除……');
      try{
        const value=await transport.current.resetGroupText(key);if(!mounted.current)return;
        if(value?.authorityMode==='ui'){
          groupUnsafe.current.delete(key);setGroupUnknown(old=>(Array.isArray(old)?old:[]).filter(k=>k!==key));
          setGroupState({...value,nativeChatKey:key});setGroupMessage('已复位，该群回到界面接收文字。在群里 @ 她，插件会重新自动切换。');
        }else{setGroupState({...value,nativeChatKey:key});setGroupMessage('复位后仍受阻。'+blockedReasonText(value?.blockedReason));}
      }catch(error){if(mounted.current)setGroupMessage('复位未完成（'+String(error?.message||'unknown').slice(0,80)+'），该群保持原状。');}
      finally{groupAction.current=false;if(mounted.current)setBusy(false);}
    }
    async function loadGroupStatus(){
      if(groupAction.current)return;groupAction.current=true;setBusy(true);groupPollAbort.current=null;setGroupState(null);
      let key=groupKey;
      try{
        const routes=await transport.current.groupTextRoutes();if(!mounted.current)return;setGroupRoutes(routes);
        key=routes.some(row=>row.nativeChatKey===key)?key:routes[0]?.nativeChatKey??'';setGroupKey(key);
        if(!key){setGroupMessage('没有可用的已授权群聊。');return;}
        await finishGroup(key,await transport.current.groupTextStatus(key));
      }catch{if(mounted.current&&!groupPollAbort.current?.signal.aborted){
        if(groupPollAbort.current)unknownGroup(key);
        setGroupMessage('无法确认群接收状态，未重复执行切换。');
      }}finally{groupAction.current=false;if(mounted.current)setBusy(false);}
    }
    async function activateGroupText(){
      const key=groupKey;
      if(groupAction.current||groupLocked(key)||!trustedGroups.some(row=>row.nativeChatKey===key)
        ||groupState?.nativeChatKey!==key||groupState.canActivate!==true||groupState.authorityMode!=='ui')return;
      groupAction.current=true;setBusy(true);groupPollAbort.current=null;setGroupState(null);
      setGroupMessage('正在核对该群静默状态并切换文字接收……');
      try{await finishGroup(key,await transport.current.activateGroupText(key));}
      catch{if(mounted.current&&!groupPollAbort.current?.signal.aborted){unknownGroup(key);setGroupMessage('该群切换结果未确认，不会重试或重置；请读取状态。');}}
      finally{groupAction.current=false;if(mounted.current)setBusy(false);}
    }
    async function loadCutover(){
      setBusy(true);
      try{const value=await transport.current.cutoverStatus();setCutover(value);
        setCutoverMessage(value.authorityMode==='native'?'原生文字接收已启用；尚需新消息验证实际收发。'
          :value.authorityMode==='ui'?'当前由界面接收文字。只有状态允许时才能切换。':'文字接收状态受阻，请先检查，不要重复切换。');
      }catch{setCutoverMessage('无法确认接收状态，未执行切换。');}finally{setBusy(false);}
    }
    async function activateNativeText(){
      setBusy(true);setCutoverMessage('正在核对静默边界并切换文字接收……');
      try{let result=await transport.current.activateNativeText();if(!mounted.current)return;setCutover(null);
        if(result.outcome==='working'){
          setCutoverMessage('正在切换，继续读取同一次任务的状态；不会重复提交。');
          pollAbort.current=new AbortController();
          result=await pollCutoverJob({transport:transport.current,attemptId:result.attemptId,signal:pollAbort.current.signal});
          if(pollAbort.current.signal.aborted)return;
        }
        if(result.outcome==='active')setCutoverMessage('原生文字接收已启用；这不代表新消息收发已经验证，请读取状态后用一条新消息验证。');
        else if(result.outcome==='unknown'){setCutoverUnknown(true);setCutoverMessage('切换结果未知，不会自动重试或重置。请先核对状态。');}
        else setCutoverMessage(result.outcome==='aborted'?'本次切换已中止，没有确认启用。请先读取状态。':'本次未执行切换，请先读取状态并检查待处理任务。');
      }catch{if(!mounted.current||pollAbort.current?.signal.aborted)return;setCutoverUnknown(true);setCutoverMessage('未确认切换结果，不会自动重试或重置。请先核对状态。');}finally{if(mounted.current&&!pollAbort.current?.signal.aborted)setBusy(false);}
    }
    async function observe(){
      setBusy(true);setMessage('正在检查，请稍候……');
      try{
        const result=await transport.current.observe();
        setMessage(result.chatRestored
          ?'检查已结束，已返回原聊天。好友操作仍待完整验证，尚未启用。'
          :'检查未确认返回原聊天，请先核对微信窗口，暂勿重复操作。');
        if(!result.chatRestored)setUnknown(true);
      }catch{
        setUnknown(true);
        setMessage('未确认检查完成，请先核对状态，暂勿重复操作。');
      }finally{setBusy(false);}
    }
    async function observeMoments(){
      setBusy(true);setMomentsMessage('正在检查朋友圈读取……');
      try{
        const result=await transport.current.observeMoments();
        if(result.purpose!=='moments-feed-metadata'||result.chatRestored!==true)throw Error('social-observation-result-invalid');
        setMomentsMessage('界面读取检查完成，已返回原聊天；帖子内容仍待验证。');
      }catch{setUnknown(true);setMomentsMessage('未确认检查完成，请先核对微信状态，暂勿重复操作。');}
      finally{setBusy(false);}
    }
    async function loadPlan(){
      setBusy(true);
      try{
        const value=await transport.current.preparedPlan();setPlan(value);
        setPlanMessage(value.lastRequestObservation?.state==='pending-verification'
          ?'最近一次核对：好友申请已提交，等待对方通过。尚未确认成为好友，不会重复提交。'
          :value.status==='approved'?'已收到微信私聊确认，可进行本次测试。':value.status==='pending'
          ?'仅核对资料不会添加好友；实际测试需要先在微信私聊确认。':'原操作已结束，本次仅可核对资料，不能重复执行添加。');
      }
      catch{setPlanMessage('暂时没有可核对的有效方案。');}
      finally{setBusy(false);}
    }
    async function requestApproval(){
      setBusy(true);
      try{
        const result=await transport.current.requestApproval(plan);
        setPlanMessage(result.outcome==='sent'?'确认请求已发到你的微信私聊，尚未执行添加。确认后请刷新方案。'
          :result.outcome==='already-approved'?'微信确认已通过，请刷新方案。':'确认请求未再次发送，请先查看微信中的既有请求，暂勿重复操作。');
        if(!['sent','already-approved'].includes(result.outcome))setUnknown(true);
      }catch{setUnknown(true);setPlanMessage('未确认请求结果，暂勿重复发送确认请求。');}
      finally{setBusy(false);}
    }
    async function initialAcceptance(){
      setBusy(true);setPlanMessage('正在进行本次加好友测试……');
      try{
        const result=await transport.current.initialAcceptance(plan);
        setUnknown(true);
        setPlanMessage(result.executionStatus==='not-dispatched'?'本次没有执行，请先刷新方案核对确认状态。'
          :'本次结果尚未确认，已停止处理，不会自动重复。请先核对微信状态。');
      }catch{setUnknown(true);setPlanMessage('本次结果未确认，暂勿重复操作。');}
      finally{setBusy(false);}
    }
    async function requestFullApproval(){
      setBusy(true);
      try{
        const result=await transport.current.requestFullApproval(plan);
        setPlanMessage(result.outcome==='sent'?'完整申请的确认请求已发到你的微信私聊。批准后请刷新方案。'
          :'确认请求未再次发送，请核对既有请求；原有批准不会自动升级为完整提交授权。');
        if(result.outcome!=='sent')setUnknown(true);
      }catch{setUnknown(true);setPlanMessage('未确认请求结果，暂勿重复发送。');}
      finally{setBusy(false);}
    }
    async function fullAcceptance(){
      setBusy(true);setPlanMessage('正在核对并提交本次好友申请……');
      try{
        const result=await transport.current.fullAcceptance(plan);
        setUnknown(true);
        setPlanMessage(result.status==='confirmed'&&result.applicationSubmittedVerified===true&&result.friendshipConfirmed===false
          ?result.chatRestored===true?'好友申请已提交，等待对方通过。尚未确认成为好友，不会重复提交。'
            :'好友申请已提交，但未能确认返回原聊天。已停止后续操作，请核对微信窗口，不要重复提交。'
          :result.executionStatus==='not-dispatched'?'本次没有执行，请刷新方案核对确认状态。'
          :'本次结果尚未确认，已停止，不会自动重复提交。');
      }catch{setUnknown(true);setPlanMessage('本次结果未确认，暂勿重复操作。');}
      finally{setBusy(false);}
    }
    async function observePlan(){
      setBusy(true);setPlanMessage('正在核对账号资料……');
      try{
        const result=await transport.current.observePlan(plan);
        setPlanMessage(result.chatRestored
          ?result.profileIdentityMatched?'账号资料已匹配，已返回原聊天。本次只进行了资料核对。'
            :result.state==='profile-observed'?'已显示候选资料，但账号信息尚未核对成功。已返回原聊天，本次只进行了资料核对。'
            :'未确认找到匹配账号，已返回原聊天。本次没有执行添加操作。'
          :'未确认返回原聊天，请先核对微信窗口。');
        if(!result.chatRestored)setUnknown(true);
      }catch{setUnknown(true);setPlanMessage('未确认核对完成，暂勿重复操作。');}
      finally{setBusy(false);}
    }
    return h('div',{style:{maxWidth:720,display:'flex',flexDirection:'column',gap:12}},
      h('h2',null,'微信连接'),
      h('p',null,'检查会临时打开“添加朋友”，搜索已配置的主人，然后返回原聊天。不会发送消息或好友申请。'),
      h('button',{type:'button',disabled:busy||unknown,onClick:observe},busy?'正在检查……':'检查好友搜索'),
      h('p',{'aria-live':'polite'},message),
      h('h3',null,'朋友圈读取'),
      h('p',null,'检查会临时打开朋友圈，再返回原聊天，不发表、点赞或评论。'),
      h('button',{type:'button',disabled:busy||unknown,onClick:observeMoments},'检查朋友圈界面'),
      h('p',{'aria-live':'polite'},momentsMessage),
      h('h3',null,'本地文字接收'),
      h('button',{type:'button',disabled:busy,onClick:loadCutover},'读取文字接收状态'),
      h('button',{type:'button',disabled:busy||cutoverUnknown||cutover?.canActivate!==true||cutover?.authorityMode!=='ui',onClick:activateNativeText},'切换为原生文字接收'),
      h('p',{'aria-live':'polite'},cutoverMessage),
      h('h3',null,'群聊文字接收'),
      h('label',null,'选择群聊 ',h('select',{value:groupKey||'',disabled:busy||trustedGroups.length===0,onChange:event=>{
        if(groupAction.current)return;const key=event.target.value;
        if(trustedGroups.some(row=>row.nativeChatKey===key)){setGroupKey(key);setGroupState(null);setGroupMessage('已选择群聊，请读取该群接收状态。');}
      }},h('option',{value:'',disabled:true},'先读取群接收状态'),...trustedGroups.map(row=>h('option',{key:row.nativeChatKey,value:row.nativeChatKey},row.target)))),
      h('button',{type:'button',disabled:busy,onClick:loadGroupStatus},'读取群接收状态'),
      h('button',{type:'button',disabled:busy||groupLocked(groupKey)||groupState?.nativeChatKey!==groupKey||groupState?.canActivate!==true||groupState?.authorityMode!=='ui',onClick:activateGroupText},'减少头像取名操作'),
      h('button',{type:'button',disabled:busy||!groupKey||!(groupLocked(groupKey)||groupState?.nativeChatKey===groupKey&&(groupState?.authorityMode==='blocked'||groupState?.authorityMode==='native'&&groupState?.nativeActive===false&&!!groupState?.blockedReason)),onClick:resetGroupText},'复位该群'),
      h('p',{'aria-live':'polite'},groupMessage),
      h('h3',null,'待添加好友'),
      h('button',{type:'button',disabled:busy,onClick:loadPlan},'载入待添加好友方案'),
      plan&&h('div',null,h('p',null,'微信号：'+plan.account),h('p',null,'验证信息：'+plan.greeting),
        plan.expectedProfileName&&h('p',null,'昵称：'+plan.expectedProfileName),
        h('p',null,'添加前会通过你的微信私聊确认。'),
        h('button',{type:'button',disabled:busy||unknown,onClick:observePlan},'核对好友资料'),
        h('p',null,'首次测试会尝试点击“添加到通讯录”；出现尚未识别的申请窗口时会停止，不自动重复。'),
        h('button',{type:'button',disabled:busy||unknown||plan.status!=='pending',onClick:requestApproval},'请求微信私聊确认'),
        h('button',{type:'button',disabled:busy||unknown||plan.status!=='approved'||plan.approvalPurpose==='add-friend-request',onClick:initialAcceptance},'开始首次加好友测试'),
        h('p',null,'完整申请会核对资料、填写验证信息并提交一次，需要另行通过私聊确认完整方案。'),
        h('button',{type:'button',disabled:busy||unknown||plan.status!=='pending'||plan.approvalNoticeClaimed===true,onClick:requestFullApproval},'请求完整申请确认'),
        h('button',{type:'button',disabled:busy||unknown||plan.status!=='approved'||plan.approvalPurpose!=='add-friend-request',onClick:fullAcceptance},'提交已批准的完整申请')),
      h('p',{'aria-live':'polite'},planMessage));
  }
  function apply(ctx){
    ctx.effect(()=>ctx.slots.inject('settings.section',()=>ctx.slots.register(
      {name:'settings.section',id:'wxauto-connection',order:18,label:()=> '微信连接'},SettingsPage)));
  }
  return {inject:['slots'],apply,SettingsPage,createObservationTransport,pollCutoverJob,pollGroupCutoverJob};
}});
