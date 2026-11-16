"""Resumable validated HTTP range download for large public model artifacts."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import requests


def download(url, output, sha256=None):
    output = Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    session = requests.Session()
    session.trust_env = False
    response = session.get(url, headers={'Range':'bytes=0-0'}, timeout=30)
    response.raise_for_status()
    if response.status_code != 206:
        raise RuntimeError('range-not-supported')
    total = int(response.headers['Content-Range'].split('/')[-1])
    chunk_size = 8*1024**2
    count = (total+chunk_size-1)//chunk_size
    partial = output.with_suffix(output.suffix+'.partial')
    state_path = output.with_suffix(output.suffix+'.parts.json')
    if output.is_file() and output.stat().st_size == total:
        if not sha256 or hashlib.file_digest(output.open('rb'),'sha256').hexdigest()==sha256:
            return
    state = {'url':url,'size':total,'chunkSize':chunk_size,'done':[]}
    if state_path.exists() and partial.exists():
        old=json.loads(state_path.read_text('utf-8'))
        if all(old.get(k)==state[k] for k in ('url','size','chunkSize')):
            state=old
    if not partial.exists():
        with partial.open('wb') as f: f.truncate(total)
    lock=threading.Lock()
    done=set(state['done'])
    def fetch(index):
        start=index*chunk_size
        end=min(total-1,start+chunk_size-1)
        for attempt in range(4):
            try:
                client=requests.Session();client.trust_env=False
                r=client.get(url,headers={'Range':f'bytes={start}-{end}'},timeout=(15,60))
                r.raise_for_status()
                expected=f'bytes {start}-{end}/{total}'
                if r.status_code!=206 or r.headers.get('Content-Range')!=expected or len(r.content)!=end-start+1:
                    raise RuntimeError('invalid-range-response')
                with partial.open('r+b') as f:
                    f.seek(start);f.write(r.content)
                with lock:
                    done.add(index);state['done']=sorted(done)
                    tmp=state_path.with_suffix('.tmp')
                    tmp.write_text(json.dumps(state),'utf-8');os.replace(tmp,state_path)
                    if len(done)%16==0 or len(done)==count:
                        print(f'{output.name}: {len(done)}/{count} chunks',flush=True)
                return
            except Exception:
                if attempt==3:raise
                time.sleep(1+attempt)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch,[i for i in range(count) if i not in done]))
    if sha256:
        with partial.open('rb') as f: actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=sha256:raise RuntimeError('sha256-mismatch')
    os.replace(partial,output)
    state_path.unlink(missing_ok=True)
    print('Ready '+str(output),flush=True)


if __name__=='__main__':
    download(sys.argv[1],sys.argv[2],sys.argv[3] if len(sys.argv)>3 else None)
