# Microsoft Surface Book 3 (13.5", Intel Core i5-1035G7 / Ice Lake)

Consolidated reverse-engineering reports, kernel patches, DKMS modules, and hardware diagnostics for the **Surface Book 3 (13.5", Ice Lake)** on CachyOS / Arch Linux (`linux-surface`).

---

## Subprojects

### 1. [`camera-ipu4p/`](camera-ipu4p/) — Intel IPU4P Cameras (`8086:8a19` / `8086:9a19`)
- **Problem:** Stock `linux-surface` / upstream `intel-ipu6` lacked Ice Lake IPU4P CSI-2 PHY building-block mappings, `ACCINV = 16` D-PHY timing, 2-lane vs. 4-lane `DLY_CNT` register offsets, and `INT3472` discrete regulator/VCM (`dw9719`) glue for all 3 internal cameras (Front RGB `OV5693`, Rear RGB `OV8865` + `DW9719`, Front IR `OV7251`).
- **Artifacts:**
  - [`camera-ipu4p/CRAFT-ANYTHING-RE-REPORT.md`](camera-ipu4p/CRAFT-ANYTHING-RE-REPORT.md) — Clean-room RE report from `iacamera64.sys`, `iactrllogic64.sys`, and sensor `.sys`/`.aiqb` files.
  - [`camera-ipu4p/sb3-ipu4p.cany/`](camera-ipu4p/sb3-ipu4p.cany/) — Structured Craft Anything evidence/annotation project.
  - [`camera-ipu4p/dkms/`](camera-ipu4p/dkms/) — Folded `surface-ipu4p` (`0.2.0`) DKMS module + `libcamera` IPA tuning (`ov5693.yaml`, `ov7251.yaml`, calibrated 7-illuminant `ov8865.yaml`) + WirePlumber rule (`52-surface-ipu4p.conf`).
- **Upstream / Reference Repos:**
  - Fork: [`JValdivia23/ipu4-drivers`](https://github.com/JValdivia23/ipu4-drivers) (`surface-book3-support`)
  - Reference PR: [`linux-surface/linux-surface#2289`](https://github.com/linux-surface/linux-surface/pull/2289)

### 2. [`usbc-charger/`](usbc-charger/) — USB-C UCSI-over-HID (`TC = 0x16`) & AC Charger (`TC = 0x02`)
- **Problem:**
  1. `ACPI\MSHW0134` / SSAM HID `0019:045E:09A6` (`SurfaceUcsiHidDriver.sys`) had no Linux `typec_ucsi` transport driver, leaving `/sys/class/typec/` and USB-C PD power-supply telemetry missing.
  2. `surface_charger` did not expose `POWER_SUPPLY_PROP_INPUT_POWER_LIMIT` (`CID = 0x0f` `Artg` in mW) and had a positive return value bug in `spwr_ac_get_property()`.
- **Artifacts:**
  - [`usbc-charger/patches/`](usbc-charger/patches/) — 4 upstream kernel patches (`ucsi_surface_hid`, `HID_GROUP_SURFACE_UCSI`, and `surface_charger` fixes/Artg).
  - [`usbc-charger/dkms/`](usbc-charger/dkms/) — Standalone DKMS/module sources (`ucsi-surface-hid/` and `surface-charger/`).
  - [`usbc-charger/scripts/`](usbc-charger/scripts/) — Live module verification (`test_modules.sh`) & DKMS installer wrapper (`run_dkms_install.sh`).
  - [`usbc-charger/captures/ucsi-hid-diag-sb3.log`](usbc-charger/captures/ucsi-hid-diag-sb3.log) — Live UCSI + SSAM diagnostic capture generated with [`tools/ucsi_hid_diag.py`](../../tools/ucsi_hid_diag.py).
- **Upstream PRs:**
  - [`linux-surface/kernel#184`](https://github.com/linux-surface/kernel/pull/184) (open)
  - [`linux-surface/linux-surface#2354`](https://github.com/linux-surface/linux-surface/pull/2354) (open)

### 3. [`standby-kip/`](standby-kip/) — Modern Standby (`s2idle`) & Keyboard Base (`KIP` MCU) Power Management
- **Artifacts:**
  - [`standby-kip/STANDBY-KIP-RE-REPORT.md`](standby-kip/STANDBY-KIP-RE-REPORT.md) — Disassembly findings from `SurfaceService.exe` (`DynamicStandbyBudget::MsBudgetSelect`, showing Windows uses `s2idle` + 5%–20% battery-budget hibernate) and `SurfaceHotPlug.sys` / `SurfaceBookBaseV3_KIP_APP1.cfu` (`TC = 0x0e, TID = 0x02, CID = 0x0a / 0x0b` display-off/on KIP backlight & wake-lock control).
  - Firmware unwrap helper: [`tools/unwrap_kip_cfu.py`](../../tools/unwrap_kip_cfu.py).

### 4. [`rtc-integration/`](rtc-integration/) — SAM EC Real-Time Clock (`TC = 0x01`) & Integration Driver (`MSHW0117`)
- **Artifacts:**
  - [`rtc-integration/SAM-RTC-INTEGRATION-RE-REPORT.md`](rtc-integration/SAM-RTC-INTEGRATION-RE-REPORT.md) — Analysis of `SurfaceTimeAlarmAcpiFilter.sys` (`ACPI000E` `_GRT`/`_SRT` mapped to SSAM `TC = 0x01, TID = 0x01, CID = 0x1f/0x20`), UEFI CMOS sync on S5 hibernation wake, and `SurfaceIntegrationDriver.sys` (`TC = 0x04, CID = 0x03` critical battery emergency hibernate).
  - CLI inspection/sync tool: [`tools/surface-sam-rtc.py`](../../tools/surface-sam-rtc.py).
