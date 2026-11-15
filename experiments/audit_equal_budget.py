"""Independent replay/audit of the equal-budget JSON result.

This intentionally reimplements the trial decision and search allocation
instead of importing the study's model functions.
"""

import json
import math
import random
from pathlib import Path


def success(low: int, period: int, delay: int, setup: int, high_cycles: int,
            response: int, profile: tuple[int, ...]) -> bool:
    """Independently replay timestamped output events and sample points."""
    bits = [(response >> shift) & 1 for shift in range(7, -1, -1)]
    low_fs = low * period
    high_fs = high_cycles * period
    sample_period_fs = low_fs + high_fs
    sample_times = [low_fs + index * sample_period_fs for index in range(8)]
    events = []
    slacks = []
    for index, jitter in enumerate(profile):
        actual_delay = max(0, delay + jitter)
        slacks.append(low_fs - actual_delay - setup)
        falling_edge = sample_times[index] + high_fs
        events.append((falling_edge + actual_delay, index + 1))
    events.sort()

    sampled = []
    current_bit = bits[0]
    event_index = 0
    for sample_time in sample_times:
        while event_index < len(events) and events[event_index][0] <= sample_time:
            _, next_bit = events[event_index]
            current_bit = bits[next_bit]
            event_index += 1
        sampled.append(current_bit)

    observed = 0
    for bit in sampled:
        observed = (observed << 1) | bit
    return observed == response and all(slack >= 0 for slack in slacks)


def wilson(k: int, n: int, z: float) -> list[float]:
    p = k / n
    z2 = z * z
    denominator = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def audit_reference_study() -> None:
    path = Path("docs/evidence/spi-reference-study.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    target = data["target"]
    period = target["clock_fs"]
    delay = target["output_delay_fs"]
    setup = target["setup_fs"]
    response = target["response"]
    high_cycles = target["high_cycles"]
    candidates = range(1, 9)

    def deterministic_pass(low: int) -> bool:
        return success(low, period, delay, setup, high_cycles, response, (0,) * 7)

    fixed = [{"low_cycles": low, "successes": int(deterministic_pass(low)),
              "trials": 1, "success_rate": float(deterministic_pass(low))}
             for low in candidates]
    low, high = 1, 8
    obs = []

    def classify(value: int) -> bool:
        passed = deterministic_pass(value)
        obs.append({"low_cycles": value, "successes": int(passed), "trials": 1})
        return passed

    if classify(low):
        boundary = low
    elif not classify(high):
        boundary = high
    else:
        left, right = low, high
        while right - left > 1:
            middle = (left + right) // 2
            if classify(middle):
                right = middle
            else:
                left = middle
        boundary = right
    assert fixed == data["deterministic_fixed_sweep"]
    assert {"boundary": boundary, "trials": len(obs), "bracketed": True,
            "observations": obs} == data["deterministic_binary_search"]
    assert response == 0xAA

    jitter = data["seeded_jitter"]["magnitude_fs"]
    repeats = data["seeded_jitter"]["repeats_per_setting"]
    rng = random.Random(data["seeded_jitter"]["seed"])
    rows = []
    for value in candidates:
        hits = 0
        for _ in range(repeats):
            profile = tuple(rng.randint(-jitter, jitter) for _ in range(7))
            hits += int(success(value, period, delay, setup, high_cycles,
                                response, profile))
        rows.append({"low_cycles": value, "successes": hits, "trials": repeats,
                     "success_rate": hits / repeats})
    assert rows == data["seeded_jitter"]["fixed_sweep"]
    print("Reference study independently replayed: deterministic scan/search and all 80,000 jitter trials match.")


def main() -> None:
    audit_reference_study()
    path = Path("docs/evidence/spi-equal-budget-study.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    period = data["target"]["clock_fs"]
    delay = data["target"]["output_delay_fs"]
    setup = data["target"]["setup_fs"]
    high_cycles = data["target"]["high_cycles"]
    response = data["target"]["response"]
    jitter = data["jitter"]["range_fs"][1]
    threshold = data["success_threshold"]
    candidates = data["budget"]["candidate_settings"]
    fixed_n = data["budget"]["fixed_repeats_per_setting"]
    binary_n = data["budget"]["binary_repeats_per_probe"]
    z = data["interval_methods"]["wilson_z"]
    seeds = [run["seed"] for run in data["runs"]]
    assert len(seeds) == 1000 and len(set(seeds)) == len(seeds)
    assert data["target"]["response"] == 0xAA  # every adjacent response bit toggles
    assert delay > jitter  # clipping at zero delay is inactive in this study

    analytic_probabilities = {}
    for low in candidates:
        slack = low * period - delay - setup
        total_jitters = 2 * jitter + 1
        passing_jitters = min(total_jitters, max(0, slack + jitter + 1))
        analytic_probabilities[str(low)] = (passing_jitters / total_jitters) ** 7
    for key, expected in analytic_probabilities.items():
        assert math.isclose(expected, data["theoretical_success_probabilities"][key],
                            rel_tol=0, abs_tol=1e-15), key
    oracle_boundary = next(low for low in candidates
                           if analytic_probabilities[str(low)] >= threshold)
    assert oracle_boundary == data["oracle_boundary_cycles"]

    fixed_hits = binary_hits = 0
    differences = []
    fixed_trials_seen = set()
    binary_trials_seen = set()
    for stored in data["runs"]:
        seed = stored["seed"]
        generator = random.Random(seed)
        profiles = [tuple(generator.randint(-jitter, jitter) for _ in range(7))
                    for _ in range(binary_n)]

        fixed_rows = []
        for low in candidates:
            hits = sum(success(low, period, delay, setup, high_cycles,
                               response, profile)
                       for profile in profiles[:fixed_n])
            rate = hits / fixed_n
            fixed_rows.append((low, hits, fixed_n, rate, rate >= threshold))
        fixed_boundary = next((row[0] for row in fixed_rows if row[4]), None)

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
        elif not classify(high):
            binary_boundary = None
        else:
            left, right = low, high
            while right - left > 1:
                middle = (left + right) // 2
                if classify(middle):
                    right = middle
                else:
                    left = middle
            binary_boundary = right

        expected_fixed = [{"low_cycles": l, "successes": h, "trials": n,
                           "success_rate": rate, "meets_threshold": meets}
                          for l, h, n, rate, meets in fixed_rows]
        expected_binary = [{"low_cycles": l, "successes": h, "trials": n,
                            "success_rate": rate, "meets_threshold": meets}
                           for l, h, n, rate, meets in binary_rows]
        assert expected_fixed == stored["fixed_sweep"]["observations"], seed
        assert expected_binary == stored["binary_search"]["observations"], seed
        assert fixed_boundary == stored["fixed_sweep"]["boundary"], seed
        assert binary_boundary == stored["binary_search"]["boundary"], seed
        assert stored["fixed_trials"] == len(candidates) * fixed_n
        assert stored["binary_trials"] == len(binary_rows) * binary_n
        assert stored["fixed_trials"] == stored["binary_trials"] == 800
        assert stored["paired_profiles"] is True

        fixed_correct = fixed_boundary == data["oracle_boundary_cycles"]
        binary_correct = binary_boundary == data["oracle_boundary_cycles"]
        fixed_hits += fixed_correct
        binary_hits += binary_correct
        differences.append(int(binary_correct) - int(fixed_correct))

    count = len(data["runs"])
    fixed_interval = wilson(fixed_hits, count, z)
    binary_interval = wilson(binary_hits, count, z)

    bootstrap = data["interval_methods"]["paired_bootstrap"]
    assert bootstrap == "percentile"
    rng = random.Random(data["interval_methods"]["paired_bootstrap_seed"])
    reps = data["interval_methods"]["paired_bootstrap_resamples"]
    boot_means = []
    for _ in range(reps):
        sample = [differences[rng.randrange(count)] for _ in range(count)]
        boot_means.append(sum(sample) / count)
    boot_means.sort()
    delta_ci = [boot_means[int(0.025 * reps)],
                boot_means[min(reps - 1, int(0.975 * reps))]]

    summary = data["summary"]
    assert fixed_hits == summary["fixed_correct"]
    assert binary_hits == summary["binary_correct"]
    assert fixed_interval == summary["fixed_accuracy_wilson_95"]
    assert binary_interval == summary["binary_accuracy_wilson_95"]
    assert sum(differences) / count == summary["paired_binary_minus_fixed_accuracy"]
    assert delta_ci == summary["paired_accuracy_difference_bootstrap_95"]

    print(f"Independent replay passed for {count} seeds.")
    print(f"Fixed: {fixed_hits}/{count}, Wilson95={fixed_interval}")
    print(f"Binary: {binary_hits}/{count}, Wilson95={binary_interval}")
    print(f"Paired delta={sum(differences)/count}, bootstrap95={delta_ci}")
    print(f"Independent probability oracle boundary={oracle_boundary}; probabilities={analytic_probabilities}")
    print("All per-setting counts, boundaries, 800-trial budgets, and CI summaries match.")


if __name__ == "__main__":
    main()
