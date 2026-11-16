import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
function load(react={}){
  let module;
  vm.runInNewContext(fs.readFileSync(new URL('./client.js',import.meta.url),'utf8'),{
    window:{__ModuleLoader__:{load(value){module=value.factory(()=>react)}}},AbortSignal,
  });return module;
}
test('settings request uses authenticated native fetch and a single fixed-purpose body',async()=>{
  const calls=[],module=load();
  const native={fetch:async(...args)=>{calls.push(args);return {ok:true,json:async()=>({ok:true,value:{chatRestored:true}})}}};
  const transport=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},__DSH_TRANSPORT__:native,
    fetch:()=>assert.fail('must prefer native carrier')});
  assert.equal((await transport.observe()).chatRestored,true);
  assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/local-dsh-wxauto/social/observe');
  assert.equal(calls[0][1].credentials,'same-origin');
  assert.deepEqual(JSON.parse(calls[0][1].body),{purpose:'friend-search-owner'});
  assert.deepEqual(Object.keys(calls[0][1].headers),['content-type']);
});

test('owner text status is fixed GET and activation fixed empty POST sharing original pending lock',async()=>{
  const calls=[],module=load();let finish;
  const transport=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},fetch:(url,args)=>{
    calls.push([url,args]);if(calls.length===1)return Promise.resolve({ok:true,json:async()=>({ok:true,value:{authorityMode:'ui',canActivate:true}})});
    return new Promise(r=>finish=r);
  }});
  await transport.cutoverStatus();assert.equal(calls[0][0],'/api/local-dsh-wxauto/owner-text-cutover');assert.equal(calls[0][1].method,'GET');assert.equal(calls[0][1].body,undefined);
  const pending=transport.activateNativeText();await assert.rejects(transport.observe(),/in-progress/);
  assert.equal(calls[1][0],'/api/local-dsh-wxauto/owner-text-cutover/activate');assert.equal(calls[1][1].method,'POST');assert.deepEqual(JSON.parse(calls[1][1].body),{});
  finish({ok:false,json:async()=>({ok:false,code:'owner-text-cutover-outcome-unknown'})});await assert.rejects(pending,/outcome-unknown/);
  assert.equal(calls.length,2);
});

test('settings activation is enabled only for explicitly ready UI mode and existing buttons remain',()=>{
  for(const [state,unknown,disabled] of [[null,false,true],[{canActivate:true,authorityMode:'native'},false,true],
    [{canActivate:false,authorityMode:'ui'},false,true],[{canActivate:true,authorityMode:'ui'},false,false],[{canActivate:true,authorityMode:'ui'},true,true]]){
    let index=0;const values=[false,'',false,null,'',state,'',unknown];
    const module=load({createElement:(type,props,...children)=>({type,props,children}),useRef:()=>({current:null}),useState:()=>[values[index++],()=>{}]});
    const tree=module.SettingsPage(),nodes=[];const walk=n=>{if(n&&typeof n==='object'){nodes.push(n);for(const child of n.children??[])walk(child);}};walk(tree);
    const button=nodes.find(n=>n.type==='button'&&n.children[0]==='切换为原生文字接收');assert.equal(button.props.disabled,disabled);
    assert.ok(nodes.some(n=>n.children?.[0]==='检查好友搜索'));assert.ok(nodes.some(n=>n.children?.[0]==='载入待添加好友方案'));
  }
});

test('working job polls only GET for the same attempt until terminal, never posts again',async()=>{
  const module=load(),attemptId='a'.repeat(32),calls=[];let clock=0;
  const transport=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},fetch:async(url,args)=>{
    calls.push([url,args.method]);const outcome=calls.length<4?'working':'active';
    return {ok:true,json:async()=>({ok:true,value:{outcome,attemptId}})};
  }});
  const started=await transport.activateNativeText();
  const result=await module.pollCutoverJob({transport,attemptId:started.attemptId,now:()=>clock,wait:async ms=>clock+=ms});
  assert.equal(result.outcome,'active');assert.deepEqual(calls.map(v=>v[1]),['POST','GET','GET','GET']);
});

test('job poll cancels or times out without another POST or accepting another attempt',async()=>{
  const module=load(),attemptId='a'.repeat(32);let reads=0,clock=0;const abort=new AbortController();
  const transport={cutoverStatus:async()=>{reads++;return {outcome:'working',attemptId};}};
  await assert.rejects(module.pollCutoverJob({transport,attemptId,now:()=>clock,timeoutMs:2500,wait:async ms=>clock+=ms}),/outcome-unknown/);
  assert.equal(reads,2);reads=0;
  await assert.rejects(module.pollCutoverJob({transport,attemptId,signal:abort.signal,wait:async()=>abort.abort()}),/poll-cancelled/);assert.equal(reads,0);
  await assert.rejects(module.pollCutoverJob({transport:{cutoverStatus:async()=>({outcome:'active',attemptId:'b'.repeat(32)})},attemptId,wait:async()=>{}}),/result-invalid/);
});
test('untrusted page and unavailable carrier cannot invoke diagnostics',async()=>{
  const module=load();
  for(const location of [undefined,{protocol:'https:',hostname:'untrusted.example'},{protocol:'file:',hostname:''}]){
    const transport=module.createObservationTransport({location,fetch:()=>assert.fail('no request')});
    await assert.rejects(transport.observe(),/native-transport-unavailable/);
  }
});
test('duplicate UI actions and failed requests are never automatically retried',async()=>{
  let complete,calls=0;const module=load();
  const transport=module.createObservationTransport({location:{protocol:'http:',hostname:'127.0.0.1'},
    fetch:()=>{calls++;return new Promise(resolve=>{complete=resolve})}});
  const pending=transport.observe();await assert.rejects(transport.observe(),/in-progress/);
  complete({ok:false,json:async()=>({ok:false,code:'social-host-request-rejected'})});
  await assert.rejects(pending,/host-request-rejected/);assert.equal(calls,1);
});
test('prepared plan metadata loads read-only; lookup sends IDs and hash instead of arbitrary account',async()=>{
  const calls=[],module=load(),plan={planId:'1'.repeat(32),hash:'a'.repeat(64),account:'_Test50K'};
  const transport=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},
    fetch:async(...args)=>{calls.push(args);return {ok:true,json:async()=>({ok:true,value:plan})}}});
  await transport.preparedPlan();assert.equal(calls[0][1].method,'GET');assert.equal(calls[0][1].body,undefined);
  await transport.observePlan(plan);assert.deepEqual(JSON.parse(calls[1][1].body),{purpose:'friend-search-plan',planId:plan.planId,hash:plan.hash});
  await assert.rejects(transport.observePlan({...plan,planId:'../private'}),/invalid-request/);assert.equal(calls.length,2);
});
test('approval and initial test carry only the reviewed plan identity, never a target or action override',async()=>{
  const calls=[],module=load(),plan={planId:'1'.repeat(32),hash:'a'.repeat(64),account:'_Test50K'};
  const transport=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},
    fetch:async(...args)=>{calls.push(args);return {ok:true,json:async()=>({ok:true,value:{outcome:'sent'}})}}});
  await transport.requestApproval(plan);await transport.initialAcceptance(plan);
  assert.equal(calls[0][0],'/api/local-dsh-wxauto/social/friend-lookup-plan/approval');
  assert.equal(calls[1][0],'/api/local-dsh-wxauto/social/friend-lookup-plan/acceptance');
  for(const [,args] of calls)assert.deepEqual(JSON.parse(args.body),{planId:plan.planId,hash:plan.hash});
  await assert.rejects(transport.initialAcceptance({...plan,planId:'forged'}),/invalid-request/);assert.equal(calls.length,2);
});

test('full request approval and submission use distinct fixed routes with no action override',async()=>{
  const calls=[],module=load(),plan={planId:'1'.repeat(32),hash:'a'.repeat(64)};
  const transport=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},
    fetch:async(...args)=>{calls.push(args);return {ok:true,json:async()=>({ok:true,value:{}})}}});
  await transport.requestFullApproval(plan);await transport.fullAcceptance(plan);
  assert.deepEqual(calls.map(([url])=>url),['/api/local-dsh-wxauto/social/friend-lookup-plan/request-approval',
    '/api/local-dsh-wxauto/social/friend-lookup-plan/request-acceptance']);
  for(const [,args] of calls)assert.deepEqual(JSON.parse(args.body),plan);
  await assert.rejects(transport.fullAcceptance({...plan,planId:'../other'}),/invalid-request/);
  assert.equal(calls.length,2);
});

test('group list/status/activation use native owner carrier and exact trusted-key bodies',async()=>{
  const module=load(),calls=[],key='wechat:123@chatroom';
  const transport=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},
    __DSH_TRANSPORT__:{fetch:async(url,args)=>{calls.push([url,args]);return {ok:true,json:async()=>({ok:true,
      value:url.endsWith('group-text-routes')?[{nativeChatKey:key,target:'Fixture group'}]:{authorityMode:'ui',canActivate:true}})}}},
    fetch:()=>assert.fail('must not bypass native carrier')});
  const routes=await transport.groupTextRoutes();assert.equal(routes[0].nativeChatKey,key);
  await transport.groupTextStatus(key);await transport.activateGroupText(key);
  assert.deepEqual(calls.map(([url,args])=>[url,args.method]),[
    ['/api/local-dsh-wxauto/group-text-routes','GET'],['/api/local-dsh-wxauto/group-text-cutover','POST'],
    ['/api/local-dsh-wxauto/group-text-cutover/activate','POST']]);
  assert.equal(calls[0][1].body,undefined);
  for(const [,args]of calls.slice(1)){assert.deepEqual(JSON.parse(args.body),{nativeChatKey:key});assert.deepEqual(Object.keys(args.headers),['content-type']);}
  for(const invalid of ['wechat:owner','../group','wechat:other@chatroom/../../x',null])await assert.rejects(transport.activateGroupText(invalid),/invalid-request/);
  assert.equal(calls.length,3);
});

test('malformed/ambiguous group lists cannot populate selector or add private fields',async()=>{
  const module=load(),row={nativeChatKey:'wechat:123@chatroom',target:'Fixture group'};
  for(const value of [[row,{...row,target:'Duplicate'}],[row,{nativeChatKey:'wechat:456@chatroom',target:row.target}],
    [{...row,accountId:'must not project'}],[{...row,nativeChatKey:'wechat:owner'}]]){
    const transport=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},
      fetch:async()=>({ok:true,json:async()=>({ok:true,value})})});
    await assert.rejects(transport.groupTextRoutes(),/routes-invalid/);
  }
});

test('group polling posts read-only status for one key/attempt and never repeats activation',async()=>{
  const module=load(),calls=[],key='wechat:123@chatroom',attemptId='c'.repeat(32);let clock=0;
  const transport=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},fetch:async(url,args)=>{
    calls.push([url,args]);return {ok:true,json:async()=>({ok:true,value:{outcome:calls.length<4?'working':'active',attemptId,nativeChatKey:key}})};
  }});
  const started=await transport.activateGroupText(key);
  const result=await module.pollGroupCutoverJob({transport,nativeChatKey:key,attemptId:started.attemptId,now:()=>clock,wait:async ms=>clock+=ms});
  assert.equal(result.outcome,'active');assert.equal(calls.filter(([url])=>url.endsWith('/activate')).length,1);
  assert.equal(calls.slice(1).every(([url,args])=>url==='/api/local-dsh-wxauto/group-text-cutover'&&args.method==='POST'&&JSON.parse(args.body).nativeChatKey===key),true);
  await assert.rejects(module.pollGroupCutoverJob({transport:{groupTextStatus:async()=>({outcome:'active',attemptId:'d'.repeat(32)})},nativeChatKey:key,attemptId,wait:async()=>{}}),/result-invalid/);
  await assert.rejects(module.pollGroupCutoverJob({transport:{groupTextStatus:async()=>({outcome:'active',attemptId,nativeChatKey:'wechat:456@chatroom'})},nativeChatKey:key,attemptId,wait:async()=>{}}),/result-invalid/);
});

test('group polling timeout/cancel and shared pending lock issue no activation retry',async()=>{
  const module=load(),key='wechat:123@chatroom',attemptId='c'.repeat(32);let clock=0,reads=0;
  const transport={groupTextStatus:async()=>{reads++;return {outcome:'working',attemptId};}};
  await assert.rejects(module.pollGroupCutoverJob({transport,nativeChatKey:key,attemptId,now:()=>clock,timeoutMs:2500,wait:async ms=>clock+=ms}),/outcome-unknown/);
  assert.equal(reads,2);const abort=new AbortController();reads=0;
  await assert.rejects(module.pollGroupCutoverJob({transport,nativeChatKey:key,attemptId,signal:abort.signal,wait:async()=>abort.abort()}),/poll-cancelled/);assert.equal(reads,0);
  let finish,calls=0;const pending=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},fetch:()=>{calls++;return new Promise(r=>finish=r);}});
  const request=pending.groupTextStatus(key);await assert.rejects(pending.activateGroupText(key),/in-progress/);await assert.rejects(pending.observe(),/in-progress/);
  finish({ok:true,json:async()=>({ok:true,value:{}})});await request;assert.equal(calls,1);
});

function pageHarness(transport){
  const states=[],refs=[],cleanups=[];let si=0,ri=0,ei=0;
  const module=load({createElement:(type,props,...children)=>({type,props,children}),
    useState:initial=>{const i=si++;if(!(i in states))states[i]=initial;return [states[i],v=>{states[i]=typeof v==='function'?v(states[i]):v;}];},
    useRef:initial=>{const i=ri++;if(!refs[i])refs[i]={current:i===0?transport:initial};return refs[i];},
    useEffect:fn=>{const i=ei++;if(!(i in cleanups))cleanups[i]=fn();}});
  const render=()=>{si=ri=ei=0;const tree=module.SettingsPage(),nodes=[];const visit=n=>{if(n&&typeof n==='object'){nodes.push(n);for(const c of n.children??[])visit(c);}};visit(tree);return nodes;};
  return {render,button:(nodes,label)=>nodes.find(n=>n.type==='button'&&n.children[0]===label),unmount:()=>cleanups.forEach(fn=>fn?.())};
}

test('group UI uses only trusted options, prevents duplicate activate and keeps unknown locked after a fresh status read',async()=>{
  const key='wechat:123@chatroom';let activations=0,finish;
  const transport={groupTextRoutes:async()=>[{nativeChatKey:key,target:'Trusted fixture group'}],
    groupTextStatus:async()=>({authorityMode:'ui',canActivate:true}),
    activateGroupText:()=>{activations++;return new Promise(r=>finish=r);}};
  const page=pageHarness(transport);let nodes=page.render();
  assert.equal(page.button(nodes,'减少头像取名操作').props.disabled,true);
  await page.button(nodes,'读取群接收状态').props.onClick();nodes=page.render();
  assert.ok(nodes.some(n=>n.type==='option'&&n.children[0]==='Trusted fixture group'));
  assert.equal(page.button(nodes,'减少头像取名操作').props.disabled,false);
  const handler=page.button(nodes,'减少头像取名操作').props.onClick,pending=handler();await handler();assert.equal(activations,1);
  finish({outcome:'unknown',authorityMode:'blocked',canActivate:false});await pending;nodes=page.render();
  assert.equal(page.button(nodes,'减少头像取名操作').props.disabled,true);
  await page.button(nodes,'读取群接收状态').props.onClick();nodes=page.render();
  assert.equal(page.button(nodes,'减少头像取名操作').props.disabled,true);
  await page.button(nodes,'减少头像取名操作').props.onClick();assert.equal(activations,1);
  for(const label of ['检查好友搜索','读取文字接收状态','载入待添加好友方案','检查朋友圈界面'])assert.ok(page.button(nodes,label));
});

test('unmount while activation is pending does not start a new status poll',async()=>{
  const key='wechat:123@chatroom';let finish,reads=0;
  const page=pageHarness({groupTextRoutes:async()=>[{nativeChatKey:key,target:'Fixture'}],
    groupTextStatus:async()=>{reads++;return {authorityMode:'ui',canActivate:true};},activateGroupText:()=>new Promise(r=>finish=r)});
  let nodes=page.render();await page.button(nodes,'读取群接收状态').props.onClick();nodes=page.render();
  const pending=page.button(nodes,'减少头像取名操作').props.onClick();page.unmount();finish({outcome:'working',attemptId:'f'.repeat(32)});await pending;
  assert.equal(reads,1);
});
