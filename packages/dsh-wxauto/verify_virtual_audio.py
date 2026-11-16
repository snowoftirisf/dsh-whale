"""Local virtual-cable loopback acceptance; no WeChat control or default device."""
import argparse
import json
import os
from pathlib import Path
import time
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--sample',required=True)
    parser.add_argument('--stereo',action='store_true')
    parser.add_argument('--output-dtype',choices=('float32','int16'),default='float32')
    parser.add_argument('--gain',type=float,default=1.0)
    options=parser.parse_args()
    os.environ['WHALE_DATA_ROOT']=str(Path(options.root).resolve())
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    from voice_runtime import audio_file, Speech
    from voice_audio import output_format
    sample=audio_file(options.sample)
    if not sample.is_relative_to((Path(options.root)/'voice/outbox').resolve()):
        raise ValueError('sample-outbox-required')
    def endpoint(name,channel):
        candidates=[(i,d) for i,d in enumerate(sd.query_devices())
            if d['name']==name and d[channel]>=1 and 'WASAPI' in sd.query_hostapis(d['hostapi'])['name']]
        if len(candidates)!=1:raise RuntimeError('virtual-endpoint-ambiguous-or-missing')
        return candidates[0]
    output,device=endpoint('CABLE Input (VB-Audio Virtual Cable)','max_output_channels')
    incoming,_=endpoint('CABLE Output (VB-Audio Virtual Cable)','max_input_channels')
    samples,original_rate=sf.read(str(sample),dtype='float32',always_2d=True)
    duration=len(samples)/original_rate
    if not 0.5<=duration<=10:raise ValueError('short-test-sample-required')
    samples,rate=output_format(samples,original_rate,output,device,sd)
    if not 0 < options.gain <= 1:raise ValueError('invalid-test-gain')
    samples=samples*options.gain
    if options.stereo and samples.shape[1]==1:samples=np.repeat(samples,2,axis=1)
    if options.output_dtype=='int16':samples=np.clip(samples*32767,-32768,32767).astype(np.int16)
    blocks=[]
    statuses=[]
    def receive(data,frames,timing,status):
        if status:statuses.append(str(status))
        if sum(len(block) for block in blocks)<rate*12:blocks.append(data.copy())
    with sd.InputStream(device=incoming,samplerate=rate,channels=2,dtype='float32',callback=receive):
        time.sleep(0.2)
        with sd.OutputStream(device=output,samplerate=rate,channels=samples.shape[1],dtype=options.output_dtype) as stream:
            stream.write(samples)
        time.sleep(0.4)
    if not blocks:raise RuntimeError('virtual-capture-empty')
    recorded=np.concatenate(blocks)
    folder=Path(options.root)/'voice/captures'
    folder.mkdir(parents=True,exist_ok=True)
    captured=folder/('cable-acceptance-'+uuid.uuid4().hex+'.wav')
    sf.write(str(captured),recorded,rate)
    transcript=Speech().transcribe(str(captured))
    result={'source':'virtual-cable-loopback','sampleRate':rate,'originalSampleRate':original_rate,
        'outputChannels':samples.shape[1],'outputDtype':options.output_dtype,'gain':options.gain,
        'sampleSeconds':round(duration,3),'capturedSeconds':round(len(recorded)/rate,3),
        'peak':round(float(np.max(np.abs(recorded))),6),'captureFile':str(captured),
        'streamWarnings':statuses,'transcription':transcript,'defaultDevicesChanged':False,'wechatSent':False}
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
