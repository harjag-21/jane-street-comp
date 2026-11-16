# SPDX-License-Identifier: Apache-2.0
"""Held-out failure-shape benchmark for the FROZEN 4-strategy fuzzer suite.

Zero edits are made to `tools/fuzz_benchmark.py` strategies (`payload_only`,
`joint_random`, `guided_feedback`, `guided_diverse`); this script evaluates them
unchanged against `MODE_SPI_SHAPES` (`target_mode=8`: `H1..H4`).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import tools.fuzz_benchmark as fb  # noqa: E402

SHAPE_BUG_CLASSES: Tuple[str, ...] = (
    "H1_interior_notch",
    "H2_bimodal_band",
    "H3_three_step_cascade",
    "H4_upper_watchdog_tail",
)


def classify_shape_divergence(ev: Dict[str, Any]) -> str:
    """Post-hoc classification for held-out failure shapes H1..H4."""
    ref_b = ev["ref_bytes"]
    dut_b = ev["dut_bytes"]
    for rb, db in zip(ref_b, dut_b):
        if db == 0x33 and rb != 0x33:
            return "H1_interior_notch"
        if db == 0x77 and rb != 0x77:
            return "H2_bimodal_band"
        if db == 0xE1 and rb != 0xE1:
            return "H3_three_step_cascade"
        if db == 0xC4 and rb != 0xC4:
            return "H4_upper_watchdog_tail"
    return "other_shape_divergence"


def _patch_for_shapes() -> None:
    """Bind post-hoc classifier/label helpers without touching frozen strategies."""
    fb.classify_multispec_divergence = classify_shape_divergence
    fb.bug_classes_for_mode = lambda _mode: SHAPE_BUG_CLASSES


def _worker_shape_seed(
    args: Tuple[int, Tuple[str, ...], int, int],
) -> Tuple[int, Dict[str, Dict[str, Any]]]:
    _patch_for_shapes()
    return fb._worker_run_seed(args)


def run_heldout_benchmark(
    *,
    budget: int = 100,
    seeds: Sequence[int] = tuple(20262101 + i for i in range(20)),
    replay_repeats: int = 5,
    workers: int = min(10, os.cpu_count() or 4),
) -> Dict[str, Any]:
    _patch_for_shapes()
    orig_worker = fb._worker_run_seed
    fb._worker_run_seed = _worker_shape_seed
    try:
        rep = fb.run_benchmark(
            budget=budget,
            seeds=seeds,
            replay_repeats=replay_repeats,
            target_mode=8,
            workers=workers,
        )
    finally:
        fb._worker_run_seed = orig_worker

    rep["evidence_class"] = "rtl_simulation_heldout_shape_benchmark"
    rep["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    rep["strategies_frozen_from_commit"] = "23bca4a"
    return rep


def format_shape_markdown(rep: Dict[str, Any]) -> str:
    s = rep["summary_by_strategy"]
    all_bugs = rep["bug_classes"]
    max_b = len(all_bugs)
    lines = [
        f"# {len(rep['seeds'])}-Seed Held-Out Failure-Shape Benchmark (Frozen Strategies)",
        "",
        f"- **Timestamp (UTC):** {rep['timestamp_utc']}",
        f"- **Evidence class:** `{rep['evidence_class']}`",
        "- **Target:** `MODE_SPI_SHAPES` (`H1` interior notch `gap 8..9`, `H2` bimodal `{2,3} U {12,13}`, `H3` quiet-prefix 3-step `gap 4..6`, `H4` watchdog tail `gap 14..15`)",
        f"- **Frozen baseline guarantee:** `tools/fuzz_benchmark.py` strategies (`payload_only`, `joint_random`, `guided_feedback`, `guided_diverse`) run completely unmodified from commit `{rep['strategies_frozen_from_commit']}`.",
        f"- **Scale:** `{rep['budget_per_seed']}` trials/seed x `{len(rep['seeds'])}` disjoint seeds (`{rep['seeds'][0]}..{rep['seeds'][-1]}`) in `{rep['elapsed_seconds']}s`",
        "",
        "## Strategy Comparison (Matched Trial Budget)",
        "",
        f"| Strategy | Mean +/- SD Bugs (`0..{max_b}`) | Median `[min, max]` | Mean Obs. Pin Signatures | Mean Raw Divergences |",
        "|---|---|---|---|---|",
    ]
    for strat in rep["strategies"]:
        r = s[strat]
        lines.append(
            f"| `{strat}` | **{r['mean_unique_bugs']:.2f}** +/- {r['std_unique_bugs']:.2f} | "
            f"`{r['median_unique_bugs']:.1f}` `[{r['min_unique_bugs']}, {r['max_unique_bugs']}]` | "
            f"{r['mean_obs_signatures']:.1f} | {r['mean_raw_divergences']:.1f} |"
        )

    lines += [
        "",
        "## Paired Seed-Level Statistical Comparisons (`10,000` Bootstrap Resamples)",
        "",
        "| Pair (`A - B`) | Mean Bug Delta (95% CI) | Mean Sig Delta (95% CI) | Seed Win / Tie / Loss |",
        "|---|---|---|---|",
    ]
    for k, pv in rep["paired_comparisons"].items():
        bd = pv["bugs_delta"]
        sd = pv["sigs_delta"]
        lines.append(
            f"| `{k}` | **{bd['mean_delta']:+.2f}** `[{bd['ci95_lo']:+.2f}, {bd['ci95_hi']:+.2f}]` | "
            f"{sd['mean_delta']:+.1f} `[{sd['ci95_lo']:+.1f}, {sd['ci95_hi']:+.1f}]` | "
            f"`{pv['wins']}W / {pv['ties']}T / {pv['losses']}L` |"
        )

    lines += [
        "",
        "## Per-Shape Hit Rate & Mean Trials-to-Discover (TTD)",
        "",
        "| Bug Shape | Timing Shape | " + " | ".join(f"`{st}`" for st in rep["strategies"]) + " |",
        "|---|---|" + "|".join(["---"] * len(rep["strategies"])) + "|",
    ]
    shapes_desc = {
        "H1_interior_notch": "Narrow notch `gap in {8,9}` (silent `0xAB`)",
        "H2_bimodal_band": "Two bands `{2,3} U {12,13}` (silent `0x04`)",
        "H3_three_step_cascade": "Quiet `0xB9 -> 0x35`, `gap in {4,5,6}`",
        "H4_upper_watchdog_tail": "Extreme tail `gap in {14,15}`",
    }
    for bcls in all_bugs:
        cells = []
        for st in rep["strategies"]:
            hr = s[st]["bug_hit_rate"][bcls] * 100
            ttd = s[st]["mean_trials_to_discover"][bcls]
            cells.append(f"`{hr:.0f}%` ({ttd if ttd is not None else '-'})")
        lines.append(f"| `{bcls}` | {shapes_desc.get(bcls, '-')} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Minimized & Replayed Counterexamples",
        "",
        "| Bug Shape | Discovered Sequence (`gap_wait`) | Minimized Sequence | Observed Timing Envelope | Replay Rate |",
        "|---|---|---|---|---|",
    ]
    for bcls, item in sorted(rep["minimized_and_replayed_examples"].items()):
        ex = item["discovered_example"]
        m = item["minimized"]
        rep_r = item["replay_at_discovered_gap"]
        fail_list = [w["gap_wait"] for w in m["window_scan"] if w["diverged"]]
        lines.append(
            f"| `{bcls}` | `{' -> '.join(ex['commands_hex'])}` (`gap={ex['gap_wait']}`) | "
            f"`{' -> '.join(m['minimized_commands_hex'])}` | fails `gap in {fail_list}` | "
            f"`{rep_r['deterministic_divergence_matches']}/{rep_r['repeats']}` |"
        )

    lines += [
        "",
        "## Per-Seed Breakdown (All Held-Out Seeds)",
        "",
        "| Seed | " + " | ".join(f"`{st}` (Bugs / Sigs / Raw)" for st in rep["strategies"]) + " |",
        "|---|" + "|".join(["---"] * len(rep["strategies"])) + "|",
    ]
    by_s = {st: {r["seed"]: r for r in rep["per_seed_runs"][st]} for st in rep["strategies"]}
    for seed in rep["seeds"]:
        row_cells = []
        for st in rep["strategies"]:
            r = by_s[st][seed]
            short_b = ",".join(b.split("_", 1)[0] for b in r["bug_classes_found"]) or "-"
            row_cells.append(f"**{r['unique_bug_classes_found']}** (`{short_b}`) / {r['unique_obs_signatures']} / {r['raw_divergences']}")
        lines.append(f"| `{seed}` | " + " | ".join(row_cells) + " |")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen fuzzers on held-out failure shapes")
    parser.add_argument("--budget", type=int, default=100)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--workers", type=int, default=min(10, os.cpu_count() or 4))
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--png-out", type=Path)
    args = parser.parse_args()

    seed_list = [20262101 + i for i in range(args.seeds)]
    rep = run_heldout_benchmark(
        budget=args.budget, seeds=seed_list, replay_repeats=args.repeats, workers=args.workers
    )
    md = format_shape_markdown(rep)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rep, indent=2, sort_keys=True), encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    if args.png_out:
        fb.save_benchmark_plot(rep, args.png_out)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
