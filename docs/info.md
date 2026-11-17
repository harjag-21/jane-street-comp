<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

**Timing Boundary Explorer** is a post-silicon programmable protocol emulator built on Tiny Tapeout IHP 130 nm CMOS5L (`8×4` tiles). Instead of fixed UART, SPI, or I2C state machines, the chip pairs a **CRC-8-verified UART control plane** (`src/project.v`) with a **deterministic single-issue 16-bit microsequencer** (`src/protocol_sequencer.v`):

- **Instruction store & state:** `64 × 16`-bit program memory (`OP_WRITE_WORD`), 8-bit pin value and direction registers (`uio_out[7:0]`, `uio_oe[7:0]`), 1-bit `last_sample`, an 8-bit MSB-first shift register (`sample_reg`), two 8-bit loop counters, a 12-bit runtime sweep parameter (`run_param` via `WAIT_PARAM`), a 16-bit free-running cycle counter (`cycle_cnt`), and an 8-entry timestamped `MARK` event FIFO.
- **Deterministic ISA (`0x0`–`0xD`, `0xE`–`0xF` fault):** Single-cycle instructions (`NOP`, `OUT`, `OE`, `SAMPLE`, `JZ`, `JMP`, `SETCOUNT`, `LOOP`, `MARK`, `HALT`, atomic `OUT_SAMPLE`) plus cycle-exact `WAIT`, `WAIT_PARAM`, and bounded `WAIT_EDGE` holds. Illegal opcodes (`0xE`–`0xF`) immediately halt in `ENG_FAULT` with `uio_oe[7:0] = 0`.
- **Host UART loader (`ui_in[0]` RX, `uo_out[0]` TX, 8N1, runtime baud selector `ui_in[2:1]`):** Uses 8-byte request frames `[0xA5, op, id, arg0..3, crc8]` and response frames `[0x5A, id, status, d0..3, crc8]` (`CRC-8/07`, including `0x19 OP_PATCH_WORD` and autonomous scan opcodes `0x1A`–`0x1D`), with `ui_in[2:1]` selecting `CLKS_PER_BIT` (`00=8` fast sim, `01=104`, `10=217`, `11=434` for `115200` baud at `12/25/50 MHz`), 2-FF RX synchronization, parser timeout recovery, and `ena & rst_n` deselection reset.
- **Autonomous timing scan controller (`0x1A`–`0x1D`):** Executes up to `32` consecutive parameterized trials on-chip (`param_start + i * param_step`), scores each trial against `sample_reg` (`pass_mode=0`) or `last_mark_tag` (`pass_mode=1`), builds a 32-bit pass/fail bitmap, and captures the first-failing trial snapshot (`fail_idx`, `fail_param`, `eng_state`, `sample_reg`, `cycle_cnt`, `last_mark_tag`, `event_count`).
- **Live telemetry outputs:** `uo_out[1] = trial_active`, `uo_out[2] = prog_valid`, `uo_out[4:3] = eng_state`, `uo_out[5] = event_overflow`, `uo_out[6] = scan_active`, `uo_out[7] = scan_done`.

By loading a protocol program (such as SPI mode-0) once and sweeping the 12-bit `START` parameter (`run_param`), a host computer can locate reproducible cycle-level response boundaries and drain `MARK` event timestamps over UART.

## How to test

1. **Assemble and run protocol programs against the RTL simulation bridge** (`tools/timing_explorer.py`, `tools/protocol_asm.py`):
   - `python tools/timing_explorer.py run-example --sim --example uart` (loads an 8N1 UART TX program driving `uio[0]`)
   - `python tools/timing_explorer.py run-example --sim --example i2c --i2c-ack 1` (open-drain START/byte/ACK/NACK/STOP on `uio[0]=SCL`, `uio[1]=SDA`)
   - `python tools/timing_explorer.py sweep --sim --delay-ns 23 --min-low 2 --max-low 8 --method sweep --out trace.jsonl --report report.md` (sweeps SPI mode-0 `WAIT_PARAM` across a delayed target and writes a replayable trace)
   - `python tools/timing_explorer.py replay trace.jsonl` (verifies program CRC-8 and recomputes the boundary summary)
2. **Run repository unit and Cocotb verification suites:**
   - Software timing oracle & equal-budget tests: `python -m unittest discover -s experiments -v`
   - Explorer CLI + live-RTL bridge tests: `python -m unittest discover -s tools -v`
   - Standalone sequencer testbench: `iverilog -g2012 -Wall -s tb_v1_sequencer src/protocol_sequencer.v test/tb_v1_sequencer.v`
   - Full Cocotb top-level verification (`RTL` and `GATES=yes` in a path without spaces): `cd test && make -B`
3. **Hardware testing via UART:** `info.yaml` configures `clock_hz: 50000000` (`50 MHz`, where `ui_in[2:1] = 2'b11` gives `115200` baud; `2'b01` matches `12 MHz` and `2'b10` matches `25 MHz`). Connect a host UART to `ui_in[0]` / `uo_out[0]` and invoke `tools/timing_explorer.py --port <device> --baud <rate>`.

## External hardware

- Required only when testing **I2C open-drain** programs on physical hardware: external pull-up resistors (typically `4.7 kOhm` to the I/O rail) on `uio[0]` (`SCL`) and `uio[1]` (`SDA`), because open-drain mode releases pins via `OE = 0` rather than driving high.
- Standard push-pull UART/SPI targets and logic-analyzer probes can attach directly to `uio[7:0]`. Note: all current timing sweeps, contract checks, and fuzzing studies were executed in RTL/software simulation against behavioral target models (`sim_explorer_tb.v`); no physical hardware bench measurements or fabricated CMOS5L silicon/pad characterizations exist yet.

## AI tool usage disclosure

Portions of repository planning, RTL implementation/lint cleanup, testbench/toolchain code, and documentation formatting were developed with an AI coding assistant, with every RTL, synthesis, place-and-route, DRC/LVS, and simulation result independently executed and verified in the recorded logs under `docs/`.
