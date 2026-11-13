# Surface Book 3 SAM Real-Time Clock & Integration Driver Reverse Engineering Report

- **Date:** November 2026
- **Target Device:** Microsoft Surface Book 3 (13.5", Intel Core i5-1035G7, Ice Lake, `Surface Book 3`)
- **OS Environment:** CachyOS Linux (`linux-surface` 7.3.1-1-cachyos)
- **Reference Windows Drivers:**
  - `SurfaceTimeAlarmAcpiFilter.sys` (v1.5.137.0, `ACPI\VEN_ACPI&DEV_000E&SUBSYS_MSHW0214`)
  - `SurfaceIntegrationDriver.sys` (v1.5.137.0, `ACPI\MSHW0117`)

---

## 1. Executive Summary & Root Cause Analysis

### 1.1 The Surface Book 3 Dual-RTC Architecture

The Microsoft Surface Book 3 hardware platform has two distinct Real-Time Clocks:

1. **Standard Intel SoC / PCH CMOS RTC (`PNP0B00:00`, `/dev/rtc0`, legacy I/O ports `0x70`/`0x71`):**
   - Traditional x86 battery-backed CMOS/MC146818 RTC attached to the Intel Ice Lake PCH.
   - Exposed under Linux as `/sys/class/rtc/rtc0` (`rtc_cmos`).
   - Synchronized by the Linux kernel's 11-minute `sync_hw_clock` timer to UTC while NTP (`chrony`/`systemd-timesyncd`) is synchronized.

2. **Surface Aggregator Module (SAM) EC Real-Time Clock:**
   - Maintained continuously inside the Surface Embedded Controller (SAM EC, STM32-based microcontroller on the clipboard motherboard).
   - Serves as the primary persistent time source across deep platform sleep states, cold boot, and S5 power-off.
   - Accessible via the Surface Serial Hub (SSH / SSAM) protocol over UART using Target Category `TC = 0x01` (Core/System), Target ID `TID = 0x01` (Clipboard SAM EC), Instance ID `IID = 0x00`.

3. **ACPI Time and Alarm Device (`ACPI000E:00`, `/dev/rtc1`):**
   - Defined in ACPI DSDT as `Device (SRTC)` with Hardware ID `ACPI000E`.
   - Exposed under Linux as `/sys/class/rtc/rtc1` (`acpi-tad`).

---

### 1.2 Why Linux `acpi-tad` (`ACPI000E:00` / `rtc1`) Fails with `-EIO` (5)

Under Linux on Surface Book 3, reading `/sys/class/rtc/rtc1/since_epoch` or `/sys/class/rtc/rtc1/time` consistently fails with:
```text
cat: /sys/class/rtc/rtc1/since_epoch: Input/output error
```
Kernel errno: `-EIO` (`5`).

#### Disassembly of DSDT `ACPI000E` (`\_SB.SRTC._GRT`):
```asl
Device (SRTC)
{
    Name (TIME, Buffer (0x10){})
    CreateWordField (TIME, Zero, YEAR)
    CreateByteField (TIME, 0x02, MNTH)
    CreateByteField (TIME, 0x03, DAYS)
    CreateByteField (TIME, 0x04, HOUR)
    CreateByteField (TIME, 0x05, MINS)
    CreateByteField (TIME, 0x06, SECS)
    CreateByteField (TIME, 0x07, ISOK)
    CreateWordField (TIME, 0x08, MILS)
    CreateWordField (TIME, 0x0A, TMZN)
    CreateByteField (TIME, 0x0C, DASV)
    CreateField (TIME, 0x68, 0x18, PAD2)

    Method (_INI, 0, Serialized)
    {
        ISOK = Zero
    }

    Method (_HID, 0, NotSerialized)
    {
        Return ("ACPI000E")
    }

    Method (_GRT, 0, Serialized)  // Get Real Time
    {
        YEAR = Zero
        MNTH = Zero
        DAYS = Zero
        HOUR = Zero
        MINS = Zero
        SECS = Zero
        ISOK = Zero
        TMZN = Zero
        DASV = Zero
        PAD2 = Zero
        Local1 = ^^_SAN.RQST (One, 0x1F, Zero, Zero, One)
        If ((ObjectType (Local1) == 0x03))
        {
            If ((SizeOf (Local1) == 0x10))
            {
                CreateWordField (Local1, Zero, TYEA)
                CreateByteField (Local1, 0x02, TMNT)
                CreateByteField (Local1, 0x03, TDAY)
                CreateByteField (Local1, 0x04, THOU)
                CreateByteField (Local1, 0x05, TMIN)
                CreateByteField (Local1, 0x06, TSEC)
                CreateByteField (Local1, 0x07, TISO)
                CreateWordField (Local1, 0x08, TMIL)
                CreateWordField (Local1, 0x0A, TTMZ)
                CreateByteField (Local1, 0x0C, TDAS)
                YEAR = TYEA
                MNTH = TMNT
                DAYS = TDAY
                HOUR = THOU
                MINS = TMIN
                SECS = TSEC
                ISOK = TISO
                MILS = TMIL
                TMZN = TTMZ
                DASV = TDAS
            }
        }
        Return (TIME)
    }
}
```

Notice:
1. `_GRT` calls `^^_SAN.RQST(One, 0x1F, Zero, Zero, One)` where `_SAN` represents the ACPI Surface Serial Hub interface.
2. In DSDT, `_SAN` operations are defined over an ACPI `GenericSerialBus` `OperationRegion (OR01, GenericSerialBus, Zero, 0x0100)`.
3. The method `RQST` guards communication with `If ((AVBL == One))`.
4. On Linux, SAM communication is driven natively by the `surface_aggregator` serial bus driver over UART (`ttyS0`), completely outside ACPI AML `GenericSerialBus`. Linux ACPI does not bind an OpRegion handler to `_SAN`, so `_SAN.AVBL == 0`.
5. As a result, `_SAN.RQST` aborts and returns an uninitialized or non-buffer object.
6. `_GRT` therefore returns the 16-byte `TIME` buffer where `ISOK` (`Valid`, byte offset `+0x07`) is `0`.
7. In the Linux kernel driver `drivers/acpi/acpi_tad.c`:
   ```c
   static int acpi_tad_get_real_time(struct device *dev, struct rtc_time *rt)
   {
       ...
       status = acpi_evaluate_object(handle, "_GRT", NULL, &output);
       if (ACPI_FAILURE(status))
           return -EIO;
       ...
       rt_desc = (struct acpi_tad_rt *)out_obj->buffer.pointer;
       if (!(rt_desc->valid & ACPI_TAD_VALID_TIME))
           return -EIO;
       ...
   }
   ```
   Because `rt_desc->valid == 0`, `acpi_tad` rejects the buffer and returns `-EIO` (`5`).

---

### 1.3 Why Surface UEFI POST Overwrote CMOS `rtc0` with `UTC - 7h` (`-25,179s`)

On the Surface Book 3, the Surface UEFI POST firmware operates as follows:
- Whenever the machine transitions through S5 power-off (cold boot or `HibernateMode=shutdown` wake), UEFI POST re-initializes CMOS `rtc0` (`PNP0B00:00`) by copying the hardware clock from the SAM EC.
- The SAM EC RTC had an epoch set to `UTC - 7 hours` (originally programmed by Windows to match local Pacific Daylight Time, bias = 420 minutes, matching EFI variable `RTC-378d7b65` offset `0x01a4 = 420m`). Over time this drifted by several seconds to `-25,179s`.
- While Linux is running with NTP active, Linux periodically writes true UTC to CMOS `rtc0`.
- But upon entering hibernation (`HibernateMode=shutdown`), the system powers off into S5.
- Upon pressing the power button to resume from hibernation:
  1. Surface UEFI POST executes and overwrites CMOS `rtc0` with the SAM EC clock: `t_wake - 25,179s`.
  2. The Linux hibernation resume kernel unpacks the memory snapshot and calls `syscore_resume() -> timekeeping_resume()`.
  3. `timekeeping_resume()` reads `ts_new` from CMOS `rtc0` (`t_wake - 25,179s`) and subtracts `timekeeping_suspend_time` (`t_sleep`, which was recorded in true UTC before power-down).
  4. The calculated delta is:
     $$\Delta = \text{ts\_new} - \text{timekeeping\_suspend\_time} = T_{\text{hibernated}} - 25{,}179\,\text{s}$$
  5. If $T_{\text{hibernated}} < 7\,\text{hours}$, $\Delta < 0$. The kernel rejects negative deltas and injects $0\,\text{s}$, causing `CLOCK_REALTIME` and `CLOCK_BOOTTIME` to completely stall at the pre-hibernate timestamp.
  6. If $T_{\text{hibernated}} > 7\,\text{hours}$, the kernel injects $T_{\text{hibernated}} - 25{,}179\,\text{s}$, causing the clock to wake ~7 hours behind true UTC until chronyd steps the clock after Wi-Fi reconnects.

---

## 2. Reverse Engineering `SurfaceTimeAlarmAcpiFilter.sys`

- **Binary Path:** `/home/jmvp/work/surface-RE/SurfaceUpdate/surfacetimealarmacpifilter/SurfaceTimeAlarmAcpiFilter.sys`
- **INF Path:** `/home/jmvp/work/surface-RE/SurfaceUpdate/surfacetimealarmacpifilter/SurfaceTimeAlarmAcpiFilter.inf`
- **Driver Class:** `Extension`
- **Target Hardware ID:** `ACPI\VEN_ACPI&DEV_000E&SUBSYS_MSHW0214`
- **Role:** Lower Filter Driver on Windows ACPI Time and Alarm participant stack.

### 2.1 Driver Installation & Architecture

In `SurfaceTimeAlarmAcpiFilter.inf`:
```ini
[Extension.NTamd64]
%ACPI000E.DeviceDesc% = Extension_Install, ACPI\VEN_ACPI&DEV_000E&SUBSYS_MSHW0214

[Extension_Install.NT.Filters]
AddFilter = SurfaceTimeAlarmAcpiFilter, , MyFilterInstallPosition

[MyFilterInstallPosition]
FilterPosition = Lower
```

Instead of allowing Windows ACPI (`acpi.sys`) to evaluate AML methods on `ACPI000E` through the DSDT (which would fail due to unhandled `_SAN` OpRegion), Microsoft inserts `SurfaceTimeAlarmAcpiFilter.sys` directly below `acpi.sys`.

The driver is built using KMDF 1.15 and Microsoft's **Driver Module Framework (DMF)**, specifically incorporating the `Dmf_SamCommunicationViaSsh` module.

---

### 2.2 IOCTL Interception Mechanism (`EvtIoDeviceControl`)

The WDF queue `EvtIoDeviceControl` callback is located at `0x140020ae0` (with entry trampolines at `0x140001600`):

```asm
140020b0a: movl    %r14d, %ecx
140020b0d: subl    $0x32c004, %ecx         # IOCTL_ACPI_ASYNC_EVAL_METHOD_V1
140020b13: je      0x140020cd5
140020b19: subl    $0x1c, %ecx             # 0x32c020: IOCTL_ACPI_ASYNC_EVAL_METHOD_EX
140020b1c: je      0x140020c78
140020b22: cmpl    $0x1c, %ecx             # 0x32c03c: IOCTL_ACPI_EVAL_METHOD_EX_V1
140020b25: je      0x140020b6c
```

When an ACPI method evaluation IOCTL is received:
1. The driver checks the input buffer signature (`AeiB`, `AeiC`, or `AeiN`).
2. It extracts the 4-byte ACPI method name from offset `+0x04`.
3. It performs a table lookup against the ACPI Method Dispatch Table at `0x1400192c0`:

| Method Name | ASCII Signature | Hex Name | Handler Function | Purpose |
|:---:|:---:|:---:|:---:|:---|
| `_GRT` | `_TRG` | `0x5452475f` | `0x140001980` | Get Real Time |
| `_SRT` | `_TRS` | `0x5452535f` | `0x140001cd0` | Set Real Time |
| `_CWS` | `_SWC` | `0x5357435f` | `0x140001800` | Clear Wake Alarm Status |
| `_STP` | `_PTS` | `0x5054535f` | `0x140001ea0` | Set Expired Timer Wake Policy |
| `_STV` | `_VTS` | `0x5654535f` | `0x140001f10` | Set Timer Value |
| `_TIP` | `_PIT` | `0x5049545f` | `0x140001ad0` | Timer In Policy |
| `_TIV` | `_VIT` | `0x5649545f` | `0x140001b40` | Timer Value Remaining |
| `_GCP` | `_PCG` | `0x5043475f` | `0x140001ea0` | Get Capabilities |

---

### 2.3 SSAM Command Dispatch Helper (`0x1400343d4`)

All method handlers route their hardware requests through the SSAM command dispatcher at `0x1400343d4`:

```asm
140034466: movb    %bl, -0x28(%rbp)        # TID = 0x01 (Clipboard SAM EC)
140034479: movb    $0x1, -0x27(%rbp)       # TC  = 0x01 (Core/System / RTC)
140034470: movb    %sil, -0x26(%rbp)       # IID = 0x00
14003442e: movzbl  %r8b, %r12d             # CID (passed in %r8b)
```

The parameters to `0x1400343d4` are:
- `%rcx`: DMF context pointer
- `%dl`: Target ID (`TID = 0x01`)
- `%r8b`: Command ID (`CID`)
- `%r9d`: Instance ID (`IID = 0x00`)
- Stack `+0x20`: Input payload buffer pointer
- Stack `+0x28`: Input payload length
- Stack `+0x30`: Output response buffer pointer
- Stack `+0x38`: Output response buffer capacity

---

### 2.4 Detailed Method Implementations

#### 1. `_GRT` (Get Real Time, `0x140001980`):
- **Command:** `TC = 0x01, TID = 0x01, CID = 0x1f, IID = 0x00`
- **Input:** 0 bytes
- **Output:** 16-byte `ACPI_TIME` buffer (`struct.Struct("<HBBBBBBHhBBBB")`)
- **Disassembly:**
  ```asm
  140001a17: movq    $0x10, 0x38(%rsp)      # Response capacity: 16 bytes
  140001a20: leaq    0x40(%rsp), %rcx       # Response buffer
  140001a25: movq    %rcx, 0x30(%rsp)
  140001a2a: xorl    %r9d, %r9d             # IID = 0
  140001a35: movb    $0x1f, %r8b            # CID = 0x1f
  140001a3d: movb    $0x1, %dl              # TID = 0x01
  140001a3f: callq   0x1400343d4            # Dispatch to SAM
  140001a44: movl    %eax, %ebx
  140001a46: testl   %eax, %eax
  140001a48: jns     0x140001a7d
  ...
  140001a7d: cmpb    %sil, 0x47(%rsp)       # Check Valid bit at offset +0x07 != 0
  140001a82: jne     0x140001a8b
  140001a84: movl    $0xc0000001, %ebx      # STATUS_UNSUCCESSFUL if Valid == 0
  ```
- **Validation:** At `0x140001a7d`, the handler inspects byte `+0x07` (`Valid`). If zero, it fails the request with `STATUS_UNSUCCESSFUL` (`0xC0000001`).

#### 2. `_SRT` (Set Real Time, `0x140001cd0`):
- **Command:** `TC = 0x01, TID = 0x01, CID = 0x20, IID = 0x00`
- **Disassembly:**
  ```asm
  140001d34: movl    (%rdi), %eax           # ACPI argument signature
  140001d3b: cmpl    $0x43696541, %eax      # 'AeiC'
  140001d40: je      0x140001d7f
  140001d42: cmpl    $0x4e696541, %eax      # 'AeiN'
  140001d47: je      0x140001d7f
  ...
  140001d85: cmpw    %r12w, 0x12(%rdi)      # Validate DataLength == 0x10 (16 bytes)
  140001d8a: je      0x140001dbd
  ...
  140001dff: leaq    0x14(%rdi), %rax       # 16-byte payload buffer pointer
  140001e03: movq    %r12, 0x28(%rsp)       # Payload length: 16 bytes
  140001e08: movb    $0x1, %dl              # TID = 0x01
  140001e0a: movq    %rax, 0x20(%rsp)
  140001e0f: xorl    %r9d, %r9d             # IID = 0x00
  140001e12: movb    $0x20, %r8b            # CID = 0x20
  140001e15: callq   0x1400343d4            # Dispatch to SAM
  ```
- **Input:** 16-byte `ACPI_TIME` buffer.
- **Output:** 4-byte status code (`0` = success).

#### 3. `_STV` (Set Timer Value, `0x140001f10`):
- **Command:** `TC = 0x01, TID = 0x01, CID = 0x1a, IID = 0x00`
- **Input:** 8-byte buffer:
  - `TimerIdentifier` (uint32, offset 0): `0` = AC wake alarm, `1` = DC wake alarm
  - `TimerValue` (uint32, offset 4): timeout in seconds
- **Output:** 4-byte status code.

#### 4. `_CWS` (Clear Wake Status, `0x140001800`):
- **Command:** `TC = 0x01, TID = 0x01, CID = 0x1c, IID = 0x00`
- **Input:** 4-byte `TimerIdentifier` (uint32).
- **Output:** 4-byte status code.

#### 5. `_TIV` (Timer Value Remaining, `0x140001b40`):
- **Command:** `TC = 0x01, TID = 0x01, CID = 0x1d, IID = 0x00`
- **Input:** 4-byte `TimerIdentifier` (uint32).
- **Output:** 4-byte remaining seconds (uint32).

#### 6. `_GCP` (Get Capabilities, `0x140001ea0`):
- **Handler:** Returns immediate bitmask `0x05`:
  - Bit 0 (`ACPI_TAD_AC_WAKE`, `0x01`): AC wake timer supported
  - Bit 2 (`ACPI_TAD_RT`, `0x04`): Real-Time Clock supported
  - Total Capabilities: `0x05`.

---

### 2.5 The 16-byte `ACPI_TIME` Structure Specification

The exact wire format expected by `CID = 0x1f` and `CID = 0x20` matches ACPI 6.x Section 9.17:

```c
struct acpi_time {
    uint16_t year;          // Offset 0x00: 1900 - 9999
    uint8_t  month;         // Offset 0x02: 1 - 12
    uint8_t  day;           // Offset 0x03: 1 - 31
    uint8_t  hour;          // Offset 0x04: 0 - 23
    uint8_t  minute;        // Offset 0x05: 0 - 59
    uint8_t  second;        // Offset 0x06: 0 - 59
    uint8_t  valid;         // Offset 0x07: Bit 0 = 1 if valid, 0 if invalid
    uint16_t milliseconds;  // Offset 0x08: 0 - 999
    int16_t  timezone;      // Offset 0x0A: Bias in minutes from UTC (-1440 to 1440, 2047 = unspecified)
    uint8_t  daylight;      // Offset 0x0C: Bit 0 = Daylight saving, Bit 1 = In daylight saving
    uint8_t  pad0;          // Offset 0x0D: Reserved (0x00)
    uint8_t  pad1;          // Offset 0x0E: Reserved (0x00)
    uint8_t  pad2;          // Offset 0x0F: Reserved (0x00)
} __attribute__((packed));  // Total size: 16 bytes
```

Python `struct` format: `"<HBBBBBBHhBBBB"` (16 bytes).

---

## 3. Reverse Engineering `SurfaceIntegrationDriver.sys`

- **Binary Path:** `/home/jmvp/work/surface-RE/SurfaceUpdate/surfaceintegration/SurfaceIntegrationDriver.sys`
- **INF Path:** `/home/jmvp/work/surface-RE/SurfaceUpdate/surfaceintegration/SurfaceIntegrationDriver.inf`
- **Target Hardware ID:** `ACPI\MSHW0117`
- **Role:** Surface Integration Driver (power policy, slider overlay, thermal coordination, SAM battery telemetry & emergency triggers).

### 3.1 `SamHibernateNotificationEnabled` Registry Key

In `SurfaceIntegrationDriver.inf`:
```ini
[SurfaceIntegrationDriver_Device.NT.HW]
HKLM,SYSTEM\CurrentControlSet\Control\Power, "SamHibernateNotificationEnabled", %REG_DWORD%, 0x1 ; Enable hibernate when triggered by SAM (battery RSOC < 3%)
```

This configuration directive instructs the driver to enable emergency hardware hibernation whenever the SAM EC reports that the battery Relative State of Charge (RSOC) drops below 3%.

---

### 3.2 Event Registration (`0x140026391`)

In `SurfaceIntegrationDriver.sys` at `0x140026391`, the driver registers a notification handler via `Dmf_SamNotificationViaSsh`:

```asm
14002637a: leaq    -0x24db1(%rip), %rax    # Callback handler: 0x1400015d0
140026381: movq    %rax, -0x58(%rbp)
140026391: movb    $0x4, -0x70(%rbp)       # TC  = 0x04 (Power / Battery / Thermal)
140026395: movb    $0x1, -0x6e(%rbp)       # TID = 0x01 (SAM EC)
14002639c: movl    $0x3, -0x64(%rbp)       # CID = 0x03 (Emergency Critical Battery Event)
1400263a3: callq   0x14002c780             # DMF_SamNotificationViaSsh_RegisterForTargetNotification
```

Parameters registered:
- Target Category: `TC = 0x04`
- Target ID: `TID = 0x01`
- Instance ID: `IID = 0x00`
- Command/Event ID: `CID = 0x03`
- Callback Handler: `0x1400015d0`

---

### 3.3 Event Handler Callback (`0x1400015d0` $\rightarrow$ `0x1400014f0`)

When SAM generates an asynchronous event on `TC = 0x04`:
```asm
14000166e: cmpq    $0x3, %rbx              # Check CID == 0x03
140001672: jne     0x140001682
140001674: movq    0x10(%rax), %rcx
140001678: movq    %rdi, %rdx
14000167b: callq   0x1400014f0             # Invoke Emergency Hibernate Trigger
```

Inside `0x1400014f0`:
```asm
1400014f6: movzbl  0x2(%rdx), %r8d
1400014fb: cmpb    $0x3, %r8b
1400014ff: je      0x140001558
...
140001558: callq   0x14002e694             # Initiate System Hibernate
```
`0x14002e694` calls `ZwInitiatePowerAction(PowerActionHibernate, PowerSystemHibernate, 0, FALSE)` to commit an immediate emergency platform hibernation before SAM cuts battery power.

---

## 4. Linux Implementation Architecture

### 4.1 Comparison of Integration Approaches

| Approach | Complexity | Kernel Changes | Robustness | Selected |
|---|---|---|---|:---:|
| **A. Kernel ACPI Filter Driver** | High | Requires out-of-tree kernel module patching ACPI TAD table evaluation | Fragile across kernel updates | No |
| **B. Direct SSAM Device Access (`/dev/surface/aggregator`)** | Low | None (standard `surface_aggregator_cdev`) | Direct hardware query, zero kernel patching | **Yes** |

### 4.2 The Solution Components

1. **`surface-sam-rtc.py` (`/home/jmvp/work/surface-RE/surface-rtc/surface-sam-rtc.py`):**
   - User-space CLI tool communicating directly with `/dev/surface/aggregator` via `SSAM_CDEV_REQUEST_IOCTL`.
   - Subcommands:
     - `status`: Reads live SAM EC RTC via `TC=0x01, TID=0x01, CID=0x1f, IID=0x00`, unpacks 16-byte `ACPI_TIME`, and compares against `CLOCK_REALTIME` and CMOS `rtc0`.
     - `sync-utc`: Writes current UTC time to SAM EC RTC via `TC=0x01, TID=0x01, CID=0x20, IID=0x00` and verifies readback within $\pm 2\,\text{s}$.
     - `selftest`: Full unit test suite for packet packing, unpacking, field validation, and epoch math.

2. **Updated `surface-hibernate-rtc-sync.sh` (`/home/jmvp/dotfiles/profiles/surface/scripts/surface-hibernate-rtc-sync.sh`):**
   - Dynamically inspects the live SAM EC hardware RTC offset via `read_sam_ec_offset_sec()` before hibernation.
   - If `/dev/surface/aggregator` is active, it uses the exact live hardware offset (whether $0\,\text{s}$ or $-25{,}179\,\text{s}$).
   - Widened valid offset window to $\pm 14\,\text{hours}$ (`-50400` to `+50400` seconds) so that once SAM RTC is synced to UTC, `offset = 0` is accepted and saved.
   - Preserves complete fallback logic and mock test harness.

---

## 5. Live Hardware Verification on Surface Book 3 (`surface`, 2026-11-11)

Running `surface-sam-rtc status` (`TC = 0x01, TID = 0x01, IID = 0x00, CID = 0x1f`) on the live Surface Book 3 returned:
```text
SAM EC Clock:       2026-11-11T20:21:54.8194Z (epoch: 1794428514)
SAM EC Valid Flag:  1 (is_valid: True)
SAM EC Timezone:    2047 min, Daylight: 3
SAM EC Raw Hex:     ea070b0b141536010220ff0703000000
--------------------------------------------------------------------------------
System UTC Time:    2026-11-12T03:21:19.219507Z (epoch: 1794453679.22)
CMOS rtc0 Epoch:    1794453679 (offset to sys: -0.2s)
--------------------------------------------------------------------------------
SAM EC Offset:      -25165.2s (-6.99h relative to system UTC)
```
- **Exact Match with Empirical UEFI POST Offset:** Prior to this query, `/var/lib/surface-hibernate-rtc/offset_sec` (calibrated empirically from `rtc0` after hibernation wake) was `-25165`. The live SSAM `CID = 0x1f` response measured `-25165.2s` — matching to the exact second (`0.2s` sub-second precision) and proving that Surface UEFI POST initializes CMOS `rtc0` directly from the SAM EC clock exposed by `TC = 0x01, TID = 0x01, CID = 0x1f`.
