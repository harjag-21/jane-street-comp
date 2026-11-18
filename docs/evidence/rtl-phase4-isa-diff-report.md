# Phase 4 Randomized ISA Differential Testing Report

Compared `src/protocol_sequencer.v` cycle-by-cycle against independent Python ISA reference model `SequencerRefModel` across **120** reproducible programs (**235,034** simulated clock cycles).

- **Cycle & FIFO Mismatches:** **`0`** (`0` tolerated)
- **Opcodes Executed (`16/16`):** `0x0, 0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8, 0x9, 0xA, 0xB, 0xC, 0xD, 0xE, 0xF`

| Boundary / Branch Feature | Observed Count |
|---|---|
| `JZ` taken / not taken | 100 / 143 |
| `LOOP` taken / fallthrough (`cnt=1` / `cnt=0`) | 58 / 62 / 1 |
| `SAMPLE` shift / `no_shift=1` | 166 / 186 |
| `OUT_SAMPLE` shift / `no_shift=1` | 175 / 158 |
| `MARK` immediate / `MARK_SAMPLE` | 177 / 157 |
| `LD_TX` MSB-first / LSB-first | 87 / 86 |
| `SHIFT_TX` setup / shift-only / shift+sample | 92 / 19 / 71 |
| `WAIT(0)` / `WAIT(4095)` | 64 / 1 |
| `WAIT_PARAM(0)` / saturated `0xFFF` | 18 / 23 |
| `WAIT_EDGE` immediate hit / delayed hit / timeout | 190 / 154 / 45 |
| Event FIFO overflow cycles (`>8` `MARK`s) | 12 |
| `HALT(keep_oe=0)` / `HALT(keep_oe=1)` | 43 / 64 |
| Illegal opcode (`0xF` + reserved `0xE`) `ENG_FAULT` + Hi-Z (`uio_oe=0`) | 12 (reserved `0xE`: 4) |
| `cmd_halt` mid-run abort | 1 |
