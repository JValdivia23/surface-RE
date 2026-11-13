# Surface Linux Reverse Engineering (`surface-RE`)

Central repository for hardware, firmware, and Windows driver reverse-engineering (RE) documentation, kernel patches, DKMS modules, and diagnostic tools for Microsoft Surface devices running Linux (`linux-surface` / Arch / CachyOS).

---

## Repository Structure

```text
surface-RE/
├── README.md                         # Overview, upstream PR tracker, and multi-device index
├── tools/                            # Cross-device SSAM, UCSI-over-HID, and .cfu firmware tools
└── devices/
    ├── README.md                     # Checklist & template for adding another Surface device
    └── surface-book-3/               # Surface Book 3 (13.5", Ice Lake) RE reports, DKMS, patches
        ├── camera-ipu4p/             # Intel IPU4P cameras RE report, .cany evidence, folded DKMS
        ├── usbc-charger/             # UCSI-over-HID & surface_charger patches, DKMS, captures
        ├── standby-kip/              # Modern Standby (s2idle) & KIP MCU power management RE
        └── rtc-integration/          # SAM EC RTC & SurfaceIntegrationDriver RE
```

> **Local-only files (`.gitignore`d):** Proprietary Microsoft Windows driver/firmware dumps (`SurfaceUpdate/`, `windows-drivers/`, `*.msi`, `*.sys`, `*.cfu`), cloned upstream reference trees (`local/`), and kernel build outputs (`*.ko`, `*.o`, `*.cmd`) stay strictly local and are never committed.

---

## Devices

| Device | Directory | Highlights |
| :--- | :--- | :--- |
| Surface Book 3 (13.5", i5-1035G7) | [`devices/surface-book-3/`](devices/surface-book-3/) | IPU4P 3-camera stack, USB-C UCSI-over-HID + `Artg` charger rating, `s2idle`/KIP MCU RE, SAM EC RTC sync |

See [`devices/README.md`](devices/README.md) for the workflow when cloning this repo onto another Surface device.

---

## Upstream PRs & Related Repos

| Contribution | Target Repo / Branch | Status |
| :--- | :--- | :--- |
| Surface UCSI-over-HID driver (`ucsi_surface_hid`) & `surface_charger` `Artg` power rating | [`linux-surface/kernel#184`](https://github.com/linux-surface/kernel/pull/184) (`JValdivia23/kernel:surface-ucsi-hid`) | Open |
| Enable `CONFIG_UCSI_SURFACE_HID` in `configs/7.3` | [`linux-surface/linux-surface#2354`](https://github.com/linux-surface/linux-surface/pull/2354) (`JValdivia23/linux-surface:surface-ucsi-hid-config`) | Open |
| Surface Book 3 IPU4P CSI-2 & `libcamera` tuning fork | [`JValdivia23/ipu4-drivers`](https://github.com/JValdivia23/ipu4-drivers/tree/surface-book3-support) (`surface-book3-support`) | Active fork |
| Reference SB3 IPU4P PR | [`linux-surface/linux-surface#2289`](https://github.com/linux-surface/linux-surface/pull/2289) (`@popos123`) | Open |
| Runtime Surface scripts & systemd hooks | [`JValdivia23/hyprland-dots`](https://github.com/JValdivia23/hyprland-dots) (`profiles/surface/`) | Active |

---

## Quick Start on a New Surface Machine

```bash
git clone https://github.com/JValdivia23/surface-RE.git
cd surface-RE
```

1. Review [`tools/README.md`](tools/README.md) for reusable SSAM / UCSI / `.cfu` utilities.
2. Follow [`devices/README.md`](devices/README.md) to create `devices/<your-surface>/` and keep any extracted Windows MSI drivers inside `SurfaceUpdate/` or `local/` (gitignored).
