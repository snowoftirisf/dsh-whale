// Session identity comes from trusted runtime state, never prompt text.
const validId=id=>typeof id==='string'&&id.trim().length>0&&id.length<=512&&!/[\r\n\0]/.test(id);

export function resolveOwnerSession({service,fallback,reservedIds=[]}) {
  let id;
  try {id=typeof service?.getSessionId==='function'?service.getSessionId():fallback;}
  catch {return null;}
  return validId(id)&&!reservedIds.includes(id)?id:null;
}

export function uniqueSessionRoute(routes,id,sessionForRoute=route=>route.sessionId) {
  if(!validId(id))return undefined;
  let matches;
  try {matches=[...routes.values()].filter(route=>sessionForRoute(route)===id);}
  catch {return undefined;}
  return matches.length===1?matches[0]:undefined;
}
