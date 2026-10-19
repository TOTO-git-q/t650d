"""Bring an app's main window to the front (restore if minimised, launch if not running).
If it is already the foreground window, minimise it instead (so one gesture toggles it)."""
import ctypes
import ctypes.wintypes as wt
import os
import threading

import inject

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindow.restype = wt.HWND
user32.GetForegroundWindow.restype = wt.HWND
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
SW_RESTORE, SW_MINIMIZE = 9, 6
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _exe_of(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def find_window(exe):
    """Largest visible, titled, unowned top-level window of the process image `exe`."""
    exe = exe.lower()
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        if _exe_of(hwnd) == exe:
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            found.append(((r.right - r.left) * (r.bottom - r.top), hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return max(found)[1] if found else None


def _to_front(hwnd):
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows only lets the process that produced the last input take the foreground;
    # a zero-length injected mouse move makes that us.
    inject.move_raw(0, 0)
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() != hwnd:      # fallback: borrow the foreground thread's input
        fg = user32.GetForegroundWindow()
        me = kernel32.GetCurrentThreadId()
        other = user32.GetWindowThreadProcessId(fg, None)
        user32.AttachThreadInput(me, other, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(me, other, False)
    return user32.GetForegroundWindow() == hwnd


def toggle(spec, log=None):
    """spec = {"exe": "claude.exe", "launch": "shell:AppsFolder\\..."}; runs off the input thread."""
    def work():
        try:
            hwnd = find_window(spec["exe"])
            if hwnd and user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_MINIMIZE)
            elif hwnd:
                ok = _to_front(hwnd)
                if log and not ok:
                    log.warning("could not bring %s to front", spec["exe"])
            elif spec.get("launch"):
                os.startfile(spec["launch"])
        except Exception as e:  # never let a gesture action kill the driver
            if log:
                log.warning("app toggle %s failed: %s", spec, e)
    threading.Thread(target=work, daemon=True).start()
