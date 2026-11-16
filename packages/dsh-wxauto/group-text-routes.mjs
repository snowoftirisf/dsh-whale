// Native group text uses its own current registry, not the voice owner's key.
export function nativeGroupTextRoutes({accountId,routes,routeSessionId,registry}={}){
  const fail=()=>{throw Error('group-text-routes-unverified');};
  if(typeof accountId!=='string'||!accountId||!(routes instanceof Map)||typeof routeSessionId!=='function'
    ||registry?.version!==1||registry.accountId!==accountId||!Array.isArray(registry.groups)||registry.groups.length>1000)fail();
  const result=new Map(),targets=new Set();
  for(const row of registry.groups){
    if(!row||typeof row!=='object')fail();
    if(row.status!=='active')continue;
    if(row.accountId!==accountId||!/^wechat:[A-Za-z0-9_-]{1,256}@chatroom$/.test(row.nativeChatKey??'')
      ||['target','sessionId','chatId'].some(k=>typeof row[k]!=='string'||!row[k]||row[k].includes('\0'))
      ||result.has(row.nativeChatKey)||targets.has(row.target))fail();
    const route=routes.get(row.target);
    if(!route||route.active===false)continue; // Admission has not enabled it yet.
    if(route.kind!=='group'||routeSessionId(route)!==row.sessionId)fail();
    result.set(row.nativeChatKey,{...route,target:row.target,kind:'group',nativeChatKey:row.nativeChatKey,
      sessionId:row.sessionId,chatId:row.chatId,active:true});
    targets.add(row.target);
  }
  return result;
}
