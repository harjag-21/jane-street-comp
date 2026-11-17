# Programmable Protocol Engine — v1 Architecture

**Status:** implemented in RTL (`src/protocol_sequencer.v`, `src/project.v`), assembled with `tools/protocol_asm.py`, and verified in Icarus Verilog + Cocotb RTL simulation (`test/tb_v1_sequencer.v`, `test/test.py`). Standalone sequencer synthesis/PnR/STA on `ihp-sg13cmos5l` passed; full top-level CMOS5L hardening and gate-level verification are tracked in `docs/COMPETITION_PLAN_AND_EVIDENCE.md`.

## Purpose

The fixed UART-to-SPI path (`master` / `67f63ab`) established a clean Tiny Tapeout CMOS5L physical hardening baseline. The competition design (`feat/v1-engine`) replaces that fixed controller with a post-silicon programmable protocol engine for the **Timing Boundary Explorer**: the host loads any protocol program over UART, sweeps a 12-bit runtime timing parameter across trials, and records sampled bytes plus cycle-accurate `MARK` timestamps.

## Implemented v1 architecture

One deterministic, single-issue microsequencer (`protocol_sequencer` in `src/protocol_sequencer.v`) owns eight bidirectional protocol pins (`uio[7:0]`) and executes one instruction per core clock cycle except during `WAIT` / `WAIT_PARAM` / `WAIT_EDGE` holds. It intentionally omits instruction caches, pipelines, privilege levels, concurrent engines, and autonomous on-chip search loops in v1.

| Block | v1 implementation | Reason |
|---|---|---|
| Program store | `64 × 16`-bit flop array loaded over UART | Supports UART, SPI mode-0 sweep, and I2C open-drain programs within `8×4` tile area without custom SRAM macro integration risk. |
| Protocol pins | `uio[7:0]`; separate 8-bit `uio_out_o` and `uio_oe_o` registers | Supports push-pull and open-drain (`OE`-driven low / high-Z release) protocols with the same ISA. |
| Event FIFO | `8`-entry timestamped FIFO (`ev_tag[7:0]`, `ev_ts[15:0]` from 16-bit `cycle_cnt_o`) with overflow flag | Captures cycle-exact boundary/protocol events (`MARK`) for host readback after a trial. |
| Loader transport | UART RX on `ui_in[0]` (2-FF synchronized), UART TX on `uo_out[0]`, runtime baud divider on `ui_in[2:1]` (`00=CLKS_PER_BIT/8`, `01=104`, `10=217`, `11=434` for `115200` baud at `12/25/50 MHz`) | Reuses the v0.1 host interface while supporting `50 MHz` Tiny Tapeout crystals/PLLs and fast `div8` simulation. |
| Status outputs | `uo_out[1] = trial_active`, `uo_out[2] = prog_valid`, `uo_out[4:3] = eng_state`, `uo_out[5] = event_overflow`, `uo_out[6] = scan_active`, `uo_out[7] = scan_done` | Gives external instrumentation direct single-cycle observability alongside UART telemetry. |
| Engine reset & select | `rst_active_n = rst_n & ena` | Clears `prog_valid`, `uio_oe_o`, event FIFO, and loader state cleanly on chip deselection or reset. |
| Data state | `last_sample_o` (1-bit), `sample_reg_o` (8-bit shift register), `loop_cnt0/1` (8-bit counters), `run_param` (12-bit runtime parameter) | Enables sampled response branching, MSB-first 8-bit capture, bounded loops, and parameterized timing sweeps without a CPU register file. |
| Timing unit | Whole core clock cycles (`1` cycle per non-wait instruction; `WAIT n` adds `n` extra hold cycles, total `n + 1` cycles on the instruction) | Makes timing explicit and verifiable to the exact clock edge. |

## 16-bit instruction format (`tools/protocol_asm.py`, `src/protocol_sequencer.v`)

Each instruction word is `{opcode[3:0], imm[11:0]}` (`opcode[15:12] | operand[11:0]`). When idle (`wait_cnt == 0`) inside `ENG_RUN`, one instruction executes each `posedge clk`:

| Opcode | Mnemonic | Operand encoding (`imm[11:0]`) | Effect |
|---|---|---|---|
| `0x0` | `NOP` | ignored | Advance `pc_o`. |
| `0x1` | `OUT` | `value = imm[7:0]` | Update `uio_out_o <= imm[7:0]`; advance `pc_o`. |
| `0x2` | `OE` | `enable = imm[7:0]` | Update `uio_oe_o <= imm[7:0]` (`0` = high-Z input); advance `pc_o`. |
| `0x3` | `WAIT` | `cycles = imm[11:0]` | Hold outputs and `pc_o` for `imm` additional cycles (`WAIT 0` takes `1` cycle total; `WAIT n` takes `n + 1` cycles total). |
| `0x4` | `SAMPLE` | `pin = imm[2:0]`, `no_shift = imm[11]` | Sample `uio_in[pin]` into `last_sample_o`; if `!no_shift`, shift `{sample_reg_o[6:0], sampled}` into `sample_reg_o`. |
| `0x5` | `JZ` | `addr = imm[5:0]` | Jump to `addr` if `last_sample_o == 0`, else `pc_o + 1`. |
| `0x6` | `JMP` | `addr = imm[5:0]` | Unconditional jump to `addr`. |
| `0x7` | `LOOP` | `ctr = imm[8]`, `addr = imm[5:0]` | If `cur_cnt > 1`, decrement selected counter and jump to `addr`; else zero it and advance `pc_o + 1`. |
| `0x8` | `SETCOUNT` | `ctr = imm[8]`, `count = imm[7:0]` | Set `loop_cnt0` or `loop_cnt1` to `count`; advance `pc_o`. |
| `0x9` | `MARK` | `from_sample = imm[8]`, `tag = imm[7:0]` | Push `{tag_or_sample_reg, cycle_cnt_o}` into the 8-entry event FIFO (sets `event_overflow_o` if full and not simultaneously popped). |
| `0xA` | `HALT` | `keep_oe = imm[0]` | Transition to `ENG_HALTED`; clear `uio_oe_o <= 8'h00` unless `keep_oe == 1`. Preserve program store, `sample_reg_o`, `cycle_cnt_o`, and FIFO for host readback. |
| `0xB` | `OUT_SAMPLE` | `no_shift = imm[11]`, `pin = imm[10:8]`, `val = imm[7:0]` | Atomically drive `uio_out_o <= val` and sample `uio_in[pin]` (into `last_sample_o` and optionally `sample_reg_o`) on the same clock edge, enabling tight SPI clock/sample timing. |
| `0xC` | `WAIT_PARAM` | `offset = imm[7:0]` | Wait `saturate_12b(run_param + offset)` additional cycles (`WAIT_PARAM 0` waits `run_param` extra cycles, so `run_param == 0` takes `1` cycle total). |
| `0xD` | `WAIT_EDGE` | `pol = imm[11]`, `pin = imm[10:8]`, `max = imm[7:0]` | Wait up to `max` (or `run_param` if `max == 0`) cycles for `uio_in[pin] == pol`. Sets `last_sample_o = 1` on hit or `0` on timeout; used for I2C clock stretching and SPI target polling. |
| `0xE–0xF` | reserved | any | Transition to `ENG_FAULT` and immediately force `uio_oe_o <= 8'h00`. |

## UART loading and control protocol (`src/project.v`)

Host requests and device responses use fixed **8-byte frames** with big-endian CRC-8/ATM (`poly = 0x07`, `init = 0x00`, computed over bytes `[0..6]`):

- **Request frame:** `[0xA5, op, req_id, arg0, arg1, arg2, arg3, crc8]`
- **Response frame:** `[0x5A, req_id, status, d0, d1, d2, d3, crc8]`

If `CMD_BODY` or `CMD_CRC` stalls for `64 × CLKS_PER_BIT` cycles, the loader parser returns to `CMD_SYNC` so truncated frames cannot wedge the receiver.

| Request `op` | Command | Request arguments | Response `d0..d3` (on `ST_OK`) |
|---|---|---|---|
| `0x10` | `PING_INFO` | ignored | `d0 = 0x01` (version), `d1 = 64` (`PROG_DEPTH`), `d2 = 8` (`EVENT_DEPTH`), `d3 = {4'd0, prog_valid, event_overflow, eng_state}` |
| `0x11` | `BEGIN_LOAD` | `arg0 = len` (`1..64`), `arg1 = exp_crc8`, `arg2 = exp_sum8` | Clears `prog_valid`; echoes `len, exp_crc, exp_sum, 0` (`ST_BAD_STATE` if `ENG_RUN`, `ST_BAD_ARG` if `len == 0` or `len > 64`) |
| `0x12` | `WRITE_WORD` | `arg0 = addr`, `arg1 = word[7:0]`, `arg2 = word[15:8]` | Sequential write (`addr == load_idx`); updates incremental `calc_crc` and `calc_sum`; returns `addr, next_idx, 0, 0` |
| `0x13` | `COMMIT_LOAD` | ignored | Verifies `load_idx == len`, `calc_crc == exp_crc`, `calc_sum == exp_sum`; sets `prog_valid` (`d3 = 1`) or returns `ST_CHECKSUM` (`0x05`) |
| `0x14` | `START` | `arg0 = param[7:0]`, `arg1[3:0] = param[11:8]` | Starts at `pc_o = 0` with `cmd_param = 12b` if `prog_valid` and not running; clears FIFO & `cycle_cnt_o` |
| `0x15` | `HALT` | ignored | Stops sequencer (`ENG_HALTED`), clears `uio_oe_o`, returns state and `pc` |
| `0x16` | `READ_STATUS` | ignored | `d0 = flags/state`, `d1 = pc`, `d2 = sample_reg`, `d3 = {last_sample, 3'd0, event_count}` |
| `0x17` | `POP_EVENT` | ignored | Returns `d0 = event_count` (pre-pop), `d1 = tag`, `d2 = ts[7:0]`, `d3 = ts[15:8]` and pops FIFO if nonempty |
| `0x18` | `READ_CYCLE_CNT` | ignored | Returns `d0 = cycle_cnt[7:0]`, `d1 = cycle_cnt[15:8]`, `d2 = sample_reg`, `d3 = event_count` |
| `0x19` | `PATCH_WORD` | `arg0 = addr`, `arg1 = word[7:0]`, `arg2 = word[15:8]` | Random-access single-word patch when `prog_valid == 1` and halted (accelerates fuzzing/minimization without reloading all 64 words) |
| `0x1A` | `CFG_SCAN` | `arg0 = start[7:0]`, `arg1 = step[7:0]`, `arg2 = {pass_mode[6], steps[5:0]}`, `arg3 = expected[7:0]` | Configure `1..32` uniform autonomous sweep steps (`ST_BAD_ARG` on `0` or `>32`); `pass_mode=0` checks `sample_reg == expected`, `pass_mode=1` checks nonempty FIFO and `last_mark_tag == expected` |
| `0x1B` | `START_SCAN` | `arg0[3:0] = start[11:8]` | Launch on-chip sweep; sequencer advances `cur_param += step` across trials with 2-cycle inter-trial overhead, asserting `scan_active` (`uo_out[6]`) then `scan_done` (`uo_out[7]`) |
| `0x1C` | `READ_SCAN_STATUS` | `arg0 = page` (`0` or `1`) | `arg0=0`: `{scan_done, scan_active}`, `steps_done`, `pass_cnt`, `fail_cnt`; `arg0=1`: `scan_pass_bitmap[31:0]` (LSB first) |
| `0x1D` | `READ_SCAN_FAIL` | `arg0 = page` (`0` or `1`) | `arg0=0`: `{valid, idx}`, `param[7:0]`, `{state, param[11:8]}`, `sample_reg`; `arg0=1`: `cycle_cnt[15:0]`, `last_mark_tag`, `event_count` for the first failing trial |

Status codes: `0x00 ST_OK`, `0x01 ST_BAD_CRC`, `0x02 ST_BAD_OP`, `0x03 ST_BAD_ARG`, `0x04 ST_BAD_STATE`, `0x05 ST_CHECKSUM`.

## Verified example programs (`tools/protocol_asm.py`, `test/test.py`)

All three acceptance programs compile cleanly via `tools/protocol_asm.py` (`--example uart|spi|i2c`) and execute end-to-end over the UART loader in Cocotb (`test/test.py`):

1. **UART TX (`24` words):** Loaded over `ui_in[0]`, emits `0x55` (`8N1`) on `uio[0]`, and logs `MARK` start/end timestamps (`test_uart_tx_loaded_program`: `PASS`).
2. **SPI Mode-0 Timing Sweep (`38` words):** Loaded over UART and invoked repeatedly via `START(param)` across `run_param` delays; verifies transition from failed capture (`run_param=0`, MISO delayed too long -> `0x00 != 0xA5`) to exact capture (`run_param=8` -> `sample_reg == 0xA5`) plus deterministic `cycle_cnt` scaling (`test_spi_mode0_timing_sweep_over_uart`: `PASS`).
3. **I2C Open-Drain ACK/NACK (`62` words):** Generates START, shifts 8-bit address (`0xA0`) using `OE`-controlled open-drain drive and high-Z release, samples ACK (`0`) vs NACK (`1`), generates STOP, and emits `MARK` tags checked by the host (`test_i2c_open_drain_ack_and_nack`: `PASS`).
