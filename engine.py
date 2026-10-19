"""Gesture engine: raw contacts -> pointer, clicks, scrolling, gestures.

Behaviour borrowed from good touchpad stacks:
  * libinput  - drop the first motion after a finger lands (contact settling), velocity
                measured over a time window not a single frame, low-speed precision zone in
                the pointer curve, thumb detection in the bottom zone by landing-time gap,
                clickfinger (1/2/3 fingers = left/right/middle), tap move threshold.
  * macOS     - linear two-finger scroll, momentum that survives staggered finger lifts,
                fling boost (flicking again during momentum adds speed), a touch that stops
                momentum is not a tap, pointer jiggle from pressing the pad is undone.
  * Windows   - hi-res wheel (fractions of a notch), tap-and-drag, swipe gestures.
Scroll output is paced through a short low-pass so uneven USB report timing (6-16 ms)
does not become uneven scroll steps.
"""
import collections
import math

import apps
import inject
import touch


class Finger:
    __slots__ = ("t0", "x", "y", "travel", "fresh", "thumb")

    def __init__(self, t0, x, y):
        self.t0, self.x, self.y = t0, x, y
        self.travel = 0.0
        self.fresh = True
        self.thumb = False


class Engine:
    def __init__(self, cfg, upmm, xsize, ysize, log, debug=False):
        self.c, self.upmm, self.xsize, self.ysize, self.log, self.debug = cfg, upmm, xsize, ysize, log, debug
        self.fingers = {}
        self.mode = None            # None | pointer | drag | scroll | pinch | swipe | dead
        self.session = None
        self.btn_down = None
        self.freeze_until = 0.0
        self.rem = [0.0, 0.0]
        self.moves = collections.deque()   # (t, ix, iy) recent pointer output, for click undo
        self.vel = collections.deque()     # (t, dist_mm) pointer speed window
        self.wpend = [0.0, 0.0]            # scroll waiting in the output pacer
        self.wrem = [0.0, 0.0]
        self.zrem = 0.0
        self.shist = collections.deque()   # (t, vv, vh) scroll output for momentum
        self.scroll_last_t = 0.0
        self.momentum = None
        self.stopped_momentum = None       # (t, [vv, vh]) momentum a touch just stopped
        self.tap_hold_until = 0.0
        self.tap_dragging = False
        self.alt_held = False
        self.ctrl_held = False
        self.pinch = None                  # touch.Pinch while a touch-injected pinch runs
        self.last_frame = 0.0
        self.last_tick = 0.0

    # ------------------------------------------------------------------ helpers
    def mm(self, u):
        return u / self.upmm

    def _speed(self, window):
        """mm/s over the last `window` seconds of self.vel."""
        v = self.vel
        if len(v) < 2:
            return 0.0
        span = v[-1][0] - v[0][0]
        if span <= 0:
            return 0.0
        return sum(d for _, d in list(v)[1:]) / span

    def _pointer(self, now, dxm, dym):
        c = self.c
        self.vel.append((now, math.hypot(dxm, dym)))
        while len(self.vel) > 2 and now - self.vel[0][0] > c["velocity_window"]:
            self.vel.popleft()
        if now < self.freeze_until:
            return
        v = self._speed(c["velocity_window"])
        slow, fast = c["pointer_gain_slow"], c["pointer_gain_fast"]
        if v < c["precision_speed"]:
            g = slow * (c["precision_gain"] + (1 - c["precision_gain"]) * v / c["precision_speed"])
        else:
            t = min(1.0, max(0.0, (v - c["pointer_speed_slow"]) / (c["pointer_speed_fast"] - c["pointer_speed_slow"])))
            g = slow + (fast - slow) * t * t * (3 - 2 * t)
        self.rem[0] += dxm * g
        self.rem[1] += dym * g
        ix, iy = int(self.rem[0]), int(self.rem[1])
        self.rem[0] -= ix
        self.rem[1] -= iy
        if ix or iy:
            inject.move(ix, iy)
            self.moves.append((now, ix, iy))
        while self.moves and now - self.moves[0][0] > c["click_undo_s"]:
            self.moves.popleft()

    def _undo_press_jiggle(self, now):
        c = self.c
        sx = sum(m[1] for m in self.moves if now - m[0] <= c["click_undo_s"])
        sy = sum(m[2] for m in self.moves if now - m[0] <= c["click_undo_s"])
        if (sx or sy) and abs(sx) + abs(sy) <= c["click_undo_px"]:
            inject.move(-sx, -sy)
        self.moves.clear()
        self.rem = [0.0, 0.0]

    def _emit_wheel(self, vv, vh):
        self.wrem[0] += vv
        self.wrem[1] += vh
        out = []
        for i in (0, 1):
            k = int(self.wrem[i])
            if k and k % 120 == 0:   # never a whole notch: keeps apps treating it as precise
                k -= 1 if k > 0 else -1
            self.wrem[i] -= k
            out.append(k)
        inject.wheel(out[0], out[1])

    def _release_mods(self):
        if self.pinch:
            self.pinch.end()
            self.pinch = None
        if self.alt_held:
            inject.key("alt", False)
            self.alt_held = False
        if self.ctrl_held:
            inject.key("ctrl", False)
            self.ctrl_held = False

    # ------------------------------------------------------------------ fingers
    def _update_fingers(self, now, raw):
        for fid in [f for f in self.fingers if f not in raw]:
            del self.fingers[fid]
        deltas = {}
        for fid, (x, y) in raw.items():
            f = self.fingers.get(fid)
            if f is None:
                self.fingers[fid] = Finger(now, x, y)
                continue
            dx, dy = self.mm(x - f.x), self.mm(y - f.y)
            f.x, f.y = x, y
            if f.fresh:          # first motion after landing is the contact settling
                f.fresh = False
                continue
            f.travel += math.hypot(dx, dy)
            deltas[fid] = (dx, dy)
        return deltas

    def _classify_thumbs(self):
        """A resting finger in the bottom zone that landed clearly apart from another finger
        is a thumb (resting on / pressing the pad) and is ignored for motion and counting."""
        c = self.c
        zone = (1 - c["thumb_zone"]) * self.ysize
        changed = False
        fs = list(self.fingers.values())
        for f in fs:
            if f.thumb and f.travel > c["thumb_release_mm"]:
                f.thumb = False
                changed = True
            elif not f.thumb and f.y > zone and f.travel < c["thumb_max_travel_mm"] and len(fs) >= 2:
                others = [g for g in fs if g is not f]
                if any(g.y <= zone for g in others) and \
                        all(abs(g.t0 - f.t0) > c["thumb_land_gap"] for g in others):
                    f.thumb = True
                    changed = True
        return changed

    # ------------------------------------------------------------------ main entry
    def frame(self, now, raw, button):
        c = self.c
        self.last_frame = now
        deltas = self._update_fingers(now, raw)
        if self._classify_thumbs() and self.session:
            # finger roles changed: restart the gesture quietly (no tap from the old one)
            self._release_mods()
            self.session = None
            self.mode = None
        eff = {fid: f for fid, f in self.fingers.items() if not f.thumb}
        n = len(eff)
        if self.debug:
            self.log.debug("n=%d/%d btn=%d mode=%s %s", n, len(raw), button, self.mode,
                           " ".join(f"{k}:{f.x},{f.y}{'T' if f.thumb else ''}" for k, f in self.fingers.items()))

        if raw and self.momentum:
            self.stopped_momentum = (now, self.momentum)
            self.momentum = None
            self.wpend = [0.0, 0.0]

        # physical click: clickfinger count, lower-right zone = right
        # Physical press. "driver" (default): we inject clickfinger/zone buttons. "hardware":
        # Logitech's receiver driver already forwards a firmware click, so only track the press.
        hw = c["physical_click"] == "hardware"
        if button and not self.btn_down:
            if hw:
                self.btn_down = "hw"
            else:
                in_zone = any(f.x > c["right_zone_x"] * self.xsize and f.y > c["right_zone_y"] * self.ysize
                              for f in self.fingers.values())
                self.btn_down = "middle" if n >= 3 else ("right" if n == 2 or (in_zone and n <= 1) else "left")
                self._undo_press_jiggle(now)
            if self.tap_hold_until or self.tap_dragging:
                if not hw:   # with a hardware press the left button is already down: its release ends it
                    inject.button("left", False)
                self.tap_hold_until, self.tap_dragging = 0.0, False
            if not hw:
                inject.button(self.btn_down, True)
            self.freeze_until = now + c["click_freeze"]
            if self.session:
                self.session["clicked"] = True
            if self.mode in ("scroll", "pinch", "swipe"):
                self._release_mods()
                self.mode = "dead"
        elif not button and self.btn_down:
            if self.btn_down != "hw":
                inject.button(self.btn_down, False)
            self.btn_down = None
            self.freeze_until = now + c["click_freeze"]

        if n and not self.session:
            stopper = self.stopped_momentum is not None and now - self.stopped_momentum[0] < 0.05
            self.session = dict(t0=now, maxn=n, travel=0.0, clicked=bool(button), fired=False,
                                acc=[0.0, 0.0], lacc=[0.0, 0.0], zacc=0.0, cacc=0.0, lock=None,
                                alt_acc=0.0, scrolled=False, stopper=stopper,
                                early=any(f.t0 < now - 0.03 for f in eff.values()))
            self.mode = None
            self.vel.clear()
            if self.tap_hold_until:
                if now < self.tap_hold_until and n == 1:
                    self.tap_dragging = True
                else:
                    inject.button("left", False)
                self.tap_hold_until = 0.0
        s = self.session

        if n == 0:
            if s:
                self._end_session(now, s)
            return

        s["maxn"] = max(s["maxn"], n)
        moved = [deltas[i] for i in eff if i in deltas]
        complete = len(moved) == n
        if moved:
            dxm = sum(d[0] for d in moved) / len(moved)
            dym = sum(d[1] for d in moved) / len(moved)
        else:
            dxm = dym = 0.0
        s["travel"] += math.hypot(dxm, dym)

        if self.btn_down or self.tap_dragging:
            want = "drag"
        elif n == 1:
            want = "pointer" if self.mode in (None, "pointer", "drag") else "dead"
        elif n == 2:
            if self.mode in ("scroll", "pinch"):
                want = self.mode
            elif self.mode == "swipe" or (self.mode == "dead" and s["maxn"] >= 3):
                want = "dead"
            else:
                want = "scroll"
        else:
            want = "dead" if self.mode == "dead" and s["clicked"] else "swipe"
        if want != self.mode:
            if self.mode == "pinch":
                self._release_mods()
            if want == "scroll" and self.mode != "dead":
                self.shist.clear()
            self.mode = want
            s["acc"] = [0.0, 0.0]
            s["lock"] = None
            s["cacc"] = s["zacc"] = 0.0

        m = self.mode
        if m == "pointer":
            if moved:
                self._pointer(now, dxm, dym)
        elif m == "drag":
            if moved:
                bx, by = max(moved, key=lambda d: abs(d[0]) + abs(d[1]))
                self._pointer(now, bx, by)
        elif not complete:
            return       # a finger just landed: its position is not settled yet
        elif m == "scroll":
            self._scroll(now, eff, deltas, dxm, dym, s)
        elif m == "pinch":
            self._pinch(eff, deltas, s)
        elif m == "swipe":
            self._swipe(dxm, dym, s)

    # ------------------------------------------------------------------ scroll / pinch / swipe
    def _pair_dist_change(self, eff, deltas):
        ids = list(eff)[:2]
        if len(ids) < 2:
            return 0.0
        a, b = eff[ids[0]], eff[ids[1]]
        (adx, ady), (bdx, bdy) = deltas[ids[0]], deltas[ids[1]]
        now_d = math.hypot(self.mm(a.x - b.x), self.mm(a.y - b.y))
        prev_d = math.hypot(self.mm(a.x - b.x) - (adx - bdx), self.mm(a.y - b.y) - (ady - bdy))
        return now_d - prev_d

    def _scroll(self, now, eff, deltas, dxm, dym, s):
        c = self.c
        s["zacc"] += self._pair_dist_change(eff, deltas)
        s["cacc"] += math.hypot(dxm, dym)
        if s["lock"] is None:
            s["acc"][0] += dxm
            s["acc"][1] += dym
            if c["pinch_zoom"] and abs(s["zacc"]) > c["pinch_start_mm"] and abs(s["zacc"]) > 1.5 * s["cacc"]:
                self.mode = "pinch"
                s["pinch_f0"] = self._pair_dist(eff) - s["zacc"]   # finger distance when the pinch began
                return
            ax, ay = abs(s["acc"][0]), abs(s["acc"][1])
            if max(ax, ay) < c["scroll_start_mm"]:
                return
            if c["axis_lock"]:
                s["lock"] = "y" if ay >= 2 * ax else ("x" if ax >= 2 * ay else "free")
            else:
                s["lock"] = "free"
            s["lacc"] = [0.0, 0.0]
            dxm, dym = s["acc"]
        # a locked axis unlocks after clear, sustained diagonal/cross motion
        s["lacc"][0] += abs(dxm)
        s["lacc"][1] += abs(dym)
        if s["lock"] == "y" and s["lacc"][0] > c["unlock_mm"] and s["lacc"][0] > 0.6 * s["lacc"][1]:
            s["lock"] = "free"
        elif s["lock"] == "x" and s["lacc"][1] > c["unlock_mm"] and s["lacc"][1] > 0.6 * s["lacc"][0]:
            s["lock"] = "free"
        if s["lock"] == "y":
            dxm = 0.0
        elif s["lock"] == "x":
            dym = 0.0
        dt = max(0.004, now - (self.shist[-1][0] if self.shist else now - 0.01))
        v = math.hypot(dxm, dym) / dt
        g = c["scroll_gain"] * (1 + c["scroll_accel"] * max(0.0, v - 40) / 100)
        sign = 1 if c["natural_scroll"] else -1
        vv, vh = sign * dym * g, -sign * dxm * g
        self.wpend[0] += vv
        self.wpend[1] += vh
        s["scrolled"] = True
        self.shist.append((now, vv, vh))
        if vv or vh:
            self.scroll_last_t = now
        while len(self.shist) > 2 and now - self.shist[0][0] > c["momentum_window"]:
            self.shist.popleft()

    def _pair_dist(self, eff):
        a, b = list(eff.values())[:2]
        return math.hypot(self.mm(a.x - b.x), self.mm(a.y - b.y))

    def _pinch(self, eff, deltas, s):
        """Linear zoom: the zoom ratio follows finger distance / distance at pinch start.
        "touch" mode injects a two-contact touchscreen pinch around the cursor (smooth in
        browsers, Photos, Office...); "wheel" mode (or if injection fails) sends hi-res ctrl+wheel."""
        if len(eff) != 2:
            return
        c = self.c
        if c["pinch_mode"] == "touch" and touch.available():
            if self.pinch is None:
                self.pinch = touch.Pinch()
                self.pinch.move(c["pinch_span"])          # contacts land at the base span
            if self.pinch.ok:
                ratio = (self._pair_dist(eff) / max(1.0, s["pinch_f0"])) ** c["pinch_gain"]
                self.pinch.move(max(c["pinch_min_span"], c["pinch_span"] * ratio))
                return
        if not self.ctrl_held:
            inject.key("ctrl", True)
            self.ctrl_held = True
        self.zrem += self._pair_dist_change(eff, deltas) * c["pinch_wheel_gain"]
        k = int(self.zrem)
        if k:
            self.zrem -= k
            inject.wheel(k)

    def _swipe(self, dxm, dym, s):
        c = self.c
        s["acc"][0] += dxm
        s["acc"][1] += dym
        ax, ay = s["acc"]
        if self.alt_held:
            s["alt_acc"] += dxm
            while abs(s["alt_acc"]) >= c["alt_tab_step_mm"]:
                fwd = s["alt_acc"] > 0
                s["alt_acc"] -= c["alt_tab_step_mm"] * (1 if fwd else -1)
                inject.chord("tab") if fwd else inject.chord("shift", "tab")
            return
        if s["fired"] or max(abs(ax), abs(ay)) < c["swipe_mm"]:
            return
        if s["maxn"] >= 4:   # four fingers: per-direction app toggles from config (None = nothing)
            s["fired"] = True
            d = ("up" if ay < 0 else "down") if abs(ay) > abs(ax) else ("left" if ax < 0 else "right")
            spec = c.get("swipe4_" + d)
            if spec:
                apps.toggle(spec, self.log)
            return
        if abs(ay) > abs(ax):
            s["fired"] = True
            inject.chord("win", "tab") if ay < 0 else inject.chord("win", "d")
        else:
            inject.key("alt", True)
            self.alt_held = True
            inject.chord("tab")
            s["alt_acc"] = 0.0

    # ------------------------------------------------------------------ session end
    def _end_session(self, now, s):
        c = self.c
        dur = now - s["t0"]
        is_tap = (dur < c["tap_time"] and s["travel"] < c["tap_move_mm"] and not s["clicked"]
                  and not s["fired"] and not self.alt_held and not s["stopper"] and not s["early"])
        self._release_mods()
        if self.tap_dragging:
            inject.button("left", False)
            self.tap_dragging = False
            if is_tap and s["maxn"] == 1:
                inject.click("left")
            is_tap = False
        if is_tap and c["tap_click"]:
            if s["maxn"] == 1:
                if c["tap_drag"]:
                    inject.button("left", True)
                    self.tap_hold_until = now + c["tap_drag_window"]
                else:
                    inject.click("left")
            elif s["maxn"] == 2:
                inject.click("right")
            elif s["maxn"] == 3:
                inject.click("middle")
        if s["scrolled"] and c["inertia"]:
            self._start_momentum(now, s)
        self.session = None
        self.mode = None

    def _start_momentum(self, now, s):
        c = self.c
        h = [e for e in self.shist if self.scroll_last_t - e[0] <= c["momentum_window"]]
        if now - self.scroll_last_t > c["momentum_max_pause"] or len(h) < 3:
            return
        span = h[-1][0] - h[0][0]
        if span <= 0:
            return
        vv = sum(e[1] for e in h[1:]) / span
        vh = sum(e[2] for e in h[1:]) / span
        if math.hypot(vv, vh) < c["inertia_min_speed"]:
            return
        sm = self.stopped_momentum
        if sm and s["stopper"] and now - sm[0] < 0.6 and vv * sm[1][0] + vh * sm[1][1] > 0:
            vv += sm[1][0] * c["fling_boost"]
            vh += sm[1][1] * c["fling_boost"]
        cap = c["inertia_max_speed"]
        sp = math.hypot(vv, vh)
        if sp > cap:
            vv, vh = vv * cap / sp, vh * cap / sp
        self.momentum = [vv, vh]
        self.stopped_momentum = None

    # ------------------------------------------------------------------ timers / output pacing
    def tick(self, now):
        dt = min(0.05, max(0.0, now - self.last_tick)) if self.last_tick else 0.0
        self.last_tick = now
        if self.tap_hold_until and now >= self.tap_hold_until and not self.session:
            inject.button("left", False)
            self.tap_hold_until = 0.0
        if self.momentum:
            vv, vh = self.momentum
            self.wpend[0] += vv * dt
            self.wpend[1] += vh * dt
            decay = math.exp(-dt / self.c["inertia_tau"])
            vv, vh = vv * decay, vh * decay
            self.momentum = [vv, vh] if math.hypot(vv, vh) > self.c["inertia_stop_speed"] else None
        if self.wpend[0] or self.wpend[1]:
            tau = self.c["scroll_smooth_s"]
            a = 1.0 if tau <= 0 else 1 - math.exp(-dt / tau)
            ov, oh = self.wpend[0] * a, self.wpend[1] * a
            if abs(self.wpend[0] - ov) < 0.5 and abs(self.wpend[1] - oh) < 0.5:
                ov, oh = self.wpend
            self.wpend[0] -= ov
            self.wpend[1] -= oh
            self._emit_wheel(ov, oh)

    def active(self):
        return bool(self.momentum or self.tap_hold_until or self.wpend[0] or self.wpend[1])

    def reset(self):
        self._release_mods()
        if self.btn_down and self.btn_down != "hw":
            inject.button(self.btn_down, False)
        if self.tap_hold_until or self.tap_dragging:
            inject.button("left", False)
        self.__init__(self.c, self.upmm, self.xsize, self.ysize, self.log, self.debug)
