"""Record virtual input during native WeChat recording, cancel without sending."""
import argparse
import json
import os
from pathlib import Path
import site
import threading
import time
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--target',required=True)
    parser.add_argument('--sample',required=True)
    args=parser.parse_args()
    os.environ['WHALE_DATA_ROOT']=str(Path(args.root).resolve())
    # Local diagnostic composition only; installed production workers stay split.
    site.addsitedir(str(Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])/'.venv-wxauto/Lib/site-packages'))
    from wxauto_connection import connect
    from social_wxauto_backend import running_client
    from native_voice import NativeVoice
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    wx=connect()
    wx.ChatWith(args.target,exact=True)
    if wx.ChatInfo().get('chat_name')!=args.target or wx.ChatInfo().get('chat_type')!='friend':
        raise RuntimeError('target-mismatch')
    client=running_client(wx)
    if client['clientVersion']!='4.2.0.12':raise RuntimeError('probe-client-version-unverified')
    config={'verified':True,'clientExe':client['executable'],'clientVersion':client['clientVersion'],
        'buttonName':'发语音 ( 按住右 Alt )','recordingLabels':['按下 退格键 (  ) 取消'],
        'outputDevice':'CABLE Input (VB-Audio Virtual Cable)','dataRoot':args.root}
    candidates=[i for i,d in enumerate(sd.query_devices()) if d['name']=='CABLE Output (VB-Audio Virtual Cable)'
        and d['max_input_channels']>=2 and 'WASAPI' in sd.query_hostapis(d['hostapi'])['name']]
    if len(candidates)!=1:raise RuntimeError('virtual-input-ambiguous-or-missing')
    blocks=[]
    timing=[]
    lock=threading.Lock()
    frames_seen=0
    def receive(data,frames,times,status):
        nonlocal frames_seen
        with lock:
            if frames_seen<48000*15:
                blocks.append(data.copy())
                frames_seen+=frames
                timing.append({'frames':frames,'adcTime':times.inputBufferAdcTime,
                    'currentTime':times.currentTime,'status':str(status)})
    before={str(m.id) for m in wx.GetAllMessage() if getattr(m,'type','')=='voice' and getattr(m,'attr','')=='self'}
    with sd.InputStream(device=candidates[0],samplerate=48000,channels=2,dtype='float32',callback=receive):
        result=NativeVoice(wx,config).probe(args.sample)
        time.sleep(.3)
    after={str(m.id) for m in wx.GetAllMessage() if getattr(m,'type','')=='voice' and getattr(m,'attr','')=='self'}
    if after-before:raise RuntimeError('probe-unexpected-new-voice-row')
    with lock:recorded=np.concatenate(blocks)
    folder=Path(args.root)/'voice/captures'
    folder.mkdir(parents=True,exist_ok=True)
    filename=folder/('native-input-probe-'+uuid.uuid4().hex+'.wav')
    sf.write(str(filename),recorded,48000)
    report={'captureFile':str(filename),'canceled':result['canceled'],'playback':result['playback'],
        'newVoiceRows':0,'sampleRate':48000,'channels':2,'capturedSeconds':round(len(recorded)/48000,3),
        'callbackStatuses':[x for x in timing if x['status']],'callbacks':timing}
    filename.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='callbacks'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
