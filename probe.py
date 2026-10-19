"""List Logitech HID collections and HID++ devices; dump touchpad info if found."""
import winhid
from hidpp import Receiver

for d in winhid.enumerate_hid():
    if d["vid"] == 0x046D:
        print(f"{d['pid']:04x} up={d['usage_page']:04x} u={d['usage']:04x} in={d['in_len']} out={d['out_len']}")

rx = Receiver()
print("receiver pid", hex(rx.pid))
for idx in range(1, 7):
    try:
        ver = rx.protocol(idx)
    except Exception as e:
        print(idx, "-", e)
        continue
    print(idx, "proto", ver, "name", rx.device_name(idx))
    fi = rx.feature_index(idx, 0x6100)
    print("   0x6100 TouchPadRawXY index:", fi)
    if fi:
        p = rx.request(idx, fi, 0)
        print("   info:", p.hex(), "x", int.from_bytes(p[0:2], "big"), "y", int.from_bytes(p[2:4], "big"),
              "z", p[4], "area", p[5], "maxcontacts", p[7], "origin", p[8],
              "dpi", int.from_bytes(p[13:15], "big") * 2 // 51)
        print("   raw report state:", rx.request(idx, fi, 1).hex())
    for fid in (0x6110, 0x2121, 0x1000, 0x6500, 0x6501, 0x6012):
        try:
            print(f"   feat {fid:04x} -> {rx.feature_index(idx, fid)}")
        except Exception as e:
            print(f"   feat {fid:04x} err {e}")
rx.close()
