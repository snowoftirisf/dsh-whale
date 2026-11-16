// Fixed-purpose projection of sanitized UI metadata, never a post/action receipt.
const fail=()=>{throw Error('social-observation-result-invalid');};
const sha=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const count=(v,max)=>Number.isSafeInteger(v)&&v>=0&&v<=max;
export function publicMomentsDiagnostic(v){
  const phases=['preflight','checking','prechecks','locating-discover','discover-open','locating-moments','moments-open',
    'locating-feed','collecting','exiting-feed','completed'];
  const classes=['mmui::XTabBarItem','mmui::ExtensionDiscoverContentCell','mmui::SNSContentView'];
  if(!v||v.purpose!=='moments-feed-metadata'||typeof v.attemptId!=='string'||!/^[a-f0-9]{32}$/.test(v.attemptId)
    ||!phases.includes(v.phase)||typeof v.completed!=='boolean'
    ||(v.chatRestored!==null&&typeof v.chatRestored!=='boolean')
    ||(v.expectedClass!==undefined&&v.expectedClass!==null&&!classes.includes(v.expectedClass))
    ||(v.matches!==undefined&&v.matches!==null&&!count(v.matches,4500))
    ||(v.code!==undefined&&v.code!==null&&(typeof v.code!=='string'||!/^social-[a-z0-9-]{1,100}$/.test(v.code)))
    ||typeof v.durationMs!=='number'||!Number.isFinite(v.durationMs)||v.durationMs<0||v.durationMs>86400000
    ||v.completed&&v.chatRestored!==true)fail();
  return {purpose:v.purpose,attemptId:v.attemptId,phase:v.phase,completed:v.completed,chatRestored:v.chatRestored,
    expectedClass:v.expectedClass??null,matches:v.matches??null,code:v.code??null,durationMs:v.durationMs};
}
const label=v=>{
  if(v===null)return null;
  if(!v||!sha(v.sha256)||!count(v.length,32000))fail();
  return {sha256:v.sha256,length:v.length};
};
function pattern(v,fields){
  if(!v||typeof v.available!=='boolean')fail();
  const out={available:v.available};
  for(const flag of ['readFailed','textMayBeTruncated'])if(v[flag]!==undefined){if(typeof v[flag]!=='boolean')fail();out[flag]=v[flag];}
  for(const field of fields)if(v[field]!==undefined)out[field]=label(v[field]);
  return out;
}
export function publicMomentsObservation(v){
  if(!v||v.purpose!=='moments-feed-metadata'||v.version!==1||v.source!=='wxauto-uia-readonly-metadata'
    ||v.scope!=='moments-feed'||v.state!=='post-reader-unobserved'||v.readOnly!==true
    ||v.sentMessages!==false||v.liveCapabilityGranted!==false||v.chatRestored!==true
    ||v.clientVersion!=='4.2.0.12'||!sha(v.clientSha256)||!sha(v.restoreTargetSha256)
    ||typeof v.observedAt!=='number'||!Number.isFinite(v.observedAt)||v.observedAt<=0
    ||!Array.isArray(v.nodes)||v.nodes.length>256||!count(v.visitedNodes,256)||v.visitedNodes<v.nodes.length
    ||typeof v.truncated!=='boolean'||v.creationTimeGranularity!=='unobserved'
    ||['postIdentityVerified','authorVerified','headBoundaryVerified','emptyTimelineVerified'].some(k=>v[k]!==false))fail();
  const nodes=v.nodes.map((n,index)=>{
    if(!n||n.index!==index||!count(n.depth,10)||n.rootIndex!==0||!sha(n.runtimeIdSha256)
      ||(n.parentIndex!==null&&(!count(n.parentIndex,255)||n.parentIndex>=index))
      ||[n.class,n.type].some(s=>typeof s!=='string'||!/^[A-Za-z0-9_:.-]{0,160}$/.test(s)))fail();
    if(n.bounds!==null&&(!Array.isArray(n.bounds)||n.bounds.length!==4||n.bounds.some(x=>typeof x!=='number'||!Number.isFinite(x))
      ||n.bounds[2]<=n.bounds[0]||n.bounds[3]<=n.bounds[1]))fail();
    return {index,parentIndex:n.parentIndex,depth:n.depth,rootIndex:0,class:n.class,type:n.type,
      runtimeIdSha256:n.runtimeIdSha256,bounds:n.bounds===null?null:[...n.bounds],name:label(n.name),automationId:label(n.automationId),
      legacy:pattern(n.legacy,['Name','Value','Description']),value:pattern(n.value,['Value']),text:pattern(n.text,['documentText'])};
  });
  return Object.fromEntries([...['purpose','version','source','scope','state','readOnly','sentMessages','liveCapabilityGranted',
    'chatRestored','clientVersion','clientSha256','restoreTargetSha256','observedAt','visitedNodes','truncated',
    'postIdentityVerified','authorVerified','headBoundaryVerified','emptyTimelineVerified','creationTimeGranularity'].map(k=>[k,v[k]]),['nodes',nodes]]);
}
