"""Window metadata only; no contact list or transcript."""
import json
import win32gui
import win32process
import psutil

windows = []
def visit(handle, _):
    if not win32gui.IsWindowVisible(handle):
        return
    pid = win32process.GetWindowThreadProcessId(handle)[1]
    try:
        if psutil.Process(pid).name().lower() == 'weixin.exe':
            windows.append({'hwnd': handle, 'title': win32gui.GetWindowText(handle), 'class': win32gui.GetClassName(handle)})
    except psutil.Error:
        pass
win32gui.EnumWindows(visit, None)
print(json.dumps(windows, ensure_ascii=False))
