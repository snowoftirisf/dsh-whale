import test from 'node:test';
import assert from 'node:assert/strict';
import {lastSendFailure} from './send-result-diagnostic.mjs';

test('last failure stores only allowlisted SDK structure, never its message or data',()=>{
  const failure=lastSendFailure({code:'send-not-confirmed',sdkResult:{type:'WxResponse',knownStatus:'failure',
    messageLength:12,messageSha256:'a'.repeat(64),message:'private',data:{path:'private'}}},
    {kind:'attachment'},'fixture-owner','fixture-time');
  assert.deepEqual(failure,{code:'send-not-confirmed',target:'fixture-owner',kind:'attachment',at:'fixture-time',
    sdkResult:{type:'WxResponse',knownStatus:'failure',messageLength:12,messageSha256:'a'.repeat(64)}});
  assert.deepEqual(lastSendFailure({code:'send-not-confirmed',sdkResult:{type:'NoneType'}},
    {kind:'attachment'},'fixture-owner','fixture-time').sdkResult,{type:'NoneType'});
  assert.equal(Object.hasOwn(lastSendFailure({code:'send-not-confirmed',sdkResult:{type:'invalid/path',message:'private'}},
    {kind:'attachment'},'fixture-owner','fixture-time'),'sdkResult'),false);
});
