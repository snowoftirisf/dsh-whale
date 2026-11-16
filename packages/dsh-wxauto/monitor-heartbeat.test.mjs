import test from 'node:test';
import assert from 'node:assert/strict';
import {monitorHeartbeatUpdate} from './monitor-heartbeat.mjs';

test('an active send heartbeat cannot make a stationary receive counter look fresh',()=>{
  const old={monitorPollCount:8,lastPollAt:'old'};
  const patch=monitorHeartbeatUpdate(old,{polls:8,deliveryActive:true,queuedCommands:2,deferredDirectionRows:{owner:1,other:50}},
    {at:'new',targets:['owner']});
  assert.equal({...old,...patch}.lastPollAt,'old');assert.equal(patch.lastWorkerHeartbeatAt,'new');
  assert.deepEqual(patch.monitorActivity,{deliveryActive:true,queuedCommands:2,deferredDirectionRows:{owner:1}});
  assert.equal(monitorHeartbeatUpdate(old,{polls:9},{at:'progress'}).lastPollAt,'progress');
});

test('zero, malformed and reset counters do not invent polling; old workers report unknown busy state',()=>{
  assert.equal(Object.hasOwn(monitorHeartbeatUpdate({},{polls:0}),'lastPollAt'),false);
  for(const polls of [-1,NaN,'9',undefined]){
    const update=monitorHeartbeatUpdate({monitorPollCount:8},{polls});
    assert.equal(Object.hasOwn(update,'lastPollAt'),false);assert.equal(update.monitorHeartbeatError,'invalid-poll-count');
  }
  const update=monitorHeartbeatUpdate({monitorPollCount:8},{polls:0});
  assert.equal(update.monitorCounterReset,true);assert.equal(Object.hasOwn(update,'lastPollAt'),false);
  assert.equal(update.monitorActivity.deliveryActive,null);assert.equal(update.monitorActivity.queuedCommands,null);
});
