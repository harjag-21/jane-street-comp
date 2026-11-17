# SPDX-License-Identifier: Apache-2.0
"""Benchmark comparing host-driven timing sweeps vs the on-chip autonomous scan controller."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.cartographer import X_VALS, Y_VALS, probe_rtl_surface_point  # noqa: E402
from tools.protocol_asm import build_spi_2d_program, build_spi_mode0_program  # noqa: E402
from tools.timing_explorer import IcarusSimTransport, ProtocolClient  # noqa: E402


def benchmark_1d_spi_sweep(num_steps: int = 32, delay_ns: int = 37) -> Dict[str, Any]:
    """Compare a 32-point 1D SPI delay sweep driven point-by-point by host vs autonomous scan."""
    prog = build_spi_mode0_program(0x55)
    with IcarusSimTransport() as transport:
        client = ProtocolClient(transport)
        transport.reset(1)
        client.load_program(prog)
        transport.configure_target(0, delay_ns=delay_ns, spi_resp=0xAA)

        # 1. Host-driven point-by-point sweep
        t0 = time.perf_counter()
        f0 = client.uart_frames_sent
        host_pass: List[bool] = []
        host_first_fail: Dict[str, Any] = {"valid": False}
        for idx in range(num_steps):
            param = idx
            tr = client.run_trial(param)
            ok = (tr["eng_state"] == "ENG_HALTED") and (tr["sample_reg"] == 0xAA)
            host_pass.append(ok)
            if (not ok) and (not host_first_fail["valid"]):
                host_first_fail = {
                    "valid": True,
                    "fail_idx": idx,
                    "fail_param": param,
                    "eng_state": tr["eng_state"],
                    "sample_reg": tr["sample_reg"],
                    "cycle_cnt": tr["cycle_cnt"],
                }
        host_frames = client.uart_frames_sent - f0
        host_wall_s = time.perf_counter() - t0

        # 2. On-chip autonomous scan (same program already loaded)
        t1 = time.perf_counter()
        f1 = client.uart_frames_sent
        auto = client.run_autonomous_scan(
            param_start=0,
            param_step=1,
            num_steps=num_steps,
            expected_val=0xAA,
            pass_mode=0,
        )
        auto_frames = client.uart_frames_sent - f1
        auto_wall_s = time.perf_counter() - t1

    auto_pass = [bool(pt["passed"]) for pt in auto["results"]]
    ff_match = (
        auto["first_fail"]["valid"] == host_first_fail["valid"]
        and auto["first_fail"]["fail_idx"] == host_first_fail["fail_idx"]
        and auto["first_fail"]["fail_param"] == host_first_fail["fail_param"]
        and auto["first_fail"]["sample_reg"] == host_first_fail["sample_reg"]
        and auto["first_fail"]["cycle_cnt"] == host_first_fail["cycle_cnt"]
    )
    return {
        "benchmark": "1d_spi_32pt",
        "num_steps": num_steps,
        "delay_ns": delay_ns,
        "exact_bitmap_match": (auto_pass == host_pass),
        "exact_first_fail_match": ff_match,
        "pass_count": auto["pass_count"],
        "fail_count": auto["fail_count"],
        "first_fail": auto["first_fail"],
        "host_uart_frames": host_frames,
        "auto_uart_frames": auto_frames,
        "uart_frame_reduction": round(host_frames / max(1, auto_frames), 2),
        "host_wall_s": round(host_wall_s, 4),
        "auto_wall_s": round(auto_wall_s, 4),
    }


def benchmark_2d_surface_uniform_scan(surface_id: int = 0) -> Dict[str, Any]:
    """Compare full 12x16=192-pt 2D uniform surface discovery: host per-point vs 12 column autonomous scans."""
    with IcarusSimTransport() as transport:
        client = ProtocolClient(transport)
        transport.reset(1)
        # Configure surface target before running
        host_grid: Dict[Tuple[int, int], bool] = {}
        t0 = time.perf_counter()
        f0 = client.uart_frames_sent
        for x in X_VALS:
            for y in Y_VALS:
                res = probe_rtl_surface_point(transport, client, surface_id, x, y)
                host_grid[(x, y)] = (res["eng_state"] == "ENG_HALTED") and (res["sample_reg"] == 0xAA)
        host_frames = client.uart_frames_sent - f0
        host_wall_s = time.perf_counter() - t0

        # Reset program cache so autonomous scan measures clean cold-to-complete cost fairly
        client.invalidate_cache()
        transport.reset(1)
        auto_grid: Dict[Tuple[int, int], bool] = {}
        t1 = time.perf_counter()
        f1 = client.uart_frames_sent
        for x in X_VALS:
            words = build_spi_2d_program(0x06, 0x05, sclk_low_cycles=x)
            client.sync_program(words)
            transport.configure_target(10, delay_ns=surface_id)
            auto_col = client.run_autonomous_scan(
                param_start=0,
                param_step=1,
                num_steps=len(Y_VALS),
                expected_val=0xAA,
                pass_mode=0,
            )
            for y, ok in zip(Y_VALS, [bool(pt["passed"]) for pt in auto_col["results"]]):
                auto_grid[(x, y)] = ok
        auto_frames = client.uart_frames_sent - f1
        auto_wall_s = time.perf_counter() - t1

    mismatches = [
        (x, y, host_grid[(x, y)], auto_grid[(x, y)])
        for x in X_VALS
        for y in Y_VALS
        if host_grid[(x, y)] != auto_grid[(x, y)]
    ]
    return {
        "benchmark": f"2d_uniform_surface_S{surface_id}",
        "total_points": len(X_VALS) * len(Y_VALS),
        "exact_grid_match": (len(mismatches) == 0),
        "mismatch_count": len(mismatches),
        "pass_points": sum(1 for v in auto_grid.values() if v),
        "fail_points": sum(1 for v in auto_grid.values() if not v),
        "host_uart_frames": host_frames,
        "auto_uart_frames": auto_frames,
        "uart_frame_reduction": round(host_frames / max(1, auto_frames), 2),
        "host_wall_s": round(host_wall_s, 4),
        "auto_wall_s": round(auto_wall_s, 4),
    }


def main() -> int:
    results = {
        "1d_spi_32pt": benchmark_1d_spi_sweep(num_steps=32, delay_ns=37),
        "2d_surfaces": [benchmark_2d_surface_uniform_scan(sid) for sid in (0, 4, 8)],
    }
    print(json.dumps(results, indent=2))
    assert results["1d_spi_32pt"]["exact_bitmap_match"]
    assert results["1d_spi_32pt"]["exact_first_fail_match"]
    for surf in results["2d_surfaces"]:
        assert surf["exact_grid_match"], f"Mismatch on {surf['benchmark']}"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
