# Timing Boundary Explorer (`tt_um_harjag_21_timing_explorer`)

**Author:** Hardik Jagga (`harjag-21`)  
**Target:** Jane Street Protocol Emulator ASIC Competition — Tiny Tapeout IHP 130 nm CMOS5L (`8×4` tiles, deadline `2027-01-18`)

A post-silicon **reprogrammable protocol emulator and cycle-deterministic timing-boundary explorer**. Instead of hardwiring fixed UART, SPI, and I2C peripherals, the ASIC pairs a CRC-8/07 UART loader with a deterministic `64 × 16` microsequencer (`src/protocol_sequencer.v`), enabling a host tool to:

1. **Sweep protocol timing boundaries** across SPI mode-0 sample phase, UART TX/RX bit periods, and I2C open-drain SCL clock-stretching latency (`WAIT_EDGE` timeout/timestamp capture).
2. **Verify cycle-level contracts** against deterministic sequencer timing (`cycle_cnt_o`, `MARK` 8-entry event FIFO).
3. **Run differential timing fuzzing (Ref vs DUT)**, automatically **minimize** multi-transaction sequences and tightest cycle thresholds, and **deterministically replay** saved counterexamples (`tools/diff_minimizer.py`, `tools/timing_explorer.py`).

## Repository structure

- `src/project.v`, `src/protocol_sequencer.v` — v1.1 top-level UART loader (`ui_in[2:1]` selectable `115200` baud dividers for `12/25/50 MHz` + `CLKS_PER_BIT=8` sim mode, `OP_PATCH_WORD`, `ena & rst_n` deselection reset) and `64 × 16` microsequencer (`0x0`–`0xD`, illegal opcodes `0xE`–`0xF` fault safely to `uio_oe=0`).
- `tools/protocol_asm.py` — Symbolic assembler + static cycle-contract analyzer (`uart`, `spi`, `i2c`, `i2c_stretch`, `spi_target`, multi-transaction sequences).
- `tools/timing_explorer.py`, `tools/sim_explorer_tb.v` — Host CLI for live Icarus RTL (`--sim`) and hardware serial UART (`--port`), JSONL replay, and live execution replay (`replay --exec`).
- `tools/diff_minimizer.py` — Differential fuzzing (`payload-only` baseline vs `joint timing + payload`), sequence delta reduction, timing window isolation, and `N`-trial replay.
- `experiments/` — Discrete femtosecond SPI reference timing oracle and equal-budget Monte Carlo search methodology (`15/15` unit tests).
- `test/` — Sequencer unit testbench (`tb_v1_sequencer.v`) and top-level Cocotb suite (`test.py`).
- `docs/` — [PROGRAMMABLE_ENGINE_ARCHITECTURE.md](docs/PROGRAMMABLE_ENGINE_ARCHITECTURE.md), [TIMING_BOUNDARY_EXPLORER_ARCHITECTURE.md](docs/TIMING_BOUNDARY_EXPLORER_ARCHITECTURE.md), [COMPETITION_PLAN_AND_EVIDENCE.md](docs/COMPETITION_PLAN_AND_EVIDENCE.md), [PROJECT_STATUS_REPORT_2026-11-16.md](docs/PROJECT_STATUS_REPORT_2026-11-16.md), [info.md](docs/info.md), and raw logs under `docs/evidence/`.

## Quickstart

```bash
# 1. Standalone sequencer RTL tests (Icarus Verilog)
iverilog -g2012 -Wall -s tb_v1_sequencer -o sim_seq.vvp src/protocol_sequencer.v test/tb_v1_sequencer.v && vvp sim_seq.vvp

# 2. Explorer CLI + live-RTL multi-protocol & minimizer integration tests
python -m unittest discover -s tools -v

# 3. Software timing model & equal-budget tests
python -m unittest discover -s experiments -v

# 4. Run an SPI boundary sweep and save + replay trace
python tools/timing_explorer.py sweep --sim --delay-ns 23 --min-low 2 --max-low 8 --method sweep --out trace.jsonl --report report.md
python tools/timing_explorer.py replay trace.jsonl --sim --exec --trials 5

# 5. Run differential timing fuzzing + counterexample minimization
python tools/diff_minimizer.py --budget 30 --seed 20261116 --repeats 10
```

See [COMPETITION_PLAN_AND_EVIDENCE.md](docs/COMPETITION_PLAN_AND_EVIDENCE.md) for the exact scope, tool versions, physical hardening metrics, and evidence limits of every claim.
