#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOGFILE="${WORKDIR}/module_test.log"
START_TS="$(date '+%Y-%m-%d %H:%M:%S')"

exec > >(tee "${LOGFILE}") 2>&1

echo "=== Surface UCSI-over-HID & Charger Artg Live Kernel Verification ==="
echo "Kernel: $(uname -r)"
echo "Started: ${START_TS}"

cleanup() {
    echo ""
    echo "=== Cleanup / Restoring Stock Modules ==="
    if [ -d /sys/module/ucsi_surface_hid ]; then
        rmmod ucsi_surface_hid && echo "Unloaded ucsi_surface_hid.ko cleanly"
    fi
    if [ -d /sys/module/surface_charger ]; then
        rmmod surface_charger || true
    fi
    modprobe surface_charger && echo "Restored stock surface_charger module"
    sleep 1
    echo "--- Post-cleanup HID bindings ---"
    for d in /sys/bus/hid/devices/0019:045E:*; do
        [ -e "$d" ] || continue
        drv="$(basename "$(readlink -f "$d/driver" 2>/dev/null || echo none)")"
        hr="$(ls "$d/hidraw" 2>/dev/null | tr '\n' ' ' || true)"
        echo "  $(basename "$d"): driver=${drv} hidraw=[${hr}]"
    done
    echo "--- Post-cleanup ADP1 uevent ---"
    cat /sys/class/power_supply/ADP1/uevent || true
}
trap cleanup EXIT

echo ""
echo "=== 1. Baseline HID & ADP1 State ==="
for d in /sys/bus/hid/devices/0019:045E:*; do
    [ -e "$d" ] || continue
    drv="$(basename "$(readlink -f "$d/driver" 2>/dev/null || echo none)")"
    hr="$(ls "$d/hidraw" 2>/dev/null | tr '\n' ' ' || true)"
    echo "  $(basename "$d"): driver=${drv} hidraw=[${hr}]"
done
cat /sys/class/power_supply/ADP1/uevent || true

echo ""
echo "=== 2. Loading typec_ucsi + ucsi_surface_hid.ko ==="
modprobe typec_ucsi
modprobe typec_displayport 2>/dev/null || true
insmod "${WORKDIR}/dkms/ucsi-surface-hid/ucsi_surface_hid.ko"

echo "Waiting 3s for async UCSI PPM initialization..."
sleep 3

echo ""
echo "=== 3. HID Bindings While ucsi_surface_hid Is Loaded ==="
for d in /sys/bus/hid/devices/0019:045E:*; do
    [ -e "$d" ] || continue
    drv="$(basename "$(readlink -f "$d/driver" 2>/dev/null || echo none)")"
    hr="$(ls "$d/hidraw" 2>/dev/null | tr '\n' ' ' || true)"
    echo "  $(basename "$d"): driver=${drv} hidraw=[${hr}]"
done

echo ""
echo "=== 4. /sys/class/typec Inspection ==="
ls -la /sys/class/typec/ || true
for p in /sys/class/typec/port*; do
    [ -e "$p" ] || continue
    echo "--- $(basename "$p") ---"
    for attr in data_role power_role port_type preferred_role usb_typec_revision usb_power_delivery_revision supported_accessory_modes orientation uevent; do
        if [ -r "$p/$attr" ]; then
            val="$(tr '\n' ' ' < "$p/$attr")"
            echo "  ${attr}: ${val}"
        fi
    done
done

echo ""
echo "=== 5. UCSI Power Supply Inspection ==="
for psy in /sys/class/power_supply/ucsi-source-psy-*; do
    [ -e "$psy" ] || continue
    echo "--- $(basename "$psy") ---"
    cat "$psy/uevent" || true
done

echo ""
echo "=== 6. Loading Patched surface_charger.ko (CID=0x0F Artg) ==="
if [ -d /sys/module/surface_charger ]; then
    rmmod surface_charger
fi
insmod "${WORKDIR}/dkms/surface-charger/surface_charger.ko"
sleep 1

echo "--- Patched ADP1 initial uevent ---"
cat /sys/class/power_supply/ADP1/uevent || true
echo "input_power_limit: $(cat /sys/class/power_supply/ADP1/input_power_limit 2>&1 || true)"

echo ""
echo "=== 7. Optional 10s Live Plug Window ==="
echo "(Optional: plug in Surface Connect charger or a USB-C device now to capture live Artg / port0-partner; sampling for 10s...)"
for i in $(seq 1 10); do
    online="$(cat /sys/class/power_supply/ADP1/online 2>/dev/null || echo '?')"
    limit="$(cat /sys/class/power_supply/ADP1/input_power_limit 2>/dev/null || echo '?')"
    partner="$(ls -d /sys/class/typec/port0-partner 2>/dev/null || true)"
    echo "  [t=${i}s] ADP1 online=${online} input_power_limit=${limit} uW | partner=${partner:-none}"
    if [ -n "${partner}" ] && [ -r "${partner}/uevent" ]; then
        sed 's/^/    partner: /' "${partner}/uevent" || true
    fi
    sleep 1
done

echo ""
echo "=== 8. Kernel Log (dmesg since ${START_TS}) ==="
journalctl -k --since "${START_TS}" --no-pager || dmesg | tail -n 50

TARGET_USER="${SUDO_USER:-$USER}"
chown "${TARGET_USER}:${TARGET_USER}" "${LOGFILE}" 2>/dev/null || true
echo ""
echo "=== Verification Complete ==="
