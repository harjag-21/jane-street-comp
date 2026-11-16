# SPDX-License-Identifier: Apache-2.0
"""Experiment 7 (20-seed Benchmark + Diversity Strategy + Held-out Bug):
Compare payload_only, joint_random, guided_feedback (v1), and guided_diverse
under matched trial budgets on real Icarus Verilog RTL co-simulation."""

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
BASE_BUG_CLASSES = (
    "D1_wren_fast_drop",
    "D2_wakeup_slow_recovery",
    "D3_prog_wren_timeout",
    "D4_fast_read_corrupt",
)
ALL_BUG_CLASSES_HELD = BASE_BUG_CLASSES + ("D5_cfg_rd_wrdi_race",)


def bug_classes_for_mode(target_mode: int) -> Tuple[str, ...]:
    return ALL_BUG_CLASSES_HELD if target_mode == 7 else BASE_BUG_CLASSES


def classify_multispec_divergence(ev: Dict[str, Any]) -> str:
    """Post-hoc classification of a discovered divergence into canonical bug class."""
    ref_b = ev["ref_bytes"]
    dut_b = ev["dut_bytes"]
    gap = ev["gap_wait"]
    for rb, db in zip(ref_b, dut_b):
        if db == 0x66 and rb != 0x66:
            return "D5_cfg_rd_wrdi_race"
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
    target_mode: int = 5,
) -> Dict[str, Any]:
    """Run one fuzzer strategy for `budget` evaluations under `seed`."""
    rng = random.Random(seed)
    tested: Set[Tuple[Tuple[int, ...], int]] = set()
    seen_sigs: Set[Tuple[Any, ...]] = set()
    seen_byte_states: Set[Tuple[int, int]] = set()
    seen_div_symptoms: Set[Tuple[Tuple[int, ...], Tuple[int, ...]]] = set()

    # Guided v1 flat corpus
    corpus: List[Dict[str, Any]] = []

    # Guided diverse bucketed corpus (strictly black-box behavioral buckets)
    buckets: Dict[Tuple[int, int, bool], List[Dict[str, Any]]] = {}
    bucket_uses: Dict[Tuple[int, int, bool], int] = {}
    cmd0_counts: Dict[int, int] = {op: 0 for op in CMD_POOL}

    first_bug_trial: Dict[str, int] = {}
    bug_examples: Dict[str, Dict[str, Any]] = {}
    unique_div_symptoms = 0
    raw_divergences = 0

    def sample_random_pair(*, balance_prefix: bool = False) -> Tuple[Tuple[int, ...], int]:
        length = rng.choice((2, 3))
        if balance_prefix:
            # Inverse-count sampling over cmd0 ensures underrepresented commands get explored
            w0 = [1.0 / (1.0 + cmd0_counts[op]) for op in CMD_POOL]
            c0 = rng.choices(CMD_POOL, weights=w0, k=1)[0]
            cmds = (c0,) + tuple(rng.choice(CMD_POOL) for _ in range(length - 1))
        else:
            cmds = tuple(rng.choice(CMD_POOL) for _ in range(length))
        gap = SAFE_FIXED_GAP if strategy == "payload_only" else rng.randint(0, MAX_GAP)
        return cmds, gap

    def pick_guided_v1(step: int) -> Tuple[Tuple[int, ...], int]:
        if step < min(12, budget // 5) or not corpus or rng.random() < 0.22:
            for _ in range(16):
                cand = sample_random_pair()
                if cand not in tested:
                    return cand
            return sample_random_pair()

        weights = [max(0.05, entry["score"] / (1.0 + 0.6 * entry["uses"])) for entry in corpus]
        seed_entry = rng.choices(corpus, weights=weights, k=1)[0]
        seed_entry["uses"] += 1
        base_cmds: Tuple[int, ...] = seed_entry["cmds"]
        base_gap: int = seed_entry["gap"]
        active_idx: int = seed_entry["active_idx"]

        for _ in range(32):
            op = rng.random()
            if op < 0.35:
                edge_pool = (0, 1, 2, 3, 5, 6, 10, 11, 13, 15, max(0, base_gap - 3), min(MAX_GAP, base_gap + 4))
                new_gap = rng.choice(edge_pool) if rng.random() < 0.8 else rng.randint(0, MAX_GAP)
                cand = (base_cmds, new_gap)
            elif op < 0.78:
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
                prefix = base_cmds[: max(1, active_idx)]
                need = 3 - len(prefix)
                new_cmds = prefix + tuple(rng.choice(CMD_POOL) for _ in range(need))
                new_gap = rng.choice((0, 1, 2, 5, 10, 12, 15)) if rng.random() < 0.7 else rng.randint(0, MAX_GAP)
                cand = (new_cmds, new_gap)
            if cand not in tested:
                return cand
        return sample_random_pair()

    def _generic_perturbed_gap(base_gap: int) -> int:
        """Protocol-agnostic timing spread over [0..15] without bug-tuned constants."""
        r = rng.random()
        if r < 0.55:
            return rng.randint(0, MAX_GAP)
        if r < 0.80:
            delta = rng.choice((-4, -3, -2, -1, 1, 2, 3, 4))
            return max(0, min(MAX_GAP, base_gap + delta))
        # Symmetric reflection / opposite half of window
        return rng.choice((0, MAX_GAP, MAX_GAP - base_gap, base_gap // 2, (base_gap + MAX_GAP) // 2))

    def pick_guided_diverse(step: int) -> Tuple[Tuple[int, ...], int]:
        # Maintain healthy prefix diversity: warmup + 28% balanced fresh exploration
        if step < min(16, budget // 5) or not buckets or rng.random() < 0.28:
            for _ in range(16):
                cand = sample_random_pair(balance_prefix=True)
                if cand not in tested:
                    return cand
            return sample_random_pair(balance_prefix=True)

        # Pick behavioral bucket with quadratic cooldown & strong preference for non-diverged stateful frontiers
        b_keys = list(buckets.keys())
        b_weights: List[float] = []
        for bk in b_keys:
            _, first_nz, is_div = bk
            # Non-diverged non-zero protocol states are prioritized over already-broken D1-like buckets
            base_w = 0.6 if is_div else (3.5 if first_nz != 0 else 1.0)
            uses = bucket_uses.get(bk, 0)
            b_weights.append(base_w / ((1.0 + uses) ** 2))

        chosen_bk = rng.choices(b_keys, weights=b_weights, k=1)[0]
        bucket_uses[chosen_bk] = bucket_uses.get(chosen_bk, 0) + 1

        entries = buckets[chosen_bk]
        seed_entry = min(entries, key=lambda e: (e["uses"], rng.random()))
        seed_entry["uses"] += 1
        base_cmds: Tuple[int, ...] = seed_entry["cmds"]
        base_gap: int = seed_entry["gap"]
        active_idx: int = seed_entry["active_idx"]

        for _ in range(32):
            op = rng.random()
            if op < 0.35:
                # Explore timing envelope for the selected prefix (uniform + symmetric spread)
                cand = (base_cmds, _generic_perturbed_gap(base_gap))
            elif op < 0.78:
                # Preserve active prefix up to active_idx, mutate downstream command, vary gap uniformly
                new_list = list(base_cmds)
                mutate_start = min(len(new_list) - 1, max(1, active_idx))
                pos = rng.randrange(mutate_start, len(new_list))
                new_list[pos] = rng.choice(CMD_POOL)
                cand = (tuple(new_list), _generic_perturbed_gap(base_gap))
            else:
                # Extend depth 2 -> 3 or resample downstream pair from active prefix
                prefix = base_cmds[: max(1, active_idx)]
                need = 3 - len(prefix)
                new_cmds = prefix + tuple(rng.choice(CMD_POOL) for _ in range(need))
                cand = (new_cmds, rng.randint(0, MAX_GAP))
            if cand not in tested:
                return cand
        return sample_random_pair(balance_prefix=True)

    for step in range(budget):
        if strategy in ("payload_only", "joint_random"):
            cmds, gap = sample_random_pair()
        elif strategy == "guided_feedback":
            cmds, gap = pick_guided_v1(step)
        elif strategy == "guided_diverse":
            cmds, gap = pick_guided_diverse(step)
        else:
            raise ValueError(f"Unknown strategy: {strategy!r}")

        tested.add((cmds, gap))
        cmd0_counts[cmds[0]] = cmd0_counts.get(cmds[0], 0) + 1
        ev = eval_ref_vs_dut(
            transport,
            client,
            cmds,
            gap,
            ref_thresh=0,
            dut_thresh=1,
            target_mode=target_mode,
            capture_per_cmd=True,
            use_sync=True,
        )
        sig = _obs_signature(ev)
        seen_sigs.add(sig)

        new_byte_pair = False
        new_active_idx: Optional[int] = None
        first_nz_byte = 0
        for idx_b, (rb, db) in enumerate(zip(ev["ref_bytes"], ev["dut_bytes"])):
            if first_nz_byte == 0 and (rb != 0 or db != 0):
                first_nz_byte = rb if rb != 0 else db
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

        non_idle_indices = [
            i for i, (rb, db) in enumerate(zip(ev["ref_bytes"], ev["dut_bytes"])) if (rb != 0 or db != 0)
        ]
        act_idx = new_active_idx if new_active_idx is not None else (non_idle_indices[0] if non_idle_indices else 1)

        if strategy == "guided_feedback":
            if is_new_div_sym or new_byte_pair:
                score = 4.0 if is_new_div_sym else (2.5 if non_idle_indices else 0.5)
                corpus.append({"cmds": cmds, "gap": gap, "score": score, "uses": 0, "active_idx": act_idx})
        elif strategy == "guided_diverse":
            if is_new_div_sym or new_byte_pair:
                bk = (cmds[0], first_nz_byte, bool(ev["diverged"]))
                lst = buckets.setdefault(bk, [])
                if len(lst) < 3:
                    lst.append({"cmds": cmds, "gap": gap, "uses": 0, "active_idx": act_idx})

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


def _worker_run_seed(
    args: Tuple[int, Tuple[str, ...], int, int],
) -> Tuple[int, Dict[str, Dict[str, Any]]]:
    seed, strategies, budget, target_mode = args
    results: Dict[str, Dict[str, Any]] = {}
    with IcarusSimTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)
        for strat in strategies:
            results[strat] = run_strategy_seed(
                transport,
                client,
                strategy=strat,
                budget=budget,
                seed=seed,
                target_mode=target_mode,
            )
    return seed, results


def _bootstrap_paired_ci(
    deltas: Sequence[float], *, resamples: int = 10000, seed: int = 42
) -> Dict[str, Any]:
    n = len(deltas)
    if n == 0:
        return {"mean_delta": 0.0, "ci95_lo": 0.0, "ci95_hi": 0.0}
    rng = random.Random(seed)
    boot_means = sorted(
        sum(deltas[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(resamples)
    )
    lo = boot_means[int(0.025 * resamples)]
    hi = boot_means[int(0.975 * resamples)]
    return {
        "mean_delta": round(sum(deltas) / n, 4),
        "ci95_lo": round(lo, 4),
        "ci95_hi": round(hi, 4),
    }


def run_benchmark(
    *,
    budget: int = 100,
    seeds: Sequence[int] = tuple(20261101 + i for i in range(20)),
    replay_repeats: int = 5,
    target_mode: int = 7,
    workers: int = 8,
    strategies: Tuple[str, ...] = ("payload_only", "joint_random", "guided_feedback", "guided_diverse"),
) -> Dict[str, Any]:
    runs_by_strategy: Dict[str, List[Dict[str, Any]]] = {s: [] for s in strategies}
    best_examples: Dict[str, Dict[str, Any]] = {}
    all_bugs = bug_classes_for_mode(target_mode)

    t0 = time.time()
    per_seed_map: Dict[int, Dict[str, Dict[str, Any]]] = {}
    tasks = [(s, strategies, budget, target_mode) for s in seeds]

    if workers <= 1 or len(tasks) <= 1:
        with IcarusSimTransport() as transport:
            transport.reset(1)
            client = ProtocolClient(transport)
            for idx, seed in enumerate(seeds, 1):
                per_seed_map[seed] = {}
                for strat in strategies:
                    res = run_strategy_seed(
                        transport, client, strategy=strat, budget=budget, seed=seed, target_mode=target_mode
                    )
                    per_seed_map[seed][strat] = res
                print(
                    f"[seed {idx}/{len(seeds)}] seed={seed} elapsed={time.time()-t0:.1f}s "
                    + " ".join(f"{st}={per_seed_map[seed][st]['unique_bug_classes_found']}" for st in strategies),
                    file=sys.stderr,
                    flush=True,
                )
    else:
        max_w = min(workers, len(tasks))
        with ProcessPoolExecutor(max_workers=max_w) as pool:
            futs = [pool.submit(_worker_run_seed, t) for t in tasks]
            done = 0
            for fut in as_completed(futs):
                seed, res_dict = fut.result()
                per_seed_map[seed] = res_dict
                done += 1
                print(
                    f"[seed {done}/{len(seeds)}] seed={seed} elapsed={time.time()-t0:.1f}s "
                    + " ".join(f"{st}={res_dict[st]['unique_bug_classes_found']}" for st in strategies),
                    file=sys.stderr,
                    flush=True,
                )

    for seed in seeds:
        for strat in strategies:
            res = per_seed_map[seed][strat]
            runs_by_strategy[strat].append(res)
            for bcls, ex in res["bug_examples"].items():
                if bcls not in best_examples:
                    best_examples[bcls] = ex

    # Replay + minimize one discovered representative per canonical bug class
    minimized_examples: Dict[str, Any] = {}
    with IcarusSimTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)
        for bcls, ex in sorted(best_examples.items()):
            minimized = minimize_divergence(
                transport,
                client,
                ex["commands"],
                ex["gap_wait"],
                max_gap=MAX_GAP,
                ref_thresh=0,
                dut_thresh=1,
                target_mode=target_mode,
                capture_per_cmd=True,
            )
            replay = replay_minimized_case(
                transport,
                client,
                minimized["minimized_commands"],
                ex["gap_wait"],
                repeats=replay_repeats,
                ref_thresh=0,
                dut_thresh=1,
                target_mode=target_mode,
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
        full_cov = sum(1 for u in unique_bugs if u == len(all_bugs))

        mean_ttd: Dict[str, Optional[float]] = {}
        hit_rate: Dict[str, float] = {}
        for bcls in all_bugs:
            hits = [r["first_bug_trial"][bcls] for r in runs if bcls in r["first_bug_trial"]]
            hit_rate[bcls] = len(hits) / n if n else 0.0
            mean_ttd[bcls] = round(statistics.mean(hits), 1) if hits else None

        summary[strat] = {
            "seeds": n,
            "budget": budget,
            "mean_unique_bugs": round(statistics.mean(unique_bugs), 3),
            "std_unique_bugs": round(statistics.stdev(unique_bugs), 3) if n > 1 else 0.0,
            "median_unique_bugs": statistics.median(unique_bugs),
            "min_unique_bugs": min(unique_bugs),
            "max_unique_bugs": max(unique_bugs),
            "seeds_finding_all": full_cov,
            "mean_obs_signatures": round(statistics.mean(obs_sigs), 2),
            "mean_raw_divergences": round(statistics.mean(raw_divs), 2),
            "bug_hit_rate": hit_rate,
            "mean_trials_to_discover": mean_ttd,
        }

    # Paired comparisons across identical seeds
    paired: Dict[str, Dict[str, Any]] = {}
    pairs = [
        ("guided_feedback", "joint_random"),
        ("guided_diverse", "joint_random"),
        ("guided_diverse", "guided_feedback"),
    ]
    for a, b in pairs:
        if a in runs_by_strategy and b in runs_by_strategy:
            deltas = [
                per_seed_map[s][a]["unique_bug_classes_found"]
                - per_seed_map[s][b]["unique_bug_classes_found"]
                for s in seeds
            ]
            sig_deltas = [
                per_seed_map[s][a]["unique_obs_signatures"]
                - per_seed_map[s][b]["unique_obs_signatures"]
                for s in seeds
            ]
            wins = sum(1 for d in deltas if d > 0)
            ties = sum(1 for d in deltas if d == 0)
            losses = sum(1 for d in deltas if d < 0)
            paired[f"{a}_vs_{b}"] = {
                "bugs_delta": _bootstrap_paired_ci(deltas),
                "sigs_delta": _bootstrap_paired_ci(sig_deltas),
                "wins": wins,
                "ties": ties,
                "losses": losses,
            }

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "rtl_simulation_feedback_fuzz_benchmark",
        "target_mode": target_mode,
        "elapsed_seconds": elapsed_s,
        "budget_per_seed": budget,
        "seeds": list(seeds),
        "strategies": list(strategies),
        "bug_classes": list(all_bugs),
        "summary_by_strategy": summary,
        "paired_comparisons": paired,
        "per_seed_runs": runs_by_strategy,
        "minimized_and_replayed_examples": minimized_examples,
    }


def format_benchmark_markdown(report: Dict[str, Any]) -> str:
    s = report["summary_by_strategy"]
    all_bugs = report["bug_classes"]
    max_b = len(all_bugs)
    mode_desc = (
        "`MODE_SPI_HELD` (`D1..D4` + held-out interior bug `D5_cfg_rd_wrdi_race`)"
        if report.get("target_mode") == 7
        else "`MODE_SPI_MULTISPEC` (`D1..D4`)"
    )
    lines = [
        f"# {len(report['seeds'])}-Seed Matched Black-Box Feedback Fuzzing Benchmark",
        "",
        f"- **Timestamp (UTC):** {report['timestamp_utc']}",
        f"- **Evidence class:** `{report['evidence_class']}`",
        f"- **Target:** {mode_desc}, 8 SPI opcodes, inter-CS gap `WAIT_PARAM in 0..15`",
        f"- **Design rule:** Strict black-box pin feedback (`ref_bytes`, `dut_bytes`, `cycle_cnt // 8`, `diverged`) over real UART frames; `guided_diverse` adds behavioral-bucket cooldown & prefix balance without bug-specific constants.",
        f"- **Scale:** `{report['budget_per_seed']}` trials/seed x `{len(report['seeds'])}` seeds (`{report['seeds'][0]}..{report['seeds'][-1]}`) in `{report['elapsed_seconds']}s`",
        "",
        "## Strategy Comparison (Matched Trial Budget)",
        "",
        f"| Strategy | Mean +/- SD Bugs (`0..{max_b}`) | Median `[min, max]` | Seeds Finding `{max_b}/{max_b}` | Mean Obs. Pin Signatures | Mean Raw Divergences |",
        "|---|---|---|---|---|---|",
    ]
    for strat in report["strategies"]:
        r = s[strat]
        lines.append(
            f"| `{strat}` | **{r['mean_unique_bugs']:.2f}** +/- {r['std_unique_bugs']:.2f} | "
            f"`{r['median_unique_bugs']:.1f}` `[{r['min_unique_bugs']}, {r['max_unique_bugs']}]` | "
            f"`{r['seeds_finding_all']}/{r['seeds']}` | {r['mean_obs_signatures']:.1f} | {r['mean_raw_divergences']:.1f} |"
        )

    lines += [
        "",
        "## Paired Seed-Level Statistical Comparisons (`10,000` Bootstrap Resamples)",
        "",
        "| Pair (`A - B`) | Mean Bug Delta (95% CI) | Mean Sig Delta (95% CI) | Seed Win / Tie / Loss |",
        "|---|---|---|---|",
    ]
    for k, pv in report["paired_comparisons"].items():
        bd = pv["bugs_delta"]
        sd = pv["sigs_delta"]
        lines.append(
            f"| `{k}` | **{bd['mean_delta']:+.2f}** `[{bd['ci95_lo']:+.2f}, {bd['ci95_hi']:+.2f}]` | "
            f"{sd['mean_delta']:+.1f} `[{sd['ci95_lo']:+.1f}, {sd['ci95_hi']:+.1f}]` | "
            f"`{pv['wins']}W / {pv['ties']}T / {pv['losses']}L` |"
        )

    lines += [
        "",
        "## Per-Bug Discovery Rate & Mean Trials-to-Discover (TTD)",
        "",
        "| Bug Class | Role | " + " | ".join(f"`{st}`" for st in report["strategies"]) + " |",
        "|---|---|" + "|".join(["---"] * len(report["strategies"])) + "|",
    ]
    for bcls in all_bugs:
        role = "**HELD-OUT**" if bcls.startswith("D5_") else "Base"
        cells = []
        for st in report["strategies"]:
            hr = s[st]["bug_hit_rate"][bcls] * 100
            ttd = s[st]["mean_trials_to_discover"][bcls]
            cells.append(f"`{hr:.0f}%` ({ttd if ttd is not None else '-'})")
        lines.append(f"| `{bcls}` | {role} | " + " | ".join(cells) + " |")

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
                f"passes `{m['highest_passing_below_fail']}` & `{m['lowest_passing_above_fail']}`)"
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

    lines += [
        "",
        "## Per-Seed Breakdown (All Seeds)",
        "",
        "| Seed | " + " | ".join(f"`{st}` (Bugs / Sigs / Raw)" for st in report["strategies"]) + " |",
        "|---|" + "|".join(["---"] * len(report["strategies"])) + "|",
    ]
    by_s = {
        st: {r["seed"]: r for r in report["per_seed_runs"][st]}
        for st in report["strategies"]
    }
    for seed in report["seeds"]:
        row_cells = []
        for st in report["strategies"]:
            r = by_s[st][seed]
            short_b = ",".join(b.split("_", 1)[0] for b in r["bug_classes_found"]) or "-"
            row_cells.append(f"**{r['unique_bug_classes_found']}** (`{short_b}`) / {r['unique_obs_signatures']} / {r['raw_divergences']}")
        lines.append(f"| `{seed}` | " + " | ".join(row_cells) + " |")

    return "\n".join(lines) + "\n"


def save_benchmark_plot(report: Dict[str, Any], out_png: Path) -> None:
    import matplotlib.pyplot as plt

    s = report["summary_by_strategy"]
    strats = report["strategies"]
    label_map = {
        "payload_only": "Payload-Only\n(fixed gap=7)",
        "joint_random": "Joint Random\n(blind sampling)",
        "guided_feedback": "Guided v1\n(flat corpus)",
        "guided_diverse": "Guided Diverse\n(bucketed novelty)",
    }
    color_map = {
        "payload_only": "#8a94a6",
        "joint_random": "#4c78a8",
        "guided_feedback": "#f58518",
        "guided_diverse": "#2b9348",
    }
    labels = [label_map.get(k, k) for k in strats]
    colors = [color_map.get(k, "#555555") for k in strats]
    means = [s[k]["mean_unique_bugs"] for k in strats]
    sigs = [s[k]["mean_obs_signatures"] for k in strats]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.4), dpi=160)
    max_b = len(report["bug_classes"])

    bars1 = ax1.bar(labels, means, color=colors, width=0.58)
    ax1.set_ylim(0, max_b + 0.5)
    ax1.set_ylabel(f"Mean Unique Bugs Found (out of {max_b})")
    ax1.set_title(f"Unique Divergences ({report['budget_per_seed']} trials/seed, {len(report['seeds'])} seeds)")
    ax1.grid(axis="y", alpha=0.3)
    for b, val in zip(bars1, means):
        ax1.text(b.get_x() + b.get_width() / 2, val + 0.08, f"{val:.2f}", ha="center", va="bottom", fontweight="bold")

    bars2 = ax2.bar(labels, sigs, color=colors, width=0.58)
    ax2.set_ylabel("Mean Observable Pin Signatures")
    ax2.set_title("Observable Pin State Coverage")
    ax2.grid(axis="y", alpha=0.3)
    for b, val in zip(bars2, sigs):
        ax2.text(b.get_x() + b.get_width() / 2, val + 0.7, f"{val:.1f}", ha="center", va="bottom", fontweight="bold")

    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run multi-seed feedback fuzzing benchmark on RTL")
    parser.add_argument("--budget", type=int, default=100)
    parser.add_argument("--seeds", type=int, default=20, help="Number of consecutive seeds starting at 20261101")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--target-mode", type=int, default=7, choices=[5, 7])
    parser.add_argument("--workers", type=int, default=min(10, os.cpu_count() or 4))
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--png-out", type=Path)
    args = parser.parse_args()

    seed_list = [20261101 + i for i in range(args.seeds)]
    rep = run_benchmark(
        budget=args.budget,
        seeds=seed_list,
        replay_repeats=args.repeats,
        target_mode=args.target_mode,
        workers=args.workers,
    )
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
