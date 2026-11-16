import {createApprovalRequester} from './social-notices.mjs';

export const friendApprovalPath='/api/local-dsh-wxauto/social/friend-lookup-plan/approval';
export const friendAcceptancePath='/api/local-dsh-wxauto/social/friend-lookup-plan/acceptance';
export const friendRequestApprovalPath='/api/local-dsh-wxauto/social/friend-lookup-plan/request-approval';
export const friendRequestAcceptancePath='/api/local-dsh-wxauto/social/friend-lookup-plan/request-acceptance';
const FIRST_NOTICE='这是首次加好友功能验收。确认后会核对账号和昵称，再尝试点击“添加到通讯录”。如果出现尚未识别的申请窗口，会停止处理，不会自动重试。\n\n';
const safeCode=error=>/^social-[a-z0-9-]{1,100}$/.test(error?.message??'')?error.message:'social-initial-contact-outcome-unknown';

function makeFriendAcceptanceActions({getPreparedPlan,request,sendNotice,ownerTarget,onResult=()=>{},onError=()=>{}},fullRequest){
  let busy=false;
  const approve=createApprovalRequester({request,ownerTarget,...(fullRequest?{purpose:'add-friend-request'}:{}),
    send:args=>sendNotice({...args,text:(fullRequest?'':FIRST_NOTICE)+args.text})});
  async function run(args,execute){
    if(!args||Object.keys(args).sort().join(',')!=='hash,planId'||!/^[0-9a-f]{32}$/.test(args.planId??'')||!/^[0-9a-f]{64}$/.test(args.hash??''))
      throw Error('social-initial-contact-arguments-invalid');
    if(busy)throw Error('social-initial-contact-in-progress');
    busy=true;
    let requestAttempted=false;
    try{
      const plan=await getPreparedPlan();
      if(plan.planId!==args.planId||plan.hash!==args.hash||!plan.expectedProfileName)throw Error('social-prepared-plan-mismatch');
      if(!execute){
        if(plan.status==='approved'){
          if(fullRequest&&plan.approvalPurpose!=='add-friend-request')throw Error('social-owner-approval-required');
          return {outcome:'already-approved'};
        }
        if(plan.status!=='pending')throw Error('social-owner-approval-required');
        return await approve({id:plan.planId,hash:plan.hash},{kind:'friend',target:ownerTarget});
      }
      if(plan.status!=='approved')throw Error('social-owner-approval-required');
      if(fullRequest&&plan.approvalPurpose!=='add-friend-request')throw Error('social-owner-approval-required');
      requestAttempted=true;
      const raw=await request('social-acceptance',{target:ownerTarget,purpose:fullRequest?'add-friend-request':'add-friend-initial-contact',planId:plan.planId,hash:plan.hash});
      if(raw?.id!==plan.planId||raw.hash!==plan.hash||!['approved','claimed','failed','unknown','confirmed','rejected','expired'].includes(raw.status)
        ||!['not-dispatched','dispatched'].includes(raw.executionStatus))throw Error('social-initial-contact-result-invalid');
      const value={id:raw.id,hash:raw.hash,status:raw.status,executionStatus:raw.executionStatus};
      if(fullRequest&&raw.status==='confirmed'){
        if(raw.executionStatus!=='dispatched'||typeof raw.chatRestored!=='boolean'
          ||raw.confirmationKind!=='friend-request-submission'||raw.applicationSubmittedVerified!==true||raw.friendshipConfirmed!==false)
          throw Error('social-initial-contact-result-invalid');
        Object.assign(value,{confirmationKind:raw.confirmationKind,applicationSubmittedVerified:true,friendshipConfirmed:false});
      }
      if(typeof raw.resultCode==='string'&&/^[a-z0-9-]{1,120}$/.test(raw.resultCode))value.resultCode=raw.resultCode;
      if(typeof raw.chatRestored==='boolean')value.chatRestored=raw.chatRestored;
      if(fullRequest&&raw.acceptancePromoted!==undefined){
        if(typeof raw.acceptancePromoted!=='boolean')throw Error('social-initial-contact-result-invalid');
        value.acceptancePromoted=raw.acceptancePromoted;
      }
      if(fullRequest&&raw.acceptanceCode!==undefined){
        if(raw.acceptanceCode!=='social-acceptance-chat-recovery-unverified')throw Error('social-initial-contact-result-invalid');
        value.acceptanceCode=raw.acceptanceCode;
      }
      for(const key of ['addContactClickAttempted','preflightObserved'])if(raw[key]!==undefined){
        if(typeof raw[key]!=='boolean')throw Error('social-initial-contact-result-invalid');
        value[key]=raw[key];
      }
      if(raw.failurePhase!==undefined){
        if(!['constructor','binding','preflight','perform','observe-after','acceptance'].includes(raw.failurePhase))throw Error('social-initial-contact-result-invalid');
        value.failurePhase=raw.failurePhase;
      }
      const failureCodes=new Set(['social-chat-restore-failed','social-backend-plan-mismatch','social-backend-approval-required',
        'social-target-or-parameters-unverified','social-client-changed','social-authorization-expired','social-backend-preflight-failed',
        'social-post-action-evidence-missing','social-post-action-outcome-unknown','social-acceptance-outcome-unknown',
        'social-acceptance-binding-invalid','social-initial-failure-unclassified']);
      if(raw.primaryFailureCode!==undefined){
        if(!failureCodes.has(raw.primaryFailureCode))throw Error('social-initial-contact-result-invalid');
        value.primaryFailureCode=raw.primaryFailureCode;
      }
      if(raw.cleanupFailureCode!==undefined){
        if(raw.cleanupFailureCode!=='social-chat-restore-failed')throw Error('social-initial-contact-result-invalid');
        value.cleanupFailureCode=raw.cleanupFailureCode;
      }
      if(raw.diagnosticArtifact!==undefined){
        const artifact=raw.diagnosticArtifact;
        if(artifact?.kind!=='unverified-ui-observation'||!/^[0-9a-f]{64}$/.test(artifact.sha256??'')
          ||!new RegExp('^backups/social-initial-contact/'+plan.planId+'/[0-9a-f]{32}\\.json$').test(artifact.path??''))
          throw Error('social-initial-contact-result-invalid');
        value.diagnosticArtifact={path:artifact.path,sha256:artifact.sha256,kind:artifact.kind};
      }
      if(['social-initial-diagnostic-unavailable','social-initial-diagnostic-save-failed'].includes(raw.diagnosticError))value.diagnosticError=raw.diagnosticError;
      onResult(value);return value;
    }catch(error){
      if(execute)onError(safeCode(error),{requestAttempted,automaticRetry:false});
      throw error;
    }finally{busy=false;}
  }
  return {approval:args=>run(args,false),acceptance:args=>run(args,true)};
}

export const createFriendAcceptanceActions=options=>makeFriendAcceptanceActions(options,false);
export const createFriendRequestAcceptanceActions=options=>makeFriendAcceptanceActions(options,true);

// Fixed owner-only routes. Native admission is enforced by the official fetch
// service before its callback; HTTP admission explicitly requires an owner peer.
export function registerFriendAcceptance(ctx,options){
  if(typeof ctx.inject!=='function')return;
  const actions=createFriendAcceptanceActions(options);
  const full=createFriendRequestAcceptanceActions(options);
  for(const [path,action] of [[friendApprovalPath,actions.approval],[friendAcceptancePath,actions.acceptance],
    [friendRequestApprovalPath,full.approval],[friendRequestAcceptancePath,full.acceptance]]){
    ctx.inject(['webServer'],scope=>scope.effect(()=>scope.webServer.register({kind:'exact',path,handler:async(req,res)=>{
      const reply=(status,body)=>{res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});res.end(JSON.stringify(body));};
      try{
        if(!['127.0.0.1','::1','::ffff:127.0.0.1'].includes(req.socket?.remoteAddress))return reply(403,{ok:false,code:'social-local-only'});
        const connection=ctx.get('connection');
        if(typeof connection?.admit!=='function')return reply(503,{ok:false,code:'social-host-gate-unavailable'});
        const admission=connection.admit(req);
        if(admission.rejection!==undefined)return reply([401,403].includes(admission.rejection)?admission.rejection:403,{ok:false,code:'social-host-request-rejected'});
        if(admission.peer?.principal?.kind!=='owner')return reply(403,{ok:false,code:'social-owner-private-required'});
        if(req.method!=='POST'||req.url?.includes('?'))return reply(405,{ok:false,code:'social-post-required'});
        if(!/^application\/json(?:\s*;|$)/i.test(req.headers?.['content-type']??''))return reply(415,{ok:false,code:'social-json-required'});
        const chunks=[];let size=0;
        for await(const chunk of req){const bytes=Buffer.from(chunk);size+=bytes.length;if(size>256)return reply(413,{ok:false,code:'social-body-too-large'});chunks.push(bytes);}
        let args;try{args=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{return reply(400,{ok:false,code:'social-invalid-request'});}
        return reply(200,{ok:true,value:await action(args)});
      }catch(error){return reply(503,{ok:false,code:safeCode(error)});}
    }})));
    ctx.inject(['connection'],scope=>{
      if(typeof scope.connection.fetch?.register!=='function')return;
      scope.connection.fetch.register({path,methods:['POST'],requestBody:'buffered',fetch:async req=>{
        const reply=(status,body)=>Response.json(body,{status,headers:{'cache-control':'no-store'}});
        try{
          const url=new URL(req.url),origin=req.headers.get('origin');
          const local=['http:','https:'].includes(url.protocol)&&['127.0.0.1','localhost','[::1]'].includes(url.hostname);
          const desktop=['dsh-app:','dsh:'].includes(url.protocol)&&url.hostname==='app';
          if((!local&&!desktop)||origin&&origin!==(local?url.origin:`${url.protocol}//${url.host}`)||['cross-site','same-site'].includes(req.headers.get('sec-fetch-site')))
            return reply(403,{ok:false,code:'social-origin-denied'});
          if(req.method!=='POST'||url.pathname!==path||url.search)return reply(405,{ok:false,code:'social-post-required'});
          if(!/^application\/json(?:\s*;|$)/i.test(req.headers.get('content-type')??''))return reply(415,{ok:false,code:'social-json-required'});
          const bytes=await req.arrayBuffer();if(bytes.byteLength>256)return reply(413,{ok:false,code:'social-body-too-large'});
          let args;try{args=JSON.parse(Buffer.from(bytes).toString('utf8'));}catch{return reply(400,{ok:false,code:'social-invalid-request'});}
          return reply(200,{ok:true,value:await action(args)});
        }catch(error){return reply(503,{ok:false,code:safeCode(error)});}
      }});
    });
  }
}
