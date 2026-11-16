import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveOwnerSession,uniqueSessionRoute} from './session-routing.mjs';
import {historyRoute} from './vault-client.mjs';

test('owner rotation follows trusted runtime while reserved group identities never become private',()=>{
  let current='owner-old';
  const options={service:{getSessionId:()=>current},fallback:'owner-old',reservedIds:['group-a','group-b']};
  const routes=new Map([['owner',{kind:'friend',target:'owner'}],['A',{kind:'group',target:'A',sessionId:'group-a'}]]);
  const lookup=id=>uniqueSessionRoute(routes,id,r=>r.kind==='friend'?resolveOwnerSession(options):r.sessionId);
  assert.equal(lookup('owner-old').target,'owner');
  current='owner-new';assert.equal(lookup('owner-old'),undefined);assert.equal(lookup(current).target,'owner');
  current='group-a';assert.equal(resolveOwnerSession(options),null);assert.equal(lookup(current).target,'A');
  assert.equal(lookup('owner-old'),undefined);
});

test('invalid trusted result does not silently restore stale configured owner access',()=>{
  for(const id of [null,undefined,'','  ',false,7,'owner\nother','owner\0other','x'.repeat(513)]) {
    assert.equal(resolveOwnerSession({service:{getSessionId:()=>id},fallback:'old'}),null);
    assert.equal(uniqueSessionRoute(new Map([['bad',{sessionId:id}]]),id),undefined);
  }
  assert.equal(resolveOwnerSession({service:{getSessionId(){throw Error('unavailable')}},fallback:'old'}),null);
  assert.equal(resolveOwnerSession({service:{},fallback:'old'}),'old');
});

test('duplicate session routes cannot select owner, query another chat or produce a delivery route',()=>{
  const routes=new Map([['owner',{target:'owner',sessionId:'group-a'}],
    ['A',{target:'A',sessionId:'group-a'}],['B',{target:'B',sessionId:'group-b'}]]);
  assert.equal(uniqueSessionRoute(routes,'group-a'),undefined);
  for(const requestedChat of [undefined,'B']) {
    assert.throws(()=>historyRoute({routes,sessionId:'group-a',ownerSessionId:'group-a',requestedChat}),/history-session-denied/);
  }
  assert.equal(uniqueSessionRoute(routes,'group-b').target,'B');
});
