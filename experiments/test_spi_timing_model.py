import random
import unittest

from spi_timing_model import Target, binary_search, fixed_sweep, run_trial


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


if __name__ == "__main__":
    unittest.main()
