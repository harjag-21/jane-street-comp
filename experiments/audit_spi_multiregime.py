"""Independent per-seed replay of the multi-regime equal-budget study."""

import json
import math
import random
from pathlib import Path

from audit_equal_budget import success, wilson


ROOT = Path(__file__).resolve().parents[1]
STUDY_PATH = ROOT / "docs" / "evidence" / "spi-multiregime-equal-budget.json"


def audit_study(data: dict[str, object], regime_id: str) -> None:
    target = data["target"]
    period = target["clock_fs"]
    delay = target["output_delay_fs"]
    setup = target["setup_fs"]
    high_cycles = target["high_cycles"]
    response = target["response"]
    jitter = data["jitter"]["range_fs"][1]
    threshold = data["success_threshold"]
    candidates = data["budget"]["candidate_settings"]
    fixed_n = data["budget"]["fixed_repeats_per_setting"]
    binary_n = data["budget"]["binary_repeats_per_probe"]
    z = data["interval_methods"]["wilson_z"]
    assert response == 0xAA and delay > jitter
    seeds = [run["seed"] for run in data["runs"]]
    assert len(seeds) == 1_000 and len(set(seeds)) == 1_000

    probabilities = {}
    for low in candidates:
        slack = low * period - delay - setup
        total = 2 * jitter + 1
        passing = min(total, max(0, slack + jitter + 1))
        probabilities[str(low)] = (passing / total) ** 7
        assert math.isclose(
            probabilities[str(low)],
            data["theoretical_success_probabilities"][str(low)],
            rel_tol=0,
            abs_tol=1e-15,
        )
    oracle = next(low for low in candidates if probabilities[str(low)] >= threshold)
    assert oracle == data["oracle_boundary_cycles"]
    assert oracle in (4, 5)

    fixed_correct = binary_correct = 0
    differences = []
    for stored in data["runs"]:
        rng = random.Random(stored["seed"])
        profiles = [tuple(rng.randint(-jitter, jitter) for _ in range(7))
                    for _ in range(binary_n)]

        fixed_rows = []
        for low in candidates:
            hits = sum(success(low, period, delay, setup, high_cycles,
                               response, profile)
                       for profile in profiles[:fixed_n])
            rate = hits / fixed_n
            fixed_rows.append((low, hits, fixed_n, rate, rate >= threshold))
        fixed_boundary = next((row[0] for row in fixed_rows if row[4]), None)
        fixed_pass_index = next((i for i, row in enumerate(fixed_rows) if row[4]), None)
        fixed_status = (
            "upper_endpoint_fail" if fixed_pass_index is None
            else "lower_endpoint_pass" if fixed_pass_index == 0
            else "interior_boundary"
        )

        binary_rows = []

        def classify(low: int) -> bool:
            hits = sum(success(low, period, delay, setup, high_cycles,
                               response, profile) for profile in profiles)
            rate = hits / binary_n
            binary_rows.append((low, hits, binary_n, rate, rate >= threshold))
            return rate >= threshold

        low, high = candidates[0], candidates[-1]
        if classify(low):
            binary_boundary = low
            binary_status = "lower_endpoint_pass"
        elif not classify(high):
            binary_boundary = None
            binary_status = "upper_endpoint_fail"
        else:
            left, right = low, high
            while right - left > 1:
                middle = (left + right) // 2
                if classify(middle):
                    right = middle
                else:
                    left = middle
            binary_boundary = right
            binary_status = "interior_boundary"

        fixed_observations = [
            {"low_cycles": l, "successes": h, "trials": n,
             "success_rate": rate, "meets_threshold": meets}
            for l, h, n, rate, meets in fixed_rows
        ]
        binary_observations = [
            {"low_cycles": l, "successes": h, "trials": n,
             "success_rate": rate, "meets_threshold": meets}
            for l, h, n, rate, meets in binary_rows
        ]
        assert fixed_observations == stored["fixed_sweep"]["observations"], regime_id
        assert binary_observations == stored["binary_search"]["observations"], regime_id
        assert fixed_boundary == stored["fixed_sweep"]["boundary"]
        assert binary_boundary == stored["binary_search"]["boundary"]
        assert fixed_status == stored["fixed_sweep"]["boundary_status"]
        assert stored["fixed_sweep"]["bracketed"] == (fixed_status == "interior_boundary")
        assert binary_status == stored["binary_search"]["boundary_status"]
        assert stored["binary_search"]["bracketed"] == (binary_status == "interior_boundary")
        assert len(binary_rows) == 5
        assert stored["fixed_trials"] == stored["binary_trials"] == 800
        assert stored["paired_profiles"] is True

        fixed_correct += int(fixed_boundary == oracle)
        binary_correct += int(binary_boundary == oracle)
        differences.append(int(binary_boundary == oracle) - int(fixed_boundary == oracle))

    count = len(seeds)
    summary = data["summary"]
    assert fixed_correct == summary["fixed_correct"]
    assert binary_correct == summary["binary_correct"]
    assert wilson(fixed_correct, count, z) == summary["fixed_accuracy_wilson_95"]
    assert wilson(binary_correct, count, z) == summary["binary_accuracy_wilson_95"]
    assert sum(differences) / count == summary["paired_binary_minus_fixed_accuracy"]

    bootstrap = data["interval_methods"]
    rng = random.Random(bootstrap["paired_bootstrap_seed"])
    reps = bootstrap["paired_bootstrap_resamples"]
    means = [
        sum(differences[rng.randrange(count)] for _ in range(count)) / count
        for _ in range(reps)
    ]
    means.sort()
    ci = [means[int(0.025 * reps)], means[min(reps - 1, int(0.975 * reps))]]
    assert ci == summary["paired_accuracy_difference_bootstrap_95"]
    print(
        f"{regime_id}: independent replay matched {count} seeds; oracle={oracle}; "
        f"fixed={fixed_correct}; binary={binary_correct}; paired CI={ci}"
    )


def main() -> None:
    data = json.loads(STUDY_PATH.read_text(encoding="utf-8"))
    assert data["design"]["regimes_predeclared"] is True
    assert len(data["regimes"]) == 6
    all_seeds = []
    for item in data["regimes"]:
        regime = item["regime"]
        study = item["study"]
        assert study["target"]["output_delay_fs"] == regime["delay_fs"]
        assert study["jitter"]["range_fs"] == [-regime["jitter_fs"], regime["jitter_fs"]]
        all_seeds.extend(run["seed"] for run in study["runs"])
        audit_study(study, regime["id"])
    assert len(all_seeds) == len(set(all_seeds)) == 6_000
    print("All six regimes and 6,000 unique seed runs passed independent replay.")


if __name__ == "__main__":
    main()
