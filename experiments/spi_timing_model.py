"""Cycle-level reference model for the Timing Boundary Explorer's first study.

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
) -> Trial:
    """Simulate one bounded mode-0 byte transaction.

    Bit 7 is preloaded before the first rising sample. Each falling edge then
    schedules the next bit after target delay plus optional per-edge uniform
    integer jitter in [-jitter_fs, +jitter_fs]. A transition is valid iff its
    data has been stable for at least setup_fs at the next rising sample.
    Equality passes by definition. A stale sampled bit is retained.
    """
    if low_cycles < 1:
        raise ValueError("low phase must be at least one reference cycle")
    if jitter_fs < 0:
        raise ValueError("jitter magnitude must be non-negative")
    if jitter_fs and rng is None:
        raise ValueError("provide a seeded RNG when jitter is enabled")

    bits = [(target.response >> shift) & 1 for shift in range(7, -1, -1)]
    sampled = [bits[0]]  # first bit is stable before the first rising edge
    slacks: list[int] = []
    low_fs = low_cycles * target.clock_fs

    for index in range(1, 8):
        perturbation = rng.randint(-jitter_fs, jitter_fs) if rng else 0
        actual_delay = max(0, target.output_delay_fs + perturbation)
        slack = low_fs - actual_delay - target.setup_fs
        slacks.append(slack)
        sampled.append(bits[index] if slack >= 0 else sampled[-1])

    observed = 0
    for bit in sampled:
        observed = (observed << 1) | bit
    return Trial(low_cycles, observed, target.response, observed == target.response, tuple(slacks))


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
