# Phase 4 SPI Target-Responder Operating Limit Characterization

Measured `build_spi_target_responder_program(0x5A, max_wait=0)` (`40` words, SPI **Mode 0** only, `run_param=60`) on `tt_um_harjag_21_timing_explorer` against an external controller sending `MOSI = 0xA5`.

| Case | Group | Ext MISO (`exp 0x5a`) | Resp MOSI (`exp 0xa5`) | Event Tag | Cycles | Outcome |
|---|---|---|---|---|---|---|
| half_clks=1 (too fast) | `half_period` | `0x0e` | `0xff` | `0xff` | 345 | MISFRAMED / FAIL |
| half_clks=2 (too fast) | `half_period` | `0x2d` | `0x4b` | `0x4b` | 41 | MISFRAMED / FAIL |
| half_clks=3 (below min=4) | `half_period` | `0x5a` | `0xa5` | `0xa5` | 56 | EXACT PASS |
| half_clks=4 (stated min) | `half_period` | `0x5a` | `0xa5` | `0xa5` | 72 | EXACT PASS |
| half_clks=5 | `half_period` | `0x5a` | `0xa5` | `0xa5` | 88 | EXACT PASS |
| half_clks=6 | `half_period` | `0x5a` | `0xa5` | `0xa5` | 104 | EXACT PASS |
| half_clks=8 | `half_period` | `0x5a` | `0xa5` | `0xa5` | 136 | EXACT PASS |
| half_clks=12 | `half_period` | `0x5a` | `0xa5` | `0xa5` | 200 | EXACT PASS |
| asymmetric hi=3, lo=5 | `asymmetry_jitter` | `0x5a` | `0xa5` | `0xa5` | 72 | EXACT PASS |
| asymmetric hi=4, lo=3 | `asymmetry_jitter` | `0x5a` | `0xa5` | `0xa5` | 64 | EXACT PASS |
| asymmetric hi=4, lo=5 | `asymmetry_jitter` | `0x5a` | `0xa5` | `0xa5` | 80 | EXACT PASS |
| jittered nominal hi=5+/-1, lo=5-/+1 | `asymmetry_jitter` | `0x5a` | `0xa5` | `0xa5` | 87 | EXACT PASS |
| cs_delay=55 (< timeout 60) | `timeout` | `0x5a` | `0xa5` | `0xa5` | 157 | EXACT PASS |
| cs_delay=80 (> timeout 60) | `timeout` | `0x00` | `0x00` | `0xee` | 66 | CS TIMEOUT (0xEE) |
| shortened 4-bit transfer | `truncated` | `0x05` | `0xa0` | `0xa0` | 312 | MISFRAMED / FAIL |
| CS_N 2-cycle glitch + short 12-cyc gap (< timeout 60) | `glitch` | `0x5a` | `0xa5` | `0xa5` | 116 | EXACT PASS |
| CS_N 2-cycle glitch + long 70-cyc gap (> timeout 60) | `glitch` | `0xb4` | `0x52` | `0x52` | 162 | MISFRAMED / FAIL |
| CS_N 2-cycle glitch then shared-bus SCLK w/ CS_N=1 | `glitch` | `0x5a` | `0x3c` | `0x3c` | 116 | SPURIOUS SCLK CAPTURE (CS_N=1) |
