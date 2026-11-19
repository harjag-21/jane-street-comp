# Phase 4 I2C Pulse-by-Pulse Timing Audit (50 MHz Core Clock)

Audited real `tt_um_harjag_21_timing_explorer` RTL at `clock_hz = 50,000,000` (`T = 20 ns`) under idealized `0 ns` pull-up rise time across `START`, bits `7..0`, `ACK/NACK`, and `STOP` (`(uio_oe & uio_out) == 0` verified at every cycle).

| Profile | `half_cycles` | ACK | Rx | `t_BUF` (ns) | `t_HD;STA` (ns) | Min `t_LOW` (ns) | Min `t_HIGH` (ns) | Min `t_SU;DAT` (ns) | Min `t_HD;DAT` (ns) | `t_SU;STO` (ns) | Steady Rate (kHz) | Spec Result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `i2c-400k-nominal` | 62 | 1 | `0xa0` | 1300 | 1240 | 1260 | 1240 | 1240 | 20 | 1240 | 400.000 | FAIL (bit_6 t_LOW 1260ns < 1300.0ns; bit_5 t_LOW 1260ns < 1300.0ns; bit_4 t_LOW 1260ns < 1300.0ns; bit_3 t_LOW 1260ns < 1300.0ns; bit_2 t_LOW 1260ns < 1300.0ns; bit_1 t_LOW 1260ns < 1300.0ns; bit_0 t_LOW 1260ns < 1300.0ns; ACK/NACK t_LOW 1260ns < 1300.0ns) |
| `i2c-400k-nominal-nack` | 62 | 0 | `0xa0` | 1300 | 1240 | 1260 | 1240 | 1240 | 0 | 1240 | 400.000 | FAIL (bit_6 t_LOW 1260ns < 1300.0ns; bit_5 t_LOW 1260ns < 1300.0ns; bit_4 t_LOW 1260ns < 1300.0ns; bit_3 t_LOW 1260ns < 1300.0ns; bit_2 t_LOW 1260ns < 1300.0ns; bit_1 t_LOW 1260ns < 1300.0ns; bit_0 t_LOW 1260ns < 1300.0ns; ACK/NACK t_LOW 1260ns < 1300.0ns) |
| `i2c-fast-compliant` | 64 | 1 | `0xa0` | 1340 | 1280 | 1300 | 1280 | 1280 | 20 | 1280 | 387.597 | PASS |
| `i2c-fast-compliant-nack` | 64 | 0 | `0xa0` | 1340 | 1280 | 1300 | 1280 | 1280 | 0 | 1280 | 387.597 | PASS |
| `i2c-100k` | 250 | 1 | `0xa0` | 5060 | 5000 | 5020 | 5000 | 5000 | 20 | 5000 | 99.800 | PASS |
| `i2c-100k-nack` | 250 | 0 | `0xa0` | 5060 | 5000 | 5020 | 5000 | 5000 | 0 | 5000 | 99.800 | PASS |
| `i2c-2byte-od-fast` | 64 | 1 | `0xa0,0x55` | 1340 | 1280 | 1320 | 1280 | 1280 | 20 | 1280 | 384.615 | PASS |
| `i2c-2byte-od-fast-nack` | 64 | 0 | `0xa0` | 1340 | 1280 | 1320 | 1280 | 1280 | 0 | 1280 | 384.615 | PASS |
| `i2c-2byte-od-100k` | 250 | 1 | `0xa0,0x55` | 5060 | 5000 | 5040 | 5000 | 5000 | 20 | 5000 | 99.602 | PASS |
| `i2c-2byte-od-100k-nack` | 250 | 0 | `0xa0` | 5060 | 5000 | 5040 | 5000 | 5000 | 0 | 5000 | 99.602 | PASS |

## Per-Pulse Detail (`i2c-fast-compliant`, `half_cycles=64`, ACK)

| Pulse | Role | `t_LOW` (ns) | `t_HIGH` (ns) | Period (ns) | Freq (kHz) | `t_SU;DAT` (ns) | `t_HD;DAT` (ns) |
|---|---|---|---|---|---|---|---|
| 0 | `bit_7` | 2560 | 1280 | 3840 | 260.417 | 1280 | 20 |
| 1 | `bit_6` | 1300 | 1280 | 2580 | 387.597 | 1280 | 20 |
| 2 | `bit_5` | 1300 | 1280 | 2580 | 387.597 | 1280 | 20 |
| 3 | `bit_4` | 1300 | 1280 | 2580 | 387.597 | 1280 | 15520 |
| 4 | `bit_3` | 1300 | 1280 | 2580 | 387.597 | 3860 | 12940 |
| 5 | `bit_2` | 1300 | 1280 | 2580 | 387.597 | 6440 | 10360 |
| 6 | `bit_1` | 1300 | 1280 | 2580 | 387.597 | 9020 | 7780 |
| 7 | `bit_0` | 1300 | 1280 | 2580 | 387.597 | 11600 | 5200 |
| 8 | `ACK/NACK` | 1300 | 1300 | 2600 | 384.615 | 14180 | 2600 |
