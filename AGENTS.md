# AGENTS.md: guide for AI coding agents

This repository is **t650d**, a user-mode Windows driver for the **Logitech T650 Wireless
Rechargeable Touchpad** (Unifying receiver `046D:C52B`, device WPID `0x4101`). It is a drop-in
replacement for Logitech SetPoint. Read this file before changing code.

## What it does, in one paragraph

`t650d.py` opens the receiver's HID++ vendor collections (usage page `0xFF00`, usage `1` = short
reports, usage `2` = long reports). It finds the device whose HID++ name contains `T650` and sets
feature `0x6100` (TouchPad Raw XY) to raw report state `0x05`. It then assembles finger frames
and feeds them to `engine.Engine`. The engine turns contacts into pointer motion, clicks, hi-res
wheel scrolling with inertia, a touch-injected pinch, and swipe gestures, all injected with
`SendInput` / `InjectTouchInput`. On exit the pad is restored to raw state `0x00` (native firmware
mouse).

## Layout

| File | Responsibility | Notes |
|---|---|---|
| `t650d.py` | CLI, `DEFAULTS` config, `Pad` (raw mode, frame parsing, link up/down, receiver reopen), main loop | Everything configurable lives in `DEFAULTS`; `config.json` overrides it |
| `engine.py` | Pure gesture logic | No I/O except through `inject`, `touch` and `apps`, so it is fully testable offline |
| `hidpp.py` | HID++ 2.0 requests; threaded readers route replies (our SWID `0x0A`) vs events | `io_error` flags dead handles |
| `winhid.py` | SetupAPI enumeration + overlapped ReadFile/WriteFile via ctypes | No hidapi dependency |
| `inject.py` | SendInput wrappers; injected events carry `dwExtraInfo = 0x7650` | |
| `touch.py` | Two-contact touch pinch around the cursor | Needs per-monitor DPI awareness (set in `main`) |
| `apps.py` | Find an app window by exe name and toggle it to front, minimise it, or launch it | Runs in a worker thread |
| `test_engine.py` | Offline regression tests with synthetic frames | Run after every engine change |
| `replay.py` | Replays a `--debug` log through the engine; lists injected clicks and flags double clicks | Preferred way to debug click problems |
| `probe.py`, `rawdump.py` | Hardware probing | Need the real device |
| `mousehook.py` | System-wide click-source logger | **Makes the whole mouse laggy while it runs**; use briefly |

## Raw report layout (HID++ long report `0x11`, feature `0x6100` event)

`r[0]=0x11, r[1]=device index, r[2]=feature index`, then params `p = r[4:20]`:

- `p[0:2]`: timestamp (unused).
- `p[2:9]` and `p[9:16]`: two finger slots. For a slot `d`:
  - `x = (d[0]&0x3F)<<8 | d[1]` and `y = (d[2]&0x3F)<<8 | d[3]`
  - `contact_type = d[0]>>6` (non-zero means no data), `contact_status = d[2]>>6` (1 = touching)
  - `z = d[4]`, `area = d[5]`, `finger_id = d[6]>>4` (0 = empty slot)
- `p[8]`: flags. Bit 0 = end of frame, bit 1 = spurious, bit 2 = physical button.
- `p[15]&0x0F`: total finger count. A frame is complete when end-of-frame is set or the count is
  ≤ 2. Contacts that are missing from a frame count as lifted.
- The y origin is lower-left (`getTouchpadInfo` origin byte = 1), so the code flips y.
- Pad size is 2832 × 2364 units at 600 dpi (about 23.6 units/mm).

## Invariants (do not break)

1. **Standard library only.** No pip dependencies and no kernel components.
2. **Never inject a click for the physical press when `physical_click == "hardware"`.** With
   Logitech's receiver driver installed, the firmware click is forwarded as well, and injecting
   one too causes double clicks. The default `"driver"` is correct when only Windows' inbox HID
   driver is present.
3. **Restore native mode on exit** (`Pad.close`). Otherwise a crashed driver leaves the pad dead
   until it is power-cycled.
4. **Engine changes must keep `python test_engine.py` green.** Add a test for every behaviour you
   change.
5. **Don't run low-level mouse hooks during normal use** (see `mousehook.py`).
6. **Keep `.ps1` and `.bat` files ASCII-only.** Non-ASCII text breaks under legacy code pages.
7. **Wheel output never sends exact multiples of 120.** This keeps hi-res handling in apps.

## Common tasks

- **Tune the feel.** Change `DEFAULTS` in `t650d.py`, or tell the user to use `config.json`. Don't
  hard-code numbers in `engine.py`.
- **Add or change a gesture.**
  - Swipes: `Engine._swipe`.
  - Taps: `Engine._end_session`.
  - Physical clicks: the button block in `Engine.frame`.
  - Finger-count → mode decisions: the `want = ...` block in `Engine.frame`.
- **Support another touchpad with feature `0x6100`.** Change the `find_device("T650")` match. Then
  check origin/flip, size and resolution with `probe.py`.
- **Debug a bad gesture.** Run with `--debug`, reproduce the gesture, then run `replay.py` on the
  log.

## Verify

```powershell
python test_engine.py          # must print "all engine checks passed"
python -W error -c "import t650d"
```
