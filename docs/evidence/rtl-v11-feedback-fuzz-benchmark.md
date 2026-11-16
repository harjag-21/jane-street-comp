# Multi-Seed Black-Box Feedback-Guided Fuzzing Benchmark

- **Timestamp (UTC):** 2026-11-16T16:54:43.478434+00:00
- **Evidence class:** `rtl_simulation_feedback_fuzz_benchmark`
- **Target:** `MODE_SPI_MULTISPEC` Reference (`delay_ns=0`) vs Buggy DUT (`delay_ns=1`), 4 distinct timing+state bugs (`D1..D4`), 8 SPI opcodes, inter-CS gap `WAIT_PARAM in 0..15`
- **Design rule:** Strict black-box feedback using only pin responses (`ref_bytes`, `dut_bytes`, `cycle_cnt // 8`, `diverged`) over real UART `OP_PATCH_WORD`/`OP_START` frames
- **Scale:** `100` trials/seed x `5` seeds (`[20261101, 20261102, 20261103, 20261104, 20261105]`) in `664.94s`

## Strategy Comparison (Matched Trial Budget)

| Strategy | Mean Unique Bugs (`0..4`) | Range `[min, max]` | Seeds Finding `4/4` | Mean Obs. Pin Signatures | Mean Raw Divergences |
|---|---|---|---|---|---|
| `payload_only` | **0.00** | `[0, 0]` | `0/5` | 27.2 | 0.0 |
| `joint_random` | **2.00** | `[1, 3]` | `0/5` | 49.6 | 4.6 |
| `guided_feedback` | **2.40** | `[2, 3]` | `0/5` | 48.8 | 21.2 |

## Per-Bug Discovery Rate & Mean Trials-to-Discover (TTD)

| Bug Class | `payload_only` Hit Rate | `joint_random` Hit Rate (Mean TTD) | `guided_feedback` Hit Rate (Mean TTD) |
|---|---|---|---|
| `D1_wren_fast_drop` | `0%` | `100%` (17.2) | `100%` (14.6) |
| `D2_wakeup_slow_recovery` | `0%` | `60%` (50.3) | `40%` (18) |
| `D3_prog_wren_timeout` | `0%` | `20%` (89) | `80%` (41) |
| `D4_fast_read_corrupt` | `0%` | `20%` (58) | `20%` (86) |

## Minimized & Replayed Counterexamples

| Bug Class | Discovered Sequence (`gap_wait`) | Minimized Sequence | Window Failing/Passing | Replay Rate |
|---|---|---|---|---|
| `D1_wren_fast_drop` | `0x04 -> 0x06 -> 0x9f` (`gap=1`) | `0x06 -> 0x9f` | gap `1` (fails `<= 2`, passes `>= 3`) | `5/5` |
| `D2_wakeup_slow_recovery` | `0xb9 -> 0xab -> 0x9f` (`gap=0`) | `0xb9 -> 0xab -> 0x9f` | gap `0` (fails `<= 6`, passes `>= 7`) | `5/5` |
| `D3_prog_wren_timeout` | `0x06 -> 0x02 -> 0x05` (`gap=10`) | `0x06 -> 0x02 -> 0x05` | gap `10` (passes `<= 9`, fails `>= 10`) | `5/5` |
| `D4_fast_read_corrupt` | `0x9f -> 0x35 -> 0x04` (`gap=0`) | `0x9f -> 0x35 -> 0x04` | gap `0` (fails `<= 1`, passes `>= 2`) | `5/5` |
