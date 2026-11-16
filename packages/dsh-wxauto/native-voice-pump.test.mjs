import test from 'node:test';
import assert from 'node:assert/strict';
import {createNativeVoicePump} from './native-voice-pump.mjs';

const deferred=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {promise,resolve,reject};};
const flush=async()=>{for(let i=0;i<40;i++)await Promise.resolve();};
function clock(){
  let time=0,next=0;const timers=new Map();
  return {
    now:()=>time,setTimer:(callback,delay)=>{const id=++next;timers.set(id,{callback,due:time+delay});return id;},
    clearTimer:id=>timers.delete(id),pending:()=>[...timers.values()].map(t=>t.due),
    async to(target){
      assert.ok(target>=time);
      for(let guard=0;;guard++){
        if(guard>100)throw Error('fixture busy loop');
        const due=[...timers].filter(([,v])=>v.due<=target).sort((a,b)=>a[1].due-b[1].due)[0];
        if(!due)break;
        time=due[1].due;timers.delete(due[0]);due[1].callback();await flush();
      }
      time=target;await flush();
    },
  };
}
function fixture(overrides={}){
  const c=clock(),calls=[],success=[],errors=[];
  const pump=createNativeVoicePump({now:c.now,setTimer:c.setTimer,clearTimer:c.clearTimer,
    capture:async()=>{calls.push(['capture',c.now()]);return {ok:true};},
    scan:async()=>{calls.push(['scan',c.now()]);return {fixture:true};},
    onSuccess:value=>success.push(value),onError:(error,facts)=>errors.push([error.message,facts.phase]),...overrides});
  return {c,pump,calls,success,errors};
}

test('same-turn refresh shares exact promise and performs one immediate capture even at clock zero',async()=>{
  const f=fixture();const a=f.pump.refresh({capture:true}),b=f.pump.refresh({capture:true}),c=f.pump.refresh();
  assert.equal(a,b);assert.equal(a,c);assert.deepEqual(await a,{ok:true,captures:1,scans:1,deferred:false});
  assert.deepEqual(f.calls,[['capture',0],['scan',0]]);assert.deepEqual(f.c.pending(),[]);
});

test('retry wake during pause cannot capture/scan or spin; explicit refresh resumes retained intent once',async()=>{
  let paused=false,scans=0;
  const f=fixture({isPaused:()=>paused,scan:async()=>({failed:++scans===1?1:0})});
  await f.pump.refresh({capture:true});assert.deepEqual(f.c.pending(),[15000]);paused=true;
  await f.c.to(15000);assert.equal(scans,1);assert.deepEqual(f.c.pending(),[]);
  await f.c.to(60000);assert.deepEqual(f.calls,[['capture',0]]);
  assert.equal((await f.pump.refresh({capture:true})).deferred,true);assert.deepEqual(f.c.pending(),[]);
  paused=false;const first=f.pump.refresh(),second=f.pump.refresh();assert.equal(first,second);await first;
  assert.deepEqual(f.calls,[['capture',0],['capture',60000]]);assert.equal(scans,2);assert.deepEqual(f.c.pending(),[]);
});

test('owned capture finishes while paused but ASR waits; scan retained and no duplicate capture on resume',async()=>{
  let paused=false,captures=0,scans=0;const entered=deferred(),gate=deferred();
  const f=fixture({isPaused:()=>paused,capture:async()=>{captures++;entered.resolve();await gate.promise;return {ok:true};},scan:async()=>++scans});
  const running=f.pump.refresh({capture:true});await entered.promise;paused=true;
  assert.equal((await f.pump.refresh()).deferred,true);gate.resolve();
  const result=await running;assert.equal(result.captures,1);assert.equal(result.scans,0);assert.equal(result.deferred,true);
  assert.equal(scans,0);assert.deepEqual(f.c.pending(),[]);
  paused=false;await f.pump.refresh();assert.equal(captures,1);assert.equal(scans,1);assert.deepEqual(f.c.pending(),[]);
});

test('pause retains capture throttling/backoff flags; close discards intents and cancels wake',async()=>{
  let paused=true,captures=0,scans=0;
  const f=fixture({isPaused:()=>paused,capture:async()=>{captures++;return {ok:true};},scan:async()=>++scans});
  await f.pump.refresh({capture:true});assert.equal(captures,0);assert.equal(scans,0);assert.deepEqual(f.c.pending(),[]);
  paused=false;await f.pump.refresh();assert.equal(captures,1);assert.equal(scans,1);
  paused=true;await f.c.to(1000);await f.pump.refresh({capture:true});assert.deepEqual(f.c.pending(),[]);
  paused=false;await f.pump.refresh();assert.equal(captures,1);assert.deepEqual(f.c.pending(),[15000]);
  paused=true;f.pump.close();await f.c.to(20000);paused=false;assert.deepEqual(await f.pump.refresh(),{closed:true});
  assert.equal(captures,1);assert.equal(scans,2);
});

test('pause/resume cannot bypass a failed capture retry deadline',async()=>{
  let paused=false,captures=0,scans=0;
  const f=fixture({isPaused:()=>paused,capture:async()=>({ok:++captures>1}),scan:async()=>++scans});
  await f.pump.refresh({capture:true});paused=true;await f.pump.refresh();assert.deepEqual(f.c.pending(),[]);
  await f.c.to(5000);paused=false;await f.pump.refresh();
  assert.equal(captures,1);assert.equal(scans,0);assert.deepEqual(f.c.pending(),[15000]);
  await f.c.to(15000);assert.equal(captures,2);assert.equal(scans,1);
});

test('capture hint inside rate limit is retained with exactly one delayed capture; scans remain usable',async()=>{
  const f=fixture();await f.pump.refresh({capture:true});await f.c.to(1000);
  assert.equal((await f.pump.refresh({capture:true})).deferred,true);
  assert.deepEqual(f.c.pending(),[15000]);await f.c.to(5000);await f.pump.refresh({capture:true});
  assert.deepEqual(f.c.pending(),[15000]);await f.c.to(14999);
  assert.equal(f.calls.filter(c=>c[0]==='capture').length,1);
  await f.c.to(15000);
  assert.deepEqual(f.calls.filter(c=>c[0]==='capture'),[['capture',0],['capture',15000]]);
  assert.deepEqual(f.calls.at(-1),['scan',15000]);assert.deepEqual(f.c.pending(),[]);
});

test('capture hints received during a running capture are not lost and do not duplicate immediate capture',async()=>{
  const gate=deferred(),entered=deferred();let captures=0,scans=0;
  const f=fixture({capture:async()=>{captures++;if(captures===1){entered.resolve();await gate.promise;}return {ok:true};},scan:async()=>++scans});
  const first=f.pump.refresh({capture:true});await entered.promise;
  assert.equal(first,f.pump.refresh({capture:true}));gate.resolve();await first;
  assert.equal(captures,1);assert.equal(scans,1);assert.deepEqual(f.c.pending(),[15000]);
  await f.c.to(15000);assert.equal(captures,2);assert.equal(scans,2);
});

test('capture failure retries at fifteen seconds without a five-minute external tick and without busy polling',async()=>{
  let captures=0,scans=0;
  const f=fixture({capture:async()=>++captures===1?{ok:false,code:'fixture-capture-failed'}:{ok:true},scan:async()=>++scans});
  assert.equal((await f.pump.refresh({capture:true})).ok,false);
  assert.equal(captures,1);assert.equal(scans,0);assert.deepEqual(f.c.pending(),[15000]);
  await f.c.to(1000);await f.pump.refresh({capture:true});await f.pump.refresh();
  assert.equal(captures,1);assert.equal(scans,0);await f.c.to(14999);assert.equal(captures,1);
  await f.c.to(15000);assert.equal(captures,2);assert.equal(scans,1);assert.deepEqual(f.c.pending(),[]);
  assert.deepEqual(f.errors,[['fixture-capture-failed','capture']]);
});

test('scan exception retries with a fresh capture and external hints cannot bypass backoff',async()=>{
  let scans=0;
  const f=fixture({scan:async()=>{if(++scans===1)throw Error('fixture-scan-failed');return scans;}});
  await f.pump.refresh({capture:true});assert.deepEqual(f.c.pending(),[15000]);
  await f.c.to(3000);await f.pump.refresh();assert.equal(scans,1);
  await f.c.to(15000);assert.equal(scans,2);assert.deepEqual(f.calls,[['capture',0],['capture',15000]]);
  assert.deepEqual(f.errors,[['fixture-scan-failed','scan']]);assert.deepEqual(f.success,[2]);
});

test('soft claim failures retain diagnostics and recapture missing media later without a busy loop',async()=>{
  let scans=0;
  const f=fixture({scan:async()=>++scans===1?{dispatched:1,failed:1}:{dispatched:1,failed:0}});
  const result=await f.pump.refresh({capture:true});
  assert.equal(result.ok,false);assert.equal(scans,1);assert.deepEqual(f.success,[{dispatched:1,failed:1}]);
  assert.deepEqual(f.errors,[]);assert.deepEqual(f.c.pending(),[15000]);
  await f.c.to(5000);await f.pump.refresh();await f.pump.refresh({capture:true});
  assert.equal(scans,1);assert.deepEqual(f.calls,[['capture',0]]);
  await f.c.to(14999);assert.equal(scans,1);await f.c.to(15000);
  assert.equal(scans,2);assert.deepEqual(f.calls,[['capture',0],['capture',15000]]);
  assert.deepEqual(f.success,[{dispatched:1,failed:1},{dispatched:1,failed:0}]);
  assert.deepEqual(f.c.pending(),[]);
});

test('close cancels recapture scheduled by soft scan failures',async()=>{
  let scans=0;
  const f=fixture({scan:async()=>{scans++;return {failed:1};}});
  await f.pump.refresh({capture:true});assert.deepEqual(f.c.pending(),[15000]);
  f.pump.close();assert.deepEqual(f.c.pending(),[]);await f.c.to(60000);
  assert.equal(scans,1);assert.deepEqual(f.calls,[['capture',0]]);
});

test('retry delay is measured after failure and still respects the capture start rate limit',async()=>{
  const gate=deferred(),entered=deferred();let captures=0;
  const f=fixture({capture:async()=>{if(++captures===1){entered.resolve();await gate.promise;throw Error('late failure');}return {ok:true};}});
  const work=f.pump.refresh({capture:true});await entered.promise;await f.c.to(10000);gate.resolve();await work;
  assert.deepEqual(f.c.pending(),[25000]);await f.c.to(24999);assert.equal(captures,1);
  await f.c.to(25000);assert.equal(captures,2);
});

test('sync and asynchronous reporter errors cannot break success or retry',async()=>{
  let scans=0,errors=0,success=0;
  const f=fixture({scan:async()=>{if(++scans===1)throw Error('scan failed');return scans;},
    onError:async()=>{errors++;throw Error('report failed');},onSuccess:()=>{success++;throw Error('report failed');}});
  assert.equal((await f.pump.refresh()).ok,false);await f.c.to(15000);
  assert.equal(errors,1);assert.equal(success,1);assert.equal(scans,2);assert.deepEqual(f.c.pending(),[]);
  await f.pump.refresh();assert.equal(scans,3);
});

test('request arriving in completion microtasks is processed instead of joining a finished finally window',async()=>{
  let pump,scans=0,late;
  const f=fixture({scan:async()=>++scans,onSuccess:()=>{
    if(scans===1)queueMicrotask(()=>queueMicrotask(()=>{late=pump.refresh({capture:true});}));
  }});pump=f.pump;
  await pump.refresh();await flush();await late;
  assert.equal(scans,2);assert.deepEqual(f.calls,[['capture',0]]);
  assert.deepEqual(f.c.pending(),[]);
  // The request attached after a settled task must also start fresh work.
  await pump.refresh().then(()=>pump.refresh());assert.equal(scans,4);
});

test('close cancels delayed capture and refuses new work',async()=>{
  const f=fixture();await f.pump.refresh({capture:true});await f.c.to(1);await f.pump.refresh({capture:true});
  assert.deepEqual(f.c.pending(),[15000]);f.pump.close();assert.deepEqual(f.c.pending(),[]);
  assert.deepEqual(await f.pump.refresh({capture:true}),{closed:true});await f.c.to(60000);
  assert.equal(f.calls.filter(c=>c[0]==='capture').length,1);
});

test('close or external closed while capture/scan is pending suppresses late callbacks and follow-up work',async()=>{
  const gate=deferred(),entered=deferred();let scans=0;
  const f=fixture({capture:async()=>{entered.resolve();await gate.promise;return {ok:true};},scan:async()=>++scans});
  const work=f.pump.refresh({capture:true});await entered.promise;f.pump.close();gate.resolve();await work;
  assert.equal(scans,0);assert.equal(f.success.length,0);assert.deepEqual(f.c.pending(),[]);
  const secondGate=deferred(),secondEntered=deferred();let closed=false;
  const g=fixture({isClosed:()=>closed,scan:async()=>{secondEntered.resolve();await secondGate.promise;return 'late';}});
  const second=g.pump.refresh();await secondEntered.promise;closed=true;secondGate.resolve();await second;
  assert.equal(g.success.length,0);assert.deepEqual(g.c.pending(),[]);
});
