# Noisy / Probabilistic Timing Boundary Study (RTL Co-Simulation)

- **Timestamp (UTC):** 2026-11-16T16:22:48.779694+00:00
- **Evidence class:** `rtl_simulation_noisy_target`
- **Target model:** `MODE_SPI_NOISY` (`T=10 ns`, `delay=37 ns +/- 9 ns` uniform LFSR jitter per trial)
- **Sharp baseline classification:** `sharp_threshold` (first p=1.0 at `4` cycles, mixed settings `[]`)
- **Noisy target classification:** `probabilistic_transition_region` (mixed acceptance zone at `low_cycles = [3, 4]`, first `p>=0.50` at `4`, first `p>=0.90` at `5`, width `3` cycles)

## Empirical Pass-Rate Curve (`K=30` RTL Trials/Setting)

| `low_cycles` | Low Window (ns) | Analytic `P(pass)` | Observed Pass/Trials | Empirical `p_hat` | Wilson 95% CI |
|---|---|---|---|---|---|
| 2 | 20 ns | 0.0% | `0/30` | **0.0%** | `[0.0%, 11.3%]` |
| 3 | 30 ns | 15.8% | `3/30` | **10.0%** | `[3.5%, 25.6%]` |
| 4 | 40 ns | 68.4% | `19/30` | **63.3%** | `[45.5%, 78.1%]` |
| 5 | 50 ns | 100.0% | `30/30` | **100.0%** | `[88.6%, 100.0%]` |
| 6 | 60 ns | 100.0% | `30/30` | **100.0%** | `[88.6%, 100.0%]` |
| 7 | 70 ns | 100.0% | `30/30` | **100.0%** | `[88.6%, 100.0%]` |

## Fixed Repeated Sweep vs Naive 1-Shot Binary Search vs Repeated Binary Search (`p >= 0.90`)

- **Repeated fixed sweep (3 extra seeds, `K=20`/setting):** classifications = `['probabilistic_transition_region', 'probabilistic_transition_region', 'probabilistic_transition_region']`, `p >= 0.90` at `low_cycles = [5, 5, 5]`
- **Naive 1-shot binary search (`12` runs, mean `4` trials/run):** estimates = `{'4': 7, '5': 5}` (`41.7%` hit true `p>=0.90` boundary `5`; single-shot noise produced `2` distinct boundary values)
- **Repeated binary search (`p >= 0.90`, `K=20`/probe, `12` runs, mean `80` trials/run):** estimates = `{'5': 12}` (`100.0%` accuracy on true `p>=0.90` boundary `low_cycles = 5`)
