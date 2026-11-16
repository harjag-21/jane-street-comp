# Timing Boundary Explorer — Project Status Report

**Report date:** 2026-11-16  
**Repository:** `Jane Street ASIC` (`feat/v1-engine`; `master` holds the hardened v0.1 baseline at `67f63ab`)  
**Purpose:** high-level snapshot of verified deliverables, physical hardening results, current evidence limits, and remaining submission gates. Detailed logs and exact claim boundaries live in `docs/COMPETITION_PLAN_AND_EVIDENCE.md`.

## Executive summary

Phases A, C, D, and the host Timing Boundary Explorer pipeline of Phase E are now implemented and verified:

- **v0.1 baseline (`master` / `67f63ab`):** Passed RTL simulation (`2/2` Cocotb + standalone Icarus) and full Tiny Tapeout IHP CMOS5L hardening (`80/80` stages, Antenna/LVS/Magic DRC passed, `2.440%` utilization).
- **v1 programmable protocol engine (`feat/v1-engine` / `3194176`):** Replaced the fixed SPI block with a `64 × 16`-word deterministic microsequencer (`src/protocol_sequencer.v`), CRC-8-verified UART loader + `ena & rst_n` deselection reset (`src/project.v`), assembler (`tools/protocol_asm.py`), and live host Timing Boundary Explorer CLI + RTL bridge (`tools/timing_explorer.py`, `tools/sim_explorer_tb.v`).
- **Physical hardening & gate-level (`3194176`):** Passed full **80/80** Tiny Tapeout CMOS5L hardening in `1:20:34` (`0` lint errors/warnings, Magic DRC/Netgen LVS/Antenna passed, `0` setup/hold/max-slew/max-cap violations, `+10.2228 ns` slow setup slack at `20 ns` period, `17.0308%` stdcell utilization per `metrics.json` / `14.747 %` via `tt_tool.py --print-stats`). Post-PnR gate-level Cocotb passed **`4/4`** under Icarus 12.0 functional verification (using a `/tmp`-stripped PDK stdcell copy without `specify`/`delayed_*`; not SDF timing simulation).

What remains is hardware/metadata finalization and external validation: locking `clock_hz`, UART `CLKS_PER_BIT`, `author`, `top_module`, and `pinout` in `info.yaml`, adding any physical FPGA/board demo if available, running final `tt_tool.py --check-docs` + final hardening + SDF gate-level check, and merging `feat/v1-engine` → `master` before **2027-01-18**.

## 1. Completed and verified deliverables

### 1.1 Software timing oracle & methodology studies (`experiments/`)
- Deterministic and jitter SPI timing model (`experiments/spi_timing_model.py`) with censored/unbracketed boundary reporting and `15/15` passing unit tests (`python -m unittest discover -s experiments -v`).
- Reproducible paired equal-budget studies (`1,000` seeds, `6` regimes) establishing that fixed sweep (`789/1,000`) and binary search (`803/1,000`) differ by `+1.4` percentage points (`−0.7` to `+3.7` 95% bootstrap interval including zero), so no algorithmic accuracy advantage is claimed.

### 1.2 v1 programmable hardware, assembler, and host explorer (`src/`, `tools/`)
- **Sequencer (`src/protocol_sequencer.v`):** `64 × 16` instruction store, `8`-entry timestamped `MARK` FIFO, `0x0..0xC` opcodes (`OUT`, `OE`, 12-bit `WAIT`, `SAMPLE`, `JZ`, `JMP`, `SETCOUNT`, `LOOP`, `MARK`, `HALT`, simultaneous `OUT_SAMPLE`, parameterized `WAIT_PARAM`), and `ENG_FAULT` safe high-Z handling on `0xD..0xF`. Passes `iverilog -g2012 -Wall -s tb_v1_sequencer` (`test/tb_v1_sequencer.v`).
- **UART loader top-level (`src/project.v`):** `CLKS_PER_BIT = 8` default 8N1 UART RX/TX with 2-FF RX sync, frame-timeout recovery, incremental CRC-8/07, `ena & rst_n` deselection reset, and 8-byte commands (`PING_INFO`, `BEGIN_LOAD`, `WRITE_WORD`, `COMMIT_LOAD`, `START`, `HALT`, `READ_STATUS`, `POP_EVENT`, `READ_CYCLE_CNT`). Passes `4/4` Cocotb end-to-end RTL tests (`test/test.py`).
- **Toolchain & Timing Boundary Explorer (`tools/protocol_asm.py`, `tools/timing_explorer.py`, `tools/sim_explorer_tb.v`):** Symbolic assembler emitting UART TX (`24` words), SPI mode-0 sweep (`38` words), and I2C open-drain ACK/NACK (`62` words) programs; host CLI supporting both serial UART (`--port`) and live Icarus RTL simulation (`--sim`), fixed sweeps, binary boundary search, replayable JSONL trace verification (`replay`), and Markdown boundary reports (`3/3` integration tests PASS in `tools/test_timing_explorer.py`).

### 1.3 Physical CMOS5L hardening & gate-level verification (`3194176`)
- Full Tiny Tapeout flow (`./tt/tt_tool.py --pdk ihp-sg13cmos5l --harden`, LibreLane `3.1.0.dev4`, PDK `89d42ee6d2fd9b50b86a1aada6fee6673c3e6008`) passed **80/80 stages** on `8 × 4` tiles (`1.208 mm²` core): `12,921` stdcells (`1,656` sequential), `525,267 um` routed wirelength, `0` lint warnings/errors, `0` DRC/LVS/antenna violations, `+10.2228 ns` slow setup slack, `+0.1166 ns` fast hold slack.
- Gate-level Cocotb (`GATES=yes` on `runs/wokwi/final/nl/tt_um_example.nl.v`) passed **4/4** functional checks.

## 2. Remaining work before final submission

1. **Resolve board clock, UART divider, and metadata (`info.yaml`):** Lock target `clock_hz`, verify `CLKS_PER_BIT` against the host UART baud (adjusting RTL parameter or runtime divider if moving from `CLKS_PER_BIT = 8`), and fill `author`, unique `top_module`, and `pinout` in `info.yaml`.
2. **External/physical target demonstration (if hardware available):** Exercise `tools/timing_explorer.py --port <serial> --baud <rate>` against an FPGA/carrier-board target and record empirical traces alongside the simulation traces under `docs/evidence/`.
3. **Final docs/precheck & timing-aware GL verification:** Confirm `./tt/tt_tool.py --pdk ihp-sg13cmos5l --check-docs` exits `0` (`docs/info.md` sections are now drafted), run final `--harden`, SDF timing-annotated gate-level simulation (or upstream Tiny Tapeout CI `tt-gds-action`), and precheck after any `info.yaml`/RTL constant updates, then merge `feat/v1-engine` into `master`.

## 3. Claims that must still NOT be made

- Do not claim KLayout DRC/XOR passed (disabled in default Tiny Tapeout `src/config.json`).
- Do not claim SDF back-annotated gate-level timing simulation passed yet (current `GATES=yes` check ran functional cell models in Icarus `12.0` with `specify` blocks stripped).
- Do not claim fabricated silicon measurements or physical I/O pad slew/delay measurements.
- Do not claim statistical superiority of binary search over fixed sweep from the current equal-budget noise studies.
