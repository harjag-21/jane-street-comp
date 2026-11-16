# Timing Boundary Explorer — Run Report

- **Timestamp (UTC):** 2026-11-16T12:57:06.716151+00:00
- **Git commit:** `5aac9d5`
- **Evidence class:** `rtl_simulation`
- **Method:** `sweep` (7 trials executed)
- **Program:** `spi_mode0` (38 words, `crc8=0x71`)
- **Boundary status:** `bracketed` — boundary low cycles = `3`
- **Note:** Exact unit-step transition: fail at 2, pass at 3.
- **Oracle reference:** `clock=10 ns`, `delay=23 ns` -> theoretical first passing low cycle `3`

| `run_param` | `low_cycles` | Sampled MISO | Cycles | Event Tag | Pass |
|---|---|---|---|---|---|
| 0 | 2 | `0xd5` | 38 | 0xd5 | FAIL |
| 1 | 3 | `0xaa` | 46 | 0xaa | PASS |
| 2 | 4 | `0xaa` | 54 | 0xaa | PASS |
| 3 | 5 | `0xaa` | 62 | 0xaa | PASS |
| 4 | 6 | `0xaa` | 70 | 0xaa | PASS |
| 5 | 7 | `0xaa` | 78 | 0xaa | PASS |
| 6 | 8 | `0xaa` | 86 | 0xaa | PASS |
