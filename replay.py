"""Replay a --debug log through the engine and print the clicks it would inject."""
import re, sys, datetime, logging
import inject, t650d
from engine import Engine
calls = []
T = [0.0]
for name in ("button", "click", "chord", "key"):
    setattr(inject, name, (lambda n: lambda *a: calls.append((T[0], n) + a))(name))
inject.move = lambda *a: None
inject.wheel = lambda *a: None
cfg = t650d.load_config()
e = Engine(cfg, 23.6, 2832, 2364, logging.getLogger("r"))
pat = re.compile(r'(\S+ \S+),(\d+) DEBUG n=\d+/\d+ btn=(\d) mode=\S+ ?(.*)')
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    m = pat.match(line)
    if not m:
        continue
    t = datetime.datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S").timestamp() + int(m[2]) / 1000
    raw = {int(a): (int(b), int(c)) for a, b, c in re.findall(r'(\d+):(\d+),(\d+)', m[4])}
    T[0] = t
    e.frame(t, raw, bool(int(m[3])))
    e.tick(t)
    T[0] = t + 0.3; e.tick(t + 0.3) if not raw else None
t0 = calls[0][0] if calls else 0
last_up = None
for c in calls:
    tag = ""
    if c[1] == "button" and c[2] == "left" and c[3] and last_up and c[0] - last_up < 0.5:
        tag = "   <== second left-down %.0f ms after previous up (double-click!)" % ((c[0] - last_up) * 1000)
    if c[1] == "click" and c[2] == "left" and last_up and c[0] - last_up < 0.5:
        tag = "   <== click %.0f ms after previous up (double-click!)" % ((c[0] - last_up) * 1000)
    print("%8.3f" % (c[0] - t0), *c[1:], tag)
    if (c[1] == "button" and c[2] == "left" and not c[3]) or (c[1] == "click" and c[2] == "left"):
        last_up = c[0]
