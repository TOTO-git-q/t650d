"""t650d - lightweight user-mode driver for the Logitech T650 touchpad (replaces SetPoint).

Puts the pad into HID++ raw-touch mode (feature 0x6100) and does pointer, taps,
physical click, smooth hi-res two-finger scrolling with inertia, pinch-zoom and
three/four-finger swipes itself, injecting input with SendInput.

  pythonw t650d.py            run (background)
  python  t650d.py --debug    run with console log of every frame
  python  t650d.py --stop     stop the running instance (pad returns to native mode)
  python  t650d.py --native   just put the pad back into native (firmware) mode
"""
import ctypes
import json
import logging
import os
import queue
import sys
import threading
import time

from engine import Engine
from hidpp import Receiver

HERE = os.path.dirname(os.path.abspath(__file__))
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.OpenEventW.restype = ctypes.c_void_p
STOP_EVENT = "Local\\t650d_stop"

DEFAULTS = {
    # pointer: px per mm of finger travel; precision zone below precision_speed (mm/s),
    # then a smooth ramp slow -> fast between pointer_speed_slow and pointer_speed_fast
    "pointer_gain_slow": 7.0,
    "pointer_gain_fast": 22.0,
    "pointer_speed_slow": 25.0,
    "pointer_speed_fast": 300.0,
    "precision_speed": 8.0,
    "precision_gain": 0.6,
    "velocity_window": 0.05,      # s, speed is measured over this window, not one frame
    # scrolling: wheel units (120 = 1 notch) per mm, linear unless scroll_accel > 0
    "scroll_gain": 45.0,
    "scroll_accel": 0.0,          # extra gain per 100 mm/s above 40 mm/s (0 = strictly linear)
    "natural_scroll": None,       # None = follow Windows precision-touchpad setting
    "scroll_start_mm": 0.6,
    "axis_lock": True,
    "unlock_mm": 6.0,             # cross-axis travel that releases the axis lock
    "scroll_smooth_s": 0.010,     # output pacer time constant (0 = off)
    "inertia": True,
    "inertia_tau": 0.38,          # s, momentum decay time constant
    "inertia_min_speed": 250.0,   # wheel units/s needed to start momentum
    "inertia_stop_speed": 40.0,
    "inertia_max_speed": 12000.0,
    "momentum_window": 0.07,      # s of motion used to measure release speed
    "momentum_max_pause": 0.06,   # s still before lift -> no momentum
    "fling_boost": 0.8,           # share of stopped momentum added by a quick re-flick
    # pinch to zoom: "touch" = injected touchscreen pinch (linear, smooth), "wheel" = ctrl+wheel
    "pinch_zoom": True,
    "pinch_mode": "touch",
    "pinch_start_mm": 2.5,
    "pinch_span": 300,            # px between the injected contacts at pinch start
    "pinch_min_span": 40,
    "pinch_gain": 1.0,            # zoom ratio = (finger distance ratio) ** pinch_gain
    "pinch_wheel_gain": 17.0,     # wheel mode: wheel units per mm of distance change
    # taps
    "tap_click": None,            # None = follow Windows setting
    "tap_time": 0.18,
    "tap_move_mm": 1.5,
    "tap_drag": None,             # None = follow Windows setting
    "tap_drag_window": 0.22,
    # physical press: "driver" = we send the click (1/2/3 fingers = left/right/middle,
    # lower-right zone = right). "hardware" only for when Logitech's own receiver driver
    # (installed with SetPoint) forwards a firmware click too - then ours would double it.
    "physical_click": "driver",
    # driver-mode physical click: lower-right zone becomes right click (fractions of pad size)
    "right_zone_x": 0.70,
    "right_zone_y": 0.78,
    "click_freeze": 0.07,         # s pointer is frozen after press/release
    "click_undo_s": 0.05,         # pointer motion this recent before a press is undone ...
    "click_undo_px": 8,           # ... if it is at most this many pixels
    # thumb resting in the bottom zone is ignored
    "thumb_zone": 0.15,
    "thumb_land_gap": 0.15,
    "thumb_max_travel_mm": 2.0,
    "thumb_release_mm": 6.0,
    # swipes (3 fingers: up task view, down desktop, left/right alt-tab)
    "swipe_mm": 22.0,
    # 4-finger swipes: bring app to front / minimise if already in front / launch if closed
    # e.g. {"exe": "notepad.exe", "launch": "notepad.exe"}; see config.example.json
    "swipe4_up": None,
    "swipe4_down": None,
    "swipe4_left": None,
    "swipe4_right": None,
    "alt_tab_step_mm": 18.0,
}


def windows_ptp_settings():
    out = {}
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\PrecisionTouchPad")
        def get(name, default):
            try:
                return winreg.QueryValueEx(k, name)[0]
            except OSError:
                return default
        out["natural_scroll"] = get("ScrollDirection", 0) == 0  # 0 = "down motion scrolls up"
        out["tap_click"] = bool(get("TapsEnabled", 1))
        out["tap_drag"] = bool(get("TapAndDrag", 1))
    except OSError:
        pass
    return out


def load_config():
    cfg = dict(DEFAULTS)
    path = os.path.join(HERE, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    win = windows_ptp_settings()
    for k in ("natural_scroll", "tap_click", "tap_drag"):
        if cfg[k] is None:
            cfg[k] = win.get(k, True)
    return cfg


# ---------------------------------------------------------------------------
class Pad:
    """T650 on a Unifying receiver: raw mode control + frame assembly."""
    RAW_ON = 0x05   # raw reports + enhanced sensitivity
    RAW_OFF = 0x00  # firmware (native mouse) mode

    def __init__(self, on_frame, log):
        self.log = log
        self.on_frame = on_frame
        self.rx = None          # opened by start()/reopen(); None while no receiver is present
        self._rx_missing = False
        self.idx = self.fi = None
        self.xsize, self.ysize, self.flip_y, self.units_per_mm = 2832, 2364, True, 23.6
        self.connected = False
        self._pending = {}
        self._extra = []
        self.button = False
        self.last_report = 0.0
        self.reenable = threading.Event()

    def start(self):
        return self.reopen()

    def lost(self):
        return self.rx is None or self.rx.io_error

    def reopen(self):
        """Handles died (receiver re-plugged, driver stack rebuilt): open the receiver afresh."""
        try:
            if self.rx:
                self.rx.close()
        except OSError:
            pass
        self.rx = None
        try:
            self.rx = Receiver()
        except (RuntimeError, OSError) as e:
            if not self._rx_missing:      # log once, not every 2 s retry
                self.log.info("receiver not available yet: %s", e)
            self._rx_missing = True
            return False
        self._rx_missing = False
        self.rx.start(self._on_report)
        self.idx = self.fi = None
        self.connected = False
        self.log.info("receiver reopened")
        return True

    def connect(self):
        """Find the pad (if needed) and switch it to raw mode. False while it is asleep/off."""
        try:
            if self.fi is None:
                idx, name = self.rx.find_device("T650")
                if not idx:
                    return False
                fi = self.rx.feature_index(idx, 0x6100)
                info = self.rx.request(idx, fi, 0)
                self.xsize = int.from_bytes(info[0:2], "big")
                self.ysize = int.from_bytes(info[2:4], "big")
                self.flip_y = info[8] == 1
                self.units_per_mm = int.from_bytes(info[13:15], "big") / 25.4 or 23.6
                self.idx, self.fi = idx, fi
                self.log.info("found %s idx=%d feat=%d size=%dx%d %.1f u/mm", name, idx, fi,
                              self.xsize, self.ysize, self.units_per_mm)
        except (IOError, TimeoutError) as e:
            self.log.info("pad not reachable yet: %s", e)
            return False
        self.connected = self.set_raw(True)
        return self.connected

    def set_raw(self, on):
        for _ in range(3):
            try:
                self.rx.request(self.idx, self.fi, 2, bytes([self.RAW_ON if on else self.RAW_OFF]))
                self.log.info("raw mode %s", "on" if on else "off")
                return True
            except (IOError, TimeoutError) as e:
                self.log.warning("set_raw(%s) failed: %s", on, e)
                time.sleep(0.3)
        return False

    def _on_report(self, r):
        if r[0] == 0x10 and r[2] == 0x41 and (self.idx is None or r[1] == self.idx):
            up = not (r[4] & 0x40)          # receiver: device (dis)connection notification
            self.log.info("device %d link %s", r[1], "up" if up else "down")
            if up:
                self.reenable.set()
            else:
                self.connected = False
            return
        if r[1] != self.idx:
            return
        if r[0] != 0x11 or r[2] != self.fi:
            return
        p = r[4:20]
        self.last_report = time.monotonic()
        eof = p[8] & 1
        button = bool((p[8] >> 2) & 1)
        count = p[15] & 0x0F
        if count:
            for off in (2, 9):
                d = p[off:off + 7]
                fid = d[6] >> 4
                if not fid or (d[0] >> 6):
                    continue
                x = ((d[0] & 0x3F) << 8) | d[1]
                y = ((d[2] & 0x3F) << 8) | d[3]
                if self.flip_y:
                    y = self.ysize - y
                self._pending[fid] = (x, y) if (d[2] >> 6) else None
                self._extra.append(f"{fid}:z{d[4]}a{d[5]}")
        if eof or count <= 2:
            # contacts not reported in a frame count as lifted (like Linux INPUT_MT_DROP_UNUSED)
            contacts = {k: v for k, v in self._pending.items() if v is not None}
            self._pending = {}
            if self.log.isEnabledFor(logging.DEBUG):
                self.log.debug("raw ts=%d %s", int.from_bytes(p[0:2], "big"), " ".join(self._extra))
            self._extra = []
            self.button = button
            self.on_frame(time.monotonic(), contacts, button)

    def close(self):
        if not self.rx:
            return
        try:
            if self.connected:
                self.set_raw(False)
        finally:
            self.rx.close()


# ---------------------------------------------------------------------------
def setpoint_running():
    import subprocess
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq SetPoint.exe", "/NH"], capture_output=True, text=True,
                         creationflags=0x08000000).stdout
    return "SetPoint.exe" in out


def main():
    args = sys.argv[1:]
    if "--stop" in args:
        h = kernel32.OpenEventW(0x0002, False, STOP_EVENT)
        if not h:
            print("t650d is not running")
            return
        kernel32.SetEvent(ctypes.c_void_p(h))
        print("stop signalled")
        return
    debug = "--debug" in args
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)] if debug and sys.stdout else
                        [logging.FileHandler(os.path.join(HERE, "t650d.log"), encoding="utf-8")])
    log = logging.getLogger("t650d")

    if "--native" in args:
        pad = Pad(lambda *a: None, log)
        if pad.start() and pad.connect():
            pad.close()
        else:
            pad.close()
            print("pad is asleep/off; it resets to native mode by itself when it reconnects")
            return
        print("pad back in native mode")
        return

    mutex = kernel32.CreateMutexW(None, False, "Local\\t650d_instance")
    if ctypes.get_last_error() == 183:
        log.error("already running")
        return
    if setpoint_running() and "--force" not in args:
        log.error("SetPoint.exe is running; quit it first (or pass --force)")
        print("SetPoint.exe is running; quit it first (or pass --force)")
        return
    stop = kernel32.CreateEventW(None, True, False, STOP_EVENT)

    ctypes.WinDLL("user32").SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # physical px for touch
    ctypes.WinDLL("winmm").timeBeginPeriod(1)          # 1 ms timer: smooth momentum/pacer ticks
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x8000)  # ABOVE_NORMAL
    cfg = load_config()
    log.info("config %s", cfg)
    frames = queue.Queue()
    pad = Pad(lambda t, c, b: frames.put((t, c, b)), log)
    if pad.start():
        pad.connect()
    eng = Engine(cfg, pad.units_per_mm, pad.xsize, pad.ysize, log, debug)
    last_check = last_try = time.monotonic()
    try:
        while kernel32.WaitForSingleObject(ctypes.c_void_p(stop), 0) != 0:
            try:
                t, cur, btn = frames.get(timeout=0.004 if eng.active() else 0.1)
                eng.frame(t, cur, btn)
            except queue.Empty:
                now = time.monotonic()
                # finger data went stale (pad slept / report lost): treat as lift
                if eng.fingers and now - pad.last_report > 0.25:
                    eng.frame(now, {}, False)
            eng.tick(time.monotonic())
            now = time.monotonic()
            # (re)connect: on the receiver's link-up notification, or poll every 10 s while away
            if pad.lost() and now - last_try > 2:
                last_try = now
                eng.reset()
                if pad.reopen():
                    pad.reenable.set()
            if pad.reenable.is_set() or (not pad.connected and now - last_try > 10):
                pad.reenable.clear()
                last_try = now
                eng.reset()
                time.sleep(0.2)
                if not pad.lost() and pad.connect():
                    eng.upmm, eng.xsize, eng.ysize = pad.units_per_mm, pad.xsize, pad.ysize
            if pad.connected and now - last_check > 30 and not eng.fingers:
                last_check = now
                try:  # re-assert raw mode if the pad reset itself
                    st = pad.rx.request(pad.idx, pad.fi, 1)[0]
                    if not st & 1:
                        log.info("raw mode was lost (state %02x), re-enabling", st)
                        pad.set_raw(True)
                except (IOError, TimeoutError):
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        eng.reset()
        pad.close()
        log.info("stopped")


if __name__ == "__main__":
    main()
