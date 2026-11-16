# Independently Parameterized Transfer Study (Frozen Strategies)

- **Timestamp (UTC):** 2026-11-16T19:22:32.678892+00:00
- **Evidence class:** `rtl_simulation_parameterized_transfer_study`
- **Frozen baseline guarantee:** `tools/fuzz_benchmark.py` (`joint_random`, `guided_feedback`, `guided_diverse`) unmodified since commit `23bca4a`.
- **Target setup:** `10` independently parameterized 4-bug target instances (`MODE_SPI_PARAM=9`) frozen from RNG `20269999` prior to testing x `2` seeds/instance (`20` matched runs, budget `150` trials each) in `812.34s`.

## End-of-Budget Summary (`t = 150` Trials)

| Strategy | Mean +/- SD Unique Bugs (`0..4`) | Median `[min, max]` | Mean Obs. Pin Signatures | Mean Raw Divergences |
|---|---|---|---|---|
| `joint_random` | **1.70** +/- 0.92 | `2.0` `[0, 3]` | 57.9 | 2.3 |
| `guided_feedback` | **0.75** +/- 0.85 | `1.0` `[0, 3]` | 49.1 | 6.0 |
| `guided_diverse` | **0.65** +/- 0.74 | `0.5` `[0, 2]` | 54.2 | 2.8 |

## Paired Discovery Differences Across Trials (`10,000` Bootstrap Resamples)

| Comparison (`A - B`) | `t = 37` Delta (95% CI) | `t = 75` Delta (95% CI) | `t = 112` Delta (95% CI) | `t = 150` Delta (95% CI) |
|---|---|---|---|---|
| `guided_diverse_vs_joint_random` | -0.35 `[-0.60, -0.10]` | -0.75 `[-1.00, -0.50]` | -1.20 `[-1.55, -0.85]` | **-1.05** `[-1.45, -0.65]` (`1W/4T/15L`) |
| `guided_feedback_vs_joint_random` | +0.00 `[-0.35, +0.35]` | -0.40 `[-0.75, -0.05]` | -0.80 `[-1.25, -0.35]` | **-0.95** `[-1.40, -0.45]` (`3W/4T/13L`) |
| `guided_diverse_vs_guided_feedback` | -0.35 `[-0.65, -0.05]` | -0.35 `[-0.60, -0.05]` | -0.40 `[-0.75, -0.05]` | **-0.10** `[-0.50, +0.30]` (`6W/7T/7L`) |

## Replay Verification Across Target Instances (`9/9` Exact `5/5` Determinism)

| Target | Bug ID | Commands | `gap_wait` | Replay |
|---|---|---|---|---|
| `T00` | `P0_0xb1` | `0x02 -> 0x04 -> 0x04` | `12` | `5/5` |
| `T01` | `P1_0xb2` | `0x05 -> 0x04 -> 0xab` | `13` | `5/5` |
| `T03` | `P2_0xb3` | `0x05 -> 0x35 -> 0x05` | `11` | `5/5` |
| `T04` | `P2_0xb3` | `0xab -> 0x02 -> 0x9f` | `12` | `5/5` |
| `T05` | `P1_0xb2` | `0x04 -> 0x02 -> 0xab` | `12` | `5/5` |
| `T06` | `P3_0xb4` | `0x05 -> 0x06 -> 0x04` | `11` | `5/5` |
| `T07` | `P1_0xb2` | `0x06 -> 0x02 -> 0x04` | `13` | `5/5` |
| `T08` | `P0_0xb1` | `0x35 -> 0x05 -> 0x04` | `12` | `5/5` |
| `T09` | `P1_0xb2` | `0x06 -> 0x05 -> 0x35` | `1` | `5/5` |

## Per-Run Breakdown

| Instance | Seed | `joint_random` (Bugs / Sigs) | `guided_feedback` (Bugs / Sigs) | `guided_diverse` (Bugs / Sigs) |
|---|---|---|---|---|
| `T00` | `20265000` | **2** / 62 | **2** / 52 | **2** / 65 |
| `T00` | `20265001` | **2** / 66 | **0** / 51 | **1** / 58 |
| `T01` | `20265100` | **3** / 62 | **1** / 46 | **1** / 51 |
| `T01` | `20265101` | **2** / 59 | **1** / 50 | **1** / 57 |
| `T02` | `20265200` | **0** / 58 | **0** / 45 | **0** / 47 |
| `T02` | `20265201` | **2** / 56 | **0** / 48 | **1** / 64 |
| `T03` | `20265300` | **0** / 50 | **1** / 49 | **0** / 58 |
| `T03` | `20265301` | **3** / 62 | **1** / 50 | **0** / 51 |
| `T04` | `20265400` | **1** / 57 | **2** / 57 | **0** / 46 |
| `T04` | `20265401` | **2** / 57 | **1** / 50 | **0** / 43 |
| `T05` | `20265500` | **2** / 55 | **0** / 51 | **1** / 56 |
| `T05` | `20265501` | **0** / 57 | **1** / 49 | **0** / 51 |
| `T06` | `20265600` | **1** / 60 | **1** / 40 | **2** / 58 |
| `T06` | `20265601` | **1** / 58 | **0** / 48 | **0** / 55 |
| `T07` | `20265700` | **2** / 54 | **0** / 50 | **0** / 59 |
| `T07` | `20265701` | **2** / 59 | **0** / 37 | **1** / 50 |
| `T08` | `20265800` | **2** / 55 | **1** / 51 | **0** / 52 |
| `T08` | `20265801` | **2** / 56 | **0** / 56 | **0** / 51 |
| `T09` | `20265900` | **3** / 55 | **3** / 53 | **2** / 58 |
| `T09` | `20265901` | **2** / 60 | **0** / 50 | **1** / 54 |
