# v0.1 UART-to-SPI RTL simulation record

**Date:** 2026-11-15  
**Claim class:** RTL simulation result, not synthesis, gate-level, board, or silicon evidence.

## Scope

`src/project.v` implements the selected v0.1 bring-up frame:

- UART 8N1 request: `A5 opcode id low_lo low_hi mosi crc8`.
- One mode-0, MSB-first SPI transaction for opcode `01` with a nonzero low phase.
- UART response: `5A id status low_lo low_hi miso crc8`.
- CRC-8 polynomial `0x07`, initial value `0x00`, non-reflected.

The design keeps `uio_oe` low and reads SPI MISO from `uio_in[0]`. Its UART divider is the simulation/integration parameter `CLKS_PER_BIT=8`; it is not a selected board baud rate.

## Reproducible command

From the repository root, with Icarus Verilog 13.0 available:

```powershell
iverilog -g2012 -Wall -s tb_v01 -o build_v01.out src/project.v test/tb_v01.v
vvp build_v01.out
```

Observed result:

```text
PASS: v0.1 UART/SPI contract checks passed
```

`test/tb_v01.v` checked one valid command against a behavioural `0xAA` SPI target, including response CRC, eight rising/falling SPI edges, a two-core-clock high phase, and the requested three-core-clock low phase. It also checked BAD_CRC, BAD_OPCODE, and BAD_ARGUMENT responses and asserted that each rejected request did not assert SPI chip-select.

The repository Cocotb suite in `test/test.py` also passed 2/2 on Icarus 12.0 with Cocotb 2.0.1 in the Tiny Tapeout development container. It checks the valid response and CRC plus the three rejected-command statuses. The template Makefile expands an unquoted `$(PWD)`, so this workspace's space-containing path could not be run directly; the same `src/` and `test/` files were copied to `/tmp/asic-v01-run` inside the container for that invocation. This is an infrastructure workaround, not a source change.

## Limits

This does not establish area, timing closure, pad behavior, physical SPI compatibility, reset/reload on the demo board, gate-level behavior, or the programmable architecture required for final submission.
