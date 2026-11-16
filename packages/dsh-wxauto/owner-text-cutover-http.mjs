// Local authenticated owner controls only. No model tool or caller-selected
// account/target/path. Registration itself does not restore or activate anything.
export const ownerTextCutoverPath='/api/local-dsh-wxauto/owner-text-cutover';
export const ownerTextActivatePath=ownerTextCutoverPath+'/activate';
const errorCode=e=>/^owner-text-[a-z0-9-]{1,100}$/.test(e?.message??'')?e.message:'owner-text-cutover-outcome-unknown';
function project(raw,activation){
  if(!raw||typeof raw!=='object'||Array.isArray(raw))throw Error('owner-text-cutover-result-invalid');
  const value={};
  if(activation||raw.outcome!==undefined){if(!['working','active','aborted','refused','unknown'].includes(raw.outcome))throw Error('owner-text-cutover-result-invalid');value.outcome=raw.outcome;}
  if(raw.attemptId!==undefined){if(typeof raw.attemptId!=='string'||!/^[a-f0-9]{32}$/.test(raw.attemptId))throw Error('owner-text-cutover-result-invalid');value.attemptId=raw.attemptId;}
  if(value.outcome==='working'&&!value.attemptId)throw Error('owner-text-cutover-result-invalid');
  if(raw.authorityMode!==undefined){if(!['ui','native','blocked'].includes(raw.authorityMode))throw Error('owner-text-cutover-result-invalid');value.authorityMode=raw.authorityMode;}
  if(!activation&&raw.authorityMode===undefined)throw Error('owner-text-cutover-result-invalid');
  for(const key of ['canActivate','nativeActive']){
    if(raw[key]!==undefined){if(typeof raw[key]!=='boolean')throw Error('owner-text-cutover-result-invalid');value[key]=raw[key];}
    else if(!activation)throw Error('owner-text-cutover-result-invalid');
  }
  if(raw.phase!==undefined){if(typeof raw.phase!=='string'||!/^[a-z-]{1,80}$/.test(raw.phase))throw Error('owner-text-cutover-result-invalid');value.phase=raw.phase;}
  if(raw.quiet!==undefined){
    if(!raw.quiet||typeof raw.quiet!=='object'||Array.isArray(raw.quiet))throw Error('owner-text-cutover-result-invalid');
    value.quiet={};
    for(const key of ['incomingPending','outgoingPending','evidencePending','activeInputs','contextPending','pending','active','paused']){
      if(raw.quiet[key]===undefined)continue;
      if(['active','paused'].includes(key)){if(typeof raw.quiet[key]!=='boolean')throw Error('owner-text-cutover-result-invalid');}
      else if(!Number.isSafeInteger(raw.quiet[key])||raw.quiet[key]<0)throw Error('owner-text-cutover-result-invalid');
      value.quiet[key]=raw.quiet[key];
    }
  }
  if(typeof raw.code==='string'&&/^owner-text-[a-z0-9-]{1,100}$/.test(raw.code))value.code=raw.code;
  return value;
}
export function registerOwnerTextCutover(ctx,{status,activate}={}){
  if(typeof status!=='function'||typeof activate!=='function')throw Error('owner-text-cutover-config-invalid');
  if(typeof ctx.inject!=='function')return;
  let busy=false;
  const run=async activation=>{
    if(!activation)return project(await status(),false);
    if(busy)throw Error('owner-text-cutover-in-progress');busy=true;
    try{return project(await activate(),true);}finally{busy=false;}
  };
  for(const [route,method,activation] of [[ownerTextCutoverPath,'GET',false],[ownerTextActivatePath,'POST',true]]){
    ctx.inject(['webServer'],scope=>scope.effect(()=>scope.webServer.register({kind:'exact',path:route,handler:async(req,res)=>{
      const reply=(code,body)=>{res.writeHead(code,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});res.end(JSON.stringify(body));};
      try{
        if(!['127.0.0.1','::1','::ffff:127.0.0.1'].includes(req.socket?.remoteAddress))return reply(403,{ok:false,code:'owner-text-local-only'});
        const connection=ctx.get('connection');if(typeof connection?.admit!=='function')return reply(503,{ok:false,code:'owner-text-host-gate-unavailable'});
        const admission=connection.admit(req);
        if(admission.rejection!==undefined)return reply([401,403].includes(admission.rejection)?admission.rejection:403,{ok:false,code:'owner-text-host-request-rejected'});
        if(admission.peer?.principal?.kind!=='owner')return reply(403,{ok:false,code:'owner-text-owner-required'});
        if(req.method!==method||req.url?.includes('?'))return reply(405,{ok:false,code:'owner-text-method-denied'});
        if(activation){
          if(!/^application\/json(?:\s*;|$)/i.test(req.headers?.['content-type']??''))return reply(415,{ok:false,code:'owner-text-json-required'});
          const chunks=[];let size=0;for await(const chunk of req){const bytes=Buffer.from(chunk);size+=bytes.length;if(size>64)return reply(413,{ok:false,code:'owner-text-body-too-large'});chunks.push(bytes);}
          let args;try{args=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{return reply(400,{ok:false,code:'owner-text-invalid-request'});}
          if(!args||typeof args!=='object'||Array.isArray(args)||Object.keys(args).length)return reply(400,{ok:false,code:'owner-text-invalid-request'});
        }
        return reply(200,{ok:true,value:await run(activation)});
      }catch(error){return reply(503,{ok:false,code:errorCode(error)});}
    }})));
    ctx.inject(['connection'],scope=>{
      if(typeof scope.connection.fetch?.register!=='function')return;
      scope.connection.fetch.register({path:route,methods:[method],requestBody:'buffered',fetch:async req=>{
        const reply=(code,body)=>Response.json(body,{status:code,headers:{'cache-control':'no-store'}});
        try{
          const url=new URL(req.url),origin=req.headers.get('origin');
          const local=['http:','https:'].includes(url.protocol)&&['127.0.0.1','localhost','[::1]'].includes(url.hostname);
          const desktop=['dsh-app:','dsh:'].includes(url.protocol)&&url.hostname==='app';
          if((!local&&!desktop)||origin&&origin!==(local?url.origin:`${url.protocol}//${url.host}`)||['cross-site','same-site'].includes(req.headers.get('sec-fetch-site')))
            return reply(403,{ok:false,code:'owner-text-origin-denied'});
          if(req.method!==method||url.pathname!==route||url.search)return reply(405,{ok:false,code:'owner-text-method-denied'});
          if(activation){
            if(!/^application\/json(?:\s*;|$)/i.test(req.headers.get('content-type')??''))return reply(415,{ok:false,code:'owner-text-json-required'});
            const bytes=await req.arrayBuffer();if(bytes.byteLength>64)return reply(413,{ok:false,code:'owner-text-body-too-large'});
            let args;try{args=JSON.parse(Buffer.from(bytes).toString('utf8'));}catch{return reply(400,{ok:false,code:'owner-text-invalid-request'});}
            if(!args||typeof args!=='object'||Array.isArray(args)||Object.keys(args).length)return reply(400,{ok:false,code:'owner-text-invalid-request'});
          }
          return reply(200,{ok:true,value:await run(activation)});
        }catch(error){return reply(503,{ok:false,code:errorCode(error)});}
      }});
    });
  }
}
