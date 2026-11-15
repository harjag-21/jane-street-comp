"""Event-level reference model for the Timing Boundary Explorer's first study.

This is a software oracle for an explicitly simplified SPI mode-0 target. It
does not model analog pad behavior, metastability, or fabricated silicon.
All times are integer femtoseconds so cycle quantization is explicit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import random
from pathlib import Path
from statistics import median
from typing import Iterable


@dataclass(frozen=True)
class Target:
    clock_fs: int = 10_000_000  # 10 ns reference clock
    output_delay_fs: int = 23_000_000  # 23 ns after each falling SCLK edge
    setup_fs: int = 2_000_000  # 2 ns required before the rising sample edge
    high_cycles: int = 2  # held fixed in this one-axis experiment
    response: int = 0xAA  # every adjacent bit toggles

    def __post_init__(self) -> None:
        if self.clock_fs <= 0 or self.output_delay_fs < 0 or self.setup_fs < 0:
            raise ValueError("clock must be positive and delays non-negative")
        if self.high_cycles < 1:
            raise ValueError("SPI high phase must be at least one reference cycle")
        if not 0 <= self.response <= 0xFF:
            raise ValueError("response must be an unsigned byte")

    @property
    def minimum_passing_low_cycles(self) -> int:
        """Smallest integer L where L*T >= output delay + setup requirement."""
        return math.ceil((self.output_delay_fs + self.setup_fs) / self.clock_fs)


@dataclass(frozen=True)
class Trial:
    low_cycles: int
    observed: int
    expected: int
    passed: bool
    transition_slack_fs: tuple[int, ...]


def run_trial(
    low_cycles: int,
    target: Target,
    *,
    jitter_fs: int = 0,
    rng: random.Random | None = None,
    transition_jitter_fs: tuple[int, ...] | None = None,
) -> Trial:
    """Simulate one bounded mode-0 byte transaction.

    Bit 7 is preloaded before the first rising sample. Each falling edge then
    schedules the next bit after target delay plus optional per-edge uniform
    integer jitter in [-jitter_fs, +jitter_fs]. A transition is valid iff its
    data has been stable for at least setup_fs at the next rising sample.
    Equality passes by definition. Delayed transitions remain scheduled after
    a missed sample and may therefore affect later samples.
    """
    if low_cycles < 1:
        raise ValueError("low phase must be at least one reference cycle")
    if jitter_fs < 0:
        raise ValueError("jitter magnitude must be non-negative")
    if jitter_fs and rng is None:
        raise ValueError("provide a seeded RNG when jitter is enabled")
    if transition_jitter_fs is not None and len(transition_jitter_fs) != 7:
        raise ValueError("one jitter value is required for each of seven transitions")

    bits = [(target.response >> shift) & 1 for shift in range(7, -1, -1)]
    slacks: list[int] = []
    low_fs = low_cycles * target.clock_fs
    high_fs = target.high_cycles * target.clock_fs
    sample_period_fs = low_fs + high_fs
    sample_times_fs = [low_fs + index * sample_period_fs for index in range(8)]
    events: list[tuple[int, int, int]] = []

    for index in range(7):
        perturbation = (
            transition_jitter_fs[index]
            if transition_jitter_fs is not None
            else rng.randint(-jitter_fs, jitter_fs) if rng else 0
        )
        actual_delay = max(0, target.output_delay_fs + perturbation)
        slack = low_fs - actual_delay - target.setup_fs
        slacks.append(slack)
        falling_edge_fs = sample_times_fs[index] + high_fs
        events.append((falling_edge_fs + actual_delay, index + 1, index))

    # Apply target output events in timestamp order. A transition that misses
    # its intended sample is not discarded; the line can change later and be
    # observed by a subsequent sample. Events are transport-delayed updates.
    events.sort()
    sampled = []
    current_bit = bits[0]  # first bit is stable before the first rising edge
    event_index = 0
    for sample_time_fs in sample_times_fs:
        while event_index < len(events) and events[event_index][0] <= sample_time_fs:
            _, next_bit, _transition_index = events[event_index]
            current_bit = bits[next_bit]
            event_index += 1
        sampled.append(current_bit)

    observed = 0
    for bit in sampled:
        observed = (observed << 1) | bit
    setup_valid = all(slack >= 0 for slack in slacks)
    return Trial(low_cycles, observed, target.response,
                 observed == target.response and setup_valid, tuple(slacks))


def fixed_sweep(
    target: Target,
    low_values: Iterable[int],
    repeats: int = 1,
    *,
    jitter_fs: int = 0,
    seed: int = 0,
) -> list[dict[str, int | float]]:
    """Evaluate every candidate L and retain successes/trials per setting."""
    if repeats < 1:
        raise ValueError("repeats must be positive")
    rng = random.Random(seed)
    rows = []
    for low in low_values:
        passed = sum(
            run_trial(low, target, jitter_fs=jitter_fs, rng=rng).passed
            for _ in range(repeats)
        )
        rows.append({"low_cycles": low, "successes": passed, "trials": repeats,
                     "success_rate": passed / repeats})
    return rows


def binary_search(
    target: Target,
    low: int,
    high: int,
    repeats: int = 1,
    *,
    seed: int = 0,
) -> dict[str, int | bool | list[dict[str, int]]]:
    """Find first passing integer L under deterministic monotonic outcomes."""
    if low < 1 or high < low or repeats < 1:
        raise ValueError("require 1 <= low <= high and repeats >= 1")
    rng = random.Random(seed)
    observations: list[dict[str, int]] = []

    def classify(value: int) -> bool:
        passed = sum(run_trial(value, target, rng=rng).passed for _ in range(repeats))
        observations.append({"low_cycles": value, "successes": passed, "trials": repeats})
        return passed == repeats

    if classify(low):
        return {"boundary": low, "trials": len(observations) * repeats,
                "bracketed": True, "observations": observations}
    if not classify(high):
        return {"boundary": high, "trials": len(observations) * repeats,
                "bracketed": False, "observations": observations}

    left, right = low, high
    while right - left > 1:
        mid = (left + right) // 2
        if classify(mid):
            right = mid
        else:
            left = mid
    return {"boundary": right, "trials": len(observations) * repeats,
            "bracketed": True, "observations": observations}


def run_reference_study(output: Path) -> dict[str, object]:
    """Run deterministic comparison and a seeded jitter sweep; write raw JSON."""
    target = Target()
    candidates = list(range(1, 9))
    sweep = fixed_sweep(target, candidates, repeats=1)
    binary = binary_search(target, 1, 8)
    jitter = fixed_sweep(target, candidates, repeats=10_000, jitter_fs=6_000_000, seed=20261115)
    result: dict[str, object] = {
        "evidence_class": "software simulation of a declared reference model",
        "model_limit": "does not establish RTL, pad, board, or silicon timing",
        "target": asdict(target),
        "oracle_boundary_cycles": target.minimum_passing_low_cycles,
        "deterministic_fixed_sweep": sweep,
        "deterministic_binary_search": binary,
        "seeded_jitter": {"magnitude_fs": 6_000_000, "seed": 20261115,
                          "repeats_per_setting": 10_000, "fixed_sweep": jitter},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def make_jitter_profiles(repeats: int, jitter_fs: int, seed: int) -> list[tuple[int, ...]]:
    """Create exogenous per-trial jitter used unchanged at every timing value."""
    if repeats < 1 or jitter_fs < 0:
        raise ValueError("repeats must be positive and jitter non-negative")
    rng = random.Random(seed)
    return [tuple(rng.randint(-jitter_fs, jitter_fs) for _ in range(7))
            for _ in range(repeats)]


def paired_fixed_sweep(
    target: Target,
    low_values: Iterable[int],
    jitter_profiles: list[tuple[int, ...]],
    success_threshold: float = 0.95,
) -> dict[str, object]:
    """Scan every setting using the same profile IDs at every setting."""
    if not jitter_profiles:
        raise ValueError("at least one jitter profile is required")
    if not 0 <= success_threshold <= 1:
        raise ValueError("success threshold must be in [0, 1]")
    rows = []
    for low in low_values:
        successes = sum(
            run_trial(low, target, transition_jitter_fs=profile).passed
            for profile in jitter_profiles
        )
        rate = successes / len(jitter_profiles)
        rows.append({"low_cycles": low, "successes": successes,
                     "trials": len(jitter_profiles), "success_rate": rate,
                     "meets_threshold": rate >= success_threshold})
    boundary = next((row["low_cycles"] for row in rows if row["meets_threshold"]), None)
    return {"boundary": boundary, "trials": len(rows) * len(jitter_profiles),
            "observations": rows}


def paired_binary_search(
    target: Target,
    low: int,
    high: int,
    jitter_profiles: list[tuple[int, ...]],
    success_threshold: float = 0.95,
) -> dict[str, object]:
    """Binary search using identical paired profiles and the same rate rule."""
    if low < 1 or high < low or not jitter_profiles:
        raise ValueError("require 1 <= low <= high and at least one jitter profile")
    if not 0 <= success_threshold <= 1:
        raise ValueError("success threshold must be in [0, 1]")
    observations = []

    def classify(value: int) -> bool:
        successes = sum(
            run_trial(value, target, transition_jitter_fs=profile).passed
            for profile in jitter_profiles
        )
        rate = successes / len(jitter_profiles)
        observations.append({"low_cycles": value, "successes": successes,
                             "trials": len(jitter_profiles), "success_rate": rate,
                             "meets_threshold": rate >= success_threshold})
        return rate >= success_threshold

    if classify(low):
        boundary = low
    elif not classify(high):
        boundary = None
    else:
        left, right = low, high
        while right - left > 1:
            mid = (left + right) // 2
            if classify(mid):
                right = mid
            else:
                left = mid
        boundary = right
    return {"boundary": boundary, "trials": len(observations) * len(jitter_profiles),
            "observations": observations}


def run_paired_jitter_study(
    output: Path,
    *,
    seeds: Iterable[int] = range(20261115, 20261145),
    repeats: int = 200,
    jitter_fs: int = 6_000_000,
    success_threshold: float = 0.95,
) -> dict[str, object]:
    """Compare methods over paired jitter streams and save every observation."""
    target = Target()
    candidates = list(range(1, 9))
    runs = []
    for seed in seeds:
        profiles = make_jitter_profiles(repeats, jitter_fs, seed)
        sweep = paired_fixed_sweep(target, candidates, profiles, success_threshold)
        binary = paired_binary_search(target, candidates[0], candidates[-1],
                                      profiles, success_threshold)
        runs.append({"seed": seed, "fixed_sweep": sweep, "binary_search": binary,
                     "same_jitter_profiles": True})

    fixed_trials = [run["fixed_sweep"]["trials"] for run in runs]
    binary_trials = [run["binary_search"]["trials"] for run in runs]
    result: dict[str, object] = {
        "evidence_class": "software simulation of the declared reference model",
        "target": asdict(target),
        "estimand": "first integer low phase with empirical exact-byte success rate >= threshold",
        "success_threshold": success_threshold,
        "jitter": {"distribution": "independent uniform integer per transition",
                   "range_fs": [-jitter_fs, jitter_fs], "repeats_per_probe": repeats,
                   "paired_profiles_reused_across_settings_and_methods": True},
        "candidate_low_cycles": candidates,
        "summary": {"runs": len(runs),
                    "oracle_boundary_at_threshold": 4,
                    "fixed_boundary_recovery_rate": sum(
                        run["fixed_sweep"]["boundary"] == 4 for run in runs
                    ) / len(runs),
                    "binary_boundary_recovery_rate": sum(
                        run["binary_search"]["boundary"] == 4 for run in runs
                    ) / len(runs),
                    "fixed_median_trials": median(fixed_trials),
                    "binary_median_trials": median(binary_trials),
                    "median_trial_reduction_fraction": 1 - median(binary_trials) / median(fixed_trials)},
        "runs": runs,
        "limit": "simulation comparison only; no RTL, pad, board, or silicon evidence",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def theoretical_success_probability(
    low_cycles: int, target: Target, jitter_fs: int
) -> float:
    """Exact-byte probability for independent integer-uniform edge jitter."""
    if low_cycles < 1 or jitter_fs < 0:
        raise ValueError("low_cycles must be positive and jitter non-negative")
    slack_without_jitter = (
        low_cycles * target.clock_fs - target.output_delay_fs - target.setup_fs
    )
    total = 2 * jitter_fs + 1
    if low_cycles * target.clock_fs < target.setup_fs:
        return 0.0
    per_edge_passes = min(total, max(0, slack_without_jitter + jitter_fs + 1))
    return (per_edge_passes / total) ** 7


def wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> list[float]:
    """95% Wilson interval for a binomial proportion (default z)."""
    if trials < 1 or not 0 <= successes <= trials:
        raise ValueError("require trials >= 1 and 0 <= successes <= trials")
    p = successes / trials
    z2 = z * z
    denominator = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denominator
    radius = z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def run_equal_budget_study(
    output: Path,
    *,
    target: Target | None = None,
    seeds: Iterable[int] = range(20261115, 20262115),
    fixed_repeats_per_setting: int = 100,
    binary_repeats_per_probe: int = 160,
    jitter_fs: int = 15_176_495,
    success_threshold: float = 0.95,
    bootstrap_seed: int = 20262115,
) -> dict[str, object]:
    """Compare methods under an equal transaction budget over paired seeds.

    Fixed sweep uses 100 transactions at each of eight values (800 total).
    Binary search gets 160 paired transactions at each of five probes (800
    total on this range). The first 100 perturbation profiles are shared at
    every value, and binary search receives the additional 60 only at values
    it probes. Each seed is an independent study replicate.
    """
    target = target or Target()
    candidates = list(range(1, 9))
    if not 0 < success_threshold < 1:
        raise ValueError("success threshold must be strictly between 0 and 1")
    if fixed_repeats_per_setting < 1 or binary_repeats_per_probe < fixed_repeats_per_setting:
        raise ValueError("repeat counts must be positive and binary profiles must cover fixed repeats")
    fixed_budget = len(candidates) * fixed_repeats_per_setting
    binary_max_probes = 5  # endpoints plus at most three bisection probes
    binary_budget = binary_max_probes * binary_repeats_per_probe
    if fixed_budget != binary_budget:
        raise ValueError("fixed and binary transaction budgets must match")

    theoretical_probabilities = {
        str(low): theoretical_success_probability(low, target, jitter_fs)
        for low in candidates
    }
    oracle_boundary = next(
        (low for low in candidates
         if theoretical_probabilities[str(low)] >= success_threshold),
        None,
    )
    if oracle_boundary is None:
        raise ValueError("candidate settings do not include the oracle boundary")

    runs = []
    for seed in seeds:
        profiles = make_jitter_profiles(binary_repeats_per_probe, jitter_fs, seed)
        fixed = paired_fixed_sweep(
            target, candidates, profiles[:fixed_repeats_per_setting], success_threshold
        )
        binary = paired_binary_search(
            target, candidates[0], candidates[-1], profiles, success_threshold
        )
        runs.append({"seed": seed, "fixed_sweep": fixed,
                     "binary_search": binary,
                     "paired_profiles": True,
                     "fixed_trials": fixed["trials"],
                     "binary_trials": binary["trials"]})

    count = len(runs)
    if count == 0:
        raise ValueError("at least one seed is required")
    fixed_correct = [run["fixed_sweep"]["boundary"] == oracle_boundary for run in runs]
    binary_correct = [run["binary_search"]["boundary"] == oracle_boundary for run in runs]
    paired_differences = [int(b) - int(f) for b, f in zip(binary_correct, fixed_correct)]

    bootstrap_rng = random.Random(bootstrap_seed)
    boot_means = []
    for _ in range(5000):
        sample = [paired_differences[bootstrap_rng.randrange(count)] for _ in range(count)]
        boot_means.append(sum(sample) / count)
    boot_means.sort()
    delta_ci = [boot_means[int(0.025 * len(boot_means))],
                boot_means[min(len(boot_means) - 1, int(0.975 * len(boot_means)))] ]

    fixed_trials = [run["fixed_trials"] for run in runs]
    binary_trials = [run["binary_trials"] for run in runs]
    result: dict[str, object] = {
        "evidence_class": "paired software simulation of the declared reference model",
        "target": asdict(target),
        "jitter": {"distribution": "independent uniform integer per transition",
                   "range_fs": [-jitter_fs, jitter_fs]},
        "estimand": "first integer low phase with exact-byte success probability >= threshold",
        "success_threshold": success_threshold,
        "theoretical_success_probabilities": theoretical_probabilities,
        "oracle_boundary_cycles": oracle_boundary,
        "budget": {"fixed_transactions": fixed_budget,
                   "binary_transactions": binary_budget,
                   "fixed_repeats_per_setting": fixed_repeats_per_setting,
                   "binary_repeats_per_probe": binary_repeats_per_probe,
                   "candidate_settings": candidates,
                   "binary_max_probes": binary_max_probes},
        "interval_methods": {"wilson_z": 1.959963984540054,
                             "paired_bootstrap": "percentile",
                             "paired_bootstrap_resamples": 5000,
                             "paired_bootstrap_seed": bootstrap_seed},
        "summary": {
            "paired_seed_runs": count,
            "fixed_correct": sum(fixed_correct),
            "binary_correct": sum(binary_correct),
            "fixed_accuracy": sum(fixed_correct) / count,
            "fixed_accuracy_wilson_95": wilson_interval(sum(fixed_correct), count),
            "binary_accuracy": sum(binary_correct) / count,
            "binary_accuracy_wilson_95": wilson_interval(sum(binary_correct), count),
            "paired_binary_minus_fixed_accuracy": sum(paired_differences) / count,
            "paired_accuracy_difference_bootstrap_95": delta_ci,
            "fixed_median_transactions": median(fixed_trials),
            "binary_median_transactions": median(binary_trials),
            "median_transactions_used_fraction": median(binary_trials) / fixed_budget,
            "median_unused_binary_budget": fixed_budget - median(binary_trials),
        },
        "runs": runs,
        "limit": "model-only evidence; confidence intervals cover seeded simulation replicates, not physical devices",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
