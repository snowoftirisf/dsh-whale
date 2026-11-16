import test from 'node:test';
import assert from 'node:assert/strict';
import {withReplyBoundary} from './reply-boundary.mjs';

test('confirmed multi-segment send keeps order and cleans up once',async()=>{
  const calls=[];
  const value=await withReplyBoundary({begin:async()=>calls.push('begin'),
    send:async()=>{for(let i=0;i<3;i++)calls.push(i);return {sent:3};},
    end:async()=>calls.push('end')});
  assert.deepEqual(calls,['begin',0,1,2,'end']);assert.deepEqual(value,{sent:3});
});

test('unknown middle segment is preserved when end-reply fails, without replay',async()=>{
  const primary=Object.freeze(new Error('send-outcome-unknown'));
  const cleanup=new Error('worker-not-ready'),calls=[];
  await assert.rejects(withReplyBoundary({begin:async()=>calls.push('begin'),
    send:async()=>{for(let i=0;i<3;i++){calls.push(i);if(i===1)throw primary;}},
    end:async()=>{calls.push('end');throw cleanup;},
    onCleanupError:async error=>{assert.equal(error,cleanup);calls.push('report');throw Error('report failed');}}),
    error=>error===primary);
  assert.deepEqual(calls,['begin',0,1,'end','report']);
});

test('cleanup error alone remains visible rather than claiming whole task success',async()=>{
  const cleanup=new Error('end-reply-outcome-unknown');let sent=0;
  await assert.rejects(withReplyBoundary({begin:async()=>{},send:async()=>{sent++;},
    end:async()=>{throw cleanup;}}),error=>error===cleanup);
  assert.equal(sent,1);
});

test('failed begin never sends or blindly releases another reply lock',async()=>{
  const primary=new Error('begin-reply-outcome-unknown'),calls=[];
  await assert.rejects(withReplyBoundary({begin:async()=>{throw primary;},
    send:async()=>calls.push('send'),end:async()=>calls.push('end')}),error=>error===primary);
  assert.deepEqual(calls,[]);
});

test('even non-Error primary rejection is preserved',async()=>{
  let ended=0;
  await assert.rejects(withReplyBoundary({begin:async()=>{},send:async()=>{throw undefined;},
    end:async()=>{ended++;throw Error('cleanup');}}),error=>error===undefined);
  assert.equal(ended,1);
});
