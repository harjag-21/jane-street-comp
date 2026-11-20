# SPDX-License-Identifier: Apache-2.0
"""Raw vs 2-Flop Synchronized Input Robustness & Latency-Shift Study.

Measures the exact +2 clock cycle sampling latency and resulting timing-boundary shift
across raw bypass (`sync_mask=0x00`), per-pin synchronized (`sync_mask=0x08`, MISO only),
and globally synchronized (`sync_mask=0xFF`) modes on `tt_um_harjag_21_timing_explorer`.

Note: Digital RTL simulation measures cycle-accurate sampling shift and edge-wait latency
under synchronous/delayed inputs; metastability MTBF under asynchronous analog violations
depends on CMOS5L flip-flop resolution characteristics and is not proven by RTL simulation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.protocol_asm import build_spi_mode0_program, insn  # noqa: E402
from tools.timing_explorer import (  # noqa: E402
    IcarusSimTransport,
    ProtocolClient,
    classify_boundary,
    run_spi_boundary_exploration,
)


def run_sync_robustness_study(delays_ns: tuple[int, ...] = (25, 37, 50)) -> Dict[str, Any]:
    modes = [
        ("raw_bypass", 0x00),
        ("per_pin_miso_sync", 0x08),
        ("global_all_pins_sync", 0xFF),
    ]
    rows: List[Dict[str, Any]] = []
    with IcarusSimTransport() as transport:
        client = ProtocolClient(transport)
        transport.reset(1)
        for d_ns in delays_ns:
            by_mode: Dict[str, int] = {}
            for m_name, s_mask in modes:
                client.configure_ctrl(sync_mask=s_mask, cap_mask=0, cap_mode=0)
                transport.configure_target(0, delay_ns=d_ns, spi_resp=0xAA)
                recs = run_spi_boundary_exploration(
                    client,
                    mosi_byte=0x55,
                    expected_miso=0xAA,
                    min_low_cycles=2,
                    max_low_cycles=14,
                    method="sweep",
                    delay_ns=d_ns,
                )
                trials = [r for r in recs if r.get("type") == "trial"]
                cls = classify_boundary({r["low_cycles"]: bool(r["passed"]) for r in trials})
                rows.append(
                    {
                        "delay_ns": d_ns,
                        "mode": m_name,
                        "sync_mask": f"0x{s_mask:02x}",
                        "status": cls["status"],
                        "boundary_low_cycles": cls["boundary_low_cycles"],
                    }
                )
                by_mode[m_name] = int(cls["boundary_low_cycles"])
            assert by_mode["per_pin_miso_sync"] == by_mode["raw_bypass"] + 2
            assert by_mode["global_all_pins_sync"] == by_mode["raw_bypass"] + 2

        # Direct WAIT_EDGE latency comparison (I2C SCL release pin=0 raw vs synced)
        edge_prog = [
            insn("OE", 0x01),
            insn("OUT", 0x00),
            insn("WAIT", 3),
            insn("OE", 0x00),
            insn("WAIT_EDGE", (1 << 11) | (0 << 8) | 0),
            insn("MARK", 0xEE),
            insn("HALT", 0),
        ]
        client.load_program(edge_prog)
        transport.configure_target(4, delay_ns=4)  # target stretches SCL for 4 cycles
        edge_lat: Dict[str, int] = {}
        for m_name, s_mask in (("raw_bypass", 0x00), ("per_pin_scl_sync", 0x01)):
            client.configure_ctrl(sync_mask=s_mask, cap_mask=0, cap_mode=0)
            tr = client.run_trial(12)
            assert tr["eng_state"] == "ENG_HALTED" and tr["last_sample"] == 1
            edge_lat[m_name] = int(tr["events"][0]["ts_cycles"])
        client.configure_ctrl(sync_mask=0x00, cap_mask=0, cap_mode=0)
        assert edge_lat["per_pin_scl_sync"] == edge_lat["raw_bypass"] + 2

    return {
        "spi_boundary_rows": rows,
        "wait_edge_stretch_cycles": {
            "raw_mark_ts": edge_lat["raw_bypass"],
            "sync_mark_ts": edge_lat["per_pin_scl_sync"],
            "measured_delta_cycles": edge_lat["per_pin_scl_sync"] - edge_lat["raw_bypass"],
        },
        "design_decision": (
            "Per-pin 8-bit `cfg_sync_mask` (reset default 0x00 = raw bypass) supports both zero-latency "
            "timing exploration and selective/global 2-FF input synchronization without distorting unmasked experiments."
        ),
        "scope_limit": (
            "RTL simulation verifies exact +2-cycle pipeline latency and boundary shift; analog metastability "
            "MTBF is not measurable in digital RTL simulation."
        ),
    }


def format_markdown(rep: Dict[str, Any]) -> str:
    lines = [
        "# Raw vs 2-Flop Synchronized Input Robustness Study",
        "",
        "Measured live `tt_um_harjag_21_timing_explorer` RTL at `100 MHz` (`10 ns` period).",
        "",
        "| Target Delay (ns) | Mode | `sync_mask` | Boundary Status | Min Passing `low_cycles` |",
        "|---|---|---|---|---|",
    ]
    for r in rep["spi_boundary_rows"]:
        lines.append(
            f"| {r['delay_ns']} | `{r['mode']}` | `{r['sync_mask']}` | `{r['status']}` | {r['boundary_low_cycles']} |"
        )
    we = rep["wait_edge_stretch_cycles"]
    lines.extend(
        [
            "",
            f"- **Clock-Stretch `WAIT_EDGE` Latency Shift:** raw `MARK ts={we['raw_mark_ts']}` vs synced `MARK ts={we['sync_mark_ts']}` (exact **+{we['measured_delta_cycles']} cycles**).",
            f"- **Design Choice:** {rep['design_decision']}",
            f"- **Scope Caveat:** {rep['scope_limit']}",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure raw vs 2-FF synchronized input latency shift")
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args()
    rep = run_sync_robustness_study()
    md = format_markdown(rep)
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
