# Differential Timing Fuzzing & Counterexample Minimization Report

- **Timestamp (UTC):** 2026-11-16T13:46:33.754807+00:00
- **Evidence class:** `rtl_simulation_differential_targets`
- **Targets:** Reference (`CS_N` high >= `1` cycle after `WREN`) vs Buggy DUT (requires >= `6` cycles after `WREN`)
- **Baseline (payload-only fuzzing, fixed safe gap `WAIT_PARAM=10`, budget=30, seed=20261116):** `0` divergences found
- **Joint timing + payload fuzzing (same budget=30, seed=20261116):** `3` divergences found (first hit at step `16`: commands `['0x06', '0x06']`, `WAIT_PARAM=3`)
- **Sequence reduction (Exp 5):** `2 -> 2 transactions` (`['0x06', '0x06']` -> **`['0x06', '0x06']`**)
- **Tightest timing threshold:** fails at `WAIT_PARAM <= 3` (`CS_N` high `<= 5` cycles); passes at `WAIT_PARAM >= 4` (`CS_N` high `>= 6` cycles)
- **Deterministic replay (Exp 2):** `10/10` exact matches (`Ref=0x02`, `DUT=0x00`)

## Timing Window Scan on Minimized Sequence

| `WAIT_PARAM` | `CS_N` high cycles | Ref MISO | DUT MISO | Diverged |
|---|---|---|---|---|
| 0 | 2 | `0x02` | `0x00` | YES |
| 1 | 3 | `0x02` | `0x00` | YES |
| 2 | 4 | `0x02` | `0x00` | YES |
| 3 | 5 | `0x02` | `0x00` | YES |
| 4 | 6 | `0x02` | `0x02` | no |
| 5 | 7 | `0x02` | `0x02` | no |
| 6 | 8 | `0x02` | `0x02` | no |
| 7 | 9 | `0x02` | `0x02` | no |
| 8 | 10 | `0x02` | `0x02` | no |
| 9 | 11 | `0x02` | `0x02` | no |
| 10 | 12 | `0x02` | `0x02` | no |
| 11 | 13 | `0x02` | `0x02` | no |
| 12 | 14 | `0x02` | `0x02` | no |
