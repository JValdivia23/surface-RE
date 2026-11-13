# Surface Go 3 — Hardware Bring-Up

Device: `Surface_Go_3_2022` (Intel Core i3-10100Y, BIOS `15.102.143`, running `7.2.15-1-omarchy`).

## Hardware Summary

- **Cameras (Intel IPU3)**:
  - `00:14.3` IPU3 CIO2 (`8086:9d32`) + `00:05.0` IPU3 IMGU (`8086:1919`)
  - Front RGB: `ov5693` (`INT33BE:00`, `4-0036`) + `INT3472:00` discrete regulator/LED
  - Rear RGB + VCM: `ov8865` (`INT347A:00`, `2-0010`) + `dw9719` (`2-000c`) + `INT3472:01` (`tps68470` REVID `0x21`)
  - Front IR: `ov7251` (`INT347E:00`, `3-0060`)
- **Type Cover / Trackpad**:
  - Microsoft Surface Keyboard (`045e:09b5` USB HID); fixed locally by DKMS `surface-go3-hid-multitouch/0.1.0` (`MT_QUIRK_NO_SUSPEND_MODES`), which skips mode-switch `SET_REPORT`s in `mt_suspend()`, `mt_resume()`, `mt_on_hid_hw_open()`, and `mt_on_hid_hw_close()`, and skips `HID_DG_LATENCYMODE` (`rep=8`) and `HID_DG_SURFACESWITCH`/`HID_DG_BUTTONSWITCH` (`rep=16`) in `mt_need_to_apply_feature()` (leaving only `HID_DG_INPUTMODE` `rep=7`), plus UKI embedding via `/etc/mkinitcpio.conf.d/zz-surface-go3-hid-multitouch.conf`. **Post-reboot cold-boot + first-suspend/resume verified** (`srcversion 0C550FE4364C9359E8BD94C`, zero `usbhid` / `suspend error -5` errors).
- **Touchscreen**:
  - ELAN `ELAN9038:00` (`04F3:2A1C`, I2C HID over `I2C1` / `INT344B:00` GPIO `103` IRQ `136`); fixed locally by DKMS `surface-go3-i2c-hid/0.1.0`, adding `I2C_HID_QUIRK_RESET_ON_RESUME` for `04F3:2A1C` in `i2c_hid_quirks[]` (plus `I2C_HID_QUIRK_NO_WAKEUP_AFTER_RESET | I2C_HID_QUIRK_BOGUS_IRQ`) so `i2c_hid_core_resume()` issues `RESET` after `SET_POWER(ON)` across extended `s2idle`. **Installed and live-reloaded** (`srcversion D9C1DA0F275212672B82CB8`, extended-suspend verification pending user test).
- **USB-C / UCSI**:
  - `ucsi_acpi USBC000:00`: fixed locally by DKMS `surface-go3-ucsi-acpi/0.1.0`, which adds a zero-version `1.1` fallback (since `_DSM(READ)` omits `VER1`/`VER2` on cold boot), preserves `CCI_RESET_COMPLETE` across `_Q79` polling, and drains lingering `RESET_COMPLETE` in `EC0.CCI` after `PPM_RESET` so `SET_NOTIFICATION_ENABLE` does not stall. **Post-reboot cold-boot and live USB-C PD negotiation verified** (`/sys/class/typec/port0`, `port0-partner`, `port0-cable`, 20 V / 2.25 A PD).
- **Audio**:
  - `snd_hda_intel` with Realtek `ALC298` (`10ec:1260`) — captured in [hda-codec0.txt](captures/hda-codec0.txt).

## Subprojects

### 1. Cameras ([camera-ipu3/](camera-ipu3/))

- **DKMS driver overrides** (`surface-go3-cameras/0.1.0` in [dkms/](camera-ipu3/dkms/)):
  - [ov8865.c](camera-ipu3/dkms/src/ov8865.c): initialize sensor and controls on `s_stream(1)` and release runtime PM on error/stream-off (ported from `linux-surface` `0013-cameras.patch`).
  - [ov7251.c](camera-ipu3/dkms/src/ov7251.c): expose `V4L2_CID_ANALOGUE_GAIN` and support Surface INT3472 `avdd` / `powerdown` / `reset` bindings.
  - [ov5693.c](camera-ipu3/dkms/src/ov5693.c): call `ov5693_mode_configure()` at stream start and gate CSI-2 clock lane when non-continuous.
  - [dw9719.c](camera-ipu3/dkms/src/dw9719.c): power-on VCM write handling + 10ms `regulator_enable()` settle delay.
- **PipeWire / WirePlumber** ([52-surface-go3-ipu3.conf](camera-ipu3/configs/wireplumber/52-surface-go3-ipu3.conf)):
  - Disables raw `ipu3-imgu` (`00:05.0`) and `ipu3-cio2` (`00:14.3`) V4L2 devices/nodes so applications only see `libcamera` streams, and sets names/priorities for Front/Rear/IR nodes.
- **Status**:
  - Pre-DKMS baseline: Front `ov5693` streamed 30/30 frames (`1280x720-NV12` at ~28.5 fps); Rear `ov8865` stalled after frame 0 (`ipu3-cio2: payload length is 2585088, received 2588672`); `ov7251` was skipped by `libcamera`.
  - **Post-reboot verified (`7.2.15-1-omarchy`):** DKMS `surface-go3-cameras/0.1.0` modules (`ov8865`, `ov5693`, `ov7251`, `dw9719`) resolve under `updates/dkms/`; `cam -l` lists both `Internal back camera` and `Internal front camera`, `cam` live captures succeed on both cameras, and `wpctl status` exposes only the two labeled PipeWire `libcamera` sources with raw IPU3 nodes hidden.

### 2. USB-C / UCSI ([ucsi-acpi/](ucsi-acpi/))

- **DKMS driver override** (`surface-go3-ucsi-acpi/0.1.0` in [dkms.conf](ucsi-acpi/dkms.conf)):
  - [ucsi_acpi.c](ucsi-acpi/src/ucsi_acpi.c): DMI quirk for `Surface Go 2` / `Surface Go 3`:
    - Falls back to `UCSI_VERSION_1_1` (`0x0110`) when `VER1/VER2` reads `0x0000` (because `_DSM(READ)` never copies `VER1`/`VER2` from `EC0` into `UBTC`), and byte-swaps non-zero big-endian firmware versions.
    - Preserves `CCI_RESET_COMPLETE` when `_Q79` / `QUCM` clears `EC0.CCI0`/`CCI3` during PPM reset polling.
    - Drains lingering `UCSI_CCI_RESET_COMPLETE` in `EC0.CCI` (`msleep(30)` + `_DSM(READ)` up to 8 iterations) after `UCSI_PPM_RESET` so the immediately following `SET_NOTIFICATION_ENABLE` command is not stalled by the EC.
- **Status**:
  - **Post-reboot cold-boot & USB-C PD verified (`7.2.15-1-omarchy`):** updated DKMS `surface-go3-ucsi-acpi/0.1.0` auto-loaded cleanly from `updates/dkms/ucsi_acpi.ko.zst` (`srcversion 54404D360C085D541D6A94B`), bound `usb1-port1`/`usb2-port1`, and registered `port0`, `port0-partner`, `port0-cable`, `port0-plug0`, `port0.0` (`svid: ff01`), and `ucsi-source-psy-USBC000:001` (`usb_power_delivery`, `C [PD] PD_PPS`, `Charging` at `20 V` / `2.25 A`, `data_role: [host] device`, `power_role: source [sink]`).

### 3. Type Cover Touchpad ([hid-multitouch/](hid-multitouch/))

- **DKMS driver override** (`surface-go3-hid-multitouch/0.1.0` in [dkms.conf](hid-multitouch/dkms.conf)):
  - [hid-multitouch.c](hid-multitouch/src/hid-multitouch.c): adds `MT_QUIRK_NO_SUSPEND_MODES` and a specific `045e:09b5` device entry. `mt_suspend()`, `mt_resume()`, `mt_on_hid_hw_open()`, and `mt_on_hid_hw_close()` skip the mode-switch `SET_REPORT`s, and `mt_need_to_apply_feature()` skips `HID_DG_LATENCYMODE` (`rep=8`) and `HID_DG_SURFACESWITCH`/`HID_DG_BUTTONSWITCH` (`rep=16`), leaving only `HID_DG_INPUTMODE` (`rep=7`) on `mt_probe()` and USB `mt_reset_resume()`.
  - [hid-ids.h](hid-multitouch/src/hid-ids.h) and [hid-haptic.h](hid-multitouch/src/hid-haptic.h) are matching v7.2.15 headers required for the out-of-tree build.
  - [mkinitcpio.conf.d/zz-surface-go3-hid-multitouch.conf](hid-multitouch/mkinitcpio.conf.d/zz-surface-go3-hid-multitouch.conf): forces the DKMS `hid_multitouch` into the initramfs/UKI. The `zz-` prefix must sort after `surface_device_modules.conf`; a `99-` prefix is overwritten by that file's `MODULES=(...)`.
- **Status**:
  - **Post-reboot cold-boot & first-suspend verified (`7.2.15-1-omarchy`):** DKMS `surface-go3-hid-multitouch/0.1.0` (`srcversion 0C550FE4364C9359E8BD94C`, embedded in `/boot/EFI/Linux/omarchy_linux-omarchy.efi`, SHA-256 `0b151028…`) loaded cleanly on cold boot (`skipping mode changes during system suspend`), survived the first cold-boot `s2idle` cycle (`PM: suspend entry` -> `PM: suspend exit`) with zero `usbhid` / `usb_submit_urb(ctrl) failed: -1` / `suspend error -5` entries and `edgepad.service` active. Temporary `surface-go3-touchpad-resume.service` / `surface-go3-touchpad-recover` workaround removed.

### 4. Touchscreen ([i2c-hid/](i2c-hid/))
- **Problem**: `ELAN9038:00` (`04F3:2A1C`, I2C HID on `I2C1` at `0x10`, `GpioInt` pin `103`) stopped emitting touch reports after extended `s2idle` (`~7.7 h` overnight `SLP_S0`) while short suspends still worked. During the wedged state, the controller still answered I2C `HIDIOCGFEATURE` (`rid=10`), and an ephemeral `i2c_hid_acpi` `unbind`/`bind` (sending `SET_POWER(ON)` + `RESET`) immediately restored touch.
- **Root Cause**: Upstream `i2c-hid-core.c` matches `04F3:2A1C` via the wildcard `{ USB_VENDOR_ID_ELAN, HID_ANY_ID, I2C_HID_QUIRK_BOGUS_IRQ | I2C_HID_QUIRK_NO_WAKEUP_AFTER_RESET }`, which omits `I2C_HID_QUIRK_RESET_ON_RESUME`. On resume from deeper `s2idle` power states, `i2c_hid_core_resume()` only sends `SET_POWER(ON)` without a hardware `RESET`, leaving the ELAN touch controller non-reporting until `RESET` runs.
- **Fix (`i2c-hid/`)**: Added `{ USB_VENDOR_ID_ELAN, 0x2a1c, I2C_HID_QUIRK_NO_WAKEUP_AFTER_RESET | I2C_HID_QUIRK_BOGUS_IRQ | I2C_HID_QUIRK_RESET_ON_RESUME }` after the `HID_ANY_ID` entry in `i2c_hid_quirks[]` (so `i2c_hid_lookup_quirk()` selects the last matching entry) and packaged it as DKMS `surface-go3-i2c-hid/0.1.0`.
- **Status**:
  - **Installed and live-reloaded (`7.2.15-1-omarchy`):** `surface-go3-i2c-hid/0.1.0` (`srcversion D9C1DA0F275212672B82CB8`) installed to `/lib/modules/7.2.15-1-omarchy/updates/dkms/i2c-hid.ko.zst` (not in UKI initrd), loaded live, and bound `0018:04F3:2A1C.*` cleanly. Extended `s2idle` verification pending user test.

## Captures

Saved hardware/firmware inventories live in [captures/](captures/):
- [dmi.txt](captures/dmi.txt), [lspci.txt](captures/lspci.txt), [lsusb.txt](captures/lsusb.txt), [hda-codec0.txt](captures/hda-codec0.txt), [media-topology.txt](captures/media-topology.txt), [acpi-devices.txt](captures/acpi-devices.txt)
