"""Two-contact touch injection used for smooth (linear) pinch-zoom.

Apps see a real touchscreen pinch centred on the mouse cursor; legacy apps get Windows'
own fallback (zoom gesture -> ctrl+wheel). Coordinates are physical pixels, so the
process must be per-monitor DPI aware (t650d sets that at start-up).
"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)

PT_TOUCH = 2
POINTER_FLAG_INRANGE, POINTER_FLAG_INCONTACT = 0x2, 0x4
POINTER_FLAG_DOWN, POINTER_FLAG_UPDATE, POINTER_FLAG_UP = 0x10000, 0x20000, 0x40000
POINTER_FLAG_CANCELED = 0x8000
TOUCH_MASK_ALL = 0x7          # contact area | orientation | pressure
TOUCH_FEEDBACK_NONE = 0x3


class POINTER_INFO(ctypes.Structure):
    _fields_ = [("pointerType", wt.DWORD), ("pointerId", ctypes.c_uint32), ("frameId", ctypes.c_uint32),
                ("pointerFlags", ctypes.c_uint32), ("sourceDevice", wt.HANDLE), ("hwndTarget", wt.HWND),
                ("ptPixelLocation", wt.POINT), ("ptHimetricLocation", wt.POINT),
                ("ptPixelLocationRaw", wt.POINT), ("ptHimetricLocationRaw", wt.POINT),
                ("dwTime", wt.DWORD), ("historyCount", ctypes.c_uint32), ("InputData", ctypes.c_int32),
                ("dwKeyStates", wt.DWORD), ("PerformanceCount", ctypes.c_uint64), ("ButtonChangeType", ctypes.c_int)]


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [("pointerInfo", POINTER_INFO), ("touchFlags", ctypes.c_uint32), ("touchMask", ctypes.c_uint32),
                ("rcContact", wt.RECT), ("rcContactRaw", wt.RECT), ("orientation", ctypes.c_uint32),
                ("pressure", ctypes.c_uint32)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


user32.InjectTouchInput.argtypes = [ctypes.c_uint32, ctypes.POINTER(POINTER_TOUCH_INFO)]
user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
user32.MonitorFromPoint.restype = wt.HMONITOR
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]

_ready = None


def available():
    global _ready
    if _ready is None:
        _ready = bool(user32.InitializeTouchInjection(2, TOUCH_FEEDBACK_NONE))
    return _ready


class Pinch:
    """Two touch contacts placed horizontally around a fixed centre; set the span in px."""

    def __init__(self):
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        self.cx, self.cy = pt.x, pt.y
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(user32.MonitorFromPoint(pt, 2), ctypes.byref(mi))
        self.left, self.right = mi.rcMonitor.left + 2, mi.rcMonitor.right - 3
        self.cy = min(max(self.cy, mi.rcMonitor.top + 4), mi.rcMonitor.bottom - 5)
        self.span = None
        self.ok = True

    def _contacts(self, span, flags):
        half = span / 2
        xs = (max(self.left, int(round(self.cx - half))), min(self.right, int(round(self.cx + half))))
        arr = (POINTER_TOUCH_INFO * 2)()
        for i, x in enumerate(xs):
            t = arr[i]
            t.pointerInfo.pointerType = PT_TOUCH
            t.pointerInfo.pointerId = i
            t.pointerInfo.pointerFlags = flags
            t.pointerInfo.ptPixelLocation = wt.POINT(x, self.cy)
            t.touchMask = TOUCH_MASK_ALL
            t.rcContact = wt.RECT(x - 2, self.cy - 2, x + 2, self.cy + 2)
            t.orientation = 90
            t.pressure = 32000
        return arr

    def _inject(self, span, flags):
        if self.ok and not user32.InjectTouchInput(2, self._contacts(span, flags)):
            self.ok = False
        return self.ok

    def move(self, span):
        if self.span is None:
            self._inject(span, POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT)
            self.start_span = span
        else:
            self._inject(span, POINTER_FLAG_UPDATE | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT)
        self.span = span

    def end(self):
        if self.span is None:
            return
        flags = POINTER_FLAG_UP
        if abs(self.span - self.start_span) < 12:   # barely moved: cancel so it can't act as a tap
            flags |= POINTER_FLAG_CANCELED
        self._inject(self.span, flags)
        self.span = None
