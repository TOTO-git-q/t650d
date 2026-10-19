"""Log every mouse button event system-wide for N seconds, marking injected vs hardware.

  python mousehook.py 60 clicks.log

Diagnostics only. A low-level mouse hook in Python delays EVERY mouse event on the
system while it runs (pointer and scrolling feel laggy), so run it briefly and stop it.
Use it to tell a firmware click (HARDWARE) from one injected by t650d (extra=0x7650).
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


NAMES = {0x201: "L down", 0x202: "L up", 0x204: "R down", 0x205: "R up", 0x207: "M down", 0x208: "M up"}
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
out = open(sys.argv[2] if len(sys.argv) > 2 else "mousehook.log", "w", encoding="utf-8", buffering=1)
t0 = time.perf_counter()


@HOOKPROC
def proc(code, wparam, lparam):
    if code == 0 and wparam in NAMES:
        m = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        src = "t650d" if m.dwExtraInfo == 0x7650 else ("injected-other" if m.flags & 1 else "HARDWARE")
        out.write(f"{time.perf_counter() - t0:8.3f} {NAMES[wparam]:7s} {src} extra={m.dwExtraInfo:#x}\n")
    return user32.CallNextHookEx(None, code, wparam, lparam)


hook = user32.SetWindowsHookExW(14, proc, None, 0)
end = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 120)
msg = wt.MSG()
while time.time() < end:
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    time.sleep(0.005)
user32.UnhookWindowsHookEx(hook)
out.write("done\n")
