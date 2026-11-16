"""Read-only probe: why connect() can't find exactly one WeChat main window.

Run with the plugin's own Python (the same one the worker uses):
  & "$env:USERPROFILE\\WhaleData\\runtime\\.venv-wxauto\\Scripts\\python.exe" -X utf8 setup\\wx-window-probe.py

It only lists windows and reads their UI Automation class names. It clicks
nothing, sends no keys, and writes nothing. Paste the output when asking for help.
"""
import ctypes
import ctypes.wintypes as wt

import psutil
from wxauto4 import uia
from wxauto4.utils import GetAllWindows

TITLES = ('微信', 'WeChat', 'Weixin')
user32 = ctypes.windll.user32
wechat = {p.pid: p.info['exe'] for p in psutil.process_iter(['name', 'exe'])
          if (p.info['name'] or '').lower() in ('weixin.exe', 'wechat.exe')}
print('WeChat processes:', len(wechat), sorted(set(v for v in wechat.values() if v)))


def pid_of(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def describe(hwnd):
    win32 = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, win32, 256)
    try:
        control = uia.ControlFromHandle(hwnd)
        kids = control.GetChildren()
        uia_class, uia_name = control.ClassName, control.Name
        kid_classes = [k.ClassName for k in kids[:6]]
    except Exception as error:
        uia_class, uia_name, kids, kid_classes = f'<{type(error).__name__}>', '', [], []
    return (f'hwnd={hwnd} visible={bool(user32.IsWindowVisible(hwnd))} win32={win32.value!r} '
            f'uia={uia_class!r} uiaName={uia_name!r} kids={len(kids)} {kid_classes}')


print('\n-- what connect() sees (wxauto4 GetAllWindows, title filter) --')
seen = 0
for hwnd, cls, title in GetAllWindows():
    if title in TITLES or pid_of(hwnd) in wechat:
        seen += 1
        print(f'title={title!r} passes_title_filter={title in TITLES} wechat_pid={pid_of(hwnd) in wechat} ' + describe(hwnd))
print('windows listed:', seen)

print('\n-- every top-level window owned by WeChat (EnumWindows) --')
rows = []
callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(
    lambda h, _: (rows.append(h) if pid_of(h) in wechat else None) or True)
user32.EnumWindows(callback, 0)
for hwnd in rows:
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    if title.value or user32.IsWindowVisible(hwnd):
        print(f'title={title.value!r} ' + describe(hwnd))

flag = ctypes.c_bool()
user32.SystemParametersInfoW(0x0046, 0, ctypes.byref(flag), 0)  # SPI_GETSCREENREADER
print('\nscreen-reader flag:', flag.value, '| this python is admin:', bool(ctypes.windll.shell32.IsUserAnAdmin()))
