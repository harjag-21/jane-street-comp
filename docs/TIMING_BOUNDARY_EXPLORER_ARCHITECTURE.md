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

**First candidate, not yet frozen:** a single SPI mode-0 master transaction to a simulation target that returns a known byte and changes MISO after a declared clock-to-output delay from the falling SCLK edge. Keep the high phase and all other settings fixed; sweep only the low-phase duration in integer cycles before the next rising edge samples MISO. The expected boundary follows from the target delay plus the model's explicit setup rule. Later experiments can sweep the high phase or a sample offset as separate axes.

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
- Define one trial as one complete, bounded SPI transaction. A trial passes only if the captured response exactly matches the expected byte before timeout; record timeout and protocol/model errors separately so the search algorithm cannot quietly treat them as ordinary timing failures.
- For jitter runs, add a seeded, recorded delay perturbation to each target transition. Report the tested perturbation rule and per-setting pass counts. Do not report a single hard boundary when repeated outcomes disagree; report the transition region and uncertainty instead.

Before coding, review the SPI edge ordering and equality convention against the selected simulator's event semantics. The analytical boundary is an oracle for this deliberately simplified target model; it is not a prediction of a real target's setup/hold behavior or of the ASIC pad timing.

## Higher-risk research option

After the fixed sweep and binary-search baselines work, test a sequential estimator that models response probability versus timing, chooses the next probe near the uncertain boundary, and stops when a predeclared confidence interval reaches a chosen width. The probability threshold (such as 99% successful trials) must be justified for the use case, not presented as universal. Detect and report violations of the assumed monotonic response curve instead of forcing a boundary estimate.

This could make the write-up more research-driven if it measurably reduces trials at the same error and false-classification limits under jitter and drift. Adaptive interface calibration, bit-error-rate testing, and timing-margin searches are established neighboring fields; novelty must be checked against them before presenting this as a research contribution.

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
