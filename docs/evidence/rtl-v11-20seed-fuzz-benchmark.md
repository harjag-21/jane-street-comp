# 20-Seed Matched Black-Box Feedback Fuzzing Benchmark

- **Timestamp (UTC):** 2026-11-16T17:39:20.867502+00:00
- **Evidence class:** `rtl_simulation_feedback_fuzz_benchmark`
- **Target:** `MODE_SPI_HELD` (`D1..D4` + held-out interior bug `D5_cfg_rd_wrdi_race`), 8 SPI opcodes, inter-CS gap `WAIT_PARAM in 0..15`
- **Design rule:** Strict black-box pin feedback (`ref_bytes`, `dut_bytes`, `cycle_cnt // 8`, `diverged`) over real UART frames; `guided_diverse` adds behavioral-bucket cooldown & prefix balance without bug-specific constants.
- **Scale:** `100` trials/seed x `20` seeds (`20261101..20261120`) in `597.38s`

## Strategy Comparison (Matched Trial Budget)

| Strategy | Mean +/- SD Bugs (`0..5`) | Median `[min, max]` | Seeds Finding `5/5` | Mean Obs. Pin Signatures | Mean Raw Divergences |
|---|---|---|---|---|---|
| `payload_only` | **0.00** +/- 0.00 | `0.0` `[0, 0]` | `0/20` | 27.1 | 0.0 |
| `joint_random` | **1.85** +/- 0.59 | `2.0` `[1, 3]` | `0/20` | 52.1 | 4.5 |
| `guided_feedback` | **2.45** +/- 0.69 | `2.0` `[1, 4]` | `0/20` | 48.6 | 19.4 |
| `guided_diverse` | **2.05** +/- 0.94 | `2.0` `[1, 4]` | `0/20` | 50.2 | 9.0 |

## Paired Seed-Level Statistical Comparisons (`10,000` Bootstrap Resamples)

| Pair (`A - B`) | Mean Bug Delta (95% CI) | Mean Sig Delta (95% CI) | Seed Win / Tie / Loss |
|---|---|---|---|
| `guided_feedback_vs_joint_random` | **+0.60** `[+0.20, +1.00]` | -3.5 `[-6.1, -0.9]` | `11W / 8T / 1L` |
| `guided_diverse_vs_joint_random` | **+0.20** `[-0.30, +0.75]` | -1.9 `[-4.7, +0.8]` | `7W / 7T / 6L` |
| `guided_diverse_vs_guided_feedback` | **-0.40** `[-0.85, +0.05]` | +1.6 `[-1.6, +5.1]` | `4W / 8T / 8L` |

## Per-Bug Discovery Rate & Mean Trials-to-Discover (TTD)

| Bug Class | Role | `payload_only` | `joint_random` | `guided_feedback` | `guided_diverse` |
|---|---|---|---|---|---|
| `D1_wren_fast_drop` | Base | `0%` (-) | `100%` (29) | `100%` (15.3) | `100%` (27.2) |
| `D2_wakeup_slow_recovery` | Base | `0%` (-) | `55%` (51.7) | `20%` (35) | `35%` (45.4) |
| `D3_prog_wren_timeout` | Base | `0%` (-) | `20%` (50.8) | `80%` (52.6) | `35%` (37.1) |
| `D4_fast_read_corrupt` | Base | `0%` (-) | `10%` (78) | `25%` (49.4) | `25%` (29.6) |
| `D5_cfg_rd_wrdi_race` | **HELD-OUT** | `0%` (-) | `0%` (-) | `20%` (50.5) | `10%` (27) |

## Minimized & Replayed Counterexamples

| Bug Class | Discovered Sequence (`gap_wait`) | Minimized Sequence | Window Failing/Passing | Replay Rate |
|---|---|---|---|---|
| `D1_wren_fast_drop` | `0x04 -> 0x06 -> 0x9f` (`gap=1`) | `0x06 -> 0x9f` | gap `1` (fails `<= 2`, passes `>= 3`) | `5/5` |
| `D2_wakeup_slow_recovery` | `0xb9 -> 0xab -> 0x9f` (`gap=0`) | `0xb9 -> 0xab -> 0x9f` | gap `0` (fails `<= 6`, passes `>= 7`) | `5/5` |
| `D3_prog_wren_timeout` | `0x06 -> 0x02 -> 0x05` (`gap=10`) | `0x06 -> 0x02 -> 0x05` | gap `10` (passes `<= 9`, fails `>= 10`) | `5/5` |
| `D4_fast_read_corrupt` | `0x9f -> 0x35 -> 0xb9` (`gap=1`) | `0x9f -> 0x35 -> 0xb9` | gap `1` (fails `<= 1`, passes `>= 2`) | `5/5` |
| `D5_cfg_rd_wrdi_race` | `0x35 -> 0x04 -> 0x35` (`gap=5`) | `0x35 -> 0x04 -> 0x35` | gap `5` (local fail `[3, 5]`; passes `2` & `6`) | `5/5` |

## Per-Seed Breakdown (All Seeds)

| Seed | `payload_only` (Bugs / Sigs / Raw) | `joint_random` (Bugs / Sigs / Raw) | `guided_feedback` (Bugs / Sigs / Raw) | `guided_diverse` (Bugs / Sigs / Raw) |
|---|---|---|---|---|
| `20261101` | **0** (`-`) / 25 / 0 | **2** (`D1,D2`) / 49 / 6 | **2** (`D1,D3`) / 52 / 16 | **2** (`D1,D4`) / 47 / 9 |
| `20261102` | **0** (`-`) / 29 / 0 | **1** (`D1`) / 52 / 1 | **3** (`D1,D3,D5`) / 46 / 18 | **3** (`D1,D2,D5`) / 49 / 12 |
| `20261103` | **0** (`-`) / 26 / 0 | **2** (`D1,D4`) / 49 / 5 | **3** (`D1,D3,D4`) / 50 / 21 | **1** (`D1`) / 48 / 3 |
| `20261104` | **0** (`-`) / 27 / 0 | **3** (`D1,D2,D3`) / 46 / 5 | **3** (`D1,D2,D3`) / 51 / 25 | **1** (`D1`) / 47 / 4 |
| `20261105` | **0** (`-`) / 29 / 0 | **2** (`D1,D2`) / 52 / 6 | **2** (`D1,D2`) / 43 / 25 | **2** (`D1,D2`) / 44 / 13 |
| `20261106` | **0** (`-`) / 27 / 0 | **2** (`D1,D3`) / 56 / 5 | **3** (`D1,D3,D4`) / 53 / 23 | **2** (`D1,D3`) / 55 / 11 |
| `20261107` | **0** (`-`) / 26 / 0 | **2** (`D1,D2`) / 52 / 4 | **3** (`D1,D3,D5`) / 50 / 12 | **1** (`D1`) / 52 / 1 |
| `20261108` | **0** (`-`) / 25 / 0 | **1** (`D1`) / 49 / 3 | **2** (`D1,D4`) / 33 / 6 | **1** (`D1`) / 56 / 9 |
| `20261109` | **0** (`-`) / 29 / 0 | **2** (`D1,D2`) / 55 / 2 | **3** (`D1,D3,D4`) / 55 / 19 | **4** (`D1,D2,D3,D4`) / 58 / 13 |
| `20261110` | **0** (`-`) / 26 / 0 | **2** (`D1,D2`) / 47 / 8 | **2** (`D1,D3`) / 48 / 28 | **3** (`D1,D4,D5`) / 56 / 11 |
| `20261111` | **0** (`-`) / 29 / 0 | **2** (`D1,D4`) / 46 / 4 | **3** (`D1,D2,D3`) / 53 / 19 | **3** (`D1,D2,D3`) / 53 / 12 |
| `20261112` | **0** (`-`) / 27 / 0 | **2** (`D1,D2`) / 53 / 3 | **2** (`D1,D2`) / 38 / 18 | **1** (`D1`) / 53 / 6 |
| `20261113` | **0** (`-`) / 27 / 0 | **1** (`D1`) / 57 / 7 | **3** (`D1,D3,D5`) / 49 / 12 | **4** (`D1,D2,D3,D4`) / 48 / 9 |
| `20261114` | **0** (`-`) / 32 / 0 | **1** (`D1`) / 50 / 1 | **2** (`D1,D3`) / 51 / 18 | **2** (`D1,D2`) / 51 / 7 |
| `20261115` | **0** (`-`) / 25 / 0 | **2** (`D1,D3`) / 57 / 5 | **2** (`D1,D3`) / 49 / 20 | **1** (`D1`) / 47 / 11 |
| `20261116` | **0** (`-`) / 27 / 0 | **3** (`D1,D2,D3`) / 50 / 9 | **1** (`D1`) / 43 / 18 | **2** (`D1,D3`) / 52 / 10 |
| `20261117` | **0** (`-`) / 26 / 0 | **2** (`D1,D2`) / 54 / 3 | **2** (`D1,D3`) / 52 / 19 | **2** (`D1,D3`) / 50 / 10 |
| `20261118` | **0** (`-`) / 25 / 0 | **2** (`D1,D2`) / 54 / 7 | **2** (`D1,D3`) / 45 / 26 | **2** (`D1,D2`) / 46 / 15 |
| `20261119` | **0** (`-`) / 25 / 0 | **1** (`D1`) / 56 / 2 | **2** (`D1,D3`) / 54 / 25 | **2** (`D1,D3`) / 51 / 6 |
| `20261120` | **0** (`-`) / 31 / 0 | **2** (`D1,D2`) / 58 / 3 | **4** (`D1,D3,D4,D5`) / 57 / 19 | **2** (`D1,D4`) / 41 / 8 |
