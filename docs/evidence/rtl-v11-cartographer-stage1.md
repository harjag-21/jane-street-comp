# Stage 1: 2D Adaptive Timing-Surface Cartographer Evaluation

- **Timestamp (UTC):** 2026-11-16T20:05:19.800023+00:00
- **Evidence class:** `rtl_simulation_2d_cartographer_stage1`
- **Axes:** `X = sclk_low_cycles (2..13)` (`20..130 ns`), `Y = gap_wait (0..15)` (`2..17` `CS_N` high cycles), `192`-point space.
- **Matched query budget:** `64` / `192` (`33.3%`), `5` seeds (`[20267001, 20267002, 20267003, 20267004, 20267005]`), `0` RTL changes (`MODE_SPI_SURFACE=10`), finished in `35.61s`.

## Dev (`S0..S2`) vs Held-Out (`S3..S6`) Split Aggregate

| Split | Strategy | Held-Out Grid Acc (%) | Boundary Acc (%) | Failure-Region Recall (%) | Narrow/Island Recall (%) |
|---|---|---|---|---|---|
| Dev (`S0..S2`) | `uniform_grid` | **89.3%** | **67.7%** | 100.0% | 100.0% |
| Dev (`S0..S2`) | `blind_random` | **89.4%** | **69.1%** | 100.0% | 100.0% |
| Dev (`S0..S2`) | `adaptive_cartographer` | **93.1%** | **73.9%** | 93.3% | 80.0% |
| **Held-Out (`S3..S6`)** | `uniform_grid` | **90.0%** | **74.3%** | 100.0% | 100.0% |
| **Held-Out (`S3..S6`)** | `blind_random` | **87.0%** | **65.7%** | 95.0% | 90.0% |
| **Held-Out (`S3..S6`)** | `adaptive_cartographer` | **90.9%** | **70.3%** | 95.0% | 92.5% |

## Per-Surface Breakdown (`64` Queries Each)

| Surface | Role | Regions (Small) | `uniform_grid` (Unseen / Bnd / Reg) | `blind_random` (Unseen / Bnd / Reg) | `adaptive_cartographer` (Unseen / Bnd / Reg) |
|---|---|---|---|---|---|
| `S0_notch_watchdog` | `dev` | `3` (`1`) | 78.1% / 37.8% / `3.0/3` | 90.0% / 74.3% / `3.0/3` | 94.5% / 79.0% / `2.4/3` |
| `S1_bimodal_islands` | `dev` | `3` (`2`) | 93.8% / 78.4% / `3.0/3` | 88.4% / 66.3% / `3.0/3` | 94.8% / 81.3% / `3.0/3` |
| `S2_diagonal_resonance` | `dev` | `3` (`1`) | 96.1% / 86.8% / `3.0/3` | 89.7% / 66.7% / `3.0/3` | 89.8% / 61.5% / `3.0/3` |
| `S3_held_tilted_ridge` | `held_out` | `3` (`0`) | 93.0% / 82.7% / `3.0/3` | 82.5% / 61.3% / `3.0/3` | 86.6% / 65.2% / `3.0/3` |
| `S4_held_twin_pinholes` | `held_out` | `3` (`2`) | 95.3% / 81.2% / `3.0/3` | 90.3% / 63.2% / `2.6/3` | 93.9% / 66.0% / `2.4/3` |
| `S5_held_4zone_core` | `held_out` | `3` (`1`) | 93.8% / 75.8% / `3.0/3` | 90.9% / 67.7% / `2.8/3` | 95.3% / 75.9% / `3.0/3` |
| `S6_held_periodic_comb` | `held_out` | `4` (`3`) | 78.1% / 57.6% / `4.0/4` | 84.4% / 70.5% / `4.0/4` | 88.0% / 74.0% / `4.0/4` |

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
