# Programmable Protocol Engine — v1 Architecture Proposal

**Status:** design proposal; no RTL, area result, or protocol program has been implemented from this document.

## Purpose

The fixed UART-to-SPI path in `src/project.v` establishes an integration baseline. The final competition design must be reprogrammable after fabrication, so this document defines the smallest useful program model that can replace that fixed transaction path.

The intended differentiator remains the Timing Boundary Explorer: the host loads a protocol program, varies a declared timing parameter, and records results. The engine must also be useful without that host experiment.

## Chosen first architecture

Use one deterministic, single-issue microsequencer with a small instruction store. It owns eight bidirectional protocol pins and executes one instruction per core cycle except for `WAIT` and host-access operations. It has no instruction cache, pipeline, CPU privilege model, concurrent engines, or autonomous search logic in v1.

| Block | v1 choice | Reason |
|---|---|---|
| Program store | 64 words × 16 bits, loaded over UART | Supports complete first examples while limiting flop-based storage risk before SRAM integration is justified. |
| Protocol pins | `uio[7:0]`; separate output-value and output-enable registers | Supports push-pull and open-drain patterns with the same program model. |
| Loader transport | UART RX on `ui_in[0]`, UART TX on `uo_out[0]` | Reuses the v0.1 host path; a board clock/baud choice remains open. |
| Engine control | Reset, load, start, halt, status/readback commands | Supports the required reload after board deselection and enables reproducible host runs. |
| Data state | `last_sample` bit, eight-bit sample register, two loop counters | Enough for sampled protocol response, polling, and bounded loops without a general CPU register file. |
| Timing unit | Whole core cycles only | Makes timing explicit and avoids a false claim of sub-cycle resolution. |

The final pin allocation remains open until the board wiring and electrical review. This table reserves the `uio` bank for protocol use; it is a resource plan, not an `info.yaml` declaration.

## 16-bit instruction format

Each word is `opcode[15:12] | operand[11:0]`. All instructions are deterministic at the core-clock edge. `WAIT n` takes `n + 1` core cycles after its decode cycle, so zero is a valid one-cycle delay.

| Opcode | Operand | Effect |
|---|---|---|
| `0x0 NOP` | ignored | Advance PC. |
| `0x1 OUT` | `value[7:0]` | Replace the protocol output-value register. |
| `0x2 OE` | `enable[7:0]` | Replace the protocol output-enable register; zero means input/high-Z. |
| `0x3 WAIT` | `cycles_minus_1[7:0]` | Hold outputs and PC for the requested cycle count. |
| `0x4 SAMPLE` | `pin[2:0]`, `shift[2:0]` | Read one protocol pin into `last_sample`; shift it into the sample register. |
| `0x5 JZ` | `address[5:0]` | Jump if `last_sample` is zero. |
| `0x6 JMP` | `address[5:0]` | Unconditional jump. |
| `0x7 LOOP` | `counter[0]`, `address[5:0]` | Decrement a preloaded counter and jump while nonzero. |
| `0x8 SETCOUNT` | `counter[0]`, `count[7:0]` | Set a loop counter. |
| `0x9 MARK` | `tag[7:0]` | Emit a compact host-visible event record. |
| `0xA HALT` | ignored | Stop execution; preserve program and sample state for readback. |
| `0xB–0xF` | reserved | Decode to a safe halt with an illegal-instruction status. |

`SAMPLE` shifts the prior sample register left and writes the sampled pin at bit zero. SPI programs therefore sample MSB-first with eight `SAMPLE` instructions; the host receives a defined bit order. The assembler will provide named pins and macros so programs do not expose raw bit masks everywhere.

## Loading and observability contract

The UART control plane will have a versioned loader protocol rather than reuse the v0.1 fixed-SPI command as a permanent ABI:

1. Reset places the engine in `HALTED`, clears pin output enables, and invalidates the loaded-program flag.
2. `BEGIN_LOAD(length, checksum)` clears the program-valid flag.
3. `WRITE_WORD(address, word)` loads each word within the 64-word limit.
4. `COMMIT_LOAD` verifies the declared length/checksum and marks the program valid.
5. `START(run_parameters)` starts at PC zero only when program valid.
6. `READ_STATUS` returns halted/running/fault state, PC, last sample byte, and bounded event data.

The host must reload after a selection/power cycle. Program loading is part of the ASIC interface and will be tested in RTL; it is not an external software convenience.

## Example-program acceptance criteria

The v1 architecture is acceptable only if the assembler can emit and RTL simulation can execute all three examples below:

| Example | Required evidence |
|---|---|
| UART transmitter | A configured output pin emits an 8N1 byte at a program-declared whole-cycle bit period. |
| SPI mode-0 controller | Program drives CS/SCLK/MOSI, samples eight MISO bits, and returns the received byte. Timing-boundary sweeps vary a declared `WAIT` value. |
| I2C controller | Program produces start/stop and open-drain SCL/SDA behavior, releases pins through `OE`, and samples SDA for ACK. |

The first timing explorer demonstration will use the SPI example. UART and I2C examples demonstrate that the mechanism is programmable rather than a renamed fixed SPI controller.

## Risks and decision gates

1. Synthesize the engine with a 64 × 16 flop store before implementing the full loader. If mapped area or routing margin is poor, compare an IHP SRAM macro and a 32-word program limit with equal example coverage.
2. Simulate reset, partial/invalid load, checksum rejection, reload, illegal instruction, and `HALT` with all protocol outputs disabled where safe.
3. Do not claim a protocol speed until post-route timing and the selected board clock are recorded.
4. Only add a second engine, trace FIFO, adaptive search hardware, larger memory, or SRAM after the one-engine programs and physical baseline fit.
