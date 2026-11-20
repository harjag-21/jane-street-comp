# Programmable Protocol Engine — v1 Architecture

**Status:** implemented in RTL (`src/protocol_sequencer.v`, `src/project.v`), assembled with `tools/protocol_asm.py`, and verified in Icarus Verilog + Cocotb RTL simulation (`test/tb_v1_sequencer.v`, `test/test.py`). Standalone sequencer synthesis/PnR/STA on `ihp-sg13cmos5l` passed; full top-level CMOS5L hardening and gate-level verification are tracked in `docs/COMPETITION_PLAN_AND_EVIDENCE.md`.

## Purpose

The fixed UART-to-SPI path (`master` / `67f63ab`) established a clean Tiny Tapeout CMOS5L physical hardening baseline. The competition design replaces that fixed controller with a runtime-reprogrammable protocol engine for the **Timing Boundary Explorer**: the host loads any protocol program over UART, sweeps a 12-bit runtime timing parameter across trials, and records sampled bytes plus cycle-accurate `MARK` timestamps.

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
| Data state | `last_sample_o` (1-bit), `sample_reg_o` (8-bit receive shift register), `tx_reg` (8-bit transmit shift register) + `tx_lsb_first` + `tx_od`, `loop_cnt0/1` (8-bit counters), `run_param` (12-bit runtime parameter) | Enables sampled response branching, MSB-first 8-bit capture, push-pull or open-drain MSB/LSB-first looped multi-byte transmission, bounded loops, and parameterized timing sweeps without a CPU register file. |
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
| `0xC` | `WAIT_PARAM` / `WAIT_PARAM2` | `sel = imm[8]`, `offset = imm[7:0]` | Wait `saturate_12b((sel ? run_param2 : run_param) + offset)` additional cycles (`WAIT_PARAM 0` waits `run_param` extra cycles, `WAIT_PARAM2 0` waits `run_param2` extra cycles; `0` takes `1` cycle total). |
| `0xD` | `WAIT_EDGE` | `pol = imm[11]`, `pin = imm[10:8]`, `max = imm[7:0]` | Wait up to `max` (or `run_param` if `max == 0`) cycles for `uio_in[pin] == pol`. Sets `last_sample_o = 1` on hit or `0` on timeout; used for I2C clock stretching and SPI target polling. |
| `0xE` (`imm[11]=0`) | `LD_TX` / `LD_TX_OD` | `imm[10] = 0`, `od = imm[9]`, `lsb_first = imm[8]`, `val = imm[7:0]` | Load `tx_reg <= val`, `tx_lsb_first <= lsb_first`, and `tx_od <= od` (`0` push-pull `LD_TX`, `1` open-drain `LD_TX_OD`); advance `pc_o`. Reserved sub-encodings `imm[10] = 1` (`0xE400..0xE7FF`) transition to `ENG_FAULT` and clear `uio_oe_o <= 8'h00`. |
| `0xE` (`imm[11]=1`) | `SHIFT_TX` | `do_shift = imm[10]`, `clk_val = imm[9]`, `clk_pin = imm[8:6]`, `rx_pin = imm[5:3]`, `tx_pin = imm[2:0]` | When `tx_od == 0` (push-pull): drive `uio_out_o[tx_pin]` with `tx_cur_bit` and `uio_out_o[clk_pin] <= clk_val` (if `clk_pin != tx_pin`). When `tx_od == 1` (open-drain): force `uio_out_o <= 0` on `tx_pin`/`clk_pin` and drive `uio_oe_o[tx_pin] <= ~tx_cur_bit` (`0`=release high-Z, `1`=pull low) and `uio_oe_o[clk_pin] <= ~clk_val` (if `clk_pin != tx_pin`). If `do_shift == 1`, shift `tx_reg` by 1 bit and (when `rx_pin != tx_pin`) sample `uio_in[rx_pin]` into `last_sample_o` and `sample_reg_o`. |
| `0xF` | reserved | any | Transition to `ENG_FAULT` and immediately force `uio_oe_o <= 8'h00`. |

## UART loading and control protocol (`src/project.v`)

Host requests and device responses use fixed **8-byte frames** with big-endian CRC-8/ATM (`poly = 0x07`, `init = 0x00`, computed over bytes `[0..6]`):

- **Request frame:** `[0xA5, op, req_id, arg0, arg1, arg2, arg3, crc8]`
- **Response frame:** `[0x5A, req_id, status, d0, d1, d2, d3, crc8]`

If `CMD_BODY` or `CMD_CRC` stalls for `64 × CLKS_PER_BIT` cycles, the loader parser returns to `CMD_SYNC` so truncated frames cannot wedge the receiver.

| Request `op` | Command | Request arguments | Response `d0..d3` (on `ST_OK`) |
|---|---|---|---|
| `0x10` | `PING_INFO` | ignored | `d0 = 0x01` (version), `d1 = 64` (`PROG_DEPTH`), `d2 = 8` (`EVENT_DEPTH`), `d3 = {4'hF, prog_valid, event_overflow, eng_state}` (`d3[7:4]` feature flags: synchronizers, cycle budget, edge capture, 2D scan) |
| `0x11` | `BEGIN_LOAD` | `arg0 = len` (`1..64`), `arg1 = exp_crc8`, `arg2 = exp_sum8` | Clears `prog_valid` and wipes `imem[0..63]` to `0x0000` (`NOP`) via 1-cycle `prog_clear`; echoes `len, exp_crc, exp_sum, 0` (`ST_BAD_STATE` if `ENG_RUN`, `ST_BAD_ARG` if `len == 0` or `len > 64`) |
| `0x12` | `WRITE_WORD` | `arg0 = addr`, `arg1 = word[7:0]`, `arg2 = word[15:8]` | Sequential write (`addr == load_idx`); updates incremental `calc_crc` and `calc_sum`; returns `addr, next_idx, 0, 0` |
| `0x13` | `COMMIT_LOAD` | ignored | Verifies `load_idx == len`, `calc_crc == exp_crc`, `calc_sum == exp_sum`; sets `prog_valid` (`d3 = 1`) or returns `ST_CHECKSUM` (`0x05`) |
| `0x14` | `START` | `arg0 = p1[7:0]`, `arg1[3:0] = p1[11:8]`, `arg2 = p2[7:0]`, `arg3[3:0] = p2[11:8]` | Starts at `pc_o = 0` with `run_param = p1`, `run_param2 = p2` if `prog_valid` and not running; clears FIFO & `cycle_cnt_o` |
| `0x15` | `HALT` | ignored | Stops sequencer (`ENG_HALTED`, preserves prior `stop_reason` if already stopped), clears `uio_oe_o` and active scan flags, returns status and `pc` |
| `0x16` | `READ_STATUS` | ignored | `d0 = {1'b0, stop_reason[2:0], prog_valid, event_overflow, eng_state}` (`stop_reason`: `0=NONE, 1=HALT_INSN, 2=ILLEGAL_OP, 3=CMD_HALT, 4=CYCLE_LIMIT`), `d1 = pc`, `d2 = sample_reg`, `d3 = {last_sample, 3'd0, event_count}` |
| `0x17` | `POP_EVENT` | ignored | Returns `d0 = event_count` (pre-pop), `d1 = tag`, `d2 = ts[7:0]`, `d3 = ts[15:8]` and pops FIFO if nonempty |
| `0x18` | `READ_CYCLE_CNT` | ignored | Returns `d0 = cycle_cnt[7:0]`, `d1 = cycle_cnt[15:8]`, `d2 = sample_reg`, `d3 = event_count` |
| `0x19` | `PATCH_WORD` | `arg0 = addr`, `arg1 = word[7:0]`, `arg2 = word[15:8]` | Random-access single-word patch when `prog_valid == 1` and halted (accelerates fuzzing/minimization without reloading all 64 words) |
| `0x1A` | `CFG_SCAN` | `arg0 = start[7:0]`, `arg1 = step[7:0]`, `arg2 = {pass_mode[6], steps[5:0]}`, `arg3 = expected[7:0]` | Configure `1..32` uniform autonomous sweep steps (`ST_BAD_ARG` on `0` or `>32`); `pass_mode=0` checks `sample_reg == expected`, `pass_mode=1` checks nonempty FIFO and `last_mark_tag == expected` |
| `0x1B` | `START_SCAN` | `arg0[3:0] = start[11:8]` | Launch 1D on-chip sweep; sequencer advances `cur_param += step` across trials with 2-cycle inter-trial overhead, asserting `scan_active` (`uo_out[6]`) then `scan_done` (`uo_out[7]`) |
| `0x1C` | `READ_SCAN_STATUS` | `arg0 = page` (`0` or `1`) | `arg0=0`: `{scan_done, scan_active}`, `steps_done`, `pass_cnt`, `fail_cnt`; `arg0=1`: `scan_pass_bitmap[31:0]` (LSB first) |
| `0x1D` | `READ_SCAN_FAIL` | `arg0 = page` (`0..2`) | `arg0=0`: `{valid, idx}`, `param[7:0]`, `{state, param[11:8]}`, `sample_reg`; `arg0=1`: `cycle_cnt[15:0]`, `last_mark_tag`, `{stop_reason, 0, evcnt}`; `arg0=2`: `fail_x_idx`, `param2[11:0]`, `scan2_nx` |
| `0x1E` | `SET_CTRL` | `arg0 = sub` (`0..3`), `arg1..3` | `arg0=0`: set `cfg_sync_mask[7:0]`, `cfg_cap_mask[7:0]`, `cfg_cap_mode[1:0]`; `arg0=1`: set 16-bit `cfg_cycle_budget`; `arg0=2/3`: readback config & `stop_reason` |
| `0x1F` | `CFG_SCAN_2D` | `arg0 = x_start[7:0]`, `arg1 = x_step`, `arg2[4:0] = nx` (`1..16`), `arg3[3:0] = x_start[11:8]` | Configure outer X axis (`1..16` columns) for single-load 2D grid scan alongside Y axis from `CFG_SCAN` |
| `0x20` | `START_SCAN_2D` | `arg0[3:0] = y_start[11:8]` | Launch full `(nx × ny)` on-chip grid scan varying both `cmd_param` (Y) and `cmd_param2` (X) from one loaded program |
| `0x21` | `READ_SCAN_2D` | `arg0` (`0..15`, `0xFE`, `0xFF`) | `0xFF`: `{scan_done, scan_active}`, `total_pass`, `{cur_x, fail_x}`; `0xFE`: `total_fail`, `nx`, `ny`; `0..15`: 32-bit Y pass bitmap for column `x = arg0` |

| Status codes: `0x00 ST_OK`, `0x01 ST_BAD_CRC`, `0x02 ST_BAD_OP`, `0x03 ST_BAD_ARG`, `0x04 ST_BAD_STATE`, `0x05 ST_CHECKSUM`.

## Autonomous on-chip scan operating contract (`0x1A`–`0x1D`)

1. **Typical host sequence:**
   - Load and commit a protocol program (`BEGIN_LOAD` → `WRITE_WORD`* → `COMMIT_LOAD`) so `prog_valid == 1`.
   - Configure the sweep via `CFG_SCAN`: `arg0 = param_start[7:0]`, `arg1 = param_step[7:0]`, `arg2 = {pass_mode[6], num_steps[5:0]}`, `arg3 = expected_val`.
   - Launch via `START_SCAN` with `arg0[3:0] = param_start[11:8]`.
   - Wait for `uo_out[7]` (`scan_done`) or poll `READ_SCAN_STATUS(arg0=0)` until `scan_active == 0 && scan_done == 1`.
   - Read `READ_SCAN_STATUS(arg0=1)` for the 32-bit pass/fail bitmap, and if `fail_cnt > 0`, read `READ_SCAN_FAIL(arg0=0)` and `READ_SCAN_FAIL(arg0=1)` for the first failing snapshot.
2. **Configuration validation & stepping:**
   - `CFG_SCAN` rejects `num_steps == 0` or `num_steps > 32` (low 6 bits `arg2[5:0]`) with `ST_BAD_ARG`, and returns `ST_BAD_STATE` if `eng_state == ENG_RUN` or `scan_active == 1`. Reserved bit `arg2[7]` is ignored. Valid `CFG_SCAN` clears prior `scan_done`.
   - `START_SCAN` returns `ST_BAD_STATE` unless `prog_valid == 1`, `eng_state != ENG_RUN`, `scan_active == 0`, and `scan_num_steps != 0`. Reserved bits `arg0[7:4]` are ignored.
   - Each step `i` (`0 .. scan_num_steps - 1`) sets `run_param` to `scan_cur_param` and advances `scan_cur_param <= scan_cur_param + param_step` (12-bit modular arithmetic).
3. **Pass classification & bitmap encoding:**
   - `pass_mode == 0`: trial `i` passes iff `eng_state == ENG_HALTED && sample_reg == expected_val`.
   - `pass_mode == 1`: trial `i` passes iff `eng_state == ENG_HALTED && event_count != 0 && last_mark_tag == expected_val` (`event_count == 0` always fails even if `expected_val == 0`; if `>8` `MARK`s occur in one trial, `last_mark_tag` still reflects the final `MARK` payload while `event_count == 8 != 0`).
   - `scan_pass_bitmap[i]` is `1` if step `i` passed (`0` if failed or `i >= steps_done`), packed LSB-first (`bit 0` = step `0`, ..., `bit 31` = step `31`). Always `pass_cnt + fail_cnt == steps_done`.
4. **First-failure snapshot:**
   - Cleared on `OP_START_SCAN` (`first_fail_valid = 0`). Latched permanently on the earliest failing step `i` (`!first_fail_valid`) and preserved across any subsequent failures: `{first_fail_valid, first_fail_idx}`, `first_fail_param[11:0]`, `first_fail_state` (`ENG_HALTED`/`ENG_FAULT`), `first_fail_sample`, `first_fail_cycles[15:0]`, `first_fail_tag`, and `first_fail_evcnt[3:0]`.
5. **Lifecycle, abort, reset, and busy guards:**
   - `uo_out[6]` (`scan_active`) stays `1` from `START_SCAN` until step `num_steps - 1` completes (`scan_done` rises on `uo_out[7]`), `OP_HALT`, or reset.
   - `OP_HALT` stops the sequencer and clears `scan_active <= 0` immediately without setting `scan_done` (so `scan_active == 0 && scan_done == 0` distinguishes abort from normal completion, while partial `steps_done`/`pass_cnt`/`fail_cnt`/`first_fail_*` remain readable).
   - Reset (`rst_n == 0` or `ena == 0`) clears all scan state, `scan_active`, `scan_done`, counters, bitmap, and snapshot.
   - While `scan_active == 1`, `BEGIN_LOAD`, `WRITE_WORD`, `COMMIT_LOAD`, `PATCH_WORD`, `START`, `CFG_SCAN`, and `START_SCAN` are rejected with `ST_BAD_STATE`; readback commands (`PING_INFO`, `READ_STATUS`, `READ_SCAN_STATUS`, `READ_SCAN_FAIL`, `READ_CYCLE_CNT`, `POP_EVENT`) remain available.

## Verified example programs (`tools/protocol_asm.py`, `test/test.py`, `test/tb_jtag_tap.v`)

Five protocol demonstrations compile cleanly within the `64`-word `imem` store (`tools/protocol_asm.py`) and execute end-to-end over the UART loader with zero RTL changes:

1. **UART TX (`24` words):** Loaded over `ui_in[0]`, emits `0x55` (`8N1`) on `uio[0]`, and logs `MARK` start/end timestamps (`test_uart_tx_loaded_program`: `PASS`).
2. **SPI Controller Modes 0–3 (`38 / 39 / 38 / 39` words <= `64`):** Parameterized by `(CPOL, CPHA)` via `build_spi_program(..., mode=0..3)`:
   - Modes `0` (`CPOL=0, CPHA=0`) and `2` (`CPOL=1, CPHA=0`) fit in **`38` words**, sampling MISO on the leading edge (`0->1` or `1->0`) and shifting on the trailing edge.
   - Modes `1` (`CPOL=0, CPHA=1`) and `3` (`CPOL=1, CPHA=1`) fit in **`39` words** (adding 1 word to drop `CS_N` before the first leading edge), driving/launching on the leading edge and sampling on the trailing edge.
   - All four modes pass both `OP_START` single trials and autonomous `OP_START_SCAN` sweeps across delayed targets (`test_spi_mode0_timing_sweep_over_uart`, `test_spi_modes_1_to_3_programs`, `test_phase3_spi_modes_responder_and_i2c_rates`: `PASS`).
3. **End-to-End SPI Target-Responder (`40` words <= `64`):** `build_spi_target_responder_program(0x5A, max_wait=0)` drives only `uio_oe[3]` (`MISO`) while polling external `CS_N` (`uio_in[0]`) and `SCLK` (`uio_in[1]`) via `WAIT_EDGE`, returning `0x5A` to an external Mode-0 controller, capturing `0xA5` on `MOSI` (`uio_in[2]`), and trapping `CS_N` timeouts via `JZ -> MARK 0xEE` (`test_spi_target_responder_end_to_end`, `MODE_SPI_EXT_CTRL`, [rtl-phase4-spi-responder-limits.md](evidence/rtl-phase4-spi-responder-limits.md)).
   - **Measured operating limits:** **Mode 0** only; empirical `half_clks = 1..2` fails, `half_clks = 3` is the tight 3-cycle transition cliff (`56` cycles), and **`half_clks >= 4`** (`<= 6.25 MHz` @ `50 MHz`) passes across symmetric, asymmetric (`hi=3, lo=5` / `hi=4, lo=3`), and jittered (`5+/-1`) SCLK; requires glitch-free `CS_N` and a complete 8-bit frame (truncating at 4 bits produces partial `0xa0` after `4 × run_param` timeouts = `312` cycles, and a 2-cycle `CS_N` glitch on a shared bus lets subsequent `CS_N=1` `SCLK` pulses clock `sample_reg`).
4. **I2C Open-Drain ACK/NACK (`62` words <= `64`) & Pulse-by-Pulse `50 MHz` Timing Audit:** Generates START, shifts 8-bit `0xA0` using `OUT = 0` + `OE`-toggled high-Z release (`uio[0]=SCL`, `uio[1]=SDA`), samples ACK (`0`) vs NACK (`1`), generates STOP, and logs `MARK` tags (`test_i2c_open_drain_ack_and_nack`, `test_i2c_100khz_and_400khz_at_50mhz`, [rtl-phase4-i2c-timing-audit.md](evidence/rtl-phase4-i2c-timing-audit.md)).
   - **Clock, pin, and pull-up assumptions (`50 MHz` nominal core clock = `20 ns` period per `info.yaml`, idealized `0 ns` pull-up rise time):** Steady-state bit period in `build_i2c_write_byte_program` equals `2 * half_cycles + 1` cycles (`half_cycles + 1` low, `half_cycles` high; bit 7 initial low = `2 * half_cycles` after `START`):
     - **Nominal 400 kHz (`i2c-400k-nominal`, `half_cycles = 62`):** `125` cycles/bit = **`400.000 kHz`** steady rate, **non-compliant with UM10204 Fast-mode `t_LOW >= 1.30 us`** because steady `t_LOW = 63 × 20 ns = 1260 ns < 1300 ns` (`t_HIGH = 1240 ns`).
     - **Fast-mode Compliant (`i2c-fast-compliant`, `half_cycles = 64`):** `129` cycles/bit = **`387.597 kHz`** (`bit_6..bit_0` `t_LOW = 1300 ns >= 1300 ns`, `t_HIGH = 1280 ns >= 600 ns`, `t_HD;STA = 1280 ns`, `t_SU;DAT >= 1280 ns`, `t_HD;DAT = 20 ns` ACK / `0 ns` NACK, `t_SU;STO = 1280 ns`) — passes **100%** of UM10204 Fast-mode limits across every pulse (`START`, bits `7..0`, `ACK`/`NACK`, `STOP`).
     - **Standard-mode (`i2c-100k`, `half_cycles = 250`):** `501` cycles/bit = **`99.800 kHz <= 100 kHz`** (`t_LOW = 5020 ns >= 4700 ns`, `t_HIGH = 5000 ns >= 4000 ns`, `t_HD;STA = 5000 ns`, `t_SU;STO = 5000 ns`) — passes **100%** of UM10204 Standard-mode limits.
     - **Pull-ups:** Both `uio[0]` (`SCL`) and `uio[1]` (`SDA`) operate strictly open-drain (`(oe & out) == 0`); physical boards require external pull-up resistors sized so `R_p * C_bus` stays within I2C rise-time limits (`<= 300 ns` at Fast-mode, `<= 1000 ns` at `100 kHz`, e.g. `~2.2 kOhm`–`4.7 kOhm` depending on board capacitance).
5. **IEEE 1149.1 JTAG TAP `IDCODE` (`50` words <= `64`):** With zero RTL modifications, resets an independently written behavioral JTAG TAP (`test/jtag_tap_model.v`) from a dirty `PAUSE_DR`/`BYPASS` state via 5× `TMS=1` into `Test-Logic-Reset`, shifts 4-bit `IR=IDCODE (4'h1)`, reads the 32-bit `IDCODE` (`0x1A2B4D6F`) across 4 nested-loop `MARK_SAMPLE` events (`cycle_cnt = 321`), passes `check_timing_contract`, and exercises `TDO` delay sweeping via both `START` and the autonomous scan controller (`test/tb_jtag_tap.v`, `test_jtag_tap_idcode_read_contract_and_tdo_sweep`: `PASS`).
