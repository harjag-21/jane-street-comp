# 20-Seed Held-Out Failure-Shape Benchmark (Frozen Strategies)

- **Timestamp (UTC):** 2026-11-16T18:19:56.972419+00:00
- **Evidence class:** `rtl_simulation_heldout_shape_benchmark`
- **Target:** `MODE_SPI_SHAPES` (`H1` interior notch `gap 8..9`, `H2` bimodal `{2,3} U {12,13}`, `H3` quiet-prefix 3-step `gap 4..6`, `H4` watchdog tail `gap 14..15`)
- **Frozen baseline guarantee:** `tools/fuzz_benchmark.py` strategies (`payload_only`, `joint_random`, `guided_feedback`, `guided_diverse`) run completely unmodified from commit `23bca4a`.
- **Scale:** `100` trials/seed x `20` disjoint seeds (`20262101..20262120`) in `752.36s`

## Strategy Comparison (Matched Trial Budget)

| Strategy | Mean +/- SD Bugs (`0..4`) | Median `[min, max]` | Mean Obs. Pin Signatures | Mean Raw Divergences |
|---|---|---|---|---|
| `payload_only` | **0.00** +/- 0.00 | `0.0` `[0, 0]` | 10.9 | 0.0 |
| `joint_random` | **0.45** +/- 0.51 | `0.0` `[0, 1]` | 26.8 | 0.6 |
| `guided_feedback` | **0.45** +/- 0.60 | `0.0` `[0, 2]` | 23.4 | 2.4 |
| `guided_diverse` | **0.70** +/- 0.57 | `1.0` `[0, 2]` | 27.6 | 2.5 |

## Paired Seed-Level Statistical Comparisons (`10,000` Bootstrap Resamples)

| Pair (`A - B`) | Mean Bug Delta (95% CI) | Mean Sig Delta (95% CI) | Seed Win / Tie / Loss |
|---|---|---|---|
| `guided_feedback_vs_joint_random` | **+0.00** `[-0.30, +0.30]` | -3.4 `[-4.8, -1.9]` | `3W / 13T / 4L` |
| `guided_diverse_vs_joint_random` | **+0.25** `[-0.10, +0.55]` | +0.8 `[-0.5, +2.0]` | `9W / 7T / 4L` |
| `guided_diverse_vs_guided_feedback` | **+0.25** `[-0.10, +0.60]` | +4.2 `[+2.1, +6.2]` | `10W / 5T / 5L` |

## Per-Shape Hit Rate & Mean Trials-to-Discover (TTD)

| Bug Shape | Timing Shape | `payload_only` | `joint_random` | `guided_feedback` | `guided_diverse` |
|---|---|---|---|---|---|
| `H1_interior_notch` | Narrow notch `gap in {8,9}` (silent `0xAB`) | `0%` (-) | `0%` (-) | `0%` (-) | `10%` (15) |
| `H2_bimodal_band` | Two bands `{2,3} U {12,13}` (silent `0x04`) | `0%` (-) | `30%` (31.8) | `20%` (23.2) | `20%` (45.5) |
| `H3_three_step_cascade` | Quiet `0xB9 -> 0x35`, `gap in {4,5,6}` | `0%` (-) | `10%` (46.5) | `20%` (53.5) | `40%` (59.2) |
| `H4_upper_watchdog_tail` | Extreme tail `gap in {14,15}` | `0%` (-) | `5%` (57) | `5%` (71) | `0%` (-) |

## Minimized & Replayed Counterexamples

| Bug Shape | Discovered Sequence (`gap_wait`) | Minimized Sequence | Observed Timing Envelope | Replay Rate |
|---|---|---|---|---|
| `H1_interior_notch` | `0xab -> 0x02 -> 0xab` (`gap=9`) | `0xab -> 0x02 -> 0xab` | fails `gap in [8, 9]` | `5/5` |
| `H2_bimodal_band` | `0x04 -> 0x9f -> 0x35` (`gap=3`) | `0x04 -> 0x9f -> 0x35` | fails `gap in [2, 3, 12, 13]` | `5/5` |
| `H3_three_step_cascade` | `0xb9 -> 0x35 -> 0x05` (`gap=4`) | `0xb9 -> 0x35 -> 0x05` | fails `gap in [4, 5, 6]` | `5/5` |
| `H4_upper_watchdog_tail` | `0x05 -> 0x02 -> 0xab` (`gap=14`) | `0x05 -> 0x02 -> 0xab` | fails `gap in [14, 15]` | `5/5` |

## Per-Seed Breakdown (All Held-Out Seeds)

| Seed | `payload_only` (Bugs / Sigs / Raw) | `joint_random` (Bugs / Sigs / Raw) | `guided_feedback` (Bugs / Sigs / Raw) | `guided_diverse` (Bugs / Sigs / Raw) |
|---|---|---|---|---|
| `20262101` | **0** (`-`) / 11 / 0 | **1** (`H2`) / 24 / 3 | **1** (`H2`) / 22 / 9 | **0** (`-`) / 28 / 0 |
| `20262102` | **0** (`-`) / 12 / 0 | **0** (`-`) / 27 / 0 | **0** (`-`) / 22 / 0 | **1** (`H3`) / 30 / 6 |
| `20262103` | **0** (`-`) / 11 / 0 | **0** (`-`) / 27 / 0 | **0** (`-`) / 27 / 0 | **1** (`H3`) / 31 / 6 |
| `20262104` | **0** (`-`) / 11 / 0 | **0** (`-`) / 27 / 0 | **2** (`H3,H4`) / 29 / 6 | **1** (`H3`) / 25 / 1 |
| `20262105` | **0** (`-`) / 13 / 0 | **0** (`-`) / 26 / 0 | **0** (`-`) / 23 / 0 | **0** (`-`) / 23 / 0 |
| `20262106` | **0** (`-`) / 11 / 0 | **0** (`-`) / 25 / 0 | **0** (`-`) / 24 / 0 | **1** (`H3`) / 22 / 3 |
| `20262107` | **0** (`-`) / 12 / 0 | **0** (`-`) / 28 / 0 | **1** (`H3`) / 24 / 4 | **1** (`H2`) / 27 / 3 |
| `20262108` | **0** (`-`) / 12 / 0 | **1** (`H3`) / 26 / 1 | **0** (`-`) / 24 / 0 | **1** (`H2`) / 30 / 5 |
| `20262109` | **0** (`-`) / 10 / 0 | **0** (`-`) / 25 / 0 | **0** (`-`) / 24 / 0 | **1** (`H2`) / 27 / 8 |
| `20262110` | **0** (`-`) / 9 / 0 | **0** (`-`) / 27 / 0 | **0** (`-`) / 27 / 0 | **0** (`-`) / 27 / 0 |
| `20262111` | **0** (`-`) / 10 / 0 | **1** (`H2`) / 27 / 1 | **1** (`H2`) / 22 / 7 | **1** (`H3`) / 32 / 3 |
| `20262112` | **0** (`-`) / 10 / 0 | **0** (`-`) / 26 / 0 | **1** (`H3`) / 24 / 5 | **0** (`-`) / 23 / 0 |
| `20262113` | **0** (`-`) / 12 / 0 | **1** (`H2`) / 28 / 1 | **0** (`-`) / 21 / 0 | **0** (`-`) / 28 / 0 |
| `20262114` | **0** (`-`) / 10 / 0 | **1** (`H4`) / 30 / 1 | **1** (`H3`) / 25 / 4 | **0** (`-`) / 29 / 0 |
| `20262115` | **0** (`-`) / 9 / 0 | **1** (`H2`) / 24 / 1 | **0** (`-`) / 19 / 0 | **1** (`H1`) / 28 / 1 |
| `20262116` | **0** (`-`) / 8 / 0 | **1** (`H2`) / 25 / 1 | **1** (`H2`) / 25 / 8 | **0** (`-`) / 24 / 0 |
| `20262117` | **0** (`-`) / 12 / 0 | **1** (`H3`) / 27 / 1 | **0** (`-`) / 28 / 0 | **1** (`H3`) / 30 / 1 |
| `20262118` | **0** (`-`) / 12 / 0 | **1** (`H2`) / 29 / 1 | **1** (`H2`) / 20 / 4 | **2** (`H2,H3`) / 31 / 6 |
| `20262119` | **0** (`-`) / 11 / 0 | **0** (`-`) / 28 / 0 | **0** (`-`) / 17 / 0 | **1** (`H3`) / 31 / 6 |
| `20262120` | **0** (`-`) / 12 / 0 | **0** (`-`) / 30 / 0 | **0** (`-`) / 22 / 0 | **1** (`H1`) / 26 / 1 |
