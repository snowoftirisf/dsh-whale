// Fixed-purpose local diagnostics. No arbitrary target, UI command or writer API.
import {publicMomentsObservation,publicMomentsDiagnostic} from './moments-observe-public.mjs';
export const socialObservePath='/local-dsh-wxauto/social/observe';
export const nativeSocialObservePath='/api'+socialObservePath;
export const preparedFriendPlanPath='/api/local-dsh-wxauto/social/friend-lookup-plan';

function validPurpose(args){
  if(!args||typeof args!=='object'||Array.isArray(args))return false;
  if(['friend-search-owner','moments-feed-metadata'].includes(args.purpose))return Object.keys(args).length===1;
  return args.purpose==='friend-search-plan'&&Object.keys(args).sort().join(',')==='hash,planId,purpose'
    &&/^[0-9a-f]{32}$/.test(args.planId??'')&&/^[0-9a-f]{64}$/.test(args.hash??'');
}
function errorCode(error){return /^social-[a-z0-9-]{1,100}$/.test(error?.message??'')?error.message:'social-observation-failed';}
export function createObservationAction({request,ownerTarget,onResult=()=>{},onError=()=>{}}){
  let busy=false;
  return async args=>{
    if(!validPurpose(args))throw Error('social-invalid-request');
    if(busy)throw Error('social-observation-in-progress');
    busy=true;
    try{
      const result=publicObservation(await request('social-observe',{target:ownerTarget,...args}));
      if(result.purpose!==args.purpose||args.purpose==='friend-search-plan'&&(result.planId!==args.planId||result.planHash!==args.hash))
        throw Error('social-observation-result-invalid');
      onResult(result);return result;
    }catch(error){
      let diagnostic;
      if(args.purpose==='moments-feed-metadata'&&error?.observation){try{diagnostic=publicMomentsDiagnostic(error.observation);}catch{}}
      if(diagnostic)onError(errorCode(error),diagnostic);else onError(errorCode(error));throw error;
    }
    finally{busy=false;}
  };
}

function publicObservation(value){
  if(value?.purpose==='moments-feed-metadata')return publicMomentsObservation(value);
  if(!value||!['friend-search-owner','friend-search-plan'].includes(value.purpose))throw Error('social-observation-result-invalid');
  const result={purpose:value.purpose};
  if(value.purpose==='friend-search-plan'){
    if(!/^[0-9a-f]{32}$/.test(value.planId??'')||!/^[0-9a-f]{64}$/.test(value.planHash??'')
      ||!Array.isArray(value.profileButtons)||value.profileButtons.length>64)throw Error('social-observation-result-invalid');
    result.planId=value.planId;result.planHash=value.planHash;
    if(!['matched','id-unavailable','id-ambiguous','id-invalid','id-mismatch','unsupported-profile'].includes(value.identityEvidence))
      throw Error('social-observation-result-invalid');
    result.identityEvidence=value.identityEvidence;
    result.profileButtons=value.profileButtons.map(button=>{
      if(!/^[\w:]{1,120}$/.test(button.class??'')||!/^\w{1,80}$/.test(button.type??'')
        ||!/^[0-9a-f]{64}$/.test(button.nameSha256??'')||!['add-contact','send-message','unknown'].includes(button.label))
        throw Error('social-observation-result-invalid');
      return Object.fromEntries(['class','type','nameSha256','label'].map(key=>[key,button[key]]));
    });
  }
  for(const key of ['readOnly','queryValueMatched','profileIdentityMatched','responseFreshnessVerified',
    'newSearchCompleted','accountMatched','applicationSubmitted','friendAccepted','sentMessages','liveCapabilityGranted','chatRestored']){
    if(typeof value[key]!=='boolean')throw Error('social-observation-result-invalid');
    result[key]=value[key];
  }
  if(!result.readOnly||result.applicationSubmitted||result.friendAccepted||result.sentMessages||result.liveCapabilityGranted)
    throw Error('social-observation-result-invalid');
  if(result.purpose==='friend-search-plan'&&result.profileIdentityMatched!==(result.identityEvidence==='matched'))
    throw Error('social-observation-result-invalid');
  for(const key of ['querySha256','clientSha256','restoreTargetSha256']){
    if(!/^[0-9a-f]{64}$/.test(value[key]??''))throw Error('social-observation-result-invalid');
    result[key]=value[key];
  }
  if(!/^\d{1,4}(?:\.\d{1,4}){1,3}$/.test(value.clientVersion??'')||!['unknown','no-match-observed','profile-observed'].includes(value.state))
    throw Error('social-observation-result-invalid');
  result.clientVersion=value.clientVersion;result.state=value.state;
  if(!Array.isArray(value.controls)||value.controls.length>256)throw Error('social-observation-result-invalid');
  result.controls=value.controls.map(control=>{
    if(!/^[\w:]{1,120}$/.test(control.class??'')||!/^\w{1,80}$/.test(control.type??'')
      ||!['automationIdSha256','runtimeIdSha256'].every(key=>/^[0-9a-f]{64}$/.test(control[key]??'')))
      throw Error('social-observation-result-invalid');
    return Object.fromEntries(['class','type','automationIdSha256','runtimeIdSha256'].map(key=>[key,control[key]]));
  });
  return result;
}

function reply(res,status,value){
  res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});
  res.end(JSON.stringify(value));
}

export function socialObservationHandler({connection,request,ownerTarget,observe=createObservationAction({request,ownerTarget})}){
  let busy=false;
  return async(req,res)=>{
    const peer=req.socket?.remoteAddress;
    if(!['127.0.0.1','::1','::ffff:127.0.0.1'].includes(peer))return reply(res,403,{ok:false,code:'social-local-only'});
    // Preserve the host's authentication and authority fence; unavailable is closed.
    try{
      const service=connection();
      if(typeof service?.requestRejection!=='function')return reply(res,503,{ok:false,code:'social-host-gate-unavailable'});
      const rejection=service.requestRejection(req);
      if(rejection!==undefined&&rejection!==null&&rejection!==false)
        return reply(res,[401,403].includes(rejection)?rejection:403,{ok:false,code:'social-host-request-rejected'});
    }catch{return reply(res,503,{ok:false,code:'social-host-gate-unavailable'});}
    if(req.method!=='POST')return reply(res,405,{ok:false,code:'social-post-required'});
    if(!/^application\/json(?:\s*;|$)/i.test(req.headers?.['content-type']??''))
      return reply(res,415,{ok:false,code:'social-json-required'});
    if(busy)return reply(res,409,{ok:false,code:'social-observation-in-progress'});
    busy=true;
    try{
      const chunks=[];let size=0;
      for await(const chunk of req){
        const bytes=Buffer.from(chunk);size+=bytes.length;
        if(size>256)return reply(res,413,{ok:false,code:'social-body-too-large'});
        chunks.push(bytes);
      }
      let args;try{args=JSON.parse(Buffer.concat(chunks).toString('utf8'));}
      catch{return reply(res,400,{ok:false,code:'social-invalid-request'});}
      if(!validPurpose(args))
        return reply(res,400,{ok:false,code:'social-invalid-request'});
      return reply(res,200,{ok:true,value:await observe(args)});
    }catch(error){
      const code=errorCode(error);
      return reply(res,503,{ok:false,code});
    }finally{busy=false;}
  };
}

// The installed connection service admits authenticated owner peers BEFORE this
// fetch callback. Do not expose this callback through an unauthenticated bridge.
export function nativeSocialObservationHandler(observe){
  return async request=>{
    const send=(status,body)=>Response.json(body,{status,headers:{'cache-control':'no-store'}});
    const url=new URL(request.url),origin=request.headers.get('origin');
    const local=['http:','https:'].includes(url.protocol)&&['127.0.0.1','localhost','[::1]'].includes(url.hostname);
    const desktop=['dsh-app:','dsh:'].includes(url.protocol)&&url.hostname==='app';
    const own=local?url.origin:`${url.protocol}//${url.host}`;
    if((!local&&!desktop)||origin&&origin!==own||['cross-site','same-site'].includes(request.headers.get('sec-fetch-site')))
      return send(403,{ok:false,code:'social-origin-denied'});
    if(url.pathname!==nativeSocialObservePath||url.search)return send(404,{ok:false,code:'social-path-denied'});
    if(request.method!=='POST')return send(405,{ok:false,code:'social-post-required'});
    if(!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')??''))return send(415,{ok:false,code:'social-json-required'});
    const bytes=await request.arrayBuffer();if(bytes.byteLength>256)return send(413,{ok:false,code:'social-body-too-large'});
    let args;try{args=JSON.parse(Buffer.from(bytes).toString('utf8'));}catch{return send(400,{ok:false,code:'social-invalid-request'});}
    if(!validPurpose(args))return send(400,{ok:false,code:'social-invalid-request'});
    try{return send(200,{ok:true,value:await observe(args)});}
    catch(error){const code=errorCode(error);return send(code==='social-observation-in-progress'?409:503,{ok:false,code});}
  };
}

export function registerSocialObservation(ctx,options){
  if(typeof ctx.inject!=='function')return;
  const observe=createObservationAction(options);
  ctx.inject(['webServer'],scope=>{
    const handler=socialObservationHandler({connection:()=>ctx.get('connection'),observe});
    scope.effect(()=>scope.webServer.register({kind:'exact',path:socialObservePath,handler}));
    scope.effect(()=>scope.webServer.register({kind:'exact',path:nativeSocialObservePath,handler}));
    if(options.getPreparedPlan)scope.effect(()=>scope.webServer.register({kind:'exact',path:preparedFriendPlanPath,
      handler:async(req,res)=>{
        const connection=ctx.get('connection');
        if(!['127.0.0.1','::1','::ffff:127.0.0.1'].includes(req.socket?.remoteAddress))return reply(res,403,{ok:false,code:'social-local-only'});
        if(typeof connection?.admit!=='function')return reply(res,503,{ok:false,code:'social-host-gate-unavailable'});
        const admission=connection.admit(req);
        if(admission.rejection!==undefined)return reply(res,[401,403].includes(admission.rejection)?admission.rejection:403,{ok:false,code:'social-host-request-rejected'});
        if(admission.peer?.principal?.kind!=='owner')return reply(res,403,{ok:false,code:'social-owner-private-required'});
        if(req.method!=='GET'||req.url?.includes('?'))return reply(res,405,{ok:false,code:'social-get-required'});
        try{return reply(res,200,{ok:true,value:await options.getPreparedPlan()});}
        catch(error){return reply(res,503,{ok:false,code:errorCode(error)});}
      }}));
  });
  ctx.inject(['connection'],scope=>{
    if(typeof scope.connection.fetch?.register!=='function')return;
    scope.connection.fetch.register({path:nativeSocialObservePath,methods:['POST'],requestBody:'buffered',
      fetch:nativeSocialObservationHandler(observe)});
    if(options.getPreparedPlan)scope.connection.fetch.register({path:preparedFriendPlanPath,methods:['GET'],
      fetch:async request=>{
        const url=new URL(request.url);
        if(url.pathname!==preparedFriendPlanPath||url.search||request.method!=='GET')return Response.json({ok:false,code:'social-path-denied'},{status:400});
        try{return Response.json({ok:true,value:await options.getPreparedPlan()},{headers:{'cache-control':'no-store'}});}
        catch(error){return Response.json({ok:false,code:errorCode(error)},{status:503});}
      }});
  });
}
