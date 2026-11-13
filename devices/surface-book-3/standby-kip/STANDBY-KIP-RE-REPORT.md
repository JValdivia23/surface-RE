# Surface Book 3 Modern Standby (`s2idle`) & KIP Base Firmware Reverse Engineering Report

**Date:** 2026-11-11  
**Target Hardware:** Microsoft Surface Book 3 (13.5", Intel Core i5-1035G7, Ice Lake)  
**Target Firmware & Drivers Analyzed:**
- [`SurfaceService.exe`](../SurfaceUpdate/servicenull/SurfaceService.exe) & [`SurfaceIntegrationDriver.inf`](../SurfaceUpdate/surfaceintegration/SurfaceIntegrationDriver.inf) (`ACPI\MSHW0117`)
- [`SurfaceHotPlug.sys`](../SurfaceUpdate/hotplug/SurfaceHotPlug.sys) & [`SurfaceHotPlug.inf`](../SurfaceUpdate/hotplug/SurfaceHotPlug.inf) (`ACPI\MSHW0153` — `; Kip Device`)
- [`SurfaceSerialHubDriver.sys`](../SurfaceUpdate/surfaceserialhub/SurfaceSerialHubDriver.sys) (`ACPI\MSHW0084` — SSAM Bus)
- [`SurfaceBookBaseV3_KIP_APP1.cfu`](../SurfaceUpdate/surfacebookbasev3fwupdate/SurfaceBookBaseV3_KIP_APP1.cfu) (Keyboard Base `KIP` ARM Cortex-M MCU Firmware, `TID = 0x02`, Flash Base `0x0000f994`)

---

## 1. Executive Summary

A common observation among Surface Book 3 users is that Windows appears to have lower battery drain when the lid is closed compared to default Linux installations. Reverse engineering the Windows driver stack and MCU firmware revealed two distinct mechanisms behind this behavior:

1. **Windows Secretly Uses `suspend-then-hibernate` (`DynamicStandbyBudget`):**
   - Surface Book 3 does **not** support S3 sleep on either Windows or Linux; both operating systems use `S0ix` Modern Standby (`s2idle`).
   - However, [`SurfaceService.exe`](../SurfaceUpdate/servicenull/SurfaceService.exe) implements `DynamicStandbyBudget::MsBudgetSelect` (`0x140037d10`), which dynamically programs Windows's `STANDBY_BUDGET_PERCENT` and `HIBERNATEIDLE` timeouts so that the laptop automatically transitions from `s2idle` to zero-power hibernation (`S4`) after consuming **5%–20% battery** (or after **18–72 hours**).
   - Default Linux distributions remain in `s2idle` indefinitely until the battery reaches 0%. A Linux system configured with `suspend-then-hibernate` (`HibernateDelaySec=30min`) actually enters zero-drain hibernation **faster** and wastes **less** battery than Windows's native `DynamicStandbyBudget`.

2. **Undocumented KIP Base Display-Off / Sleep Notification (`TC = 0x0e, TID = 0x02, CID = 0x0a / 0x0b`):**
   - On the Surface Book 3 SSAM bus, `TID = 0x01` is the tablet SAM EC and `TID = 0x02` is the Keyboard Base (`KIP`) MCU.
   - Both Windows ([`SurfaceSerialHubDriver.sys`](../SurfaceUpdate/surfaceserialhub/SurfaceSerialHubDriver.sys)) and Linux (`surface_aggregator`) send `TC = 0x01, TID = 0x01, CID = 0x15/0x16` (`DisplayOff`/`On`) and `CID = 0x33/0x34` (`D0Exit`/`Entry`) to **SAM (`TID = 0x01`)**.
   - However, Windows's [`SurfaceHotPlug.sys`](../SurfaceUpdate/hotplug/SurfaceHotPlug.sys) (`ACPI\MSHW0153`) also sends `TC = 0x0e, TID = 0x02, CID = 0x0a` (`DISPLAY_STATE_OFF`) and `CID = 0x0b` (`DISPLAY_STATE_ON`) directly to the **KIP MCU (`TID = 0x02`)** whenever the screen turns off or the lid closes. Linux's `surface_hotplug` driver has no PM callbacks and never sends these commands.
   - Disassembling [`SurfaceBookBaseV3_KIP_APP1.cfu`](../SurfaceUpdate/surfacebookbasev3fwupdate/SurfaceBookBaseV3_KIP_APP1.cfu) shows that `CID = 0x0a` immediately fades out the keyboard backlight + CapsLock/Fn LEDs, disables the backlight boost regulator (GPIO `0xab`), and releases bit `0x20000` in the KIP MCU deep-sleep inhibit bitmask (`[0x2001be30]`). Without `CID = 0x0a`, the KIP MCU waits for its built-in **20-second hardware inactivity timer** (`0x4e20 = 20,000 ms` at `0x00037b9e`) before entering the same state.

---

## 2. Windows `DynamicStandbyBudget` (`SurfaceService.exe`)

In [`SurfaceService.exe`](../SurfaceUpdate/servicenull/SurfaceService.exe), `DynamicStandbyBudget::MsBudgetSelect` (`VA = 0x140037d10..0x140037fcc`) is invoked when entering Modern Standby (`s2idle`). It reads the current battery percentage (`%r8d`) and calls `PowerWriteDCValueIndex` (`0x1400383a0`) to program the active Windows Power Scheme:

| Battery Threshold (`%r8d`) | `STANDBY_BUDGET_PERCENT` | `HIBERNATEIDLE` Timeout (Seconds) | `HIBERNATEIDLE` Timeout (Hours) |
|---|---|---|---|
| **$\ge 70\%$ (`0x46`)** | `0x14` (**20%**) | `0x3f480` (`259,200s`) | **72 hours** |
| **$40\%–69\%$ (`0x28`)** | `0x0a` (**10%**) | `0x1fa40` (`129,600s`) | **36 hours** |
| **$10\%–39\%$ (`0x0a`)** | `0x05` (**5%**) | `0x0fd20` (`64,800s`) | **18 hours** |
| **$1\%–9\%$ (`0x01`)** | `0x00` (**0%**) | `0x0003c` (`60s`) | **1 minute** |

Additionally, `SamHibernateNotification` (`VA = 0x14004c6ad`) listens for SAM EC low-battery notifications (`RSOC < 3%`) and forces immediate emergency hibernation.

---

## 3. Windows `SurfaceHotPlug.sys` KIP Display State Notifications

In [`SurfaceHotPlug.sys`](../SurfaceUpdate/hotplug/SurfaceHotPlug.sys) (`ACPI\MSHW0153`), device initialization (`VA = 0x1400073c7`) calls `PoRegisterPowerSettingCallback` with:
- **SettingGuid:** `GUID_CONSOLE_DISPLAY_STATE` (`6fe69556-704a-47a0-8f24-c28d936fda47` at `0x14005c380`)
- **Callback:** `SurfaceHotPlugPowerSettingCallback` (`VA = 0x14006e410`)

Inside `SurfaceHotPlugPowerSettingCallback` (`0x14006e51a..0x14006e615`):
- When `DisplayState == 0` (**Display Off / Lid Closed**):
  - Sends synchronous SSAM command:
    - `TC = 0x0e` (`SSAM_SSH_TC_KIP`)
    - `TID = 0x02` (`KIP` MCU)
    - `SID = 0x00`
    - `CID = 0x0a` (`DISPLAY_STATE_OFF`)
    - `IID = 0x00`, `Payload = []` (0 bytes)
- When `DisplayState != 0` (**Display On / Dimmed**):
  - Sends synchronous SSAM command:
    - `TC = 0x0e` (`SSAM_SSH_TC_KIP`)
    - `TID = 0x02` (`KIP` MCU)
    - `SID = 0x00`
    - `CID = 0x0b` (`DISPLAY_STATE_ON`)
    - `IID = 0x00`, `Payload = []` (0 bytes)

### Bonus: Battery Saver Keyboard Backlight Lockout (`TC = 0x17, TID = 0x02, CID = 0x01`)
In [`SurfaceIntegrationDriver.inf`](../SurfaceUpdate/surfaceintegration/SurfaceIntegrationDriver.inf) (line 53):
```ini
HKR,,PlatformKipSupported,0x00010001,1 ; KIP is supported on this platform (it enables power saving features in KIP)
```
When `PlatformKipSupported == 1`, `SSHUtility::SetKeyboardBackLightMode` (`VA = 0x140044260` in [`SurfaceService.exe`](../SurfaceUpdate/servicenull/SurfaceService.exe)) sends:
- `TC = 0x17` (`BKL`), `TID = 0x02` (`KIP`), `CID = 0x01`, `IID = 0x00`, `Payload = [0x01]` when entering Battery Saver mode (disabling keyboard backlight even on keypress), and `Payload = [0x00]` when leaving Battery Saver mode.
- In KIP firmware, this is handled at `0x00037e1c` (`CID = 0x01` in `0x00037d54`) $\rightarrow$ `0x00037d0c(mode)`.

---

## 4. KIP MCU Firmware Disassembly (`SurfaceBookBaseV3_KIP_APP1.cfu`)

Unwrapping [`SurfaceBookBaseV3_KIP_APP1.cfu`](../SurfaceUpdate/surfacebookbasev3fwupdate/SurfaceBookBaseV3_KIP_APP1.cfu) (see [`unwrap_kip_cfu.py`](unwrap_kip_cfu.py)) yields a 394,860-byte ARM Cortex-M Thumb-2 firmware image loaded at Flash address **`0x0000f994`** (`SP = 0x200237d8`, `Reset_Handler = 0x0000fa95`).

### 4.1 `TC = 0x0e` Command Dispatcher (`0x0002c592`)
| Address | `CID` | Action in KIP Firmware |
|---|---|---|
| `0x0002c7a2` | **`0x0a`** (`DISPLAY_STATE_OFF`) | Logs trace `0x344`, calls `0x00037c74(1)` $\rightarrow$ sets RTOS event bit **`0x08`** on `[0x20000044]` |
| `0x0002c7b8` | **`0x0b`** (`DISPLAY_STATE_ON`) | Logs trace `0x345`, calls `0x00037c74(0)` $\rightarrow$ sets RTOS event bit **`0x01`** on `[0x20000044]` |
| `0x0002c730` | **`0x1d`** | Calls `0x0002caba(r0)` — updates KIP host power state `[0x20002444]` and notifies registered callbacks |
| `0x0002c738` | **`0x27`** | Enables KIP subsystem notifications (`0x0002c832(1, ...)`) |
| `0x0002c74c` | **`0x28`** | Disables KIP subsystem notifications (`0x0002c832(0, ...)`) |
| `0x0002c7da` | **`0x34`** | Host RTC timestamp sync (`0x0002661a`) |

### 4.2 KIP Illumination & Peripheral Power State Machine (`0x000376fc` / `0x00037786`)
The RTOS thread at `0x000376fc` waits on event group `[0x20000044]` (`[0x00037f5c]`) and dispatches `0x00037786`:
- **When `CID = 0x0a` (`DISPLAY_STATE_OFF`, event bit `0x08`) is received (`0x000378ca` & `0x00037a44`):**
  1. Cancels the active timers (`[0x2000005c]`, `[0x20000048]`) and clears brightness state (`0x0003c496` / `0x00037aec` $\rightarrow$ State `4`).
  2. If the state machine (`[0x20002438]`) is not already in State `3` (Off), calls `0x00037bee(0)` to immediately fade out all three PWM controllers:
     - `0x0003c234`: Keyboard Backlight PWM (`[r0, #0x24]`) + GPIO `0xab` boost regulator enable (`0x0003c59c`)
     - `0x0003c2c0`: CapsLock LED PWM (`[r0, #0x1c]`)
     - `0x0003c340`: Fn LED PWM (`[r0, #0x20]`)
  3. Transitions `[0x20002438]` to **State `3` (Off/Asleep)** (`0x00037bb6`).
  4. At `0x00037a56`:
     ```armasm
     37a50: ldrb   r0, [r7]          @ [0x20002438] state
     37a52: cmp    r0, #0x3          @ State == 3 (Off)?
     37a54: bne    0x37a60
     37a56: movs.w r0, #0x20000
     37a5a: bl     0x1c170           @ Clear bit 0x20000 in [0x2001be30] (allow MCU deep sleep!)
     37a5e: b      0x37a68
     37a60: movs.w r0, #0x20000
     37a64: bl     0x1c156           @ Set bit 0x20000 in [0x2001be30] (inhibit MCU deep sleep!)
     ```
- **When `CID = 0x0b` (`DISPLAY_STATE_ON`, event bit `0x01`) is received (`0x000378d4` & `0x00037938`):**
  1. Checks if the keyboard base is attached in laptop posture (`0x0003775e`: `[0x2001f86a] == 0 && [0x2001f86b] == 2`).
  2. Calls `0x00037b12(0)` to immediately restore Keyboard Backlight, CapsLock LED, and Fn LED PWM levels and re-arm the 20-second (`0x4e20` ms) inactivity timer (`0x00037b6e`).

### 4.3 KIP MCU Deep-Sleep Manager (`0x0001bfde..0x0001c196` & `0x000244e0..0x00024580`)
- `[0x2001be30]` (`[0x0001c1f0]`) is the KIP MCU's 32-bit **Sleep Inhibit / Wake-Lock Bitmask**:
  - `0x0001c156(mask)`: Atomically sets bits in `[0x2001be30]` (`cpsid i; orrs; msr primask`).
  - `0x0001c170(mask)`: Atomically clears bits in `[0x2001be30]` (`cpsid i; bics; msr primask`).
  - `0x0001c18a()`: Reads `[0x2001be30]`.
- In the MCU idle loop (`0x0001bfde..0x0001c080`), the firmware checks 4 conditions before entering low-power mode (`0x000244e0` / `0x0002451c`):
  1. Next timer deadline (`0x0001c190`).
  2. Wake-lock bitmask `0x0001c18a() == 0` (`0x0001c00e`).
  3. Subsystem sleep callbacks at `0x0001c0f4`.
  4. Hardware status bit at `0x00024464`.
- Once all bits in `[0x2001be30]` are `0`, `0x000244e0` and `0x0002451c` gate the MCU peripheral clocks and enter deep sleep (`wfi` / stop mode).
