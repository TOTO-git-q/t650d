"""Offline checks of the gesture engine with synthetic frames (no device, no real input)."""
import logging
import inject
import t650d
from engine import Engine

calls = []
for name in ("move", "button", "click", "wheel", "key", "chord"):
    setattr(inject, name, (lambda n: lambda *a: calls.append((n,) + a))(name))

U = 23.6  # units per mm
cfg = dict(t650d.DEFAULTS, natural_scroll=True, tap_click=True, tap_drag=True, physical_click="driver")
log = logging.getLogger("t")
FRAME = 0.010


class Sim:
    def __init__(self):
        self.e = Engine(cfg, U, 2832, 2364, log)
        self.t = 0.0

    def feed(self, frames):
        for cur, btn in frames:
            self.t += FRAME
            self.e.frame(self.t, cur, btn)
            self.e.tick(self.t)
            self.t += 0.004
            self.e.tick(self.t)
        return self

    def idle(self, secs):
        end = self.t + secs
        while self.t < end:
            self.t += 0.004
            self.e.tick(self.t)
        return self


def run(frames, idle=0.0):
    calls.clear()
    Sim().feed(frames).idle(idle)
    return calls[:]


def wheels(cs):
    return [c for c in cs if c[0] == "wheel"]


def lift():
    return [({}, False)]


# 1) one finger moves right 20 mm -> pointer moves right only; landing jump dropped
fr = [({1: (1000, 1000)}, False), ({1: (1040, 1040)}, False)] + \
     [({1: (1040 + i * U, 1040)}, False) for i in range(1, 21)] + lift()
cs = run(fr)
mv = [c for c in cs if c[0] == "move"]
assert mv and sum(c[1] for c in mv) > 100 and all(c[2] == 0 for c in mv), mv

# 2) slow precise motion moves less per mm than fast motion
slow = run([({1: (1000 + i * 2, 1000)}, False) for i in range(120)] + lift())   # ~8 mm/s... 0.085 mm/frame
fast = run([({1: (1000 + i * 59, 1000)}, False) for i in range(5)] + lift())    # 2.5 mm/frame
px_per_mm_slow = sum(c[1] for c in slow if c[0] == "move") / (119 * 2 / U)
px_per_mm_fast = sum(c[1] for c in fast if c[0] == "move") / (3 * 59 / U)
assert px_per_mm_slow < px_per_mm_fast * 0.7, (px_per_mm_slow, px_per_mm_fast)

# 3) quick tap -> left down, released after tap-drag window; two-finger tap -> right
cs = run([({1: (1000, 1000)}, False)] * 5 + lift(), idle=0.4)
assert [c[:3] for c in cs if c[0] == "button"] == [("button", "left", True), ("button", "left", False)], cs
cs = run([({1: (1000, 1000), 2: (1300, 1000)}, False)] * 5 + lift())
assert ("click", "right") in cs, cs

# 4) two fingers move down 30 mm -> hi-res positive wheel, never whole notches, inertia adds
fr = [({1: (1000, 1000 + i * U * 0.5), 2: (1300, 1000 + i * U * 0.5)}, False) for i in range(61)]
calls.clear()
sim = Sim().feed(fr)
finger = sum(c[1] for c in wheels(calls))
sim.feed(lift()).idle(2.0)
wh = wheels(calls)
total = sum(c[1] for c in wh)
assert all(c[1] >= 0 and c[2] == 0 for c in wh)
assert all(c[1] % 120 for c in wh if c[1]), "whole-notch deltas emitted"
assert max(c[1] for c in wh) < 120
assert total > finger * 1.3, (total, finger)
assert not any(c[0] == "move" for c in calls)
print("scroll: %d wheel events, max %d, finger part %d, with inertia %d" % (len(wh), max(c[1] for c in wh), finger, total))

# 5) staggered lift (one finger leaves 30 ms before the other) still gives momentum
fr2 = fr + [({2: (1300, 1000 + 61 * U * 0.5)}, False)] * 3 + lift()
cs = run(fr2, idle=2.0)
assert sum(c[1] for c in wheels(cs)) > total * 0.8, "momentum lost on staggered lift"

# 6) touching during momentum stops it and is not a click; quick re-flick boosts
calls.clear()
sim = Sim().feed(fr + lift()).idle(0.15)
n_before = len(wheels(calls))
sim.feed([({1: (1000, 1000)}, False)] * 4 + lift()).idle(0.5)
assert not any(c[0] in ("button", "click") for c in calls), "tap that stopped momentum clicked"
calls.clear()
sim = Sim().feed(fr + lift()).idle(0.15)
sim.feed(fr[:12] + lift())          # short re-flick
calls.clear()
sim.idle(0.05)
boosted = sum(c[1] for c in wheels(calls))
calls.clear()
Sim().feed(fr[:12] + lift())
calls.clear()
plain = Sim()
plain.feed(fr[:12] + lift())
calls.clear()
plain.idle(0.05)
single = sum(c[1] for c in wheels(calls))
assert boosted > single * 1.3, (boosted, single)

# 7) pinch apart, touch mode -> injected span follows finger-distance ratio linearly, then ends
import engine as engine_mod
class FakePinch:
    def __init__(self):
        self.ok = True
        calls.append(("pinch_start",))
    def move(self, span):
        calls.append(("span", span))
    def end(self):
        calls.append(("pinch_end",))
engine_mod.touch.available = lambda: True
engine_mod.touch.Pinch = FakePinch
fr = [({1: (1400 - i * U * 0.6, 1000), 2: (1600 + i * U * 0.6, 1000)}, False) for i in range(40)]
cs = run(fr + lift())
spans = [c[1] for c in cs if c[0] == "span"]
assert cs.count(("pinch_start",)) == 1 and cs.count(("pinch_end",)) == 1, cs[:5]
assert spans[0] == cfg["pinch_span"] and all(b >= a for a, b in zip(spans, spans[1:]))
d0 = (200 + 2 * U * 0.6) / U   # first (settling) delta after landing is dropped
d_end = (200 + 2 * 39 * U * 0.6) / U
assert abs(spans[-1] / spans[0] - d_end / d0) < 0.02, (spans[-1] / spans[0], d_end / d0)
steps = [b - a for a, b in zip(spans[1:], spans[2:])]
assert max(steps) - min(steps) < 1.0, "span should grow linearly with finger spread"
assert not any(c[0] == "wheel" for c in cs)
# 7b) wheel mode -> ctrl held + hi-res ctrl+wheel, ctrl released
cfg["pinch_mode"] = "wheel"
cs = run(fr + lift())
assert ("key", "ctrl", True) in cs and ("key", "ctrl", False) in cs
wz = [c[1] for c in cs if c[0] == "wheel"]
assert wz and max(wz) < 120 and sum(wz) > 0
cfg["pinch_mode"] = "touch"

# 8) three fingers swipe up 30 mm -> win+tab once
fr = [({1: (1000, 1500 - i * U), 2: (1300, 1500 - i * U), 3: (1600, 1500 - i * U)}, False) for i in range(31)]
cs = run(fr + lift())
assert [c for c in cs if c[0] == "chord"] == [("chord", "win", "tab")], cs

# 9) physical click with one finger -> left; press jiggle undone
fr = [({1: (1000, 1000)}, False)] * 3 + [({1: (1000 + k * 8, 1000 + k * 8)}, False) for k in range(1, 3)] + \
     [({1: (1016, 1016)}, True)] * 2 + [({1: (1016, 1016)}, False)] + lift()
cs = run(fr)
assert [c for c in cs if c[0] == "button"] == [("button", "left", True), ("button", "left", False)], cs
mv = [c for c in cs if c[0] == "move"]
assert sum(c[1] for c in mv) == 0 and sum(c[2] for c in mv) == 0, mv

# 10) physical click in lower-right zone -> right
cs = run([({1: (2600, 2200)}, False)] * 2 + [({1: (2600, 2200)}, True)] * 2 + lift())
assert ("button", "right", True) in cs, cs

# 11) after a scroll, the remaining finger does not move the pointer
fr = [({1: (1000, 1000 + i * U), 2: (1300, 1000 + i * U)}, False) for i in range(10)] + \
     [({1: (1000 + i * U, 1300)}, False) for i in range(10)] + lift()
cs = run(fr)
assert not any(c[0] == "move" for c in cs)

# 12) thumb resting in bottom zone + index moving -> pointer, not scroll; thumb click -> left
thumb = (1400, 2300)
fr = [({5: thumb}, False)] * 25 + [({5: thumb, 1: (1000 + i * U, 1000)}, False) for i in range(20)]
cs = run(fr + lift())
assert any(c[0] == "move" for c in cs) and not wheels(cs), [c for c in cs][:5]
fr = [({1: (1000, 1000)}, False)] * 25 + [({1: (1000, 1000), 5: thumb}, False)] * 3 + \
     [({1: (1000, 1000), 5: thumb}, True)] * 3 + [({1: (1000, 1000), 5: thumb}, False)] + lift()
cs = run(fr)
assert ("button", "left", True) in cs and ("button", "right", True) not in cs, cs

# 13) index tap while thumb rests -> click
fr = [({5: thumb}, False)] * 30 + [({5: thumb, 1: (1000, 1000)}, False)] * 5 + [({5: thumb}, False)] * 3
cs = run(fr, idle=0.4)
assert ("button", "left", True) in cs, cs

# 14) hardware mode (default): physical press injects no button at all, never a tap
cfg["physical_click"] = "hardware"
fr = [({1: (1000, 1000)}, False)] * 3 + [({1: (1000, 1000)}, True)] * 3 + [({1: (1000, 1000)}, False)] + lift()
cs = run(fr, idle=0.4)
assert not any(c[0] in ("button", "click") for c in cs), cs
# ... and a press right after a tap does not inject a release under the user's finger
fr = [({1: (1000, 1000)}, False)] * 4 + lift() + [({1: (1000, 1000)}, False)] +      [({1: (1000, 1000)}, True)] * 5 + [({1: (1000, 1000)}, False)] + lift()
cs = run(fr, idle=0.4)
assert [c for c in cs if c[0] == "button"] == [("button", "left", True)], cs
cfg["physical_click"] = "driver"

# 15) four-finger swipes: up/down toggle the configured apps, left/right do nothing
engine_mod.apps.toggle = lambda spec, log=None: calls.append(("app", spec["exe"]))
cfg["swipe4_up"], cfg["swipe4_down"] = {"exe": "claude.exe"}, {"exe": "chatgpt.exe"}
def four(dx, dy):
    return [({k: (900 + k * 250 + i * dx * U, 1300 + i * dy * U) for k in (1, 2, 3, 4)}, False) for i in range(31)] + lift()
assert [c for c in run(four(0, -1)) if c[0] in ("app", "chord", "key")] == [("app", "claude.exe")]
assert [c for c in run(four(0, 1)) if c[0] in ("app", "chord", "key")] == [("app", "chatgpt.exe")]
assert [c for c in run(four(1, 0)) if c[0] in ("app", "chord", "key")] == []
assert [c for c in run(four(-1, 0)) if c[0] in ("app", "chord", "key")] == []

print("all engine checks passed")
