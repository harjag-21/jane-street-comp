# Timing Boundary Explorer — Project Status Report

**Report date:** 2026-11-16  
**Repository:** `Jane Street ASIC`  
**Purpose:** provide one evidence-aware snapshot of completed work, current state, unresolved risks, and the ordered work plan.

## Executive summary

The project has a tested software timing model and a first UART-controlled, single-transaction SPI RTL bring-up slice. Python reference-model tests and statistical-study audits have passed. The bring-up RTL passed both a standalone Icarus testbench and two Cocotb tests in the Tiny Tapeout development container.

The physical baseline has **not** been run successfully on this RTL. No synthesis/area/timing report, final GDS, passing precheck, gate-level result, FPGA result, or silicon result exists for the current project RTL. The intended final architecture is a post-fabrication programmable protocol engine, but that engine, its loader, toolchain, and UART/SPI/I2C example programs are still proposals or future work.

The immediate priority is to get an authentic Tiny Tapeout hardening run on the current RTL and use its results to guide architecture sizing. The next major design task is a minimal programmable engine; the 64-word instruction store proposal must be tested against area before it is treated as a fixed choice.

## 1. Completed work

### 1.1 Competition and project planning

- Reviewed the organizer's competition brief and FAQ. The current plan records post-fabrication reprogrammability, a software toolchain and example programs, UART/SPI/I2C as the starting set, and flexibility as the main judging criterion. Source: [official competition brief and FAQ](https://blog.janestreet.com/protocol-emulator-asic-competition/#faq), checked 2026-11-15.
- Selected the Timing Boundary Explorer as the project's demonstration identity, with a programmable protocol engine as the required foundation.
- Defined a v0.1 bring-up interface: UART 8N1 framed requests and responses, one SPI mode-0 MSB-first transaction per valid command, host-controlled timing sweeps, and raw per-trial logging.
- Explicitly marked v0.1 as a bring-up milestone rather than a final competition-compliant design.
- Drafted a possible minimal 16-bit instruction set, 64-word program memory, UART loader, and protocol-program acceptance criteria. These are **design proposals**, not implementation results.

### 1.2 Timing model and research evidence

- Implemented a deterministic SPI timing model and oracle in `experiments/spi_timing_model.py`.
- Added tests for edge ordering, delayed transitions, setup checks, seeded jitter, search endpoints, and study-budget constraints. The latest recorded software run passed **15/15** tests.
- Independently replayed the saved reference, paired, and equal-budget studies with exact JSON equality.
- Independently audited six equal-budget regimes and 6,000 unique seed runs.
- The equal-budget comparison used 800 trials per method per seed across 1,000 seeds. Fixed sweep recovered the study boundary in 769/1,000 runs; binary search did so in 789/1,000. The paired interval included zero, so the evidence does not show a resolved accuracy advantage.
- In the six-regime study, the noisy near-threshold setting gave 789/1,000 fixed-sweep recoveries and 803/1,000 binary-search recoveries; the paired interval again included zero. This is an observation for further study, not proof of a method advantage.
- Added explicit censored/out-of-range search outcomes so a configured endpoint is not misreported as a measured boundary.

These are simulation/model results. They do not establish real target behavior, ASIC pad timing, or fabricated-silicon performance.

### 1.3 RTL v0.1 bring-up and verification

Current implementation: `src/project.v`.

- UART RX/TX, command validation, CRC-8/07, one SPI transfer, and seven-byte result framing are implemented for the v0.1 interface.
- The standalone Icarus testbench passed. It checks one valid `0xAA` target transfer, response CRC, SPI edge count, the declared two-core-clock high phase and three-core-clock low phase, and rejection of bad CRC, bad opcode, and zero low-phase commands without starting SPI.
- The repository Cocotb suite passed **2/2** in the Tiny Tapeout development container with Icarus 12.0 and Cocotb 2.0.1. The container run used a no-space temporary copy because the template Makefile mishandles this workspace path containing spaces.
- Evidence and reproduction commands are recorded in `docs/evidence/rtl-v01-simulation-2026-11-15.md`.

Limits: this checks RTL simulation with a behavioral SPI target. It does not establish synthesis, area, routed timing, pad behavior, board wiring, reset/reload behavior on the demo board, gate-level behavior, or silicon behavior.

### 1.4 Repository and tool setup

- The workspace is a local Git repository with no configured remote; recent work is committed locally and has not been published.
- Tiny Tapeout's CMOS5L scaffold, local support-tools checkout, 8x4 metadata, and an IHP-SG13 PDK install were prepared earlier. Those setup checks do not imply hardening or precheck success.
- The running development container has the needed venv (`/ttsetup/venv`) with Cocotb 2.0.1 and Tiny Tapeout support tools. A system-Python invocation initially appeared to lack them; using the venv resolved that environment mismatch.

## 2. Current repository state

Recent relevant commits:

| Commit | Contents |
|---|---|
| `a90d74a` | v0.1 UART/SPI RTL and standalone testbench |
| `3f53a4c` | Minimum programmable-engine architecture proposal |
| `0bd0dfb` | Cocotb compatibility fix, passing run record, and evidence update |
| `34a0054` | Competition compliance gate clarification |

The most recent status check showed no uncommitted changes. The working tree should be checked again before starting the next implementation branch or flow run.

Primary project documents:

- `docs/COMPETITION_PLAN_AND_EVIDENCE.md` — evidence ledger, decisions, setup notes, and next actions.
- `docs/TIMING_BOUNDARY_EXPLORER_ARCHITECTURE.md` — research hypothesis, v0.1 interface, search semantics, and gates.
- `docs/PROGRAMMABLE_ENGINE_ARCHITECTURE.md` — proposed programmable engine, ISA, loader, and example-program requirements.
- `docs/evidence/rtl-v01-simulation-2026-11-15.md` — current RTL simulation evidence and limits.

## 3. Physical-flow status and the latest hardening attempt

No hardening result exists for current RTL. An attempted run from a temporary no-space copy did not reach synthesis:

1. The first temporary copy placed `project.v` at the project root; the tool expected `src/project.v`.
2. After correcting the source layout, the Tiny Tapeout tool rejected the temporary directory because it was not a Git repository. The tool's hardening path reads repository/remote metadata.

These were setup failures, not RTL or PDK failures. The next attempt should use a genuine Git checkout containing the exact current committed source and the necessary repository metadata. Keep the run directory and full logs, and state the source commit and PDK version in the evidence record. Do not invent or publish a remote simply to satisfy the tooling.

## 4. Work remaining — ordered plan

### Phase A — establish reproducible physical baseline

1. Confirm `git status`, source commit, branch, remotes, active container, tool versions, and PDK version.
2. Prepare a real no-space checkout/worktree of the current committed design, retaining authentic Git metadata. Avoid hand-copying source into a non-repository for the official tool.
3. Run the Tiny Tapeout CMOS5L flow. Capture full logs and the final flow status.
4. Record synthesis area, sequential/combinational cell counts, timing constraints, critical path, routing congestion, and any warnings. If the flow stops, record the last successful stage and exact reason.
5. Preserve the distinction between a synthesis report, a complete hardening result, final GDS, and passing precheck.

**Exit condition:** reproducible area/timing data for the current v0.1 RTL, or a clearly diagnosed tool/setup blocker.

### Phase B — deepen v0.1 verification and resolve interface details

1. Expand UART tests for malformed stop bits, consecutive frames, and parser recovery.
2. Expand SPI checks for MOSI bit sequence, all sample edges, final falling edge/CS timing, minimum supported low phase, and large low-phase values.
3. Exercise reset during request reception, SPI, and response transmission; verify subsequent recovery.
4. Test a valid request after every rejected request and verify stop-and-wait behavior.
5. Choose the actual demo-board clock and UART baud only after checking the board/template wiring and timing constraints. Keep the current divider as a test parameter until that is known.
6. Resolve the UART/SPI pin assignment against the Tiny Tapeout signal mapping and board electrical requirements before changing `info.yaml` labels.

**Exit condition:** tests cover interface success, rejection, recovery, reset, and timing edge cases, and the board-level assumptions are documented.

### Phase C — validate programmable-engine feasibility

1. Use the v0.1 physical baseline to estimate available tile margin.
2. Implement only the single-issue sequencer and instruction store from `docs/PROGRAMMABLE_ENGINE_ARCHITECTURE.md` first.
3. Compare 64×16 and 32×16 flop-based stores; consider an IHP SRAM macro only with a complete mapped and physical comparison.
4. Revisit the instruction set where the physical or software results show a mismatch. Keep instruction semantics and assembler encoding in one checked specification.

**Exit condition:** a justified program depth/store choice and a sequencer that fits a measured physical budget with margin.

### Phase D — loader, toolchain, and protocol programs

1. Implement reset-safe UART load/start/status commands, program length/address checking, and integrity checking.
2. Implement an assembler and documented host protocol for loading and reading results.
3. Test partial/corrupt program loads, invalid addresses, illegal opcodes, halt/fault behavior, reset, and reload after deselection/power cycle.
4. Write and simulate UART transmitter, SPI mode-0 controller, and I2C open-drain/ACK example programs.
5. Connect the timing-boundary host sweep to a program parameter (for example, the SPI `WAIT` duration), and preserve raw request/response/trial records.

**Exit condition:** all three protocols execute as loaded programs; the chip can accept new programs after fabrication and reload them after losing state.

### Phase E — research and final submission evidence

1. Run the host timing explorer against the program-driven SPI example, first on the known simulation target and then on a separate controllable FPGA/microcontroller target if available.
2. Predeclare baseline methods, total trial budgets, success/failure rules, noise/drift conditions, and uncertainty reporting. Compare methods under identical profiles or controlled target conditions.
3. Keep adaptive probing as an optional research experiment until it demonstrates a repeatable benefit at a fixed trial budget and error target.
4. Run full regression, gate-level simulation, final Tiny Tapeout hardening, and the official precheck on the final reprogrammable design.
5. Prepare the architecture/ISA, toolchain instructions, protocol examples, verification write-up, and a concise demo. Record all evidence against the final source revision.

**Exit condition:** final design passes the required flow/precheck and submission materials substantiate each claimed feature at the correct evidence level.

## 5. Key risks and controls

| Risk | Current status | Control / next action |
|---|---|---|
| Bring-up-only fixed SPI does not meet final flexibility requirement | Known | Treat programmable engine/toolchain/examples as mandatory, not stretch goals. |
| Program memory or loader may exceed area/routing budget | Unknown | Run baseline first, then synthesize minimal sequencer/store before full feature work. |
| Board clock and UART baud are not selected | Open | Verify template/demo-board clock and quantify baud error before making speed claims. |
| Hardening invocation needs Git metadata and path-safe setup | Observed | Use an actual no-space Git checkout/worktree; capture exact source revision and logs. |
| Timing-search accuracy advantage is unresolved | Measured studies include zero in paired intervals | Do not claim binary search is more accurate; design stronger equal-budget experiments. |
| Simulation models omit physical I/O effects | Known limitation | Keep model, RTL, routed, board, and silicon evidence separate; add a controllable external target. |
| I2C electrical behavior depends on open-drain/release and pull-ups | Open | Verify pin OE behavior and document required external pull-ups/board wiring. |

## 6. Claims that must not be made yet

- Do not say the current design is a completed Tiny Tapeout submission or passes precheck.
- Do not quote current RTL area, maximum clock, protocol bit rate, pad delay, or silicon timing; none has been measured for this RTL.
- Do not call the v0.1 fixed SPI block a general-purpose protocol emulator.
- Do not claim a reliable statistical accuracy win for binary search from the current studies; the paired confidence intervals include zero.
- Do not call the 64-word ISA/store implemented or area-feasible; it is a proposal awaiting RTL and synthesis.

## 7. Source of truth and update policy

Use `docs/COMPETITION_PLAN_AND_EVIDENCE.md` as the detailed evidence ledger and this report as its high-level status view. When implementation, a test result, rule interpretation, or tool result changes, update both with the source revision, command, outcome, and evidence limits. Keep artifacts and logs tied to the exact source revision they validate.
