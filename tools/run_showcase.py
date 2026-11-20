# SPDX-License-Identifier: Apache-2.0
"""Single repeatable end-to-end ASIC showcase workflow for judges and reviewers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.protocol_asm import (  # noqa: E402
    assemble,
    build_i2c_write_multibyte_program,
    build_spi_2d_program,
    build_spi_mode0_program,
    insn,
    verify_program_safety,
)
from tools.sync_robustness_study import run_sync_robustness_study  # noqa: E402
from tools.timing_explorer import (  # noqa: E402
    IcarusSimTransport,
    ProtocolClient,
    classify_boundary,
    execute_replay,
    replay_records,
    run_spi_boundary_exploration,
    write_jsonl,
)


def run_showcase(trace_out: Path | None = None) -> Dict[str, Any]:
    # 1. Pre-load static safety verifier ("safe-to-run" gate)
    spi1d = build_spi_mode0_program(0x55)
    spi2d = build_spi_2d_program(0x06, 0x05, sclk_low_cycles=None)
    i2c_2b, _ = build_i2c_write_multibyte_program([0xA0, 0x55], half_cycles=None)
    runaway = [insn("OE", 0xFF), insn("OUT", 0x5A), insn("JMP", 0)]
    unsafe_od = assemble(["OE 0x03", "OUT 0x02", "HALT 0"])

    verifies = {
        "spi_1d": verify_program_safety(spi1d, run_param=4),
        "spi_2d": verify_program_safety(spi2d, run_param=4, run_param2=4),
        "i2c_2byte_od": verify_program_safety(i2c_2b, run_param=62, open_drain_pins=(0, 1)),
        "runaway_no_budget": verify_program_safety(runaway, cycle_budget=0),
        "runaway_with_budget": verify_program_safety(runaway, cycle_budget=25),
        "i2c_od_violation": verify_program_safety(unsafe_od, open_drain_pins=(0, 1)),
    }
    assert verifies["spi_1d"]["verdict"] == "SAFE_TO_RUN"
    assert verifies["spi_2d"]["verdict"] == "SAFE_TO_RUN"
    assert verifies["i2c_2byte_od"]["verdict"] == "SAFE_TO_RUN"
    assert verifies["runaway_no_budget"]["verdict"] == "UNSAFE"
    assert verifies["runaway_with_budget"]["verdict"] == "SAFE_WITH_BUDGET"
    assert verifies["i2c_od_violation"]["verdict"] == "UNSAFE"

    with IcarusSimTransport() as transport:
        client = ProtocolClient(transport)
        transport.reset(1)
        ping = client.ping()

        # 2. Load SPI 1D program -> sweep + autonomous 1D scan + save & replay trace
        transport.configure_target(0, delay_ns=37, spi_resp=0xAA)
        client.load_program(spi1d)
        recs = run_spi_boundary_exploration(
            client,
            mosi_byte=0x55,
            expected_miso=0xAA,
            min_low_cycles=2,
            max_low_cycles=10,
            method="sweep",
            delay_ns=37,
        )
        trials = [r for r in recs if r.get("type") == "trial"]
        cls1d = classify_boundary({r["low_cycles"]: bool(r["passed"]) for r in trials})
        auto1d = client.run_autonomous_scan(
            param_start=0,
            param_step=1,
            num_steps=9,
            expected_val=0xAA,
            pass_mode=0,
        )
        assert [r["passed"] for r in trials] == [p["passed"] for p in auto1d["results"]]

        with tempfile.TemporaryDirectory() as tmp:
            tpath = trace_out or (Path(tmp) / "showcase_trace.jsonl")
            tpath.parent.mkdir(parents=True, exist_ok=True)
            write_jsonl(tpath, recs)
            replay_summary = replay_records(recs)
            live_replay = execute_replay(recs, client, repeats=2, only_failures=False)
            assert replay_summary["boundary"]["boundary_low_cycles"] == cls1d["boundary_low_cycles"]
            assert len(live_replay["cases"]) == len(trials)
            assert live_replay["all_deterministic"]

        # 3. Single-load autonomous 2D scan on S0 (12 x 16 = 192 points)
        f0 = client.uart_frames_sent
        client.load_program(spi2d)
        transport.configure_target(10, delay_ns=0, spi_resp=0xAA)
        auto2d = client.run_autonomous_2d_scan(
            x_start=0,
            x_step=1,
            num_x=12,
            y_start=0,
            y_step=1,
            num_y=16,
            expected_val=0xAA,
            pass_mode=0,
        )
        auto2d_frames = client.uart_frames_sent - f0

        # 4. Safe execution cycle-budget guard against infinite runaway loop
        client.load_program(runaway)
        client.set_cycle_budget(25)
        budget_tr = client.run_trial(0)
        client.set_cycle_budget(0)
        assert budget_tr["eng_state"] == "ENG_FAULT"
        assert budget_tr["stop_reason"] == "CYCLE_LIMIT"
        assert budget_tr["cycle_cnt"] == 25

        # 5. Hardware passive input-edge capture (observe 0xAA alternating MISO bits with zero OP_MARK instructions)
        cap_prog = [w for w in build_spi_mode0_program(0x55) if ((w >> 12) & 0xF) != 0x9]
        transport.configure_target(0, delay_ns=0, spi_resp=0xAA)
        client.load_program(cap_prog)
        client.configure_ctrl(sync_mask=0, cap_mask=0x08, cap_mode=3)  # passive edges on MISO (pin 3)
        cap_tr = client.run_trial(2)
        client.configure_ctrl(sync_mask=0, cap_mask=0, cap_mode=0)
        assert cap_tr["eng_state"] == "ENG_HALTED"
        assert len(cap_tr["events"]) >= 6

    # 6. Raw vs 2-FF sync boundary shift study
    sync_rep = run_sync_robustness_study(delays_ns=(25, 37))

    return {
        "ping": ping,
        "safety_verifier": {k: {"verdict": v["verdict"], "words": v["word_count"]} for k, v in verifies.items()},
        "sweep_1d_boundary": cls1d["boundary_low_cycles"],
        "auto_1d_pass_fail": (auto1d["pass_count"], auto1d["fail_count"]),
        "auto_2d_s0": {
            "total_points": auto2d["num_x"] * auto2d["num_y"],
            "total_pass": auto2d["total_pass"],
            "total_fail": auto2d["total_fail"],
            "first_fail": auto2d["first_fail"],
            "uart_frames_including_load": auto2d_frames,
        },
        "cycle_budget_guard": {
            "budget": 25,
            "eng_state": budget_tr["eng_state"],
            "stop_reason": budget_tr["stop_reason"],
            "cycle_cnt": budget_tr["cycle_cnt"],
        },
        "edge_capture_events": cap_tr["events"],
        "sync_shift_rows": sync_rep["spi_boundary_rows"],
        "replay_verified_cases": len(live_replay["cases"]),
    }


def format_markdown(rep: Dict[str, Any]) -> str:
    s0 = rep["auto_2d_s0"]
    bg = rep["cycle_budget_guard"]
    lines = [
        "# Timing Boundary Explorer End-to-End Showcase Walkthrough",
        "",
        "Single automated run executing all pre-load checks, 1D/2D RTL scans, raw vs 2-FF sync boundary checks, runaway budget protection, hardware edge capture, and deterministic trace replay.",
        "",
        "## 1. Pre-Load Static Program Verifier",
        "",
        "| Program | Words | Verdict |",
        "|---|---|---|",
    ]
    for name, info in rep["safety_verifier"].items():
        lines.append(f"| `{name}` | {info['words']} | `{info['verdict']}` |")
    lines.extend(
        [
            "",
            "## 2. Live RTL Measurements",
            "",
            f"- **Chip Info (`OP_PING_INFO`):** `prog_depth={rep['ping']['prog_depth']}`, `event_depth={rep['ping']['event_depth']}`, `feature_flags=0x{rep['ping']['feature_flags']:x}`",
            f"- **1D SPI Boundary + Replay:** `low_cycles >= {rep['sweep_1d_boundary']}` (`1D scan={rep['auto_1d_pass_fail'][0]} pass / {rep['auto_1d_pass_fail'][1]} fail`, replayed `{rep['replay_verified_cases']}` cases with `0` mismatches)",
            f"- **Single-Load Autonomous 2D Scan (`S0`, 12x16 = {s0['total_points']} pts):** `{s0['total_pass']}` pass / `{s0['total_fail']}` fail in `{s0['uart_frames_including_load']}` total UART frames (program loaded once)",
            f"- **Cycle-Budget Guard:** runaway infinite loop stopped at cycle `{bg['cycle_cnt']}` in `{bg['eng_state']}` (`stop_reason={bg['stop_reason']}`)",
            f"- **Passive Input-Edge Capture:** `{len(rep['edge_capture_events'])}` pin transitions captured in hardware FIFO without `OP_MARK` instructions: `{rep['edge_capture_events']}`",
            "",
            "## 3. Raw vs 2-FF Synchronizer Boundary Shift",
            "",
            "| Delay (ns) | Mode | `sync_mask` | Min Passing `low_cycles` |",
            "|---|---|---|---|",
        ]
    )
    for row in rep["sync_shift_rows"]:
        lines.append(
            f"| {row['delay_ns']} | `{row['mode']}` | `{row['sync_mask']}` | {row['boundary_low_cycles']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run single end-to-end ASIC showcase workflow")
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--trace-out", type=Path)
    args = parser.parse_args()
    rep = run_showcase(trace_out=args.trace_out)
    md = format_markdown(rep)
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
