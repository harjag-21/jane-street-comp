# SPDX-License-Identifier: Apache-2.0
"""Experiments 4, 5, 7: Differential Ref-vs-DUT fuzzing, minimization, and replay."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.protocol_asm import assemble  # noqa: E402
from tools.timing_explorer import IcarusSimTransport, ProtocolClient  # noqa: E402


def build_spi_sequence_program(
    commands: Sequence[int],
    *,
    cs_pin: int = 0,
    sclk_pin: int = 1,
    mosi_pin: int = 2,
    miso_pin: int = 3,
) -> List[int]:
    """Assemble 1..3 SPI mode-0 transactions separated by `WAIT_PARAM` CS_N high gaps."""
    if not (1 <= len(commands) <= 3):
        raise ValueError(f"Expected 1..3 SPI command bytes, got {len(commands)}")
    cs_mask = 1 << cs_pin
    sclk_mask = 1 << sclk_pin
    mosi_mask = 1 << mosi_pin
    oe_mask = cs_mask | sclk_mask | mosi_mask

    lines = [
        f"OUT 0x{cs_mask:02x}",
        f"OE  0x{oe_mask:02x}",
    ]
    for idx, cmd in enumerate(commands):
        if idx > 0:
            lines.append("WAIT_PARAM 0")
        lines.append("OUT 0x00")  # assert CS_N low
        for bit in range(7, -1, -1):
            m = mosi_mask if ((cmd >> bit) & 1) else 0
            lines.append(f"OUT 0x{m:02x}")
            lines.append(f"OUT_SAMPLE 0x{(sclk_mask | m):02x}, {miso_pin}, 0")
        lines.append("OUT 0x00")
        lines.append(f"OUT 0x{cs_mask:02x}")  # deassert CS_N high
    lines.append("MARK_SAMPLE")
    lines.append("HALT 0")
    return assemble(lines)


def eval_ref_vs_dut(
    transport: IcarusSimTransport,
    client: ProtocolClient,
    commands: Sequence[int],
    gap_wait: int,
    *,
    ref_thresh: int = 1,
    dut_thresh: int = 6,
) -> Dict[str, Any]:
    """Execute exact stimulus sequence on Ref and DUT models via the ASIC RTL."""
    words = build_spi_sequence_program(commands)

    transport.reset(1)
    transport.configure_target(3, delay_ns=ref_thresh)
    client.load_program(words)
    ref_res = client.run_trial(gap_wait)

    transport.reset(1)
    transport.configure_target(3, delay_ns=dut_thresh)
    client.load_program(words)
    dut_res = client.run_trial(gap_wait)

    ref_tags = [e["tag"] for e in ref_res["events"]]
    dut_tags = [e["tag"] for e in dut_res["events"]]
    diverged = (
        ref_res["sample_reg"] != dut_res["sample_reg"]
        or ref_tags != dut_tags
    )
    return {
        "commands": [int(c) for c in commands],
        "commands_hex": [f"0x{c:02x}" for c in commands],
        "gap_wait": int(gap_wait),
        "cs_high_cycles": int(gap_wait) + 2,
        "program_words": len(words),
        "ref_sample_reg": ref_res["sample_reg"],
        "dut_sample_reg": dut_res["sample_reg"],
        "ref_events": ref_res["events"],
        "dut_events": dut_res["events"],
        "diverged": diverged,
    }


def run_fuzz_campaign(
    transport: IcarusSimTransport,
    client: ProtocolClient,
    *,
    budget: int = 30,
    seed: int = 20261116,
    joint_timing: bool,
    fixed_gap: int = 10,
    ref_thresh: int = 1,
    dut_thresh: int = 6,
) -> Dict[str, Any]:
    """Experiment 7: Compare payload-only fuzzing against joint timing+payload fuzzing."""
    rng = random.Random(seed)
    cmd_pool = [0x04, 0x05, 0x06, 0x9F]
    first_div: Optional[Dict[str, Any]] = None
    divergences = 0
    trials: List[Dict[str, Any]] = []

    for step in range(budget):
        length = rng.choice([2, 3])
        cmds = [rng.choice(cmd_pool) for _ in range(length)]
        gap = rng.randint(0, 10) if joint_timing else fixed_gap
        ev = eval_ref_vs_dut(
            transport,
            client,
            cmds,
            gap,
            ref_thresh=ref_thresh,
            dut_thresh=dut_thresh,
        )
        ev["step"] = step
        trials.append(ev)
        if ev["diverged"]:
            divergences += 1
            if first_div is None:
                first_div = ev
    return {
        "mode": "joint_timing_and_payload" if joint_timing else "payload_only_fixed_gap",
        "budget": budget,
        "seed": seed,
        "divergences_found": divergences,
        "first_divergence": first_div,
        "trials": trials,
    }


def minimize_divergence(
    transport: IcarusSimTransport,
    client: ProtocolClient,
    initial_cmds: Sequence[int],
    failing_gap: int,
    *,
    max_gap: int = 12,
    ref_thresh: int = 1,
    dut_thresh: int = 6,
) -> Dict[str, Any]:
    """Experiment 5: Delta-reduce sequence length and pinpoint exact timing threshold."""
    cur_cmds = list(initial_cmds)
    reduction_steps: List[Dict[str, Any]] = []

    changed = True
    while changed and len(cur_cmds) > 1:
        changed = False
        for drop_idx in range(len(cur_cmds)):
            candidate = cur_cmds[:drop_idx] + cur_cmds[drop_idx + 1 :]
            probe = eval_ref_vs_dut(
                transport,
                client,
                candidate,
                failing_gap,
                ref_thresh=ref_thresh,
                dut_thresh=dut_thresh,
            )
            reduction_steps.append(
                {
                    "dropped_index": drop_idx,
                    "candidate_hex": [f"0x{c:02x}" for c in candidate],
                    "gap_wait": failing_gap,
                    "diverged": probe["diverged"],
                }
            )
            if probe["diverged"]:
                cur_cmds = candidate
                changed = True
                break

    # Sweep gap_wait from 0..max_gap on minimized sequence to isolate exact timing window
    window_scan: List[Dict[str, Any]] = []
    for g in range(0, max_gap + 1):
        res = eval_ref_vs_dut(
            transport,
            client,
            cur_cmds,
            g,
            ref_thresh=ref_thresh,
            dut_thresh=dut_thresh,
        )
        window_scan.append(
            {
                "gap_wait": g,
                "cs_high_cycles": g + 2,
                "diverged": res["diverged"],
                "ref_sample_reg": res["ref_sample_reg"],
                "dut_sample_reg": res["dut_sample_reg"],
            }
        )

    failing_gaps = [w["gap_wait"] for w in window_scan if w["diverged"]]
    passing_gaps = [w["gap_wait"] for w in window_scan if not w["diverged"]]
    highest_fail_gap = max(failing_gaps) if failing_gaps else None
    lowest_pass_gap = (
        min(g for g in passing_gaps if highest_fail_gap is None or g > highest_fail_gap)
        if passing_gaps
        else None
    )

    return {
        "initial_commands_hex": [f"0x{c:02x}" for c in initial_cmds],
        "minimized_commands": cur_cmds,
        "minimized_commands_hex": [f"0x{c:02x}" for c in cur_cmds],
        "sequence_reduction": f"{len(initial_cmds)} -> {len(cur_cmds)} transactions",
        "failing_gap_wait": failing_gap,
        "highest_failing_gap_wait": highest_fail_gap,
        "highest_failing_cs_high_cycles": (
            highest_fail_gap + 2 if highest_fail_gap is not None else None
        ),
        "lowest_passing_gap_wait": lowest_pass_gap,
        "lowest_passing_cs_high_cycles": (
            lowest_pass_gap + 2 if lowest_pass_gap is not None else None
        ),
        "reduction_steps": reduction_steps,
        "window_scan": window_scan,
    }


def replay_minimized_case(
    transport: IcarusSimTransport,
    client: ProtocolClient,
    cmds: Sequence[int],
    gap_wait: int,
    *,
    repeats: int = 10,
    ref_thresh: int = 1,
    dut_thresh: int = 6,
) -> Dict[str, Any]:
    """Experiment 2 + 4: Re-run minimized counterexample N times and check determinism."""
    matches = 0
    signatures: List[Tuple[int, int]] = []
    for _ in range(repeats):
        res = eval_ref_vs_dut(
            transport,
            client,
            cmds,
            gap_wait,
            ref_thresh=ref_thresh,
            dut_thresh=dut_thresh,
        )
        sig = (res["ref_sample_reg"], res["dut_sample_reg"])
        signatures.append(sig)
        if res["diverged"] and sig == signatures[0]:
            matches += 1
    return {
        "repeats": repeats,
        "deterministic_divergence_matches": matches,
        "ref_observed_hex": f"0x{signatures[0][0]:02x}" if signatures else None,
        "dut_observed_hex": f"0x{signatures[0][1]:02x}" if signatures else None,
        "replay_rate": matches / repeats if repeats else 0.0,
    }


def run_full_differential_study(
    *, budget: int = 30, seed: int = 20261116, repeats: int = 10
) -> Dict[str, Any]:
    with IcarusSimTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)

        baseline = run_fuzz_campaign(
            transport,
            client,
            budget=budget,
            seed=seed,
            joint_timing=False,
            fixed_gap=10,
        )
        joint = run_fuzz_campaign(
            transport,
            client,
            budget=budget,
            seed=seed,
            joint_timing=True,
        )
        if joint["first_divergence"] is None:
            raise RuntimeError("Joint fuzzer did not hit expected divergence within budget")

        first = joint["first_divergence"]
        minimized = minimize_divergence(
            transport,
            client,
            first["commands"],
            first["gap_wait"],
        )
        replay = replay_minimized_case(
            transport,
            client,
            minimized["minimized_commands"],
            minimized["highest_failing_gap_wait"],
            repeats=repeats,
        )

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "rtl_simulation_differential_targets",
        "ref_spec_min_cs_high_cycles": 1,
        "dut_quirk_min_cs_high_cycles": 6,
        "baseline_payload_only": {
            "budget": baseline["budget"],
            "seed": baseline["seed"],
            "fixed_gap_wait": 10,
            "divergences_found": baseline["divergences_found"],
        },
        "joint_timing_payload": {
            "budget": joint["budget"],
            "seed": joint["seed"],
            "divergences_found": joint["divergences_found"],
            "first_divergence": joint["first_divergence"],
        },
        "minimized_counterexample": minimized,
        "replay_verification": replay,
    }


def format_diff_markdown(report: Dict[str, Any]) -> str:
    b = report["baseline_payload_only"]
    j = report["joint_timing_payload"]
    fd = j["first_divergence"]
    m = report["minimized_counterexample"]
    r = report["replay_verification"]
    lines = [
        "# Differential Timing Fuzzing & Counterexample Minimization Report",
        "",
        f"- **Timestamp (UTC):** {report['timestamp_utc']}",
        f"- **Evidence class:** `{report['evidence_class']}`",
        f"- **Targets:** Reference (`CS_N` high >= `{report['ref_spec_min_cs_high_cycles']}` cycle after `WREN`) vs Buggy DUT (requires >= `{report['dut_quirk_min_cs_high_cycles']}` cycles after `WREN`)",
        f"- **Baseline (payload-only fuzzing, fixed safe gap `WAIT_PARAM=10`, budget={b['budget']}, seed={b['seed']}):** `{b['divergences_found']}` divergences found",
        f"- **Joint timing + payload fuzzing (same budget={j['budget']}, seed={j['seed']}):** `{j['divergences_found']}` divergences found (first hit at step `{fd['step']}`: commands `{fd['commands_hex']}`, `WAIT_PARAM={fd['gap_wait']}`)",
        f"- **Sequence reduction (Exp 5):** `{m['sequence_reduction']}` (`{m['initial_commands_hex']}` -> **`{m['minimized_commands_hex']}`**)",
        f"- **Tightest timing threshold:** fails at `WAIT_PARAM <= {m['highest_failing_gap_wait']}` (`CS_N` high `<= {m['highest_failing_cs_high_cycles']}` cycles); passes at `WAIT_PARAM >= {m['lowest_passing_gap_wait']}` (`CS_N` high `>= {m['lowest_passing_cs_high_cycles']}` cycles)",
        f"- **Deterministic replay (Exp 2):** `{r['deterministic_divergence_matches']}/{r['repeats']}` exact matches (`Ref={r['ref_observed_hex']}`, `DUT={r['dut_observed_hex']}`)",
        "",
        "## Timing Window Scan on Minimized Sequence",
        "",
        "| `WAIT_PARAM` | `CS_N` high cycles | Ref MISO | DUT MISO | Diverged |",
        "|---|---|---|---|---|",
    ]
    for row in m["window_scan"]:
        lines.append(
            f"| {row['gap_wait']} | {row['cs_high_cycles']} | `0x{row['ref_sample_reg']:02x}` | `0x{row['dut_sample_reg']:02x}` | {'YES' if row['diverged'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Differential fuzzing and counterexample minimizer")
    parser.add_argument("--budget", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261116)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args(argv)

    report = run_full_differential_study(
        budget=args.budget, seed=args.seed, repeats=args.repeats
    )
    md = format_diff_markdown(report)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
