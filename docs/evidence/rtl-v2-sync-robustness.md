# Raw vs 2-Flop Synchronized Input Robustness Study

Measured live `tt_um_harjag_21_timing_explorer` RTL at `100 MHz` (`10 ns` period).

| Target Delay (ns) | Mode | `sync_mask` | Boundary Status | Min Passing `low_cycles` |
|---|---|---|---|---|
| 25 | `raw_bypass` | `0x00` | `bracketed` | 3 |
| 25 | `per_pin_miso_sync` | `0x08` | `bracketed` | 5 |
| 25 | `global_all_pins_sync` | `0xff` | `bracketed` | 5 |
| 37 | `raw_bypass` | `0x00` | `bracketed` | 4 |
| 37 | `per_pin_miso_sync` | `0x08` | `bracketed` | 6 |
| 37 | `global_all_pins_sync` | `0xff` | `bracketed` | 6 |
| 50 | `raw_bypass` | `0x00` | `bracketed` | 6 |
| 50 | `per_pin_miso_sync` | `0x08` | `bracketed` | 8 |
| 50 | `global_all_pins_sync` | `0xff` | `bracketed` | 8 |

- **Clock-Stretch `WAIT_EDGE` Latency Shift:** raw `MARK ts=12` vs synced `MARK ts=14` (exact **+2 cycles**).
- **Design Choice:** Per-pin 8-bit `cfg_sync_mask` (reset default 0x00 = raw bypass) supports both zero-latency timing exploration and selective/global 2-FF input synchronization without distorting unmasked experiments.
- **Scope Caveat:** RTL simulation verifies exact +2-cycle pipeline latency and boundary shift; analog metastability MTBF is not measurable in digital RTL simulation.
