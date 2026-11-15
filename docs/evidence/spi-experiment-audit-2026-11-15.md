# SPI experiment audit — 2026-11-15

## Scope

Audited the software SPI reference model, the 1–8-cycle Cocotb event-model cross-check, and the 1,000-seed equal-budget fixed-sweep versus binary-search comparison. This audit does not establish competition RTL behavior, analog target behavior, pad timing, fabricated-silicon behavior, or research novelty.

## Reproduced checks

- `python -m unittest discover -s experiments -v`: 8 tests passed.
- Cocotb 2.0.1 with Icarus 12.0: 1 test passed. The cycle sweep matched the declared deterministic boundary; the exact-equality setup case returned `0xAA` with seven 2 ns transition ages. Captured output: `cocotb-spi-boundary-audit.log`.
- `python experiments/audit_equal_budget.py`: independently reimplemented the per-edge pass decision, fixed allocation, binary-search query order, Wilson intervals, and paired seed bootstrap from the raw JSON. Every per-setting count, boundary, trial count, and summary matched across all 1,000 seeds.

## Independently checked equal-budget results

- Exact per-transaction probabilities for low phases 3, 4, and 5 cycles were 0.0573468764, 0.9599999585, and 1.0. Under the declared 95% threshold, the model boundary is 4 cycles.
- Both methods used 800 transactions per seed in all 1,000 runs. Fixed sweep used 100 trials at each of 8 values; binary search used 160 trials at each of 5 probes.
- Fixed sweep: 769/1,000 boundary recoveries; Wilson 95% interval [0.7418777, 0.7940635].
- Binary search: 789/1,000 boundary recoveries; Wilson 95% interval [0.7626295, 0.8131586].
- Paired difference: +0.02; percentile bootstrap interval [−0.003, +0.043], using 5,000 resamples and seed 20262115. The interval includes zero.

## Corrections made during audit

- Corrected the equal-budget validation record: the generator creates 160 jitter profiles per seed, not 200. Fixed sweep uses the first 100 profiles at each candidate; binary search uses all 160 at its five queried candidates. Both consume 800 transactions per seed.
- Added the bootstrap resample count and seed to the machine-readable results so the paired confidence interval is reproducible.
- Added input validation for empty paired profiles and invalid probability thresholds.
- Updated the ledger's next action: the deterministic Cocotb cycle sweep is complete; jittered Cocotb cross-checking remains future work.

The machine-readable source data are in `spi-equal-budget-study.json`; the independent replay is in `../../experiments/audit_equal_budget.py`.
