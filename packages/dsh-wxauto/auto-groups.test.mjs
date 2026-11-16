import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createAutoGroups} from './auto-groups.mjs';

function fixture(t,{admit=async()=>{},deactivate=async()=>{},existing=[]}={}) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'auto-groups-'));
  let groups=[],reads=0,prepared=[];
  const service=createAutoGroups({accountId:'account',file:path.join(root,'groups.json'),existing,
    readCandidates:async()=>{reads++;return {source:'validated-native-snapshot',accountId:'account',snapshotId:'fixture',groups};},
    ensureChat:async({nativeChatKey})=>({id:nativeChatKey==='wechat:old@chatroom'?'old-id':'new-id'}),
    prepare:async r=>prepared.push(r),admit,deactivate});
  t.after(()=>service.close());
  const group=(key,name)=>({chatKey:key,displayName:name,eligible:true,
    membership:{deleteFlag:0,isInChatRoom:1,roomRows:1,selfMembershipRows:1}});
  return {service,group,setGroups:g=>{groups=g;},prepared,reads:()=>reads,root};
}

test('runtime coordinator discovers, durably registers, and retries UI admission without creating another session',async t=>{
  let attempts=0;
  const f=fixture(t,{admit:async()=>{if(++attempts===1)throw Error('dynamic-unread-recovery-required');}});
  f.setGroups([f.group('wechat:new@chatroom','new group')]);
  const a=await f.service.refresh();assert.equal(a.pending,1);assert.equal(a.active,0);
  const id=f.service.snapshot()[0].sessionId;
  const b=await f.service.refresh();assert.equal(b.active,1);assert.equal(f.prepared.length,1);
  assert.equal(f.service.snapshot()[0].sessionId,id);
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.root,'groups.json'),'utf8')).groups[0].sessionId,id);
});

test('existing canonical identity retains original session even with no native title',async t=>{
  const f=fixture(t,{existing:[{target:'approved title',sessionId:'original-session',chatId:'old-id'}]});
  const g=f.group('wechat:old@chatroom',null);g.eligible=false;f.setGroups([g]);
  const r=await f.service.refresh();assert.equal(r.active,1);assert.equal(f.prepared.length,0);
  assert.equal(f.service.snapshot()[0].sessionId,'original-session');
});

test('concurrent refresh shares work and closed coordinator never discovers again',async t=>{
  const f=fixture(t);const a=f.service.refresh(),b=f.service.refresh();assert.equal(a,b);
  await a;assert.equal(f.reads(),1);f.service.close();assert.deepEqual(await f.service.refresh(),{closed:true});
  assert.equal(f.reads(),1);
});

test('complete native discovery revokes a dynamic route when membership disappears',async t=>{
  const revoked=[];const f=fixture(t,{deactivate:async r=>revoked.push(r.sessionId)});
  f.setGroups([f.group('wechat:new@chatroom','new group')]);await f.service.refresh();
  const session=f.service.snapshot()[0].sessionId;
  f.setGroups([]);const next=await f.service.refresh();
  assert.deepEqual(revoked,[session]);assert.equal(next.active,0);
});
