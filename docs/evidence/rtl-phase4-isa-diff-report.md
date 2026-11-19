# Phase 4 Randomized ISA Differential Testing Report

Compared `src/protocol_sequencer.v` cycle-by-cycle against independent Python ISA reference model `SequencerRefModel` across **120** reproducible programs (**218,477** simulated clock cycles).

- **Cycle & FIFO Mismatches:** **`0`** (`0` tolerated)
- **Opcodes Executed (`16/16`):** `0x0, 0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8, 0x9, 0xA, 0xB, 0xC, 0xD, 0xE, 0xF`

| Boundary / Branch Feature | Observed Count |
|---|---|
| `JZ` taken / not taken | 99 / 146 |
| `LOOP` taken / fallthrough (`cnt=1` / `cnt=0`) | 58 / 62 / 1 |
| `SAMPLE` shift / `no_shift=1` | 159 / 173 |
| `OUT_SAMPLE` shift / `no_shift=1` | 173 / 165 |
| `MARK` immediate / `MARK_SAMPLE` | 187 / 164 |
| `LD_TX` / `LD_TX_OD` (MSB / LSB / open-drain) | 74 / 96 / 86 |
| `SHIFT_TX` setup / shift-only / shift+sample / open-drain | 90 / 18 / 67 / 42 |
| `WAIT(0)` / `WAIT(4095)` | 68 / 1 |
| `WAIT_PARAM(0)` / saturated `0xFFF` | 20 / 22 |
| `WAIT_EDGE` immediate hit / delayed hit / timeout | 171 / 147 / 60 |
| Event FIFO overflow cycles (`>8` `MARK`s) | 19 |
| `HALT(keep_oe=0)` / `HALT(keep_oe=1)` | 49 / 58 |
| Illegal opcode (`0xF` + reserved `0xE`) `ENG_FAULT` + Hi-Z (`uio_oe=0`) | 12 (reserved `0xE`: 7) |
| `cmd_halt` mid-run abort | 1 |
