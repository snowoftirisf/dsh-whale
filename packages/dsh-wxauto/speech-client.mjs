import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';

// Private stdio transport. Speech inference never runs in the WeChat UI worker.
export function speechClient({python, script, dataRoot, report = () => {}, timeoutMs = 300000,
  spawnProcess = spawn, platform = process.platform, shutdownGraceMs = 5000}) {
  const worker = spawnProcess(python, ['-u', script], {windowsHide:true, stdio:['pipe','pipe','pipe'],
    env:{...process.env, ...(dataRoot?{WHALE_DATA_ROOT:dataRoot}:{}), PYTHONIOENCODING:'utf-8', HF_HUB_OFFLINE:'1', TRANSFORMERS_OFFLINE:'1'}});
  const pending = new Map();
  let closed = false;
  const terminateTree = () => {
    if(worker.exitCode !== null || worker.signalCode !== null) return;
    if(platform === 'win32' && Number.isInteger(worker.pid)) {
      // A Windows venv launcher and guide extractor can have their own children.
      // Killing only the launcher leaves inference/download work orphaned.
      const killer=spawnProcess('taskkill.exe',['/PID',String(worker.pid),'/T','/F'],
        {windowsHide:true,stdio:'ignore'});
      killer.on('error',()=>worker.kill());
    } else worker.kill();
  };
  worker.stderr.on('data', () => {});
  const fail = () => {
    const wasClosed = closed;
    closed = true;
    for (const p of pending.values()) { clearTimeout(p.timer); p.reject(Error('speech-worker-exited')); }
    pending.clear();
    if(!wasClosed)report('speech-worker-exited');
  };
  worker.on('error', fail);
  worker.on('exit', fail);
  const lines = createInterface({input:worker.stdout});
  lines.on('line', line => {
    let result; try {result=JSON.parse(line)} catch {return}
    const p = pending.get(result.id); if (!p) return;
    clearTimeout(p.timer); pending.delete(result.id);
    result.ok ? p.resolve(result.value) : p.reject(Error(`speech-${result.code}`));
  });
  return {
    available() {return !closed},
    request(op, args = {}) {
      if (closed) return Promise.reject(Error('speech-worker-closed'));
      return new Promise((resolve,reject) => {
        const id=randomUUID();
        const timer=setTimeout(()=>{pending.delete(id);reject(Error('speech-timeout'))},timeoutMs);
        pending.set(id,{resolve,reject,timer});
        worker.stdin.write(JSON.stringify({...args,op,id})+'\n',error=>{
          if(error){clearTimeout(timer);pending.delete(id);reject(error)}
        });
      });
    },
    close() {
      if(!closed){
        closed=true;
        for (const p of pending.values()) {clearTimeout(p.timer);p.reject(Error('speech-worker-closed'))}
        pending.clear();
        worker.stdin.end(JSON.stringify({op:'stop'})+'\n');
        setTimeout(terminateTree,shutdownGraceMs).unref();
      }
      lines.close();
    },
  };
}
