import {spawn} from 'node:child_process';

// Dedicated read-only process, not a new history writer or native key scanner.
export function readDiscoveredGroups({python,root,dataRoot,accountId,signal,unreadChatKey}) {
  return new Promise((resolve,reject)=>{
    const child=spawn(python,['-u','-X','utf8','-m','group_discovery','--root',dataRoot,'--account-id',accountId,
      ...(unreadChatKey?['--unread-chat-key',unreadChatKey]:[])],{
      cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe'],signal,
      env:{...process.env,PYTHONIOENCODING:'utf-8'}});
    let output='',settled=false,timer;
    const fail=code=>{if(!settled){settled=true;clearTimeout(timer);reject(Error(code));}};
    timer=setTimeout(()=>{fail('group-discovery-timeout');child.kill();},30000);
    child.stderr.on('data',()=>{});
    child.stdout.on('data',chunk=>{
      if(settled)return;
      output+=chunk.toString('utf8');
      if(Buffer.byteLength(output)>2*1024*1024){fail('group-discovery-output-limit');child.kill();}
    });
    child.once('error',()=>fail('group-discovery-start-failed'));
    child.once('close',code=>{
      clearTimeout(timer);if(settled)return;
      let value;try{value=JSON.parse(output);}catch{fail('group-discovery-invalid-output');return;}
      if(code!==0||value.ok!==true){fail(String(value.code??'group-discovery-failed').slice(0,120));return;}
      settled=true;resolve(value.value);
    });
  });
}
