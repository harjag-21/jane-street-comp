# Multi-regime equal-budget study — 2026-11-15

## Question

Does the earlier fixed-sweep versus binary-search comparison change when target output delay and transition jitter width change, under the same transaction budget?

## Predeclared design

- Six conditions vary target delay (18, 23, 28 ns) and uniform integer per-transition jitter widths (6, 10, 15.176495, 20 ns).
- Each condition has 1,000 unique seeds. The six seed ranges do not overlap.
- The response is `0xAA`; success is the exact byte plus all seven intended-sample setup checks. The decision threshold is the study convention of 95%.
- Fixed sweep: 100 trials at each of 8 settings. Binary search: 160 trials at each of 5 probes. Each uses exactly 800 transactions per seed.
- The matrix was restricted to analytic boundaries 4 or 5 cycles. This keeps the binary method on its five-probe path so actual transaction budgets match. It deliberately does not test boundaries at the range endpoints or outside the range.
- Results include per-regime Wilson intervals and paired percentile seed-bootstrap intervals (5,000 resamples with recorded, regime-specific seeds).

## Results

| Target delay | Jitter half-width | Oracle boundary | Fixed recovery | Binary recovery | Paired difference (95% bootstrap CI) |
|---:|---:|---:|---:|---:|---:|
| 18 ns | 15.176495 ns | 4 | 1000/1000 | 1000/1000 | 0.0 pp [0.0, 0.0] |
| 23 ns | 6 ns | 4 | 1000/1000 | 1000/1000 | 0.0 pp [0.0, 0.0] |
| 23 ns | 10 ns | 4 | 1000/1000 | 1000/1000 | 0.0 pp [0.0, 0.0] |
| 23 ns | 15.176495 ns | 4 | 789/1000 | 803/1000 | +1.4 pp [−0.7, +3.7] |
| 23 ns | 20 ns | 5 | 1000/1000 | 1000/1000 | 0.0 pp [0.0, 0.0] |
| 28 ns | 15.176495 ns | 5 | 1000/1000 | 1000/1000 | 0.0 pp [0.0, 0.0] |

For the near-threshold condition, the Wilson 95% intervals were 76.3%–81.3% for fixed sweep and 77.7%–82.6% for binary search. The paired interval includes zero, so this sample does not establish an accuracy advantage. The other five conditions saturated at 100% recovery for both methods; they confirm correctness in those tested regimes but provide little information about relative accuracy.

The 23 ns / 15.176495 ns condition uses a new seed range here and is a separate replicate from the earlier 1,000-seed study (769 fixed, 789 binary). The difference between the two paired estimates is consistent with their uncertainty; neither interval excludes zero.

## Independent verification

- `python experiments/run_spi_multiregime_study.py` produced the raw aggregate `spi-multiregime-equal-budget.json`.
- `python experiments/audit_spi_multiregime.py` independently regenerated per-transition jitter profiles and event-scheduled samples, replayed all candidate counts and binary query paths, and matched each boundary, all 800-trial budgets, Wilson intervals, and paired bootstrap intervals across 6,000 unique seed runs.
- At the time of this study, unit tests: 11/11 passed. Search-limit tests were added afterward; the current suite has 15 tests. The seeded Cocotb event-model check: 2/2 passed (documented separately in `spi-jitter-crosscheck-2026-11-15.md`).

## Interpretation and limits

The supported conclusion is narrow: under these synthetic conditions and equal 800-transaction budgets, binary search showed no statistically resolved recovery-accuracy advantage over the fixed sweep. The single informative near-threshold condition had a positive point estimate but a confidence interval crossing zero. The other five conditions were too easy to distinguish the methods. This does not establish hardware performance or research novelty. The target is synthetic, the DUT is still an empty Cocotb signal shell, and no physical, board, RTL, or silicon result is represented.
