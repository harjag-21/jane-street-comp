# Stage 1: 2D Adaptive Timing-Surface Cartographer Evaluation

- **Timestamp (UTC):** 2026-11-16T20:04:41.279638+00:00
- **Evidence class:** `rtl_simulation_2d_cartographer_stage1`
- **Axes:** `X = sclk_low_cycles (2..13)` (`20..130 ns`), `Y = gap_wait (0..15)` (`2..17` `CS_N` high cycles), `192`-point space.
- **Matched query budget:** `40` / `192` (`20.8%`), `5` seeds (`[20267001, 20267002, 20267003, 20267004, 20267005]`), `0` RTL changes (`MODE_SPI_SURFACE=10`), finished in `31.52s`.

## Dev (`S0..S2`) vs Held-Out (`S3..S6`) Split Aggregate

| Split | Strategy | Held-Out Grid Acc (%) | Boundary Acc (%) | Failure-Region Recall (%) | Narrow/Island Recall (%) |
|---|---|---|---|---|---|
| Dev (`S0..S2`) | `uniform_grid` | **86.8%** | **61.4%** | 77.8% | 33.3% |
| Dev (`S0..S2`) | `blind_random` | **85.5%** | **60.9%** | 84.4% | 66.7% |
| Dev (`S0..S2`) | `adaptive_cartographer` | **89.9%** | **66.8%** | 77.8% | 33.3% |
| **Held-Out (`S3..S6`)** | `uniform_grid` | **88.0%** | **69.0%** | 100.0% | 100.0% |
| **Held-Out (`S3..S6`)** | `blind_random` | **84.5%** | **62.5%** | 85.0% | 72.5% |
| **Held-Out (`S3..S6`)** | `adaptive_cartographer` | **89.6%** | **70.9%** | 85.4% | 79.2% |

## Per-Surface Breakdown (`40` Queries Each)

| Surface | Role | Regions (Small) | `uniform_grid` (Unseen / Bnd / Reg) | `blind_random` (Unseen / Bnd / Reg) | `adaptive_cartographer` (Unseen / Bnd / Reg) |
|---|---|---|---|---|---|
| `S0_notch_watchdog` | `dev` | `3` (`1`) | 97.4% / 91.8% / `2.0/3` | 85.8% / 65.0% / `2.8/3` | 92.5% / 74.9% / `2.0/3` |
| `S1_bimodal_islands` | `dev` | `3` (`2`) | 77.6% / 39.1% / `3.0/3` | 83.5% / 54.5% / `2.4/3` | 86.8% / 58.7% / `3.0/3` |
| `S2_diagonal_resonance` | `dev` | `3` (`1`) | 85.5% / 53.2% / `2.0/3` | 87.2% / 63.2% / `2.4/3` | 90.3% / 66.8% / `2.0/3` |
| `S3_held_tilted_ridge` | `held_out` | `3` (`0`) | 82.9% / 61.5% / `3.0/3` | 78.4% / 55.0% / `2.8/3` | 87.9% / 72.3% / `3.0/3` |
| `S4_held_twin_pinholes` | `held_out` | `3` (`2`) | 93.4% / 84.2% / `3.0/3` | 89.5% / 63.2% / `2.0/3` | 94.0% / 74.3% / `2.0/3` |
| `S5_held_4zone_core` | `held_out` | `3` (`1`) | 88.8% / 57.5% / `3.0/3` | 89.7% / 67.3% / `2.4/3` | 92.0% / 67.7% / `3.0/3` |
| `S6_held_periodic_comb` | `held_out` | `4` (`3`) | 86.8% / 73.0% / `4.0/4` | 80.5% / 64.5% / `4.0/4` | 84.6% / 69.5% / `3.0/4` |

## Deterministic RTL Replay Verification (`22/22` Regions Matched `5/5`)

| Surface | Region Tag | Size | Sample `(X, Y)` | Replay |
|---|---|---|---|---|
| `S0_notch_watchdog` | `0x55` | 32 | `(2, 0)` | `5/5` |
| `S0_notch_watchdog` | `0xc4` | 30 | `(4, 13)` | `5/5` |
| `S0_notch_watchdog` | `0xee` | 4 | `(7, 6)` | `5/5` |
| `S1_bimodal_islands` | `0x55` | 16 | `(2, 0)` | `5/5` |
| `S1_bimodal_islands` | `0x77` | 6 | `(4, 2)` | `5/5` |
| `S1_bimodal_islands` | `0x88` | 6 | `(9, 11)` | `5/5` |
| `S2_diagonal_resonance` | `0x00` | 42 | `(2, 0)` | `5/5` |
| `S2_diagonal_resonance` | `0xc4` | 48 | `(2, 12)` | `5/5` |
| `S2_diagonal_resonance` | `0xee` | 4 | `(9, 5)` | `5/5` |
| `S3_held_tilted_ridge` | `0x55` | 16 | `(2, 0)` | `5/5` |
| `S3_held_tilted_ridge` | `0x00` | 22 | `(3, 0)` | `5/5` |
| `S3_held_tilted_ridge` | `0xee` | 14 | `(5, 3)` | `5/5` |
| `S4_held_twin_pinholes` | `0x00` | 24 | `(2, 0)` | `5/5` |
| `S4_held_twin_pinholes` | `0x33` | 4 | `(4, 11)` | `5/5` |
| `S4_held_twin_pinholes` | `0x44` | 4 | `(10, 4)` | `5/5` |
| `S5_held_4zone_core` | `0x11` | 28 | `(2, 0)` | `5/5` |
| `S5_held_4zone_core` | `0xee` | 4 | `(8, 8)` | `5/5` |
| `S5_held_4zone_core` | `0x22` | 25 | `(9, 11)` | `5/5` |
| `S6_held_periodic_comb` | `0x55` | 32 | `(2, 0)` | `5/5` |
| `S6_held_periodic_comb` | `0x66` | 8 | `(6, 1)` | `5/5` |
| `S6_held_periodic_comb` | `0x66` | 8 | `(6, 6)` | `5/5` |
| `S6_held_periodic_comb` | `0x66` | 8 | `(6, 11)` | `5/5` |
