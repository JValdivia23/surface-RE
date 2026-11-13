# Cross-Device Surface RE & Diagnostic Tools

Reusable diagnostic, SSAM, and firmware-inspection scripts that apply across multiple Microsoft Surface models.

## Tools

- [`surface-sam-rtc.py`](surface-sam-rtc.py)
  - Inspects and synchronizes the Surface Aggregator Module (SAM) EC Real-Time Clock via `/dev/surface/aggregator` (`TC = 0x01`, `TID = 0x01`, `IID = 0x00`, `CID = 0x1f` / `0x20`).
  - Subcommands: `status`, `sync-utc`, `selftest`.
- [`ucsi_hid_diag.py`](ucsi_hid_diag.py)
  - Clean-room UCSI-over-HID (`hidraw` Feature/Input reports `0x32` & `0x33`) and SSAM `TC = 0x02` power/HPMI telemetry diagnostic tool.
  - Writes `diag.log` and `diag.done` to `SURFACE_DIAG_LOG_DIR` (defaults to the current working directory).
- [`unwrap_kip_cfu.py`](unwrap_kip_cfu.py)
  - Unwraps Microsoft `.cfu` (Component Firmware Update) payloads (defaulting to `SurfaceUpdate/surfacebookbasev3fwupdate/SurfaceBookBaseV3_KIP_APP1.cfu` if present locally) into a raw flash binary and an ARM Cortex-M Thumb-2 ELF for inspection with `llvm-objdump`.
