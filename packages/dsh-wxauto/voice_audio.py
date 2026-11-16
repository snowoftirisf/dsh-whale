"""Shared duration-limited waveform segmentation for speech and future singing."""
import numpy as np


def stereo_waveform(samples):
    samples=np.asarray(samples)
    if samples.ndim==1:samples=samples.reshape(-1,1)
    if samples.ndim!=2 or samples.shape[1] not in (1,2):
        raise ValueError('voice-stereo-source-required')
    return np.repeat(samples,2,axis=1) if samples.shape[1]==1 else samples


class PlaybackFeed:
    """Feed continuous silence before PLAY, then every waveform sample once."""
    def __init__(self,samples):
        self.samples=np.ascontiguousarray(samples,dtype=np.float32)
        self.started=False
        self.position=0

    def fill(self,output):
        output.fill(0)
        if not self.started:return False
        count=min(len(output),len(self.samples)-self.position)
        output[:count]=self.samples[self.position:self.position+count]
        self.position+=count
        return self.position==len(self.samples)


def output_format(samples, sample_rate, device_index, device, audio_backend):
    """Match a virtual endpoint's supported rate without changing voice pitch."""
    channels = samples.shape[1] if samples.ndim > 1 else 1
    try:
        audio_backend.check_output_settings(device=device_index, samplerate=sample_rate,
            channels=channels, dtype='float32')
        return samples, sample_rate
    except audio_backend.PortAudioError:
        target_rate = int(device['default_samplerate'])
        audio_backend.check_output_settings(device=device_index, samplerate=target_rate,
            channels=channels, dtype='float32')
        from math import gcd
        from scipy.signal import resample_poly
        factor = gcd(sample_rate, target_rate)
        converted = resample_poly(samples, target_rate // factor, sample_rate // factor, axis=0)
        return np.ascontiguousarray(converted, dtype=np.float32), target_rate


def segments(samples, sample_rate, max_seconds=55):
    samples=np.asarray(samples)
    limit=int(sample_rate*max_seconds)
    minimum=max(1,int(sample_rate*0.5))
    start=0
    while len(samples)-start>limit:
        end=start+limit
        if len(samples)-end<minimum:
            end=len(samples)-minimum
        else:
            # Prefer a quiet phrase boundary within the last two seconds.
            window=max(1,int(sample_rate*0.02))
            positions=range(max(start+minimum,end-2*sample_rate),end-window+1,window)
            end=min(positions,key=lambda at:float(np.mean(np.square(samples[at:at+window]))),default=end)
        yield samples[start:end]
        start=end
    if len(samples)>start:yield samples[start:]
