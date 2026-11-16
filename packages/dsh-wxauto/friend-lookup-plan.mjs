// Only a fixed local pointer selects the plan; HTTP clients cannot supply IDs.
export async function preparedFriendLookup({readPointer,request,ownerTarget,now=Date.now}){
  const pointer=readPointer();
  if(!pointer||Object.keys(pointer).sort().join(',')!=='hash,planId'
    ||!/^[0-9a-f]{32}$/.test(pointer.planId??'')||!/^[0-9a-f]{64}$/.test(pointer.hash??''))
    throw Error('social-prepared-plan-unavailable');
  const plan=await request('social-inspect',{target:ownerTarget,planId:pointer.planId});
  const terminalReadOnly=['failed','unknown','confirmed'].includes(plan?.status)
    &&plan.approvedBy===ownerTarget&&typeof plan.approvalMessageId==='string'&&!!plan.approvalMessageId.trim();
  if(plan?.id!==pointer.planId||plan.hash!==pointer.hash||plan.action!=='add_friend'
    ||(!['pending','approved'].includes(plan.status)&&!terminalReadOnly)||!Number.isFinite(plan.expiresAt)||plan.expiresAt*1000<=now()
    ||plan.origin?.kind!=='friend'||plan.origin?.target!==ownerTarget
    ||typeof plan.parameters?.keywords!=='string'||!/^[A-Za-z0-9_-]{5,128}$/.test(plan.parameters.keywords)
    ||typeof (plan.parameters.addmsg??'')!=='string'||(plan.parameters.addmsg??'').length>256)
    throw Error('social-prepared-plan-mismatch');
  if(typeof (plan.parameters.expectedProfileName??'')!=='string'||Array.from(plan.parameters.expectedProfileName??'').length>128)
    throw Error('social-prepared-plan-mismatch');
  // Project a recorded observation, never change the terminal execution state.
  // This is historical UI evidence, not a live friendship check or write gate.
  const observed=plan.postActionObservation,observedAt=Date.parse(observed?.observedAt);
  const lastRequestObservation=observed?.source==='wxauto-uia'
    &&observed.requestState==='pending-verification'&&observed.friendshipConfirmed===false
    &&observed.automaticRetry===false&&observed.normalCapabilityPromoted===false
    &&observed.evidencePath===`backups/social-initial-contact/${plan.id}/verified-request-pending.json`
    &&/^[a-f0-9]{64}$/.test(observed.evidenceSha256??'')
    &&Number.isFinite(plan.createdAt)&&Number.isFinite(observedAt)
    &&observedAt>=plan.createdAt*1000&&observedAt<=now()+5000
      ?{state:'pending-verification',observedAt:observed.observedAt}:null;
  return {planId:plan.id,hash:plan.hash,account:plan.parameters.keywords,greeting:plan.parameters.addmsg??'',
    expectedProfileName:plan.parameters.expectedProfileName??'',status:plan.status,
    ...(plan.approvalNotice?.state==='claimed'?{approvalNoticeClaimed:true,
      ...(plan.approvalNotice.purpose==='add-friend-request'?{approvalPurpose:'add-friend-request'}:{})}:{}),
    ...(lastRequestObservation?{lastRequestObservation}:{})};
}
