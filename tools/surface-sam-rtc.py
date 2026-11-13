#!/usr/bin/env python3
"""
surface-sam-rtc.py - Microsoft Surface SAM EC Real-Time Clock Tool

Reads and synchronizes the Surface Aggregator Module (SAM) EC Real-Time Clock
via the Surface Serial Hub (SSH / SSAM) character device (/dev/surface/aggregator).

Protocol Reference:
- Target Category: TC = 0x01 (Core/System / RTC)
- Target ID:       TID = 0x01 (Clipboard SAM EC)
- Instance ID:     IID = 0x00
- Commands:
    CID = 0x1f: Get Real Time (_GRT) -> returns 16-byte ACPI_TIME buffer
    CID = 0x20: Set Real Time (_SRT) -> accepts 16-byte ACPI_TIME buffer
"""

from __future__ import annotations

import argparse
import calendar
import ctypes
import datetime
import errno
import fcntl
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Linux ioctl macros (x86_64)
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(dir_bits: int, type_ch: int, nr: int, size: int) -> int:
    return (dir_bits << 30) | (size << 16) | (type_ch << 8) | nr


# SSAM Character Device Request IOCTL definition:
# struct ssam_cdev_request {
#     __u8 target_category;
#     __u8 target_id;
#     __u8 command_id;
#     __u8 instance_id;
#     __u16 flags;
#     __s16 status;
#     __u64 payload_ptr;
#     __u16 payload_len;
#     __u8 reserved[6];
#     __u64 response_ptr;
#     __u16 response_max_len;
#     __u8 reserved2[6];
# } __attribute__((packed));  // Total size: 40 bytes
SSAM_CDEV_REQUEST_HAS_RESPONSE = 0x01
SSAM_CDEV_REQUEST_IOCTL = _IOC(_IOC_WRITE | _IOC_READ, 0xA5, 1, 40)

# SAM EC RTC Target Constants
SSAM_TC_CORE = 0x01
SSAM_TID_SAM = 0x01
SSAM_IID_DEFAULT = 0x00
SSAM_CID_RTC_GET = 0x1F  # _GRT: Get Real Time
SSAM_CID_RTC_SET = 0x20  # _SRT: Set Real Time

# ACPI_TIME Wire Format (16 bytes):
# uint16_t Year, uint8_t Month, uint8_t Day, uint8_t Hour, uint8_t Minute, uint8_t Second,
# uint8_t Valid, uint16_t Milliseconds, int16_t TimeZone, uint8_t Daylight, uint8_t Pad0, Pad1, Pad2
ACPI_TIME_STRUCT = struct.Struct("<HBBBBBBHhBBBB")
ACPI_TIME_LEN = 16

DEVICE_NODE = Path("/dev/surface/aggregator")
RTC0_SYSFS = Path("/sys/class/rtc/rtc0/since_epoch")


class SsamDeviceError(Exception):
    """Raised when SSAM device access or command execution fails."""


class SsamClient:
    """Interface to /dev/surface/aggregator for synchronous SSAM requests."""

    def __init__(self, dev_path: Path = DEVICE_NODE, auto_modprobe: bool = True):
        self.dev_path = dev_path
        self.fd: int = -1

        if not self.dev_path.exists() and auto_modprobe and os.geteuid() == 0:
            try:
                subprocess.run(
                    ["modprobe", "surface_aggregator_cdev"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for _ in range(5):
                    if self.dev_path.exists():
                        break
                    time.sleep(0.1)
            except OSError:
                pass

        if not self.dev_path.exists():
            raise SsamDeviceError(
                f"Device {self.dev_path} not found. Is 'surface_aggregator_cdev' loaded?"
            )

        try:
            self.fd = os.open(str(self.dev_path), os.O_RDWR | os.O_CLOEXEC)
        except OSError as exc:
            raise SsamDeviceError(f"Failed to open {self.dev_path}: {exc}") from exc

    def close(self) -> None:
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1

    def __enter__(self) -> SsamClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def request(
        self,
        tc: int,
        tid: int,
        cid: int,
        iid: int = 0,
        payload: bytes = b"",
        has_response: bool = True,
        resp_cap: int = 64,
    ) -> bytes:
        """Sends a synchronous command to SAM and returns response payload."""
        if self.fd < 0:
            raise SsamDeviceError("SSAM client is not open")

        pay_buf = (ctypes.c_ubyte * max(len(payload), 1))()
        for i, b in enumerate(payload):
            pay_buf[i] = b

        resp_buf = (ctypes.c_ubyte * max(resp_cap, 1))()
        pay_ptr = ctypes.addressof(pay_buf) if payload else 0
        resp_ptr = ctypes.addressof(resp_buf) if has_response else 0
        flags = SSAM_CDEV_REQUEST_HAS_RESPONSE if has_response else 0

        req = bytearray(
            struct.pack(
                "<BBBBHhQH6xQH6x",
                tc,
                tid,
                cid,
                iid,
                flags,
                0,
                pay_ptr,
                len(payload),
                resp_ptr,
                resp_cap if has_response else 0,
            )
        )

        try:
            fcntl.ioctl(self.fd, SSAM_CDEV_REQUEST_IOCTL, req, True)
        except OSError as exc:
            raise SsamDeviceError(
                f"SSAM IOCTL failed (tc={hex(tc)}, tid={hex(tid)}, cid={hex(cid)}): {exc}"
            ) from exc

        _, _, _, _, _, status, _, _, _, out_len = struct.unpack(
            "<BBBBHhQH6xQH6x", req
        )
        if status != 0:
            raise SsamDeviceError(
                f"SSAM request returned error status {status} (tc={hex(tc)}, tid={hex(tid)}, cid={hex(cid)})"
            )

        if not has_response:
            return b""
        return bytes(resp_buf[:out_len])


def unpack_acpi_time(data: bytes) -> dict[str, Any]:
    """Unpacks a 16-byte ACPI_TIME buffer into a dictionary and epoch timestamp."""
    if len(data) < ACPI_TIME_LEN:
        raise ValueError(
            f"Expected at least {ACPI_TIME_LEN} bytes for ACPI_TIME, got {len(data)}"
        )

    (
        year,
        month,
        day,
        hour,
        minute,
        second,
        valid,
        milliseconds,
        timezone,
        daylight,
        pad0,
        pad1,
        pad2,
    ) = ACPI_TIME_STRUCT.unpack(data[:ACPI_TIME_LEN])

    sam_epoch: int | None = None
    iso_str = "Invalid/Unset"
    try:
        # Construct UTC timestamp using calendar.timegm
        sam_epoch = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))
        iso_str = (
            f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}."
            f"{milliseconds:03d}Z"
        )
    except (ValueError, OverflowError):
        pass

    return {
        "year": year,
        "month": month,
        "day": day,
        "hour": hour,
        "minute": minute,
        "second": second,
        "valid": valid,
        "is_valid": bool(valid & 0x01),
        "milliseconds": milliseconds,
        "timezone_bias_minutes": timezone,
        "daylight_flags": daylight,
        "sam_epoch": sam_epoch,
        "iso_timestamp": iso_str,
        "raw_hex": data[:ACPI_TIME_LEN].hex(),
    }


def pack_acpi_time(
    epoch_sec: float,
    valid: int = 1,
    timezone_bias: int = 0,
    daylight: int = 0,
) -> bytes:
    """Packs an epoch second float into a 16-byte ACPI_TIME buffer."""
    gm = time.gmtime(epoch_sec)
    ms = int(round((epoch_sec % 1.0) * 1000.0)) % 1000
    return ACPI_TIME_STRUCT.pack(
        gm.tm_year,
        gm.tm_mon,
        gm.tm_mday,
        gm.tm_hour,
        gm.tm_min,
        gm.tm_sec,
        valid,
        ms,
        timezone_bias,
        daylight,
        0,
        0,
        0,
    )


def read_cmos_rtc0_epoch() -> int | None:
    """Reads CMOS rtc0 since_epoch from sysfs if available."""
    try:
        if RTC0_SYSFS.is_file():
            return int(RTC0_SYSFS.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass
    return None


def cmd_status(json_output: bool = False) -> int:
    """Queries and displays the current SAM EC RTC and system clock status."""
    sys_utc = time.time()
    cmos_epoch = read_cmos_rtc0_epoch()

    try:
        with SsamClient() as client:
            resp = client.request(
                tc=SSAM_TC_CORE,
                tid=SSAM_TID_SAM,
                cid=SSAM_CID_RTC_GET,
                iid=SSAM_IID_DEFAULT,
                payload=b"",
                has_response=True,
                resp_cap=16,
            )
    except SsamDeviceError as exc:
        if json_output:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error querying SAM EC RTC: {exc}", file=sys.stderr)
        return 1

    rtc_info = unpack_acpi_time(resp)
    sam_epoch = rtc_info["sam_epoch"]

    sam_offset = (sam_epoch - sys_utc) if sam_epoch is not None else None
    rtc0_offset = (cmos_epoch - sys_utc) if cmos_epoch is not None else None

    report = {
        "sam_ec_rtc": {
            "iso_timestamp": rtc_info["iso_timestamp"],
            "sam_epoch": sam_epoch,
            "valid": rtc_info["valid"],
            "is_valid": rtc_info["is_valid"],
            "timezone_bias_minutes": rtc_info["timezone_bias_minutes"],
            "daylight_flags": rtc_info["daylight_flags"],
            "raw_hex": rtc_info["raw_hex"],
        },
        "system_utc": {
            "iso_timestamp": datetime.datetime.fromtimestamp(
                sys_utc, tz=datetime.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "sys_epoch": round(sys_utc, 3),
        },
        "cmos_rtc0": {
            "epoch": cmos_epoch,
            "offset_to_sys_sec": round(rtc0_offset, 3) if rtc0_offset is not None else None,
        },
        "offsets": {
            "sam_minus_sys_sec": round(sam_offset, 3) if sam_offset is not None else None,
            "rtc0_minus_sys_sec": round(rtc0_offset, 3) if rtc0_offset is not None else None,
        },
    }

    if json_output:
        print(json.dumps(report, indent=2))
        return 0

    print("================================================================================")
    print("                     Surface SAM EC Real-Time Clock Status                     ")
    print("================================================================================")
    print(f"SAM EC Clock:       {rtc_info['iso_timestamp']} (epoch: {sam_epoch})")
    print(f"SAM EC Valid Flag:  {rtc_info['valid']} (is_valid: {rtc_info['is_valid']})")
    print(f"SAM EC Timezone:    {rtc_info['timezone_bias_minutes']} min, Daylight: {rtc_info['daylight_flags']}")
    print(f"SAM EC Raw Hex:     {rtc_info['raw_hex']}")
    print("--------------------------------------------------------------------------------")
    print(f"System UTC Time:    {report['system_utc']['iso_timestamp']} (epoch: {report['system_utc']['sys_epoch']})")
    if cmos_epoch is not None and rtc0_offset is not None:
        print(f"CMOS rtc0 Epoch:    {cmos_epoch} (offset to sys: {rtc0_offset:+.1f}s)")
    else:
        print("CMOS rtc0 Epoch:    Unavailable")
    print("--------------------------------------------------------------------------------")
    if sam_offset is not None:
        hours = sam_offset / 3600.0
        print(f"SAM EC Offset:      {sam_offset:+.1f}s ({hours:+.2f}h relative to system UTC)")
        if abs(sam_offset) <= 2.0:
            print("Status:             [SYNCHRONIZED] SAM EC clock is synchronized with UTC.")
        else:
            print(f"Status:             [SKEWED] SAM EC clock is offset by {hours:+.2f}h.")
            print("                    Run 'surface-sam-rtc.py sync-utc' (as root) to align SAM RTC.")
    else:
        print("Status:             [INVALID] SAM EC returned an unparseable clock timestamp.")
    print("================================================================================")
    return 0


def cmd_sync_utc(json_output: bool = False) -> int:
    """Sets the SAM EC RTC to the current system UTC time and verifies readback."""
    if os.geteuid() != 0:
        msg = "Syncing the SAM EC RTC requires root privileges (run with sudo)."
        if json_output:
            print(json.dumps({"error": msg}, indent=2))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    sys_utc_start = time.time()
    payload = pack_acpi_time(sys_utc_start, valid=1, timezone_bias=0, daylight=0)

    try:
        with SsamClient() as client:
            # Send CID = 0x20 (_SRT)
            # Response may be 4 bytes status (or empty depending on firmware revision)
            try:
                resp = client.request(
                    tc=SSAM_TC_CORE,
                    tid=SSAM_TID_SAM,
                    cid=SSAM_CID_RTC_SET,
                    iid=SSAM_IID_DEFAULT,
                    payload=payload,
                    has_response=True,
                    resp_cap=16,
                )
            except SsamDeviceError:
                # Fallback with has_response=False if the firmware completes asynchronously
                client.request(
                    tc=SSAM_TC_CORE,
                    tid=SSAM_TID_SAM,
                    cid=SSAM_CID_RTC_SET,
                    iid=SSAM_IID_DEFAULT,
                    payload=payload,
                    has_response=False,
                )

            # Re-read SAM EC RTC to verify
            time.sleep(0.05)
            verify_resp = client.request(
                tc=SSAM_TC_CORE,
                tid=SSAM_TID_SAM,
                cid=SSAM_CID_RTC_GET,
                iid=SSAM_IID_DEFAULT,
                payload=b"",
                has_response=True,
                resp_cap=16,
            )
    except SsamDeviceError as exc:
        if json_output:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error writing SAM EC RTC: {exc}", file=sys.stderr)
        return 1

    verify_info = unpack_acpi_time(verify_resp)
    sys_utc_now = time.time()
    sam_epoch = verify_info["sam_epoch"]

    if sam_epoch is None:
        msg = "SAM EC clock verification failed: returned unparseable epoch."
        if json_output:
            print(json.dumps({"error": msg, "details": verify_info}, indent=2))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    delta = abs(sam_epoch - sys_utc_now)
    success = delta <= 2.0

    result = {
        "success": success,
        "written_utc": sys_utc_start,
        "readback_sam_epoch": sam_epoch,
        "readback_iso": verify_info["iso_timestamp"],
        "delta_sec": round(sam_epoch - sys_utc_now, 3),
        "valid": verify_info["valid"],
    }

    if json_output:
        print(json.dumps(result, indent=2))
        return 0 if success else 1

    if success:
        print(f"Successfully synchronized SAM EC RTC to UTC!")
        print(f"  Verified SAM Timestamp: {verify_info['iso_timestamp']} (delta: {result['delta_sec']:+.2f}s)")
        return 0
    else:
        print(
            f"Warning: SAM EC RTC readback delta exceeds tolerance: {result['delta_sec']:+.2f}s "
            f"(target ~{sys_utc_now:.0f}, readback {sam_epoch})",
            file=sys.stderr,
        )
        return 1


def cmd_selftest() -> int:
    """Executes offline unit tests validating ACPI_TIME buffer manipulation and SSAM request structures."""
    print("Running surface-sam-rtc self-tests...")

    # Test 1: Verify ACPI_TIME struct size
    assert ACPI_TIME_STRUCT.size == 16, f"Struct size is {ACPI_TIME_STRUCT.size}, expected 16"
    print("  [PASS] ACPI_TIME_STRUCT size is 16 bytes")

    # Test 2: Known timestamp round-trip
    # 2026-11-12 02:30:15.500 UTC -> epoch 1794450615.5
    test_epoch = 1794450615.5
    packed = pack_acpi_time(test_epoch, valid=1, timezone_bias=0, daylight=0)
    assert len(packed) == 16, f"Packed length is {len(packed)}"
    unpacked = unpack_acpi_time(packed)

    assert unpacked["year"] == 2026, f"Year mismatch: {unpacked['year']}"
    assert unpacked["month"] == 11, f"Month mismatch: {unpacked['month']}"
    assert unpacked["day"] == 12, f"Day mismatch: {unpacked['day']}"
    assert unpacked["hour"] == 2, f"Hour mismatch: {unpacked['hour']}"
    assert unpacked["minute"] == 30, f"Minute mismatch: {unpacked['minute']}"
    assert unpacked["second"] == 15, f"Second mismatch: {unpacked['second']}"
    assert unpacked["milliseconds"] == 500, f"MS mismatch: {unpacked['milliseconds']}"
    assert unpacked["valid"] == 1, f"Valid mismatch: {unpacked['valid']}"
    assert unpacked["is_valid"] is True
    assert unpacked["timezone_bias_minutes"] == 0
    assert unpacked["daylight_flags"] == 0
    assert unpacked["sam_epoch"] == 1794450615
    print("  [PASS] ACPI_TIME round-trip pack & unpack succeeds (2026-11-12 02:30:15 UTC)")

    # Test 3: Timezone bias round-trip (-420 min / PDT)
    packed_tz = pack_acpi_time(test_epoch, valid=1, timezone_bias=-420, daylight=1)
    unpacked_tz = unpack_acpi_time(packed_tz)
    assert unpacked_tz["timezone_bias_minutes"] == -420, f"TZ mismatch: {unpacked_tz['timezone_bias_minutes']}"
    assert unpacked_tz["daylight_flags"] == 1, f"Daylight mismatch: {unpacked_tz['daylight_flags']}"
    print("  [PASS] TimeZone bias (-420m) and Daylight flags round-trip succeeds")

    # Test 4: Invalid short buffer handling
    try:
        unpack_acpi_time(b"\x00" * 15)
        raise AssertionError("Failed to reject short buffer")
    except ValueError:
        pass
    print("  [PASS] Rejection of undersized ACPI_TIME buffer (<16 bytes)")

    # Test 5: SSAM CDEV Request IOCTL structure verification
    # struct ssam_cdev_request size is 40 bytes:
    # 4 bytes (tc, tid, cid, iid) + 2 bytes (flags) + 2 bytes (status) +
    # 8 bytes (payload_ptr) + 2 bytes (payload_len) + 6 bytes pad +
    # 8 bytes (resp_ptr) + 2 bytes (resp_len) + 6 bytes pad = 40 bytes
    cdev_struct = struct.Struct("<BBBBHhQH6xQH6x")
    assert cdev_struct.size == 40, f"SSAM CDEV struct size is {cdev_struct.size}, expected 40"
    assert SSAM_CDEV_REQUEST_IOCTL == 0xC028A501, f"IOCTL code {hex(SSAM_CDEV_REQUEST_IOCTL)}, expected 0xc028a501"
    print("  [PASS] SSAM_CDEV_REQUEST_IOCTL code (0xc028a501) and packing size (40 bytes) verified")

    print("\nAll surface-sam-rtc self-tests passed successfully (100% PASS).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Microsoft Surface SAM EC Real-Time Clock Reading and Synchronization Tool"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    subparsers.add_parser("status", help="Read SAM EC RTC and display offset to system clock")
    subparsers.add_parser("sync-utc", help="Synchronize SAM EC RTC to system UTC time (requires root)")
    subparsers.add_parser("selftest", help="Run offline unit tests on packet codecs and structures")

    args = parser.parse_args(argv)

    cmd = args.command or "status"
    if cmd == "status":
        return cmd_status(json_output=args.json)
    elif cmd == "sync-utc":
        return cmd_sync_utc(json_output=args.json)
    elif cmd == "selftest":
        return cmd_selftest()
    else:
        parser.print_help(sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
