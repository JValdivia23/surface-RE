# Supported & In-Progress Surface Devices

Each Microsoft Surface model gets its own directory under `devices/<device-slug>/` so hardware-specific findings, ACPI/SSAM captures, kernel patches, and DKMS work stay isolated while sharing [`tools/`](../tools/).

| Device | Directory | SoC / Generation | Status |
| :--- | :--- | :--- | :--- |
| Surface Book 3 (13.5") | [`surface-book-3/`](surface-book-3/) | Intel Core i5-1035G7 (Ice Lake) | Cameras (IPU4P), USB-C PD + Charger (`Artg`), Standby/KIP RE, SAM RTC sync |

---

## Adding Another Surface Device

When you clone this repo onto another Surface machine to start reverse-engineering or hardware bring-up:

1. **Create a new device directory** (e.g. `devices/surface-pro-9/` or `devices/surface-laptop-5/`):
   ```bash
   mkdir -p devices/<device-slug>/{docs,captures,patches,dkms}
   ```
2. **Keep Windows driver/firmware dumps local-only:**
   - Extract any official Microsoft `.msi` / driver packages into `SurfaceUpdate/` or `local/` at the repo root — both are `.gitignore`d so proprietary `.sys`/`.bin`/`.cfu`/`.msi` files are never committed.
3. **Collect baseline Linux hardware info** into `devices/<device-slug>/captures/`:
   - `dmidecode -t system` / `/sys/class/dmi/id/product_name`
   - `lspci -nnk` & `lsusb`
   - `/sys/bus/surface_aggregator/devices/` & `/sys/bus/hid/devices/`
   - `acpidump` / disassembled DSDT/SSDT notes (if diagnosing missing ACPI/SSAM devices)
4. **Reuse [`tools/`](../tools/)** for SSAM RTC, UCSI-over-HID, or `.cfu` firmware checks, and add a `devices/<device-slug>/README.md` summarizing findings and any upstream PRs.
5. **Update the table above** and the root [`README.md`](../README.md).
