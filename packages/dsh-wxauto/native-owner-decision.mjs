// Only consume a decision returned by the private owner-text worker RPC.
// Never reparse history, mint UI grants or repeat the approve/reject operation.
const PLAN=/^[a-f0-9]{32}$/,HASH=/^[a-f0-9]{64}$/;
const unknown=()=>({handled:true,execution:null,reply:'未能确认这项审批或操作的结果，未自动重试。'});

export async function finishNativeOwnerDecision(event,result,{ownerTarget,request,deferExecution}={}){
  if(event?.sourceKind!=='wechat-native-text'||event.kind!=='friend'||event.target!==ownerTarget
    ||event.direction!=='incoming'||event.mediaType!=='text'||!PLAN.test(event.nativeOwnerTextClaimId??'')
    ||!PLAN.test(event.authorityEpoch??'')||!/^wechat-server:[1-9][0-9]*$/.test(event.nativeId??''))return unknown();
  if(result?.decisionStatus==='unknown')return unknown();
  if(result?.handled!==true||!['approved','rejected'].includes(result.decisionStatus))return {handled:true,execution:null,
    reply:'这个确认编号不存在、已过期或不能再使用，未执行。'};
  if(!PLAN.test(result.planId??'')||!HASH.test(result.hash??'')||result.approvalMessageId!==event.nativeId
    ||!['add_friend','accept_friend','create_group'].includes(result.action))return unknown();
  if(result.decisionStatus==='rejected')return {handled:true,execution:null,reply:'已拒绝这项操作，未执行。'};
  try{
    const plan=await request('social-inspect',{target:ownerTarget,planId:result.planId});
    if(plan?.id!==result.planId||plan.hash!==result.hash||plan.action!==result.action||plan.approvedBy!==ownerTarget
      ||plan.approvalMessageId!==event.nativeId||plan.origin?.kind!=='friend'||plan.origin.target!==ownerTarget)return unknown();
    if(plan.status!=='approved')return {handled:true,execution:null,reply:plan.status==='expired'
      ?'这项方案已过期，未执行。':'这项方案未再次执行，请先核对已有结果。'};
    if(await deferExecution?.(plan)===true)return {handled:true,execution:null,
      reply:'已收到这项固定加好友方案的确认，首次功能测试尚未开始，当前未执行添加。'};
    const execution=await request('social-execute',{target:ownerTarget,planId:plan.id,hash:plan.hash});
    const reply=execution.executionStatus==='unverified'?'方案已批准，但当前微信操作后端尚未实测就绪，未执行。'
      :execution.status==='confirmed'?'操作已执行并核实结果。'
      :execution.status==='failed'?'操作未成功，未自动重试。'
      :execution.status==='expired'?'这项方案已过期，未执行。'
      :['unknown','claimed'].includes(execution.status)?'操作结果尚不确定，未自动重试。':'这项方案未再次执行。';
    return {handled:true,execution,reply};
  }catch{return unknown();}
}
