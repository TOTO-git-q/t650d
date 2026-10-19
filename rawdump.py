"""Dump raw touch frames for N seconds (does not change device mode)."""
import sys
import time
from hidpp import Receiver

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 5
rx = Receiver()
fi = rx.feature_index(1, 0x6100)
print("raw state", rx.request(1, fi, 1).hex())
end = time.time() + secs
n = 0
t0 = None
while time.time() < end:
    r = rx.long.read(50)
    if not r or r[0] != 0x11 or r[1] != 1 or r[2] != fi:
        continue
    t0 = t0 or time.time()
    n += 1
    if n <= 40 or n % 25 == 0:
        print(f"{time.time()-t0:7.3f}", r[3:20].hex())
print("reports", n, "rate", n / max(1e-6, time.time() - (t0 or time.time())))
rx.close()
