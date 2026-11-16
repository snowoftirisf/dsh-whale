import test from 'node:test';
import assert from 'node:assert/strict';
import {createOwnerTextCutover} from './owner-text-cutover.mjs';
import {ownerTextScopeDigest} from './native-owner-text-inbox.mjs';

const ids=['4af746e5-6ce9-4d4b-859f-3e291d8941e2','8c04687a-bd47-423b-8149-99033436b61e'];
function fixture(options={}){
  const config={accountId:'fixture',target:'owner',nativeVoiceSources:{ownerNativeChatKey:'wechat:peer'}};
  const calls=[],phases=[];let mode='ui',paused=false,active=false,revision=0,closed=false,marker=false,inbox=false,captures=0;
  let cutover,held,abortCount=0;
  const ticket='a'.repeat(32),epoch='b'.repeat(32),token=Object.freeze({authorityEpoch:epoch});
  const status=()=>({version:1,mode,readsStopped:false,authorityEpoch:mode==='native'?epoch:null,
    ticket:mode==='preparing'?ticket:null,deferred:0,dirty:false,...(mode==='preparing'?{preparedAtMs:1000000}:{})});
  const requestOwner=async(op,args)=>{
    calls.push([op,args]);
    if(op==='owner-text-status'){
      if(options.badStatus&&calls.some(c=>c[0]==='owner-text-commit'))return {version:0,mode:'blocked'};
      return status();
    }
    if(op==='owner-text-prepare'){mode='preparing';if(options.prepareLost)throw Error('fixture lost ack');return status()}
    if(op==='owner-text-commit'){
      if(options.commitRefused)throw Error('fixture refused');
      mode='native';marker=true;
      if(options.commitLost||options.badStatus)throw Error('fixture lost ack');
      return status();
    }
    if(op==='owner-text-abort'){
      if(options.abortLost)throw Error('fixture abort unknown');
      assert.equal(args.ticket,ticket);mode='ui';return status();
    }
    throw Error('unexpected operation');
  };
  const catalogClient={async catalog({snapshotId}){
    calls.push(['catalog',snapshotId]);
    return {source:'authenticated-native-owner-text-catalog',complete:true,accountId:config.accountId,
      nativeChatKey:'wechat:peer',scopeDigest:ownerTextScopeDigest('fixture','wechat:peer','peer'),snapshotId,
      messages:[{nativeId:'wechat-server:1',serverId:'1',nativeChatKey:'wechat:peer',senderId:'peer',rawText:options.catalogChanged&&snapshotId===ids[1]?'changed fixture':'fixture',timestampSeconds:500,localId:1}]};
  },close(){calls.push(['catalog-close'])}};
  const sync={async exclusiveRefresh(work){calls.push(['lease']);return await work({refresh(){
    const index=captures++;calls.push(['capture',index]);
    if(options.holdCapture&&index===0){const promise=new Promise(resolve=>{held=resolve});promise.abort=()=>{abortCount++;held({ok:false})};return promise}
    return Promise.resolve({ok:true,snapshotId:options.sameSnapshot?ids[0]:ids[index]});
  }})}};
  const publisher={exists:()=>inbox,prepare(catalog,{cutoverAtMs}){calls.push(['baseline-prepare',cutoverAtMs]);assert.equal(cutoverAtMs,1000000);return token},
    publish(value){assert.equal(value,token);calls.push(['publish']);inbox=true},
    discardPublished(value,proof){assert.equal(value,token);calls.push(['discard',proof]);assert.equal(marker,false);assert.equal(proof.commitNotActive,true);inbox=false}};
  const runtime={active:()=>active,async restore(){calls.push(['restore']);if(options.restoreFails)return {active:false};active=true;return {active:true}}};
  cutover=createOwnerTextCutover({config,requestOwner,sync,catalogClient,publisher,runtime,
    getQuiet:async()=>({quiet:true,sessionId:'original',revision}),pauseOwner(){calls.push(['pause']);paused=true;return ()=>{calls.push(['release']);paused=false}},
    readActivity:()=>({quiet:!options.captureDirty,revision:options.captureDirty?revision+1:revision}),
    ensureScanning:async()=>{calls.push(['scan']);if(options.closeDuringScan)cutover.close()},isClosed:()=>closed,
    now:()=>1000000,monitorMs:1,report(value){phases.push(value.phase);if(options.activityBeforePublish&&value.phase==='preparing-baseline')revision++}});
  return {cutover,calls,phases,paused:()=>paused,active:()=>active,marker:()=>marker,inbox:()=>inbox,captures:()=>captures,aborts:()=>abortCount,
    releaseCapture:()=>held?.({ok:true,snapshotId:ids[0]}),setClosed:()=>closed=true};
}
async function run(f){const job=f.cutover.activate();await f.cutover.wait(job.attemptId);return f.cutover.status()}
const count=(f,op)=>f.calls.filter(c=>c[0]===op).length;

test('duplicate activation while running returns same attempt and performs one cutover',async()=>{
  const f=fixture(),first=f.cutover.activate(),duplicate=f.cutover.activate();
  assert.equal(duplicate.attemptId,first.attemptId);await f.cutover.wait(first.attemptId);
  assert.equal(f.cutover.status().outcome,'active');assert.equal(count(f,'lease'),1);assert.equal(f.captures(),2);
  assert.equal(f.cutover.activate().outcome,'active');assert.equal(count(f,'owner-text-commit'),1);
});

test('lost commit ack recovers fresh native status and restores exactly once with no rollback/recommit',async()=>{
  const f=fixture({commitLost:true}),result=await run(f);
  assert.equal(result.outcome,'active');assert.equal(count(f,'owner-text-commit'),1);assert.equal(count(f,'restore'),1);
  assert.equal(count(f,'discard'),0);assert.equal(count(f,'owner-text-abort'),0);assert.equal(f.paused(),false);assert.equal(f.marker(),true);
});

test('known nonactive commit refusal discards exact baseline then aborts and releases',async()=>{
  const f=fixture({commitRefused:true}),result=await run(f);
  assert.equal(result.outcome,'aborted');assert.equal(f.inbox(),false);assert.equal(f.paused(),false);
  const order=f.calls.map(c=>c[0]);assert.ok(order.indexOf('discard')<order.indexOf('owner-text-abort'));assert.ok(order.indexOf('owner-text-abort')<order.indexOf('release'));
  assert.equal(count(f,'owner-text-commit'),1);assert.equal(count(f,'restore'),0);
});

test('malformed postcommit status preserves pause and marker and never repeats unknown attempt',async()=>{
  const f=fixture({badStatus:true}),result=await run(f);
  assert.equal(result.outcome,'unknown');assert.equal(f.paused(),true);assert.equal(f.marker(),true);assert.equal(f.inbox(),true);
  assert.equal(count(f,'discard'),0);assert.equal(count(f,'owner-text-abort'),0);
  const before=f.calls.length;assert.equal(f.cutover.activate().outcome,'unknown');assert.equal(f.calls.length,before);
});

test('parent restore failure after commit keeps native marker and paused unknown with no rollback',async()=>{
  const f=fixture({restoreFails:true}),result=await run(f);
  assert.equal(result.outcome,'unknown');assert.equal(f.marker(),true);assert.equal(f.paused(),true);
  assert.equal(count(f,'owner-text-commit'),1);assert.equal(count(f,'discard'),0);assert.equal(count(f,'owner-text-abort'),0);
});

test('activity revision changes before publication abort without publishing baseline',async()=>{
  const f=fixture({activityBeforePublish:true}),result=await run(f);
  assert.equal(result.outcome,'aborted');assert.equal(count(f,'publish'),0);assert.equal(count(f,'owner-text-abort'),1);assert.equal(f.paused(),false);
});

test('abort RPC unknown keeps parent paused and does not retry abort',async()=>{
  const f=fixture({catalogChanged:true,abortLost:true}),result=await run(f);
  assert.equal(result.outcome,'unknown');assert.equal(f.paused(),true);assert.equal(count(f,'owner-text-abort'),1);assert.equal(count(f,'publish'),0);
});

test('dirty activity during held capture aborts only owned capture promise',async()=>{
  const f=fixture({holdCapture:true,captureDirty:true}),job=f.cutover.activate();
  await new Promise(resolve=>setTimeout(resolve,20));await f.cutover.wait(job.attemptId);
  assert.equal(f.aborts(),1);assert.equal(f.captures(),1);assert.equal(f.cutover.status().outcome,'refused');assert.equal(f.paused(),false);
  assert.equal(count(f,'owner-text-prepare'),0);assert.equal(count(f,'publish'),0);
});

test('close before activation refuses all work',async()=>{
  const f=fixture();f.cutover.close();assert.equal(f.cutover.activate().outcome,'refused');
  assert.deepEqual(f.calls.map(c=>c[0]),['catalog-close']);
});

test('close during parent scanning cannot release pause or report active',async()=>{
  const f=fixture({closeDuringScan:true}),result=await run(f);
  assert.equal(result.outcome,'unknown');assert.equal(f.paused(),true);assert.equal(f.marker(),true);assert.equal(count(f,'discard'),0);
});

test('same reused snapshot and changed catalog text independently abort before baseline',async()=>{
  for(const options of [{sameSnapshot:true},{catalogChanged:true}]){
    const f=fixture(options),result=await run(f);assert.equal(result.outcome,'aborted');
    assert.equal(count(f,'publish'),0);assert.equal(count(f,'owner-text-abort'),1);assert.equal(f.paused(),false);
  }
});
