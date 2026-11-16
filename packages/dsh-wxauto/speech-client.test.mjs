import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {speechClient} from './speech-client.mjs';

function fixture({respond=true}={}) {
  const calls=[],worker=new EventEmitter();
  Object.assign(worker,{pid:123456,exitCode:null,signalCode:null,stdout:new PassThrough(),stderr:new PassThrough(),
    kill(){calls.push(['fallback-kill'])}});
  worker.stdin=new Writable({write(bytes,_encoding,done){
    const request=JSON.parse(bytes.toString());
    if(respond&&request.id)queueMicrotask(()=>worker.stdout.write(JSON.stringify({id:request.id,ok:true,value:{op:request.op}})+'\n'));
    done();
  }});
  const client=speechClient({python:'fixture',script:'fixture.py',platform:'win32',shutdownGraceMs:1,
    spawnProcess:(...args)=>{calls.push(args);return calls.length===1?worker:new EventEmitter()}});
  return {client,worker,calls};
}

test('private JSONL replies remain correlated and normal exit needs no tree kill',async()=>{
  const {client,worker,calls}=fixture();
  assert.deepEqual(await client.request('status'),{op:'status'});
  worker.exitCode=0;worker.emit('exit',0);client.close();
  await new Promise(resolve=>setTimeout(resolve,15));assert.equal(calls.length,1);
});

test('closing rejects pending work and kills only this private Windows worker tree',async()=>{
  const {client,worker,calls}=fixture({respond:false});
  const rejected=assert.rejects(client.request('synthesize'),/speech-worker-closed/);
  client.close();await rejected;
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.deepEqual(calls[1].slice(0,2),['taskkill.exe',['/PID','123456','/T','/F']]);
  assert.equal(calls[1][2].windowsHide,true);assert.equal(worker.stdin.writableEnded,true);
  await assert.rejects(client.request('status'),/speech-worker-closed/);
});
