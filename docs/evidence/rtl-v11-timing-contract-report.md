# Dynamic Timing-Contract Verification Report

- **Timestamp (UTC):** 2026-11-16T16:37:48.416485+00:00
- **Evidence class:** `rtl_simulation_timing_contract_audit`
- **Checks passed:** `13/13`

Supporting verification extending static bounds (`analyze_timing_bounds`) and exact trace checks (`check_timing_contract`) over `JZ`, `JMP`, `SETCOUNT`/`LOOP`, and `WAIT_EDGE` (hits and timeouts).

| Case | Category | Observed Cycles | Static Bounds `[min, max]` | Verdict | Notes |
|---|---|---|---|---|---|
| `unbounded_jmp_loop_detection` | `static_safety` | - | `unbounded` | PASS | bounded=False, unbounded_pcs=[0] |
| `i2c_jz_ack_path` | `jz_branch` | 106 | `[106, 107]` | PASS | exact match |
| `negative_off_by_one_cycle_rejected` | `negative_check` | 107 | `[106, 107]` | PASS | Observed cycle_cnt=107 != predicted=106 |
| `i2c_jz_nack_path` | `jz_branch` | 107 | `[106, 107]` | PASS | exact match |
| `loop_setcount_n0` | `loop_counter` | 8 | `[8, 8]` | PASS | exact match |
| `loop_setcount_n1` | `loop_counter` | 8 | `[8, 8]` | PASS | exact match |
| `loop_setcount_n5` | `loop_counter` | 32 | `[32, 32]` | PASS | exact match |
| `wait_edge_delayed_and_timeout_mw4_timeout` | `wait_edge` | 15 | `[12, 16]` | PASS | exact match |
| `wait_edge_delayed_and_timeout_mw7_release@11` | `wait_edge` | 19 | `[12, 19]` | PASS | exact match |
| `wait_edge_delayed_and_timeout_mw10_release@11` | `wait_edge` | 19 | `[12, 22]` | PASS | exact match |
| `wait_edge_immediate_hit_mw0_release@4` | `wait_edge` | 12 | `[11, 12]` | PASS | exact match |
| `wait_edge_immediate_hit_mw3_release@4` | `wait_edge` | 12 | `[12, 15]` | PASS | exact match |
| `negative_wrong_wait_edge_timeout_rejected` | `negative_check` | 14 | `[12, 17]` | PASS | Observed cycle_cnt=14 != predicted=16; Event 1 ts_cycles=11 != predicted=14 |
