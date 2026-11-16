import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHistoryService} from './history-service.mjs';

test('invalidation prevents an older in-flight index from replacing the fresh index',async t=>{
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'whale-context-refresh-')),queries=[];
  const client={close(){},request:async op=>{
    if(op==='ensure_chat')return {id:'owner-chat'};
    if(op==='get_summaries')return {summaries:[]};
    if(op==='search')return new Promise(resolve=>queries.push(resolve));
    throw Error('unexpected operation');
  }};
  const service=createHistoryService({config:{dataRoot,accountId:'fixture',chatKeys:{owner:'owner'}},root:dataRoot,
    routes:new Map([['owner',{target:'owner',kind:'friend'}]]),routeSessionId:()=> 'session',currentSessionId:()=> 'session',report:()=>{},client});
  t.after(()=>service.close());
  const first=service.prepareContext('session'),rejected=assert.rejects(first,/refresh-superseded/);
  await new Promise(resolve=>setImmediate(resolve));assert.equal(queries.length,1);
  service.invalidateContext('session');const second=service.prepareContext('session');
  await new Promise(resolve=>setImmediate(resolve));assert.equal(queries.length,2);
  const result=id=>({messages:[{id,text:id,metadata:{},direction:'incoming',delivery_state:'received'}]});
  queries[0](result('stale'));await rejected;
  const third=service.prepareContext('session');assert.equal(third,second);assert.equal(queries.length,2);
  queries[1](result('fresh'));await third;
  assert.match(service.contextText('session'),/fresh/);assert.doesNotMatch(service.contextText('session'),/stale/);
});
