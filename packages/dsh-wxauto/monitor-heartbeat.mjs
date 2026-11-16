// Heartbeat is loop liveness. Only an increased poll counter proves receive progress.
export function monitorHeartbeatUpdate(previous,event,{at=new Date().toISOString(),targets=[]}={}) {
  const result={lastWorkerHeartbeatAt:at};
  const count=value=>Number.isSafeInteger(value)&&value>=0;
  if(count(event.polls)){
    const old=count(previous.monitorPollCount)?previous.monitorPollCount:0;
    result.monitorPollCount=event.polls;
    if(event.polls>old)result.lastPollAt=at;
    result.monitorCounterReset=event.polls<old;
    result.monitorHeartbeatError=null;
  }else result.monitorHeartbeatError='invalid-poll-count';
  const allowed=new Set(targets),deferred=event.deferredDirectionRows;
  result.monitorActivity={deliveryActive:typeof event.deliveryActive==='boolean'?event.deliveryActive:null,
    queuedCommands:count(event.queuedCommands)?event.queuedCommands:null,
    deferredDirectionRows:deferred&&typeof deferred==='object'&&!Array.isArray(deferred)
      ?Object.fromEntries(Object.entries(deferred).filter(([name,n])=>allowed.has(name)&&count(n)&&n<=10000)):null};
  return result;
}
