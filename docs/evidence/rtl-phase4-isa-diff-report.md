# Phase 4 Randomized ISA Differential Testing Report

Compared `src/protocol_sequencer.v` cycle-by-cycle against independent Python ISA reference model `SequencerRefModel` across **120** reproducible programs (**240,862** simulated clock cycles).

- **Cycle & FIFO Mismatches:** **`0`** (`0` tolerated)
- **Opcodes Executed (`16/16`):** `0x0, 0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8, 0x9, 0xA, 0xB, 0xC, 0xD, 0xE, 0xF`

| Boundary / Branch Feature | Observed Count |
|---|---|
| `JZ` taken / not taken | 104 / 168 |
| `LOOP` taken / fallthrough (`cnt=1` / `cnt=0`) | 58 / 62 / 1 |
| `SAMPLE` shift / `no_shift=1` | 177 / 182 |
| `OUT_SAMPLE` shift / `no_shift=1` | 195 / 160 |
| `MARK` immediate / `MARK_SAMPLE` | 186 / 174 |
| `WAIT(0)` / `WAIT(4095)` | 63 / 1 |
| `WAIT_PARAM(0)` / saturated `0xFFF` | 19 / 30 |
| `WAIT_EDGE` immediate hit / delayed hit / timeout | 191 / 171 / 49 |
| Event FIFO overflow cycles (`>8` `MARK`s) | 11 |
| `HALT(keep_oe=0)` / `HALT(keep_oe=1)` | 57 / 50 |
| Illegal opcode (`0xE`/`0xF`) `ENG_FAULT` + Hi-Z (`uio_oe=0`) | 12 |
| `cmd_halt` mid-run abort | 1 |
