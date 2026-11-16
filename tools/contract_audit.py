# SPDX-License-Identifier: Apache-2.0
"""Verify dynamic timing-contract checker (JZ, LOOP, WAIT_EDGE, timeouts) on RTL."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.protocol_asm import (  # noqa: E402
    analyze_timing_bounds,
    assemble,
    build_i2c_stretch_probe_program,
    build_i2c_write_byte_program,
    check_timing_contract,
    insn,
)
from tools.timing_explorer import IcarusSimTransport, ProtocolClient, run_i2c_stretch_sweep  # noqa: E402


def run_contract_verification() -> Dict[str, Any]:
    cases: List[Dict[str, Any]] = []

    unbounded = analyze_timing_bounds(assemble(["loop_inf:", "NOP", "JMP loop_inf"]), 0)
    cases.append(
        {
            "name": "unbounded_jmp_loop_detection",
            "category": "static_safety",
            "expected_pass": False,
            "contract_ok": bool(not unbounded["bounded"]),
            "details": f"bounded={unbounded['bounded']}, unbounded_pcs={unbounded['unbounded_pcs']}",
        }
    )

    with IcarusSimTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)

        # JZ ACK vs NACK
        i2c_prog = build_i2c_write_byte_program(0xA0, half_cycles=4, ack_tag=0xAC, nack_tag=0xDE)
        client.load_program(i2c_prog)
        for name, ack_val, s_bit in [("i2c_jz_ack_path", 1, 0), ("i2c_jz_nack_path", 0, 1)]:
            transport.configure_target(2, i2c_ack=ack_val)
            res = client.run_trial(0)
            chk = check_timing_contract(
                i2c_prog,
                0,
                observed_cycles=res["cycle_cnt"],
                observed_events=res["events"],
                eng_state=res["eng_state"],
                sample_bits=[s_bit],
            )
            cases.append(
                {
                    "name": name,
                    "category": "jz_branch",
                    "observed_cycles": res["cycle_cnt"],
                    "bounds": [chk["bounds"]["min_cycles"], chk["bounds"]["max_cycles"]],
                    "contract_ok": chk["contract_ok"],
                    "errors": chk["errors"],
                }
            )
            if name == "i2c_jz_ack_path":
                bad = check_timing_contract(
                    i2c_prog,
                    0,
                    observed_cycles=res["cycle_cnt"] + 1,
                    observed_events=res["events"],
                    eng_state=res["eng_state"],
                    sample_bits=[s_bit],
                )
                cases.append(
                    {
                        "name": "negative_off_by_one_cycle_rejected",
                        "category": "negative_check",
                        "observed_cycles": res["cycle_cnt"] + 1,
                        "bounds": [bad["bounds"]["min_cycles"], bad["bounds"]["max_cycles"]],
                        "contract_ok": not bad["contract_ok"],
                        "errors": bad["errors"],
                    }
                )

        # SETCOUNT / LOOP
        for count in (0, 1, 5):
            prog = assemble(
                [
                    f"SETCOUNT 0, {count}",
                    "lp:",
                    "WAIT_PARAM 1",
                    "MARK 0x33, 0",
                    "LOOP 0, lp",
                    "HALT 0",
                ]
            )
            client.load_program(prog)
            res = client.run_trial(2)
            chk = check_timing_contract(
                prog,
                2,
                observed_cycles=res["cycle_cnt"],
                observed_events=res["events"],
                eng_state=res["eng_state"],
                sample_bits=(),
                edge_waits=(),
            )
            cases.append(
                {
                    "name": f"loop_setcount_n{count}",
                    "category": "loop_counter",
                    "observed_cycles": res["cycle_cnt"],
                    "bounds": [chk["bounds"]["min_cycles"], chk["bounds"]["max_cycles"]],
                    "contract_ok": chk["contract_ok"],
                    "errors": chk["errors"],
                }
            )

        # WAIT_EDGE timeout, exact hit, delayed hit, and immediate hit
        for label, stretch_c, waits in [
            ("wait_edge_delayed_and_timeout", 7, (4, 7, 10)),
            ("wait_edge_immediate_hit", 0, (0, 3)),
        ]:
            sw = run_i2c_stretch_sweep(client, stretch_cycles=stretch_c, max_wait_values=waits)
            for tr in sw["trials"]:
                outcome = "timeout" if not tr["passed"] else f"release@{tr['measured_release_delta_cycles']}"
                cases.append(
                    {
                        "name": f"{label}_mw{tr['max_wait']}_{outcome}",
                        "category": "wait_edge",
                        "observed_cycles": tr["cycle_cnt"],
                        "bounds": tr["contract_bounds"],
                        "contract_ok": tr["contract_ok"],
                        "errors": tr["contract_errors"],
                    }
                )

        # Negative test on WAIT_EDGE outcome mismatch
        probe = build_i2c_stretch_probe_program(scl_pin=0, sda_pin=1, half_cycles=4)
        client.load_program(probe)
        transport.configure_target(4, delay_ns=2)
        res_pass = client.run_trial(5)
        bad_timeout = check_timing_contract(
            probe,
            5,
            observed_cycles=res_pass["cycle_cnt"],
            observed_events=res_pass["events"],
            eng_state=res_pass["eng_state"],
            edge_waits=[None],  # false timeout expectation
        )
        cases.append(
            {
                "name": "negative_wrong_wait_edge_timeout_rejected",
                "category": "negative_check",
                "observed_cycles": res_pass["cycle_cnt"],
                "bounds": [bad_timeout["bounds"]["min_cycles"], bad_timeout["bounds"]["max_cycles"]],
                "contract_ok": not bad_timeout["contract_ok"],
                "errors": bad_timeout["errors"],
            }
        )

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "rtl_simulation_timing_contract_audit",
        "all_checks_passed": all(c["contract_ok"] for c in cases),
        "cases": cases,
    }


def format_contract_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Dynamic Timing-Contract Verification Report",
        "",
        f"- **Timestamp (UTC):** {report['timestamp_utc']}",
        f"- **Evidence class:** `{report['evidence_class']}`",
        f"- **Checks passed:** `{sum(1 for c in report['cases'] if c['contract_ok'])}/{len(report['cases'])}`",
        "",
        "Supporting verification extending static bounds (`analyze_timing_bounds`) and exact trace checks (`check_timing_contract`) over `JZ`, `JMP`, `SETCOUNT`/`LOOP`, and `WAIT_EDGE` (hits and timeouts).",
        "",
        "| Case | Category | Observed Cycles | Static Bounds `[min, max]` | Verdict | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for c in report["cases"]:
        b = c.get("bounds")
        b_str = f"`[{b[0]}, {b[1]}]`" if b else "`unbounded`"
        obs = str(c.get("observed_cycles", "-"))
        verdict = "PASS" if c["contract_ok"] else "FAIL"
        notes = c.get("details") or ("; ".join(c["errors"]) if c.get("errors") else "exact match")
        lines.append(f"| `{c['name']}` | `{c['category']}` | {obs} | {b_str} | {verdict} | {notes} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify dynamic timing contracts against RTL")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args()

    rep = run_contract_verification()
    md = format_contract_markdown(rep)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rep, indent=2, sort_keys=True), encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    print(md)
    return 0 if rep["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
