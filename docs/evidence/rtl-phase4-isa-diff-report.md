# Phase 4 Randomized ISA Differential Testing Report

Compared `src/protocol_sequencer.v` cycle-by-cycle against independent Python ISA reference model `SequencerRefModel` across **150** reproducible programs (**177,547** simulated clock cycles).

- **Cycle & FIFO Mismatches:** **`0`** (`0` tolerated)
- **Opcodes Executed (`16/16`):** `0x0, 0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8, 0x9, 0xA, 0xB, 0xC, 0xD, 0xE, 0xF`

| Boundary / Branch Feature | Observed Count |
|---|---|
| `JZ` taken / not taken | 124 / 169 |
| `LOOP` taken / fallthrough (`cnt=1` / `cnt=0`) | 85 / 73 / 1 |
| `SAMPLE` shift / `no_shift=1` | 193 / 221 |
| `OUT_SAMPLE` shift / `no_shift=1` | 193 / 175 |
| `MARK` immediate / `MARK_SAMPLE` | 223 / 189 |
| `LD_TX` / `LD_TX_OD` (MSB / LSB / open-drain) | 98 / 99 / 98 |
| `SHIFT_TX` setup / shift-only / shift+sample / open-drain | 116 / 19 / 79 / 39 |
| `WAIT(0)` / `WAIT(4095)` | 70 / 1 |
| `WAIT_PARAM(0)` / saturated `0xFFF` | 21 / 18 |
| `WAIT_EDGE` immediate hit / delayed hit / timeout | 208 / 168 / 56 |
| Event FIFO overflow cycles (`>8` `MARK`s) | 25779 |
| `HALT(keep_oe=0)` / `HALT(keep_oe=1)` | 61 / 64 |
| Illegal opcode (`0xF` + reserved `0xE`) `ENG_FAULT` + Hi-Z (`uio_oe=0`) | 14 (reserved `0xE`: 8) |
| `cmd_halt` mid-run abort (on `OP_MARK`) | 2 (1) |
| `WAIT_PARAM2` (`cmd_param2`) / 2-FF sync seeds | 171 / 37 |
| Cycle-budget timeouts / edge-capture seeds | 9 / 30 |
