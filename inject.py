"""SendInput wrappers: relative mouse move, buttons, hi-res wheel, key chords."""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
KEYEVENTF_KEYUP, KEYEVENTF_EXTENDEDKEY = 0x0002, 0x0001
EXTRA = 0x7650  # tag on our injected events

VK = dict(ctrl=0x11, alt=0x12, shift=0x10, win=0x5B, tab=0x09, left=0x25, up=0x26, right=0x27, down=0x28,
          d=0x44, esc=0x1B)
_EXTENDED = {0x5B, 0x25, 0x26, 0x27, 0x28}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _mouse(flags, dx=0, dy=0, data=0):
    i = INPUT(type=0)
    i.u.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, EXTRA)
    return i


def _key(vk, up=False):
    i = INPUT(type=1)
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0)
    i.u.ki = KEYBDINPUT(vk, 0, flags, 0, EXTRA)
    return i


def move(dx, dy):
    if dx or dy:
        _send(_mouse(MOUSEEVENTF_MOVE, dx, dy))


def move_raw(dx, dy):
    """Like move() but also sends a zero move (used to count as 'last input' for focus rules)."""
    _send(_mouse(MOUSEEVENTF_MOVE, dx, dy))


_BTN = {"left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP)}


def button(name, down):
    _send(_mouse(_BTN[name][0 if down else 1]))


def click(name):
    d, u = _BTN[name]
    _send(_mouse(d), _mouse(u))


def wheel(v=0, h=0):
    """v>0 scrolls up, h>0 scrolls right; units of 1/120 notch (hi-res)."""
    ev = []
    if v:
        ev.append(_mouse(MOUSEEVENTF_WHEEL, data=v))
    if h:
        ev.append(_mouse(MOUSEEVENTF_HWHEEL, data=h))
    if ev:
        _send(*ev)


def key(name, down):
    _send(_key(VK[name], not down))


def chord(*names):
    """Press names in order, release in reverse."""
    vks = [VK[n] for n in names]
    _send(*[_key(v) for v in vks], *[_key(v, True) for v in reversed(vks)])
