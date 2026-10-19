"""Minimal Windows HID access via ctypes (no hidapi dependency)."""
import ctypes
import ctypes.wintypes as wt

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID), ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT), ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
                ("InputReportByteLength", wt.USHORT), ("OutputReportByteLength", wt.USHORT),
                ("FeatureReportByteLength", wt.USHORT), ("Reserved", wt.USHORT * 17),
                ("rest", wt.USHORT * 10)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wt.DWORD), ("OffsetHigh", wt.DWORD), ("hEvent", wt.HANDLE)]


DIGCF_PRESENT = 0x2
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
ERROR_IO_PENDING = 997
WAIT_TIMEOUT = 0x102

setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                      ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                                      ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(wt.DWORD), wt.BOOL]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.CancelIo.argtypes = [wt.HANDLE]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.ResetEvent.argtypes = [wt.HANDLE]
hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]


def _hid_guid():
    g = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(g))
    return g


def _open(path, access):
    return kernel32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING,
                                FILE_FLAG_OVERLAPPED, None)


def enumerate_hid():
    """Yield dicts: path, vid, pid, usage_page, usage, in_len, out_len."""
    guid = _hid_guid()
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    try:
        i = 0
        while True:
            ifd = SP_DEVICE_INTERFACE_DATA()
            ifd.cbSize = ctypes.sizeof(ifd)
            if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid), i, ctypes.byref(ifd)):
                break
            i += 1
            need = wt.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), None, 0, ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8  # cbSize of SP_DEVICE_INTERFACE_DETAIL_DATA_W on x64
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), buf, need, None, None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            h = _open(path, 0)
            if h == INVALID_HANDLE_VALUE:
                continue
            try:
                attr = HIDD_ATTRIBUTES()
                attr.Size = ctypes.sizeof(attr)
                hid.HidD_GetAttributes(h, ctypes.byref(attr))
                pp = ctypes.c_void_p()
                caps = HIDP_CAPS()
                if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                    hid.HidP_GetCaps(pp, ctypes.byref(caps))
                    hid.HidD_FreePreparsedData(pp)
                yield dict(path=path, vid=attr.VendorID, pid=attr.ProductID, usage_page=caps.UsagePage,
                           usage=caps.Usage, in_len=caps.InputReportByteLength, out_len=caps.OutputReportByteLength)
            finally:
                kernel32.CloseHandle(h)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)


class HidDevice:
    def __init__(self, path, in_len, out_len):
        self.h = _open(path, GENERIC_READ | GENERIC_WRITE)
        if self.h == INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(), "open failed: " + path)
        self.in_len, self.out_len = in_len, out_len
        self._rev = kernel32.CreateEventW(None, True, False, None)
        self._wev = kernel32.CreateEventW(None, True, False, None)
        self._rbuf = ctypes.create_string_buffer(in_len)
        self._rov = None

    def write(self, data: bytes):
        buf = ctypes.create_string_buffer(bytes(data).ljust(self.out_len, b"\0"), self.out_len)
        ov = OVERLAPPED()
        ov.hEvent = self._wev
        kernel32.ResetEvent(self._wev)
        n = wt.DWORD()
        if not kernel32.WriteFile(self.h, buf, self.out_len, ctypes.byref(n), ctypes.byref(ov)):
            if ctypes.get_last_error() != ERROR_IO_PENDING:
                raise OSError(ctypes.get_last_error(), "write failed")
            if kernel32.WaitForSingleObject(self._wev, 1000) == WAIT_TIMEOUT:
                kernel32.CancelIo(self.h)
                raise TimeoutError("write timeout")
            kernel32.GetOverlappedResult(self.h, ctypes.byref(ov), ctypes.byref(n), False)

    def read(self, timeout_ms=1000):
        """Return report bytes, or None on timeout. Keeps one read pending across timeouts."""
        if self._rov is None:
            self._rov = OVERLAPPED()
            self._rov.hEvent = self._rev
            kernel32.ResetEvent(self._rev)
            n = wt.DWORD()
            if kernel32.ReadFile(self.h, self._rbuf, self.in_len, ctypes.byref(n), ctypes.byref(self._rov)):
                self._rov = None
                return self._rbuf.raw[:n.value]
            if ctypes.get_last_error() != ERROR_IO_PENDING:
                self._rov = None
                raise OSError(ctypes.get_last_error(), "read failed")
        if kernel32.WaitForSingleObject(self._rev, timeout_ms) == WAIT_TIMEOUT:
            return None
        n = wt.DWORD()
        ok = kernel32.GetOverlappedResult(self.h, ctypes.byref(self._rov), ctypes.byref(n), False)
        self._rov = None
        if not ok:
            raise OSError(ctypes.get_last_error(), "read failed")
        return self._rbuf.raw[:n.value]

    def close(self):
        kernel32.CancelIo(self.h)
        kernel32.CloseHandle(self.h)
