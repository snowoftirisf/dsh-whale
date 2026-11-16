import test from 'node:test';import assert from 'node:assert/strict';
import {nativeGroupTextRoutes} from './group-text-routes.mjs';
const setup=()=>({accountId:'a',routes:new Map([['G',{target:'G',kind:'group',sessionId:'original',active:true}],['Owner',{kind:'friend',sessionId:'owner'}]]),
 routeSessionId:r=>r.sessionId,registry:{version:1,accountId:'a',groups:[{accountId:'a',status:'active',nativeChatKey:'wechat:1@chatroom',target:'G',sessionId:'original',chatId:'canonical'}]}});
test('group route carries current canonical chat identity without voice owner config',()=>{
 const p=setup(),read=()=>nativeGroupTextRoutes(p).get('wechat:1@chatroom');assert.equal(read().chatId,'canonical');
 p.registry.groups[0].chatId='rotated';assert.equal(read().chatId,'rotated');
 p.registry.groups[0].target='Renamed';p.routes.set('Renamed',{kind:'group',sessionId:'original'});assert.equal(read().target,'Renamed');
});
test('stale session, private route, foreign account and duplicate native binding refuse',()=>{
 for(const change of [p=>p.registry.groups[0].sessionId='other',p=>p.routes.get('G').kind='friend',p=>p.registry.accountId='foreign',p=>p.registry.groups.push({...p.registry.groups[0]})]){
  const p=setup();change(p);assert.throws(()=>nativeGroupTextRoutes(p),/unverified/);
 }
});
test('pending or not-yet-enabled groups retain original UI admission',()=>{
 const p=setup();p.registry.groups[0].status='pending';assert.equal(nativeGroupTextRoutes(p).size,0);
 p.registry.groups[0].status='active';p.routes.get('G').active=false;assert.equal(nativeGroupTextRoutes(p).size,0);
});
