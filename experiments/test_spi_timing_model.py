import random
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from spi_timing_model import (
    Target,
    binary_search,
    fixed_sweep,
    make_jitter_profiles,
    paired_binary_search,
    paired_fixed_sweep,
    run_equal_budget_study,
    run_trial,
    theoretical_success_probability,
    wilson_interval,
)


class TimingModelTests(unittest.TestCase):
    def setUp(self):
        self.target = Target()

    def test_oracle_boundary_and_adjacent_values(self):
        self.assertEqual(self.target.minimum_passing_low_cycles, 3)
        self.assertFalse(run_trial(2, self.target).passed)
        self.assertTrue(run_trial(3, self.target).passed)

    def test_first_bit_is_preloaded(self):
        trial = run_trial(1, self.target)
        self.assertEqual((trial.observed >> 7) & 1, (self.target.response >> 7) & 1)

    def test_late_transitions_can_affect_later_samples(self):
        trial = run_trial(1, self.target)
        # At L=1, each 23 ns response update misses its 10 ns sample but
        # arrives during a later high phase. The line therefore produces D5.
        self.assertEqual(trial.observed, 0xD5)
        self.assertFalse(trial.passed)
        self.assertTrue(all(slack < 0 for slack in trial.transition_slack_fs))

    def test_correct_digital_byte_with_setup_violation_is_not_a_pass(self):
        trial = run_trial(3, self.target, transition_jitter_fs=(6_000_000,) * 7)
        self.assertEqual(trial.observed, self.target.response)
        self.assertTrue(all(slack == -1_000_000 for slack in trial.transition_slack_fs))
        self.assertFalse(trial.passed)

    def test_binary_search_recovers_known_boundary(self):
        result = binary_search(self.target, 1, 8)
        self.assertTrue(result["bracketed"])
        self.assertEqual(result["boundary"], self.target.minimum_passing_low_cycles)
        self.assertEqual(result["trials"], 5)

    def test_seeded_jitter_is_reproducible_and_gives_transition_region(self):
        first = fixed_sweep(self.target, range(1, 7), repeats=10_000,
                            jitter_fs=6_000_000, seed=9)
        second = fixed_sweep(self.target, range(1, 7), repeats=10_000,
                             jitter_fs=6_000_000, seed=9)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["successes"], 0)
        self.assertEqual(first[-1]["successes"], 10_000)
        self.assertTrue(any(0 < row["successes"] < 10_000 for row in first))

    def test_jitter_requires_explicit_seeded_rng(self):
        with self.assertRaises(ValueError):
            run_trial(3, self.target, jitter_fs=1)

    def test_paired_jitter_searches_use_same_profiles_and_threshold(self):
        profiles = make_jitter_profiles(repeats=30, jitter_fs=6_000_000, seed=17)
        sweep = paired_fixed_sweep(self.target, range(1, 9), profiles, 0.95)
        binary = paired_binary_search(self.target, 1, 8, profiles, 0.95)
        self.assertEqual(sweep["boundary"], 4)
        self.assertEqual(binary["boundary"], 4)
        self.assertEqual(sweep["trials"], 240)
        self.assertEqual(binary["trials"], 150)
        sweep_l3 = next(row for row in sweep["observations"] if row["low_cycles"] == 3)
        binary_l3 = next(row for row in binary["observations"] if row["low_cycles"] == 3)
        self.assertEqual(sweep_l3, binary_l3)

    def test_probability_oracle_and_wilson_interval(self):
        self.assertLess(theoretical_success_probability(3, self.target, 15_176_495), 0.95)
        self.assertAlmostEqual(
            theoretical_success_probability(4, self.target, 15_176_495), 0.96, places=5
        )
        self.assertEqual(theoretical_success_probability(5, self.target, 15_176_495), 1.0)
        lower, upper = wilson_interval(95, 100)
        self.assertLess(lower, 0.95)
        self.assertGreater(upper, 0.95)

    def test_equal_budget_study_keeps_both_methods_within_800_trials(self):
        with TemporaryDirectory() as temp_dir:
            result = run_equal_budget_study(
                Path(temp_dir) / "study.json", seeds=range(20)
            )
        self.assertEqual(result["budget"]["fixed_transactions"], 800)
        self.assertEqual(result["budget"]["binary_transactions"], 800)
        self.assertTrue(all(run["fixed_trials"] == 800 for run in result["runs"]))
        self.assertTrue(all(run["binary_trials"] == 800 for run in result["runs"]))

    def test_equal_budget_study_uses_target_specific_oracle_boundary(self):
        target = Target(output_delay_fs=28_000_000)
        with TemporaryDirectory() as temp_dir:
            result = run_equal_budget_study(
                Path(temp_dir) / "study.json",
                target=target,
                seeds=range(20),
                jitter_fs=15_176_495,
            )
        self.assertEqual(result["oracle_boundary_cycles"], 5)
        self.assertEqual(result["summary"]["paired_seed_runs"], 20)


if __name__ == "__main__":
    unittest.main()
