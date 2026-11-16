"""Replay one specified self voice via wxauto; capture only its WeChat process.

Local acceptance utility, no message sending and no general microphone capture.
"""
import argparse
import json
import os
from pathlib import Path
import threading
import time
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--target',required=True)
    parser.add_argument('--message-id',required=True)
    parser.add_argument('--expected-transcript')
    args=parser.parse_args()
    os.environ['WHALE_DATA_ROOT']=str(Path(args.root).resolve())
    # Diagnostic-only reuse of the already installed UI dependency; production
    # speech and wxauto workers remain separate interpreters/environments.
    import site
    site.addsitedir(str(Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])/'.venv-wxauto/Lib/site-packages'))
    from wxauto_connection import connect
    from proctap import ProcessAudioCapture
    import numpy as np
    import soundfile as sf
    wx=connect()
    wx.ChatWith(args.target,exact=True)
    if wx.ChatInfo().get('chat_name')!=args.target or wx.ChatInfo().get('chat_type')!='friend':
        raise RuntimeError('target-mismatch')
    selected=[m for m in wx.GetAllMessage() if getattr(m,'type','')=='voice'
        and getattr(m,'attr','')=='self' and str(getattr(m,'id',''))==args.message_id]
    if not selected and args.expected_transcript:
        # UIA message identities are scoped to a rendered client incarnation.
        # A new interpreter must requalify the most recent *self* voice by its
        # exact already-known test transcript, rather than trust the old ID.
        voices=[m for m in wx.GetAllMessage() if getattr(m,'type','')=='voice' and getattr(m,'attr','')=='self']
        if voices and voices[-1].to_text().strip()==args.expected_transcript.strip():selected=[voices[-1]]
    if len(selected)!=1:raise RuntimeError('specified-self-voice-not-visible')
    data=[]
    lock=threading.Lock()
    def receive(pcm,count):
        with lock:
            if sum(len(block) for block in data)<48000*2*4*7:data.append(bytes(pcm))
    capture=ProcessAudioCapture(pid=int(wx._api.control.ProcessId),on_data=receive)
    try:
        capture.start()
        time.sleep(.2)
        selected[0].click()
        time.sleep(5)
    finally:capture.stop()
    with lock:samples=np.frombuffer(b''.join(data),dtype=np.float32)
    if len(samples)%2:samples=samples[:-1]
    if not len(samples):raise RuntimeError('native-playback-capture-empty')
    folder=Path(args.root)/'voice/captures'
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/('native-playback-'+uuid.uuid4().hex+'.wav')
    sf.write(str(path),samples.reshape(-1,2),48000)
    print(json.dumps({'captureFile':str(path),'source':'specified-wechat-self-voice-playback',
        'messageId':args.message_id,'capturedSeconds':round(len(samples)/2/48000,3),
        'peak':float(np.max(np.abs(samples))),'messagesSent':0},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
