"""HID++ 2.0 over a Logitech Unifying receiver, on top of winhid.

Two usage modes:
  * synchronous (probe scripts): request() reads the HID handles itself;
  * threaded (driver): start(on_report) spawns reader threads that route replies
    to request() and hand every other report to on_report(report_bytes).
"""
import queue
import threading
import time
import winhid

LOGI_VID = 0x046D
SWID = 0x0A  # our software id (low nibble of byte 3)


class Receiver:
    def __init__(self, pid=None):
        short = long_ = None
        for d in winhid.enumerate_hid():
            if d["vid"] != LOGI_VID or (pid and d["pid"] != pid) or d["usage_page"] != 0xFF00:
                continue
            if d["usage"] == 1 and short is None:
                short = d
            elif d["usage"] == 2 and long_ is None:
                long_ = d
        if not long_:
            raise RuntimeError("no Logitech HID++ long-report collection found")
        self.pid = long_["pid"]
        self.long = winhid.HidDevice(long_["path"], long_["in_len"], long_["out_len"])
        self.short = winhid.HidDevice(short["path"], short["in_len"], short["out_len"]) if short else None
        self._threads = []
        self._replies = None
        self._req_lock = threading.Lock()
        self._stop = threading.Event()
        self.io_error = False   # a handle went bad (receiver unplugged / driver stack rebuilt)

    # ---- threaded mode -------------------------------------------------
    def start(self, on_report):
        self._replies = queue.Queue()
        for dev in (self.long, self.short):
            if dev is None:
                continue
            t = threading.Thread(target=self._reader, args=(dev, on_report), daemon=True)
            t.start()
            self._threads.append(t)

    def _reader(self, dev, on_report):
        while not self._stop.is_set():
            try:
                r = dev.read(200)
            except OSError:
                self.io_error = True
                time.sleep(0.5)
                continue
            if not r:
                continue
            if self._is_reply(r):
                self._replies.put(r)
            else:
                on_report(r)

    @staticmethod
    def _is_reply(r):
        if r[0] == 0x10 and r[2] == 0x8F:
            return True
        if r[0] == 0x11 and r[2] == 0xFF:
            return True
        return r[0] == 0x11 and (r[3] & 0x0F) == SWID

    def _next_report(self, timeout_ms):
        if self._replies is not None:
            try:
                return self._replies.get(timeout=timeout_ms / 1000)
            except queue.Empty:
                return None
        for dev in (self.long, self.short):
            if dev is None:
                continue
            r = dev.read(timeout_ms)
            if r:
                return r
        return None

    # ---- requests ------------------------------------------------------
    def request(self, devidx, feat_idx, func, params=b"", timeout=0.6):
        """HID++ 2.0 request; returns response params (16 bytes) or raises."""
        with self._req_lock:
            if self._replies is not None:
                while not self._replies.empty():
                    self._replies.get_nowait()
            msg = bytes([0x11, devidx, feat_idx, (func << 4) | SWID]) + bytes(params)
            try:
                self.long.write(msg.ljust(20, b"\0"))
            except OSError:
                self.io_error = True
                raise
            end = time.time() + timeout
            while time.time() < end:
                r = self._next_report(15)
                if not r or r[1] != devidx:
                    continue
                if r[0] == 0x10 and r[2] == 0x8F:  # HID++ 1.0 error (device unreachable etc.)
                    raise IOError(f"hidpp1 error 0x{r[5]:02x} dev {devidx}")
                if r[0] == 0x11 and r[2] == 0xFF and r[3] == feat_idx:
                    raise IOError(f"hidpp2 error 0x{r[5]:02x}")
                if r[0] == 0x11 and r[2] == feat_idx and r[3] == ((func << 4) | SWID):
                    return r[4:20]
            raise TimeoutError(f"no reply dev {devidx} feat {feat_idx} func {func}")

    def feature_index(self, devidx, feature_id):
        r = self.request(devidx, 0, 0, bytes([feature_id >> 8, feature_id & 0xFF]))
        return r[0] or None

    def protocol(self, devidx):
        r = self.request(devidx, 0, 1, b"\0\0\x5a")
        return r[0], r[1]

    def device_name(self, devidx):
        fi = self.feature_index(devidx, 0x0005)
        if not fi:
            return None
        n = self.request(devidx, fi, 0)[0]
        name = b""
        while len(name) < n:
            name += self.request(devidx, fi, 1, bytes([len(name)]))
        return name[:n].decode(errors="replace")

    def find_device(self, name_part):
        for idx in range(1, 7):
            name = None
            for _ in range(3):  # first request after idle often times out while the device wakes
                try:
                    self.protocol(idx)
                    name = self.device_name(idx) or ""
                    break
                except TimeoutError:
                    continue
                except IOError:
                    break
            if name is None:
                continue
            if name_part.lower() in name.lower():
                return idx, name
        return None, None

    def close(self):
        self._stop.set()
        for t in self._threads:
            t.join(0.5)
        self.long.close()
        if self.short:
            self.short.close()
