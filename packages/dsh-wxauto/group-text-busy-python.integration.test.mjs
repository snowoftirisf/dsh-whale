// Actual Python ACK/authority + Node RPC branding/journal. Test fixtures only.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';
import {groupTextAdmissionRpcClient} from './group-text-admission-rpc.mjs';
import {createGroupTextAdmissionCoordinator} from './group-text-admission-coordinator.mjs';
import {createNativeGroupInbox} from './native-group-inbox.text-candidate.mjs';
import {createNativeGroupTextInbox} from './native-group-text-inbox.admission-candidate.mjs';
import {nativeGroupTextPaths} from './group-text-contract.mjs';

test('real Python busy-before-admit proof permits one Node retry and one real marker', {timeout:60000}, async t=>{
 const workspace=fileURLToPath(new URL('../../',import.meta.url));
 const python=(process.env.WHALE_TEST_PYTHON||path.join(workspace,'.venv-wxauto','Scripts','python.exe'));
 const child=spawn(python,[path.join(workspace,'tools','native_group_admission_busy_fixture.py')],
  {cwd:workspace,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let stderr='';child.stderr.setEncoding('utf8');child.stderr.on('data',s=>{stderr+=s;});
 let rpc,coordinator,generation=null;const commands=[],acks=[];
 const exited=new Promise(resolve=>child.once('exit',(code,signal)=>resolve({code,signal})));
 t.after(async()=>{coordinator?.close();rpc?.close();child.stdin.end();
  const timeout=setTimeout(()=>child.kill(),5000);await exited;clearTimeout(timeout);});
 const lines=createInterface({input:child.stdout});
 const ready=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('fixture startup timed out: '+stderr)),15000);
  child.once('error',error=>{clearTimeout(timer);reject(error);});
  child.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exited '+code+': '+stderr));});
  lines.on('line',line=>{let e;try{e=JSON.parse(line);}catch{return reject(Error('invalid fixture stdio'));}
   if(e.event==='fixture-ready'){clearTimeout(timer);generation=e.workerInstanceId;resolve(e);}
   else {acks.push(e);rpc?.handle(e);}
  });
 });
 const {record,dataRoot,claimId,handoff,authorityEpoch}=ready;
 const groupInbox=createNativeGroupInbox({dataRoot,accountId:record.accountId,textDelegation:true});
 const textInbox=createNativeGroupTextInbox({dataRoot,accountId:record.accountId,nativeChatKey:record.nativeChatKey});
 const route={...record,kind:'group',active:false};let enabled=false,restored=false,pumps=0;
 rpc=groupTextAdmissionRpcClient({pendingRoute:args=>coordinator.pendingRoute(args),workerInstanceId:()=>generation,
  write:(line,callback)=>{commands.push(JSON.parse(line));child.stdin.write(line,callback);}});
 coordinator=createGroupTextAdmissionCoordinator({dataRoot,accountId:record.accountId,groupInbox,
  textAdmission:{prepare:async()=>groupInbox.read(record)},getRoute:()=>route,
  runtime:{restore:async()=>{assert.equal(enabled,true);restored=true;},active:()=>restored},
  requestAdmit:args=>rpc.request(args),requestStatus:args=>rpc.status(args),
  consumeNotIssued:(error,args)=>rpc.consumeNotIssued(error,args),readTextStatus:()=>textInbox.status(),pump:async()=>{pumps++;}});
 await coordinator.prepare(record,claimId);
 const result=await coordinator.commit(record,claimId,handoff,{
  activateRoute:async()=>{route.active=true;},
  deliverLegacy:async()=>{assert.deepEqual(groupInbox.events(record,groupInbox.read(record),{}),[]);groupInbox.submit(record,claimId);},
  enableRoute:async()=>{enabled=true;},
 });
 assert.equal(result.native,true);assert.equal(result.authorityEpoch,authorityEpoch);
 assert.equal(pumps,1);assert.equal(groupInbox.read(record).state,'submitted');
 assert.equal(coordinator.status(record).phase,'complete');
 const admits=commands.filter(c=>c.op==='group-text-admit');assert.equal(admits.length,2);
 assert.notEqual(admits[0].id,admits[1].id);
 for(const c of admits)assert.equal(c.claimId,claimId);
 const busy=acks.filter(e=>e.code==='group-text-worker-busy');assert.equal(busy.length,1);
 assert.equal(busy[0].issuance,'not-issued');assert.equal(busy[0].workerInstanceId,generation);
 const paths=nativeGroupTextPaths({dataRoot,nativeChatKey:record.nativeChatKey});
 const marker=JSON.parse(fs.readFileSync(path.join(paths.directory,'authority.json'),'utf8'));
 assert.equal(marker.authorityEpoch,authorityEpoch);assert.equal(marker.admission.rawClaimId,claimId);
 assert.equal(fs.existsSync(path.join(paths.directory,'transition.lock')),false);
 assert.equal(textInbox.pending().length,2); // Admission does not ask/replay inputs.
});
