# Protocol Timing Explorer ASIC — Project Plan and Evidence Ledger

**Purpose:** working control document for the Jane Street Protocol Emulator ASIC competition project. Use it to track requirements, design decisions, evidence, risks, and claims made in the repository or submission.

**Last reviewed:** 2026-11-15 (Asia/Kolkata)  
**Competition deadline:** 2027-01-18, per the current official competition page  
**Working project identity:** a reprogrammable protocol emulator used as a timing-boundary explorer

> Keep this file honest. A plan is not a result. A simulation is not gate-level evidence. Gate-level evidence is not measured silicon. A claim without a source or reproducible artifact must not be presented as established fact.

## 1. Project objective

Build an open-source, reprogrammable protocol emulator ASIC for Tiny Tapeout CMOS5L. Its most memorable use should be helping a user explore how a digital target responds to controlled protocol-timing changes.

The chip remains a general-purpose protocol engine: protocols are programs loaded after fabrication, not fixed UART/SPI/I2C blocks. A host tool will configure runs, sweep one or more timing parameters, collect timestamped responses, and produce a report and replayable trace.

### Research question (hypothesis, not novelty claim)

Can a compact programmable I/O engine and host workflow systematically characterize the timing limits of a digital protocol target by generating controlled timing variations, recording target responses, and reporting reproducible acceptance boundaries?

This is a project hypothesis. Prior work exists on programmable I/O, protocol specification and compilation, active protocol inference, and capture/replay/perturbation. Do not claim that any of those broad ideas is new. See §4.

### Success criteria

**Submission floor — must complete:**

- Open-source RTL on the official Tiny Tapeout IHP CMOS5L template.
- Reprogrammable protocol behavior, with a documented loader and ISA/toolchain.
- Working demonstrations for UART, SPI, and I2C, with explicit roles and settings documented.
- RTL tests and a written verification approach.
- Passing Tiny Tapeout GDS flow and precheck.
- Architecture, ISA, examples, external hardware needs, and AI-use disclosure documented.

**Main differentiator — target:**

- A timing sweep can be described/configured from the host, executed reproducibly, and tied to captured response events.
- The tool reports a boundary or response map and can replay a selected run.
- At least one known-behavior target/model validates the measurement method; seek a second, independently implemented or physical target for a stronger demonstration.

**Risk option — only after the main path works:**

- Adaptive probe selection that reduces the number of experiments needed to locate a timing boundary.
- Promote only if it has a measured advantage over a straightforward sweep and does not endanger hardening or required deliverables.

## 2. Proposed system and boundaries

### End-to-end user flow

1. Select a protocol program and target pins.
2. Specify a transaction plus a timing parameter to vary (for example, sample phase, bit period, or an inter-edge delay).
3. Generate a deterministic run or parameter sweep.
4. Drive the target and timestamp/capture relevant input/output events.
5. Classify target responses using a documented oracle or explicit user-supplied rule.
6. Plot/report the observed response boundary and save a replayable trace with configuration and tool versions.

### Hardware concepts to evaluate, not yet frozen

- A small deterministic sequencer with pin direction/value control, bit shifting, cycle delays, input-dependent branches, and bounded waits.
- A free-running cycle counter and edge/event timestamps.
- A compact capture buffer or FIFO drained by the host.
- A host loading/control path that does not unnecessarily consume protocol I/O during a run.
- Optional deterministic perturbation support, preferably expressed as parameters to a normal protocol program rather than a second complex execution engine.

No exact engine count, instruction encoding, RAM macro, clock, loader pinout, capture depth, or protocol-role matrix is approved yet. These choices must follow an area/timing estimate and a runnable vertical slice. Multi-engine execution, broad automatic state-machine inference, USB, and Ethernet are outside the initial commitment.

### Target selection rule

First create a controllable target model with a known timing threshold. This validates the search and report pipeline. Then choose a real or independently implemented target based on actual available hardware and reproducible reset/control. Do not commit to a physical I2C device until its relevant timing behavior, pull-up needs, voltage, and board connections are confirmed.

Because the ASIC cannot be available before submission, a physical demonstration must use a suitable FPGA or other test setup if one is available. Otherwise, label the demonstration accurately as RTL/gate-level simulation and do not imply a measured silicon result.

## 3. Work plan and gates

Dates below are planning targets, not official competition milestones. Re-plan if the toolchain or physical flow takes longer than expected. Preserve a final hardening buffer.

| Phase | Target window | Deliverable | Exit gate / fallback |
|---|---|---|---|
| 0. Rules, tools, and scope | Nov 15–17 | This ledger; confirm official template/flow; inventory available host/FPGA/Pmod equipment; choose first target model and timing parameter | No architecture lock until I/O, host loading, reset, and target assumptions are written down |
| 1. Vertical slice | Nov 18–24 | Minimal executable model: load/configure one program, generate a waveform, capture response events, produce a basic report | A known-threshold target gives the expected boundary. If not, simplify to one protocol and one swept parameter |
| 2. ASIC foundation | Nov 25–Dec 7 | Template-based RTL wrapper, reset, host configuration/loading path, first sequencer operations, synthesis and early area/timing data | A tiny UART or pin-pattern program runs end to end. If loading is blocking, use the simplest documented boot interface |
| 3. Required protocol floor | Dec 8–21 | UART, SPI, I2C firmware examples and protocol reference tests; stable ISA/toolchain draft | All required examples run in RTL. Freeze instruction semantics before broadening features |
| 4. Explorer integration | Dec 22–Jan 4 | Timestamped capture plus host sweep/report/replay; one known target and, if feasible, a second target | Main demo reproduces a known boundary and a saved trace. If capture storage is risky, stream compact events or reduce capture depth |
| 5. Freeze and physical flow | Jan 5–11 | Feature freeze; full Tiny Tapeout hardening, GDS/precheck, gate-level verification, area/timing reports | Passing flow with evidence archived. Cut optional features immediately if reruns or repairs are needed |
| 6. Submission buffer | Jan 12–17 | Clean public repository, final documentation, example programs, verification report, AI-use note, final rerun | Submit only claims supported by artifacts; preserve a recovery buffer before Jan 18 |

### Parallel risk experiments

- Keep experiments isolated from the submission path (separate branch or small host-side prototype).
- Limit the first adaptive-search spike to a short, predefined timebox after the simple sweep works.
- Before promoting an experiment, state its expected measurable benefit, area/software cost, and a kill criterion.
- At most one risky feature may become a hard submission dependency. Everything else stays optional.

## 4. Prior-art and novelty ledger

| Topic | Verified prior art / source | Consequence for our claims |
|---|---|---|
| Programmable protocol engines | RP2040 PIO has multiple concurrently executing state machines and shared instruction memory. [RP2040 datasheet](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf) | Do not claim that a tiny programmable pin sequencer or multiple lanes is novel by itself. |
| Protocol description and hardware generation | PRO-GRAM compiles grammar-based protocol descriptions into synthesizable VHDL. [Paper](https://websrv.cecs.uci.edu/~papers/compendium94-03/papers/1996/isss96/pdffiles/1_3.pdf) | Do not claim protocol-to-hardware compilation as a new general idea. Our target, if any, is compilation to a reprogrammable pin-engine ISA with timing analysis. |
| Executable formal protocol models | ESL generates Mealy machines and executable Verilog/SMV protocol models. [Paper](https://www.cs.cmu.edu/~dongw/FMCAD00.pdf) | Formal protocol models and executable generation are established. |
| One spec for stimulus and monitoring | ProtoLang proposes using one protocol program to drive designs and reconstruct transactions from waveforms; it is a 2026 preprint and describes work in progress. [Preprint](https://arxiv.org/abs/2606.13659) | Do not present “one specification for driver and monitor” alone as our novelty. |
| Timed-model code generation | Code generation from timed automata and real-time models is established. [On Implementable Timed Automata](https://pmc.ncbi.nlm.nih.gov/articles/PMC7281891/) | Timing contracts and static analysis need a precise, target-specific contribution and evaluation. |
| Capture/replay/perturbation | CAERUS records, replays, and perturbs digital signals to uncover timing behavior. [Paper](https://www.usenix.org/system/files/cset19-paper_seitz.pdf) | Do not claim capture/replay or timing perturbation alone as novel. Investigate the specific integrated protocol timing-boundary workflow. |
| Protocol inference | Active and passive protocol state-machine inference have significant prior literature. [PISE](https://www.ndss-symposium.org/wp-content/uploads/2023/03/bar2023-23002-paper.pdf), [survey comparison](https://arxiv.org/abs/2209.14031) | Broad automatic protocol inference is high-risk and not a core deliverable. |

**Novelty status:** unproven. Before writing “first,” “novel,” or “new,” search for closer work on timing-envelope characterization of digital protocol targets using programmable I/O, and compare the actual method and hardware. Safer wording until then: “This project implements and evaluates …”

## 5. Competition requirements and source-of-truth ledger

The current official page was checked on 2026-11-15. The email attachment and screenshot are user-provided competition communications; the latest update and FAQ on the official page take precedence where older email wording differs.

| ID | Claim / requirement | Current status | Source / evidence |
|---|---|---|---|
| R-01 | Target process is IHP 130 nm CMOS5L using the Tiny Tapeout Verilog flow; current main template targets CMOS5L. | Official, checked 2026-11-15 | [Official rules, update Nov 9](https://blog.janestreet.com/protocol-emulator-asic-competition/) |
| R-02 | Maximum allocation is confirmed as 8×4 tiles (32 tiles); nominal area is about 1.0 mm². | Official, checked 2026-11-15 | [Official rules](https://blog.janestreet.com/protocol-emulator-asic-competition/) |
| R-03 | Submission deadline is January 18, 2027. | Official, checked 2026-11-15 | [Official rules](https://blog.janestreet.com/protocol-emulator-asic-competition/) |
| R-04 | Entry must be open source and use a reprogrammable design capable of supporting protocols through loaded programs. | Official, checked 2026-11-15 | [Challenge and rules](https://blog.janestreet.com/protocol-emulator-asic-competition/) |
| R-05 | Start with UART, SPI, and I2C. Standard settings are suggested; concurrent protocols and controller/target roles are a plus, not mandatory. | Official FAQ, checked 2026-11-15 | [Official FAQ](https://blog.janestreet.com/protocol-emulator-asic-competition/#faq) |
| R-06 | 24 digital pins: 8 inputs, 8 outputs, 8 bidirectional, plus clock/reset. No analog pads. | Official FAQ, checked 2026-11-15 | [Official FAQ](https://blog.janestreet.com/protocol-emulator-asic-competition/#faq) |
| R-07 | CMOS5L pad performance has no silicon characterization yet; published I/O figures are estimates. | Official FAQ, checked 2026-11-15 | [Official FAQ](https://blog.janestreet.com/protocol-emulator-asic-competition/#faq) |
| R-08 | SRAM macros may be used if integration fits and passes precheck. | Official FAQ, checked 2026-11-15 | [Official FAQ](https://blog.janestreet.com/protocol-emulator-asic-competition/#faq) |
| R-09 | Required submission materials include public repository, RTL, tests, passing GDS/precheck, architecture/ISA documentation, toolchain and examples, verification write-up. | Official FAQ, checked 2026-11-15 | [Official FAQ](https://blog.janestreet.com/protocol-emulator-asic-competition/#faq) |
| R-10 | Judging emphasizes flexibility; write-up clarity is weighed heavily. There is no fixed numeric weighting. | Official FAQ, checked 2026-11-15 | [Official FAQ](https://blog.janestreet.com/protocol-emulator-asic-competition/#faq) |
| R-11 | AI tools are allowed; usage must be described in the write-up. | Official FAQ, checked 2026-11-15 | [Official FAQ](https://blog.janestreet.com/protocol-emulator-asic-competition/#faq) |

**Do not reuse stale rule summaries** that say 6×4 is the final maximum or that the CMOS5L template is only on a branch. Those statements were superseded by the Nov 9 official update. Recheck official rules before final submission in case details change.

## 6. Claim and result handling

Every public technical or competition claim must be categorized as one of:

- **Official fact:** linked directly to organizer/Tiny Tapeout primary documentation, with date checked.
- **Prior-art observation:** linked to paper, datasheet, or repository; distinguish peer-reviewed work, preprint, and self-reported project status.
- **Design hypothesis:** planned behavior or expected benefit; never phrase as achieved.
- **Measured result:** record exact artifact/commit, tool version, command, input, output, and limitations.

### Evidence record template

```text
Evidence ID:
Claim supported:
Evidence class: simulation / formal / synthesis / place-and-route / GDS precheck / gate-level / FPGA / physical silicon
Source commit or artifact hash:
Tool and version:
Command / configuration:
Input or test case:
Result and raw-log path:
What this establishes:
What it does not establish:
Reproduced by / date:
```

Never collapse evidence classes. In particular, RTL simulation does not establish routed timing; routed timing does not establish pad timing; precheck does not establish correct protocol behavior; no result establishes fabricated-silicon behavior until it is measured on silicon.

For each final protocol claim, state protocol role, pin mapping, supported modes/rates, test conditions, and evidence class. For each physical result, preserve the complete flow logs and the exact source revision.

## 7. Risk register

| Risk | Impact | Mitigation / decision gate |
|---|---|---|
| Explorer software grows into a second major project | ASIC or required protocols unfinished | Keep the first version to one parameter, deterministic sweep, event capture, report, and replay. Adaptive search stays optional. |
| Host loader consumes useful pins or conflicts with protocol I/O | Demonstration and flexibility reduced | Decide host/pin multiplexing early; document the pin budget and boot/reset sequence. |
| Timestamp/capture memory is too costly or difficult to route | Area, timing, or GDS failure | Prototype compact edge records; estimate FIFO depth and host drain rate; synthesize and harden early. |
| Target response oracle is ambiguous | Boundary report is not meaningful | Start with a known-threshold model and explicit classification rule; add a second independent target only after this is stable. |
| External target voltage/pull-up/transceiver needs are unclear | Unsafe or non-reproducible board demo | Check electrical needs and Pmod support before choosing the target; document all external parts. |
| P&R or flow instability near deadline | No valid tapeout artifact | Run the official flow early; freeze features by Jan 5 target; retain rerun buffer. |
| Claims overstate evidence or novelty | Credibility loss | Use this ledger; link artifacts; label simulations and self-reported comparisons precisely. |
| Current hardware/tool access is insufficient for a physical demo | Reduced demonstration strength | Keep RTL/gate-level path as the guaranteed demo; investigate FPGA/board access in Phase 0 without blocking design. |

## 8. Current status (setup gate)

- Workspace began with an unborn local Git repository. A local setup snapshot was committed as `2321a8ca9deb6a8880e4684cc901c388461a62f9` so Tiny Tapeout's tools can identify the source revision; no remote is configured and nothing was published. The official Tiny Tapeout CMOS5L template scaffold was copied from upstream `main` at commit `4b1204ab387b5ab94db1d57067051d8d89a2f83c` (upstream commit date 2026-10-28); `info.yaml` allocation is `8x4`.
- Project identity is provisional: title `Timing Boundary Explorer`, author `TBD`, top module `tt_um_example`, and working description in `info.yaml`. Replace author and top-module name before publication/submission.
- Host inventory: Git 2.51.2, Python 3.13.7, Icarus Verilog 13.0, Docker CLI 29.6.2. Verilator, Yosys, OpenLane, `tt_tool.py`, `make`, Cocotb, and GitHub CLI were not found on the host PATH. Docker Desktop was started and its daemon responds.
- The template development image was built as `jane-street-asic-dev:diagnostic` with an explicit PyPI index after the default package index failed to resolve pinned packages. The unmodified template RTL test passed in that image: Cocotb 2.0.1, Icarus 12.0, `1/1 PASS`. Raw output: `docs/evidence/template-rtl-baseline.log`. This establishes the RTL simulation baseline only.
- The repository's `.devcontainer/Dockerfile` now sets the public PyPI index and retry/timeout values to fetch the pinned packages in this environment. `.gitattributes` keeps copied shell scripts LF-terminated for Linux containers. A container built from a cached base plus the Tiny Tapeout Docker-in-Docker feature has a responding nested Docker daemon. Its first startup hook failed because the cached base contained a CRLF copy of the support-tools script. The source shell file is now LF-normalized; the existing container hook was not rerun.
- Tiny Tapeout support tools were copied into ignored local `tt/` from upstream commit `9d13ff3e3e6202090740110438c6a10c1d62ca0f`. `tt_tool.py --pdk ihp-sg13cmos5l --create-user-config` succeeded and generated an 8x4 configuration (`DIE_AREA: 0 0 1724.16 710.64`). This validates metadata/config generation, not GDS, timing, or precheck.
- CIEL release manifest for IHP-SG13 version `cc9afba57e0ec1b5ba77e6bbb638ba9dbe5866b8` was fetched and checked. The 505,034,071-byte `common.tar.zst` asset was fully downloaded, decompressed, and extracted (8,155 members; 899,174,064 uncompressed member bytes). CMOS5L I/O, SRAM, and standard-cell archives were also verified and extracted (12,619,905; 2,596,008; and 6,290,705 compressed bytes respectively). `ciel enable --pdk ihp-sg13cmos5l cc9afba57e0ec1b5ba77e6bbb638ba9dbe5866b8` succeeded; `ciel output` and `ciel ls` returned that version. Recorded confirmation: `docs/evidence/pdk-install.log`. The active PDK is under the running development container's `/home/vscode/ttsetup/pdk`; the download archives remain in ignored `.ttsetup/downloads` for recovery. A separate incomplete extraction remains under ignored workspace `.ttsetup/pdk`; it is not the active PDK root.
- Project architecture: not frozen. RTL remains the untouched template example. The proposal and decision gates are in `docs/TIMING_BOUNDARY_EXPLORER_ARCHITECTURE.md`. The initial simulation target parameters are selected for the software study only; no RTL DUT, competition pin map, instruction set, or hardware interface is frozen.
- The deterministic software reference model and timing oracle are implemented in `experiments/spi_timing_model.py`. For the declared model (`T=10 ns`, target delay `23 ns`, setup `2 ns`, response `0xAA`), the analytical first passing low phase is 3 cycles. The six model tests pass (`python -m unittest discover -s experiments -v`). Deterministic binary search found cycle 3 in 5 transactions; the fixed scan examined 8 settings. In the earlier seeded jitter sweep (`uniform integer delay perturbation ±6 ns`, seed `20261115`, 10,000 transactions per setting), exact-byte success counts at low phases 1–8 cycles were `0, 0, 5485, 10000, 10000, 10000, 10000, 10000`. Raw results: `docs/evidence/spi-reference-study.json`.
- A paired software comparison is recorded in `docs/evidence/spi-paired-jitter-study.json`: 30 seeds, 200 pre-generated per-trial jitter profiles per probe, with identical profiles reused across settings and both algorithms. The estimand is the first integer low phase with empirical exact-byte success rate at least 95%; 95% is a study convention, not a universal requirement. Both methods recovered the model boundary (4 cycles) in 30/30 runs. Median transaction counts were 1,600 for the 8-setting fixed scan and 1,000 for binary search (37.5% fewer for this model/range, with equal repeats per queried setting). This is not an equal-total-budget accuracy comparison and is not evidence of novelty or hardware performance. Validation record: `docs/evidence/spi-paired-jitter-validation.log`.
- A separate Cocotb event-model check is in `experiments/cocotb_spi/`; it uses an empty signal shell, not the competition RTL. On Icarus 12.0/Cocotb 2.0.1 it passed 1/1 test: the 1–8-cycle sweep at 23 ns target delay matched the 3-cycle oracle boundary, retained the preloaded first response bit, and the exact-equality case (28 ns delay + 2 ns setup in a 30 ns low phase) returned `0xAA` with all seven measured transition ages equal to 2 ns. Captured output: `docs/evidence/cocotb-spi-boundary.log`. This validates only the testbench event model and equality convention, not an ASIC implementation.
- Tiny Tapeout hardening was attempted several times on the untouched template. Attempts 1–5 exposed, respectively, a missing Git commit, a failed default Docker image pull, a wrong system-Python PATH, ABC path parsing caused by the workspace's spaces, and a clean-worktree configuration file missing because it is generated/ignored; their records are `docs/evidence/template-hardening-attempt-01.log` through `template-hardening-attempt-05.log`. The runner image's exact OCI Linux/amd64 manifest, config, and layer digests are recorded in `docs/evidence/librelane-image-download.log`; the image was imported and LibreLane reported `3.1.0.dev4`. The successful no-space-worktree attempt used source commit `6dadfe03b834d1216690c6d25949c441b82acfe6` and downloaded/used CIEL PDK version `89d42ee6d2fd9b50b86a1aada6fee6673c3e6008` alongside the previously installed `cc9afba57e0ec1b5ba77e6bbb638ba9dbe5866b8`. It passed early lint/synthesis/physical-design steps and produced a 5.4 MB KLayout stream-out GDS, then remained in Magic DRC for 30 minutes and was stopped at the announced runtime checkpoint. This is not a completed hardening, final GDS, or precheck pass. The captured console log is `docs/evidence/template-hardening-baseline.log`; the full run directory is ignored under `runs/template-hardening-baseline/`. No project RTL, completed GDS/precheck, gate-level, FPGA, or silicon result exists.
- Official competition facts above were checked against the organizer’s page on 2026-11-15.

## 9. Decision log

| Date | Decision | Reason | Revisit condition |
|---|---|---|---|
| 2026-11-15 | Make timing-boundary exploration the primary product/demo identity, with a modest programmable protocol engine as the foundation. | More distinctive use than protocol count alone; aligns with debugging/reverse-engineering motivation. | Revisit if a controllable target and reproducible timing sweep cannot be demonstrated early. |
| 2026-11-15 | Keep adaptive probe selection as a risk option, not a submission dependency. | Potential edge, but adds algorithm/tool scope and depends on a sound basic sweep. | Promote only after a simple sweep is correct and the adaptive method shows a measurable improvement. |

## 10. Immediate next actions

1. Extend the Cocotb model check to sweep the full cycle range and compare every transaction against the independent Python oracle, including first-bit and setup-boundary cases.
2. Add equal-total-budget comparisons, boundary-error summaries, and confidence intervals for the paired algorithms; avoid treating a 30-seed result as a universal performance guarantee.
3. Decide the smallest loaded program format, host loader, trace record, and pin allocation needed for one programmable SPI transaction; document each decision before RTL grows.
4. Implement the minimum programmable SPI timing engine and pass the measurement gate in simulation.
5. Re-run the physical flow early from the no-space worktree, and use a defined runtime budget for Magic DRC/precheck before adding more capture memory or protocols.

Update this document whenever a rule is rechecked, an architecture decision is made, a result is produced, or a risk changes. Keep raw logs and generated evidence in the repository; this ledger is an index, not a substitute for them.
