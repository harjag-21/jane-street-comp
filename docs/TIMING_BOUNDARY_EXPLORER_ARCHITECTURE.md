# Timing Boundary Explorer — Architecture Draft

**Status:** v0.1 single-SPI bring-up contract selected; the final reprogrammable protocol engine and ISA remain to be designed.
**Purpose:** keep the experiment, ASIC, host software, and evidence plan aligned before RTL work.

## Research question (hypothesis)

For a digital target with a repeatable timing acceptance threshold, can a small programmable ASIC and host tool estimate the threshold with fewer trials and more reproducible evidence than a dense fixed sweep, while reporting clock-quantization and trial uncertainty honestly?

This is a research question, not a novelty claim. The contribution would need to be shown by a measured comparison against a fixed-sweep baseline and by independent reruns. The competition ledger tracks prior art and source-backed claims.

## Proposed long-term system split

The components below describe the broader hypothesis. The immediate v0.1 implementation is the smaller command-driven SPI path specified later in this document.

### ASIC

1. **Host/configuration receiver.** A small framed command interface loads a program and run parameters. UART is the current candidate because it is easy to drive from a host script; pin allocation and boot protocol remain open.
2. **Programmable protocol sequencer.** A compact instruction memory and interpreter could drive selected pins, wait for a programmable number of clock cycles, sample inputs, and branch or repeat. Loading UART, SPI, and I2C example programs is a later flexibility objective; it is not a v0.1 acceptance criterion.
3. **Timing control.** A run parameter controls the interval under study in integer clock cycles. The ASIC emits explicit start/end markers so each trial can be correlated with captured events.
4. **Response capture.** A cycle counter timestamps selected input changes or samples them at defined protocol points. A bounded FIFO returns compact event records to the host. Counter width, FIFO depth, and overflow behavior need resource estimates before they are fixed.
5. **Pin control.** Protocol pins use the available digital input, output, and bidirectional pads. The host-control pins, bidirectional output enables, reset sequence, and I2C open-drain behavior require a documented pin budget and electrical review.

### Host explorer

1. Compile a human-readable protocol/test description to the ASIC's loaded instruction format.
2. Configure a trial range and repeat count. Begin with a deterministic coarse sweep, then refine near the observed transition; keep adaptive selection optional until it beats the fixed baseline on the same target.
3. Collect and decode event records, associate each trial with its timing value, and classify the target response using an explicit rule (for example, expected reply plus frame check).
4. Report the tested range, clock period, cycle granularity, repeats, success counts, confidence interval, missing/invalid trials, and raw data needed to replay the result.

The initial timing unit is **clock cycles**. Converting it to nanoseconds is valid only when the actual clock frequency and clock source are known and recorded. A cycle counter does not establish sub-cycle timing resolution.

### Reference target and oracle

Start with a simulation target whose threshold is a known parameter. Sweep that target to verify stimulus timing, capture alignment, classification, and boundary error. A later board demonstration should use a separate controllable target (for example, an FPGA or microcontroller) with its own declared threshold behavior. The target implementation must not share hidden internal state with the host classifier.

Simulation establishes behavior only for the modeled target. It does not establish real pad timing or fabricated-silicon performance.

## Minimum experiment

**Selected v0.1 experiment:** a single SPI mode-0 master transaction to a simulation target that returns a known byte and changes MISO after a declared clock-to-output delay from the falling SCLK edge. Keep the high phase and all other settings fixed; sweep only the low-phase duration in integer cycles before the next rising edge samples MISO. The expected boundary follows from the target delay plus the model's explicit setup rule. Later experiments can sweep the high phase or a sample offset as separate axes.

1. Use a fixed pattern with enough transitions to expose bit/edge alignment errors and one adjustable low-phase duration.
2. Define success and failure before collecting results; include malformed, timeout, and ambiguous responses as separate outcomes.
3. Compare a fixed low-phase sweep against binary search with repeats on identical target delays and trial budgets.
4. Measure boundary error in clock cycles, trial count, repeat-to-repeat spread, false classifications, and report reproducibility from saved raw traces.
5. Repeat with target delays near clock-quantization points and with injected response jitter in simulation.

The minimum experiment should answer whether the explorer adds measurable value before adding adaptive search, multiple simultaneous buses, large trace buffers, or multiple protocol families in one run.

### Executable timing-oracle draft

This is a specification proposal for the first simulation experiment, not a claim about physical SPI devices:

- Let `T` be the recorded core-clock period, `L` the programmable SCLK-low duration in whole core cycles, `H` the fixed high duration, `d` the target's declared MISO clock-to-output delay after a falling SCLK edge, and `s` the declared setup requirement at the next rising sample.
- Preload the first response bit before the first rising edge. After each falling edge, schedule the next MISO bit after `d`; sample it at the following rising edge. Use a byte pattern with frequent bit changes so stale-bit and edge-alignment errors are visible.
- In the deterministic model, a transition is accepted exactly when its age at the sample is at least `s`. Thus the predicted first passing low duration is `L_min = ceil((d + s) / T)` cycles, with equality passing by definition. The testbench computes this from the model parameters before running the explorer. The model must explicitly handle the first bit, which has no preceding falling edge in the transaction.
- Define one trial as one complete SPI transaction. In the simulation model, it passes only if the received byte matches and every intended transition meets the explicit setup rule. In hardware, record the received byte and transport/protocol status; the known-target host classifier compares the byte to its expected response. Never fold a missing response or invalid transport record into the target's timing-failure count.
- For jitter runs, add a seeded, recorded delay perturbation to each target transition. Report the tested perturbation rule and per-setting pass counts. Do not report a single hard boundary when repeated outcomes disagree; report the transition region and uncertainty instead.

Before coding, review the SPI edge ordering and equality convention against the selected simulator's event semantics. The analytical boundary is an oracle for this deliberately simplified target model; it is not a prediction of a real target's setup/hold behavior or of the ASIC pad timing.

## Competition compliance gate

The [organizer's brief and FAQ](https://blog.janestreet.com/protocol-emulator-asic-competition/#faq) require a chip that can be reprogrammed after fabrication to support protocols beyond fixed-function blocks, plus a software toolchain and example programs. UART, SPI, and I2C are the starting protocol set, and flexibility is the main judging criterion. A CPU is not required; a small sequencer, interpreter, or another reprogrammable architecture may qualify. This is a final-submission requirement, not an optional future enhancement. The v0.1 contract below is only a risk-reduction milestone and must grow into this required architecture before feature freeze.

The final architecture gate is passed only when a loaded program can configure pin direction/value, cycle waits, and sampling (or equivalent primitives), and documented programs demonstrate UART, SPI, and I2C. The repository must include the loader/toolchain and examples. The timing-boundary host tool will compile/use those programs to run controlled sweeps. Reset and reload after board deselection must be verified because the organizer's FAQ notes the design loses state when deselected. Keep fixed-function helper blocks only where they do not prevent post-fabrication reprogramming.

## v0.1 minimum bring-up contract

This decision narrows the first hardware milestone to one complete host-to-target-to-host path. It is not, by itself, a compliant final competition design and does not freeze the later programmable multi-protocol architecture. Treat the byte framing as a bring-up interface; the final loader may version or replace it after the ISA/program model is designed.

### Transaction behavior

- The ASIC performs one SPI mode-0, MSB-first, 8-bit transaction per accepted host command. `CS_N` is asserted for the transaction; SCLK idles low. The low phase is a command parameter in core-clock cycles; the high phase is fixed at two core-clock cycles for this first implementation.
- The host controls the sweep and repeat count by sending one command per trial. The ASIC does not store a sweep program or choose the next timing value.
- The target's first MISO bit must be valid before the first rising sample. Each later MISO bit is scheduled after the preceding falling edge. Model success requires the expected received byte and all declared setup checks to pass. Hardware capture reports the received byte; the host classifies it against the expected result and keeps transport/protocol failures separate from timing failures.
- The initial expected target response is `0xAA`, selected because its adjacent bits toggle and expose bit/edge alignment errors. This is a test pattern, not a claimed device requirement.

### Host command and result records

UART is selected for the first host interface, with 8 data bits, no parity, one stop bit, and idle high (8N1). The baud rate is deferred until the core clock and board interface are verified. The initial fixed-length request is seven bytes:

| Byte(s) | Meaning |
|---|---|
| 0 | Sync `0xA5` |
| 1 | Opcode `0x01` (one SPI transaction) |
| 2 | Request ID |
| 3–4 | `low_cycles`, unsigned 16-bit little-endian; zero is invalid |
| 5 | MOSI byte |
| 6 | CRC-8, polynomial `0x07`, initial value `0x00`, xorout `0x00`, non-reflected; over bytes 0–5 |

The fixed-length response is also seven bytes: sync `0x5A`, echoed request ID, status, echoed `low_cycles` (16-bit little-endian), received MISO byte, and CRC-8 with the same parameters over preceding bytes. Status values are `0=OK`, `1=BAD_CRC`, `2=BAD_OPCODE`, and `3=BAD_ARGUMENT`. For rejected commands, the received-byte field is zero and no SPI transaction is run. The host uses stop-and-wait: it does not send another request until it receives the matching response or records a host-side timeout. A timeout, UART framing failure, or missing response is recorded as missing/invalid transport data, never as a target timing failure.

The host retains a JSONL record per attempted trial, including the exact request and response bytes, request ID, timing parameter, transmit byte, received byte, status, expected byte, and outcome classification. The analysis input also records the clock period if known, target model/version, sweep settings, seed, and software revision. The host owns all sweeps, retries, boundary estimation, and confidence calculations.

### Initial logical pin allocation

| Tiny Tapeout signal | v0.1 function |
|---|---|
| `ui_in[0]` | UART host-to-ASIC RX |
| `uo_out[0]` | UART ASIC-to-host TX |
| `uo_out[1]` | SPI SCLK |
| `uo_out[2]` | SPI CS_N |
| `uo_out[3]` | SPI MOSI |
| `uio_in[0]` | SPI MISO input (`uio_oe[0]=0`) |
| `uo_out[4]` | `TRIAL_ACTIVE` marker for logic-analyzer correlation |
| All other user pins | Reserved in v0.1 |

This is the project pin plan, not a verified board/electrical assignment. The UART baud rate and `info.yaml` clock frequency remain unset until the actual clock source, divider accuracy, and board-level pin/electrical constraints are checked. Report timing in cycles until that clock is established. The RTL and `info.yaml` pin labels remain unchanged until the implementation uses this mapping.

### Explicitly deferred

Instruction memory or another post-fabrication program mechanism, the final loader/assembler/toolchain, configurable pin selection, and UART/SPI/I2C example programs are deferred only until the v0.1 transaction path passes simulation; they are mandatory before final submission. Evaluate the smallest program engine and synthesize it early. Programmable high phase, multiple simultaneous buses, on-chip sweeps, a trace FIFO, adaptive search in the ASIC, and a physical external target remain optional unless measurements justify them.

## Search-limit behavior

Search results distinguish a measured interior transition from a limit hit:

- `interior_boundary`: a failing point and a passing point were observed; the first passing candidate is reported and `bracketed=true`.
- `lower_endpoint_pass`: the lowest tested candidate already passes. Report that candidate as the first passing value in the tested set, but set `bracketed=false`; the experiment cannot establish behavior below the search range.
- `upper_endpoint_fail`: the highest tested candidate fails. Report `boundary=null` and `bracketed=false`; do not relabel the maximum tested value as the boundary.

For noisy classification these statuses use the predeclared empirical success threshold. A no-pass result is an out-of-range/censored observation, not an estimate. Fixed sweep and binary search must report the same status vocabulary.

## Higher-risk research option

After the fixed sweep and binary-search baselines work, test a sequential estimator that models response probability versus timing, chooses the next probe near the uncertain boundary, and stops when a predeclared confidence interval reaches a chosen width. The probability threshold (such as 99% successful trials) must be justified for the use case, not presented as universal. Detect and report violations of the assumed monotonic response curve instead of forcing a boundary estimate.

This could make the write-up more research-driven if it measurably reduces trials at the same error and false-classification limits under jitter and drift. Adaptive interface calibration, bit-error-rate testing, and timing-margin searches are established neighboring fields; novelty must be checked against them before presenting this as a research contribution.

## Decisions intentionally deferred

- Final board wiring, clock source/frequency, UART baud, and electrical pin validation.
- Instruction width, opcode set, program depth, and branch semantics.
- Event-trigger policy, timestamp width, FIFO depth, and use of SRAM macros.
- Target device and external pull-up/level requirements.
- Sweep/refinement algorithm beyond a deterministic baseline.
- Whether any adaptive algorithm is important enough to be a paper contribution.

## Decision gates

1. **Interface gate:** demonstrate the fixed UART command/response round-trip and raw-trial log in simulation.
2. **Measurement gate:** recover the known simulation threshold within a declared cycle error and classify invalid cases correctly.
3. **Value gate:** show that refinement reduces trials at a fixed boundary-error target without increasing false classifications.
4. **Hardware gate:** synthesize and route the smallest working engine before growing program or trace memory.
5. **Claim gate:** only describe novelty after a refreshed literature review and report evidence at its actual level (simulation, gate-level, routed, or measured silicon).

Until these gates pass, all architecture benefits above remain design hypotheses.
