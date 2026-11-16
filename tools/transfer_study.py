# SPDX-License-Identifier: Apache-2.0
"""Frozen-strategy transfer study across independently parameterized target instances.

Strategies (`joint_random`, `guided_feedback`, `guided_diverse`) in `tools/fuzz_benchmark.py`
are completely unmodified from commit `23bca4a`. Target parameters are generated from an
independent RNG seed and frozen before any fuzzing trial executes.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import tools.fuzz_benchmark as fb  # noqa: E402
from tools.diff_minimizer import replay_minimized_case  # noqa: E402
from tools.timing_explorer import IcarusSimTransport, ProtocolClient  # noqa: E402

STRATEGIES: Tuple[str, ...] = ("joint_random", "guided_feedback", "guided_diverse")
ERR_CODES: Tuple[int, ...] = (0xB1, 0xB2, 0xB3, 0xB4)


def generate_frozen_target_instances(
    *, target_seed: int = 20269999, num_instances: int = 10
) -> List[Dict[str, Any]]:
    """Independently sample parameterized 4-bug target specs prior to any fuzzing."""
    rng = random.Random(target_seed)
    pool = list(fb.CMD_POOL)
    instances: List[Dict[str, Any]] = []

    for idx in range(num_instances):
        # Sample 4 active opcodes returning normal state signatures (0x11..0x44)
        norm_ops = rng.sample(pool, 4)

        # Sample 4 unique (op0, op1) trigger pairs + randomized timing window widths [2..4]
        used_pairs: set[Tuple[int, int]] = set()
        slots: List[Dict[str, Any]] = []
        shape_kinds = ["fast_edge", "slow_edge", "interior_low", "interior_high"]
        rng.shuffle(shape_kinds)

        for k in range(4):
            while True:
                op0 = rng.choice(pool)
                op1 = rng.choice(pool)
                if op0 != op1 and (op0, op1) not in used_pairs:
                    used_pairs.add((op0, op1))
                    break

            width = rng.choice((3, 4, 5))  # 4..6 gap values out of 0..15 (~25..37% timing match)
            kind = shape_kinds[k]
            if kind == "fast_edge":
                gap_lo = 0
                gap_hi = width
            elif kind == "slow_edge":
                gap_hi = fb.MAX_GAP
                gap_lo = fb.MAX_GAP - width
            elif kind == "interior_low":
                gap_lo = rng.randint(2, 6)
                gap_hi = min(fb.MAX_GAP - 1, gap_lo + width)
            else:
                gap_lo = rng.randint(7, 11)
                gap_hi = min(fb.MAX_GAP - 1, gap_lo + width)

            slots.append(
                {
                    "slot": k,
                    "bug_id": f"P{k}_0x{ERR_CODES[k]:02x}",
                    "op0": op0,
                    "op1": op1,
                    "gap_lo": gap_lo,
                    "gap_hi": gap_hi,
                    "cs_lo": gap_lo + 3,
                    "cs_hi": gap_hi + 3,
                    "err_byte": ERR_CODES[k],
                    "shape": kind,
                }
            )

        instances.append(
            {
                "instance_id": f"T{idx:02d}",
                "norm_ops": norm_ops,
                "slots": slots,
            }
        )
    return instances


def classify_param_divergence(ev: Dict[str, Any]) -> str:
    """Post-hoc classification strictly by observed DUT error code 0xB1..0xB4."""
    ref_b = ev["ref_bytes"]
    dut_b = ev["dut_bytes"]
    for rb, db in zip(ref_b, dut_b):
        for k, code in enumerate(ERR_CODES):
            if db == code and rb != code:
                return f"P{k}_0x{code:02x}"
    return "other_param_div"


def _patch_for_param(slots: Sequence[Dict[str, Any]]) -> None:
    fb.classify_multispec_divergence = classify_param_divergence
    names = tuple(s["bug_id"] for s in slots)
    fb.bug_classes_for_mode = lambda _m: names


def _configure_transport_instance(
    transport: IcarusSimTransport, inst: Dict[str, Any]
) -> None:
    transport.configure_param_normals(inst["norm_ops"])
    for s in inst["slots"]:
        transport.configure_param_slot(
            s["slot"], s["op0"], s["op1"], s["cs_lo"], s["cs_hi"], s["err_byte"]
        )


def _worker_param_pair(
    args: Tuple[Dict[str, Any], int, int],
) -> Tuple[str, int, Dict[str, Dict[str, Any]]]:
    inst, seed, budget = args
    _patch_for_param(inst["slots"])
    out: Dict[str, Dict[str, Any]] = {}
    with IcarusSimTransport() as transport:
        transport.reset(1)
        _configure_transport_instance(transport, inst)
        client = ProtocolClient(transport)
        for strat in STRATEGIES:
            res = fb.run_strategy_seed(
                transport,
                client,
                strategy=strat,
                budget=budget,
                seed=seed,
                target_mode=9,
            )
            curve = [
                sum(1 for t in res["first_bug_trial"].values() if t <= step)
                for step in range(1, budget + 1)
            ]
            res["discovery_curve"] = curve
            out[strat] = res
    return inst["instance_id"], seed, out


def run_transfer_study(
    *,
    instances: Sequence[Dict[str, Any]],
    seeds_per_instance: int = 2,
    budget: int = 120,
    workers: int = min(10, os.cpu_count() or 4),
    replay_repeats: int = 5,
) -> Dict[str, Any]:
    tasks: List[Tuple[Dict[str, Any], int, int]] = []
    for i, inst in enumerate(instances):
        for j in range(seeds_per_instance):
            seed = 20265000 + i * 100 + j
            tasks.append((inst, seed, budget))

    run_records: Dict[Tuple[str, int], Dict[str, Dict[str, Any]]] = {}
    t0 = time.time()

    if workers <= 1 or len(tasks) <= 1:
        for done, t_arg in enumerate(tasks, 1):
            iid, sd, out = _worker_param_pair(t_arg)
            run_records[(iid, sd)] = out
            print(
                f"[pair {done}/{len(tasks)}] {iid} seed={sd} elapsed={time.time()-t0:.1f}s "
                + " ".join(f"{s}={out[s]['unique_bug_classes_found']}" for s in STRATEGIES),
                file=sys.stderr,
                flush=True,
            )
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
            futs = [pool.submit(_worker_param_pair, t_arg) for t_arg in tasks]
            done = 0
            for fut in as_completed(futs):
                iid, sd, out = fut.result()
                run_records[(iid, sd)] = out
                done += 1
                print(
                    f"[pair {done}/{len(tasks)}] {iid} seed={sd} elapsed={time.time()-t0:.1f}s "
                    + " ".join(f"{s}={out[s]['unique_bug_classes_found']}" for s in STRATEGIES),
                    file=sys.stderr,
                    flush=True,
                )

    ordered_keys = [(inst["instance_id"], 20265000 + i * 100 + j) for i, inst in enumerate(instances) for j in range(seeds_per_instance)]

    # Verify deterministic replay (5/5) on sample discovered examples across instances
    replay_checks: List[Dict[str, Any]] = []
    with IcarusSimTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)
        for inst in instances:
            _configure_transport_instance(transport, inst)
            # Pick first discovered example from any strategy on seed 0 of this instance
            pair_res = run_records[(inst["instance_id"], 20265000 + int(inst["instance_id"][1:]) * 100)]
            chosen_ex = None
            chosen_bug = None
            for strat in STRATEGIES:
                if pair_res[strat]["bug_examples"]:
                    chosen_bug = sorted(pair_res[strat]["bug_examples"].keys())[0]
                    chosen_ex = pair_res[strat]["bug_examples"][chosen_bug]
                    break
            if chosen_ex is not None:
                rep = replay_minimized_case(
                    transport,
                    client,
                    chosen_ex["commands"],
                    chosen_ex["gap_wait"],
                    repeats=replay_repeats,
                    ref_thresh=0,
                    dut_thresh=1,
                    target_mode=9,
                    capture_per_cmd=True,
                )
                replay_checks.append(
                    {
                        "instance_id": inst["instance_id"],
                        "bug_id": chosen_bug,
                        "commands_hex": chosen_ex["commands_hex"],
                        "gap_wait": chosen_ex["gap_wait"],
                        "matches": rep["deterministic_divergence_matches"],
                        "repeats": rep["repeats"],
                    }
                )

    elapsed_s = round(time.time() - t0, 2)

    # Curves + paired CIs at every trial t = 1..budget
    curves_by_strategy: Dict[str, Dict[str, List[float]]] = {}
    for strat in STRATEGIES:
        means, lo95, hi95 = [], [], []
        for idx_t in range(budget):
            vals = [run_records[k][strat]["discovery_curve"][idx_t] for k in ordered_keys]
            ci = fb._bootstrap_paired_ci(vals, resamples=4000, seed=100 + idx_t)
            means.append(ci["mean_delta"])
            lo95.append(ci["ci95_lo"])
            hi95.append(ci["ci95_hi"])
        curves_by_strategy[strat] = {"mean": means, "ci95_lo": lo95, "ci95_hi": hi95}

    paired_curves: Dict[str, Dict[str, List[float]]] = {}
    paired_checkpoints: Dict[str, Dict[str, Any]] = {}
    for a, b in (
        ("guided_diverse", "joint_random"),
        ("guided_feedback", "joint_random"),
        ("guided_diverse", "guided_feedback"),
    ):
        pkey = f"{a}_vs_{b}"
        m_d, lo_d, hi_d = [], [], []
        for idx_t in range(budget):
            deltas = [
                run_records[k][a]["discovery_curve"][idx_t]
                - run_records[k][b]["discovery_curve"][idx_t]
                for k in ordered_keys
            ]
            ci = fb._bootstrap_paired_ci(deltas, resamples=4000, seed=500 + idx_t)
            m_d.append(ci["mean_delta"])
            lo_d.append(ci["ci95_lo"])
            hi_d.append(ci["ci95_hi"])
        paired_curves[pkey] = {"mean_delta": m_d, "ci95_lo": lo_d, "ci95_hi": hi_d}

        checkpoints = {}
        raw_t = [max(1, rep_t) for rep_t in (budget // 4, budget // 2, (3 * budget) // 4, budget)]
        ckpt_steps = sorted(set(raw_t))
        for t_chk in ckpt_steps:
            deltas = [
                run_records[k][a]["discovery_curve"][t_chk - 1]
                - run_records[k][b]["discovery_curve"][t_chk - 1]
                for k in ordered_keys
            ]
            ci = fb._bootstrap_paired_ci(deltas, resamples=10000, seed=42 + t_chk)
            checkpoints[str(t_chk)] = {
                **ci,
                "wins": sum(1 for d in deltas if d > 0),
                "ties": sum(1 for d in deltas if d == 0),
                "losses": sum(1 for d in deltas if d < 0),
            }
        paired_checkpoints[pkey] = checkpoints

    final_summary: Dict[str, Any] = {}
    for strat in STRATEGIES:
        bugs_final = [run_records[k][strat]["unique_bug_classes_found"] for k in ordered_keys]
        sigs_final = [run_records[k][strat]["unique_obs_signatures"] for k in ordered_keys]
        raw_final = [run_records[k][strat]["raw_divergences"] for k in ordered_keys]
        final_summary[strat] = {
            "runs": len(ordered_keys),
            "mean_unique_bugs": round(statistics.mean(bugs_final), 3),
            "std_unique_bugs": round(statistics.stdev(bugs_final), 3) if len(bugs_final) > 1 else 0.0,
            "median_unique_bugs": statistics.median(bugs_final),
            "min_unique_bugs": min(bugs_final),
            "max_unique_bugs": max(bugs_final),
            "mean_obs_signatures": round(statistics.mean(sigs_final), 2),
            "mean_raw_divergences": round(statistics.mean(raw_final), 2),
        }

    per_run_rows = []
    for iid, sd in ordered_keys:
        row = {"instance_id": iid, "seed": sd}
        for strat in STRATEGIES:
            r = run_records[(iid, sd)][strat]
            row[strat] = {
                "unique_bugs": r["unique_bug_classes_found"],
                "bugs": r["bug_classes_found"],
                "obs_signatures": r["unique_obs_signatures"],
                "raw_divergences": r["raw_divergences"],
                "first_bug_trial": r["first_bug_trial"],
            }
        per_run_rows.append(row)

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "rtl_simulation_parameterized_transfer_study",
        "elapsed_seconds": elapsed_s,
        "num_instances": len(instances),
        "seeds_per_instance": seeds_per_instance,
        "total_paired_runs": len(ordered_keys),
        "budget": budget,
        "strategies_frozen_from_commit": "23bca4a",
        "final_summary": final_summary,
        "paired_checkpoints": paired_checkpoints,
        "curves_by_strategy": curves_by_strategy,
        "paired_curves": paired_curves,
        "replay_verification": replay_checks,
        "per_run_rows": per_run_rows,
    }


def format_transfer_markdown(rep: Dict[str, Any]) -> str:
    fs = rep["final_summary"]
    lines = [
        "# Independently Parameterized Transfer Study (Frozen Strategies)",
        "",
        f"- **Timestamp (UTC):** {rep['timestamp_utc']}",
        f"- **Evidence class:** `{rep['evidence_class']}`",
        f"- **Frozen baseline guarantee:** `tools/fuzz_benchmark.py` (`joint_random`, `guided_feedback`, `guided_diverse`) unmodified since commit `{rep['strategies_frozen_from_commit']}`.",
        f"- **Target setup:** `{rep['num_instances']}` independently parameterized 4-bug target instances (`MODE_SPI_PARAM=9`) frozen from RNG `20269999` prior to testing x `{rep['seeds_per_instance']}` seeds/instance (`{rep['total_paired_runs']}` matched runs, budget `{rep['budget']}` trials each) in `{rep['elapsed_seconds']}s`.",
        "",
        f"## End-of-Budget Summary (`t = {rep['budget']}` Trials)",
        "",
        "| Strategy | Mean +/- SD Unique Bugs (`0..4`) | Median `[min, max]` | Mean Obs. Pin Signatures | Mean Raw Divergences |",
        "|---|---|---|---|---|",
    ]
    for st in STRATEGIES:
        r = fs[st]
        lines.append(
            f"| `{st}` | **{r['mean_unique_bugs']:.2f}** +/- {r['std_unique_bugs']:.2f} | "
            f"`{r['median_unique_bugs']:.1f}` `[{r['min_unique_bugs']}, {r['max_unique_bugs']}]` | "
            f"{r['mean_obs_signatures']:.1f} | {r['mean_raw_divergences']:.1f} |"
        )

    sample_cp = next(iter(rep["paired_checkpoints"].values()))
    ckpt_keys = sorted(sample_cp.keys(), key=lambda x: int(x))
    lines += [
        "",
        "## Paired Discovery Differences Across Trials (`10,000` Bootstrap Resamples)",
        "",
        "| Comparison (`A - B`) | " + " | ".join(f"`t = {tk}` Delta (95% CI)" for tk in ckpt_keys) + " |",
        "|---|" + "|".join(["---"] * len(ckpt_keys)) + "|",
    ]
    for pkey, cp in rep["paired_checkpoints"].items():
        cells = []
        for idx_k, t_s in enumerate(ckpt_keys):
            c = cp[t_s]
            if idx_k == len(ckpt_keys) - 1:
                cells.append(f"**{c['mean_delta']:+.2f}** `[{c['ci95_lo']:+.2f}, {c['ci95_hi']:+.2f}]` (`{c['wins']}W/{c['ties']}T/{c['losses']}L`)")
            else:
                cells.append(f"{c['mean_delta']:+.2f} `[{c['ci95_lo']:+.2f}, {c['ci95_hi']:+.2f}]`")
        lines.append(f"| `{pkey}` | " + " | ".join(cells) + " |")

    rep_ok = sum(1 for x in rep["replay_verification"] if x["matches"] == x["repeats"])
    lines += [
        "",
        f"## Replay Verification Across Target Instances (`{rep_ok}/{len(rep['replay_verification'])}` Exact `5/5` Determinism)",
        "",
        "| Target | Bug ID | Commands | `gap_wait` | Replay |",
        "|---|---|---|---|---|",
    ]
    for rv in rep["replay_verification"]:
        lines.append(
            f"| `{rv['instance_id']}` | `{rv['bug_id']}` | `{' -> '.join(rv['commands_hex'])}` | `{rv['gap_wait']}` | `{rv['matches']}/{rv['repeats']}` |"
        )

    lines += [
        "",
        "## Per-Run Breakdown",
        "",
        "| Instance | Seed | `joint_random` (Bugs / Sigs) | `guided_feedback` (Bugs / Sigs) | `guided_diverse` (Bugs / Sigs) |",
        "|---|---|---|---|---|",
    ]
    for row in rep["per_run_rows"]:
        lines.append(
            f"| `{row['instance_id']}` | `{row['seed']}` | "
            f"**{row['joint_random']['unique_bugs']}** / {row['joint_random']['obs_signatures']} | "
            f"**{row['guided_feedback']['unique_bugs']}** / {row['guided_feedback']['obs_signatures']} | "
            f"**{row['guided_diverse']['unique_bugs']}** / {row['guided_diverse']['obs_signatures']} |"
        )
    return "\n".join(lines) + "\n"


def save_transfer_plot(rep: Dict[str, Any], out_png: Path) -> None:
    import matplotlib.pyplot as plt

    xs = list(range(1, rep["budget"] + 1))
    colors = {
        "joint_random": "#4c78a8",
        "guided_feedback": "#f58518",
        "guided_diverse": "#2b9348",
    }
    labels = {
        "joint_random": "Joint Random (blind)",
        "guided_feedback": "Guided v1 (flat corpus)",
        "guided_diverse": "Guided Diverse (bucketed novelty)",
    }

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.6, 4.5), dpi=160)

    for st in STRATEGIES:
        c = rep["curves_by_strategy"][st]
        ax1.plot(xs, c["mean"], color=colors[st], linewidth=2.0, label=labels[st])
        ax1.fill_between(xs, c["ci95_lo"], c["ci95_hi"], color=colors[st], alpha=0.14)
    ax1.set_xlabel("Trials Spent")
    ax1.set_ylabel("Mean Unique Bugs Discovered (0..4)")
    ax1.set_title(f"Discovery Curves ({rep['total_paired_runs']} Paired Runs, 95% CI)")
    ax1.set_xlim(1, rep["budget"])
    ax1.set_ylim(0, 4.0)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", fontsize=8.5)

    p_specs = [
        ("guided_diverse_vs_joint_random", "#2b9348", "Diverse - Joint Random"),
        ("guided_feedback_vs_joint_random", "#f58518", "Guided v1 - Joint Random"),
        ("guided_diverse_vs_guided_feedback", "#7b2cbf", "Diverse - Guided v1"),
    ]
    for pkey, col, lbl in p_specs:
        pc = rep["paired_curves"][pkey]
        ax2.plot(xs, pc["mean_delta"], color=col, linewidth=1.9, label=lbl)
        ax2.fill_between(xs, pc["ci95_lo"], pc["ci95_hi"], color=col, alpha=0.13)
    ax2.axhline(0.0, color="#333333", linestyle="--", linewidth=1.0)
    ax2.set_xlabel("Trials Spent")
    ax2.set_ylabel("Paired Bug Advantage (95% Bootstrap CI)")
    ax2.set_title("Paired Differences Across Trials")
    ax2.set_xlim(1, rep["budget"])
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper left", fontsize=8.5)

    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Frozen-strategy parameterized target transfer study")
    parser.add_argument("--freeze-out", type=Path, default=ROOT_DIR / "docs" / "evidence" / "rtl-v11-frozen-transfer-targets.json")
    parser.add_argument("--num-instances", type=int, default=10)
    parser.add_argument("--seeds-per-instance", type=int, default=2)
    parser.add_argument("--budget", type=int, default=120)
    parser.add_argument("--workers", type=int, default=min(10, os.cpu_count() or 4))
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--png-out", type=Path)
    args = parser.parse_args()

    instances = generate_frozen_target_instances(
        target_seed=20269999, num_instances=args.num_instances
    )
    args.freeze_out.parent.mkdir(parents=True, exist_ok=True)
    args.freeze_out.write_text(
        json.dumps(
            {
                "generator_seed": 20269999,
                "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
                "instances": instances,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    rep = run_transfer_study(
        instances=instances,
        seeds_per_instance=args.seeds_per_instance,
        budget=args.budget,
        workers=args.workers,
    )
    md = format_transfer_markdown(rep)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rep, indent=2, sort_keys=True), encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    if args.png_out:
        save_transfer_plot(rep, args.png_out)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
