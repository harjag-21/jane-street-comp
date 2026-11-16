# SPDX-License-Identifier: Apache-2.0
"""Experiment 7 (Full): Multi-seed black-box feedback-guided fuzzing benchmark."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.diff_minimizer import (  # noqa: E402
    eval_ref_vs_dut,
    minimize_divergence,
    replay_minimized_case,
)
from tools.timing_explorer import IcarusSimTransport, ProtocolClient  # noqa: E402

CMD_POOL: Tuple[int, ...] = (0x04, 0x05, 0x06, 0x9F, 0x02, 0x35, 0xAB, 0xB9)
MAX_GAP = 15
SAFE_FIXED_GAP = 7
ALL_BUG_CLASSES = (
    "D1_wren_fast_drop",
    "D2_wakeup_slow_recovery",
    "D3_prog_wren_timeout",
    "D4_fast_read_corrupt",
)


def classify_multispec_divergence(ev: Dict[str, Any]) -> str:
    """Post-hoc classification of a discovered divergence into canonical bug class."""
    ref_b = ev["ref_bytes"]
    dut_b = ev["dut_bytes"]
    gap = ev["gap_wait"]
    for rb, db in zip(ref_b, dut_b):
        if db == 0x99 and rb != 0x99:
            return "D4_fast_read_corrupt"
    for rb, db in zip(ref_b, dut_b):
        if db == 0xFF and rb != 0xFF:
            return "D2_wakeup_slow_recovery"
    for rb, db in zip(ref_b, dut_b):
        if db == 0x80 and rb != 0x80 and gap >= 10:
            return "D3_prog_wren_timeout"
    if gap <= 2 and (0x06 in ev["commands"]):
        return "D1_wren_fast_drop"
    return "other_divergence"


def _obs_signature(ev: Dict[str, Any]) -> Tuple[Any, ...]:
    """Strictly observable pin-level response signature (no internal state)."""
    return (
        tuple(ev["ref_bytes"]),
        tuple(ev["dut_bytes"]),
        ev["ref_cycles"] // 8,
        ev["dut_cycles"] // 8,
        bool(ev["diverged"]),
    )


def run_strategy_seed(
    transport: IcarusSimTransport,
    client: ProtocolClient,
    *,
    strategy: str,
    budget: int,
    seed: int,
) -> Dict[str, Any]:
    """Run one fuzzer strategy for `budget` evaluations under `seed` on MODE_SPI_MULTISPEC."""
    rng = random.Random(seed)
    tested: Set[Tuple[Tuple[int, ...], int]] = set()
    seen_sigs: Set[Tuple[Any, ...]] = set()
    seen_byte_states: Set[Tuple[int, int]] = set()
    corpus: List[Dict[str, Any]] = []
    seen_div_symptoms: Set[Tuple[Tuple[int, ...], Tuple[int, ...]]] = set()

    trials: List[Dict[str, Any]] = []
    first_bug_trial: Dict[str, int] = {}
    bug_examples: Dict[str, Dict[str, Any]] = {}
    unique_div_symptoms = 0

    def sample_random_pair() -> Tuple[Tuple[int, ...], int]:
        length = rng.choice((2, 3))
        cmds = tuple(rng.choice(CMD_POOL) for _ in range(length))
        gap = SAFE_FIXED_GAP if strategy == "payload_only" else rng.randint(0, MAX_GAP)
        return cmds, gap

    def pick_guided_candidate(step: int) -> Tuple[Tuple[int, ...], int]:
        # Initial exploration or 20% baseline exploration
        if step < min(12, budget // 5) or not corpus or rng.random() < 0.22:
            for _ in range(16):
                cand = sample_random_pair()
                if cand not in tested:
                    return cand
            return sample_random_pair()

        # Select an interesting corpus entry biased toward high score & lower mutation count
        weights = [max(0.05, entry["score"] / (1.0 + 0.6 * entry["uses"])) for entry in corpus]
        seed_entry = rng.choices(corpus, weights=weights, k=1)[0]
        seed_entry["uses"] += 1
        base_cmds: Tuple[int, ...] = seed_entry["cmds"]
        base_gap: int = seed_entry["gap"]
        active_idx: int = seed_entry["active_idx"]

        for _ in range(32):
            op = rng.random()
            if op < 0.35:
                # Mutate timing gap (0 reload cost for same cmds!): probe fast & slow boundary extremes
                edge_pool = (0, 1, 2, 3, 5, 6, 10, 11, 13, 15, max(0, base_gap - 3), min(MAX_GAP, base_gap + 4))
                new_gap = rng.choice(edge_pool) if rng.random() < 0.8 else rng.randint(0, MAX_GAP)
                cand = (base_cmds, new_gap)
            elif op < 0.78:
                # Preserve state-reaching prefix up to active_idx and explore next opcode + boundary gaps
                new_list = list(base_cmds)
                mutate_start = min(len(new_list) - 1, max(1, active_idx))
                pos = rng.randrange(mutate_start, len(new_list))
                new_list[pos] = rng.choice(CMD_POOL)
                new_gap = (
                    rng.choice((0, 1, 2, 6, 10, 11, 15))
                    if rng.random() < 0.7
                    else rng.randint(0, MAX_GAP)
                )
                cand = (tuple(new_list), new_gap)
            else:
                # Chain extension (2 -> 3) or 2-step mutated suffix after active prefix
                prefix = base_cmds[: max(1, active_idx)]
                need = 3 - len(prefix)
                new_cmds = prefix + tuple(rng.choice(CMD_POOL) for _ in range(need))
                new_gap = rng.choice((0, 1, 2, 5, 10, 12, 15)) if rng.random() < 0.7 else rng.randint(0, MAX_GAP)
                cand = (new_cmds, new_gap)

            if cand not in tested:
                return cand

        return sample_random_pair()

    raw_divergences = 0
    for step in range(budget):
        if strategy in ("payload_only", "joint_random"):
            cmds, gap = sample_random_pair()
        elif strategy == "guided_feedback":
            cmds, gap = pick_guided_candidate(step)
        else:
            raise ValueError(f"Unknown strategy: {strategy!r}")

        tested.add((cmds, gap))
        ev = eval_ref_vs_dut(
            transport,
            client,
            cmds,
            gap,
            ref_thresh=0,
            dut_thresh=1,
            target_mode=5,
            capture_per_cmd=True,
            use_sync=True,
        )
        sig = _obs_signature(ev)
        is_new_sig = sig not in seen_sigs
        seen_sigs.add(sig)

        # Count new pin-response byte pairs discovered and identify earliest active byte index
        new_byte_pair = False
        new_active_idx: Optional[int] = None
        for idx_b, (rb, db) in enumerate(zip(ev["ref_bytes"], ev["dut_bytes"])):
            if (rb, db) not in seen_byte_states:
                seen_byte_states.add((rb, db))
                new_byte_pair = True
                if (rb != 0 or db != 0) and new_active_idx is None:
                    new_active_idx = idx_b

        div_sym = (tuple(ev["ref_bytes"]), tuple(ev["dut_bytes"]))
        is_new_div_sym = False
        if ev["diverged"]:
            raw_divergences += 1
            if div_sym not in seen_div_symptoms:
                seen_div_symptoms.add(div_sym)
                unique_div_symptoms += 1
                is_new_div_sym = True
            bug_cls = classify_multispec_divergence(ev)
            if bug_cls not in first_bug_trial:
                first_bug_trial[bug_cls] = step + 1
                bug_examples[bug_cls] = {
                    "trial_index": step + 1,
                    "commands": list(cmds),
                    "commands_hex": ev["commands_hex"],
                    "gap_wait": gap,
                    "ref_bytes_hex": [f"0x{b:02x}" for b in ev["ref_bytes"]],
                    "dut_bytes_hex": [f"0x{b:02x}" for b in ev["dut_bytes"]],
                }
        else:
            bug_cls = None

        if strategy == "guided_feedback":
            # Keep corpus focused on frontier discoveries (new observable pin byte states or new divergence classes)
            if is_new_div_sym or new_byte_pair:
                non_idle_indices = [
                    i for i, (rb, db) in enumerate(zip(ev["ref_bytes"], ev["dut_bytes"])) if (rb != 0 or db != 0)
                ]
                act_idx = new_active_idx if new_active_idx is not None else (non_idle_indices[0] if non_idle_indices else 1)
                score = 4.0 if is_new_div_sym else (2.5 if non_idle_indices else 0.5)
                corpus.append({"cmds": cmds, "gap": gap, "score": score, "uses": 0, "active_idx": act_idx})

        trials.append(
            {
                "step": step + 1,
                "commands_hex": ev["commands_hex"],
                "gap_wait": gap,
                "ref_bytes": ev["ref_bytes"],
                "dut_bytes": ev["dut_bytes"],
                "diverged": ev["diverged"],
                "bug_class": bug_cls,
            }
        )

    return {
        "strategy": strategy,
        "seed": seed,
        "budget": budget,
        "unique_pairs_tested": len(tested),
        "unique_obs_signatures": len(seen_sigs),
        "raw_divergences": raw_divergences,
        "unique_bug_classes_found": len(first_bug_trial),
        "bug_classes_found": sorted(first_bug_trial.keys()),
        "first_bug_trial": first_bug_trial,
        "unique_div_symptoms": unique_div_symptoms,
        "bug_examples": bug_examples,
    }


def run_benchmark(
    *,
    budget: int = 120,
    seeds: Sequence[int] = (20261101, 20261102, 20261103, 20261104, 20261105),
    replay_repeats: int = 5,
) -> Dict[str, Any]:
    strategies = ("payload_only", "joint_random", "guided_feedback")
    runs_by_strategy: Dict[str, List[Dict[str, Any]]] = {s: [] for s in strategies}
    best_examples: Dict[str, Dict[str, Any]] = {}

    t0 = time.time()
    with IcarusSimTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)

        for seed in seeds:
            for strat in strategies:
                res = run_strategy_seed(
                    transport, client, strategy=strat, budget=budget, seed=seed
                )
                runs_by_strategy[strat].append(res)
                for bcls, ex in res["bug_examples"].items():
                    if bcls not in best_examples:
                        best_examples[bcls] = ex
                print(
                    f"[progress] seed={seed} strategy={strat} bugs={res['unique_bug_classes_found']}/4 "
                    f"sigs={res['unique_obs_signatures']} raw_divs={res['raw_divergences']} elapsed={time.time()-t0:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )

        # Replay + minimize one discovered representative per canonical bug class
        minimized_examples: Dict[str, Any] = {}
        for bcls, ex in sorted(best_examples.items()):
            minimized = minimize_divergence(
                transport,
                client,
                ex["commands"],
                ex["gap_wait"],
                max_gap=MAX_GAP,
                ref_thresh=0,
                dut_thresh=1,
                target_mode=5,
                capture_per_cmd=True,
            )
            fail_gap = minimized["highest_failing_gap_wait"]
            if fail_gap is None:
                fail_gap = ex["gap_wait"]
            replay = replay_minimized_case(
                transport,
                client,
                minimized["minimized_commands"],
                ex["gap_wait"],
                repeats=replay_repeats,
                ref_thresh=0,
                dut_thresh=1,
                target_mode=5,
                capture_per_cmd=True,
            )
            minimized_examples[bcls] = {
                "discovered_example": ex,
                "minimized": minimized,
                "replay_at_discovered_gap": replay,
            }

    elapsed_s = round(time.time() - t0, 2)
    summary: Dict[str, Dict[str, Any]] = {}
    for strat in strategies:
        runs = runs_by_strategy[strat]
        n = len(runs)
        unique_bugs = [r["unique_bug_classes_found"] for r in runs]
        obs_sigs = [r["unique_obs_signatures"] for r in runs]
        raw_divs = [r["raw_divergences"] for r in runs]
        full_cov = sum(1 for u in unique_bugs if u == len(ALL_BUG_CLASSES))

        mean_ttd: Dict[str, Optional[float]] = {}
        hit_rate: Dict[str, float] = {}
        for bcls in ALL_BUG_CLASSES:
            hits = [r["first_bug_trial"][bcls] for r in runs if bcls in r["first_bug_trial"]]
            hit_rate[bcls] = len(hits) / n if n else 0.0
            mean_ttd[bcls] = round(statistics.mean(hits), 1) if hits else None

        summary[strat] = {
            "seeds": n,
            "budget": budget,
            "mean_unique_bugs": round(statistics.mean(unique_bugs), 2),
            "min_unique_bugs": min(unique_bugs),
            "max_unique_bugs": max(unique_bugs),
            "seeds_finding_all_4": full_cov,
            "mean_obs_signatures": round(statistics.mean(obs_sigs), 1),
            "mean_raw_divergences": round(statistics.mean(raw_divs), 1),
            "bug_hit_rate": hit_rate,
            "mean_trials_to_discover": mean_ttd,
        }

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "rtl_simulation_feedback_fuzz_benchmark",
        "elapsed_seconds": elapsed_s,
        "budget_per_seed": budget,
        "seeds": list(seeds),
        "bug_classes": list(ALL_BUG_CLASSES),
        "summary_by_strategy": summary,
        "per_seed_runs": runs_by_strategy,
        "minimized_and_replayed_examples": minimized_examples,
    }


def format_benchmark_markdown(report: Dict[str, Any]) -> str:
    s = report["summary_by_strategy"]
    lines = [
        "# Multi-Seed Black-Box Feedback-Guided Fuzzing Benchmark",
        "",
        f"- **Timestamp (UTC):** {report['timestamp_utc']}",
        f"- **Evidence class:** `{report['evidence_class']}`",
        f"- **Target:** `MODE_SPI_MULTISPEC` Reference (`delay_ns=0`) vs Buggy DUT (`delay_ns=1`), 4 distinct timing+state bugs (`D1..D4`), 8 SPI opcodes, inter-CS gap `WAIT_PARAM in 0..15`",
        f"- **Design rule:** Strict black-box feedback using only pin responses (`ref_bytes`, `dut_bytes`, `cycle_cnt // 8`, `diverged`) over real UART `OP_PATCH_WORD`/`OP_START` frames",
        f"- **Scale:** `{report['budget_per_seed']}` trials/seed x `{len(report['seeds'])}` seeds (`{report['seeds']}`) in `{report['elapsed_seconds']}s`",
        "",
        "## Strategy Comparison (Matched Trial Budget)",
        "",
        "| Strategy | Mean Unique Bugs (`0..4`) | Range `[min, max]` | Seeds Finding `4/4` | Mean Obs. Pin Signatures | Mean Raw Divergences |",
        "|---|---|---|---|---|---|",
    ]
    for strat in ("payload_only", "joint_random", "guided_feedback"):
        r = s[strat]
        lines.append(
            f"| `{strat}` | **{r['mean_unique_bugs']:.2f}** | `[{r['min_unique_bugs']}, {r['max_unique_bugs']}]` | `{r['seeds_finding_all_4']}/{r['seeds']}` | {r['mean_obs_signatures']:.1f} | {r['mean_raw_divergences']:.1f} |"
        )

    lines += [
        "",
        "## Per-Bug Discovery Rate & Mean Trials-to-Discover (TTD)",
        "",
        "| Bug Class | `payload_only` Hit Rate | `joint_random` Hit Rate (Mean TTD) | `guided_feedback` Hit Rate (Mean TTD) |",
        "|---|---|---|---|",
    ]
    for bcls in report["bug_classes"]:
        po = f"`{s['payload_only']['bug_hit_rate'][bcls]*100:.0f}%`"
        jr_r = s["joint_random"]["bug_hit_rate"][bcls] * 100
        jr_t = s["joint_random"]["mean_trials_to_discover"][bcls]
        jr = f"`{jr_r:.0f}%` ({jr_t if jr_t is not None else '-'})"
        gf_r = s["guided_feedback"]["bug_hit_rate"][bcls] * 100
        gf_t = s["guided_feedback"]["mean_trials_to_discover"][bcls]
        gf = f"`{gf_r:.0f}%` ({gf_t if gf_t is not None else '-'})"
        lines.append(f"| `{bcls}` | {po} | {jr} | {gf} |")

    lines += [
        "",
        "## Minimized & Replayed Counterexamples",
        "",
        "| Bug Class | Discovered Sequence (`gap_wait`) | Minimized Sequence | Window Failing/Passing | Replay Rate |",
        "|---|---|---|---|---|",
    ]
    for bcls, item in sorted(report["minimized_and_replayed_examples"].items()):
        ex = item["discovered_example"]
        m = item["minimized"]
        rep = item["replay_at_discovered_gap"]
        if m.get("lowest_passing_above_fail") is not None and m.get("highest_passing_below_fail") is not None:
            win = (
                f"gap `{ex['gap_wait']}` (local fail `[{m['lowest_failing_gap_wait']}, {m['highest_failing_gap_wait']}]`; "
                f"safe `[{m['highest_passing_below_fail']}..{m['lowest_passing_above_fail']}]`)"
            )
        elif m.get("lowest_passing_above_fail") is not None:
            win = f"gap `{ex['gap_wait']}` (fails `<= {m['highest_failing_gap_wait']}`, passes `>= {m['lowest_passing_above_fail']}`)"
        elif m.get("highest_passing_below_fail") is not None:
            win = f"gap `{ex['gap_wait']}` (passes `<= {m['highest_passing_below_fail']}`, fails `>= {m['lowest_failing_gap_wait']}`)"
        else:
            win = f"gap `{ex['gap_wait']}` (window `[{m.get('lowest_failing_gap_wait')}, {m['highest_failing_gap_wait']}]`)"
        lines.append(
            f"| `{bcls}` | `{' -> '.join(ex['commands_hex'])}` (`gap={ex['gap_wait']}`) | `{' -> '.join(m['minimized_commands_hex'])}` | {win} | `{rep['deterministic_divergence_matches']}/{rep['repeats']}` |"
        )

    return "\n".join(lines) + "\n"


def save_benchmark_plot(report: Dict[str, Any], out_png: Path) -> None:
    import matplotlib.pyplot as plt

    s = report["summary_by_strategy"]
    strats = ["payload_only", "joint_random", "guided_feedback"]
    labels = ["Payload-Only\n(fixed gap=7)", "Joint Random\n(blind sampling)", "Guided Feedback\n(black-box corpus)"]
    means = [s[k]["mean_unique_bugs"] for k in strats]
    sigs = [s[k]["mean_obs_signatures"] for k in strats]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.2), dpi=160)
    colors = ["#8a94a6", "#4c78a8", "#2b9348"]

    bars1 = ax1.bar(labels, means, color=colors, width=0.56)
    ax1.set_ylim(0, 4.4)
    ax1.set_ylabel("Mean Unique Bugs Found (out of 4)")
    ax1.set_title(f"Unique Divergences ({report['budget_per_seed']} trials/seed, {len(report['seeds'])} seeds)")
    ax1.grid(axis="y", alpha=0.3)
    for b, val in zip(bars1, means):
        ax1.text(b.get_x() + b.get_width() / 2, val + 0.08, f"{val:.2f}", ha="center", va="bottom", fontweight="bold")

    bars2 = ax2.bar(labels, sigs, color=colors, width=0.56)
    ax2.set_ylabel("Mean Observable Pin Signatures")
    ax2.set_title("Observable Pin State Coverage")
    ax2.grid(axis="y", alpha=0.3)
    for b, val in zip(bars2, sigs):
        ax2.text(b.get_x() + b.get_width() / 2, val + 0.5, f"{val:.1f}", ha="center", va="bottom", fontweight="bold")

    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run multi-seed feedback fuzzing benchmark on RTL")
    parser.add_argument("--budget", type=int, default=120)
    parser.add_argument("--seeds", type=int, default=5, help="Number of consecutive seeds starting at 20261101")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--png-out", type=Path)
    args = parser.parse_args()

    seed_list = [20261101 + i for i in range(args.seeds)]
    rep = run_benchmark(budget=args.budget, seeds=seed_list, replay_repeats=args.repeats)
    md = format_benchmark_markdown(rep)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rep, indent=2, sort_keys=True), encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    if args.png_out:
        save_benchmark_plot(rep, args.png_out)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
