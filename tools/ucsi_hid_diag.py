#!/usr/bin/env python3
"""
Surface Book 3 UCSI-over-HID (0019:045E:09A6 / 01:15:02:06:00) + SSAM Power Diagnostic & Recovery Tool.
Clean-room implementation of standard USB-IF UCSI v1.x over HID Feature/Input Report 0x32 & 0x33
and SSAM TC=0x02 power/HPMI telemetry queries.
"""

import ctypes
import errno
import fcntl
import glob
import os
import select
import struct
import subprocess
import sys
import time
from datetime import datetime

LOG_DIR = os.environ.get("SURFACE_DIAG_LOG_DIR", os.getcwd())
LOG_PATH = os.path.join(LOG_DIR, "diag.log")
DONE_PATH = os.path.join(LOG_DIR, "diag.done")

# Linux ioctl helpers (x86_64)
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(dir_bits: int, type_ch: int, nr: int, size: int) -> int:
    return (dir_bits << 30) | (size << 16) | (type_ch << 8) | nr


def HIDIOCSFEATURE(length: int) -> int:
    return _IOC(_IOC_WRITE | _IOC_READ, ord("H"), 0x06, length)


def HIDIOCGFEATURE(length: int) -> int:
    return _IOC(_IOC_WRITE | _IOC_READ, ord("H"), 0x07, length)


SSAM_CDEV_REQUEST_HAS_RESPONSE = 0x01
SSAM_CDEV_REQUEST_IOCTL = _IOC(_IOC_WRITE | _IOC_READ, 0xA5, 1, 40)

# UCSI CCI bits
CCI_NOT_SUPPORTED = 1 << 25
CCI_CANCEL_COMPLETE = 1 << 26
CCI_RESET_COMPLETE = 1 << 27
CCI_BUSY = 1 << 28
CCI_ACK_COMPLETE = 1 << 29
CCI_ERROR = 1 << 30
CCI_CMD_COMPLETE = 1 << 31

# UCSI Commands
UCSI_PPM_RESET = 0x01
UCSI_CANCEL = 0x02
UCSI_CONNECTOR_RESET = 0x03
UCSI_ACK_CC_CI = 0x04
UCSI_SET_NOTIFICATION_ENABLE = 0x05
UCSI_GET_CAPABILITY = 0x06
UCSI_GET_CONNECTOR_CAPABILITY = 0x07
UCSI_SET_UOR = 0x09
UCSI_SET_PDR = 0x0B
UCSI_GET_ALTERNATE_MODES = 0x0C
UCSI_GET_CAM_SUPPORTED = 0x0D
UCSI_GET_CURRENT_CAM = 0x0E
UCSI_GET_PDOS = 0x10
UCSI_GET_CABLE_PROPERTY = 0x11
UCSI_GET_CONNECTOR_STATUS = 0x12
UCSI_GET_ERROR_STATUS = 0x13

UCSI_ACK_CONNECTOR_CHANGE = 1 << 16
UCSI_ACK_COMMAND_COMPLETE = 1 << 17

# Valid UCSI v1.1 notification mask (excludes reserved bits 3, 4, 10, 13)
UCSI_NTFY_ALL_V1 = 0xDBE7

POWER_OP_MODES = {
    0: "NoConsumer",
    1: "USB_Default",
    2: "BC_1.2",
    3: "USB_PD",
    4: "TypeC_1.5A",
    5: "TypeC_3.0A",
}

PARTNER_TYPES = {
    1: "DFP (Downstream/Device)",
    2: "UFP (Upstream/Host/Charger)",
    3: "PoweredCable_NoUFP",
    4: "PoweredCable_WithUFP",
    5: "DebugAccessory",
    6: "AudioAccessory",
}

BATT_CHG_STATUS = {
    0: "NotCharging",
    1: "Nominal",
    2: "Slow",
    3: "VerySlow/Trickle",
}

CONN_CHANGE_BITS = {
    1: "ExtSupply",
    2: "PowerOpMode",
    5: "SupportedProviderCaps",
    6: "NegotiatedPowerLevel",
    7: "PDResetComplete",
    8: "SupportedCAM",
    9: "BatteryChargingStatus",
    11: "PartnerChanged",
    12: "PowerDirection",
    13: "SinkPathStatus",
    14: "ConnectChange",
    15: "Error",
}

ERROR_STATUS_BITS = {
    0: "UnrecognizedCmd",
    1: "NonExistentConn",
    2: "InvalidCmdParams",
    3: "IncompatiblePartner",
    4: "CC_CommError",
    5: "DeadBattery",
    6: "ContractNegotiationFail",
    7: "Overcurrent",
    8: "Undefined",
    9: "PartnerRejectedSwap",
    10: "HardReset",
    11: "PPMPolicyConflict",
    12: "SwapRejected",
    13: "ReverseCurrentProtection",
    14: "SetSinkPathRejected",
}


class Logger:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.fp = open(path, "w", encoding="utf-8", buffering=1)

    def log(self, msg: str = ""):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}" if msg else ""
        print(line, flush=True)
        self.fp.write(line + "\n")

    def close(self):
        self.fp.close()


def find_ucsi_hidraw() -> tuple[str, str]:
    for uev in sorted(glob.glob("/sys/bus/hid/devices/*/uevent")):
        data = {}
        with open(uev, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    data[k] = v
        if (
            data.get("HID_ID") == "0019:0000045E:000009A6"
            and data.get("HID_PHYS") == "01:15:02:06:00"
        ):
            dev_dir = os.path.dirname(uev)
            hidraws = sorted(glob.glob(os.path.join(dev_dir, "hidraw", "hidraw*")))
            if hidraws:
                node = "/dev/" + os.path.basename(hidraws[0])
                return node, dev_dir
    raise RuntimeError(
        "Could not find Surface UCSI HID device (0019:045E:09A6 @ 01:15:02:06:00)"
    )


def format_cci(cci: int) -> str:
    flags = []
    conn = (cci >> 1) & 0x7F
    dlen = (cci >> 8) & 0xFF
    if cci & CCI_CMD_COMPLETE:
        flags.append("CMD_DONE")
    if cci & CCI_ERROR:
        flags.append("ERROR")
    if cci & CCI_ACK_COMPLETE:
        flags.append("ACK_DONE")
    if cci & CCI_BUSY:
        flags.append("BUSY")
    if cci & CCI_RESET_COMPLETE:
        flags.append("RESET_DONE")
    if cci & CCI_CANCEL_COMPLETE:
        flags.append("CANCEL_DONE")
    if cci & CCI_NOT_SUPPORTED:
        flags.append("NOT_SUPP")
    if conn:
        flags.append(f"CONN_CHG={conn}")
    return f"0x{cci:08x} (len={dlen}, {'|'.join(flags) if flags else 'IDLE'})"


def decode_pdo(pdo: int, is_source: bool = True) -> str:
    if pdo == 0:
        return "0x00000000 (empty)"
    ptype = (pdo >> 30) & 0x3
    if ptype == 0:  # Fixed supply
        mv = ((pdo >> 10) & 0x3FF) * 50
        ma = (pdo & 0x3FF) * 10
        w = (mv * ma) / 1_000_000.0
        extra = []
        if is_source:
            if pdo & (1 << 29):
                extra.append("DualRolePower")
            if pdo & (1 << 27):
                extra.append("UnconstrainedPower")
            if pdo & (1 << 26):
                extra.append("USBComm")
            if pdo & (1 << 25):
                extra.append("DualRoleData")
        return f"0x{pdo:08x} [Fixed {mv/1000:.2f}V @ {ma/1000:.2f}A = {w:.1f}W {' '.join(extra)}]"
    elif ptype == 1:  # Battery
        max_mv = ((pdo >> 20) & 0x3FF) * 50
        min_mv = ((pdo >> 10) & 0x3FF) * 50
        mw = (pdo & 0x3FF) * 250
        return f"0x{pdo:08x} [Batt {min_mv/1000:.2f}-{max_mv/1000:.2f}V @ {mw/1000:.2f}W]"
    elif ptype == 2:  # Variable
        max_mv = ((pdo >> 20) & 0x3FF) * 50
        min_mv = ((pdo >> 10) & 0x3FF) * 50
        ma = (pdo & 0x3FF) * 10
        return f"0x{pdo:08x} [Var {min_mv/1000:.2f}-{max_mv/1000:.2f}V @ {ma/1000:.2f}A]"
    else:  # Augmented / PPS
        max_mv = ((pdo >> 17) & 0xFF) * 100
        min_mv = ((pdo >> 8) & 0xFF) * 100
        ma = (pdo & 0x7F) * 50
        return f"0x{pdo:08x} [PPS {min_mv/1000:.2f}-{max_mv/1000:.2f}V @ {ma/1000:.2f}A]"


def decode_rdo(rdo: int) -> str:
    if rdo == 0:
        return "0x00000000 (none)"
    obj_pos = (rdo >> 28) & 0x7
    giveback = bool(rdo & (1 << 27))
    cap_mismatch = bool(rdo & (1 << 26))
    usb_comm = bool(rdo & (1 << 25))
    no_suspend = bool(rdo & (1 << 24))
    op_ma = ((rdo >> 10) & 0x3FF) * 10
    max_ma = (rdo & 0x3FF) * 10
    flags = []
    if cap_mismatch:
        flags.append("CAP_MISMATCH!")
    if giveback:
        flags.append("GiveBack")
    if usb_comm:
        flags.append("USBComm")
    if no_suspend:
        flags.append("NoSuspend")
    return (
        f"0x{rdo:08x} (PDO#{obj_pos}, Op={op_ma/1000:.2f}A, "
        f"Max/Min={max_ma/1000:.2f}A{', ' + '|'.join(flags) if flags else ''})"
    )


def decode_connector_status(raw: bytes) -> dict:
    if len(raw) < 9:
        return {"raw": raw.hex()}
    chg_mask = struct.unpack_from("<H", raw, 0)[0]
    b2 = raw[2]
    b3 = raw[3]
    rdo = struct.unpack_from("<I", raw, 4)[0]
    b8 = raw[8]

    pwr_op_mode = b2 & 0x7
    connected = bool((b2 >> 3) & 0x1)
    pwr_dir = (b2 >> 4) & 0x1  # 0 = Consumer/Sink, 1 = Provider/Source
    partner_flags = ((b2 >> 5) & 0x7) | ((b3 & 0x1F) << 3)
    partner_type = (b3 >> 5) & 0x7
    batt_chg = b8 & 0x3
    limit_reason = (b8 >> 2) & 0xF
    b8_hi = (b8 >> 6) & 0x3

    changes = [name for bit, name in CONN_CHANGE_BITS.items() if chg_mask & (1 << bit)]
    pflags = []
    if partner_flags & 1:
        pflags.append("USB")
    if partner_flags & 2:
        pflags.append("AltMode")

    return {
        "change_mask": f"0x{chg_mask:04x} ({'|'.join(changes) if changes else 'none'})",
        "connected": connected,
        "power_dir": "SOURCE (Provider)" if pwr_dir else "SINK (Consumer)",
        "power_op_mode": POWER_OP_MODES.get(pwr_op_mode, f"Unknown({pwr_op_mode})"),
        "partner_type": PARTNER_TYPES.get(partner_type, f"Unknown({partner_type})"),
        "partner_flags": "|".join(pflags) if pflags else f"0x{partner_flags:02x}",
        "rdo": decode_rdo(rdo),
        "rdo_raw": rdo,
        "batt_charging_status": BATT_CHG_STATUS.get(batt_chg, str(batt_chg)),
        "power_limit_reason": f"0x{limit_reason:x}",
        "b8_hi": b8_hi,
        "extra_bytes": raw[9:].hex(),
    }


def decode_error_status(raw: bytes) -> str:
    err_mask = struct.unpack_from("<H", raw, 0)[0]
    names = [name for bit, name in ERROR_STATUS_BITS.items() if err_mask & (1 << bit)]
    vendor = raw[2:].hex()
    return f"mask=0x{err_mask:04x} ({'|'.join(names) if names else 'none'}), vendor={vendor}"


class UcsiHidPpm:
    def __init__(self, hidraw_path: str, logger: Logger):
        self.path = hidraw_path
        self.log = logger
        self.fd = os.open(hidraw_path, os.O_RDWR | os.O_NONBLOCK)

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def get_feature_33(self) -> tuple[int, int, bytes]:
        buf = bytearray(3)
        buf[0] = 0x33
        fcntl.ioctl(self.fd, HIDIOCGFEATURE(3), buf, True)
        return buf[1], buf[2], bytes(buf)

    def get_feature_32(self) -> tuple[int, int, int, bytes, bytes]:
        buf = bytearray(49)
        buf[0] = 0x32
        fcntl.ioctl(self.fd, HIDIOCGFEATURE(49), buf, True)
        ver, seq, cci = struct.unpack_from("<HHI", buf, 1)
        msg_in = bytes(buf[17:33])
        return ver, seq, cci, msg_in, bytes(buf)

    def set_feature_32(self, control: int, msg_out: bytes = b"") -> None:
        buf = bytearray(49)
        buf[0] = 0x32
        struct.pack_into("<H", buf, 3, 0)
        struct.pack_into("<Q", buf, 9, control)
        if msg_out:
            buf[33 : 33 + len(msg_out)] = msg_out[:16]
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(49), buf, True)

    def drain_input_reports(self) -> list[tuple[int, int]]:
        events = []
        while True:
            try:
                data = os.read(self.fd, 64)
                if not data:
                    break
                if len(data) >= 3:
                    rid = data[0]
                    seq = struct.unpack_from("<H", data, 1)[0]
                    events.append((rid, seq))
                else:
                    events.append((data[0], 0))
            except BlockingIOError:
                break
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    break
                raise
        return events

    def wait_for_cci(self, mask: int, timeout_s: float = 3.0) -> tuple[int, bytes]:
        deadline = time.monotonic() + timeout_s
        last_cci = 0
        last_msg = b"\x00" * 16
        while time.monotonic() < deadline:
            r, _, _ = select.select([self.fd], [], [], 0.04)
            if r:
                self.drain_input_reports()
            _, _, cci, msg_in, _ = self.get_feature_32()
            last_cci, last_msg = cci, msg_in
            if cci & CCI_BUSY:
                time.sleep(0.02)
                continue
            if cci & (mask | CCI_ERROR | CCI_NOT_SUPPORTED):
                return cci, msg_in
            time.sleep(0.02)
        raise TimeoutError(
            f"Timeout waiting for CCI mask 0x{mask:08x}, last CCI={format_cci(last_cci)}"
        )

    def ppm_reset(self) -> int:
        self.drain_input_reports()
        _, _, pre_cci, _, _ = self.get_feature_32()
        if pre_cci & CCI_RESET_COMPLETE:
            self.set_feature_32(UCSI_SET_NOTIFICATION_ENABLE)
            try:
                self.wait_for_cci(CCI_CMD_COMPLETE, timeout_s=2.0)
            except Exception:
                pass
        self.set_feature_32(UCSI_PPM_RESET)
        cci, _ = self.wait_for_cci(CCI_RESET_COMPLETE, timeout_s=4.0)
        self.log.log(f"  PPM_RESET -> CCI={format_cci(cci)}")
        return cci

    def ack(self, ack_flags: int) -> int:
        ctrl = UCSI_ACK_CC_CI | ack_flags
        self.set_feature_32(ctrl)
        cci, _ = self.wait_for_cci(CCI_ACK_COMPLETE, timeout_s=2.0)
        return cci

    def run_cmd(self, control: int, label: str = "", timeout_s: float = 3.0) -> tuple[int, bytes]:
        # Clear any lingering command-complete state first if needed
        _, _, pre_cci, _, _ = self.get_feature_32()
        if pre_cci & CCI_CMD_COMPLETE:
            try:
                self.ack(UCSI_ACK_COMMAND_COMPLETE)
            except Exception:
                pass
        self.set_feature_32(control)
        cci, msg_in = self.wait_for_cci(CCI_CMD_COMPLETE, timeout_s=timeout_s)
        dlen = (cci >> 8) & 0xFF
        # Acknowledge command completion per UCSI spec
        try:
            self.ack(UCSI_ACK_COMMAND_COMPLETE)
        except Exception as e:
            self.log.log(f"  [warn] ACK_CC_CI after {label or hex(control)} failed: {e}")
        return cci, msg_in[: max(dlen, 16)]


class SsamClient:
    def __init__(self, logger: Logger):
        self.log = logger
        self.fd = -1
        if not os.path.exists("/dev/surface/aggregator"):
            subprocess.run(
                ["modprobe", "surface_aggregator_cdev"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.15)
        if os.path.exists("/dev/surface/aggregator"):
            self.fd = os.open("/dev/surface/aggregator", os.O_RDWR)

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def request(
        self,
        tc: int,
        tid: int,
        cid: int,
        iid: int = 0,
        payload: bytes = b"",
        has_response: bool = True,
        resp_cap: int = 64,
    ) -> bytes | None:
        if self.fd < 0:
            return None
        pay_buf = (ctypes.c_ubyte * max(len(payload), 1))()
        for i, b in enumerate(payload):
            pay_buf[i] = b
        resp_buf = (ctypes.c_ubyte * resp_cap)()
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
        fcntl.ioctl(self.fd, SSAM_CDEV_REQUEST_IOCTL, req, True)
        _, _, _, _, _, status, _, _, _, out_len = struct.unpack(
            "<BBBBHhQH6xQH6x", req
        )
        if status != 0:
            raise OSError(f"SSAM status={status}")
        if not has_response:
            return b""
        return bytes(resp_buf[:out_len])


def read_sysfs_power() -> str:
    parts = []
    adp = "/sys/class/power_supply/ADP1/online"
    if os.path.exists(adp):
        with open(adp) as f:
            parts.append(f"ADP1={f.read().strip()}")
    for bat in ("BAT1", "BAT2"):
        bdir = f"/sys/class/power_supply/{bat}"
        if os.path.exists(bdir):
            st = open(f"{bdir}/status").read().strip()
            p_uw = int(open(f"{bdir}/power_now").read().strip())
            e_uwh = int(open(f"{bdir}/energy_now").read().strip())
            v_uv = int(open(f"{bdir}/voltage_now").read().strip())
            parts.append(
                f"{bat}:{st}({e_uwh/1e6:.2f}Wh,{p_uw/1e6:.2f}W,{v_uv/1e6:.2f}V)"
            )
    return " | ".join(parts)


def dump_ssam_power(ssam: SsamClient, logger: Logger) -> None:
    if ssam.fd < 0:
        logger.log("  SSAM cdev not available")
        return
    # ADP1 PSR (CID=0x0D) and Artg adapter rating mW (CID=0x0F)
    for label, tid, iid, cid in [
        ("ADP1_PSR (TID=1,IID=1,CID=0x0D)", 1, 1, 0x0D),
        ("ADP1_ARTG_mW (TID=1,IID=1,CID=0x0F)", 1, 1, 0x0F),
        ("BASE_PSR? (TID=2,IID=1,CID=0x0D)", 2, 1, 0x0D),
        ("BASE_ARTG_mW? (TID=2,IID=1,CID=0x0F)", 2, 1, 0x0F),
        ("HPMI_SDB_CAPS (TID=1,IID=0,CID=0x50)", 1, 0, 0x50),
        ("BAT1_PROT_TP (TID=1,IID=1,CID=0x53)", 1, 1, 0x53),
        ("BAT2_PROT_TP (TID=2,IID=1,CID=0x53)", 2, 1, 0x53),
    ]:
        try:
            res = ssam.request(0x02, tid, cid, iid=iid, has_response=True)
            if res is not None:
                if len(res) == 4:
                    val = struct.unpack("<I", res)[0]
                    logger.log(f"  SSAM {label}: 0x{val:08x} ({val}) raw={res.hex()}")
                else:
                    logger.log(f"  SSAM {label}: ({len(res)}B) raw={res.hex()}")
        except Exception as e:
            logger.log(f"  SSAM {label}: err={e}")


def query_full_connector_state(ppm: UcsiHidPpm, logger: Logger, prefix: str = "  ") -> dict:
    _, raw_cs = ppm.run_cmd(UCSI_GET_CONNECTOR_STATUS | (1 << 16), "GET_CONNECTOR_STATUS")
    cs = decode_connector_status(raw_cs)
    logger.log(
        f"{prefix}Conn1 Status: connected={cs['connected']} | dir={cs['power_dir']} | "
        f"op_mode={cs['power_op_mode']} | partner={cs['partner_type']} [{cs['partner_flags']}]"
    )
    logger.log(
        f"{prefix}  RDO={cs['rdo']} | batt_chg={cs['batt_charging_status']} | "
        f"limit={cs['power_limit_reason']} | chg={cs['change_mask']} | raw={raw_cs.hex()}"
    )
    if "Error" in cs["change_mask"]:
        try:
            _, raw_err = ppm.run_cmd(UCSI_GET_ERROR_STATUS, "GET_ERROR_STATUS")
            logger.log(f"{prefix}  GET_ERROR_STATUS: {decode_error_status(raw_err)}")
        except Exception as e:
            logger.log(f"{prefix}  GET_ERROR_STATUS failed: {e}")

    if cs["connected"] and cs["power_op_mode"] == "USB_PD":
        try:
            # Partner Source PDOs: partner=1 (bit23), offset=0, num=3 (4 PDOs, bits33:32), source=1 (bit34)
            ctrl_pdo = (
                UCSI_GET_PDOS
                | (1 << 16)
                | (1 << 23)
                | (0 << 24)
                | (3 << 32)
                | (1 << 34)
            )
            cci_p, raw_pdos = ppm.run_cmd(ctrl_pdo, "GET_PDOS(PartnerSource)")
            plen = (cci_p >> 8) & 0xFF
            pdos = [
                struct.unpack_from("<I", raw_pdos, off)[0]
                for off in range(0, min(plen, 16), 4)
            ]
            for idx, p in enumerate(pdos, 1):
                if p:
                    logger.log(f"{prefix}  Partner Source PDO#{idx}: {decode_pdo(p, True)}")
        except Exception as e:
            logger.log(f"{prefix}  GET_PDOS(PartnerSource) err: {e}")
    return cs


def monitor_window(
    ppm: UcsiHidPpm, ssam: SsamClient, logger: Logger, duration_s: float, title: str
) -> dict:
    logger.log(f"=== {title} ({duration_s:.0f}s) ===")
    start = time.monotonic()
    last_summary = ""
    last_cs = {}
    poll_next = start
    while time.monotonic() - start < duration_s:
        r, _, _ = select.select([ppm.fd], [], [], 0.15)
        ir_events = ppm.drain_input_reports() if r else []
        now = time.monotonic()

        if ir_events or now >= poll_next:
            poll_next = now + 0.45
            ver, seq, cci, msg_in, _ = ppm.get_feature_32()
            conn_chg = (cci >> 1) & 0x7F
            adp_artg = ""
            if ssam.fd >= 0:
                try:
                    r_psr = ssam.request(0x02, 1, 0x0D, iid=1)
                    r_artg = ssam.request(0x02, 1, 0x0F, iid=1)
                    psr_v = struct.unpack("<I", r_psr)[0] if r_psr and len(r_psr) == 4 else -1
                    artg_v = struct.unpack("<I", r_artg)[0] if r_artg and len(r_artg) == 4 else -1
                    adp_artg = f"PSR={psr_v} ARTG={artg_v}mW"
                except Exception:
                    pass

            if ir_events or conn_chg:
                logger.log(
                    f"EVENT ir={ir_events} seq={seq} CCI={format_cci(cci)} | "
                    f"{adp_artg} | {read_sysfs_power()}"
                )
                try:
                    last_cs = query_full_connector_state(ppm, logger, prefix="    ")
                except Exception as e:
                    logger.log(f"    query_full_connector_state err: {e}")
                if conn_chg:
                    try:
                        ppm.ack(UCSI_ACK_CONNECTOR_CHANGE)
                    except Exception as e:
                        logger.log(f"    ACK_CONNECTOR_CHANGE err: {e}")
            else:
                try:
                    _, raw_cs = ppm.run_cmd(
                        UCSI_GET_CONNECTOR_STATUS | (1 << 16), "GET_CONNECTOR_STATUS"
                    )
                    cs = decode_connector_status(raw_cs)
                    last_cs = cs
                    summary = (
                        f"conn={cs['connected']} dir={cs['power_dir']} mode={cs['power_op_mode']} "
                        f"rdo=0x{cs['rdo_raw']:08x} chg={cs['batt_charging_status']} | {adp_artg} | "
                        f"{read_sysfs_power()}"
                    )
                    if summary != last_summary:
                        logger.log(f"STATE {summary}")
                        if cs["connected"] and cs["power_op_mode"] == "USB_PD":
                            logger.log(f"    RDO detail: {cs['rdo']}")
                        if "Error" in cs["change_mask"]:
                            _, raw_err = ppm.run_cmd(UCSI_GET_ERROR_STATUS, "GET_ERROR_STATUS")
                            logger.log(f"    ERROR_STATUS: {decode_error_status(raw_err)}")
                        last_summary = summary
                except Exception as e:
                    logger.log(f"POLL err: {e}")
    return last_cs


def main() -> int:
    if os.path.exists(DONE_PATH):
        os.remove(DONE_PATH)
    logger = Logger(LOG_PATH)
    logger.log("Surface Book 3 UCSI-over-HID + SSAM Power Diagnostic")
    logger.log("====================================================")

    ppm = None
    ssam = None
    try:
        node, sysfs_dir = find_ucsi_hidraw()
        logger.log(f"Found UCSI HID PPM: {node} ({sysfs_dir})")
        logger.log(f"Initial Sysfs Power: {read_sysfs_power()}")

        ssam = SsamClient(logger)
        ppm = UcsiHidPpm(node, logger)

        # Stage 1: Completely non-destructive raw read before touching UCSI CONTROL
        logger.log("\n--- Stage 1: Non-Destructive Initial Snapshot ---")
        num_conn, udc_supp, raw33 = ppm.get_feature_33()
        logger.log(
            f"  HID Feature 0x33: raw={raw33.hex()} -> "
            f"NumberOfConnectors={num_conn}, IsUSBDeviceControllerSupported={udc_supp}"
        )
        ver, seq, cci, msg_in, raw32 = ppm.get_feature_32()
        logger.log(
            f"  HID Feature 0x32: UCSI_VERSION=0x{ver:04x}, seq={seq}, CCI={format_cci(cci)}"
        )
        logger.log(f"  Initial MESSAGE_IN={msg_in.hex()} | Full0x32={raw32.hex()}")
        dump_ssam_power(ssam, logger)

        # Stage 2: Standard UCSI PPM initialization & capability dump
        logger.log("\n--- Stage 2: UCSI PPM Initialization & Capabilities ---")
        ppm.ppm_reset()
        basic_ntfy = 0x8001  # CMD_COMPLETE | ERROR
        cci_ntfy0, _ = ppm.run_cmd(
            UCSI_SET_NOTIFICATION_ENABLE | (basic_ntfy << 16),
            "SET_NOTIFICATION_ENABLE(basic)",
        )
        logger.log(f"  SET_NOTIFICATION_ENABLE(0x{basic_ntfy:04x}) -> {format_cci(cci_ntfy0)}")

        _, cap = ppm.run_cmd(UCSI_GET_CAPABILITY, "GET_CAPABILITY")
        bm_attr, b_nconn = struct.unpack_from("<IB", cap, 0)
        opt_feat = cap[5] | (cap[6] << 8) | (cap[7] << 16)
        b_nalt, _, bcd_bc, bcd_pd, bcd_typec = struct.unpack_from("<BBHHH", cap, 8)
        logger.log(
            f"  GET_CAPABILITY: raw={cap.hex()} -> attrs=0x{bm_attr:08x}, "
            f"num_conn={b_nconn}, opt_feat=0x{opt_feat:06x}, alt_modes={b_nalt}, "
            f"BC=0x{bcd_bc:04x}, PD=0x{bcd_pd:04x}, TypeC=0x{bcd_typec:04x}"
        )

        # Compute supported notifications exactly like Linux ucsi_get_supported_notifications()
        ntfy_mask = UCSI_NTFY_ALL_V1
        if not (opt_feat & (1 << 2)):  # UCSI_CAP_ALT_MODE_DETAILS
            ntfy_mask &= ~(1 << 8)
        if not (opt_feat & (1 << 4)):  # UCSI_CAP_PDO_DETAILS
            ntfy_mask &= ~((1 << 6) | (1 << 5))
        if not (opt_feat & (1 << 6)):  # UCSI_CAP_EXT_SUPPLY_NOTIFICATIONS
            ntfy_mask &= ~(1 << 1)
        if not (opt_feat & (1 << 7)):  # UCSI_CAP_PD_RESET
            ntfy_mask &= ~(1 << 7)
        if ver > 0x0120:
            ntfy_mask |= 1 << 16  # SINK_PATH_STS_CHANGE (bit 32 in control)

        cci_ntfy, _ = ppm.run_cmd(
            UCSI_SET_NOTIFICATION_ENABLE | (ntfy_mask << 16),
            "SET_NOTIFICATION_ENABLE(full)",
        )
        logger.log(f"  SET_NOTIFICATION_ENABLE(0x{ntfy_mask:05x}) -> {format_cci(cci_ntfy)}")

        _, ccap = ppm.run_cmd(
            UCSI_GET_CONNECTOR_CAPABILITY | (1 << 16), "GET_CONNECTOR_CAPABILITY"
        )
        logger.log(f"  GET_CONNECTOR_CAPABILITY(1): raw={ccap[:2].hex()}")

        # Local Connector 1 Sink PDOs & Source PDOs
        for is_src, lbl in [(0, "Local Sink PDO"), (1, "Local Source PDO")]:
            try:
                ctrl_lpdo = (
                    UCSI_GET_PDOS
                    | (1 << 16)
                    | (0 << 23)
                    | (0 << 24)
                    | (3 << 32)
                    | (is_src << 34)
                )
                cci_lp, raw_lpdos = ppm.run_cmd(ctrl_lpdo, f"GET_PDOS({lbl})")
                lplen = (cci_lp >> 8) & 0xFF
                for idx, off in enumerate(range(0, min(lplen, 16), 4), 1):
                    p = struct.unpack_from("<I", raw_lpdos, off)[0]
                    if p:
                        logger.log(f"  {lbl}#{idx}: {decode_pdo(p, bool(is_src))}")
            except Exception as e:
                logger.log(f"  GET_PDOS({lbl}) err: {e}")

        try:
            _, raw_err = ppm.run_cmd(UCSI_GET_ERROR_STATUS, "GET_ERROR_STATUS")
            logger.log(f"  Baseline GET_ERROR_STATUS: {decode_error_status(raw_err)}")
        except Exception as e:
            logger.log(f"  Baseline GET_ERROR_STATUS: {e}")

        query_full_connector_state(ppm, logger, prefix="  ")

        # Stage 3: Live 20s monitoring with 60W USB-C PD charger plugged in
        print("\n" + "=" * 72)
        print("ACTION: Plug in your 60W USB-C PD charger NOW (disconnect Surface Connect")
        print("        magnetic charger if connected so we isolate USB-C charging).")
        input("Press ENTER once the 60W USB-C PD charger is plugged in... ")
        print("=" * 72 + "\n")

        last_cs = monitor_window(
            ppm, ssam, logger, 18.0, "Stage 3: Live USB-C PD Observation"
        )
        dump_ssam_power(ssam, logger)

        # Stage 4: Safe UCSI Recovery Commands if not charging cleanly
        logger.log("\n--- Stage 4: Testing Standard UCSI Recovery Commands ---")
        for step_name, cmd_val in [
            ("SET_PDR(conn=1, Sink/Consumer)", UCSI_SET_PDR | (1 << 16) | (2 << 23)),
            ("CONNECTOR_RESET(conn=1, SoftReset)", UCSI_CONNECTOR_RESET | (1 << 16)),
            (
                "CONNECTOR_RESET(conn=1, HardReset)",
                UCSI_CONNECTOR_RESET | (1 << 16) | (1 << 23),
            ),
        ]:
            logger.log(f"\n>>> Issuing {step_name} (control=0x{cmd_val:016x}) ...")
            try:
                cci_r, _ = ppm.run_cmd(cmd_val, step_name, timeout_s=5.0)
                logger.log(f"  Result CCI={format_cci(cci_r)}")
                if cci_r & CCI_ERROR:
                    _, raw_err = ppm.run_cmd(UCSI_GET_ERROR_STATUS, "GET_ERROR_STATUS")
                    logger.log(f"  ERROR_STATUS: {decode_error_status(raw_err)}")
            except Exception as e:
                logger.log(f"  Command raised: {e}")
            monitor_window(ppm, ssam, logger, 6.0, f"Post-{step_name} Watch")

        logger.log("\n--- Final Snapshot ---")
        logger.log(f"Final Sysfs Power: {read_sysfs_power()}")
        dump_ssam_power(ssam, logger)
        logger.log("Diagnostic complete. Log saved to " + LOG_PATH)
        return 0
    except Exception as exc:
        logger.log(f"FATAL: {exc!r}")
        import traceback

        logger.log(traceback.format_exc())
        return 1
    finally:
        if ppm:
            ppm.close()
        if ssam:
            ssam.close()
        try:
            os.chmod(LOG_PATH, 0o644)
        except Exception:
            pass
        logger.close()
        with open(DONE_PATH, "w", encoding="utf-8") as df:
            df.write("done\n")
        try:
            os.chmod(DONE_PATH, 0o644)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
