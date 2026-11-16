// Approval text is rendered by the trusted plan store, never by the model.
export function createApprovalRequester({request,send,ownerTarget,purpose}) {
  if(purpose!==undefined&&purpose!=='add-friend-request')throw Error('social-notice-purpose-denied');
  return async (plan,origin)=>{
    let claimed=false;
    try {
      const notice=await request('social-approval-notice',{target:origin.target,planId:plan.id,hash:plan.hash,...(purpose?{purpose}:{})});
      if(notice?.claimed!==true)return {outcome:'not-sent',state:notice?.state||'not-claimed'};
      claimed=true;
      if(purpose&&notice.purpose!==purpose)throw Error('social-notice-purpose-mismatch');
      if(notice.planId!==plan.id||notice.hash!==plan.hash||notice.ownerTarget!==ownerTarget
          ||typeof notice.text!=='string'||!notice.text.trim()||!/^[0-9a-f]{8}$/i.test(notice.code||'')){
        throw Error('social-notice-invalid');
      }
      const dual=notice.approveCode!==undefined||notice.rejectCode!==undefined;
      if(dual&&(!/^[0-9a-f]{8}$/i.test(notice.approveCode??'')||!/^[0-9a-f]{8}$/i.test(notice.rejectCode??'')
        ||notice.approveCode.toLowerCase()===notice.rejectCode.toLowerCase()||notice.code!==notice.approveCode
        ||!/^[0-9a-f]{32}$/.test(notice.noticeId??'')))throw Error('social-notice-invalid');
      await send({text:notice.text,target:ownerTarget,id:'social-approval:'+plan.id+(dual?':'+notice.noticeId:'')});
      return {outcome:'sent',code:notice.code,...(dual?{approveCode:notice.approveCode,rejectCode:notice.rejectCode}:{})};
    } catch {
      // The durable claim prevents automatic duplicate questions after a crash
      // or an acknowledgement loss. A notice failure never approves the plan.
      return {outcome:claimed?'failed-or-unknown':'not-confirmed',automaticRetry:false};
    }
  };
}

export function literalNoticeParts(text,maxChars=1800) {
  if(typeof text!=='string'||!text.trim())throw Error('social-notice-empty');
  if(!Number.isInteger(maxChars)||maxChars<1)throw Error('social-notice-size-invalid');
  // No voice/meme/@ parsing. Parameters are literal review material, even if
  // an external applicant inserted strings that resemble channel directives.
  const chars=Array.from(text),parts=[];
  for(let at=0;at<chars.length;at+=maxChars)parts.push({kind:'text',text:chars.slice(at,at+maxChars).join('')});
  return parts;
}
