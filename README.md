# t650d: Logitech T650 touchpad driver for Windows (SetPoint replacement)

**t650d** is a small open-source Windows driver for the **Logitech T650 Wireless Rechargeable
Touchpad**. It replaces Logitech SetPoint and gives the pad smooth, linear two-finger scrolling
with inertia, pinch-to-zoom and multi-finger gestures.

It runs entirely in user mode. You don't need a kernel driver, driver signing or admin rights,
and nothing has to be installed with pip. It is under 2,000 lines of plain Python using `ctypes`, tests and diagnostic tools included.

[涓枃璇存槑](#涓枃璇存槑)

## Supported hardware

| | |
|---|---|
| Device | **Logitech T650** Wireless Rechargeable Touchpad (HID++ name `Rechargeable Touchpad T650`, Unifying WPID `0x4101`) |
| Connection | Logitech **Unifying receiver** (USB `046D:C52B`) |
| OS | Windows 10 / 11 (tested on Windows 11 build 26200) |
| Python | 3.9+ (tested on 3.12), standard library only |

Other setups are untested:

- **T651** (the Bluetooth version for Mac) uses a different transport.
- Other Logitech touchpads that expose HID++ feature `0x6100` may work if you change the
  device-name match in `t650d.py` (`find_device("T650")`).

## Why

SetPoint 6.x is abandoned. With it, the T650 scrolls in coarse, stepped mouse-wheel notches
(120 units each). Two-finger scrolling feels stiff and "one notch at a time". The pad itself
reports raw finger positions at about 100 Hz, so a better gesture engine can make it feel
like a modern precision touchpad.

## Features

| Gesture | Action |
|---|---|
| 1 finger move | Pointer, with a speed curve that has a low-speed precision zone and smooth acceleration; speed is measured over a 50 ms window |
| 1 finger tap / double tap | Left click / double click |
| Tap, then touch again and move | Tap-and-drag |
| 2 finger tap | Right click |
| 3 finger tap | Middle click |
| Physical press | Clickfinger: 1/2/3 fingers = left/right/middle; the lower-right corner = right; press and move = drag |
| 2 finger slide | **Linear hi-res scrolling** (fractions of a wheel notch), axis lock with auto-unlock |
| 2 finger flick | **Inertia (momentum)**; a quick re-flick adds speed; a touch stops it without clicking |
| Pinch | **Smooth, linear zoom** around the cursor (injected two-contact touch pinch); falls back to hi-res Ctrl+wheel |
| 3 finger up / down | Task view (Win+Tab) / show desktop (Win+D) |
| 3 finger left / right | Window switcher (Alt+Tab). Keep the fingers down and slide further to step through windows; lift to pick |
| 4 finger up / down / left / right | Configurable: bring an app's window to the front, minimise it if it is already in front, or launch it if it is closed |

Under the hood, borrowed from mature touchpad stacks (libinput, macOS, Windows Precision Touchpad):

- The first motion after a finger lands is dropped, because the contact is still settling.
- A thumb resting in the bottom zone is detected by the time gap between finger landings and
  ignored for motion and for counting fingers.
- The small pointer jiggle caused by pressing the pad is undone, and the pointer is frozen for
  70 ms around each press and release.
- Momentum survives fingers lifting one after the other.
- Scroll output goes through a 10 ms pacer, so uneven USB report timing does not turn into
  uneven scrolling.
- Wheel deltas are never exact multiples of 120, so apps treat them as precise.
- A 1 ms timer resolution keeps momentum smooth.
- If the pad sleeps, reconnects, or the receiver is unplugged, the driver takes the pad over
  again automatically.
- The scroll direction and the tap settings follow your Windows Precision Touchpad settings.

## How it works

```
T650 --(Unifying)--> receiver --HID++ 2.0 long reports (usage page FF00)--> t650d
   feature 0x6100 "TouchPad Raw XY": set raw report state 0x05
   -> per-frame finger contacts (id, x, y), plus the button bit
   -> gesture engine (engine.py)
   -> SendInput (pointer, buttons, hi-res wheel, keys) / InjectTouchInput (pinch)
```

On exit, t650d puts the pad back into native firmware mode (raw report state `0x00`), so the pad
keeps working as a basic mouse when the driver is not running.

## Install

1. **Uninstall Logitech SetPoint**, or at least quit it. If both run, they fight over the pad.
2. **Unplug the Unifying receiver and plug it back in.** Uninstalling SetPoint also removes
   Logitech's driver for the receiver's HID++ interface (`MI_02`). Replugging lets Windows bind
   its built-in HID driver. Until you do this, t650d logs `no Logitech HID++ long-report collection found`.
3. Get the code and install autostart:

   ```powershell
   git clone https://github.com/TOTO-git-q/t650d
   cd t650d
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```

   `install.ps1` adds a Startup-folder shortcut that runs `pythonw t650d.py` (no console window)
   and starts the driver now. `uninstall.ps1` stops it and removes the shortcut.

To run it by hand instead:

```powershell
pythonw t650d.py            # run in the background
python  t650d.py --debug    # console log of every frame
python  t650d.py --stop     # stop the running instance (the pad goes back to native mode)
python  t650d.py --native   # only put the pad back into native firmware mode
```

## Configuration

Defaults live in `DEFAULTS` at the top of `t650d.py`. To override any of them, create
`config.json` next to `t650d.py` (see `config.example.json`) and restart the driver.

| Key | Default | Meaning |
|---|---|---|
| `pointer_gain_slow` / `pointer_gain_fast` | 7 / 22 | Pixels per mm of finger travel at slow / fast speed |
| `scroll_gain` | 45 | Wheel units (120 = one notch) per mm |
| `scroll_accel` | 0 | 0 = strictly linear scrolling |
| `natural_scroll` | null | null = follow the Windows touchpad setting |
| `inertia`, `inertia_tau` | true, 0.38 | Momentum on/off, and its decay time in seconds |
| `pinch_mode` | `"touch"` | `"touch"` = smooth injected pinch, `"wheel"` = Ctrl+wheel |
| `pinch_gain` | 1.0 | Zoom ratio = (finger distance ratio) ^ gain |
| `physical_click` | `"driver"` | Use `"hardware"` only if Logitech's receiver driver is still installed (see Troubleshooting) |
| `swipe4_up` ... `swipe4_right` | null | `{"exe": "app.exe", "launch": "<path or shell:AppsFolder\\AUMID>"}` |

## Troubleshooting

- **Every physical press is a double click.** Logitech's own receiver driver is still installed
  (it comes with SetPoint or the Unifying Software) and forwards the firmware click as well.
  Uninstall it and replug the receiver, or set `"physical_click": "hardware"`.
- **The pad does nothing after a crash.** It is still in raw mode. Run `python t650d.py --native`,
  or switch the pad off and on.
- **No input reaches some windows.** Windows blocks injected input to elevated (admin) windows
  unless the injecting process is elevated too.
- **`mousehook.py` makes the whole mouse laggy while it runs.** That is expected for a Python
  low-level hook. Use it only briefly, for diagnostics.

## Development

```powershell
python test_engine.py                    # offline gesture-engine regression tests (no device needed)
python t650d.py --debug *> debug.log     # record real frames
python replay.py debug.log               # replay a recording through the engine; lists clicks and flags double clicks
python probe.py                          # list Logitech HID collections, HID++ devices and touchpad info
python rawdump.py 10                     # dump raw 0x6100 reports for 10 s
```

| File | Role |
|---|---|
| `t650d.py` | Entry point, config defaults, `Pad` (raw-mode control, frame assembly, reconnect) |
| `engine.py` | Gesture engine: raw contacts in, pointer / clicks / scroll / gestures out |
| `hidpp.py` | HID++ 2.0 request/response over the receiver, threaded reader |
| `winhid.py` | Minimal Windows HID access (SetupAPI + overlapped I/O) via ctypes |
| `inject.py` | SendInput wrappers (move, buttons, hi-res wheel, key chords) |
| `touch.py` | Two-contact touch injection used for the linear pinch zoom |
| `apps.py` | Bring an app window to the front, minimise it, or launch it (4-finger swipes) |

AI coding agents: read [AGENTS.md](AGENTS.md) first.

## Credits

Details of the HID++ `0x6100` raw-report layout were learned from the Linux kernel driver
`hid-logitech-hidpp` (no code was copied). Gesture behaviour is modelled on libinput, macOS
and Windows Precision Touchpad.

## License

[MIT](LICENSE)

---

## 涓枃璇存槑

**t650d** 鏄粰 **缃楁妧 T650 鏃犵嚎鍏呯數瑙︽帶鏉?*锛圠ogitech T650 Wireless Rechargeable Touchpad锛?閫氳繃 Unifying 鎺ユ敹鍣ㄨ繛鎺ワ級鍐欑殑 Windows 椹卞姩锛岀敤鏉ユ浛浠ｇ綏鎶€鑰佹棫鐨?SetPoint銆傚畠瀹屽叏鍦ㄧ敤鎴锋€佽繍琛岋紝
涓嶇敤鍐呮牳椹卞姩锛屼笉鐢ㄧ鐞嗗憳鏉冮檺锛屼篃涓嶇敤 pip 瑁呬换浣曚笢瑗裤€?
- **鍙屾寚婊氬姩**锛氱嚎鎬с€佺粏绮掑害锛屽甫鎯€с€傚師鏉?SetPoint 鏄竴鏍间竴鏍艰烦鐨勶紝鐜板湪鏄繛缁殑銆?- **鍙屾寚鎹忓悎**锛氫互鍏夋爣涓轰腑蹇冨钩婊戠缉鏀撅紝缂╂斁姣斾緥鍜屼袱鎸囧紶寮€鐨勬瘮渚嬬嚎鎬у搴斻€?- **鐐瑰嚮**锛氳交鐐广€佸弻鍑汇€佽交鐐瑰悗鎷栨嫿锛涘疄浣撴寜鍘嬫椂 1/2/3 鎸囧垎鍒槸宸?鍙?涓敭锛屽彸涓嬭鏄彸閿€?- **涓夋寚**锛氫笂婊戜换鍔¤鍥撅紝涓嬫粦鏄剧ず妗岄潰锛屽乏鍙虫粦鍒囨崲绐楀彛銆?- **鍥涙寚**锛氫笂涓嬪乏鍙抽兘鍙互鍦ㄩ厤缃噷缁戝畾鎴?鍒囧埌鏌愪釜搴旂敤绐楀彛"銆?- **闃茶瑙?*锛氳瘑鍒苟蹇界暐鎼佸湪鏉垮瓙涓嬫部鐨勬媷鎸囥€?- **鑷姩閲嶈繛**锛氭澘瀛愪紤鐪犻啋鏉ャ€佹帴鏀跺櫒鎷旀彃鍚庯紝椹卞姩閮戒細鑷姩閲嶆柊鎺ョ銆?
瀹夎瑕佺偣锛?
1. 鍏堝嵏杞?SetPoint銆?2. **鎶?Unifying 鎺ユ敹鍣ㄦ嫈涓嬫潵鍐嶆彃鍥炲幓銆?* 鍗歌浇 SetPoint 浼氶『甯﹀垹鎺夋帴鏀跺櫒 HID++ 鎺ュ彛鐨勯┍鍔紝
   鎷旀彃涓€娆℃墠鑳借 Windows 鐢ㄨ嚜甯﹂┍鍔ㄩ噸鏂拌瘑鍒畠銆?3. 杩愯 `install.ps1`锛岃缃紑鏈鸿嚜鍚€?
<!-- keywords: Logitech T650 driver Windows, T650 touchpad Windows 11, SetPoint alternative,
SetPoint replacement, T650 smooth scrolling, T650 two finger scroll not smooth, T650 gestures,
Logitech Wireless Rechargeable Touchpad T650, Unifying receiver touchpad, HID++ 0x6100 TouchPad
Raw XY, precision touchpad emulation, 缃楁妧 T650 椹卞姩, 瑙︽帶鏉?鍙屾寚婊氬姩 鍗￠】, SetPoint 鏇夸唬 -->
