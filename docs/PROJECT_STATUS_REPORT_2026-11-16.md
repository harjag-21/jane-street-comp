# Timing Boundary Explorer — Project Status Report

**Report date:** 2026-11-16  
**Repository:** `Jane Street ASIC` (`feat/v1-engine`; `master` holds the hardened v0.1 baseline at `67f63ab`)  
**Purpose:** high-level snapshot of verified deliverables, physical hardening results, current evidence limits, and remaining submission gates. Detailed logs and exact claim boundaries live in `docs/COMPETITION_PLAN_AND_EVIDENCE.md`.

## Executive summary

Phases A, C, D, and the host Timing Boundary Explorer pipeline of Phase E are now implemented and verified:

- **v0.1 baseline (`master` / `67f63ab`):** Passed RTL simulation (`2/2` Cocotb + standalone Icarus) and full Tiny Tapeout IHP CMOS5L hardening (`80/80` stages, Antenna/LVS/Magic DRC passed, `2.440%` utilization).
- **v1 programmable protocol engine (`feat/v1-engine` / `3194176`):** Replaced the fixed SPI block with a `64 × 16`-word deterministic microsequencer (`src/protocol_sequencer.v`), CRC-8-verified UART loader + `ena & rst_n` deselection reset (`src/project.v`), assembler (`tools/protocol_asm.py`), and live host Timing Boundary Explorer CLI + RTL bridge (`tools/timing_explorer.py`, `tools/sim_explorer_tb.v`).
- **Physical hardening & gate-level (`3194176`):** Passed full **80/80** Tiny Tapeout CMOS5L hardening in `1:20:34` (`0` lint errors/warnings, Magic DRC/Netgen LVS/Antenna passed, `0` setup/hold/max-slew/max-cap violations, `+10.2228 ns` slow setup slack at `20 ns` period, `17.0308%` stdcell utilization per `metrics.json` / `14.747 %` via `tt_tool.py --print-stats`). Post-PnR gate-level Cocotb passed **`4/4`** under Icarus 12.0 functional verification (using a `/tmp`-stripped PDK stdcell copy without `specify`/`delayed_*`; not SDF timing simulation).

- **v1.1 submission RTL & competition experiments (`feat/v1-engine` / `fd670a0`):** Finalized `tt_um_harjag_21_timing_explorer`, `Hardik Jagga` & `pinout` in `info.yaml`, `ui_in[2:1]` selectable `12/25/50 MHz @ 115200` baud dividers, `0xD WAIT_EDGE`, `0x19 OP_PATCH_WORD`, multi-protocol sweeps, execution replay, and differential Ref-vs-DUT fuzzing + minimizer (`tools/diff_minimizer.py`). Verified `--check-docs` (`0` errors), Cocotb RTL (**`4/4` PASS**), full **80/80 CMOS5L hardening** (`1:20:23`, `17.2471%` stdcell utilization / `15.040 %` via `--print-stats`, `+10.9713 ns` slow setup slack, `0` DRC/LVS/antenna/timing violations), and `GATES=yes` Cocotb (**`4/4` PASS**, `212.36 s` functional netlist run).
- **Dynamic contracts, feedback fuzzing benchmark, & noisy target validation (no RTL changes past `fd670a0`):**
  - Dynamic timing-contract checker (`tools/protocol_asm.py`, `tools/contract_audit.py`): **13/13** RTL/negative checks pass across `JZ` ACK/NACK (`106` vs `107` cycles), `SETCOUNT`/`LOOP` (`N=0,1,5`), `WAIT_EDGE` release/timeout, unbounded loop detection, and off-by-one/wrong-outcome rejection (`docs/evidence/rtl-v11-timing-contract-report.md`).
  - Multi-seed black-box feedback fuzzing benchmark (`tools/fuzz_benchmark.py`, `5` seeds × `100` trials = `1,500` RTL evaluations on 4-bug `MODE_SPI_MULTISPEC`): `payload_only` (`0.00` unique bugs, `27.2` sigs) vs `joint_random` (`2.00` unique bugs, `49.6` sigs, `4.6` raw hits) vs `guided_feedback` (`2.40` unique bugs, `48.8` sigs, `21.2` raw hits; excelled on `D3` `80%` @ `41` TTD vs `20%` @ `89`, while losing `D2` `40%` vs `60%` due to exploitation of early hits); all `4` minimized bugs (`D1..D4`) replayed **`5/5`** (`docs/evidence/rtl-v11-feedback-fuzz-benchmark.md`).
  - Noisy target boundary study (`tools/noisy_boundary_study.py`, `MODE_SPI_NOISY`, `37 ± 9 ns` LFSR jitter): distinguished `probabilistic_transition_region` (`[3, 4]` cycles, `0% -> 10.0% -> 63.3% -> 100.0%`, analytic probs inside every 95% Wilson CI) from sharp `0 ns` jitter baseline; naive 1-shot binary search split `4` vs `5` (`41.7%` on `p>=0.90`), whereas repeated-probe binary (`K=20`, `80` trials/run) and repeated sweeps achieved `100%` accuracy on `low_cycles = 5` (`docs/evidence/rtl-v11-noisy-boundary-report.md`).

What remains is pushing to public GitHub (`harjag-21`) to run upstream `.github/workflows/gds.yaml` (`precheck` + timing-capable `gl_test`), capturing an optional physical `--port` hardware trace if available, and merging `feat/v1-engine` → `master` before **2027-01-18**.

## 1. Completed and verified deliverables

### 1.1 Software timing oracle & methodology studies (`experiments/`)
- Deterministic and jitter SPI timing model (`experiments/spi_timing_model.py`) with censored/unbracketed boundary reporting and `15/15` passing unit tests (`python -m unittest discover -s experiments -v`).
- Reproducible paired equal-budget studies (`1,000` seeds, `6` regimes) establishing that fixed sweep (`789/1,000`) and binary search (`803/1,000`) differ by `+1.4` percentage points (`−0.7` to `+3.7` 95% bootstrap interval including zero), so no algorithmic accuracy advantage is claimed.

### 1.2 v1 programmable hardware, assembler, and host explorer (`src/`, `tools/`)
- **Sequencer (`src/protocol_sequencer.v`):** `64 × 16` instruction store, `8`-entry timestamped `MARK` FIFO, `0x0..0xC` opcodes (`OUT`, `OE`, 12-bit `WAIT`, `SAMPLE`, `JZ`, `JMP`, `SETCOUNT`, `LOOP`, `MARK`, `HALT`, simultaneous `OUT_SAMPLE`, parameterized `WAIT_PARAM`), and `ENG_FAULT` safe high-Z handling on `0xD..0xF`. Passes `iverilog -g2012 -Wall -s tb_v1_sequencer` (`test/tb_v1_sequencer.v`).
- **UART loader top-level (`src/project.v`):** `CLKS_PER_BIT = 8` default 8N1 UART RX/TX with 2-FF RX sync, frame-timeout recovery, incremental CRC-8/07, `ena & rst_n` deselection reset, and 8-byte commands (`PING_INFO`, `BEGIN_LOAD`, `WRITE_WORD`, `COMMIT_LOAD`, `START`, `HALT`, `READ_STATUS`, `POP_EVENT`, `READ_CYCLE_CNT`). Passes `4/4` Cocotb end-to-end RTL tests (`test/test.py`).
- **Toolchain & Timing Boundary Explorer (`tools/protocol_asm.py`, `tools/timing_explorer.py`, `tools/sim_explorer_tb.v`):** Symbolic assembler emitting UART TX (`24` words), SPI mode-0 sweep (`38` words), and I2C open-drain ACK/NACK (`62` words) programs; host CLI supporting both serial UART (`--port`) and live Icarus RTL simulation (`--sim`), fixed sweeps, binary boundary search, replayable JSONL trace verification (`replay`), and Markdown boundary reports (`3/3` integration tests PASS in `tools/test_timing_explorer.py`).

### 1.3 Physical CMOS5L hardening & gate-level verification (`fd670a0`)
- Full Tiny Tapeout flow (`./tt/tt_tool.py --pdk ihp-sg13cmos5l --harden`, LibreLane `3.1.0.dev4`, PDK `89d42ee6d2fd9b50b86a1aada6fee6673c3e6008`) passed **80/80 stages** (`1:20:23`) on `8 × 4` tiles (`1.208 mm²` core): `13,122` stdcells (`1,661` sequential), `208,375 um²` stdcell area (`17.2471%` stdcell utilization in `metrics.json` / `15.040 %` via `--print-stats`), `516,305 um` routed wirelength, `0` lint warnings/errors, `0` Magic DRC/Netgen LVS/antenna violations, `+10.9713 ns` slow setup slack, `+0.1084 ns` fast hold slack.
- Gate-level Cocotb (`GATES=yes` on `runs/wokwi/final/nl/tt_um_harjag_21_timing_explorer.nl.v`) passed **4/4** functional checks.

## 2. Remaining work before final submission

1. **GitHub push & upstream CI signoff:** Push to public GitHub (`harjag-21`) to run `.github/workflows/gds.yaml` (`tt-gds-action` precheck + upstream `gl_test`), then merge `feat/v1-engine` into `master`.
2. **External/physical target demonstration (if hardware available):** Exercise `tools/timing_explorer.py sweep --port <serial> --baud <rate>` against an FPGA/carrier-board target and record traces under `docs/evidence/`.

## 3. Claims that must still NOT be made

- Do not claim KLayout DRC/XOR passed (disabled in default Tiny Tapeout `src/config.json`).
- Do not claim SDF back-annotated gate-level timing simulation passed yet (current `GATES=yes` check ran functional cell models in Icarus `12.0` with `specify` blocks stripped).
- Do not claim fabricated silicon measurements or physical I/O pad slew/delay measurements.
- Do not claim statistical superiority of binary search over fixed sweep from the current equal-budget noise studies.
