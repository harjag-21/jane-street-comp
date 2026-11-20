# Timing Boundary Explorer End-to-End Showcase Walkthrough

Single automated run executing all pre-load checks, 1D/2D RTL scans, raw vs 2-FF sync boundary checks, runaway budget protection, hardware edge capture, and deterministic trace replay.

## 1. Pre-Load Static Program Verifier

| Program | Words | Verdict |
|---|---|---|
| `spi_1d` | 38 | `SAFE_TO_RUN` |
| `spi_2d` | 59 | `SAFE_TO_RUN` |
| `i2c_2byte_od` | 49 | `SAFE_TO_RUN` |
| `runaway_no_budget` | 3 | `UNSAFE` |
| `runaway_with_budget` | 3 | `SAFE_WITH_BUDGET` |
| `i2c_od_violation` | 3 | `UNSAFE` |

## 2. Live RTL Measurements

- **Chip Info (`OP_PING_INFO`):** `prog_depth=64`, `event_depth=8`, `feature_flags=0xf`
- **1D SPI Boundary + Replay:** `low_cycles >= 4` (`1D scan=7 pass / 2 fail`, replayed `9` cases with `0` mismatches)
- **Single-Load Autonomous 2D Scan (`S0`, 12x16 = 192 pts):** `126` pass / `66` fail in `81` total UART frames (program loaded once)
- **Cycle-Budget Guard:** runaway infinite loop stopped at cycle `25` in `ENG_FAULT` (`stop_reason=CYCLE_LIMIT`)
- **Passive Input-Edge Capture:** `7` pin transitions captured in hardware FIFO without `OP_MARK` instructions: `[{'tag': 247, 'ts_cycles': 9}, {'tag': 255, 'ts_cycles': 15}, {'tag': 247, 'ts_cycles': 21}, {'tag': 255, 'ts_cycles': 27}, {'tag': 247, 'ts_cycles': 33}, {'tag': 255, 'ts_cycles': 39}, {'tag': 247, 'ts_cycles': 45}]`

## 3. Raw vs 2-FF Synchronizer Boundary Shift

| Delay (ns) | Mode | `sync_mask` | Min Passing `low_cycles` |
|---|---|---|---|
| 25 | `raw_bypass` | `0x00` | 3 |
| 25 | `per_pin_miso_sync` | `0x08` | 5 |
| 25 | `global_all_pins_sync` | `0xff` | 5 |
| 37 | `raw_bypass` | `0x00` | 4 |
| 37 | `per_pin_miso_sync` | `0x08` | 6 |
| 37 | `global_all_pins_sync` | `0xff` | 6 |
