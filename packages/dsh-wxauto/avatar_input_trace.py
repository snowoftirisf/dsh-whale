"""Time-limited local input diagnostics; never dispatch an additional action.

The owned worker is single-threaded for UI operations. Wrappers call the exact
original once, preserve return values/exceptions, and only observe matching chat
controls. No Name, message body, full paths, screenshot or clipboard is logged.
"""
from functools import wraps
import hashlib
import json
from pathlib import Path
import time
import traceback


def install_avatar_input_trace(uia, root, observe, *, expires_at, clock=time.time, menu_probe=None):
    if (type(expires_at) not in (int,float) or not 0 < expires_at-clock() <= 900
            or not callable(observe)):
        raise ValueError('avatar-trace-expiry-invalid')
    originals={}
    pid=root.ProcessId
    cls=uia.Control
    allowed={'mmui::ChatTextItemView','mmui::ChatVoiceItemView',
             'mmui::ChatVoiceMessageItemView','mmui::ChatInputField',
             'mmui::ChatBubbleItemView','mmui::ChatBubbleReferItemView',
             'mmui::ChatImageItemView','mmui::ChatFileItemView'}

    def facts(control,method,kwargs):
        if clock()>=expires_at or control.ProcessId!=pid or control.ClassName not in allowed:
            return None
        rid=control.GetRuntimeId();r=control.BoundingRectangle
        frames=traceback.extract_stack(limit=18)[:-2]
        value={'at':clock(),'method':method,'class':control.ClassName,
            'runtimeIdSha256':hashlib.sha256(json.dumps(list(rid)).encode()).hexdigest(),
            'rect':[r.left,r.top,r.right,r.bottom],
            'parameters':{k:v for k,v in kwargs.items() if k in ('x','y','pos','ratioX','ratioY','move','show_window')
                          and type(v) in (str,int,float,bool)},
            'stack':[{'function':f.name,'module':Path(f.filename).name,'line':f.lineno} for f in frames]}
        return value

    for method in ('Click','RightClick','DoubleClick'):
        if not hasattr(cls,method):continue
        original=getattr(cls,method)
        def make(original,method):
            @wraps(original)
            def wrapped(control,*args,**kwargs):
                try:value=facts(control,method,kwargs)
                except Exception:value=None
                # Publish the attempt before execution: a throwing input method
                # can already have dispatched its click. Observation never retries.
                if value is not None:
                    try:observe(value)
                    except Exception:pass
                try:
                    return original(control,*args,**kwargs)
                finally:
                    if value is not None and method=='RightClick' and callable(menu_probe):
                        # Observe the actual outcome of this existing SDK click;
                        # never open, close, focus or retry a menu for diagnosis.
                        try:
                            observation=menu_probe(root,uia)
                            observe({**value,'phase':'after-input','menuObservation':observation})
                        except Exception:
                            pass
            return wrapped
        wrapped=make(original,method)
        originals[method]=(original,wrapped)
        setattr(cls,method,wrapped)

    def restore():
        for method,(original,wrapped) in originals.items():
            if getattr(cls,method) is wrapped:setattr(cls,method,original)
    return restore


def install_configured_trace(uia,root,observe):
    """Absent/expired local diagnostics leave all SDK methods untouched."""
    file=Path(__file__).with_name('avatar-trace.local.json')
    if not file.exists():return False
    value=json.loads(file.read_text('utf-8-sig'))
    if value.get('enabled') is not True:return False
    expiry=value.get('expiresAtEpoch')
    if type(expiry) not in (int,float) or expiry<=time.time():return False
    from native_voice_menus import observe_avatar_context_menu
    install_avatar_input_trace(uia,root,observe,expires_at=expiry,menu_probe=observe_avatar_context_menu)
    return True
