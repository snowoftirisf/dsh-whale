import test from 'node:test';
import assert from 'node:assert/strict';
import {createDynamicGroupRegistry,dynamicGroupSessionId} from './dynamic-groups.mjs';

const candidate=(key='a',name='New group')=>({accountId:'account-a',nativeChatKey:'wechat:'+key+'@chatroom',
  displayName:name,membershipConfirmed:true,eligible:true});
function fixture(options={}){
  let state=null;const calls=[];
  const deps={accountId:'account-a',existing:[],load:async()=>structuredClone(state),
    save:async value=>{calls.push(['save',value.groups.map(r=>r.status)]);state=structuredClone(value);},
    ensureChat:async p=>{calls.push(['ensure',p.nativeChatKey]);return {id:'canonical-'+p.nativeChatKey};},
    activate:async r=>{calls.push(['activate',r.sessionId]);},...options};
  const registry=createDynamicGroupRegistry(deps);
  return {registry,calls,deps,get state(){return state;},set state(v){state=v;}};
}

test('pending persisted before ensure/activate; active is final snapshot gate',async()=>{
  const f=fixture();const [out]=await f.registry.discover([candidate()]);
  assert.equal(out.status,'active');assert.equal(f.registry.snapshot().length,1);
  assert.deepEqual(f.calls.map(c=>c[0]),['save','ensure','save','activate','save']);
  assert.equal(f.state.groups[0].status,'active');
  assert.equal(out.record.sessionId,dynamicGroupSessionId('account-a',candidate().nativeChatKey));
  const snap=f.registry.snapshot();snap[0].target='mutated';assert.equal(f.registry.snapshot()[0].target,'New group');
});

test('concurrent repeated discovery activates once and does not mutate producer data',async()=>{
  let release;const wait=new Promise(r=>release=r);let entered;
  const start=new Promise(r=>entered=r);
  const f=fixture({activate:async r=>{f.calls.push(['activate',r.sessionId]);entered();await wait;}});
  const c=candidate();const p=f.registry.discover([c]);const second=f.registry.discover([c]);
  c.displayName='producer mutation';await start;
  assert.deepEqual(f.registry.snapshot(),[]);release();
  const [a,b]=await Promise.all([p,second]);assert.equal(a[0].status,'active');assert.equal(b[0].status,'active');
  assert.equal(f.calls.filter(c=>c[0]==='activate').length,1);
  assert.equal(f.registry.snapshot()[0].target,'New group');
});

test('ensure or activation failure remains pending and restart uses same session',async()=>{
  const f=fixture({ensureChat:async()=>{throw Error('private detail');}});
  const [failed]=await f.registry.discover([candidate()]);
  assert.equal(failed.status,'pending');assert.equal(f.registry.snapshot().length,0);
  const restarted=fixture({load:async()=>f.state});
  const [ok]=await restarted.registry.discover([candidate()]);assert.equal(ok.status,'active');
  assert.equal(ok.record.sessionId,failed.record.sessionId);
  const g=fixture({activate:async()=>{throw Error('private detail');}});
  assert.equal((await g.registry.discover([candidate()]))[0].status,'pending');
  assert.deepEqual(g.registry.snapshot(),[]);
});

test('final save waits without exposing active; failed save rolls back preparation',async()=>{
  let saveCount=0,release,entered;const wait=new Promise(r=>release=r),start=new Promise(r=>entered=r);let rollback=0;
  const f=fixture({save:async value=>{saveCount++;if(saveCount===3){entered();await wait;throw Error('disk failure');}},
    activate:async()=>async()=>{rollback++;}});
  const p=f.registry.discover([candidate()]);await start;
  assert.deepEqual(f.registry.snapshot(),[]);release();
  assert.equal((await p)[0].status,'pending');assert.deepEqual(f.registry.snapshot(),[]);assert.equal(rollback,1);
});

test('saved dynamic active requires fresh membership/eligibility and new preparation after restart',async()=>{
  const f=fixture();await f.registry.discover([candidate()]);
  const restarted=fixture({load:async()=>f.state});
  await restarted.registry.discover([]);assert.deepEqual(restarted.registry.snapshot(),[]);
  await restarted.registry.discover([{...candidate(),membershipConfirmed:false}]);assert.deepEqual(restarted.registry.snapshot(),[]);
  const [out]=await restarted.registry.discover([candidate()]);assert.equal(out.status,'active');
  assert.equal(restarted.calls.filter(c=>c[0]==='activate').length,1);
});

test('same display name never merges native identities or existing unnamed mapping',async()=>{
  const f=fixture();const out=await f.registry.discover([candidate('a','same'),candidate('b','same')]);
  assert.ok(out.every(o=>o.status==='unresolved'));assert.deepEqual(f.registry.snapshot(),[]);
  assert.equal(f.calls.filter(c=>c[0]==='activate').length,0);
  const g=fixture({existing:[{target:'same',sessionId:'old-group',chatId:'old-id'}]});
  assert.equal((await g.registry.discover([candidate('other','same')]))[0].status,'unresolved');
  assert.deepEqual(g.registry.snapshot().map(r=>r.sessionId),['old-group']);
});

test('later duplicate display name removes dynamic authorization until uniquely resolved',async()=>{
  const f=fixture();await f.registry.discover([candidate('a','same')]);
  assert.equal(f.registry.snapshot().length,1);
  const outcomes=await f.registry.discover([candidate('a','same'),candidate('b','same')]);
  assert.ok(outcomes.every(o=>o.status==='unresolved'));assert.deepEqual(f.registry.snapshot(),[]);
  assert.equal(f.calls.filter(c=>c[0]==='activate').length,1);
});

test('dynamic ineligible candidate cannot keep stale target active',async()=>{
  const f=fixture();await f.registry.discover([candidate()]);
  assert.equal((await f.registry.discover([{...candidate(),eligible:false}]))[0].status,'pending');
  assert.deepEqual(f.registry.snapshot(),[]);
});

test('canonical chat ID can bind original session despite ineligible native display label',async()=>{
  const f=fixture({existing:[{target:'Approved original group',sessionId:'original-id',chatId:'canonical-old'}],
    ensureChat:async()=>({id:'canonical-old'})});
  const c={...candidate(),displayName:'',eligible:false};
  const [out]=await f.registry.discover([c]);
  assert.equal(out.status,'active');assert.equal(out.code,'dynamic-groups-existing-canonical-bound');
  assert.equal(out.record.sessionId,'original-id');assert.equal(out.record.target,'Approved original group');
  assert.equal(out.record.nativeChatKey,c.nativeChatKey);
  assert.equal(f.calls.filter(c=>c[0]==='activate').length,0);
  const restarted=fixture({existing:f.deps.existing,load:async()=>f.state});
  await restarted.registry.discover([]);assert.equal(restarted.registry.snapshot()[0].sessionId,'original-id');
});

test('wrong account or unconfirmed membership never ensures/activates',async()=>{
  const f=fixture();const out=await f.registry.discover([{...candidate(),accountId:'other'},
    {...candidate(),membershipConfirmed:false},{...candidate(),nativeChatKey:'not-native'}]);
  assert.ok(out.every(o=>o.status==='unresolved'));assert.deepEqual(f.calls,[]);
});

test('rename is explicit unresolved state, keeps native/session identity, never activates new name',async()=>{
  const f=fixture();const [old]=await f.registry.discover([candidate()]);
  const [renamed]=await f.registry.discover([candidate('a','Renamed group')]);
  assert.equal(renamed.code,'dynamic-groups-rename-required');assert.equal(renamed.record.status,'rename-required');
  assert.equal(renamed.record.sessionId,old.record.sessionId);assert.equal(renamed.record.desiredTarget,'Renamed group');
  assert.deepEqual(f.registry.snapshot(),[]);assert.equal(f.calls.filter(c=>c[0]==='activate').length,1);
  const f2=fixture();await f2.registry.discover([candidate('b','Renamed group')]);
  assert.notEqual(f2.registry.snapshot()[0].sessionId,old.record.sessionId);
});

test('untrusted persisted session or foreign account is rejected without activation',async()=>{
  const state={version:1,accountId:'account-a',groups:[{accountId:'account-a',nativeChatKey:candidate().nativeChatKey,
    target:'name',sessionId:'forged-owner',status:'active'}]};
  const f=fixture({load:async()=>state});await assert.rejects(f.registry.discover([candidate()]),/untrusted-session/);
  assert.equal(f.calls.length,0);
  const g=fixture({load:async()=>({...state,accountId:'other'})});
  await assert.rejects(g.registry.discover([]),/invalid-state/);
});
