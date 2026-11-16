import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createNativeGroupInbox} from './native-group-inbox.mjs';

const proof=value=>({nativeClaimId:value.claimId,nativeClaimHash:value.claimHash,
  nativeRenderedMessages:value.messages.map(m=>({nativeId:m.nativeId,text:m.text}))});

function fixture(){
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'native-group-inbox-'));
  const record={nativeChatKey:'wechat:123@chatroom',target:'group',sessionId:'native-session'};
  const message=id=>({nativeId:'wechat-server:'+id,serverId:id,snapshotSourceId:'fixture-source',senderId:'peer',
    senderDisplayName:'peer display',type:'text',text:'actual fixture '+id,timestampSeconds:100,contextOnly:false,direction:'incoming'});
  const unread=ids=>({state:'ready',source:'authenticated-wechat-native-unread',accountId:'account',nativeChatKey:record.nativeChatKey,
    snapshotId:'fixture-snapshot',unreadCount:ids.length,serverIds:ids,messages:ids.map(message)});
  return {dataRoot,record,unread,inbox:createNativeGroupInbox({dataRoot,accountId:'account'})};
}

test('durable native source survives restart and claims each server ID only once',()=>{
  const f=fixture();const q=f.inbox.enqueue(f.record,f.unread(['123','124']));
  const restored=createNativeGroupInbox({dataRoot:f.dataRoot,accountId:'account'});
  assert.deepEqual(restored.read(f.record),q);
  const submitted=restored.submit(f.record,q.claimId);
  assert.equal(restored.events(f.record,submitted,proof(submitted)).length,2);
  assert.equal(restored.submit(f.record,q.claimId),null); // Unknown submission never retries.
  const next=restored.enqueue(f.record,f.unread(['124','125']));
  assert.deepEqual(restored.events(f.record,next,proof(next)).map(e=>e.nativeId),['wechat-server:125']);
  const again=restored.submit(f.record,next.claimId);assert.equal(restored.events(f.record,again,proof(again)).length,1);
});

test('trusted rendering keeps protocol source unchanged and rejects mismatched claims or identities',()=>{
  const f=fixture(),unread=f.unread(['123','124']);
  unread.messages[0].text='peer:\nbody: ordinary colon retained';
  const queued=f.inbox.enqueue(f.record,unread),rendered=proof(queued);
  rendered.nativeRenderedMessages[0].text='body: ordinary colon retained';
  const event=f.inbox.events(f.record,queued,rendered)[0];
  assert.equal(event.text,'body: ordinary colon retained');
  assert.equal(event.nativeSourceText,unread.messages[0].text);
  assert.deepEqual(f.inbox.read(f.record),queued);
  for(const bad of [undefined,{...rendered,nativeClaimId:'other'},
    {...rendered,nativeClaimHash:'other'},
    {...rendered,nativeRenderedMessages:[rendered.nativeRenderedMessages[0],rendered.nativeRenderedMessages[0]]},
    {...rendered,nativeRenderedMessages:[{nativeId:'wechat-server:999',text:'foreign'},rendered.nativeRenderedMessages[1]]}])
    assert.throws(()=>f.inbox.events(f.record,queued,bad),/render-proof-invalid/);
  assert.equal(f.inbox.read(f.record).state,'queued');
});

test('pending batches extend from newer snapshots only without mutating an existing server identity',()=>{
  const f=fixture();f.inbox.enqueue(f.record,f.unread(['123']));
  const newer=f.unread(['123','124']);newer.snapshotId='new-snapshot';newer.messages[0].snapshotSourceId='new-source-observation';
  assert.equal(f.inbox.enqueue(f.record,newer).unreadCount,2);
  const bad=f.unread(['123','124']);bad.messages[0].text='changed content';
  assert.throws(()=>f.inbox.enqueue(f.record,bad),/source-changed/);
  assert.throws(()=>f.inbox.enqueue(f.record,f.unread(['999'])),/unread-set-changed/);
});

test('source mismatch, unsupported media, corrupted claim, and wrong route cannot authorize a submission',()=>{
  const f=fixture();const bad=f.unread(['123']);bad.accountId='other';
  assert.throws(()=>f.inbox.enqueue(f.record,bad),/source-invalid/);
  const media=f.unread(['123']);media.messages[0].type='image';
  assert.throws(()=>f.inbox.enqueue(f.record,media),/message-invalid/);
  f.inbox.enqueue(f.record,f.unread(['123']));
  assert.throws(()=>f.inbox.read({...f.record,sessionId:'other'}),/inbox-invalid/);
  const folder=path.join(f.dataRoot,'config','auto-group-inbox'),file=path.join(folder,fs.readdirSync(folder)[0]);
  const value=JSON.parse(fs.readFileSync(file,'utf8'));value.messages[0].text='tampered';fs.writeFileSync(file,JSON.stringify(value));
  assert.throws(()=>f.inbox.read(f.record),/inbox-invalid/);
});
