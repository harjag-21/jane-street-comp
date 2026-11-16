# Coverage-Preserving Hybrid Cartographer Evaluation (`S7..S12` New Held-Out Set)

- **Timestamp (UTC):** 2026-11-16T20:12:01.386116+00:00
- **Evidence class:** `rtl_simulation_2d_hybrid_cartographer_heldout`
- **Matched budget:** `64` / `192` (`33.3%`), `5` seeds (`[20268001, 20268002, 20268003, 20268004, 20268005]`), finished in `20.75s`.
- **Pre-declared PoC Gate:** **FAIL** (retains region/island recall: `True`, boundary gain vs `uniform_grid`: `-0.46 pp` [threshold `>= +5.0 pp` & beats `adaptive`], 5/5 RTL replay: `True`)

## Aggregate Comparison on `S7..S12` (New Held-Out Surfaces)

| Strategy | Held-Out Grid Acc | Boundary Acc | Failure-Region Recall | Narrow-Island Recall |
|---|---|---|---|---|
| `uniform_grid` | **90.8%** | **71.1%** | 95.8% | 95.8% |
| `blind_random` | **88.3%** | **65.6%** | 97.8% | 95.6% |
| `adaptive_cartographer` | **91.5%** | **69.1%** | 100.0% | 100.0% |
| `hybrid_mapper` | **92.7%** | **70.7%** | 95.8% | 95.8% |

## Per-Surface Results (`64` Probes Each, Unseen / Boundary / Regions)

| Surface | Regions (Small) | `uniform_grid` | `blind_random` | `adaptive_cartographer` | `hybrid_mapper` |
|---|---|---|---|---|---|
| `S7_new_odd_pinholes` | `3` (`3`) | 85.9% / 43.8% / `3.0/3` | 89.1% / 56.1% / `2.8/3` | 90.9% / 55.7% / `3.0/3` | 89.7% / 38.9% / `3.0/3` |
| `S8_new_curved_circle` | `2` (`0`) | 85.9% / 61.7% / `2.0/2` | 90.2% / 72.6% / `2.0/2` | 94.4% / 81.9% / `2.0/2` | 96.6% / 88.5% / `2.0/2` |
| `S9_new_lshape_and_core` | `3` (`1`) | 96.9% / 88.6% / `3.0/3` | 93.6% / 77.1% / `2.8/3` | 93.6% / 70.2% / `3.0/3` | 98.4% / 92.0% / `3.0/3` |
| `S10_new_antidiag_ridge` | `2` (`0`) | 94.5% / 83.3% / `2.0/2` | 85.3% / 58.9% / `2.0/2` | 95.5% / 86.4% / `2.0/2` | 95.3% / 83.3% / `2.0/2` |
| `S11_new_3band_step` | `3` (`0`) | 96.9% / 91.8% / `3.0/3` | 86.9% / 68.2% / `3.0/3` | 85.5% / 55.1% / `3.0/3` | 90.6% / 69.2% / `3.0/3` |
| `S12_new_tri_islands` | `4` (`4`) | 84.4% / 57.5% / `3.0/4` | 85.0% / 60.7% / `4.0/4` | 88.9% / 65.4% / `4.0/4` | 85.3% / 51.9% / `3.0/4` |

## Live RTL Replay Verification (`17/17` Regions Matched `5/5`)

| Surface | Region Tag | Size | Sample `(X, Y)` | Replay |
|---|---|---|---|---|
| `S7_new_odd_pinholes` | `0x00` | 12 | `(2, 0)` | `5/5` |
| `S7_new_odd_pinholes` | `0x33` | 4 | `(5, 3)` | `5/5` |
| `S7_new_odd_pinholes` | `0x44` | 4 | `(9, 9)` | `5/5` |
| `S8_new_curved_circle` | `0x55` | 32 | `(2, 0)` | `5/5` |
| `S8_new_curved_circle` | `0xee` | 29 | `(5, 8)` | `5/5` |
| `S9_new_lshape_and_core` | `0x77` | 28 | `(2, 9)` | `5/5` |
| `S9_new_lshape_and_core` | `0xee` | 4 | `(6, 6)` | `5/5` |
| `S9_new_lshape_and_core` | `0x88` | 30 | `(9, 0)` | `5/5` |
| `S10_new_antidiag_ridge` | `0x55` | 16 | `(2, 0)` | `5/5` |
| `S10_new_antidiag_ridge` | `0xc4` | 24 | `(4, 10)` | `5/5` |
| `S11_new_3band_step` | `0x11` | 15 | `(2, 0)` | `5/5` |
| `S11_new_3band_step` | `0x22` | 36 | `(2, 13)` | `5/5` |
| `S11_new_3band_step` | `0x33` | 21 | `(7, 5)` | `5/5` |
| `S12_new_tri_islands` | `0xc4` | 12 | `(2, 15)` | `5/5` |
| `S12_new_tri_islands` | `0x33` | 6 | `(3, 11)` | `5/5` |
| `S12_new_tri_islands` | `0x66` | 6 | `(6, 2)` | `5/5` |
| `S12_new_tri_islands` | `0x44` | 6 | `(10, 10)` | `5/5` |
