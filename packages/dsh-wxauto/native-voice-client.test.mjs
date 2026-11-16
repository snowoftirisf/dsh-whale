import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {nativeVoiceClient} from './native-voice-client.mjs';

function fixture({hold=false,response={ok:true,value:{text:'中文语音'}},raw,exitCode=0,clientOptions={}}={}){
  const calls=[];let child,killed=0;
  const spawnProcess=(exe,args,options)=>{
    calls.push({exe,args,options});child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;
    child.kill=()=>{killed++;child.exitCode=-1;child.emit('close',-1);};
    const input=[];child.stdin=new Writable({write(chunk,_e,done){input.push(Buffer.from(chunk));done();},final(done){
      calls.at(-1).input=Buffer.concat(input).toString('utf8');
      if(!hold)setImmediate(()=>{const selected=typeof response==='function'?response(JSON.parse(Buffer.concat(input).toString('utf8')),calls.length):response;const bytes=Buffer.from(raw??JSON.stringify(selected));
        // Deliberately split every UTF-8 character across transport chunks.
        for(const byte of bytes)child.stdout.write(Buffer.from([byte]));
        child.stdout.end();child.exitCode=exitCode;child.emit('close',exitCode);});
      done();
    }});return child;
  };
  const client=nativeVoiceClient({root:'C:/owned plugin',dataRoot:'D:/data root',python:'wx-python',decoderPython:'voice-python',spawnProcess,timeoutMs:30,...clientOptions});
  return {client,calls,killed:()=>killed};
}

test('catalog and extract use private JSON input and preserve UTF8 across chunks',async()=>{
  const v={nativeId:'wechat-server:1',serverId:'1',nativeChatKey:'wechat:123@chatroom',direction:'incoming',rawText:'中文语音'};
  const f=fixture({response:{ok:true,value:page([v])}});assert.equal((await f.client.catalog(['wechat:123@chatroom'],{snapshotId:'snapshot'})).voices[0].rawText,'中文语音');
  assert.equal(JSON.parse(f.calls[0].input).op,'catalog-page');assert.equal(f.calls[0].options.windowsHide,true);
  assert.equal(f.calls[0].options.shell,undefined);assert.equal(f.calls[0].options.env.CUDA_VISIBLE_DEVICES,'');
  await f.client.extract({snapshotId:'snapshot',nativeChatKey:'wechat:123@chatroom',serverId:'123'},['wechat:123@chatroom']);
  assert.equal(JSON.parse(f.calls[1].input).serverId,'123');f.client.close();
});

function voice(id){return {nativeId:'wechat-server:'+id,serverId:String(id),nativeChatKey:'wechat:123@chatroom',direction:'incoming',timestampSeconds:Number(id),localId:Number(id)};}
function page(voices=[],extra={}){return {source:'authenticated-native-voice-catalog-page',accountId:'fixture',snapshotId:'snapshot',scopeDigest:'a'.repeat(64),voices,observedServerIds:voices.map(v=>v.nativeId),scanned:voices.length,position:{descriptorIndex:1,rowid:0},complete:true,nextCursor:null,...extra};}

test('more than 1000 entries aggregate completely with frozen snapshot and one private scan capability',async()=>{
  const rows=Array.from({length:1001},(_,i)=>voice(i+1));
  const f=fixture({response:(_input,n)=>({ok:true,value:page(rows.slice((n-1)*500,n*500),n<3?{complete:false,nextCursor:'cursor-'+n,position:{descriptorIndex:0,rowid:n*500}}:{})})});
  const result=await f.client.catalog(['wechat:123@chatroom']);assert.equal(result.source,'authenticated-native-voice-catalog');assert.equal(result.voices.length,1001);
  const inputs=f.calls.map(c=>JSON.parse(c.input));assert.equal(inputs[0].snapshotId,undefined);assert.ok(inputs.slice(1).every(x=>x.snapshotId==='snapshot'));
  assert.equal(new Set(inputs.map(x=>x.scanKey)).size,1);assert.equal(inputs[0].scanKey.length,64);f.client.close();
});

test('outgoing-only empty input page advances; later incoming is not lost',async()=>{
  const f=fixture({response:(_input,n)=>({ok:true,value:n===1?page([],{observedServerIds:['wechat-server:1'],scanned:1,complete:false,nextCursor:'next',position:{descriptorIndex:0,rowid:500}}):page([voice(2)])})});
  assert.deepEqual((await f.client.catalog(['wechat:123@chatroom'])).voices.map(v=>v.serverId),['2']);assert.equal(f.calls.length,2);f.client.close();
});

test('mid-scan failure never resolves partial catalog or baseline input',async()=>{
  const f=fixture({response:(_input,n)=>n===1?{ok:true,value:page([voice(1)],{complete:false,nextCursor:'next',position:{descriptorIndex:0,rowid:1}})}:{ok:false,code:'native-voice-catalog-snapshot-changed'}});
  let delivered=false;await assert.rejects(f.client.catalog(['wechat:123@chatroom']).then(()=>{delivered=true;}),/snapshot-changed/);assert.equal(delivered,false);f.client.close();
});

test('account snapshot scope repeated native IDs and nonadvancing/cyclic cursors fail closed',async()=>{
  for(const change of [{accountId:'other'},{snapshotId:'other'},{scopeDigest:'b'.repeat(64)},
    {position:{descriptorIndex:0,rowid:1}}, {complete:false,nextCursor:'next',position:{descriptorIndex:0,rowid:2}}]){
    const f=fixture({response:(_input,n)=>({ok:true,value:n===1?page([voice(1)],{complete:false,nextCursor:'next',position:{descriptorIndex:0,rowid:1}}):page([voice(2)],change)})});
    await assert.rejects(f.client.catalog(['wechat:123@chatroom']),/page-invalid|cursor/);f.client.close();
  }
  const f=fixture({response:(_input,n)=>({ok:true,value:n===1?page([voice(1)],{complete:false,nextCursor:'next',position:{descriptorIndex:0,rowid:1}}):page([voice(1)])})});
  await assert.rejects(f.client.catalog(['wechat:123@chatroom']),/native-id-ambiguous/);f.client.close();
});

test('resource page/byte limits fail explicitly and do not return a truncated catalog',async()=>{
  const f=fixture({clientOptions:{maxCatalogPages:1},response:{ok:true,value:page([voice(1)],{complete:false,nextCursor:'next',position:{descriptorIndex:0,rowid:1}})}});
  await assert.rejects(f.client.catalog(['wechat:123@chatroom']),/page-limit/);assert.equal(f.calls.length,1);f.client.close();
  const b=fixture({clientOptions:{maxCatalogBytes:10},response:{ok:true,value:page([voice(1)])}});
  await assert.rejects(b.client.catalog(['wechat:123@chatroom']),/total-byte-limit/);b.client.close();
});
test('decoder arguments are structured and contain exact source hash and duration',async()=>{
  const f=fixture();const file='D:/data root/voice/$(not a command).silk';
  await f.client.decode({file,metadata:{sha256:'a'.repeat(64),durationMilliseconds:4880}});
  assert.equal(f.calls[0].exe,'voice-python');assert.ok(f.calls[0].args.includes(file));
  assert.ok(f.calls[0].args.includes('4880'));assert.equal(f.calls[0].input,'');f.client.close();
});
test('malformed and nonzero responses expose only fixed failure codes',async()=>{
  for(const opts of [{raw:'PRIVATE JSON ERROR'}, {response:{ok:false,code:'private message body'},exitCode:2},
    {response:{ok:false,code:'native-voice-blob-missing'},exitCode:2}]){
    const f=fixture(opts);
    await assert.rejects(f.client.catalog(['wechat:123']),error=>!error.message.includes('PRIVATE')&&!error.message.includes('private message'));
    f.client.close();
  }
});
test('abort and close stop owned helpers and never start work after disposal',async()=>{
  const f=fixture({hold:true});const signal=new AbortController();
  const pending=f.client.catalog(['wechat:123'],{signal:signal.signal});signal.abort();
  await assert.rejects(pending,/stopped/);assert.equal(f.killed(),1);
  const another=f.client.catalog(['wechat:123']);f.client.close();
  await assert.rejects(another,/stopped/);assert.equal(f.killed(),2);
  await assert.rejects(f.client.catalog(['wechat:123']),/stopped/);assert.equal(f.calls.length,2);
});
test('timeout terminates a helper rather than silently retrying',async()=>{
  const f=fixture({hold:true});await assert.rejects(f.client.catalog(['wechat:123']),/timeout/);
  assert.equal(f.killed(),1);assert.equal(f.calls.length,1);f.client.close();
});
