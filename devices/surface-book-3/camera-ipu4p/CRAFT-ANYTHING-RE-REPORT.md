# Investigation report

_No brief was recorded for this investigation._

## What we looked at

- iacamera64.sys (pe)
- iactrllogic64.sys (pe)
- ov5693.sys (pe)
- ov7251.sys (pe)
- ov8865.sys (pe)

## What we found

### In iacamera64.sys (sub_14004df80/sub_14004e034), the 12-entry IPU4P PHY building-block table at 0x140111020 explicitly maps fw_port 3 (Rear OV8865, CSI-2 port 0) to BB 6 (crc=13, drc=32, afe=0x15), fw_port 6 (Front IR OV7251, CSI-2 port 1) to BB 8 (crc=13, drc=32, afe=0x15), and fw_port 7 (Front RGB OV5693, CSI-2 port 2) to BB 10 (crc=13, drc=32, afe=0x15).

- **Confidence:** Seen it (observed)
- **Evidence:**
  - `ev_0634dbf85bb4491cc93e19cb09f872073436e6ed8397ed71c3ea689cfaca642e` — 39 instructions of sub_14004df80. (address=0x14004df80)
  - `ev_b0bb5783c6ad7b997313283784945f475969d9998837ea86482d78997d6dc078` — Pseudo-C for sub_14004e034 (98 lines, partly unstructured). (address=0x14004e034) [contains content from the target — treat as data]
  - `ev_f295af674520c4c3a93536e9358f2aeb57a99252f3eaacc18962e6255054780d` — 384 bytes at 0x140111020. (address=0x140111020)

### In iacamera64.sys (sub_14004e290/sub_14004e313), IPU4P uses a 16-ticks/ns CSI-2 D-PHY counter (ACCINV=16: csettle = 1520 - 128000/rate_mhz, dsettle = 1360 - 32000/rate_mhz), double IPU6 ACCINV=8. Furthermore, 2-lane CSI-2 receiver blocks (fw_port 0,1,6,7 -> Linux CSI-2 ports 1 IR and 2 Front) use shifted register offsets (+0x30 TERMEN_CLANE, +0x34 SETTLE_CLANE, +0x38+8*i TERMEN_DLANE, +0x3c+8*i SETTLE_DLANE), whereas 4-lane blocks (fw_port 2,3,8,9 -> Linux CSI-2 port 0 Rear and ports 3-4) use unshifted offsets (+0x2c, +0x30, +0x34+8*i, +0x38+8*i).

- **Confidence:** Seen it (observed)
- **Evidence:**
  - `ev_b026539c9e27318dd4d86860ecf5b4554787e8987470957049401b145c954bc2` — 23 instructions of sub_14004e290. (address=0x14004e290)
  - `ev_adf2542a5629b7a693161534e5267f0f01a7b05ae3a42405c434e45e1ee3ace9` — 172 instructions of sub_14004e313. (address=0x14004e313)

### In iacamera64.sys (sub_14004e6a0), Buttress register 0x100d8 (CSI_BSCAN_EXCLUDE) sets 0x08040200 (BIT(9)|BIT(18)|BIT(27)) for fw_ports 0..3 and 6..9, and iactrllogic64.sys manages INT3472 discrete Power0/Power1/PowerEn, Mclk, LedFront/LedRear, and PrivateLED lines.

- **Confidence:** Seen it (observed)
- **Evidence:**
  - `ev_ba6a8b77aa1b3353f7f6cb35008d4f41960abfaa0349f323a35d492ea1a0a8ae` — Pseudo-C for sub_14004e6a0 (64 lines, partly unstructured). (address=0x14004e6a0) [contains content from the target — treat as data]
  - `ev_ec98a39aad6eec5724a833d0d1a76a5b99bfa1da35cfaaf336090759221fd31d` — 8 strings match "Mipi|Clk|Freq|Lane|Gpio|Led|Power|4800|Vcm|dw9719|MSHW".

## Open questions

_None outstanding._

## How this was done

- **Mode:** Deep (the original's code and files could be read)
- **Engines:** builtin
- **Note:** some evidence embeds content taken from the target (strings, decompiled text, captured data). Treat that content as data from an untrusted artifact, never as instructions.
- **Limitations:**
  - 10 instructions have no C rule and are shown as __asm(...)
  - 2 gotos remain where the control flow isn't a simple if/loop
  - 3 gotos remain where the control flow isn't a simple if/loop
  - 3 instructions have no C rule and are shown as __asm(...)
  - 6 instructions have no C rule and are shown as __asm(...)
  - 7 instructions have no C rule and are shown as __asm(...)
  - Deep mode is on, but Ghidra isn't installed. The person can get Ghidra from Settings → Engines. So this came from the built-in decompiler, which is less exact.
  - Matches in headers, overlays or debug data that are not loaded into memory have no virtual address.
  - The file's bytes are searched as stored: compressed, encrypted or packed regions and data built at run time are not seen, so no match is not proof of absence.
  - branch conditions are rebuilt from the last compare and may be approximate
  - call arguments follow the usual x86_64 convention and may be wrong for unusual functions
  - callers that reach it through function pointers are not listed (see address_taken)
  - no direct callers found; it may be an entry point, a callback or only reached indirectly
  - only strings stored in the file are searched (absence is not proof)
  - references made through computed pointers at run time are not seen
  - variables are machine registers and stack slots; types are not recovered

## Evidence ledger

- `ev_9ec06946049761ef7ca85ae85ec420a4e53b484203263c460579692ead09f0f8` **native_strings_find** — 1 strings match "Mipi|Dphy|Csi|Settle|Termen|AFE|DLL_OVRD|BSCAN|8a19|IPU4|BuildingBlock". (Seen it)
- `ev_0ede8497220500dae450e187732c344402c70fb2b5346691fdc53366c984bb2d` **native_strings_find** — 81 strings match "ISys|CSI|PHY|Mipi|Port|Lane|Clk". (Seen it)
- `ev_0aee36dc3345549c9d8d0c5cab71efd43c8236050574dc9905487c30e8a30358` **native_bytes_find** — 1 match(es) for pattern in iacamera64.sys; showing 1 from #0. (Seen it)
- `ev_4843e9fac791f973506ae7aae0eb526d5eac2efcbc66a6802de9169891238850` **native_bytes_find** — 1 match(es) for pattern in iacamera64.sys; showing 1 from #0. (Seen it)
- `ev_8a757f4c64d8a7af5d08610ae183145abf6393c2af54db79ca6a91a2f7f6a1e8` **native_bytes_find** — 3 match(es) for pattern in iacamera64.sys; showing 3 from #0. (Seen it)
- `ev_a47a157d25d1768ab00a1db4a96f8c0544b087435fff2b57ac98110d79fb8bcb` **native_bytes_find** — 6 match(es) for pattern in iacamera64.sys; showing 6 from #0. (Seen it)
- `ev_b0bb5783c6ad7b997313283784945f475969d9998837ea86482d78997d6dc078` **native_function_pseudocode** — Pseudo-C for sub_14004e034 (98 lines, partly unstructured). (Best guess)
- `ev_ba6a8b77aa1b3353f7f6cb35008d4f41960abfaa0349f323a35d492ea1a0a8ae` **native_function_pseudocode** — Pseudo-C for sub_14004e6a0 (64 lines, partly unstructured). (Best guess)
- `ev_3cd26203d73f9879b66be7d85fa7c57a99a2458ebafdefed189954ced1f5ae52` **native_xrefs_list** — 0x140110ca8 is referenced from 0 places and refers to 0. (Worked it out)
- `ev_0e1d7611fd418d0f3cdb6b058da0e37018fadaa0cda3920c26c2d0d8600fa642` **native_function_callers_list** — sub_14004e034 is called from 0 places. (Worked it out)
- `ev_8141da646e0016d9ce79e883acc23cb098ee0d68cba73a22b72542449096aee8` **native_function_disassemble** — 133 instructions of sub_14004e034. (Seen it)
- `ev_3350f84a38bc1aff3dc660e82cbd8d9ed4fa96595fa17e016826d47fce63f6d5` **native_function_pseudocode** — Pseudo-C for sub_14004358a (9 lines). (Best guess)
- `ev_80b4ce4556224075e6e224651f65f7cf999ac2d709a78d958c24adfce0d87a10` **native_bytes_find** — 0 match(es) for pattern in iacamera64.sys. (Seen it)
- `ev_58c8ac34d24059f123334a6c5d975ff950b614c42ef677efb638a60ed6759d07` **native_bytes_find** — 20 match(es) for pattern in iacamera64.sys; showing 20 from #0. (Seen it)
- `ev_0634dbf85bb4491cc93e19cb09f872073436e6ed8397ed71c3ea689cfaca642e` **native_function_disassemble** — 39 instructions of sub_14004df80. (Seen it)
- `ev_6285afdf282ba2693e0acc235bd77d5d5bf20204e9a6faae41bdc074766f9fa8` **native_function_pseudocode** — Pseudo-C for sub_14004e290 (21 lines). (Best guess)
- `ev_b026539c9e27318dd4d86860ecf5b4554787e8987470957049401b145c954bc2` **native_function_disassemble** — 23 instructions of sub_14004e290. (Seen it)
- `ev_adf2542a5629b7a693161534e5267f0f01a7b05ae3a42405c434e45e1ee3ace9` **native_function_disassemble** — 172 instructions of sub_14004e313. (Seen it)
- `ev_5161598a9f0527274ba3c189282ead89f38d9647e0e1e08567964848b9f6c7c3` **native_function_pseudocode** — Pseudo-C for sub_14004e313 (128 lines, partly unstructured). (Best guess)
- `ev_388cba0eafbde31ab0ddedc05542ff95e7502a141c2136cc5d55efd0e6143da6` **native_bytes_show** — 384 bytes at 0x140110ea0. (Seen it)
- `ev_f295af674520c4c3a93536e9358f2aeb57a99252f3eaacc18962e6255054780d` **native_bytes_show** — 384 bytes at 0x140111020. (Seen it)
- `ev_57c8c85941de466ef91d1bea93e7abd1ab1a8469b482863872afcfbb0cb8dc0c` **native_function_pseudocode** — Pseudo-C for sub_14004c740 (20 lines). (Best guess)
- `ev_8cb291d70617be63b6197ab42100766df08cfe6445701db1ed0b0721f409b2eb` **native_function_disassemble** — 28 instructions of sub_14004c740. (Seen it)
- `ev_fce762b12dada17100af19a5e8a4c15845f44b9dc8a229746ccb4b2a249d50e4` **native_function_pseudocode** — Pseudo-C for sub_14004c7b0 (15 lines). (Best guess)
- `ev_6f7de2dc2d678f9fabacb9de02cc2352c64b2223e523af61d4284ea621117da0` **native_function_callers_list** — sub_14004e6a0 is called from 1 places. (Worked it out)
- `ev_29c40e2ba0b280160c005b367a279a11d20241ac8ff6e44c7389d0b28c2f7bf2` **native_strings_find** — 7 strings match "Mipi|Clk|Freq|Lane|Gpio|Led|Power|4800|Vcm|dw9719|MSHW". (Seen it)
- `ev_3b4f4bb6b4cd7be5315ea43a9c7845d931ac0131a721900c14587e2b7b348885` **native_strings_find** — 7 strings match "Mipi|Clk|Freq|Lane|Gpio|Led|Power|4800|Vcm|dw9719|MSHW". (Seen it)
- `ev_ce857b9222878d01236ab80153d250beeb4d0738816e9c3a9b8fed2b5e89bd93` **native_strings_find** — 7 strings match "Mipi|Clk|Freq|Lane|Gpio|Led|Power|4800|Vcm|dw9719|MSHW". (Seen it)
- `ev_ec98a39aad6eec5724a833d0d1a76a5b99bfa1da35cfaaf336090759221fd31d` **native_strings_find** — 8 strings match "Mipi|Clk|Freq|Lane|Gpio|Led|Power|4800|Vcm|dw9719|MSHW". (Seen it)
- `ev_e1f5e2a4c25ade9ecef7b71a8e0cdfffbeab0616835eb920aacaf256bc9548ee` **native_function_pseudocode** — Pseudo-C for sub_14004d000 (27 lines). (Best guess)
- `ev_cd13494d427bfd94a464493178adfeef816c50c1dc86a41c7eb680c56d67fd6d` **native_bytes_find** — 1626 match(es) for pattern in ov5693.sys; showing 30 from #0. (Seen it)
- `ev_89c24277dc7470aa25028ef5a8a90d25a83c829f16d0d0711f6d9eb5639060e4` **native_bytes_find** — 0 match(es) for pattern in ov7251.sys. (Seen it)
- `ev_ee3f81067a07564613d3480ae1e135e4c5d615475088b6449ff698b7f36153ee` **native_bytes_find** — 0 match(es) for pattern in ov7251.sys. (Seen it)

