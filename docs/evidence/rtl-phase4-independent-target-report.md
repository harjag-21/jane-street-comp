# Phase 4 Independent Target Validation Report

Validated the Timing Boundary Explorer (`ProtocolClient`, `run_spi_boundary_exploration`, `run_autonomous_scan`, `classify_boundary`, `summarize_repeated_trials`) against a separately written SPI target module (`independent_spi_target`) with hidden ground-truth rules.

| Case | Ground Truth | Explorer Result | Pass Gate |
|---|---|---|---|
| Sharp threshold | `low_cycles >= 5` | `sweep=5`, `binary=5`, error=`0` cycles, scan match=`True` | PASS (`0 <= 1` cycle error) |
| Non-monotonic (`pass-fail-pass`) | Pass `[[2, 4], [8, 13]]`, Fail `[[5, 7]]` | `status=non_monotone`, pass=`[[2, 4], [8, 13]]`, fail=`[[5, 7]]`, scan match=`True` *(note: 1-D binary search missed `[[5, 7]]` and reported `censored_all_pass`)* | PASS (both pass regions preserved) |
| Probabilistic noisy (`K=20`) | Certain fail `<=3`, stochastic `[4..6]`, certain pass `>=7` | `status=probabilistic_transition`, `boundary=None`, `uncertain_params=[4, 5, 6]` | PASS (no deterministic boundary claimed) |

## Probabilistic Zone Measurements (`K=20` per point)

| `low_cycles` | Passes / Trials | Pass Rate | Wilson 95% CI | True Analytic `p` |
|---|---|---|---|---|
| 2 | 0/20 | 0.00 | [0.000, 0.161] | 0.00 |
| 3 | 0/20 | 0.00 | [0.000, 0.161] | 0.00 |
| 4 | 5/20 | 0.25 | [0.112, 0.469] | 0.25 |
| 5 | 11/20 | 0.55 | [0.342, 0.742] | 0.50 |
| 6 | 15/20 | 0.75 | [0.531, 0.888] | 0.75 |
| 7 | 20/20 | 1.00 | [0.839, 1.000] | 1.00 |
| 8 | 20/20 | 1.00 | [0.839, 1.000] | 1.00 |
| 9 | 20/20 | 1.00 | [0.839, 1.000] | 1.00 |
