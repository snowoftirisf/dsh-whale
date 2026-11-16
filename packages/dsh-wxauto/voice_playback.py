"""Play only into a named virtual cable, after parent confirms recording.

This helper never plays to the default speaker and never controls WeChat.
"""
import json
import sys
import threading
import time
from voice_runtime import audio_file


def main():
    import sounddevice as sd
    import soundfile as sf
    filename = audio_file(sys.argv[1])
    name = sys.argv[2]
    if 'cable input' not in name.lower():
        raise ValueError('virtual-cable-required')
    devices = [(i,d) for i,d in enumerate(sd.query_devices())
               if d['name']==name and d['max_output_channels']>=1]
    # Device names repeat across host APIs. Prefer WASAPI when present.
    wasapi = [pair for pair in devices if 'WASAPI' in sd.query_hostapis(pair[1]['hostapi'])['name']]
    selected = wasapi or devices
    if len(selected)!=1:
        raise ValueError('virtual-cable-ambiguous-or-missing')
    samples, sr = sf.read(str(filename),dtype='float32',always_2d=True)
    duration=len(samples)/sr
    if not 0.5<=duration<=55:
        raise ValueError('native-voice-segment-must-be-0.5-to-55-seconds')
    from voice_audio import output_format, stereo_waveform, PlaybackFeed
    # The actual cable's mono mapping produced periodic discontinuities. Its
    # stereo path reproduced the source in real loopback acceptance.
    samples=stereo_waveform(samples)
    samples, sr = output_format(samples, sr, selected[0][0], selected[0][1], sd)
    import numpy as np
    gain=min(1.0,.95/max(float(np.max(np.abs(samples))),1e-12))
    samples=samples*gain
    samples=np.concatenate([samples,np.zeros((int(sr*.3),2),dtype=np.float32)])
    feed=PlaybackFeed(samples)
    primed=threading.Event()
    finished=threading.Event()
    warnings=[]
    def render(output,frames,timing,status):
        # The callback always fills the device, including while the UI parent
        # is opening the recorder. Blocking on stdin cannot starve this stream.
        if status and feed.started and len(warnings)<10:warnings.append(str(status))
        complete=feed.fill(output)
        primed.set()
        if complete:raise sd.CallbackStop
    with sd.OutputStream(device=selected[0][0],samplerate=sr,channels=2,dtype='float32',
            latency='high',callback=render,finished_callback=finished.set):
        if not primed.wait(3):raise RuntimeError('virtual-playback-not-primed')
        time.sleep(.25)
        print(json.dumps({'ready':True,'durationSeconds':duration,'sampleRate':sr,'channels':2,'gain':gain}),flush=True)
        if sys.stdin.readline().strip()!='PLAY':
            raise ValueError('recording-not-confirmed')
        feed.started=True
        if not finished.wait(duration+4):raise RuntimeError('virtual-playback-timeout')
        if warnings:raise RuntimeError('virtual-playback-underflow')
    print(json.dumps({'played':True,'underflowed':False,'sampleRate':sr,'channels':2,
        'gain':gain,'continuousSilence':True}),flush=True)


if __name__=='__main__':
    main()
