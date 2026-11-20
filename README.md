# Timing Boundary Explorer (`tt_um_harjag_21_timing_explorer`)

**Author:** Hardik Jagga (`harjag-21`)  
**Target:** Jane Street Protocol Emulator ASIC Competition — Tiny Tapeout IHP 130 nm CMOS5L (`8×4` tiles, submission deadline `2027-01-18`)

A **runtime-reprogrammable protocol emulator and autonomous timing-boundary explorer** designed for post-fabrication configurability. Instead of hardwiring fixed UART, SPI, or I2C state machines, the chip pairs a CRC-8/07 UART control interface (`src/project.v`) with a deterministic `64 × 16`-word microsequencer (`src/protocol_sequencer.v`) and an **on-chip autonomous scan controller (`0x1A`–`0x1D`)**:

1. **Load a protocol program once** (`64 × 16` instructions over `ui_in[0]` / `uo_out[0]`, supporting SPI, UART, open-drain I2C via `OE`-controlled high-Z release, and JTAG TAP `IDCODE` reads).
2. **Configure a timing sweep on-chip** (`OP_CFG_SCAN`: `param_start`, `param_step`, `1..32` steps, pass classification by shifted `sample_reg` or `MARK` event tag, and `expected_val`).
3. **Start one autonomous scan** (`OP_START_SCAN`), letting the chip iterate all configured timing parameters back-to-back with 2-cycle inter-trial turnaround (`uo_out[6]=scan_active`, `uo_out[7]=scan_done`).
4. **Read back compact results** (`OP_READ_SCAN_STATUS` 32-bit pass/fail bitmap + counts, and `OP_READ_SCAN_FAIL` first-failing parameter, sample/tag, cycle count, and event count) or fall back to single-trial / differential host probing (`OP_START`, `OP_PATCH_WORD`, `POP_EVENT`).

## Architecture & Toolchain Summary

- **RTL (`src/project.v`, `src/protocol_sequencer.v`):**
  - **Sequencer (`0x0`–`0xE`):** `NOP`, `OUT`, `OE`, `WAIT`, `SAMPLE`, `JZ`, `JMP`, `LOOP`, `SETCOUNT`, `MARK`, `HALT`, simultaneous `OUT_SAMPLE`, dual-parameter `WAIT_PARAM` / `WAIT_PARAM2`, bounded `WAIT_EDGE`, and transmit shifter `LD_TX` / `LD_TX_OD` / `SHIFT_TX` (`0xE`, push-pull or open-drain MSB/LSB-first looped TX + optional simultaneous sample; safe high-Z `ENG_FAULT` on `0xF` and reserved `0xE400..0xE7FF`), plus per-pin selectable 2-FF input synchronizers (`cfg_sync_mask`), optional execution cycle budget (`cfg_cycle_budget`), and passive input-edge FIFO capture (`cfg_cap_mask`/`cfg_cap_mode`).
  - **Control & Autonomous 1D/2D Scanner (`0x10`–`0x21`):** 8-byte CRC-8/07 UART frames with `ui_in[2:1]` runtime baud dividers, `imem` wipe on `OP_BEGIN_LOAD`, single-word live patching (`0x19 OP_PATCH_WORD`), control/budget/sync/capture configuration (`0x1E OP_SET_CTRL`), autonomous 1D (`0x1A`–`0x1D`) and single-load 2D grid sweeps (`0x1F`–`0x21`, up to `16 × 32`), `stop_reason` diagnostics, and `ena & rst_n` deselection reset.
- **Assembler, Pre-Load Safety Verifier & Cycle-Contract Checker (`tools/protocol_asm.py`):** Symbolic assembler generating UART TX (`24` words single-byte; `22` words 2-byte looped), SPI modes `0–3` sweeps (`38 / 39 / 38 / 39` words; `20` words 2-byte looped), single-load dual-parameter 2D SPI (`59` words), end-to-end SPI target responder (`40` words), I2C open-drain ACK/NACK (`62` words unrolled; `30` / `49` words 1B/2B parameterized via `LD_TX_OD`), I2C clock-stretch probe, and IEEE 1149.1 JTAG TAP `IDCODE` (`50` words) programs, plus `analyze_timing_bounds`, `check_timing_contract`, and `verify_program_safety` ("safe-to-run" pre-load gate).
- **Host CLI, Robustness Studies & End-to-End Showcase (`tools/timing_explorer.py`, `tools/sync_robustness_study.py`, `tools/run_showcase.py`, `tools/sim_explorer_tb.v`):** Unified `ProtocolClient` (`--sim` Icarus RTL or `--port` serial UART) with `run_autonomous_scan`, `run_autonomous_2d_scan`, `verify`, `run_showcase`, JSONL trace replay (`replay --exec`), differential timing fuzzing + minimization (`tools/diff_minimizer.py`), and noisy-target sweeps (`tools/noisy_boundary_study.py`).
- **Software Reference Model & Benchmarks (`experiments/`):** Femtosecond SPI timing oracle (`15/15` unit tests), `experiments/autonomous_scan_eval.py`, and reproducible fuzzing/cartographer studies (`docs/evidence/`).
- **Documentation:** [PROGRAMMABLE_ENGINE_ARCHITECTURE.md](docs/PROGRAMMABLE_ENGINE_ARCHITECTURE.md), [TIMING_BOUNDARY_EXPLORER_ARCHITECTURE.md](docs/TIMING_BOUNDARY_EXPLORER_ARCHITECTURE.md), [COMPETITION_PLAN_AND_EVIDENCE.md](docs/COMPETITION_PLAN_AND_EVIDENCE.md), [PROJECT_STATUS_REPORT_2026-11-16.md](docs/PROJECT_STATUS_REPORT_2026-11-16.md), [info.md](docs/info.md).

## Quickstart & Runnable Examples

**Prerequisites (for clean checkout):** Icarus Verilog (`iverilog >= 12`), GNU `make`, and Python 3.11+ with `pip install -r test/requirements.txt` (all `tools/` and `experiments/` runtime/test scripts use Python standard library only; `pyserial` is required only for `--port` hardware UART).

```bash
# 1. Autonomous 1D & single-load 2D scan benchmark (1D 32-pt + 2D 192-pt surfaces) & end-to-end judge showcase
python experiments/autonomous_scan_eval.py
python tools/run_showcase.py --md-out docs/evidence/SHOWCASE_WALKTHROUGH.md
python tools/sync_robustness_study.py --md-out docs/evidence/rtl-v2-sync-robustness.md

# 2. Explorer CLI + live-RTL multi-protocol, SPI modes 0-3, responder, I2C Fast/Standard, JTAG, 1D/2D scan, v2 sync/budget/capture, & OP_TX multi-byte (12/12)
python -m unittest discover -s tools -v

# 3. Pre-load safety verifier, dynamic cycle-contract audit (13/13), 50 MHz I2C pulse audit, independent target, 150-seed ISA diff, & responder characterization
python tools/timing_explorer.py verify --example i2c-multibyte --param 62 --od-pins 0,1
python tools/contract_audit.py
python tools/i2c_timing_audit.py --md-out docs/evidence/rtl-phase4-i2c-timing-audit.md
python tools/independent_target_eval.py --md-out docs/evidence/rtl-phase4-independent-target-report.md
python tools/isa_diff_fuzz.py --seeds 150 --md-out docs/evidence/rtl-phase4-isa-diff-report.md
python tools/spi_responder_char.py --md-out docs/evidence/rtl-phase4-spi-responder-limits.md
python -m unittest discover -s experiments -v

# 4. Standalone sequencer & JTAG TAP RTL testbenches (Icarus Verilog)
iverilog -g2012 -Wall -s tb_v1_sequencer -o sim_seq.vvp src/protocol_sequencer.v test/tb_v1_sequencer.v && vvp sim_seq.vvp
iverilog -g2012 -Wall -s tb_jtag_tap -o sim_jtag.vvp src/project.v src/protocol_sequencer.v test/jtag_tap_model.v test/tb_jtag_tap.v && vvp sim_jtag.vvp

# 5. Live protocol sweeps, 1D/2D autonomous scan CLI, replay, & differential fuzzing
python tools/timing_explorer.py run-example --sim --example uart
python tools/timing_explorer.py run-example --sim --example uart-multibyte
python tools/timing_explorer.py run-example --sim --example spi-multibyte
python tools/timing_explorer.py run-example --sim --example spi-mode1
python tools/timing_explorer.py run-example --sim --example spi-responder
python tools/timing_explorer.py run-example --sim --example i2c-fast-compliant --i2c-ack 1
python tools/timing_explorer.py run-example --sim --example i2c-multibyte --i2c-ack 1 --param 62
python tools/timing_explorer.py run-example --sim --example i2c-100k --i2c-ack 1
python tools/timing_explorer.py run-example --sim --example jtag
python tools/timing_explorer.py scan --sim --delay-ns 37 --start 0 --step 1 --steps 16 --expect 0xAA
python tools/timing_explorer.py scan2d --sim --surface 0
python tools/timing_explorer.py sweep --sim --delay-ns 23 --min-low 2 --max-low 8 --method sweep --out trace.jsonl --report report.md
python tools/timing_explorer.py replay trace.jsonl --sim --exec --trials 5
python tools/diff_minimizer.py --budget 30 --seed 20261116 --repeats 10
```

## Verification Results & Scope Limits

- **Physical ASIC hardening (`exp/v2-capabilities`, `80/80` IHP CMOS5L LibreLane, `docs/evidence/rtl-v2-hardened-metrics.json`; prior frozen milestones `573f384`, `b783133`, `v1.2-phase4-fallback` @ `12ed559`):**
  - `--check-docs` passed (`0` errors); `Verilator` lint passed (`0` errors, `0` warnings); Cocotb RTL passed **`11/11`** (`18,084,360.01 ns`, adding `test_v2_sync_budget_capture_and_2d_scan` and `OP_BEGIN_LOAD` stale `imem` wipe); `pytest tools experiments` passed **`27/27`** (`12/12` `tools/` + `15/15` `experiments/`); `isa_diff_fuzz.py --seeds 150` passed **`0` mismatches** (`177,547` cycles, `16/16` opcodes plus `WAIT_PARAM2`, 2-FF sync, cycle-budget timeouts, edge capture, and `cmd_halt`-on-`OP_MARK`); standalone `tb_v1_sequencer.v` (`8/8` groups) + `tb_jtag_tap.v` (`321` cycles) passed; [docs/evidence/SHOWCASE_WALKTHROUGH.md](docs/evidence/SHOWCASE_WALKTHROUGH.md) and [docs/evidence/rtl-v2-sync-robustness.md](docs/evidence/rtl-v2-sync-robustness.md) generated cleanly. Prior `master` (`55bd679` / `573f384`) passed upstream GitHub Actions (`test`, `docs`, `gds` [`gds`+`viewer`+`gl_test`+`precheck`]).
  - Full LibreLane flow on `exp/v2-capabilities` completed **`80/80` stages** (`1:46:17`): `20,326` stdcells (`2,538` sequential), `317,787 µm²` active stdcells (`0.318 mm²`, `26.3031%` of `1,208,170 µm²` placeable core [`1.208 mm²`] / `23.355 %` via `--print-stats`), `869,966 µm` routed wirelength, `+10.2321 ns` worst slow setup slack (`20 ns` period), `+0.1093 ns` fast hold slack, and `0` setup/hold/slew/cap/route-DRC/Magic-DRC/Netgen-LVS/antenna violations.
  - Area note: per `tt/tech/ihp-sg13cmos5l/tile_sizes.yaml`, one `1×1` tile is `202.08 × 154.98 µm` (`32 × 1×1 ≈ 1.00 mm²`), while the `8×4` macro bbox (`1724.16 × 710.64 µm`) includes inter-tile routing and power-mux channels (`1,225,260 µm²` die / `1,208,170 µm²` core).
  - Gate-level verification: post-PnR Cocotb against the hardened `exp/v2-capabilities` netlist `tt_um_harjag_21_timing_explorer.nl.v` passed **`11/11`** (`18,084,360.01 ns`, `1683.92 s`), executed as **functional verification** without SDF timing annotation (using a temporary `/tmp` copy of `sg13cmos5l_stdcell.v` with `specify`/`delayed_*` removed for Icarus `12.0`). `KLayout.DRC`/XOR remain disabled in default Tiny Tapeout `src/config.json`.
- **Simulated experiment scope:**
  - All tested autonomous scans matched host-driven results `100%` in RTL simulation (`32/32` 1D SPI bitmap + exact `first_fail` trace; `192/192` exact grid match with `0` mismatches across `12×16` surfaces `S0`, `S4`, `S8` in both 1D column scans and single-load autonomous 2D scan).
  - Reported UART reductions (**`21.33×`**, `6` vs `128` frames on a 32-pt 1D sweep; **`3.25×–3.32×`**, `309`/`303` vs `1005` frames for 1D column scans; and **`12.41×`**, `81` vs `1,005` frames including initial cold program load for single-load autonomous 2D scan) measure **host-control UART traffic in RTL simulation**, not a measured physical/hardware speedup. Note similarly that 2-FF input synchronizer evaluation verifies exact `+2`-cycle pipeline latency shift (`raw` `[3, 4, 6]` vs `synced` `[5, 6, 8]`, `WAIT_EDGE` `+2` cycles) in digital RTL simulation rather than analog metastability MTBF. There are **no FPGA or fabricated-ASIC measurements yet**.
- **Fuzzing conclusion:** Joint timing + payload sampling beats payload-only fuzzing across simulated suites, whereas response-guided mutation does not generally beat blind `joint_random` when pin responses do not gate failing state transitions (`tools/transfer_study.py`).
