# SPDX-License-Identifier: Apache-2.0
"""Experiment 1+2 (Noisy Target): Sharp vs probabilistic boundary estimation on RTL."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.protocol_asm import build_spi_mode0_program  # noqa: E402
from tools.timing_explorer import IcarusSimTransport, ProtocolClient  # noqa: E402


def wilson_interval(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Return 95% Wilson binomial confidence interval (lo, hi) in [0, 1]."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1.0 + (z * z) / n
    center = (p + (z * z) / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * n)) / n)) / denom
    return (max(0.0, round(center - half, 4)), min(1.0, round(center + half, 4)))


def classify_probabilistic_region(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Distinguish sharp threshold from a probabilistic acceptance transition zone."""
    ordered = sorted(rows, key=lambda r: r["low_cycles"])
    mixed = [r for r in ordered if 0.0 < r["pass_rate"] < 1.0]
    exact_100 = [r for r in ordered if r["pass_rate"] >= 1.0]
    p90 = [r for r in ordered if r["pass_rate"] >= 0.90]
    p50 = [r for r in ordered if r["pass_rate"] >= 0.50]

    if not exact_100:
        region_class = "all_fail_or_incomplete"
    elif not mixed:
        region_class = "sharp_threshold"
    else:
        region_class = "probabilistic_transition_region"

    zero_rows = [r["low_cycles"] for r in ordered if r["pass_rate"] == 0.0 and (not exact_100 or r["low_cycles"] < exact_100[0]["low_cycles"])]
    return {
        "classification": region_class,
        "mixed_low_cycles": [r["low_cycles"] for r in mixed],
        "first_p50_low_cycles": p50[0]["low_cycles"] if p50 else None,
        "first_p90_low_cycles": p90[0]["low_cycles"] if p90 else None,
        "first_p100_low_cycles": exact_100[0]["low_cycles"] if exact_100 else None,
        "transition_width_cycles": (
            (exact_100[0]["low_cycles"] - max(zero_rows))
            if (exact_100 and zero_rows)
            else None
        ),
    }


def _eval_point(client: ProtocolClient, low_cycles: int, expected_miso: int = 0xAA) -> bool:
    res = client.run_trial(low_cycles - 2)
    return (
        res["sample_reg"] == expected_miso
        and res["eng_state"] == "ENG_HALTED"
        and res.get("sim_observations", {}).get("spi_mosi_rx") == 0x55
    )


def run_repeated_sweep(
    client: ProtocolClient,
    *,
    low_range: Sequence[int],
    repeats_per_setting: int,
    expected_miso: int = 0xAA,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for lc in low_range:
        passes = sum(1 for _ in range(repeats_per_setting) if _eval_point(client, lc, expected_miso))
        lo, hi = wilson_interval(passes, repeats_per_setting)
        rows.append(
            {
                "low_cycles": lc,
                "low_window_ns": lc * 10,
                "passes": passes,
                "trials": repeats_per_setting,
                "pass_rate": round(passes / repeats_per_setting, 4),
                "wilson_lo": lo,
                "wilson_hi": hi,
            }
        )
    return {
        "method": "repeated_fixed_sweep",
        "total_trials": len(low_range) * repeats_per_setting,
        "repeats_per_setting": repeats_per_setting,
        "points": rows,
        "summary": classify_probabilistic_region(rows),
    }


def run_single_shot_binary(
    client: ProtocolClient, *, min_lc: int, max_lc: int, expected_miso: int = 0xAA
) -> Dict[str, Any]:
    """Existing single-shot binary search over noisy target."""
    cache: Dict[int, bool] = {}

    def probe(lc: int) -> bool:
        ok = _eval_point(client, lc, expected_miso)
        cache[lc] = ok
        return ok

    boundary: Optional[int] = None
    status = "unknown"
    if probe(min_lc):
        status = "censored_all_pass"
    elif not probe(max_lc):
        status = "all_fail_unbracketed"
    else:
        lo, hi = min_lc, max_lc
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if probe(mid):
                hi = mid
            else:
                lo = mid
        boundary = hi
        status = "bracketed"

    return {
        "trials": len(cache),
        "status": status,
        "boundary_low_cycles": boundary,
    }


def run_repeated_binary(
    client: ProtocolClient,
    *,
    min_lc: int,
    max_lc: int,
    repeats_per_probe: int,
    pass_threshold: float = 0.90,
    expected_miso: int = 0xAA,
) -> Dict[str, Any]:
    """Repeat-averaged binary search accepting a setting iff observed pass_rate >= pass_threshold."""
    cache: Dict[int, float] = {}
    total_trials = 0

    def probe_rate(lc: int) -> float:
        nonlocal total_trials
        if lc in cache:
            return cache[lc]
        passes = sum(1 for _ in range(repeats_per_probe) if _eval_point(client, lc, expected_miso))
        total_trials += repeats_per_probe
        rate = passes / repeats_per_probe
        cache[lc] = rate
        return rate

    boundary: Optional[int] = None
    status = "unknown"
    if probe_rate(min_lc) >= pass_threshold:
        status = "censored_all_pass"
    elif probe_rate(max_lc) < pass_threshold:
        status = "all_fail_unbracketed"
    else:
        lo, hi = min_lc, max_lc
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if probe_rate(mid) >= pass_threshold:
                hi = mid
            else:
                lo = mid
        boundary = hi
        status = "bracketed"

    return {
        "trials": total_trials,
        "status": status,
        "boundary_low_cycles": boundary,
        "probed_rates": {str(k): round(v, 4) for k, v in sorted(cache.items())},
    }


def run_noisy_study(
    *,
    nominal_delay_ns: int = 37,
    jitter_ns: int = 9,
    low_range: Sequence[int] = (2, 3, 4, 5, 6, 7),
    sweep_repeats: int = 30,
    binary_runs: int = 12,
    rep_binary_k: int = 20,
) -> Dict[str, Any]:
    # Analytic uniform probability P(nominal + U[-J, +J] <= 10*L):
    analytic_p: Dict[int, float] = {}
    span = 2 * jitter_ns + 1
    for lc in low_range:
        win = 10 * lc
        passing = sum(
            1
            for off in range(-jitter_ns, jitter_ns + 1)
            if max(0, nominal_delay_ns + off) <= win
        )
        analytic_p[lc] = round(passing / span, 4)

    words = build_spi_mode0_program(0x55)
    t0 = time.time()

    with IcarusSimTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)
        client.load_program(words)

        # 1. Sharp deterministic reference (jitter = 0, delay = nominal_delay_ns)
        transport.configure_target(0, delay_ns=nominal_delay_ns, spi_resp=0xAA, i2c_ack=0)
        sharp_sweep = run_repeated_sweep(
            client, low_range=low_range, repeats_per_setting=10
        )

        # 2. Noisy target sweep across seeds
        transport.set_seed(0xA11CE001)
        transport.configure_target(6, delay_ns=nominal_delay_ns, spi_resp=0xAA, i2c_ack=jitter_ns)
        # Note configure_target puts arg_d directly into spi_jitter_ns in MODE_SPI_NOISY
        noisy_sweep = run_repeated_sweep(
            client, low_range=low_range, repeats_per_setting=sweep_repeats
        )

        # 3. Consistency across repeated noisy sweeps (3 independent seeds)
        sweep_consistency: List[Dict[str, Any]] = []
        for idx in range(3):
            transport.set_seed(0xBEEF0001 + idx)
            transport.configure_target(6, delay_ns=nominal_delay_ns, spi_resp=0xAA, i2c_ack=jitter_ns)
            sw = run_repeated_sweep(client, low_range=low_range, repeats_per_setting=20)
            sweep_consistency.append(sw)

        # 4. Naive 1-shot binary search repeated binary_runs times vs Repeated-probe binary search (p>=0.90)
        min_lc, max_lc = min(low_range), max(low_range)
        naive_runs: List[Dict[str, Any]] = []
        rep_bin_runs: List[Dict[str, Any]] = []
        for idx in range(binary_runs):
            transport.set_seed(0xC0FFEE01 + idx)
            transport.configure_target(6, delay_ns=nominal_delay_ns, spi_resp=0xAA, i2c_ack=jitter_ns)
            naive_runs.append(run_single_shot_binary(client, min_lc=min_lc, max_lc=max_lc))

            transport.set_seed(0xD00DFE01 + idx)
            transport.configure_target(6, delay_ns=nominal_delay_ns, spi_resp=0xAA, i2c_ack=jitter_ns)
            rep_bin_runs.append(
                run_repeated_binary(
                    client,
                    min_lc=min_lc,
                    max_lc=max_lc,
                    repeats_per_probe=rep_binary_k,
                    pass_threshold=0.90,
                )
            )

    elapsed_s = round(time.time() - t0, 2)

    def summarize_binary(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for r in runs:
            lbl = str(r["boundary_low_cycles"]) if r["boundary_low_cycles"] is not None else r["status"]
            counts[lbl] = counts.get(lbl, 0) + 1
        vals = [r["boundary_low_cycles"] for r in runs if r["boundary_low_cycles"] is not None]
        # True p >= 0.90 boundary for nominal=27 +/- 9 is low_cycles = 4 (p=1.0 vs p(3)=0.6842)
        true_p90_lc = next((lc for lc in sorted(low_range) if analytic_p[lc] >= 0.90), None)
        exact_acc = (
            sum(1 for v in vals if v == true_p90_lc) / len(runs) if (runs and true_p90_lc is not None) else 0.0
        )
        return {
            "runs": len(runs),
            "mean_trials": round(statistics.mean(r["trials"] for r in runs), 2),
            "estimate_histogram": counts,
            "true_p90_boundary": true_p90_lc,
            "accuracy_to_true_p90": round(exact_acc, 4),
            "unique_estimates": len(counts),
        }

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "rtl_simulation_noisy_target",
        "elapsed_seconds": elapsed_s,
        "model": {
            "clock_ns": 10,
            "nominal_delay_ns": nominal_delay_ns,
            "jitter_ns": jitter_ns,
            "analytic_pass_prob": {str(k): v for k, v in analytic_p.items()},
        },
        "sharp_reference": sharp_sweep,
        "noisy_sweep_primary": noisy_sweep,
        "noisy_sweep_repeats": sweep_consistency,
        "naive_binary_summary": summarize_binary(naive_runs),
        "repeated_binary_p90_summary": summarize_binary(rep_bin_runs),
    }


def format_noisy_markdown(rep: Dict[str, Any]) -> str:
    m = rep["model"]
    ns = rep["noisy_sweep_primary"]
    sh = rep["sharp_reference"]
    nb = rep["naive_binary_summary"]
    rb = rep["repeated_binary_p90_summary"]
    lines = [
        "# Noisy / Probabilistic Timing Boundary Study (RTL Co-Simulation)",
        "",
        f"- **Timestamp (UTC):** {rep['timestamp_utc']}",
        f"- **Evidence class:** `{rep['evidence_class']}`",
        f"- **Target model:** `MODE_SPI_NOISY` (`T={m['clock_ns']} ns`, `delay={m['nominal_delay_ns']} ns +/- {m['jitter_ns']} ns` uniform LFSR jitter per trial)",
        f"- **Sharp baseline classification:** `{sh['summary']['classification']}` (first p=1.0 at `{sh['summary']['first_p100_low_cycles']}` cycles, mixed settings `{sh['summary']['mixed_low_cycles']}`)",
        f"- **Noisy target classification:** `{ns['summary']['classification']}` (mixed acceptance zone at `low_cycles = {ns['summary']['mixed_low_cycles']}`, first `p>=0.50` at `{ns['summary']['first_p50_low_cycles']}`, first `p>=0.90` at `{ns['summary']['first_p90_low_cycles']}`, width `{ns['summary']['transition_width_cycles']}` cycles)",
        "",
        "## Empirical Pass-Rate Curve (`K=30` RTL Trials/Setting)",
        "",
        "| `low_cycles` | Low Window (ns) | Analytic `P(pass)` | Observed Pass/Trials | Empirical `p_hat` | Wilson 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    for row in ns["points"]:
        ap = m["analytic_pass_prob"][str(row["low_cycles"])]
        lines.append(
            f"| {row['low_cycles']} | {row['low_window_ns']} ns | {ap*100:.1f}% | `{row['passes']}/{row['trials']}` | **{row['pass_rate']*100:.1f}%** | `[{row['wilson_lo']*100:.1f}%, {row['wilson_hi']*100:.1f}%]` |"
        )

    rep_classes = [r["summary"]["classification"] for r in rep["noisy_sweep_repeats"]]
    rep_p90s = [r["summary"]["first_p90_low_cycles"] for r in rep["noisy_sweep_repeats"]]
    lines += [
        "",
        "## Fixed Repeated Sweep vs Naive 1-Shot Binary Search vs Repeated Binary Search (`p >= 0.90`)",
        "",
        f"- **Repeated fixed sweep (3 extra seeds, `K=20`/setting):** classifications = `{rep_classes}`, `p >= 0.90` at `low_cycles = {rep_p90s}`",
        f"- **Naive 1-shot binary search (`{nb['runs']}` runs, mean `{nb['mean_trials']}` trials/run):** estimates = `{nb['estimate_histogram']}` (`{nb['accuracy_to_true_p90']*100:.1f}%` hit true `p>=0.90` boundary `{nb['true_p90_boundary']}`; single-shot noise produced `{nb['unique_estimates']}` distinct boundary values)",
        f"- **Repeated binary search (`p >= 0.90`, `K=20`/probe, `{rb['runs']}` runs, mean `{rb['mean_trials']}` trials/run):** estimates = `{rb['estimate_histogram']}` (`{rb['accuracy_to_true_p90']*100:.1f}%` accuracy on true `p>=0.90` boundary `low_cycles = {rb['true_p90_boundary']}`)",
    ]
    return "\n".join(lines) + "\n"


def save_noisy_plot(rep: Dict[str, Any], out_png: Path) -> None:
    import matplotlib.pyplot as plt

    m = rep["model"]
    ns = rep["noisy_sweep_primary"]["points"]
    sh = rep["sharp_reference"]["points"]

    xs = [r["low_cycles"] for r in ns]
    ys = [r["pass_rate"] * 100.0 for r in ns]
    err_lo = [(r["pass_rate"] - r["wilson_lo"]) * 100.0 for r in ns]
    err_hi = [(r["wilson_hi"] - r["pass_rate"]) * 100.0 for r in ns]
    analytic = [m["analytic_pass_prob"][str(x)] * 100.0 for x in xs]
    sharp_y = [r["pass_rate"] * 100.0 for r in sh]

    fig, ax = plt.subplots(figsize=(7.6, 4.4), dpi=160)
    ax.plot(xs, sharp_y, linestyle="--", marker="s", color="#6c757d", label="Sharp baseline (jitter=0 ns)")
    ax.plot(xs, analytic, linestyle=":", color="#1f77b4", linewidth=2.0, label=f"Analytic P(pass) ({m['nominal_delay_ns']} +/- {m['jitter_ns']} ns)")
    ax.errorbar(
        xs,
        ys,
        yerr=[err_lo, err_hi],
        fmt="-o",
        color="#d62828",
        capsize=4,
        linewidth=2.0,
        label=f"RTL Noisy Sweep (K={rep['noisy_sweep_primary']['repeats_per_setting']}, Wilson 95% CI)",
    )
    mixed = rep["noisy_sweep_primary"]["summary"]["mixed_low_cycles"]
    if mixed:
        ax.axvspan(min(mixed) - 0.25, max(mixed) + 0.25, color="#ffb703", alpha=0.16, label="Probabilistic transition zone")
    ax.axhline(90, color="#2a9d8f", linestyle="-.", linewidth=1.2, label="90% reliability threshold")
    ax.set_xticks(xs)
    ax.set_xlabel("SPI SCLK Low Phase (cycles @ 10 ns/cycle)")
    ax.set_ylabel("Exact-Byte Pass Rate (%)")
    ax.set_ylim(-6, 108)
    ax.set_title("Sharp Threshold vs Probabilistic Acceptance Region (ASIC RTL)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right", fontsize=8.5)

    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run noisy vs sharp target timing boundary study")
    parser.add_argument("--sweep-repeats", type=int, default=30)
    parser.add_argument("--binary-runs", type=int, default=12)
    parser.add_argument("--rep-k", type=int, default=20)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--png-out", type=Path)
    args = parser.parse_args()

    rep = run_noisy_study(
        sweep_repeats=args.sweep_repeats,
        binary_runs=args.binary_runs,
        rep_binary_k=args.rep_k,
    )
    md = format_noisy_markdown(rep)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rep, indent=2, sort_keys=True), encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    if args.png_out:
        save_noisy_plot(rep, args.png_out)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
