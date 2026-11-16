"""Fail-closed adapter for installed proctap 1.1.x. Import/constructor are inert.

PID allowlisting remains the caller's responsibility. This additionally proves
the backend did NOT silently fall back to system-wide audio. It has no alternate
capture implementation and only stops the exact capture it created.
"""
import threading

PROCESS_AUDIO_SCOPE_VERSION = 1
PROCESS_AUDIO_NORMALIZATION_VERSION = 1


class DedicatedProcessAudio:
    def __init__(self, pid, on_data, *, capture_factory=None):
        if type(pid) is not int or pid<=0 or not callable(on_data) or (capture_factory is not None and not callable(capture_factory)):
            raise RuntimeError('dedicated-process-audio-config-invalid')
        self.pid=pid;self._consumer=on_data;self._factory=capture_factory
        self._capture=None;self._verified=False;self._fault=None
        self._started=False;self._closed=False;self._stop_attempted=False
        self._lock=threading.RLock()

    @property
    def scope_verified(self):
        with self._lock:return self._verified is True and self._fault is None and not self._closed

    @property
    def scope_version(self):
        return PROCESS_AUDIO_SCOPE_VERSION

    @property
    def normalization_version(self):
        return PROCESS_AUDIO_NORMALIZATION_VERSION

    def _set_fault(self,code):
        self._verified=False
        if self._fault is None:self._fault=code

    def _inspect(self):
        capture=self._capture
        try:
            if type(capture.pid) is not int or capture.pid!=self.pid:
                raise RuntimeError('dedicated-process-audio-pid-mismatch')
            native=capture._backend._native
            test=native.is_process_specific
            if not callable(test):raise ValueError()
            result=test()
            if type(result) is not bool or result is not True:raise ValueError()
        except RuntimeError as error:
            if str(error)=='dedicated-process-audio-pid-mismatch':raise
            raise RuntimeError('dedicated-process-audio-scope-unverified') from None
        except Exception:
            raise RuntimeError('dedicated-process-audio-scope-unverified') from None
        try:
            fmt=capture.format
            if (not isinstance(fmt,dict) or any(type(fmt.get(k)) is not int for k in ('sample_rate','channels','bits_per_sample'))
                    or (fmt['sample_rate'],fmt['channels'],fmt['bits_per_sample'],fmt.get('sample_format'))!=(48000,2,32,'float32')):
                raise ValueError()
        except Exception:raise RuntimeError('dedicated-process-audio-format-unverified') from None

    def start(self):
        with self._lock:
            if self._fault:raise RuntimeError(self._fault)
            if self._closed:raise RuntimeError('dedicated-process-audio-closed')
            if self._started:return self.check()
            attempted=False
            try:
                factory=self._factory
                if factory is None:
                    from proctap import ProcessAudioCapture
                    factory=ProcessAudioCapture
                self._capture=factory(pid=self.pid,on_data=self._on_data)
                self._inspect()  # No start at all for fallback/unknown formats.
                attempted=True
                self._capture.start()
                self._inspect()  # No callback is authorized before both checks.
                self._started=True;self._verified=True
            except Exception as error:
                code=str(error) if isinstance(error,RuntimeError) and str(error).startswith('dedicated-process-audio-') else 'dedicated-process-audio-start-failed'
                self._set_fault(code)
                if attempted:
                    try:self.stop()
                    except RuntimeError:pass
                raise RuntimeError(self._fault) from None
        return True

    def _on_data(self,pcm,count):
        with self._lock:
            if not self._verified or self._fault or self._closed:return
            try:
                self._inspect()
            except RuntimeError as error:
                self._set_fault(str(error));return
            try:
                if not isinstance(pcm,(bytes,bytearray,memoryview)) or type(count) is not int:raise ValueError()
                data=bytes(pcm)
                if not data or len(data)%8:raise ValueError()
                frames=len(data)//8
                # Installed high-level SDK always supplies the documented TODO
                # sentinel -1. Derive exact frames from its standard PCM bytes.
                if count!=-1 and (count<=0 or count!=frames):raise ValueError()
            except Exception:
                self._set_fault('dedicated-process-audio-pcm-invalid');return
            try:self._consumer(data,frames)
            except Exception:self._set_fault('dedicated-process-audio-consumer-failed')

    def check(self):
        with self._lock:
            if self._fault:raise RuntimeError(self._fault)
            if self._closed:raise RuntimeError('dedicated-process-audio-closed')
            if not self._started or not self._verified:raise RuntimeError('dedicated-process-audio-not-started')
            try:self._inspect()
            except RuntimeError as error:
                self._set_fault(str(error));raise RuntimeError(self._fault) from None
            return True

    def stop(self):
        with self._lock:
            self._verified=False;self._closed=True
            if self._stop_attempted:return
            self._stop_attempted=True;capture=self._capture
        if capture is not None:
            try:capture.stop()
            except Exception:
                with self._lock:self._set_fault('dedicated-process-audio-stop-failed')
                raise RuntimeError('dedicated-process-audio-stop-failed') from None
