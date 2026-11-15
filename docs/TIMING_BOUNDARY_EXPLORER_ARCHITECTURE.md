# Timing Boundary Explorer — Architecture Draft

**Status:** proposal only; no architecture or interface is frozen.  
**Purpose:** keep the experiment, ASIC, host software, and evidence plan aligned before RTL work.

## Research question (hypothesis)

For a digital target with a repeatable timing acceptance threshold, can a small programmable ASIC and host tool estimate the threshold with fewer trials and more reproducible evidence than a dense fixed sweep, while reporting clock-quantization and trial uncertainty honestly?

This is a research question, not a novelty claim. The contribution would need to be shown by a measured comparison against a fixed-sweep baseline and by independent reruns. The competition ledger tracks prior art and source-backed claims.

## Proposed system split

### ASIC

1. **Host/configuration receiver.** A small framed command interface loads a program and run parameters. UART is the current candidate because it is easy to drive from a host script; pin allocation and boot protocol remain open.
2. **Programmable protocol sequencer.** A compact instruction memory and interpreter drive selected pins, wait for a programmable number of clock cycles, sample inputs, and branch or repeat. UART, SPI, and I2C example programs must be loadable; a set of fixed, non-programmable protocol blocks alone would not meet the flexibility objective.
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

**First candidate, not yet frozen:** a single SPI mode-0 master transaction to a simulation target that returns a known byte after a declared MISO clock-to-output delay. Sweep only the master's MISO sample offset in integer cycles; keep SCLK rate and all other settings fixed. This gives a clear timing boundary without initially mixing sample-phase and bus-frequency effects. Later experiments can sweep SCLK rate as a separate axis.

1. Use a fixed pattern with enough transitions to expose bit/edge alignment errors and one adjustable sample offset.
2. Define success and failure before collecting results; include malformed, timeout, and ambiguous responses as separate outcomes.
3. Compare a fixed sample-offset sweep against coarse-to-fine refinement on identical target delays and repeat counts.
4. Measure boundary error in clock cycles, trial count, repeat-to-repeat spread, false classifications, and report reproducibility from saved raw traces.
5. Repeat with target delays near clock-quantization points and with injected response jitter in simulation.

The minimum experiment should answer whether the explorer adds measurable value before adding adaptive search, multiple simultaneous buses, large trace buffers, or multiple protocol families in one run.

## Decisions intentionally deferred

- Host boot/loading protocol and exact pin map.
- Instruction width, opcode set, program depth, and branch semantics.
- Event-trigger policy, timestamp width, FIFO depth, and use of SRAM macros.
- Target device and external pull-up/level requirements.
- Sweep/refinement algorithm beyond a deterministic baseline.
- Whether any adaptive algorithm is important enough to be a paper contribution.

## Decision gates

1. **Interface gate:** demonstrate a host-loaded program and trace round-trip in simulation.
2. **Measurement gate:** recover the known simulation threshold within a declared cycle error and classify invalid cases correctly.
3. **Value gate:** show that refinement reduces trials at a fixed boundary-error target without increasing false classifications.
4. **Hardware gate:** synthesize and route the smallest working engine before growing program or trace memory.
5. **Claim gate:** only describe novelty after a refreshed literature review and report evidence at its actual level (simulation, gate-level, routed, or measured silicon).

Until these gates pass, all architecture benefits above remain design hypotheses.
