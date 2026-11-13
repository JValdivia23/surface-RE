#!/usr/bin/env python3
"""
Unwrap Microsoft Surface CFU (Component Firmware Update) payload for the
Surface Book 3 Keyboard Base (KIP) MCU (SurfaceBookBaseV3_KIP_APP1.cfu)
into a raw binary and an ARM Thumb-2 ELF at Flash base 0x0000f994.
"""
import os
import struct
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CFU = os.path.join(
    REPO_ROOT,
    "SurfaceUpdate",
    "surfacebookbasev3fwupdate",
    "SurfaceBookBaseV3_KIP_APP1.cfu",
)
FLASH_BASE = 0x0000F994


def unwrap_cfu(cfu_path: str, bin_path: str) -> int:
    with open(cfu_path, "rb") as f:
        data = f.read()

    out = bytearray()
    off = 0
    expected_addr = None
    while off + 8 <= len(data):
        addr, length = struct.unpack("<II", data[off : off + 8])
        if length == 0 or off + 8 + length > len(data):
            break
        if expected_addr is None:
            expected_addr = addr
        out.extend(data[off + 8 : off + 8 + length])
        off += 8 + length

    with open(bin_path, "wb") as f:
        f.write(out)

    sp, reset = struct.unpack("<II", out[:8])
    print(f"Unwrapped {len(out)} bytes from {cfu_path}")
    print(f"  Flash Base : 0x{expected_addr:08x}")
    print(f"  Initial SP : 0x{sp:08x}")
    print(f"  Reset PC   : 0x{reset:08x} (Thumb)")
    return expected_addr or FLASH_BASE


def build_elf(bin_path: str, elf_path: str, base_addr: int) -> None:
    subprocess.run(
        [
            "/usr/bin/llvm-objcopy",
            "-I",
            "binary",
            "-O",
            "elf32-littlearm",
            "--rename-section",
            ".data=.text,alloc,load,readonly,code,contents",
            "--change-section-address",
            f".text=0x{base_addr:x}",
            bin_path,
            elf_path,
        ],
        check=True,
    )
    print(f"Created ELF at {elf_path} (disassemble with: llvm-objdump -d --triple=thumbv7m-none-eabi {elf_path})")


if __name__ == "__main__":
    cfu = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CFU
    out_dir = os.path.dirname(os.path.abspath(__file__))
    bin_file = os.path.join(out_dir, "kip_app1.bin")
    elf_file = os.path.join(out_dir, "kip_app1.elf")
    base = unwrap_cfu(cfu, bin_file)
    build_elf(bin_file, elf_file, base)
